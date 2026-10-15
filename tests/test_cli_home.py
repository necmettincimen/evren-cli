"""Tests for the CLI-owned support documents (src/cli_home.py)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import cli_home
from src.docs_merge import extract_sections


class _TempHomeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name) / "cli_home"
        self.home.mkdir(parents=True, exist_ok=True)
        patcher = patch.dict(os.environ, {"EVREN_CLI_HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def read(self, name: str) -> str:
        return (self.home / name).read_text(encoding="utf-8-sig")


class CliDocsDirTest(unittest.TestCase):
    def test_env_override_is_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "home"
            import os
            from unittest.mock import patch

            with patch.dict(os.environ, {"EVREN_CLI_HOME": str(target)}):
                self.assertEqual(cli_home.cli_docs_dir(), target.resolve())

    def test_falls_back_when_override_not_writable(self):
        import os
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "blocker"
            blocker.write_text("ben dizin değilim", encoding="utf-8")
            with patch.dict(os.environ, {"EVREN_CLI_HOME": str(blocker)}):
                resolved = cli_home.cli_docs_dir()
            # Must land on a writable fallback, never on the non-directory path.
            self.assertNotEqual(resolved, blocker.resolve())
            self.assertTrue(resolved.is_dir())


class EnsureCliDocsTest(_TempHomeTest):
    def test_creates_expected_documents(self):
        result = cli_home.ensure_cli_docs(self.home)
        self.assertFalse(result["disabled"])
        self.assertEqual(result["files"]["skills.md"], "created")
        self.assertEqual(result["files"]["commands.md"], "created")
        self.assertTrue((self.home / "skills.md").exists())
        self.assertTrue((self.home / "commands.md").exists())
        self.assertTrue((self.home / "context" / "README.md").exists())

    def test_skills_catalog_lists_real_skills(self):
        cli_home.ensure_cli_docs(self.home)
        catalog = extract_sections(self.read("skills.md")).get("catalog", "")
        self.assertIn("root-cause-debug", catalog)
        self.assertIn("| Beceri | Kategori | Kaynak | Ne zaman |", catalog)

    def test_second_run_is_idempotent(self):
        cli_home.ensure_cli_docs(self.home)
        first = self.read("skills.md")
        result = cli_home.ensure_cli_docs(self.home)
        self.assertEqual(result["files"]["skills.md"], "skipped")
        self.assertEqual(self.read("skills.md"), first)

    def test_custom_skills_section_survives_refresh(self):
        cli_home.ensure_cli_docs(self.home)
        text = self.read("skills.md") + "\n### benim-kontrolum\nAmaç: dağıtım öncesi kontrol.\n"
        (self.home / "skills.md").write_text(text, encoding="utf-8", newline="")
        cli_home.ensure_cli_docs(self.home)
        self.assertIn("### benim-kontrolum", self.read("skills.md"))
        self.assertIn("Amaç: dağıtım öncesi kontrol.", self.read("skills.md"))

    def test_custom_skills_context_block(self):
        cli_home.ensure_cli_docs(self.home)
        # Skeleton note alone must not leak into the prompt block.
        self.assertEqual(cli_home.custom_skills_text(self.home), "")
        text = self.read("skills.md") + "\n### benim-kontrolum\nAmaç: X.\n"
        (self.home / "skills.md").write_text(text, encoding="utf-8", newline="")
        block = cli_home.cli_docs_context_block(self.home)
        self.assertIn("benim-kontrolum", block)
        self.assertNotIn("Bu bölümü siz yazarsınız", block)

    def test_unwritable_home_degrades_without_raising(self):
        blocker = self.home / "not-a-dir"
        blocker.write_text("dosya", encoding="utf-8")
        result = cli_home.ensure_cli_docs(blocker)
        self.assertTrue(result["disabled"])
        self.assertIn("reason", result)


class CommandsReferenceTest(_TempHomeTest):
    def test_slash_and_subcommands_parsed_from_source(self):
        slash = dict((cmd.split(maxsplit=1)[0], desc) for cmd, desc in cli_home.slash_commands())
        self.assertIn("/skills", slash)
        self.assertIn("/memory", slash)
        subs = dict(cli_home.subcommands())
        for name in ("chat", "agent", "scan", "quota", "ssh"):
            self.assertIn(name, subs)

    def test_commands_document_contains_tables(self):
        cli_home.ensure_cli_docs(self.home)
        body = extract_sections(self.read("commands.md")).get("commands", "")
        self.assertIn("### Slash komutları (REPL)", body)
        self.assertIn("`/skills", body)
        self.assertIn("`/memory`", body)
        self.assertIn("`evren agent`", body)


class LessonsTest(_TempHomeTest):
    def test_append_creates_file_and_context_block(self):
        self.assertFalse((self.home / "lessons.md").exists())
        self.assertTrue(cli_home.append_lesson("cmd kabuğunda && kullanma", self.home))
        self.assertTrue((self.home / "lessons.md").exists())
        self.assertIn("cmd kabuğunda", cli_home.lessons_text(self.home))
        self.assertIn("Önceki oturum dersleri", cli_home.cli_docs_context_block(self.home))

    def test_duplicate_lesson_is_not_appended_twice(self):
        cli_home.append_lesson("tek ders", self.home)
        before = self.read("lessons.md")
        self.assertFalse(cli_home.append_lesson("tek ders", self.home))
        self.assertEqual(self.read("lessons.md"), before)

    def test_overflow_is_archived_not_deleted(self):
        original = cli_home.MAX_LESSONS
        cli_home.MAX_LESSONS = 3
        self.addCleanup(setattr, cli_home, "MAX_LESSONS", original)
        for index in range(6):
            cli_home.append_lesson(f"ders {index}", self.home)
        kept = cli_home.lessons_text(self.home, max_lines=50)
        self.assertNotIn("ders 0", kept)
        self.assertIn("ders 5", kept)
        archive = self.home / "logs" / "lessons_archive.md"
        self.assertTrue(archive.exists())
        self.assertIn("ders 0", archive.read_text(encoding="utf-8"))

    def test_secrets_are_masked(self):
        cli_home.append_lesson("anahtar evren_llm_ABCDEF123 ile çalıştı", self.home)
        text = cli_home.lessons_text(self.home)
        self.assertNotIn("evren_llm_ABCDEF123", text)
        self.assertIn("[REDACTED]", text)

    def test_blank_lesson_rejected(self):
        self.assertFalse(cli_home.append_lesson("   ", self.home))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
