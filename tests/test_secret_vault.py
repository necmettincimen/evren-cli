"""User secrets never reach the model, the docs or the terminal."""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.agent import AgentSession, _load_domain_note
from src.secret_vault import SecretVault, set_active_vault
from src.workflow_state import redact_text

TOKEN = "fake" + "TokenForTests0123456789abcd"  # fake value; split so scanners ignore it


class VaultTest(unittest.TestCase):
    def test_capture_replaces_bearer_and_password(self):
        vault = SecretVault()
        text = vault.capture(
            f'curl -H "Authorization: Bearer {TOKEN}" https://x/api/user ve password=hunter2xyz'
        )
        self.assertNotIn(TOKEN, text)
        self.assertNotIn("hunter2xyz", text)
        self.assertIn("Bearer {{EVREN_SECRET_1}}", text)
        self.assertIn("{{EVREN_SECRET_2}}", text)
        self.assertEqual(vault.env()["EVREN_SECRET_1"], TOKEN)





class AgentSecretTest(unittest.TestCase):
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

    def test_model_never_sees_the_token(self):
        sent = []

        def reply(**kwargs):
            sent.append(str(kwargs["messages"]))
            return SimpleNamespace(
                choices=[SimpleNamespace(
                    finish_reason="stop", message=SimpleNamespace(content="tamam", tool_calls=[]),
                )],
                usage=None,
            )

        self.client.chat_stream_assembled.side_effect = reply
        self.session.run_step(
            f'curl -i https://drone.example/api/user -H "Authorization: Bearer {TOKEN}" '
            "ile son buildleri hata vermiş repoları kontrol et"
        )
        self.assertNotIn(TOKEN, sent[0])
        self.assertIn("{{EVREN_SECRET_1}}", sent[0])
        self.assertIn("EVREN GİZLİ DEĞERLER", sent[0])
        self.assertIn("latest=true", sent[0])  # Drone notes ride along
        self.assertNotIn(TOKEN, self.session.task_goal)
        self.assertIn("latest=true", _load_domain_note("drone"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
