"""Tests for the prompt-preparation context size (plan token-consumption-fixes, Faz 6 / F4)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.config import execution_context_block
from src.repl import _guidance_context
from src.token_manager import TokenManager


class GuidanceContextTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        with patch.dict(os.environ, {"EVREN_AUTO_DOCS": "true"}, clear=False):
            from src.project_docs import ensure_project_docs

            ensure_project_docs(self.root, goal="guidance baglami")

    def test_context_names_the_workspace(self):
        self.assertIn(str(self.root), _guidance_context(self.root))

    def test_context_is_smaller_than_the_previous_payload(self):
        from src.repl import _docs_context

        old = execution_context_block(self.root) + _docs_context(self.root)
        new = _guidance_context(self.root)
        self.assertLess(len(new), len(old))

    def test_context_is_bounded_by_the_digest(self):
        # The digest itself is capped at 1200 chars; the guidance payload must not
        # add anything comparable to the old execution-context block.
        self.assertLess(len(_guidance_context(self.root)), 1400)

    def test_token_estimate_is_reported(self):
        tm = TokenManager()
        self.assertLess(tm.estimate_text(_guidance_context(self.root)), 400)

    def test_missing_docs_still_yield_the_workspace(self):
        with tempfile.TemporaryDirectory() as td:
            text = _guidance_context(Path(td).resolve())
        self.assertIn(str(Path(td).resolve()), text)

    def test_digest_failure_is_survived(self):
        with patch("src.project_docs.docs_digest", side_effect=RuntimeError("bozuk")):
            text = _guidance_context(self.root)
        self.assertIn(str(self.root), text)


class GuidanceCallShapeTest(unittest.TestCase):
    """The guidance request must stay a single, tool-constrained call."""

    def test_only_the_guidance_schema_is_sent(self):
        from src.prompt_frame import (
            PromptFrame,
            _GUIDANCE_TOOL_NAME,
            request_frame_guidance,
        )
        from src.tools import TOOLS_SCHEMA

        captured = {}

        def fake_complete(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(
                        tool_calls=[SimpleNamespace(
                            function=SimpleNamespace(
                                name=_GUIDANCE_TOOL_NAME,
                                # clarify=False: one non-empty suggestion per empty field.
                                arguments='{"rol": "muhendis", "cerceve": "kirp"}',
                            ),
                        )],
                        content=None,
                    ),
                )],
            )

        client = MagicMock()
        client.chat_complete.side_effect = fake_complete
        frame = PromptFrame(brief="bir iş", kisit="kısıt", cikti="çıktı")
        request_frame_guidance(client, "m", frame, False, "ctx")

        sent = captured["tools"]
        self.assertEqual(len(sent), 1)
        self.assertLess(len(sent), len(TOOLS_SCHEMA))
        self.assertEqual(captured["tool_choice"]["function"]["name"], sent[0]["function"]["name"])

    def test_context_reaches_the_system_message(self):
        from src.prompt_frame import PromptFrame, _GUIDANCE_TOOL_NAME, request_frame_guidance

        captured = {}

        def fake_complete(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(
                        tool_calls=[SimpleNamespace(
                            function=SimpleNamespace(
                                name=_GUIDANCE_TOOL_NAME,
                                arguments='{"question": "Hedef modül hangisi?"}',
                            ),
                        )],
                        content=None,
                    ),
                )],
            )

        client = MagicMock()
        client.chat_complete.side_effect = fake_complete
        request_frame_guidance(
            client, "m", PromptFrame(brief="iş"), True,
            "Çalışma dizini: D:\\x",
        )
        system = "\n".join(
            str(m.get("content") or "") for m in captured["messages"] if m.get("role") == "system"
        )
        self.assertIn("Çalışma dizini: D:\\x", system)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
