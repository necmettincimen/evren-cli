"""plan modu uzak/ağ adımları: ssh, scp, http, wait. LLM'siz, deterministik; sırlar plan dosyasına yazılmaz (${ENV} ile ortamdan).

  {"op": "ssh",  "host": "user@ip", "jump": "user@jumpip", "identity": "~/.ssh/id_x", "sudo_pass_env": "HOST_SUDO",
                 "cmd": "kubectl ...", "stdin_file": "x.yaml",   # sudo_pass_env: komut `sudo -S bash -c` ile, parola ortamdan
                 "expect": "substr", "expect_regex": "...", "save": "out.txt", "save_as": "degisken", "timeout": 120}
  {"op": "scp",  "src": "yerel/ya/da/user@ip:yol", "dst": "user@ip:yol/ya/da/yerel", "jump": "user@jumpip"}
  {"op": "http", "method": "POST", "url": "https://...", "headers": {"Authorization": "Bearer ${DRONE_TOKEN}"},
                 "basic_env": ["ADMIN_USER", "ADMIN_PASS"], "json": {...} | "body_file": "x.json",
                 "expect_status": [200, 201], "expect": "substr", "save_as": "degisken"}
  {"op": "wait", "ssh": {...ssh adımı...} | "http": {...http adımı...}, "until": "regex", "interval": 10, "timeout": 900}

Sonraki adımlarda {{degisken}} (save_as) ve ${ENV} kullanılabilir.
"""

from __future__ import annotations

import base64
import json
import os
import shlex
import re
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

_SSH_BASE = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "StrictHostKeyChecking=accept-new"]


def _p(root: Path, rel: str) -> Path:
    path = (root / rel).resolve()
    if root != path and root not in path.parents:
        raise ValueError(f"çalışma alanı dışı yol: {rel}")
    return path


def _tail(text: str, n: int = 12) -> str:
    return "\n".join(text.strip().splitlines()[-n:])


def _check(out: str, step: dict) -> None:
    if step.get("expect") and step["expect"] not in out:
        raise RuntimeError(f"beklenen metin yok: {step['expect']!r}\n{_tail(out)}")
    if step.get("expect_regex") and not re.search(step["expect_regex"], out):
        raise RuntimeError(f"beklenen desen yok: {step['expect_regex']!r}\n{_tail(out)}")


def _ssh_run(step: dict, root: Path) -> str:
    cmd = list(_SSH_BASE)
    if step.get("jump"):
        cmd += ["-J", step["jump"]]
    if step.get("identity"):
        cmd += ["-i", os.path.expanduser(step["identity"])]
    remote = step["cmd"]
    stdin = _p(root, step["stdin_file"]).read_bytes() if step.get("stdin_file") else None
    if step.get("sudo_pass_env"):
        # Parola plan dosyasına yazılmaz; sudo -S stdin'den okur, komut root kabuğunda çalışır.
        pw = os.environ.get(step["sudo_pass_env"])
        if pw is None:
            raise RuntimeError(f"ortam değişkeni yok: {step['sudo_pass_env']}")
        remote = "sudo -S -p '' bash -c " + shlex.quote(remote)
        stdin = pw.encode() + b"\n" + (stdin or b"")
    cmd += [step["host"], remote]
    r = subprocess.run(cmd, input=stdin, capture_output=True, timeout=int(step.get("timeout", 180)))
    out = (r.stdout.decode("utf-8", "replace") + r.stderr.decode("utf-8", "replace"))
    if r.returncode not in step.get("ok_exit", [0]):
        raise RuntimeError(f"ssh çıkış kodu {r.returncode}\n{_tail(out)}")
    return out


def op_ssh(step, root, dry, ctx):
    if dry:
        return f"ssh {step['host']}: {step['cmd'][:80]}"
    out = _ssh_run(step, root)
    _check(out, step)
    _save(step, out, root, ctx)
    return _tail(out, 1)[:160] or "tamam"


def op_scp(step, root, dry, ctx):
    if dry:
        return f"scp {step['src']} -> {step['dst']}"
    cmd = ["scp", "-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]
    if step.get("jump"):
        cmd += ["-J", step["jump"]]
    if step.get("identity"):
        cmd += ["-i", os.path.expanduser(step["identity"])]
    local = lambda v: v if ":" in v and "@" in v.split(":")[0] else str(_p(root, v))  # noqa: E731
    cmd += [local(step["src"]), local(step["dst"])]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=int(step.get("timeout", 300)))
    if r.returncode != 0:
        raise RuntimeError(_tail(r.stdout + r.stderr))
    return "kopyalandı"


def _http_run(step: dict, root: Path) -> tuple[int, str]:
    headers = dict(step.get("headers") or {})
    if step.get("basic_env"):
        user, pw = (os.environ[k] for k in step["basic_env"])
        headers["Authorization"] = "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()
    headers.setdefault("User-Agent", "curl/8.4.0")
    data = None
    if step.get("json") is not None:
        data = json.dumps(step["json"]).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    elif step.get("body_file"):
        data = _p(root, step["body_file"]).read_bytes()
        headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(step["url"], method=step.get("method", "GET"), data=data, headers=headers)
    ctx = ssl._create_unverified_context() if step.get("insecure") else None  # noqa: S323
    try:
        with urllib.request.urlopen(req, timeout=int(step.get("timeout", 60)), context=ctx) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def op_http(step, root, dry, ctx):
    if dry:
        return f"{step.get('method', 'GET')} {step['url'][:80]}"
    status, body = _http_run(step, root)
    if status not in step.get("expect_status", [200, 201, 202, 204]):
        raise RuntimeError(f"HTTP {status}\n{_tail(body)}")
    _check(body, step)
    _save(step, body, root, ctx)
    return f"HTTP {status}"


def op_wait(step, root, dry, ctx):
    if dry:
        return "bekleme (kuru çalıştırmada atlandı)"
    deadline = time.time() + int(step.get("timeout", 900))
    until = re.compile(step["until"])
    last = ""
    while time.time() < deadline:
        try:
            if "ssh" in step:
                last = _ssh_run(step["ssh"], root)
            else:
                st, last = _http_run(step["http"], root)
                last = f"{st} {last}"
        except Exception as e:  # noqa: BLE001
            last = f"hata: {e}"
        if until.search(last):
            _save(step, last, root, ctx)
            return "koşul sağlandı"
        time.sleep(int(step.get("interval", 10)))
    raise RuntimeError(f"zaman aşımı; son çıktı:\n{_tail(last)}")


def _save(step: dict, text: str, root: Path, ctx: dict) -> None:
    if step.get("save"):
        path = _p(root, step["save"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="")
    if step.get("save_as"):
        ctx[step["save_as"]] = text.strip()


def expand(value, ctx: dict):
    """{{degisken}} (önceki adımların save_as değerleri) ve ${ENV} yerine koyar; yinelemeli (dict/list)."""
    if isinstance(value, str):
        value = re.sub(r"\{\{(\w+)\}\}", lambda m: ctx.get(m.group(1), m.group(0)), value)
        return re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), m.group(0)), value)
    if isinstance(value, dict):
        return {k: expand(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [expand(v, ctx) for v in value]
    return value


REMOTE_OPS = {"ssh": op_ssh, "scp": op_scp, "http": op_http, "wait": op_wait}
