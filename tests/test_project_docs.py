"""Tests for the project-level smart context docs (src/project_docs.py)."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import project_docs
from src.docs_merge import extract_sections


class _TempProjectTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        patcher = patch.dict(os.environ, {"EVREN_AUTO_DOCS": "true"}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def read(self, name: str) -> str:
        return (self.root / name).read_text(encoding="utf-8-sig")

    def write(self, name: str, text: str) -> None:
        (self.root / name).write_text(text, encoding="utf-8", newline="")


class EnsureAgentDocTest(_TempProjectTest):
    def test_creates_agent_and_changelog(self):
        result = project_docs.ensure_project_docs(self.root)
        self.assertFalse(result.disabled)
        self.assertIn("agent.md", result.created)
        self.assertIn("changelog.md", result.created)
        self.assertTrue((self.root / "agent.md").exists())
        self.assertTrue((self.root / "changelog.md").exists())

    def test_second_run_writes_nothing(self):
        project_docs.ensure_project_docs(self.root)
        before = (self.root / "agent.md").read_bytes()
        result = project_docs.ensure_project_docs(self.root)
        self.assertEqual((self.root / "agent.md").read_bytes(), before)
        self.assertEqual(result.updated, [])
        self.assertIn("agent.md", result.skipped)

    def test_human_sections_survive_refresh(self):
        project_docs.ensure_project_docs(self.root)
        text = self.read("agent.md") + "\n## Benim kurallarım\nAsla force-push yapma.\n"
        self.write("agent.md", text)
        project_docs.ensure_project_docs(self.root, goal="ikinci görev")
        self.assertIn("Asla force-push yapma.", self.read("agent.md"))

    def test_unmanaged_or_foreign_document_is_left_alone(self):
        with self.subTest("managed işaretli değil: --adopt önerisiyle atlanır"):
            self.write("agent.md", "# Bizim agent.md\n\nElle yazılmış kurallar.\n")
            result = project_docs.ensure_project_docs(self.root)
            self.assertEqual(
                self.read("agent.md"), "# Bizim agent.md\n\nElle yazılmış kurallar.\n"
            )
            self.assertIn("agent.md", result.skipped)
            self.assertTrue(any("--adopt" in note for note in result.notes))

        with self.subTest("AGENTS.md dokunulmaz, iki belge de bağlama girer"):
            root = Path(tempfile.mkdtemp())
            self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
            (root / "AGENTS.md").write_text(
                "# AGENTS.md\n\nProje kuralları.\n", encoding="utf-8", newline=""
            )
            result = project_docs.ensure_project_docs(root)
            self.assertEqual(
                (root / "AGENTS.md").read_text(encoding="utf-8-sig"),
                "# AGENTS.md\n\nProje kuralları.\n",
            )
            self.assertTrue((root / "agent.md").exists())
            self.assertEqual(result.conflict, "")
            block = project_docs.docs_context_block(root)
            self.assertIn("Proje kuralları.", block)
            self.assertIn("[runtime_env]", block)

    def test_output_safety(self):
        with self.subTest("sırlar maskelenir"):
            project_docs.ensure_project_docs(self.root, goal="evren_llm_GIZLI123 ile giriş")
            text = self.read("agent.md")
            self.assertNotIn("evren_llm_GIZLI123", text)
            self.assertIn("[REDACTED]", text)

        with self.subTest("kill switch her şeyi kapatır"):
            root = Path(tempfile.mkdtemp())
            self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
            with patch.dict(os.environ, {"EVREN_AUTO_DOCS": "false"}):
                result = project_docs.ensure_project_docs(root)
            self.assertTrue(result.disabled)
            self.assertFalse((root / "agent.md").exists())

        with self.subTest("salt-okuma kipi hiçbir şey yazmaz"):
            root = Path(tempfile.mkdtemp())
            self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
            result = project_docs.ensure_project_docs(root, read_only=True)
            self.assertFalse((root / "agent.md").exists())
            self.assertTrue(any("salt-okuma" in note for note in result.notes))


class ChangelogTest(_TempProjectTest):
    def test_entry_is_appended_and_recent_block_updated(self):
        project_docs.ensure_project_docs(self.root)
        written = project_docs.append_changelog_entry(
            self.root,
            {"goal": "docs özelliği", "files": ["src/a.py"], "exit_codes": [0],
             "verification": "pytest 3 passed"},
        )
        self.assertTrue(written)
        text = self.read("changelog.md")
        self.assertIn("docs özelliği", text)
        self.assertIn("`src/a.py`", text)
        self.assertIn("pytest 3 passed", text)
        self.assertIn("docs özelliği", extract_sections(text)["recent"])


class ContextBlockTest(_TempProjectTest):
    def test_token_budget_zero_disables_injection(self):
        project_docs.ensure_project_docs(self.root)
        self.assertEqual(project_docs.docs_context_block(self.root, budget_tokens=0), "")
        with patch.dict(os.environ, {"EVREN_DOCS_TOKENS": "0"}):
            self.assertEqual(project_docs.docs_context_block(self.root), "")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
