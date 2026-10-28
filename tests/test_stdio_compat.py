"""`evren --stdio` uyumluluk testi: gerçek alt süreç, sahte yerel model, LLM yok.

Her stdout satırı protocol/stdio-v1.schema.json'a göre doğrulanır. Ayar komutları,
hata kodları ve iki tam tur (düz metin + onaylı dosya yazımı) koşulur. CI'da
Ubuntu ve Windows'ta çalışır; evren-vscode aynı sahte sunucuyu kullanır.
"""

from __future__ import annotations

import importlib.util
import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from tests.protocol_schema import validate_command, validate_event

ROOT = Path(__file__).resolve().parent.parent
TIMEOUT = 60


def _load_fake():
    spec = importlib.util.spec_from_file_location("fake_llm_server", ROOT / "scripts" / "fake_llm_server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def fake_llm():
    mod = _load_fake()
    server = mod.serve(0)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    server.shutdown()
    server.server_close()


class Cli:
    def __init__(self, base_url: str, home: Path, workspace: Path):
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("EVREN_")}
        env.update({
            "EVREN_API_KEY": "fake-key",
            "EVREN_BASE_URL": base_url,
            "EVREN_DEFAULT_MODEL": "fake-model",
            "EVREN_CLI_HOME": str(home),
            "EVREN_AUTO_APPROVE": "false",
            "EVREN_AUTO_DOCS": "false",
            "EVREN_AUTO_ROUTE": "false",
            "EVREN_CLEF_INTENT": "false",
            "EVREN_SEMANTIC_SEARCH": "false",
            "PYTHONIOENCODING": "utf-8",
        })
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "src.cli", "--stdio", "--workspace", str(workspace)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=ROOT, env=env,
        )
        self.events: queue.Queue = queue.Queue()
        self.stderr: list[str] = []
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._read_err, daemon=True).start()

    def _read(self):
        for raw in self.proc.stdout:
            line = raw.decode("utf-8")
            if line.strip():
                self.events.put(json.loads(line))  # stdout satırı JSON değilse test düşer
        self.events.put(None)

    def _read_err(self):
        for raw in self.proc.stderr:
            self.stderr.append(raw.decode("utf-8", errors="replace"))

    def raw(self, text: str) -> None:
        self.proc.stdin.write((text + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def send(self, msg: dict, check: bool = True) -> None:
        msg = {"v": 1, **msg}
        if check:
            validate_command(msg)
        self.raw(json.dumps(msg, ensure_ascii=False))

    def next(self) -> dict:
        try:
            ev = self.events.get(timeout=TIMEOUT)
        except queue.Empty:
            raise AssertionError("olay gelmedi; stderr:\n" + "".join(self.stderr[-30:])) from None
        assert ev is not None, "süreç erken kapandı; stderr:\n" + "".join(self.stderr[-30:])
        validate_event(ev)
        return ev

    def until(self, type_: str, collect: list | None = None) -> dict:
        while True:
            ev = self.next()
            if collect is not None:
                collect.append(ev)
            if ev["type"] == type_:
                return ev

    def ask(self, msg: dict, check: bool = True) -> dict:
        """Komutu gönderir, ona verilen ilk yanıt olayını döndürür."""
        self.send(msg, check)
        while True:
            ev = self.next()
            if ev.get("reply_to") == msg.get("id"):
                return ev

    def close(self) -> int:
        self.proc.stdin.close()
        try:
            return self.proc.wait(timeout=TIMEOUT)
        finally:
            if self.proc.poll() is None:
                self.proc.kill()


@pytest.fixture()
def cli(fake_llm, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    c = Cli(fake_llm, tmp_path / "home", ws)
    c.workspace = ws
    yield c
    if c.proc.poll() is None:
        c.proc.kill()


def test_settings_commands_and_error_codes(cli):
    ready = cli.next()
    assert ready["type"] == "ready" and ready["protocol"] == 1
    assert ready["version"]

    models = cli.ask({"type": "list_models", "id": "m"})
    assert models["type"] == "models" and "fake-model" in models["models"]

    assert cli.ask({"type": "set_mode", "id": "a", "mode": "plan"})["mode"] == "plan"
    assert cli.ask({"type": "set_mode", "id": "a2", "mode": "normal"})["mode"] == "normal"
    assert cli.ask({"type": "set_model", "id": "b", "model": "fake-model-2"})["model"] == "fake-model-2"
    assert cli.ask({"type": "list_sessions", "id": "c"})["type"] == "sessions"
    assert cli.ask({"type": "new_session", "id": "d"})["type"] == "session_loaded"

    def code(msg: dict) -> str:
        ev = cli.ask(msg, check=False)  # bilerek hatalı komutlar
        assert ev["type"] == "error", ev
        return ev["code"]

    cli.raw("{bozuk")
    assert cli.next()["code"] == "invalid_json"
    cli.raw("[1,2]")
    assert cli.next()["code"] == "invalid_message"
    assert code({"v": 2, "type": "list_models", "id": "e1"}) == "unsupported_version"
    cli.raw(json.dumps({"v": 1, "type": "uydurma", "id": "e2"}))
    assert cli.until("error")["code"] == "unknown_command"
    assert code({"type": "set_mode", "id": "e3", "mode": "x"}) == "invalid_mode"
    assert code({"type": "set_model", "id": "e4", "model": ""}) == "invalid_model"
    assert code({"type": "resume", "id": "e5", "session_id": "yok-boyle-oturum"}) == "unknown_session"
    assert code({"type": "slash", "id": "e6", "command": "exit"}) == "unsupported_slash"
    assert code({"type": "prompt", "id": "e7", "text": "  "}) == "empty_prompt"
    assert code({"type": "approval_response", "id": "e8", "request_id": "r0", "decision": "allow"}) == "unknown_request"
    assert code({"type": "ask_user_response", "id": "e9", "request_id": "r0", "answer": "x"}) == "unknown_request"

    assert cli.close() == 0


def test_text_turn_and_approved_write(cli):
    assert cli.next()["type"] == "ready"

    seen: list = []
    cli.send({"type": "prompt", "id": "p1", "text": "Merhaba"})
    end = cli.until("turn_end", seen)
    types = [e["type"] for e in seen]
    assert types[0] == "turn_start" and "assistant_delta" in types and "usage" in types
    assert end["exit_code"] == 0 and "sahte model" in "".join(
        e["text"] for e in seen if e["type"] == "assistant_delta")

    seen = []
    cli.send({"type": "prompt", "id": "p2", "text": "dosya: notlar.txt"})
    req = cli.until("approval_request", seen)
    assert req["kind"] == "edit"
    assert any(e["type"] == "file_change_proposed" and e["request_id"] == req["request_id"] for e in seen)
    cli.send({"type": "approval_response", "request_id": req["request_id"], "decision": "allow"})
    result = cli.until("tool_result")
    assert result["ok"] is True, result
    assert cli.until("turn_end")["exit_code"] == 0
    assert (cli.workspace / "notlar.txt").read_text(encoding="utf-8") == "sahte model içeriği\n"

    sessions = cli.ask({"type": "list_sessions", "id": "s"})
    assert sessions["items"], "tur sonrası oturum kaydedilmeli"
    assert cli.close() == 0
