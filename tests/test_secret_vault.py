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

    def test_same_value_gets_one_placeholder(self):
        vault = SecretVault()
        text = vault.capture(f"Bearer {TOKEN} ve tekrar token={TOKEN}")
        self.assertEqual(text.count("{{EVREN_SECRET_1}}"), 2)
        self.assertNotIn("EVREN_SECRET_2", text)

    def test_expand_and_mask_round_trip(self):
        vault = SecretVault()
        masked = vault.capture(f"Bearer {TOKEN}")
        self.assertEqual(vault.expand(masked), f"Bearer {TOKEN}")
        self.assertEqual(vault.mask(f"çıktı: {TOKEN}"), "çıktı: {{EVREN_SECRET_1}}")

    def test_plain_text_is_untouched(self):
        vault = SecretVault()
        self.assertEqual(vault.capture("son buildleri kontrol et"), "son buildleri kontrol et")
        self.assertFalse(vault)
        self.assertEqual(vault.usage_note(), "")


class RedactAuthorizationTest(unittest.TestCase):
    def test_header_token_is_masked_not_the_scheme(self):
        out = redact_text(f'-H "Authorization: Bearer {TOKEN}"')
        self.assertNotIn(TOKEN, out)
        self.assertIn("Bearer [REDACTED]", out)
        self.assertNotIn("dXNlcjpwYXNz", redact_text("Authorization=Basic dXNlcjpwYXNz"))


class RunCommandSecretTest(unittest.TestCase):
    def test_command_gets_real_value_and_env_but_prints_placeholder(self):
        from src import tools

        vault = SecretVault()
        command = vault.capture(f'curl -H "Authorization: Bearer {TOKEN}" https://x')
        set_active_vault(vault)
        self.addCleanup(set_active_vault, SecretVault())
        seen = {}

        def fake_stream(argv, **kwargs):
            seen["argv"], seen["env"] = argv, kwargs.get("env")
            yield ("stdout", f"echo {TOKEN}\n")
            return 0, f"yanıt {TOKEN}", "", False

        with patch.object(tools, "stream_with_tree_kill", fake_stream), \
                patch.object(tools, "print_warning") as warn, \
                patch.object(tools, "print_command_output") as out:
            result = tools.tool_run_command(command, workspace_root=Path("."), auto_approve=True)
        self.assertIn(TOKEN, seen["argv"])
        self.assertEqual(seen["env"]["EVREN_SECRET_1"], TOKEN)
        self.assertNotIn(TOKEN, str(warn.call_args_list))
        self.assertNotIn(TOKEN, str(out.call_args_list))
        self.assertNotIn(TOKEN, result)
        self.assertIn("{{EVREN_SECRET_1}}", result)


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
