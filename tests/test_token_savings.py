"""Acceptance measurement for the token-consumption fixes (plan §6-D).

Two measurements, deliberately separated so each fix is attributable:

1. `test_task_that_runs_to_the_ceiling` — the plan's literal criterion: the same
   never-finishing task under the old settings (30 steps) and the new defaults
   (12 steps). This is where F6 (the step ceiling) does its work.
2. `test_same_step_count_savings` — both runs pinned to 12 steps, so F6 is
   excluded and the remaining fixes (F1 compaction, F2 tool schemas, F3 docs
   budget, F5 stall cap) are measured on their own.

The synthetic task produces large tool outputs, which is what made the old 128k
compaction threshold quadratic in practice.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.agent import AgentSession
from src.token_manager import TokenManager

TOOL_OUTPUT_CHARS = 30_000  # a big view_file / run_command result

OLD_ENV = {
    "EVREN_MAX_STEPS": "30",
    "EVREN_DOCS_TOKENS": "3500",
    "EVREN_COMPACT_RATIO": "1.0",
    "EVREN_CONTEXT_BUDGET": "2000000",
    "EVREN_STALL_TOKEN_CAP": "32768",
}
NEW_ENV = {
    "EVREN_MAX_STEPS": "12",
    "EVREN_DOCS_TOKENS": "1200",
    "EVREN_COMPACT_RATIO": "0.6",
    "EVREN_STALL_TOKEN_CAP": "8192",
}

_PRINT_NAMES = (
    "print_info", "print_warning", "print_error", "print_markdown",
    "print_tool_call", "print_tool_result", "print_reasoning",
)


class TokenSavingsAcceptanceTest(unittest.TestCase):
    def _run_task(self, env: dict[str, str], steps: int | None) -> tuple[int, int]:
        """Runs a never-finishing task. Returns (total prompt tokens, requests).

        `steps=None` lets the environment's EVREN_MAX_STEPS decide the ceiling.
        """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()

        client = MagicMock(default_model="m")
        sent: list[int] = []
        tm = TokenManager()

        def chat_stream_assembled(**kwargs):
            payload = json.dumps(kwargs["messages"], ensure_ascii=False)
            payload += json.dumps(kwargs.get("tools") or [], ensure_ascii=False)
            sent.append(tm.estimate_text(payload))
            step = len(sent)
            call = SimpleNamespace(
                id=f"c{step}",
                function=SimpleNamespace(
                    name="view_file", arguments=json.dumps({"path": f"modul_{step}.py"})
                ),
            )
            return SimpleNamespace(choices=[SimpleNamespace(
                finish_reason="tool_calls",
                message=SimpleNamespace(content="", tool_calls=[call]),
            )], usage=None)

        client.chat_stream_assembled.side_effect = chat_stream_assembled

        # Silence the terminal: printing 30k chars per step dominates runtime and
        # says nothing about the token budget.
        patches = [patch(f"src.agent.{name}") for name in _PRINT_NAMES]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])

        with patch.dict(os.environ, {"EVREN_AUTO_DOCS": "false", **env}, clear=False):
            session = AgentSession(client, workspace_root=root)
            with patch("src.agent.execute_tool_call", return_value="x" * TOOL_OUTPUT_CHARS):
                session.run_step("buyuk ciktili gorev", max_steps=steps)
        return sum(sent), len(sent)

    def _both_runs(self, steps: int | None) -> tuple[int, int, int, int]:
        with patch.dict(os.environ, {}, clear=False):
            for key in OLD_ENV:
                os.environ.pop(key, None)
            old_total, old_requests = self._run_task(OLD_ENV, steps)
            for key in OLD_ENV:
                os.environ.pop(key, None)
            new_total, new_requests = self._run_task(NEW_ENV, steps)
        return old_total, old_requests, new_total, new_requests

    def test_task_that_runs_to_the_ceiling(self):
        """Plan §6-D: >=35% fewer prompt tokens on a task that hits the ceiling."""
        old_total, old_requests, new_total, new_requests = self._both_runs(None)
        self.assertEqual(old_requests, 30)
        self.assertEqual(new_requests, 12)
        saving = 1 - (new_total / old_total)
        self.assertGreaterEqual(saving, 0.35, f"saving was {saving:.1%}")

    def test_same_step_count_savings(self):
        """F1+F2+F3+F5 alone (F6 excluded by pinning both runs to 12 steps)."""
        old_total, old_requests, new_total, new_requests = self._both_runs(12)
        self.assertEqual(old_requests, new_requests)
        saving = 1 - (new_total / old_total)
        self.assertGreater(saving, 0)
        # Measured ~35% on this synthetic task; guard against silent regressions.
        self.assertGreaterEqual(saving, 0.25, f"saving was {saving:.1%}")

    def test_base_prefix_cost_dropped_with_step_ceiling(self):
        """F6 arithmetic: the same per-step prefix is sent 12 instead of 30 times."""
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {"EVREN_AUTO_DOCS": "false"}, clear=False):
                session = AgentSession(
                    MagicMock(default_model="m"), workspace_root=Path(td).resolve()
                )
            prefix = session.token_manager.estimate_text(
                json.dumps(session.messages, ensure_ascii=False)
            )
        self.assertGreater(prefix, 0)
        self.assertLess(prefix * 12, prefix * 30 * 0.45)

    def test_docs_budget_shrinks_the_system_prompt(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            with patch.dict(os.environ, {"EVREN_AUTO_DOCS": "true"}, clear=False):
                from src.project_docs import agent_doc_path, ensure_project_docs

                ensure_project_docs(root, goal="butce karsilastirmasi")
                # A realistically large managed document: the budget only matters
                # when the sections actually exceed it. Sections are wrapped in
                # `evren:managed` markers (and are empty in the fresh template),
                # so the filler goes inside the conventions block.
                path = agent_doc_path(root)
                text = path.read_text(encoding="utf-8-sig")
                marker = "<!-- evren:managed:start section=conventions -->"
                self.assertIn(marker, text)
                filler = "\n".join(
                    f"- kural {i}: hedefli duzenle, sonra test et" for i in range(160)
                )
                path.write_text(
                    text.replace(marker, f"{marker}\n{filler}"),
                    encoding="utf-8", newline="",
                )

            sizes = {}
            # EVREN_AUTO_DOCS must stay enabled: it gates the whole docs feature,
            # so disabling it would make both budgets read as "no docs at all".
            for label, tokens in (("eski", "3500"), ("yeni", "1200")):
                with patch.dict(os.environ,
                                {"EVREN_DOCS_TOKENS": tokens, "EVREN_AUTO_DOCS": "true"},
                                clear=False):
                    session = AgentSession(MagicMock(default_model="m"), workspace_root=root)
                    sizes[label] = len(session.system_prompt)
        self.assertLess(sizes["yeni"], sizes["eski"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
