"""plan: üst planlayıcının (insan ya da büyük model) yazdığı adım listesini LLM'siz, refleks hızında çalıştırır.

Mekanik işler (kopyala-yeniden adlandır, toplu değiştir, derle, commit) düşünmeden deterministik yürür;
yalnız anlamsal adımlar için dar kapsamlı `agent` adımı vardır. İlk hatada durur, tam raporu yazar.

Plan dosyası (JSON):
  {"steps": [
    {"op": "clone_tree", "src": "portal-esmm-sign", "dst": "portal-egp-sign", "replacements": [["Esmm","Egp"],["esmm","egp"]]},
    {"op": "replace", "glob": "portal-egp-sign/**/*.cs", "find": "SMM", "with": "EGP", "regex": false},
    {"op": "write", "path": "x/README.md", "content": "..."},
    {"op": "run", "cmd": "dotnet build -c Release", "cwd": "portal-egp-sign", "ok_exit": [0]},
    {"op": "commit", "repo": "portal-egp-sign", "message": "..."},
    {"op": "push", "repo": "portal-egp-sign"},                      # yalnız --allow-push ile
    {"op": "ssh" | "scp" | "http" | "wait", ...},                    # bkz. plan_ops_remote.py
    {"op": "agent", "prompt": "...", "model": "mimo-v2.6-pro", "max_steps": 80, "repo": "x", "retries": 1,
     "require_changes": true, "commit": false, "push": false, "report": "reports/x.md", "worktree": false, "commit_message": "...",
     "planner": false, "env": {"EVREN_X": "1"}},                       # planner:false = planlayıcı aşaması yok (dar düzenleme işleri)
    {"op": "parallel", "max_workers": 3, "steps": [ {agent...}, {agent...} ]}   # agent alt adımlarında worktree varsayılan açık
  ]}
"""

from __future__ import annotations

import json
import os
import shutil
import re
import subprocess
import sys
import threading
from pathlib import Path

from src import plan_agent
from src.plan_ops_remote import REMOTE_OPS, expand

_AI_SIGNATURE = re.compile(r"co-authored-by|generated with|claude", re.I)


def _p(root: Path, rel: str) -> Path:
    path = (root / rel).resolve()
    if root != path and root not in path.parents:
        raise ValueError(f"çalışma alanı dışı yol: {rel}")
    return path


def _op_clone_tree(step, root, dry):
    from src.clone_tree import tool_clone_tree

    return tool_clone_tree({**{k: step[k] for k in ("src", "dst", "replacements")}, "exclude": step.get("exclude"),
                            "dry_run": dry}, root)


def _op_replace(step, root, dry):
    find, repl = step["find"], step["with"]
    flags = sum({"s": re.S, "i": re.I, "m": re.M}[c] for c in step.get("flags", ""))
    if step.get("regex"):
        find = find.replace("\\n", "\\r?\\n")  # CRLF dosyalarda satir sonu da eslesir
    else:
        find = "\\r?\\n".join(re.escape(x) for x in find.split(chr(10)))
    pat = re.compile(find, flags)
    literal = repl if step.get("regex") else repl.replace("\\", "\\\\")
    files = changed = 0
    for path in sorted(root.glob(step["glob"])):
        if not path.is_file() or any(x in path.parts for x in (".git", "bin", "obj", "node_modules")):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        files += 1
        new = pat.sub(literal, text)
        if new != text:
            changed += 1
            if not dry:
                path.write_text(new, encoding="utf-8", newline="")
    if step.get("required", True) and not dry and changed == 0:
        raise RuntimeError(f"replace hicbir dosyada eslesmedi ({files} dosya tarandi); 'required': false ile atlanabilir")
    return f"{files} dosya tarandı, {changed} değişti"


def _op_write(step, root, dry):
    path = _p(root, step["path"])
    if not dry:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(step["content"], encoding="utf-8", newline="")
    return f"yazıldı: {path}"


def _find_bash() -> str | None:
    """Git Bash tercih edilir: gizli/arka plan süreçlerinde PATH'teki `bash` Windows'un WSL yönlendiricisi
    (System32\bash.exe) olabilir; /bin/bash bulunamadığı için her `run` adımı çıkış kodu 1 verirdi."""
    if os.name == "nt":
        for cand in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe",
                     r"C:\Program Files (x86)\Git\bin\bash.exe"):
            if os.path.isfile(cand):
                return cand
        found = shutil.which("bash")
        if found and "system32" in found.lower().replace("/", "\\"):
            return None
        return found
    return shutil.which("bash")


def _op_run(step, root, dry):
    if dry:
        return f"çalıştırılacak: {step['cmd']}"
    bash = _find_bash() if step.get("shell", "bash") == "bash" else None
    cmd = [bash, "-c", step["cmd"]] if bash else step["cmd"]
    r = subprocess.run(cmd, shell=not bash, cwd=_p(root, step.get("cwd", ".")), capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=int(step.get("timeout", 900)))
    ok = r.returncode in step.get("ok_exit", [0])
    tail = (r.stdout + r.stderr).strip().splitlines()[-12:]
    if not ok:
        raise RuntimeError(f"çıkış kodu {r.returncode}\n" + "\n".join(tail))
    return "tamam" + (": " + tail[-1] if tail else "")


def _op_commit(step, root, dry):
    msg = step["message"]
    if _AI_SIGNATURE.search(msg):
        raise ValueError("commit mesajında AI imzası/attribution yasak (proje kuralı)")
    if dry:
        return f"commit: {msg[:60]}"
    repo = _p(root, step["repo"])
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    # Mesaj stdin'den utf-8 verilir: Windows'ta argv üzerinden geçen Türkçe karakterler çift kodlanıyordu (mojibake).
    r = subprocess.run(["git", "-C", str(repo), "commit", "-q", "-F", "-"], input=msg.encode("utf-8"), capture_output=True)
    r_out = (r.stdout + r.stderr).decode("utf-8", "replace")
    return "commit atıldı" if r.returncode == 0 else "commit yok: " + r_out.strip()[:120]


def _op_push(step, root, dry, allow_push=False):
    if not allow_push:
        raise PermissionError("push adımı için --allow-push gerekir (kullanıcı onayı)")
    if dry:
        return "push"
    repo = str(_p(root, step["repo"]))
    branch = step.get("branch") or subprocess.run(["git", "-C", repo, "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True,
                                                  text=True, encoding="utf-8", errors="replace").stdout.strip() or "main"
    r = subprocess.run(["git", "-C", repo, "push", "origin", branch], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[-300:])
    msg = "push edildi"
    if step.get("drone", True):
        _LAST_DRONE.clear()
        msg += "; " + _drone_trigger(repo, branch, step.get("drone_wait", 90))
        if step.get("drone_result", True) and _LAST_DRONE.get("number"):
            msg += "; " + _drone_await(_LAST_DRONE["slug"], _LAST_DRONE["number"], float(step.get("drone_timeout", 1500)))
    return msg


_LAST_DRONE: dict = {}


def _drone_trigger(repo: str, branch: str, wait_s: float = 90) -> str:
    """Push sonrası Drone build'ini API'den tetikler (GitHub webhook gecikmesine bağımlılık kalmaz).
    DRONE_TOKEN yoksa atlanır; hata push'u düşürmez. Sunucu DRONE_SERVER (varsayılan drone.sentayazilimtest.space).
    Not: API tetiği Drone'da event=custom'dır; .drone.yml'de push pipeline'ı custom'ı da kabul etmelidir."""
    tok = os.environ.get("DRONE_TOKEN")
    if not tok:
        return "Drone tetiklenmedi (DRONE_TOKEN yok)"
    url = subprocess.run(["git", "-C", repo, "remote", "get-url", "origin"], capture_output=True, text=True,
                         encoding="utf-8", errors="replace").stdout.strip()
    m = re.search(r"github\.com[:/]([^/]+)/([^/\s]+?)(?:\.git)?$", url)
    if not m:
        return "Drone tetiklenmedi (origin GitHub değil)"
    slug = f"{m.group(1)}/{m.group(2)}"
    server = os.environ.get("DRONE_SERVER", "https://drone.sentayazilimtest.space").rstrip("/")
    sha = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True, text=True,
                         encoding="utf-8", errors="replace").stdout.strip()
    try:
        import json as _json
        import time as _time
        import urllib.error
        import urllib.request
        hdr = {"Authorization": f"Bearer {tok}", "User-Agent": "curl/8.0"}  # Drone önündeki katman Python-urllib UA'sını 403'ler
        # Önce webhook'un getirdiği build'i bekle (çift build olmasın); süre dolunca API'den tetikle.
        deadline = _time.time() + float(wait_s)
        while _time.time() < deadline:
            with urllib.request.urlopen(urllib.request.Request(f"{server}/api/repos/{slug}/builds?per_page=5", headers=hdr), timeout=30) as resp:
                for b in _json.load(resp):
                    if b.get("after") == sha:
                        _LAST_DRONE.update(slug=slug, number=b.get("number"))
                        return f"Drone build #{b.get('number')} webhook ile geldi ({slug})"
            _time.sleep(10)
        def _post(path):
            r = urllib.request.Request(f"{server}{path}", method="POST", headers=hdr, data=b"")
            with urllib.request.urlopen(r, timeout=60) as resp:
                body = resp.read().decode("utf-8", "replace")
                return _json.loads(body) if body.startswith("{") else {}
        trig = f"/api/repos/{slug}/builds?branch={branch}&commit={sha}"
        try:
            n = _post(trig).get("number")
            how = "API ile tetiklendi"
        except urllib.error.HTTPError as e:
            # 404 "Bad credentials"/401/500: repo sahibi Drone kullanıcısının GitHub token'ı geçersiz.
            # Repoyu token sahibine alıp (chown) bir kez daha dene; kalıcı onarım.
            if e.code not in (401, 404, 500):
                raise
            _post(f"/api/repos/{slug}/chown")
            n = _post(trig).get("number")
            how = "repo sahipliği (chown) onarılıp API ile tetiklendi"
        _LAST_DRONE.update(slug=slug, number=n)
        return f"webhook {int(wait_s)} sn içinde gelmedi, Drone build #{n} {how} ({slug})"
    except Exception as e:  # noqa: BLE001
        return f"Drone tetiklenemedi: {str(e)[:120]}"


def _drone_await(slug: str, number, timeout_s: float = 1500) -> str:
    """Build bitene kadar bekler; failure/error/killed ise başarısız adımın log kuyruğuyla RuntimeError fırlatır
    (push sonrası 'build geçti mi' denetimi plana gömülüdür, elle bakmaya gerek kalmaz)."""
    import json as _json
    import time as _time
    import urllib.request
    server = os.environ.get("DRONE_SERVER", "https://drone.sentayazilimtest.space").rstrip("/")
    hdr = {"Authorization": f"Bearer {os.environ.get('DRONE_TOKEN', '')}", "User-Agent": "curl/8.0"}

    def _get(path):
        with urllib.request.urlopen(urllib.request.Request(f"{server}{path}", headers=hdr), timeout=60) as resp:
            return _json.load(resp)

    deadline = _time.time() + timeout_s
    build = {}
    while _time.time() < deadline:
        try:
            build = _get(f"/api/repos/{slug}/builds/{number}")
        except Exception:  # noqa: BLE001
            _time.sleep(15)
            continue
        if build.get("status") not in ("pending", "running", "blocked", "waiting_on_dependencies"):
            break
        _time.sleep(15)
    status = build.get("status", "bilinmiyor")
    if status == "success":
        return f"Drone build #{number} BAŞARILI ({slug})"
    if status in ("pending", "running", "bilinmiyor"):
        return f"Drone build #{number} {int(timeout_s)} sn içinde bitmedi (durum: {status}); elle izle ({slug})"
    detail = ""
    for st in build.get("stages", []):
        for sp in st.get("steps", []):
            if sp.get("status") in ("failure", "error", "killed"):
                try:
                    lines = _get(f"/api/repos/{slug}/builds/{number}/logs/{st.get('number')}/{sp.get('number')}")
                    detail = f"\nadım '{sp.get('name')}':\n" + "\n".join("".join(x.get("out", "") for x in lines).splitlines()[-25:])
                except Exception:  # noqa: BLE001
                    detail = f"\nadım '{sp.get('name')}' (log alınamadı)"
                break
        if detail:
            break
    raise RuntimeError(f"Drone build #{number} {status.upper()} ({slug}){detail}")


def _git_state(path: Path) -> str | None:
    """HEAD + çalışma ağacı özeti; git deposu değilse None (değişiklik denetimi atlanır)."""
    try:
        st = subprocess.run(["git", "-C", str(path), "status", "--porcelain"], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=60)
        if st.returncode != 0:
            return None
        head = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=60).stdout.strip()
        return head + "\n" + st.stdout
    except Exception:
        return None


def _op_agent(step, root, dry):
    """Dar anlamsal görev; sözleşme, soru yakalama, günlük, worktree ve canlı ilerleme için bkz. src/plan_agent.py."""
    return plan_agent.op_agent(step, root, dry, _p)


OPS = {"clone_tree": _op_clone_tree, "replace": _op_replace, "write": _op_write, "run": _op_run,
       "commit": _op_commit, "agent": _op_agent}


_PUSH_LOCK = threading.Lock()


def _locked_push(step, root, dry, allow_push):
    """agent `push: true`: eşzamanlı ajanların pushları sıralanır (Drone izleme durumu küresel)."""
    keep = {k: step[k] for k in ("branch", "drone", "drone_wait", "drone_result", "drone_timeout") if k in step}
    with _PUSH_LOCK:
        return _op_push({"repo": step["repo"], **keep}, root, dry, allow_push)


def _op_parallel(step, root, dry, ctx=None):
    return plan_agent.op_parallel(step, root, dry, OPS, _p, expand, ctx or {})


def _load_secrets() -> int:
    """~/.evren/secrets.env (KEY=VALUE satırları) ortamı doldurur; sırlar plan dosyasına ya da komut satırına yazılmaz.
    Var olan ortam değişkenleri ezilmez."""
    path = Path.home() / ".evren" / "secrets.env"
    if not path.is_file():
        return 0
    n = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() and k.strip() not in os.environ:
            os.environ[k.strip()] = v.strip().strip("'\"")
            n += 1
    return n


def run_plan(plan_path: str, workspace: str, dry_run: bool = False, allow_push: bool = False, start: int = 1) -> int:
    root = Path(workspace).resolve()
    _load_secrets()
    steps = json.loads(Path(plan_path).read_text(encoding="utf-8-sig"))["steps"]
    ctx: dict = {}
    plan_agent.PUSH_HOOK = lambda s: _locked_push(s, root, dry_run, allow_push)
    for i, step in enumerate(steps, 1):
        if i < start:
            continue
        op = step.get("op")
        try:
            step = expand(step, ctx)
            step["_label"] = f"{Path(plan_path).stem}-{i}"
            if op == "parallel":
                out = _op_parallel(step, root, dry_run, ctx)
            elif op == "push":
                out = _op_push(step, root, dry_run, allow_push)
            elif op in REMOTE_OPS:
                out = REMOTE_OPS[op](step, root, dry_run, ctx)
            elif op in OPS:
                out = OPS[op](step, root, dry_run)
            else:
                raise ValueError(f"bilinmeyen adım: {op}")
            print(f"[{i}/{len(steps)}] {op}: {out}")
        except Exception as e:  # noqa: BLE001
            print(f"[{i}/{len(steps)}] {op} HATA: {e}\nDevam için: evren plan {plan_path} --from {i}")
            return 1
    print("PLAN TAMAMLANDI" + (" (kuru çalıştırma)" if dry_run else ""))
    return 0
