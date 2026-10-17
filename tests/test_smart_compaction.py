import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.agent import AgentSession
from src.config import context_budget
from src.history_compactor import STUB_PREFIX, SUMMARY_PREFIX, HistoryCompactor
from src.models import context_window_for
from src.token_manager import TokenManager


def _history(steps: int, size: int = 6000) -> list[dict]:
    msgs = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "gorev"}]
    for i in range(steps):
        msgs.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": f"t{i}", "type": "function",
             "function": {"name": "view_file", "arguments": json.dumps({"path": f"src/f{i}.py"})}}
        ]})
        msgs.append({"role": "tool", "tool_call_id": f"t{i}", "name": "view_file",
                     "content": f"--- src/f{i}.py (Satırlar 1-250 / Toplam 900) ---\n" + "x" * size})
    return msgs


def _valid(msgs: list[dict]) -> bool:
    pending: set[str] = set()
    for m in msgs:
        if m["role"] == "assistant":
            if pending:
                return False
            pending = {tc["id"] for tc in m.get("tool_calls") or []}
        elif m["role"] == "tool":
            if m["tool_call_id"] not in pending:
                return False
            pending.discard(m["tool_call_id"])
    return not pending


class SmartCompactionTest(unittest.TestCase):
    def test_hysteresis_stubs_and_summary_fallback(self):
        comp = HistoryCompactor(TokenManager(context_window=200_000), smart=True)
        msgs: list[dict] = _history(0)
        events = 0
        prev = None
        for step in range(40):
            msgs = _history(step + 1) if False else msgs + _history(1)[2:]
            # rename ids so they stay unique
            msgs[-2]["tool_calls"][0]["id"] = f"u{step}"
            msgs[-1]["tool_call_id"] = f"u{step}"
            before = comp.events
            out = comp.compact(msgs, 1000, max_tokens=32000)
            if comp.events > before:
                events += 1
                self.assertTrue(_valid(out))
                self.assertEqual(out[0]["content"], "SYS")
                self.assertEqual(out[1]["content"], "gorev")
            elif prev is not None:
                self.assertEqual(out[:len(prev)], prev[:len(prev)] if len(out) >= len(prev) else out[:len(prev)])
            msgs = out
            prev = out
        self.assertGreaterEqual(events, 1)
        self.assertLess(events, 8)
        text = json.dumps(msgs, ensure_ascii=False)
        self.assertIn(STUB_PREFIX, text + SUMMARY_PREFIX if False else text)

    def test_stub_text(self):
        from src.history_compactor import build_stub

        self.assertIn("view_file src/a.py 1-250 okundu (", build_stub(
            "view_file", {"path": "src/a.py"}, "--- src/a.py (Satırlar 1-250 / Toplam 9) ---\nx"))
        stub = build_stub("run_command", {"command": "pytest -q"}, "Çıkış Kodu: 1\nSTDOUT:\n3 failed, 2 passed")
        self.assertIn('run_command "pytest -q" → Çıkış Kodu 1, 3 failed', stub)

    def test_window_budget_and_read_repeat(self):
        self.assertEqual(context_window_for("qwen3.8-flash-next"), 256_000)
        self.assertEqual(context_window_for("zai/glm-5.3-fp8"), 512_000)
        self.assertEqual(context_window_for("mimo-v2.6-pro"), 1_000_000)
        self.assertEqual(context_window_for("bilinmeyen"), 128_000)
        with patch.dict(os.environ, {"EVREN_CONTEXT_BUDGET": "abc"}):
            self.assertEqual(context_budget(), 64_000)
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"EVREN_AUTO_DOCS": "false"}):
            (Path(tmp) / "a.py").write_text("print(1)\n", encoding="utf-8")
            s = AgentSession(MagicMock(default_model="qwen3.8-flash-next"), workspace_root=Path(tmp))
            self.assertEqual(s.token_manager.context_window, 256_000)
            args = {"path": "a.py"}
            s._step_no = 3
            s.messages.append({"role": "tool", "tool_call_id": "c1", "content": "--- a.py ---\n1 | print(1)"})
            s._note_read("view_file", args, "--- a.py ---", "c1")
            self.assertTrue(s._read_repeat("view_file", args).startswith("BİLGİ: a.py"))
            (Path(tmp) / "a.py").write_text("print(2)\n", encoding="utf-8")
            self.assertEqual(s._read_repeat("view_file", args), "")
            (Path(tmp) / "a.py").write_text("print(1)\n", encoding="utf-8")
            s.messages[-1]["content"] = "[özet] view_file a.py okundu"
            self.assertEqual(s._read_repeat("view_file", args), "")


if __name__ == "__main__":
    unittest.main()
