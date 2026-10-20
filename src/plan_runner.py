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
    {"op": "agent", "prompt": "...", "model": "mimo-v2.6-pro", "max_steps": 80}
  ]}
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

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
    pat = re.compile(find if step.get("regex") else re.escape(find))
    files = changed = 0
    for path in sorted(root.glob(step["glob"])):
        if not path.is_file() or any(x in path.parts for x in (".git", "bin", "obj", "node_modules")):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        files += 1
        new = pat.sub(repl, text)
        if new != text:
            changed += 1
            if not dry:
                path.write_text(new, encoding="utf-8", newline="")
    return f"{files} dosya tarandı, {changed} değişti"


def _op_write(step, root, dry):
    path = _p(root, step["path"])
    if not dry:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(step["content"], encoding="utf-8", newline="")
    return f"yazıldı: {path}"


def _op_run(step, root, dry):
    if dry:
        return f"çalıştırılacak: {step['cmd']}"
    r = subprocess.run(step["cmd"], shell=True, cwd=_p(root, step.get("cwd", ".")), capture_output=True, text=True,
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
    r = subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", msg], capture_output=True, text=True)
    return "commit atıldı" if r.returncode == 0 else "commit yok: " + (r.stdout + r.stderr).strip()[:120]


def _op_push(step, root, dry, allow_push=False):
    if not allow_push:
        raise PermissionError("push adımı için --allow-push gerekir (kullanıcı onayı)")
    if dry:
        return "push"
    r = subprocess.run(["git", "-C", str(_p(root, step["repo"])), "push", "origin", "main"], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[-300:])
    return "push edildi"


def _op_agent(step, root, dry):
    if dry:
        return "agent adımı (kuru çalıştırmada atlandı)"
    directive = "\n\n[BAŞSIZ KİP] Yalnız bu dar görevi uygula; plan sunma, soru sorma."
    cmd = [sys.executable, "-m", "src.cli", "-w", str(root), "--max-steps", str(step.get("max_steps", 80))]
    if step.get("model"):
        cmd += ["-m", step["model"]]
    cmd += ["-p", step["prompt"] + directive]
    r = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                       timeout=int(step.get("timeout", 1800)))
    if r.returncode != 0:
        raise RuntimeError((r.stdout + r.stderr).strip()[-400:])
    return (r.stdout.strip().splitlines() or ["tamam"])[-1][:200]


OPS = {"clone_tree": _op_clone_tree, "replace": _op_replace, "write": _op_write, "run": _op_run,
       "commit": _op_commit, "agent": _op_agent}


def run_plan(plan_path: str, workspace: str, dry_run: bool = False, allow_push: bool = False, start: int = 1) -> int:
    root = Path(workspace).resolve()
    steps = json.loads(Path(plan_path).read_text(encoding="utf-8-sig"))["steps"]
    ctx: dict = {}
    for i, step in enumerate(steps, 1):
        if i < start:
            continue
        op = step.get("op")
        try:
            step = expand(step, ctx)
            if op == "push":
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
