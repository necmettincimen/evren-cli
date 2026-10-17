"""Planner / executor routing: large briefs are planned, then executed fast."""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.agent import AgentSession
from src.modes import AgentMode


def response(content="", finish_reason="stop", tools=()):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            finish_reason=finish_reason,
            message=SimpleNamespace(content=content, tool_calls=list(tools)),
        )],
        usage=None,
    )


ROUTE_ENV = {
    "EVREN_AUTO_DOCS": "false",
    "EVREN_AUTO_ROUTE": "true",
    "EVREN_PLANNER_MODEL": "planner-m",
    "EVREN_ROUTE_MIN_TOKENS": "100",
    "EVREN_PLANNER_STEPS": "3",
}
BIG_BRIEF = "Büyük bir görev: " + "modülü yeniden düzenle ve testleri güncelle. " * 40


class RoutingTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        env = patch.dict(os.environ, ROUTE_ENV, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("EVREN_EXECUTOR_MODEL", None)
        os.environ.pop("EVREN_EXECUTOR_THINKING", None)
        self.client = MagicMock(default_model="exec-m")
        self.session = AgentSession(self.client, workspace_root=Path(self._tmp.name))
        for name in ("print_info", "print_warning", "print_error", "print_markdown",
                     "print_tool_call", "print_tool_result", "print_reasoning"):
            patcher = patch(f"src.agent.{name}")
            patcher.start()
            self.addCleanup(patcher.stop)
        self.calls = []

    def _script(self, replies):
        replies = list(replies)

        def reply(**kwargs):
            self.calls.append({
                "model": kwargs["model"],
                "thinking": kwargs.get("thinking"),
                "tools": {t["function"]["name"] for t in kwargs.get("tools") or []},
                "messages": kwargs["messages"],
            })
            return replies.pop(0)

        self.client.chat_stream_assembled.side_effect = reply

    def test_large_brief_is_planned_then_executed_without_thinking(self):
        self._script([response("1. a.py içinde f'yi düzelt\n2. pytest"), response("bitti")])
        self.assertEqual(self.session.run_step(BIG_BRIEF), "bitti")
        planner, executor = self.calls
        self.assertEqual(planner["model"], "planner-m")
        self.assertIsNone(planner["thinking"])
        self.assertNotIn("edit_file", planner["tools"])  # planner is read-only
        self.assertIn("PLANLAYICI", str(planner["messages"]))
        self.assertEqual(executor["model"], "exec-m")
        self.assertIs(executor["thinking"], False)
        self.assertIn("edit_file", executor["tools"])
        self.assertIn("UYGULAMA PLANI", executor["messages"][-1]["content"])
        self.assertNotIn("PLANLAYICI", str(executor["messages"]))
        # The session model is restored after the task.
        self.assertEqual(self.session.model, "exec-m")

    def test_small_brief_goes_straight_to_the_session_model(self):
        self._script([response("tamam")])
        self.session.run_step("README yazım hatasını düzelt")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["model"], "exec-m")
        # Normal-mode execution runs without hidden reasoning by default.
        self.assertIs(self.calls[0]["thinking"], False)









    def test_stall_retries_with_thinking_off(self):
        self._script([response("", "length"), response("tamam")])
        with patch.dict(os.environ, {"EVREN_EXECUTOR_THINKING": "true"}):
            self.session.run_step("kısa iş")
        self.assertIsNone(self.calls[0]["thinking"])
        self.assertIs(self.calls[1]["thinking"], False)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
