"""`evren --stdio`: long-lived NDJSON protocol for editor front ends (VS Code).

stdin carries one JSON command per line, stdout one JSON event per line; every
other byte the CLI prints (rich, print, child processes) goes to stderr. The
agent runs on a worker thread, the stdin reader stays on the main thread, and
approvals / ask_user questions block the worker until the matching response
arrives. Protocol reference: docs/stdio-protocol.md.
"""

from __future__ import annotations

import itertools
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any, Callable

PROTOCOL_VERSION = 1
# A pending approval re-checks cancellation this often while it waits.
_WAIT_SLICE = 0.2

_APPROVAL_KINDS = {"edit": "edit", "command": "command", "ssh": "ssh"}
_DECISIONS = ("allow", "deny", "always")


class StdioServer:
    """Protocol state machine; I/O is injected so tests can drive it in-process."""

    def __init__(self, session_factory: Callable[[], Any], write: Callable[[str], None],
                 workspace: Path, version: str = ""):
        self._factory = session_factory
        self._write = write
        self._out_lock = threading.Lock()
        self._ids = itertools.count(1)
        self._pending: dict[str, dict] = {}
        self._pending_lock = threading.Lock()
        self._cancelled = threading.Event()
        self._worker: threading.Thread | None = None
        self.workspace = Path(workspace)
        self.version = version
        self.session = session_factory()

    # ------------------------------------------------------------------ output

    def send(self, event_type: str, reply_to: str | None = None, **data) -> None:
        msg = {"v": PROTOCOL_VERSION, "type": event_type, "id": f"s{next(self._ids)}"}
        if reply_to is not None:
            msg["reply_to"] = reply_to
        msg.update(data)
        line = json.dumps(msg, ensure_ascii=False, default=str)
        with self._out_lock:
            self._write(line + "\n")

    def _on_event(self, event_type: str, data: dict) -> None:
        if event_type == "tool_call" and not data.get("call_id"):
            data = {**data, "call_id": f"c{next(self._ids)}"}
        self.send(event_type, **data)

    def install(self) -> None:
        from src import ui

        ui.set_event_sink(self._on_event)
        ui.set_approval_handler(self._handle_approval)

    def uninstall(self) -> None:
        from src import ui

        ui.set_event_sink(None)
        ui.set_approval_handler(None)

    # --------------------------------------------------------------- approvals

    def _handle_approval(self, request: dict) -> dict:
        """Runs on the worker thread: emits the request, waits for the answer."""
        rid = f"r{next(self._ids)}"
        if request.get("kind") == "ask_user":
            slot = self._open(rid)
            self.send("ask_user", request_id=rid, question=request.get("question", ""),
                      options=request.get("options"))
            reply = self._wait(rid, slot)
            return {"answer": reply.get("answer") if reply else None}
        proposal = request.get("proposal")
        slot = self._open(rid)
        if proposal is not None:
            # File contents are not vault-masked: an edited copy comes back and
            # is written verbatim, and masking would write placeholders to disk.
            self.send("file_change_proposed", request_id=rid, path=proposal.get("path", ""),
                      old_content=proposal.get("old", ""), new_content=proposal.get("new", ""))
        from src.sessions import _mask

        self.send("approval_request", request_id=rid,
                  kind=_APPROVAL_KINDS.get(request.get("kind", ""), "other"),
                  title=_mask(request.get("title", "")), detail=_mask(request.get("detail", "")))
        reply = self._wait(rid, slot)
        return reply or {"decision": "deny"}

    def _open(self, rid: str) -> dict:
        slot = {"event": threading.Event(), "reply": None}
        with self._pending_lock:
            self._pending[rid] = slot
        return slot

    def _wait(self, rid: str, slot: dict) -> dict | None:
        try:
            while not slot["event"].wait(_WAIT_SLICE):
                if self._cancelled.is_set():
                    return None
            return slot["reply"]
        finally:
            with self._pending_lock:
                self._pending.pop(rid, None)

    def _resolve(self, rid: str, reply: dict) -> bool:
        with self._pending_lock:
            slot = self._pending.get(rid)
        if slot is None:
            return False
        slot["reply"] = reply
        slot["event"].set()
        return True

    def _release_all(self) -> None:
        with self._pending_lock:
            slots = list(self._pending.values())
        for slot in slots:
            slot["event"].set()

    # ----------------------------------------------------------------- dispatch

    @property
    def busy(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def handle_line(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            self.send("error", message=f"Geçersiz JSON: {e}", code="invalid_json")
            return
        if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
            self.send("error", message="Mesaj 'type' alanı olan bir JSON nesnesi olmalı.",
                      code="invalid_message")
            return
        cid = msg.get("id")
        if msg.get("v", PROTOCOL_VERSION) != PROTOCOL_VERSION:
            self.send("error", reply_to=cid, code="unsupported_version",
                      message=f"Desteklenen protokol sürümü: {PROTOCOL_VERSION}")
            return
        handler = getattr(self, "_cmd_" + msg["type"], None)
        if handler is None:
            self.send("error", reply_to=cid, code="unknown_command",
                      message=f"Bilinmeyen komut: {msg['type']}")
            return
        try:
            handler(msg, cid)
        except Exception as e:
            self.send("error", reply_to=cid, code="internal", message=f"{type(e).__name__}: {e}")

    def _busy_error(self, cid) -> bool:
        if self.busy:
            self.send("error", reply_to=cid, code="busy", message="Ajan çalışıyor; önce cancel gönderin.")
            return True
        return False

    # ------------------------------------------------------------------ commands

    def _cmd_prompt(self, msg: dict, cid) -> None:
        if self._busy_error(cid):
            return
        text = str(msg.get("text") or "")
        if not text.strip():
            self.send("error", reply_to=cid, code="empty_prompt", message="Boş istek.")
            return
        mode = msg.get("mode")
        if mode in ("ask", "plan"):
            text = f"{mode}: {text}"
        mentions = [_mention(a) for a in msg.get("attachments") or [] if isinstance(a, dict) and a.get("path")]
        if mentions:
            text = text + "\n\n" + " ".join(mentions)
        self._cancelled.clear()
        self._worker = threading.Thread(target=self._run_turn, args=(text, cid), daemon=True)
        self._worker.start()

    def _run_turn(self, text: str, cid) -> None:
        from src.extras import _is_evren_file, _status_lines, expand_mentions
        from src.interrupt import STOP

        session = self.session
        self.send("turn_start", reply_to=cid)
        STOP.clear()
        before = _status_lines(self.workspace)
        result, code = "", 0
        try:
            expanded, warnings = expand_mentions(text, self.workspace)
            for w in warnings:
                self.send("error", reply_to=cid, code="mention", message=w)
            result = session.run_step(expanded, compact=True) or ""
            if result.startswith(("HATA:", "İPTAL:")):
                code = 1
        except Exception as e:
            result, code = f"HATA: {e}", 1
        finally:
            STOP.clear()
            try:
                from src.ui import flush_markdown

                flush_markdown()
            except Exception:
                pass
        changed = [ln[3:].strip() for ln in _status_lines(self.workspace)
                   if ln not in before and not _is_evren_file(ln[3:].strip())]
        try:
            from src.sessions import save_session

            save_session(session)
        except Exception:
            pass
        self._send_usage(cid)
        self.send("turn_end", reply_to=cid, result=result, changed_files=changed, exit_code=code)

    def _send_usage(self, cid=None) -> None:
        try:
            usage = self.session.token_report().get("session_usage") or {}
        except Exception:
            usage = {}
        self.send("usage", reply_to=cid, prompt_tokens=int(usage.get("prompt_tokens") or 0),
                  completion_tokens=int(usage.get("completion_tokens") or 0))

    def _cmd_cancel(self, msg: dict, cid) -> None:
        from src.interrupt import STOP

        if not self.busy:
            return
        self._cancelled.set()
        STOP.set()
        self._release_all()

    def _cmd_approval_response(self, msg: dict, cid) -> None:
        decision = msg.get("decision")
        if decision not in _DECISIONS:
            self.send("error", reply_to=cid, code="invalid_decision",
                      message=f"decision şunlardan biri olmalı: {', '.join(_DECISIONS)}")
            return
        reply = {"decision": decision}
        if isinstance(msg.get("edited_content"), str):
            reply["edited_content"] = msg["edited_content"]
        if not self._resolve(str(msg.get("request_id")), reply):
            self.send("error", reply_to=cid, code="unknown_request", message="Bekleyen böyle bir onay yok.")

    def _cmd_ask_user_response(self, msg: dict, cid) -> None:
        answer = msg.get("answer")
        if not self._resolve(str(msg.get("request_id")), {"answer": answer if isinstance(answer, str) else None}):
            self.send("error", reply_to=cid, code="unknown_request", message="Bekleyen böyle bir soru yok.")

    def _cmd_set_model(self, msg: dict, cid) -> None:
        if self._busy_error(cid):
            return
        model = str(msg.get("model") or "").strip()
        if not model:
            self.send("error", reply_to=cid, code="invalid_model", message="Model adı boş.")
            return
        self.session.set_model(model)
        self.send("model_changed", reply_to=cid, model=self.session.model)

    def _cmd_set_mode(self, msg: dict, cid) -> None:
        from src.modes import AgentMode

        if self._busy_error(cid):
            return
        try:
            mode = AgentMode(msg.get("mode"))
        except ValueError:
            self.send("error", reply_to=cid, code="invalid_mode", message="Kip: normal | plan | ask")
            return
        self.session.set_mode(mode)
        self.send("mode_changed", reply_to=cid, mode=mode.value)

    def _cmd_list_sessions(self, msg: dict, cid) -> None:
        from src.sessions import list_sessions

        items = []
        for p in list_sessions(self.workspace, int(msg.get("limit") or 20)):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                data = {}
            items.append({"session_id": p.stem, "title": data.get("title", ""),
                          "model": data.get("model", ""), "mode": data.get("mode", "")})
        self.send("sessions", reply_to=cid, items=items)

    def _cmd_resume(self, msg: dict, cid) -> None:
        from src.sessions import _dir, load_session

        if self._busy_error(cid):
            return
        sid = str(msg.get("session_id") or "")
        path = _dir(self.workspace) / f"{sid}.json"
        if not sid or "/" in sid or "\\" in sid or not path.is_file():
            self.send("error", reply_to=cid, code="unknown_session", message=f"Oturum yok: {sid}")
            return
        self.session = self._factory()
        if not load_session(self.session, path):
            self.send("error", reply_to=cid, code="unknown_session", message=f"Oturum okunamadı: {sid}")
            return
        self.send("session_loaded", reply_to=cid, session_id=sid, messages=_history(self.session))

    def _cmd_new_session(self, msg: dict, cid) -> None:
        if self._busy_error(cid):
            return
        from src.todos import get_store

        self.session = self._factory()
        get_store().clear()
        self.send("session_loaded", reply_to=cid, session_id=None, messages=[])

    def _cmd_list_models(self, msg: dict, cid) -> None:
        try:
            models = list(self.session.client.list_models() or [])
        except Exception:
            models = []
        self.send("models", reply_to=cid, models=models, current=self.session.model)

    def _cmd_quota(self, msg: dict, cid) -> None:
        try:
            quota = self.session.client.get_quota()
        except Exception as e:
            self.send("error", reply_to=cid, code="quota", message=str(e))
            return
        from src.sessions import _mask

        self.send("quota", reply_to=cid, quota=_mask(quota if isinstance(quota, dict) else {"raw": quota}))

    def _cmd_slash(self, msg: dict, cid) -> None:
        """A small, safe subset of REPL slash commands mapped onto protocol commands."""
        command = str(msg.get("command") or "").lstrip("/").lower()
        args = str(msg.get("args") or "").strip()
        mapped = {
            "model": lambda: self._cmd_set_model({"model": args}, cid) if args else self._cmd_list_models({}, cid),
            "mode": lambda: self._cmd_set_mode({"mode": args}, cid),
            "new": lambda: self._cmd_new_session({}, cid),
            "clear": lambda: self._cmd_new_session({}, cid),
            "resume": lambda: self._cmd_resume({"session_id": args}, cid) if args else self._cmd_list_sessions({}, cid),
            "sessions": lambda: self._cmd_list_sessions({}, cid),
            "models": lambda: self._cmd_list_models({}, cid),
            "quota": lambda: self._cmd_quota({}, cid),
            "tokens": lambda: self._send_usage(cid),
            "todos": lambda: self._send_todos(cid),
        }.get(command)
        if mapped is None:
            self.send("error", reply_to=cid, code="unsupported_slash",
                      message=f"/{command} stdio kipinde desteklenmiyor.")
            return
        mapped()

    def _send_todos(self, cid=None) -> None:
        from src.todos import get_store

        self.send("todos", reply_to=cid, items=get_store().snapshot())


def _mention(att: dict) -> str:
    path = str(att["path"]).replace("\\", "/")
    rng = att.get("range")
    if isinstance(rng, dict) and rng.get("start"):
        return f"@{path}:{int(rng['start'])}-{int(rng.get('end') or rng['start'])}"
    return f"@{path}"


def _history(session) -> list[dict]:
    """User/assistant text turns for re-rendering a resumed chat."""
    out = []
    for m in session.messages:
        if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str) and m["content"].strip():
            out.append({"role": m["role"], "text": m["content"]})
    return out


def run_stdio(args) -> int:
    """Entry point for `evren --stdio`; returns the process exit code."""
    # Keep the real stdout for the protocol, then point fd 1 at stderr so rich,
    # print() and child processes can never corrupt the NDJSON stream. stdin is
    # taken the same way and fd 0 becomes NUL, so a stray input() gets EOF
    # instead of swallowing protocol lines.
    proto_out = os.fdopen(os.dup(1), "w", encoding="utf-8", newline="\n", buffering=1)
    proto_in = os.fdopen(os.dup(0), "rb", buffering=0)
    sys.stdout.flush()
    os.dup2(2, 1)
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.close(devnull)
    sys.stdout = sys.stderr
    sys.stdin = open(os.devnull, "r", encoding="utf-8")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ["EVREN_HEADLESS"] = "1"

    from src import settings as _settings
    from src.agent import AgentSession
    from src.api_client import EvrenClient
    from src.config import get_workspace_dir
    from src.version import get_version

    workspace = get_workspace_dir(args.workspace)
    _settings.set_workspace(workspace)
    _settings.EXTRA_ALLOW[:] = [t.strip() for t in (getattr(args, "allowed_tools", None) or "").split(",") if t.strip()]
    client = EvrenClient(default_model=args.model)

    def factory():
        return AgentSession(client=client, workspace_root=workspace, model=args.model,
                            auto_approve=bool(getattr(args, "yes", False)))

    def write(text: str) -> None:
        proto_out.write(text)
        proto_out.flush()

    server = StdioServer(factory, write, workspace, version=get_version())
    if getattr(args, "continue_last", False):
        from src.sessions import list_sessions, load_session

        found = list_sessions(workspace, 1)
        if found:
            load_session(server.session, found[0])
    server.install()
    server.send("ready", version=server.version, model=server.session.model,
                workspace=str(workspace), protocol=PROTOCOL_VERSION)
    try:
        for raw in iter(proto_in.readline, b""):
            server.handle_line(raw.decode("utf-8", errors="replace"))
    except KeyboardInterrupt:
        pass
    finally:
        server._cmd_cancel({}, None)
        if server._worker is not None:
            server._worker.join(timeout=5)
        server.uninstall()
    return 0
