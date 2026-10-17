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


    def test_zero_disables_injection(self):
        with patch.dict(os.environ, {"EVREN_DOCS_TOKENS": "0"}):
            self.assertEqual(project_docs.docs_token_budget(), 0)

    def test_invalid_value_falls_back_to_default(self):
        with patch.dict(os.environ, {"EVREN_DOCS_TOKENS": "abc"}):
            self.assertEqual(project_docs.docs_token_budget(), 1200)



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



    def test_truncation_is_announced(self):
        block = project_docs.agent_md_context_block(self.root, max_chars=200)
        self.assertIn("kırpıldı", block)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
