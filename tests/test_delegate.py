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

    def test_delegate_hidden_in_ask_and_plan(self):
        # The sub-agent runs in ASK: it must not see delegate or write tools.
        self.assertIn("delegate", ASK_HIDDEN_TOOLS)
        self.assertIn("delegate", PLAN_HIDDEN_TOOLS)
        for mode in (AgentMode.ASK, AgentMode.PLAN):
            names = schema_names(mode)
            self.assertNotIn("delegate", names)
            self.assertFalse(names & {"edit_file", "write_file", "ssh_write_file"})



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
