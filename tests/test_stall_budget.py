"""Tests for the stall output budget (plan token-consumption-fixes, Faz 5 / F5)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src import agent as agent_mod
from src.agent import AgentSession, _stall_token_cap


def response(content="", finish_reason="stop", tools=()):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            finish_reason=finish_reason,
            message=SimpleNamespace(content=content, tool_calls=list(tools)),
        )],
        usage=None,
    )


class StallBudgetBehaviourTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.client = MagicMock(default_model="m")
        with patch.dict(os.environ, {"EVREN_AUTO_DOCS": "false"}, clear=False):
            self.session = AgentSession(self.client, workspace_root=Path(self._tmp.name))
        for name in ("print_info", "print_warning", "print_error", "print_markdown",
                     "print_tool_call", "print_tool_result", "print_reasoning"):
            patcher = patch(f"src.agent.{name}")
            patcher.start()
            self.addCleanup(patcher.stop)

    def _budgets_and_limits(self, responses, env=None):
        """Runs a task and returns (max_tokens per call, compaction limit per call)."""
        self.client.chat_stream_assembled.side_effect = list(responses) + [response("bitti", "stop")]
        seen_budget = []
        seen_limit = []
        real_compact = self.session.compactor.compact

        def spy(messages, output_budget, **kwargs):
            seen_limit.append(kwargs.get("max_tokens"))
            return real_compact(messages, output_budget, **kwargs)

        self.session.compactor.compact = spy
        original = self.client.chat_stream_assembled

        def capture(**kwargs):
            seen_budget.append(kwargs.get("max_tokens"))
            return original(**kwargs)

        self.client.chat_stream_assembled = capture
        with patch.dict(os.environ, {"EVREN_MAX_STEPS": "5", **(env or {})}, clear=False):
            self.session.run_step("kesilen yanitlar")
        return seen_budget, seen_limit

    def test_budget_doubles_but_stops_at_the_new_cap(self):
        # Three consecutive stalls: 4096 -> 8192 -> 16384 (capped), never 32k.
        stalls = [response("", "length")] * 3
        budgets, _limits = self._budgets_and_limits(stalls)
        self.assertGreaterEqual(len(budgets), 4)
        self.assertLessEqual(max(b for b in budgets if b), _stall_token_cap())
        self.assertGreater(max(budgets), min(budgets))


    def test_second_stall_halves_the_compaction_limit(self):
        stalls = [response("", "length")] * 2
        _budgets, limits = self._budgets_and_limits(stalls)
        self.assertTrue(limits)
        # Later calls (repeats >= 2) must compact harder than the first call.
        self.assertLess(limits[-1], limits[0])


    def test_final_error_mentions_the_tuning_knobs(self):
        stalls = [response("", "length")] * 4
        self.client.chat_stream_assembled.side_effect = list(stalls) + [response("bitti", "stop")]
        with patch.dict(os.environ, {"EVREN_MAX_STEPS": "9"}, clear=False):
            text = self.session.run_step("hep kesiliyor")
        self.assertIn("EVREN_STALL_TOKEN_CAP", text)
        self.assertIn("/tokens", text)
        # Never advertise a command that does not exist.
        self.assertNotIn("/compact", text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
