"""Repeat guard: identical tool calls in one task must not loop forever.

Reproduces a real REPL session where the model re-ran the same
`git -C portal-backend grep ...` 25+ times per turn with identical output.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.agent import AgentSession
from src.tool_args import normalize_tool_arguments
from src.tools import TOOL_ARGUMENT_REQUIRED, TOOL_ARGUMENT_TYPES

GREP = 'git -C portal-backend grep -rn -E "TransferWorker|PreparationWorker"'


def call(name, args, call_id="c"):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(args)))


def reply(content="", tools=()):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            finish_reason="tool_calls" if tools else "stop",
            message=SimpleNamespace(content=content, tool_calls=list(tools)),
        )],
        usage=None,
    )


class RepeatGuardTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        env = patch.dict(os.environ, {"EVREN_AUTO_DOCS": "false", "EVREN_AUTO_ROUTE": "false"})
        env.start()
        self.addCleanup(env.stop)
        self.client = MagicMock(default_model="m")
        self.session = AgentSession(self.client, workspace_root=Path(self._tmp.name))
        for name in ("print_info", "print_warning", "print_error", "print_markdown",
                     "print_tool_call", "print_tool_result", "print_reasoning"):
            patcher = patch(f"src.agent.{name}")
            patcher.start()
            self.addCleanup(patcher.stop)

    def _run(self, replies, executor_output="Presentation/Program.cs:1: using X;\n" * 20):
        sent = []

        def fake(**kwargs):
            sent.append(kwargs.get("tool_choice"))
            return replies.pop(0)

        self.client.chat_stream_assembled.side_effect = fake
        with patch("src.agent.execute_tool_call", return_value=executor_output) as execute:
            result = self.session.run_step("kalan işi yap")
        return result, execute, sent

    def test_identical_grep_runs_once_then_loop_is_broken(self):
        replies = [reply(tools=[call("run_command", {"command": GREP}, f"c{i}")]) for i in range(4)]
        replies.append(reply("Bulgular: Program.cs:1. Kalan iş: düzenleme."))
        result, execute, sent = self._run(replies)
        self.assertEqual(execute.call_count, 1)           # the 3 repeats never ran
        blocked = [m for m in self.session.messages if m.get("role") == "tool"
                   and m["content"].startswith("TEKRAR ENGELLENDİ")]
        self.assertEqual(len(blocked), 3)
        self.assertEqual(sent[-1], "none")                # forced answer turn
        self.assertIn("Kalan iş", result)

    def test_same_command_after_an_edit_is_allowed(self):
        replies = [
            reply(tools=[call("run_command", {"command": "python -m pytest -q"}, "a")]),
            reply(tools=[call("edit_file", {"path": "a.py", "target_snippet": "x", "replacement_snippet": "y"}, "b")]),
            reply(tools=[call("run_command", {"command": "python -m pytest -q"}, "c")]),
            reply("bitti"),
        ]
        outputs = iter(["Çıkış Kodu: 1\nfailed", "BAŞARILI: 'a.py' güncellendi.", "Çıkış Kodu: 0\npassed"])
        self.client.chat_stream_assembled.side_effect = lambda **k: replies.pop(0)
        with patch("src.agent.execute_tool_call", side_effect=lambda **k: next(outputs)) as execute:
            self.session.run_step("testi düzelt")
        self.assertEqual(execute.call_count, 3)

    def test_identical_output_from_different_args_is_flagged(self):
        replies = [
            reply(tools=[call("run_command", {"command": GREP + " Presentation"}, "a")]),
            reply(tools=[call("run_command", {"command": GREP + " Infrastructure"}, "b")]),
            reply("bitti"),
        ]
        _result, execute, _sent = self._run(replies)
        self.assertEqual(execute.call_count, 2)
        last_tool = [m for m in self.session.messages if m.get("role") == "tool"][-1]
        self.assertIn("aynısı bu görevde 2. kez", last_tool["content"])





if __name__ == "__main__":  # pragma: no cover
    unittest.main()


