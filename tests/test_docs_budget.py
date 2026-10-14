"""Tests for the docs context budget (plan token-consumption-fixes, Faz 3 / F3)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import project_docs
from src.token_manager import TokenManager


class DocsTokenBudgetTest(unittest.TestCase):
    def test_default_is_1200(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EVREN_DOCS_TOKENS", None)
            self.assertEqual(project_docs.docs_token_budget(), 1200)

    def test_env_override_raises_it_back(self):
        with patch.dict(os.environ, {"EVREN_DOCS_TOKENS": "3500"}):
            self.assertEqual(project_docs.docs_token_budget(), 3500)

    def test_zero_disables_injection(self):
        with patch.dict(os.environ, {"EVREN_DOCS_TOKENS": "0"}):
            self.assertEqual(project_docs.docs_token_budget(), 0)

    def test_invalid_value_falls_back_to_default(self):
        with patch.dict(os.environ, {"EVREN_DOCS_TOKENS": "abc"}):
            self.assertEqual(project_docs.docs_token_budget(), 1200)

    def test_negative_is_clamped(self):
        with patch.dict(os.environ, {"EVREN_DOCS_TOKENS": "-5"}):
            self.assertEqual(project_docs.docs_token_budget(), 0)


class SectionPriorityTest(unittest.TestCase):
    def test_map_is_last_and_conventions_first(self):
        order = project_docs.CONTEXT_SECTION_PRIORITY
        self.assertEqual(order[0], "conventions")
        self.assertEqual(order[1], "current_focus")
        self.assertEqual(order[-1], "project_map")

    def test_covers_every_agent_section(self):
        self.assertEqual(
            set(project_docs.CONTEXT_SECTION_PRIORITY), set(project_docs.AGENT_SECTIONS)
        )

    def test_file_template_order_is_untouched(self):
        # The on-disk document keeps its original layout; only injection reorders.
        self.assertEqual(project_docs.AGENT_SECTIONS[0], "identity")


class InjectionSizeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        with patch.dict(os.environ, {"EVREN_AUTO_DOCS": "true"}, clear=False):
            project_docs.ensure_project_docs(self.root, goal="bütçe testi")
        self.assertTrue((self.root / "agent.md").exists())

    def test_injected_block_respects_the_default_budget(self):
        block = project_docs.docs_context_block(self.root)
        self.assertLessEqual(TokenManager().estimate_text(block), 1200)

    def test_map_section_is_dropped_before_conventions(self):
        # A tight budget must keep the high-signal sections and lose the map first.
        block = project_docs.agent_md_context_block(self.root, max_chars=900)
        self.assertIn("[conventions]", block)
        self.assertIn("[current_focus]", block)
        self.assertNotIn("[project_map]", block)
        self.assertIn("kırpıldı", block)

    def test_generous_budget_keeps_the_map(self):
        block = project_docs.agent_md_context_block(self.root, max_chars=8000)
        self.assertIn("[project_map]", block)

    def test_truncation_is_announced(self):
        block = project_docs.agent_md_context_block(self.root, max_chars=200)
        self.assertIn("kırpıldı", block)


class MapLineCapTest(unittest.TestCase):
    def test_cap_is_reduced(self):
        self.assertEqual(project_docs.MAX_MAP_LINES, 25)

    def test_long_tree_is_capped(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for i in range(80):
                (root / f"modul_{i:03d}.py").write_text("x = 1\n", encoding="utf-8")
            with patch.dict(os.environ, {"EVREN_AUTO_DOCS": "true"}, clear=False):
                project_docs.ensure_project_docs(root, goal="harita tavanı")
            text = project_docs._read_text(project_docs.agent_doc_path(root))
        body = project_docs.extract_sections(text).get("project_map", "")
        # Guard against a vacuous pass: the map must actually have content.
        self.assertTrue(body.strip())
        tree_lines = [l for l in body.splitlines() if "\u251c\u2500\u2500" in l or "\u2514\u2500\u2500" in l]
        self.assertTrue(tree_lines)
        self.assertLessEqual(len(tree_lines), project_docs.MAX_MAP_LINES)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
