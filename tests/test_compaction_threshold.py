"""Tests for the early compaction threshold (plan token-consumption-fixes, Faz 2 / F1)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.agent import AgentSession
from src.config import compaction_ratio
from src.history_compactor import HistoryCompactor
from src.token_manager import DEFAULT_CONTEXT_WINDOW, TokenManager


def response(content="", finish_reason="stop", tools=()):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            finish_reason=finish_reason,
            message=SimpleNamespace(content=content, tool_calls=list(tools)),
        )],
        usage=None,
    )


class CompactionRatioTest(unittest.TestCase):
    def test_default_is_060(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EVREN_COMPACT_RATIO", None)
            self.assertEqual(compaction_ratio(), 0.6)


    def test_out_of_range_falls_back(self):
        for value in ("", "abc", "0", "-1", "1.5", "0.05"):
            with patch.dict(os.environ, {"EVREN_COMPACT_RATIO": value}):
                self.assertEqual(compaction_ratio(), 0.6)

    def test_one_disables_early_compaction(self):
        with patch.dict(os.environ, {"EVREN_COMPACT_RATIO": "1.0"}):
            self.assertEqual(compaction_ratio(), 1.0)


class CompactionLimitTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        with patch.dict(os.environ, {"EVREN_AUTO_DOCS": "false"}, clear=False):
            self.session = AgentSession(MagicMock(default_model="m"), workspace_root=Path(self._tmp.name))





class EarlyCompactionBehaviourTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # History sits between the two thresholds: ~2.7k tokens, so a full-window
        # limit keeps it while a 60% limit does not.
        self.tm = TokenManager(context_window=3000)
        self.compactor = HistoryCompactor(self.tm)

    def big_history(self, turns=12):
        messages = [{"role": "system", "content": "sistem"}]
        for i in range(turns):
            messages.append({"role": "user", "content": f"kullanıcı {i} " + "x" * 400})
            messages.append({"role": "assistant", "content": f"yanıt {i} " + "y" * 400})
        return messages



    def test_agent_passes_the_fractional_limit(self):
        with patch.dict(os.environ, {"EVREN_AUTO_DOCS": "false"}, clear=False):
            session = AgentSession(MagicMock(default_model="m"), workspace_root=Path(self._tmp.name))
        seen = []
        real_compact = session.compactor.compact

        def spy(messages, output_budget, **kwargs):
            seen.append(kwargs.get("max_tokens"))
            return real_compact(messages, output_budget, **kwargs)

        session.compactor.compact = spy
        session.client.chat_stream_assembled.return_value = response("bitti", "stop")
        with patch.dict(os.environ, {"EVREN_COMPACT_RATIO": "0.6", "EVREN_MAX_STEPS": "1"}, clear=False):
            session.run_step("tek adım")

        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0], session.compaction_limit())
        self.assertLess(seen[0], session.token_manager.context_window)



if __name__ == "__main__":  # pragma: no cover
    unittest.main()
