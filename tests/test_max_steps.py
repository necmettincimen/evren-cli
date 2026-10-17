"""Tests for the agent step ceiling (plan token-consumption-fixes, Faz 1 / F6)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.agent import AgentSession
from src.config import default_max_steps


def response(content="", finish_reason="stop", tools=()):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            finish_reason=finish_reason,
            message=SimpleNamespace(content=content, tool_calls=list(tools)),
        )],
        usage=None,
    )


class DefaultMaxStepsTest(unittest.TestCase):

    def test_env_override(self):
        with patch.dict(os.environ, {"EVREN_MAX_STEPS": "25"}):
            self.assertEqual(default_max_steps(), 25)



class RunStepCeilingTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.client = MagicMock(default_model="m")
        with patch.dict(os.environ, {"EVREN_AUTO_DOCS": "false"}, clear=False):
            self.session = AgentSession(self.client, workspace_root=Path(self._tmp.name))
        for name in ("print_info", "print_warning", "print_error", "print_markdown",
                     "print_tool_call", "print_tool_result", "print_reasoning"):
            patcher = patch(f"src.agent.{name}")
            patcher.start()
            self.addCleanup(patcher.stop)

    def _run(self, env=None, **kwargs):
        env = env or {"EVREN_MAX_STEPS": "2"}
        # Never-ending tool loop: the ceiling is the only thing that stops it.
        call = SimpleNamespace(
            id="c", function=SimpleNamespace(name="run_command", arguments='{"command":"dir"}')
        )
        self.client.chat_stream_assembled.return_value = response("çalışıyor", tools=[call])
        with patch.dict(os.environ, env, clear=False), \
                patch("src.agent.execute_tool_call", return_value="ÇIKTI"):
            return self.session.run_step("bitmeyen görev", **kwargs)




    def test_zero_still_rejected(self):
        with self.assertRaises(ValueError):
            self.session.run_step("x", max_steps=0)

    def test_final_steps_get_a_warning(self):
        sent = []
        systems = []

        def capture(**kwargs):
            # The warning travels in a transient trailing message; the system
            # message must stay byte-identical so the prompt cache keeps hitting.
            messages = kwargs["messages"]
            systems.append(messages[0]["content"])
            last = messages[-1]
            sent.append(str(last.get("content") or "") if last.get("role") == "user" else "")
            call = SimpleNamespace(
                id="c", function=SimpleNamespace(name="run_command", arguments='{"command":"dir"}')
            )
            return response("devam", tools=[call])

        self.client.chat_stream_assembled.side_effect = capture
        with patch.dict(os.environ, {"EVREN_MAX_STEPS": "4"}, clear=False),                 patch("src.agent.execute_tool_call", return_value="ÇIKTI"):
            self.session.run_step("dört adımlık görev")

        # Warning on the last two steps only (3 and 4 of 4).
        self.assertEqual(len(sent), 4)
        self.assertNotIn("ADIM UYARISI", sent[0])
        self.assertNotIn("ADIM UYARISI", sent[1])
        self.assertIn("ADIM UYARISI", sent[2])
        self.assertIn("ADIM UYARISI", sent[3])
        self.assertEqual(len(set(systems)), 1)
        # The transient note is never persisted into the history.
        self.assertNotIn("ADIM UYARISI", str(self.session.messages))


class CliDefaultTest(unittest.TestCase):
    """`--max-steps` must stay optional so EVREN_MAX_STEPS can drive the default."""

    def parse(self, argv):
        import sys

        from src import cli

        captured = {}

        def fake_agent(args):
            captured["args"] = args
            return 0

        with patch.object(sys, "argv", ["evren", *argv]), \
                patch.object(cli, "cmd_agent", fake_agent):
            cli.main()
        return captured["args"]




if __name__ == "__main__":  # pragma: no cover
    unittest.main()
