"""Tests for the project-level smart context docs (src/project_docs.py)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import project_docs
from src.docs_merge import extract_sections, has_managed_sections


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
        self.assertTrue((self.root / "agent.md").exists())
        self.assertTrue((self.root / "changelog.md").exists())
        self.assertIn("agent.md", result.created)
        self.assertIn("changelog.md", result.created)

    def test_created_document_has_all_managed_sections(self):
        project_docs.ensure_project_docs(self.root)
        sections = extract_sections(self.read("agent.md"))
        for name in project_docs.AGENT_SECTIONS:
            self.assertIn(name, sections)
        self.assertIn(str(self.root), sections["runtime_env"])

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

    def test_goal_change_updates_only_current_focus(self):
        project_docs.ensure_project_docs(self.root, goal="ilk görev")
        result = project_docs.ensure_project_docs(self.root, goal="yeni görev")
        self.assertEqual(result.updated, ["agent.md#current_focus"])
        self.assertIn("yeni görev", extract_sections(self.read("agent.md"))["current_focus"])

    def test_unmanaged_document_is_left_alone(self):
        self.write("agent.md", "# Bizim agent.md\n\nElle yazılmış kurallar.\n")
        result = project_docs.ensure_project_docs(self.root)
        self.assertEqual(
            self.read("agent.md"), "# Bizim agent.md\n\nElle yazılmış kurallar.\n"
        )
        self.assertIn("agent.md", result.skipped)
        self.assertTrue(any("--adopt" in note for note in result.notes))

    def test_adopt_adds_markers_without_losing_text(self):
        self.write("agent.md", "# Bizim agent.md\n\nElle yazılmış kurallar.\n")
        project_docs.ensure_project_docs(self.root, adopt=True)
        text = self.read("agent.md")
        self.assertIn("Elle yazılmış kurallar.", text)
        self.assertTrue(has_managed_sections(text))

    def test_agents_md_coexists_and_is_never_written(self):
        self.write("AGENTS.md", "# AGENTS.md\n\nProje kuralları.\n")
        result = project_docs.ensure_project_docs(self.root)
        self.assertEqual(self.read("AGENTS.md"), "# AGENTS.md\n\nProje kuralları.\n")
        self.assertTrue((self.root / "agent.md").exists())
        self.assertEqual(result.conflict, "")
        # Both documents reach the model: AGENTS.md read-only, agent.md managed.
        block = project_docs.docs_context_block(self.root)
        self.assertIn("Proje kuralları.", block)
        self.assertIn("[runtime_env]", block)

    def test_case_clash_forces_alternate_name(self):
        # NTFS: 'Agent.md' and 'agent.md' are the same file -> never overwrite it.
        self.write("Agent.md", "# Elle\n\nKorunacak metin.\n")
        result = project_docs.ensure_project_docs(self.root)
        self.assertEqual(self.read("Agent.md"), "# Elle\n\nKorunacak metin.\n")
        self.assertTrue((self.root / "evren-agent.md").exists())
        self.assertIn("NTFS", result.conflict)

    def test_secrets_are_masked(self):
        project_docs.ensure_project_docs(self.root, goal="evren_llm_GIZLI123 ile giriş")
        text = self.read("agent.md")
        self.assertNotIn("evren_llm_GIZLI123", text)
        self.assertIn("[REDACTED]", text)

    def test_kill_switch_disables_everything(self):
        with patch.dict(os.environ, {"EVREN_AUTO_DOCS": "false"}):
            result = project_docs.ensure_project_docs(self.root)
        self.assertTrue(result.disabled)
        self.assertFalse((self.root / "agent.md").exists())

    def test_read_only_mode_writes_nothing(self):
        result = project_docs.ensure_project_docs(self.root, read_only=True)
        self.assertFalse((self.root / "agent.md").exists())
        self.assertTrue(any("salt-okuma" in note for note in result.notes))

    def test_state_file_records_hashes(self):
        project_docs.ensure_project_docs(self.root)
        state = project_docs.load_state(self.root)
        self.assertIn("agent.md.runtime_env", state["sections"])
        self.assertTrue(state["sections"]["agent.md.runtime_env"].startswith("sha256:"))


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

    def test_identical_task_is_not_logged_twice(self):
        project_docs.ensure_project_docs(self.root)
        entry = {"goal": "aynı iş", "files": ["src/a.py"], "exit_codes": [0]}
        self.assertTrue(project_docs.append_changelog_entry(self.root, entry))
        before = self.read("changelog.md")
        self.assertFalse(project_docs.append_changelog_entry(self.root, entry))
        self.assertEqual(self.read("changelog.md"), before)

    def test_old_entries_are_never_removed(self):
        project_docs.ensure_project_docs(self.root)
        project_docs.append_changelog_entry(self.root, {"goal": "birinci", "files": []})
        project_docs.append_changelog_entry(self.root, {"goal": "ikinci", "files": []})
        text = self.read("changelog.md")
        self.assertIn("birinci", text)
        self.assertIn("ikinci", text)
        self.assertEqual(text.count("\n## 20"), 2)

    def test_entry_secret_masked_and_length_bounded(self):
        project_docs.ensure_project_docs(self.root)
        project_docs.append_changelog_entry(
            self.root,
            {"goal": "x" * 3000, "files": ["src/a.py"], "note": "token=super-sir"},
        )
        text = self.read("changelog.md")
        self.assertNotIn("super-sir", text)
        self.assertLessEqual(max(len(part) for part in text.split("\n## ")), 1400)

    def test_changelog_created_even_without_entries(self):
        project_docs.ensure_project_docs(self.root)
        self.assertIn("Changelog", self.read("changelog.md"))

    def test_repo_changelog_md_is_never_written(self):
        # NTFS: 'CHANGELOG.md' == 'changelog.md'. The repo's own changelog must
        # survive untouched; the CLI keeps its session log in evren-changelog.md.
        original = "# Changelog\n\n## 2.0.0\n- insan yazimi gir\n"
        self.write("CHANGELOG.md", original)
        result = project_docs.ensure_project_docs(self.root)
        self.assertTrue(
            project_docs.append_changelog_entry(
                self.root, {"goal": "oturum isi", "files": ["src/a.py"]}
            )
        )
        self.assertEqual(self.read("CHANGELOG.md"), original)
        session_log = self.read("evren-changelog.md")
        self.assertIn("oturum isi", session_log)
        self.assertTrue(any("CHANGELOG" in note or "evren-changelog" in note for note in result.notes))

    def test_plain_changelog_md_is_used_when_no_clash(self):
        project_docs.ensure_project_docs(self.root)
        self.assertTrue((self.root / "changelog.md").exists())
        self.assertFalse((self.root / "evren-changelog.md").exists())


class ContextBlockTest(_TempProjectTest):
    def test_agent_block_contains_sections(self):
        project_docs.ensure_project_docs(self.root)
        block = project_docs.agent_md_context_block(self.root)
        self.assertIn("PROJE BAĞLAM BELGESİ", block)
        self.assertIn("[runtime_env]", block)
        self.assertIn("[conventions]", block)

    def test_block_empty_without_document(self):
        self.assertEqual(project_docs.agent_md_context_block(self.root), "")

    def test_unmanaged_document_still_included(self):
        self.write("agent.md", "# Elle\n\nKural: test koş.")
        block = project_docs.agent_md_context_block(self.root)
        self.assertIn("Kural: test koş.", block)
        self.assertIn("elle yönetiliyor", block)

    def test_block_is_truncated_with_notice(self):
        self.write("agent.md", "# Elle\n\n" + "x" * 5000)
        block = project_docs.agent_md_context_block(self.root, max_chars=400)
        self.assertIn("kırpıldı", block)
        self.assertLess(len(block), 700)

    def test_changelog_block_appears_after_entry(self):
        project_docs.ensure_project_docs(self.root)
        self.assertEqual(project_docs.changelog_context_block(self.root), "")
        project_docs.append_changelog_entry(self.root, {"goal": "izlenen iş", "files": []})
        self.assertIn("izlenen iş", project_docs.changelog_context_block(self.root))

    def test_agents_md_included_read_only(self):
        self.write("AGENTS.md", "# AGENTS.md\n\nHer promptta netleştir.")
        project_docs.ensure_project_docs(self.root)
        block = project_docs.docs_context_block(self.root)
        self.assertIn("Her promptta netleştir.", block)
        self.assertIn("salt-okunur", block)

    def test_token_budget_zero_disables_injection(self):
        project_docs.ensure_project_docs(self.root)
        self.assertEqual(project_docs.docs_context_block(self.root, budget_tokens=0), "")
        with patch.dict(os.environ, {"EVREN_DOCS_TOKENS": "0"}):
            self.assertEqual(project_docs.docs_context_block(self.root), "")

    def test_digest_is_short_and_includes_map(self):
        project_docs.ensure_project_docs(self.root)
        digest = project_docs.docs_digest(self.root, max_chars=600)
        self.assertIn("Proje:", digest)
        self.assertLessEqual(len(digest), 601)


class MaintenanceTest(_TempProjectTest):
    def test_status_reports_paths_and_sections(self):
        project_docs.ensure_project_docs(self.root)
        status = project_docs.docs_status(self.root)
        self.assertTrue(status["enabled"])
        self.assertTrue(status["agent_doc"].endswith("agent.md"))
        self.assertIn("runtime_env", status["agent_sections"])
        self.assertTrue(status["state"])

    def test_unmark_keeps_human_text(self):
        project_docs.ensure_project_docs(self.root)
        text = self.read("agent.md") + "\n## Kalıcı not\nBu kalmalı.\n"
        self.write("agent.md", text)
        result = project_docs.unmark_docs(self.root)
        self.assertTrue(result.updated)
        final = self.read("agent.md")
        self.assertIn("Bu kalmalı.", final)
        self.assertNotIn("evren:managed", final)

    def test_unmark_is_noop_without_documents(self):
        result = project_docs.unmark_docs(self.root)
        self.assertEqual(result.updated, [])

    def test_docs_json_disable_flag(self):
        (self.root / ".evren").mkdir(parents=True, exist_ok=True)
        (self.root / ".evren" / "docs.json").write_text('{"disabled": true}', encoding="utf-8")
        result = project_docs.ensure_project_docs(self.root)
        self.assertTrue(result.disabled)
        self.assertIn("docs.json", result.reason)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
