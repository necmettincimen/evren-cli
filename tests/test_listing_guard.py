"""Remote target hints and the "list X" fast path."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.agent import AgentSession, is_listing_task, ssh_target_note


def response(content="", tools=()):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            finish_reason="stop",
            message=SimpleNamespace(content=content, tool_calls=list(tools)),
        )],
        usage=None,
    )


class ListingTaskTest(unittest.TestCase):
    def test_plain_listing_requests(self):
        self.assertTrue(is_listing_task("koyu3-k8s üzerindeki podların resource ve limit değerlerini listele"))
        self.assertTrue(is_listing_task("namespace'leri göster"))



class ListingFastPathTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        env = patch.dict(os.environ, {"EVREN_AUTO_DOCS": "false"}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        self.client = MagicMock(default_model="m")
        self.session = AgentSession(self.client, workspace_root=Path(self._tmp.name))
        for name in ("print_info", "print_warning", "print_error", "print_markdown",
                     "print_tool_call", "print_tool_result", "print_reasoning"):
            patcher = patch(f"src.agent.{name}")
            patcher.start()
            self.addCleanup(patcher.stop)

    def _run(self, task, tool_output):
        call = SimpleNamespace(
            id="c", function=SimpleNamespace(name="run_command", arguments='{"command":"kubectl get pods -A"}'),
        )
        replies = [response("", tools=[call]), response("özet")]
        seen = []

        def reply(**kwargs):
            seen.append(kwargs.get("tool_choice"))
            return replies.pop(0)

        self.client.chat_stream_assembled.side_effect = reply
        with patch("src.agent.execute_tool_call", return_value=tool_output):
            self.session.run_step(task)
        return seen

    def test_long_listing_forces_a_summary_turn(self):
        seen = self._run("podları listele", "Çıkış Kodu: 0\n...\n[EVREN] Tam çıktı (175 satır) kullanıcının terminalinde gösterildi.")
        self.assertEqual(seen, [None, "none"])




if __name__ == "__main__":  # pragma: no cover
    unittest.main()
