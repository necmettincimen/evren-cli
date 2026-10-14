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

    def test_appended_notes_do_not_make_a_short_brief_large(self):
        # K8s/target notes add ~1k tokens; the user's 10-word brief must stay "small".
        self._script([response("tamam")])
        with patch.dict(os.environ, {"EVREN_ROUTE_MIN_TOKENS": "200"}):
            self.session.run_step("koyu3-k8s üzerindeki pod durumları konusunda öneri ver")
        self.assertEqual([c["model"] for c in self.calls], ["exec-m"])

    def test_route_can_be_disabled(self):
        self._script([response("tamam")])
        with patch.dict(os.environ, {"EVREN_AUTO_ROUTE": "false"}):
            self.session.run_step(BIG_BRIEF)
        self.assertEqual([c["model"] for c in self.calls], ["exec-m"])

    def test_failed_plan_falls_back_to_normal_execution(self):
        self._script([response("", "content_filter"), response("bitti")])
        self.assertEqual(self.session.run_step(BIG_BRIEF), "bitti")
        self.assertEqual([c["model"] for c in self.calls], ["planner-m", "exec-m"])
        self.assertIs(self.calls[1]["thinking"], False)
        self.assertNotIn("UYGULAMA PLANI", str(self.calls[1]["messages"]))

    def test_executor_model_and_thinking_are_configurable(self):
        self._script([response("1. plan"), response("bitti")])
        with patch.dict(os.environ, {"EVREN_EXECUTOR_MODEL": "fast-m", "EVREN_EXECUTOR_THINKING": "true"}):
            self.session.run_step(BIG_BRIEF)
        self.assertEqual(self.calls[1]["model"], "fast-m")
        self.assertIsNone(self.calls[1]["thinking"])
        self.assertEqual(self.session.model, "exec-m")

    def test_plan_mode_runs_on_the_planner(self):
        self._script([response("plan yazıldı")])
        self.session.mode = AgentMode.PLAN
        self.session.run_step("küçük plan isteği")
        self.assertEqual(self.calls[0]["model"], "planner-m")

    def test_continue_keeps_the_executor_profile(self):
        call = SimpleNamespace(
            id="c", function=SimpleNamespace(name="run_command", arguments='{"command":"dir"}')
        )
        self._script([response("1. plan"), response("devam", tools=[call]), response("bitti")])
        with patch("src.agent.execute_tool_call", return_value="ÇIKTI"):
            self.session.run_step(BIG_BRIEF, max_steps=1)
            self.assertIsNotNone(self.session.pending_mode)
            self.session.continue_task()
        self.assertEqual([c["model"] for c in self.calls], ["planner-m", "exec-m", "exec-m"])
        self.assertIs(self.calls[2]["thinking"], False)

    def test_thinking_can_be_kept_for_execution(self):
        self._script([response("tamam")])
        with patch.dict(os.environ, {"EVREN_EXECUTOR_THINKING": "true"}):
            self.session.run_step("kısa iş")
        self.assertIsNone(self.calls[0]["thinking"])

    def test_ask_mode_keeps_thinking(self):
        self._script([response("cevap")])
        self.session.mode = AgentMode.ASK
        self.session.run_step("bu modül ne yapıyor?")
        self.assertIsNone(self.calls[0]["thinking"])

    def test_stall_retries_with_thinking_off(self):
        self._script([response("", "length"), response("tamam")])
        with patch.dict(os.environ, {"EVREN_EXECUTOR_THINKING": "true"}):
            self.session.run_step("kısa iş")
        self.assertIsNone(self.calls[0]["thinking"])
        self.assertIs(self.calls[1]["thinking"], False)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
