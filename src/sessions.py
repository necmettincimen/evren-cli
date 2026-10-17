"""Session history: .evren/sessions/<ts>.json (vault-masked) for --continue and /resume."""

from __future__ import annotations

import json
import time
from pathlib import Path


def _dir(ws) -> Path:
    return Path(ws) / ".evren" / "sessions"


def _mask(obj):
    try:
        from src.secret_vault import active_vault

        v = active_vault()
        return json.loads(v.mask(json.dumps(obj, ensure_ascii=False, default=str)))
    except Exception:
        return obj


def save_session(session) -> Path | None:
    msgs = [m for m in session.messages if m.get("role") != "system"]
    if not msgs:
        return None
    stamp = getattr(session, "_session_stamp", None)
    if not stamp:
        stamp = session._session_stamp = time.strftime("%Y%m%d-%H%M%S")
    first = next((str(m.get("content") or "") for m in msgs if m.get("role") == "user"), "")
    data = {"model": session.model, "mode": session.mode.value, "goal": session.task_goal or "",
            "title": first.strip().splitlines()[0][:80] if first.strip() else "", "messages": msgs}
    try:
        d = _dir(session.workspace_root)
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{stamp}.json"
        path.write_text(json.dumps(_mask(data), ensure_ascii=False), encoding="utf-8")
        return path
    except Exception:
        return None


def list_sessions(ws, limit: int = 10) -> list[Path]:
    d = _dir(ws)
    return sorted(d.glob("*.json"), reverse=True)[:limit] if d.exists() else []


def load_session(session, path: Path) -> bool:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        msgs = data["messages"]
    except Exception:
        return False
    session.messages = [session.messages[0], *msgs] if session.messages else msgs
    session.task_goal = data.get("goal") or session.task_goal
    session._session_stamp = Path(path).stem
    return True


def handle_resume(session) -> None:
    from src.ui import print_info, print_warning

    items = list_sessions(session.workspace_root)
    if not items:
        print_warning("Kayıtlı oturum yok.")
        return
    for i, p in enumerate(items, 1):
        try:
            title = json.loads(p.read_text(encoding="utf-8")).get("title", "")
        except Exception:
            title = ""
        print(f"  {i}. {p.stem}  {title}")
    try:
        ans = input("Numara (boş=iptal): ").strip()
    except (EOFError, KeyboardInterrupt):
        return
    if ans.isdigit() and 1 <= int(ans) <= len(items):
        if load_session(session, items[int(ans) - 1]):
            print_info("Oturum yüklendi; kaldığınız yerden devam edebilirsiniz.")
        else:
            print_warning("Oturum okunamadı.")
