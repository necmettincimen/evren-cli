"""`evren --stdio` NDJSON protocol: a fake client drives the real StdioServer +
AgentSession + tool dispatcher in-process (scripted model, temp workspace)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src import secret_vault, session_context, ui
from src.stdio_server import StdioServer
from src.todos import get_store
from tests.protocol_schema import SCHEMA_PATH, schema, validate_event

ROOT = Path(__file__).resolve().parent.parent
ENV = {
    "EVREN_AUTO_APPROVE": "false",
    "EVREN_AUTO_DOCS": "false",
    "EVREN_AUTO_ROUTE": "false",
    "EVREN_CLEF_INTENT": "false",
    "EVREN_SEMANTIC_SEARCH": "false",
}


def _client(replies: list[dict]) -> MagicMock:
    queue = list(replies)
    counter = iter(range(1, 1000))

    def chat(**kwargs):
        reply = queue.pop(0) if queue else {"content": "bitti"}
        calls = [
            SimpleNamespace(id=f"call_{next(counter)}", function=SimpleNamespace(
                name=c["name"], arguments=json.dumps(c.get("arguments", {}), ensure_ascii=False)))
            for c in reply.get("tool_calls", [])
        ]
        content = reply.get("content", "")
        if reply.get("reasoning") and kwargs.get("on_reasoning"):
            kwargs["on_reasoning"](reply["reasoning"])
        if content and kwargs.get("on_content"):
            kwargs["on_content"](content)
        message = SimpleNamespace(content=content, reasoning="", tool_calls=calls)
        return SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="tool_calls" if calls else "stop", message=message)], usage=None)

    client = MagicMock(name="client")
    client.default_model = "test-model"
    client.api_key = "test-key"
    client.key_pool = []
    client.reasoning_effort = None
    client.chat_stream_assembled.side_effect = chat
    client.list_models.return_value = ["m1", "m2"]
    client.get_quota.return_value = {"remaining": 10, "limit": 100}
    return client


class Harness:
    def __init__(self, workspace: Path, replies: list[dict]):
        from src.agent import AgentSession

        self.events: list[dict] = []
        self.cond = threading.Condition()
        client = _client(replies)
        self.server = StdioServer(
            lambda: AgentSession(client, workspace_root=workspace), self._write, workspace, "3.11.0",
        )
        self.server.install()

    def _write(self, line: str) -> None:
        assert line.endswith("\n") and "\n" not in line[:-1]
        event = json.loads(line)
        validate_event(event)
        with self.cond:
            self.events.append(event)
            self.cond.notify_all()

    def send(self, **msg) -> None:
        self.server.handle_line(json.dumps({"v": 1, **msg}, ensure_ascii=False))

    def wait_for(self, event_type: str, timeout: float = 15.0, after: int = 0) -> dict:
        deadline = time.monotonic() + timeout
        with self.cond:
            while True:
                for ev in self.events[after:]:
                    if ev["type"] == event_type:
                        return ev
                left = deadline - time.monotonic()
                if left <= 0:
                    raise AssertionError(f"{event_type} gelmedi; olaylar: {[e['type'] for e in self.events]}")
                self.cond.wait(left)

    def types(self) -> list[str]:
        return [e["type"] for e in self.events]


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("EVREN_")}
    env.update(ENV)
    env["EVREN_CLI_HOME"] = str(tmp_path / "cli_home")
    env["HOME"] = env["USERPROFILE"] = str(tmp_path / "home")
    with patch.dict(os.environ, env, clear=True), \
            patch("src.config.EVREN_AUTO_APPROVE", False), \
            patch("src.tools.EVREN_AUTO_APPROVE", False):
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "README.md").write_bytes(b"a\n")
        session_context.current().background = None
        secret_vault.set_active_vault(secret_vault.SecretVault())
        get_store().clear()
        ui._STREAM.update(buffer=[], live=None, pending="")
        try:
            yield ws
        finally:
            ui.set_event_sink(None)
            ui.set_approval_handler(None)
            ui.set_tool_call_id(None)
            get_store().clear()
            ui._STREAM.update(buffer=[], live=None, pending="")


EDIT_REPLY = {"content": "README düzenleniyor.", "reasoning": "düşünüyorum", "tool_calls": [
    {"name": "edit_file", "arguments": {"path": "README.md", "target_snippet": "a",
                                        "replacement_snippet": "b"}}]}


def test_prompt_delta_tool_call_approval_turn_end(workspace):
    h = Harness(workspace, [EDIT_REPLY, {"content": "Tamam."}])
    h.send(type="prompt", id="p1", text="README'yi düzenle")
    proposed = h.wait_for("file_change_proposed")
    req = h.wait_for("approval_request")
    assert proposed["request_id"] == req["request_id"]
    assert proposed["path"] == "README.md"
    assert proposed["old_content"] == "a\n" and proposed["new_content"] == "b\n"
    assert req["kind"] == "edit"
    h.send(type="approval_response", id="a1", request_id=req["request_id"],
           decision="allow", edited_content="c\n")
    end = h.wait_for("turn_end")
    assert end["reply_to"] == "p1" and end["exit_code"] == 0
    assert (workspace / "README.md").read_text(encoding="utf-8") == "c\n"

    types = h.types()
    assert types[0] == "turn_start"
    for t in ("reasoning_delta", "assistant_delta", "tool_call", "tool_result", "usage"):
        assert t in types, t
    call = next(e for e in h.events if e["type"] == "tool_call")
    result = next(e for e in h.events if e["type"] == "tool_result")
    assert call["call_id"] == result["call_id"] == "call_1"
    assert call["name"] == "edit_file" and result["ok"] is True
    assert all(e["v"] == 1 and e["id"] for e in h.events)
    assert types.index("tool_call") < types.index("file_change_proposed") < types.index("tool_result")


def test_deny_keeps_file(workspace):
    h = Harness(workspace, [EDIT_REPLY, {"content": "Peki."}])
    h.send(type="prompt", id="p1", text="düzenle")
    req = h.wait_for("approval_request")
    h.send(type="approval_response", request_id=req["request_id"], decision="deny")
    h.wait_for("turn_end")
    assert (workspace / "README.md").read_text(encoding="utf-8") == "a\n"
    result = next(e for e in h.events if e["type"] == "tool_result")
    assert result["ok"] is False


def test_cancel_releases_pending_approval(workspace):
    h = Harness(workspace, [EDIT_REPLY, {"content": "x"}])
    h.send(type="prompt", id="p1", text="düzenle")
    h.wait_for("approval_request")
    h.send(type="cancel", id="c1")
    h.wait_for("turn_end")
    assert (workspace / "README.md").read_text(encoding="utf-8") == "a\n"
    assert not h.server.busy


def test_invalid_json_unknown_command_and_busy(workspace):
    h = Harness(workspace, [EDIT_REPLY, {"content": "x"}])
    h.server.handle_line("{bozuk")
    assert h.events[-1]["type"] == "error" and h.events[-1]["code"] == "invalid_json"
    h.send(type="uçmak", id="u1")
    assert h.events[-1]["code"] == "unknown_command" and h.events[-1]["reply_to"] == "u1"
    h.server.handle_line(json.dumps({"v": 2, "type": "cancel"}))
    assert h.events[-1]["code"] == "unsupported_version"
    h.send(type="prompt", id="p1", text="düzenle")
    req = h.wait_for("approval_request")
    h.send(type="prompt", id="p2", text="ikinci")
    assert any(e.get("code") == "busy" and e.get("reply_to") == "p2" for e in h.events)
    h.send(type="approval_response", request_id="yok", decision="allow")
    assert h.events[-1]["code"] == "unknown_request"
    h.send(type="approval_response", request_id=req["request_id"], decision="evet")
    assert h.events[-1]["code"] == "invalid_decision"
    h.send(type="approval_response", request_id=req["request_id"], decision="deny")
    h.wait_for("turn_end")


def test_ask_user_round_trip(workspace):
    h = Harness(workspace, [
        {"tool_calls": [{"name": "ask_user", "arguments": {"question": "Hangi dosya?", "kind": "text"}}]},
        {"content": "Anladım."},
    ])
    h.send(type="prompt", id="p1", text="sor bana")
    q = h.wait_for("ask_user")
    assert q["question"] == "Hangi dosya?"
    h.send(type="ask_user_response", request_id=q["request_id"], answer="README.md")
    h.wait_for("turn_end")
    msgs = json.dumps(h.server.session.messages, ensure_ascii=False)
    assert "Kullanıcı yanıtı: README.md" in msgs


def test_sessions_models_mode_and_resume(workspace):
    h = Harness(workspace, [{"content": "Merhaba."}])
    h.send(type="prompt", id="p1", text="selam")
    h.wait_for("turn_end")
    h.send(type="list_sessions", id="l1")
    items = h.events[-1]["items"]
    assert h.events[-1]["type"] == "sessions" and items and items[0]["title"] == "selam"
    h.send(type="new_session", id="n1")
    assert h.events[-1]["type"] == "session_loaded" and h.events[-1]["messages"] == []
    h.send(type="resume", id="r1", session_id=items[0]["session_id"])
    loaded = h.events[-1]
    assert loaded["type"] == "session_loaded"
    assert [m["text"] for m in loaded["messages"] if m["role"] == "user"][0].startswith("selam")
    h.send(type="resume", id="r2", session_id="..\\x")
    assert h.events[-1]["code"] == "unknown_session"
    h.send(type="list_models", id="m1")
    assert h.events[-1]["models"] == ["m1", "m2"]
    h.send(type="set_mode", id="s1", mode="plan")
    assert h.events[-1] == {**h.events[-1], "type": "mode_changed", "mode": "plan"}
    h.send(type="set_mode", id="s2", mode="uç")
    assert h.events[-1]["code"] == "invalid_mode"
    h.send(type="set_model", id="s3", model="m2")
    assert h.events[-1]["type"] == "model_changed" and h.server.session.model == "m2"
    h.send(type="slash", id="q1", command="/quota")
    assert h.events[-1]["type"] == "quota"
    h.send(type="slash", id="q2", command="/deploy")
    assert h.events[-1]["code"] == "unsupported_slash"


def test_tool_result_is_clipped_and_masked(workspace):
    events = []
    ui.set_event_sink(lambda t, d: events.append((t, d)))
    vault = secret_vault.SecretVault()
    secret_vault.set_active_vault(vault)
    vault._number_for("s3cr3t-value-123")
    ui.print_tool_result("run_command", "ok", detail="s3cr3t-value-123 " + "x" * 9000)
    t, d = events[-1]
    assert t == "tool_result" and d["output_truncated"] is True
    assert len(d["summary"].encode("utf-8")) <= 4096 + 64
    assert "s3cr3t-value-123" not in d["summary"]


def test_terminal_path_untouched_without_handler(workspace):
    assert ui._APPROVAL_HANDLER is None
    with patch("src.tools.prompt_confirm", return_value=False) as confirm:
        from src.tools import request_approval

        assert request_approval("command", shown="dir") is not None
        confirm.assert_called_once()


def test_mention_editor_range(workspace):
    from src.extras import expand_mentions

    (workspace / "a.py").write_text("1\n2\n3\n4\n", encoding="utf-8")
    text, _ = expand_mentions("bak @a.py#L2-3", workspace)
    assert "2: 2" in text and "3: 3" in text and "4: 4" not in text


def test_subprocess_stdout_is_protocol_only(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("EVREN_")}
    env.update(ENV)
    env["EVREN_CLI_HOME"] = str(tmp_path / "cli_home")
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, "-m", "src.cli", "--stdio", "--workspace", str(tmp_path)],
        input='{bozuk\n{"v":1,"type":"list_sessions","id":"x"}\n'.encode("utf-8"),
        capture_output=True, cwd=ROOT, env=env, timeout=60,
    )
    lines = [ln for ln in proc.stdout.decode("utf-8").splitlines() if ln.strip()]
    events = [json.loads(ln) for ln in lines]  # every stdout line is JSON
    for e in events:
        validate_event(e)
    assert events[0]["type"] == "ready" and events[0]["version"]
    assert [e["type"] for e in events[1:]] == ["error", "sessions"]
    assert proc.returncode == 0


def test_schema_lists_match_server():
    """Şemadaki komut/olay/hata listeleri sunucu koduyla aynı kalmalı."""
    import re

    from src import stdio_server

    src_text = Path(stdio_server.__file__).read_text(encoding="utf-8")
    s = schema()
    commands = {n[len("_cmd_"):] for n in dir(stdio_server.StdioServer) if n.startswith("_cmd_")}
    assert commands == set(s["x-command-types"])
    codes = set(re.findall(r'code="([a-z_]+)"', src_text))
    assert codes <= set(s["x-error-codes"]), codes - set(s["x-error-codes"])
    assert s["x-protocol-version"] == stdio_server.PROTOCOL_VERSION
    doc = (ROOT / "docs" / "stdio-protocol.md").read_text(encoding="utf-8")
    for name in s["x-event-types"] + s["x-error-codes"]:
        assert f"`{name}" in doc, f"docs/stdio-protocol.md içinde yok: {name}"
    assert SCHEMA_PATH.is_file()
