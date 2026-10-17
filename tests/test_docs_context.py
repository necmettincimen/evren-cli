"""Tests for prompt-time context injection of the smart docs (plan Faz 5)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.agent import AgentSession
from src.project_docs import (
    append_changelog_entry,
    ensure_project_docs,
)
from src.repl import _docs_context


def response(content="", finish_reason="stop", tools=()):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            finish_reason=finish_reason,
            message=SimpleNamespace(content=content, tool_calls=list(tools)),
        )],
        usage=None,
    )


def tool_call(arguments, name="run_command"):
    return SimpleNamespace(id="call_1", function=SimpleNamespace(name=name, arguments=arguments))


class _WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        patcher = patch.dict(os.environ, {"EVREN_AUTO_DOCS": "true"}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        # Keep the CLI home out of the repo during tests.
        home = self.root / "_cli_home"
        home.mkdir(parents=True, exist_ok=True)
        patcher2 = patch.dict(os.environ, {"EVREN_CLI_HOME": str(home)}, clear=False)
        patcher2.start()
        self.addCleanup(patcher2.stop)
        self.home = home

    def make_session(self) -> AgentSession:
        client = MagicMock(default_model="test-model")
        client.chat_stream_assembled.return_value = response("tamam")
        session = AgentSession(client, workspace_root=self.root)
        for name in ("print_info", "print_warning", "print_error", "print_markdown",
                     "print_tool_call", "print_tool_result"):
            patcher = patch(f"src.agent.{name}")
            patcher.start()
            self.addCleanup(patcher.stop)
        return session


class SystemPromptInjectionTest(_WorkspaceTest):
    def test_agent_doc_reaches_system_prompt(self):
        ensure_project_docs(self.root)
        session = self.make_session()
        prompt = session.system_prompt
        self.assertIn("PROJE BAĞLAM BELGESİ", prompt)
        self.assertIn("[runtime_env]", prompt)
        self.assertIn("[conventions]", prompt)

    def test_no_docs_means_no_block(self):
        session = self.make_session()
        self.assertNotIn("PROJE BAĞLAM BELGESİ", session.system_prompt)





    def test_docs_failure_does_not_break_prompt(self):
        session = self.make_session()
        with patch("src.project_docs.docs_context_block", side_effect=RuntimeError("bozuk")):
            rebuilt = session._compose_system("TEMEL")
        self.assertTrue(rebuilt.startswith("TEMEL"))




class ChangelogFromTaskTest(_WorkspaceTest):
    def test_run_step_writes_changelog_entry(self):
        session = self.make_session()
        session.client.chat_stream_assembled.side_effect = [
            response("çalıştırıyorum", tools=[tool_call('{"command": "python -m pytest -q"}')]),
            response("testler geçti"),
        ]
        session.run_step("testleri koş")
        text = (self.root / "changelog.md").read_text(encoding="utf-8")
        self.assertIn("testleri koş", text)
        self.assertIn("python -m pytest -q", text)


    def test_plan_mode_writes_nothing(self):
        session = self.make_session()
        session.run_step("plan: salt okuma yap")
        self.assertFalse((self.root / "agent.md").exists())
        self.assertFalse((self.root / "changelog.md").exists())




if __name__ == "__main__":  # pragma: no cover
    unittest.main()
