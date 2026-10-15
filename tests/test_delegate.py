"""Tests for the `delegate` sub-agent tool (Task-style delegation).

The client is fully mocked; no real API calls are made. The mock returns two
turns for the sub-agent: one read-only `view_file` tool call, then a summary.
"""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.agent import AgentSession
from src.modes import AgentMode
from src.tool_gateway import check_tool_allowed
from src.tools import ASK_HIDDEN_TOOLS, PLAN_HIDDEN_TOOLS, TOOLS_SCHEMA, tools_for_mode


def response(content="", finish_reason="stop", tools=()):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            finish_reason=finish_reason,
            message=SimpleNamespace(content=content, tool_calls=list(tools)),
        )],
        usage=None,
    )


def tool_call(arguments, name):
    return SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def schema_names(mode):
    return {t["function"]["name"] for t in tools_for_mode(mode)}


class DelegateTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.client = MagicMock(default_model="test-model")
        self.session = AgentSession(self.client, workspace_root=Path(self.tmp.name))
        for name in ("print_info", "print_warning", "print_error", "print_markdown",
                     "print_tool_call", "print_tool_result"):
            patcher = patch(f"src.agent.{name}")
            patcher.start()
            self.addCleanup(patcher.stop)


class TestDelegateSchema(unittest.TestCase):
    def test_delegate_in_normal_schema(self):
        self.assertIn("delegate", schema_names(AgentMode.NORMAL))
        names = [t["function"]["name"] for t in TOOLS_SCHEMA]
        self.assertEqual(names.count("delegate"), 1)

    def test_delegate_hidden_in_ask_and_plan(self):
        # The sub-agent runs in ASK: it must not see delegate or write tools.
        self.assertIn("delegate", ASK_HIDDEN_TOOLS)
        self.assertIn("delegate", PLAN_HIDDEN_TOOLS)
        for mode in (AgentMode.ASK, AgentMode.PLAN):
            names = schema_names(mode)
            self.assertNotIn("delegate", names)
            self.assertFalse(names & {"edit_file", "write_file", "ssh_write_file"})

    def test_gateway_denies_delegate_outside_normal(self):
        for mode in (AgentMode.ASK, AgentMode.PLAN):
            allowed, reason = check_tool_allowed("delegate", {"task": "x"}, mode)
            self.assertFalse(allowed, mode)
            self.assertIn("normal", reason)
        allowed, _ = check_tool_allowed("delegate", {"task": "x"}, AgentMode.NORMAL)
        self.assertTrue(allowed)


class TestDelegateRun(DelegateTestBase):
    def test_run_delegate_returns_summary_and_restores_state(self):
        parent_history = list(self.session.messages)
        self.client.chat_stream_assembled.side_effect = [
            response("", "tool_calls", [tool_call(json.dumps({"path": "src/agent.py"}), "view_file")]),
            response("AgentSession src/agent.py içinde tanımlı."),
        ]
        result = self.session._run_delegate("AgentSession nerede tanımlı?")

        self.assertTrue(result.startswith("ALT AJAN ÖZETİ:"))
        self.assertIn("src/agent.py içinde tanımlı", result)
        # Parent history restored verbatim: only the summary leaves the sub-run.
        self.assertEqual(self.session.messages, parent_history)
        self.assertEqual(self.session.mode, AgentMode.NORMAL)
        self.assertIsNone(self.session.pending_mode)

        # The sub-agent ran read-only: ASK tool list was advertised and the
        # sub-session saw only system + task + its own turns.
        first_request = self.client.chat_stream_assembled.call_args_list[0].kwargs
        self.assertEqual(first_request["tool_choice"], None)
        advertised = {t["function"]["name"] for t in first_request["tools"]}
        self.assertNotIn("delegate", advertised)
        self.assertNotIn("edit_file", advertised)
        sub_messages = first_request["messages"]
        self.assertEqual(sub_messages[0]["role"], "system")
        self.assertIn("AgentSession nerede tanımlı?", sub_messages[1]["content"])
        self.assertIn("ALT GÖREV", sub_messages[1]["content"])

    def test_run_delegate_empty_task_is_error(self):
        self.assertTrue(self.session._run_delegate("  ").startswith("HATA:"))
        self.assertEqual(self.client.chat_stream_assembled.call_count, 0)

    def test_run_delegate_caps_steps(self):
        self.client.chat_stream_assembled.return_value = response("ok")
        self.session._run_delegate("görev", 999)
        # Loop ends on the first text answer; the cap only bounds the ceiling.
        self.assertEqual(self.client.chat_stream_assembled.call_count, 1)
        self.session._run_delegate("görev", "olur-a")  # lenient args must not crash
        self.assertEqual(self.client.chat_stream_assembled.call_count, 2)


class TestDelegateThroughRunStep(DelegateTestBase):
    def test_delegate_captured_in_run_step_history_gets_only_summary(self):
        self.client.chat_stream_assembled.side_effect = [
            # Parent turn 1: delegate the research task.
            response("", "tool_calls", [
                tool_call(json.dumps({"task": "X nerede tanımlı?"}), "delegate"),
            ]),
            # Sub-agent turn 1: a read-only tool call.
            response("", "tool_calls", [
                tool_call(json.dumps({"path": "src/agent.py"}), "view_file"),
            ]),
            # Sub-agent turn 2: the summary.
            response("X, src/agent.py içinde tanımlı."),
            # Parent turn 2: final answer.
            response("Araştırma tamam."),
        ]
        with patch("src.agent.execute_tool_call") as exec_tool:
            result = self.session.run_step("X'i bul", max_steps=10, route=False)

        self.assertEqual(result, "Araştırma tamam.")
        # delegate itself never reached the stateless dispatcher; only the
        # sub-agent's read-only view_file went through it.
        delegated = [c for c in exec_tool.call_args_list if c.kwargs.get("name") == "delegate"]
        self.assertEqual(delegated, [])

        tool_results = [m for m in self.session.messages if m["role"] == "tool"]
        self.assertEqual(len(tool_results), 1)
        self.assertEqual(tool_results[0]["name"], "delegate")
        self.assertIn("ALT AJAN ÖZETİ:", tool_results[0]["content"])
        self.assertIn("src/agent.py içinde tanımlı", tool_results[0]["content"])

        # The sub-agent's internal turns (its view_file call/result) must NOT
        # leak into the parent history.
        self.assertNotIn("view_file", [m.get("name") for m in tool_results])
        roles = [m["role"] for m in self.session.messages]
        self.assertEqual(roles, ["system", "user", "assistant", "tool", "assistant"])

        # Mode restored to NORMAL after the sub-run.
        self.assertEqual(self.session.mode, AgentMode.NORMAL)

    def test_delegate_denied_in_ask_mode_via_run_step(self):
        # A function-scoped side_effect list can be consumed by an earlier test
        # in the same class (shared MagicMock); reset it right before the run.
        self.client.chat_stream_assembled.reset_mock()
        self.client.chat_stream_assembled.side_effect = [
            response("", "tool_calls", [
                tool_call(json.dumps({"task": "araştır"}), "delegate"),
            ]),
            response("vazgeçtim"),
        ]
        result = self.session.run_step("ask: bir şeye bak", max_steps=5, route=False)
        self.assertEqual(result, "vazgeçtim")
        tool_results = [m for m in self.session.messages if m["role"] == "tool"]
        self.assertTrue(tool_results[0]["content"].startswith("REDDEDİLDİ:"))
        # Only the parent's two turns ran; no sub-agent session was spawned.
        self.assertEqual(self.client.chat_stream_assembled.call_count, 2)


if __name__ == "__main__":
    unittest.main()


class DelegateRecursionTest(unittest.TestCase):
    def test_sub_agent_cannot_delegate_again(self):
        import os

        client = MagicMock(default_model="m")
        sent_modes = []
        replies = [
            # parent (normal) -> delegate
            response("", "tool_calls", [tool_call(json.dumps({"task": "X nerede?"}), "delegate")]),
            # sub-agent (ask) tries to delegate again -> must be denied by the gate
            response("", "tool_calls", [tool_call(json.dumps({"task": "tekrar"}), "delegate")]),
            # sub-agent answers
            response("X src/a.py:3'te tanımlı."),
            # parent finishes
            response("bitti"),
        ]

        def reply(**kwargs):
            sent_modes.append({t["function"]["name"] for t in kwargs.get("tools") or []})
            return replies.pop(0)

        client.chat_stream_assembled.side_effect = reply
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"EVREN_AUTO_DOCS": "false", "EVREN_AUTO_ROUTE": "false"}), \
                patch("src.agent.print_info"), patch("src.agent.print_tool_call"), \
                patch("src.agent.print_tool_result"), patch("src.agent.print_markdown"):
            session = AgentSession(client, workspace_root=Path(directory))
            self.assertEqual(session.run_step("araştır"), "bitti")
        self.assertIn("delegate", sent_modes[0])          # parent sees delegate
        self.assertNotIn("delegate", sent_modes[1])       # sub-agent does not
        tool_msgs = [m for m in session.messages if m.get("role") == "tool"]
        self.assertEqual(len(tool_msgs), 1)               # only the summary reached the parent
        self.assertIn("ALT AJAN ÖZETİ", tool_msgs[0]["content"])
        self.assertIn("src/a.py:3", tool_msgs[0]["content"])
