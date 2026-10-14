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

    def test_budget_zero_disables_injection(self):
        ensure_project_docs(self.root)
        with patch.dict(os.environ, {"EVREN_DOCS_TOKENS": "0"}):
            session = self.make_session()
        self.assertNotIn("PROJE BAĞLAM BELGESİ", session.system_prompt)

    def test_refresh_context_picks_up_file_change(self):
        ensure_project_docs(self.root)
        session = self.make_session()
        path = self.root / "agent.md"
        path.write_text(
            path.read_text(encoding="utf-8") + "\n## Elle kural\nYorum satırı ekle.\n",
            encoding="utf-8",
            newline="",
        )
        session.refresh_context()
        self.assertIn("Yorum satırı ekle.", session.system_prompt)

    def test_changelog_reaches_system_prompt(self):
        ensure_project_docs(self.root)
        append_changelog_entry(self.root, {"goal": "şema migrasyonu", "files": ["src/x.py"]})
        session = self.make_session()
        self.assertIn("şema migrasyonu", session.system_prompt)

    def test_cli_lessons_reach_system_prompt(self):
        from src.cli_home import append_lesson

        append_lesson("cmd kabuğunda && kullanma", self.home)
        session = self.make_session()
        self.assertIn("cmd kabuğunda && kullanma", session.system_prompt)

    def test_docs_failure_does_not_break_prompt(self):
        session = self.make_session()
        with patch("src.project_docs.docs_context_block", side_effect=RuntimeError("bozuk")):
            rebuilt = session._compose_system("TEMEL")
        self.assertTrue(rebuilt.startswith("TEMEL"))


class PromptPreparationDigestTest(_WorkspaceTest):
    def test_docs_context_empty_without_docs(self):
        self.assertEqual(_docs_context(self.root), "")

    def test_docs_context_contains_project_brief(self):
        ensure_project_docs(self.root)
        context = _docs_context(self.root)
        self.assertIn("PROJE ÖZETİ", context)
        self.assertIn("Proje:", context)

    def test_docs_context_survives_failure(self):
        with patch("src.project_docs.docs_digest", side_effect=RuntimeError("bozuk")):
            self.assertEqual(_docs_context(self.root), "")

    def test_guidance_receives_digest(self):
        from src import repl

        ensure_project_docs(self.root)
        session = self.make_session()
        captured = {}

        def fake_collect(read_line, guidance, previous=None):
            guidance(SimpleNamespace(brief=""), True)
            return None

        with patch("src.repl.collect_guided_prompt_frame", fake_collect), \
                patch("src.repl.request_frame_guidance") as guidance_call:
            guidance_call.side_effect = lambda *a, **k: captured.update(
                context=a[4] if len(a) > 4 else k.get("execution_context", "")
            ) or SimpleNamespace(question="", suggestions={})
            repl._collect_frame(session, SimpleNamespace())

        self.assertIn("PROJE ÖZETİ", captured["context"])
        self.assertIn("Proje:", captured["context"])


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

    def test_run_step_updates_current_focus(self):
        session = self.make_session()
        session.run_step("belgeyi güncelle")
        from src.docs_merge import extract_sections

        text = (self.root / "agent.md").read_text(encoding="utf-8")
        self.assertIn("belgeyi güncelle", extract_sections(text)["current_focus"])

    def test_plan_mode_writes_nothing(self):
        session = self.make_session()
        session.run_step("plan: salt okuma yap")
        self.assertFalse((self.root / "agent.md").exists())
        self.assertFalse((self.root / "changelog.md").exists())

    def test_file_edits_are_listed(self):
        session = self.make_session()
        session.client.chat_stream_assembled.side_effect = [
            response("yazıyorum", tools=[
                tool_call('{"path": "src/a.py", "content": "x"}', name="write_file"),
            ]),
            response("bitti"),
        ]
        with patch("src.agent.execute_tool_call", return_value="BAŞARILI: src/a.py"):
            session.run_step("dosya oluştur")
        text = (self.root / "changelog.md").read_text(encoding="utf-8")
        self.assertIn("`src/a.py`", text)

    def test_trace_is_reset_between_tasks(self):
        session = self.make_session()
        session.client.chat_stream_assembled.side_effect = [
            response("yazıyorum", tools=[
                tool_call('{"path": "src/a.py", "content": "x"}', name="write_file"),
            ]),
            response("bitti"),
            response("ikinci görev bitti"),
        ]
        with patch("src.agent.execute_tool_call", return_value="BAŞARILI: src/a.py"):
            session.run_step("ilk görev")
            session.run_step("ikinci görev")
        self.assertEqual(session.tool_trace, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
