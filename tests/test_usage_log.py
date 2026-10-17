"""Tests for the API usage log (plan token-consumption-fixes, Faz 0 / F7)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src import usage_log


class UsageLogTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        usage_log.reset_cache()
        self.addCleanup(usage_log.reset_cache)

    def record(self, **kwargs):
        defaults = {"model": "test-model", "step": 3, "mode": "normal"}
        defaults.update(kwargs)
        return usage_log.log_usage(
            {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140},
            workspace_root=self.root,
            **defaults,
        )

    def lines(self):
        path = usage_log.usage_log_path(self.root)
        return [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def test_appends_one_line_per_response(self):
        self.record()
        self.record(step=4)
        lines = self.lines()
        self.assertEqual(len(lines), 2)
        first = json.loads(lines[0])
        self.assertEqual(first["prompt_tokens"], 100)
        self.assertEqual(first["step"], 3)
        self.assertEqual(first["mode"], "normal")
        self.assertEqual(json.loads(lines[1])["step"], 4)


    def test_never_writes_content_or_secrets(self):
        # Split so the literal never looks like a real key in diffs or scans;
        # the runtime string is identical and still matches the redaction pattern.
        secret = "evren_llm_" + "SAKLIDEGERSAKLA"
        usage_log.log_usage(
            {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            model=f"model {secret}",
            step=1,
            mode="normal",
            workspace_root=self.root,
        )
        text = usage_log.usage_log_path(self.root).read_text(encoding="utf-8")
        # The model field is truncated to 64 chars, but a key must never appear.
        self.assertNotIn(secret, text)
        self.assertNotIn("content", text)
        self.assertNotIn("arguments", text)


    def test_missing_usage_is_not_logged(self):
        self.assertIsNone(usage_log.log_usage(None, workspace_root=self.root))
        self.assertFalse(usage_log.usage_log_path(self.root).exists())






    def test_rotation_archives_old_log(self):
        with patch.object(usage_log, "MAX_LINES", 3):
            for _ in range(5):
                self.record()
        archive = usage_log.usage_log_path(self.root).with_name(usage_log.ARCHIVE_FILE_NAME)
        self.assertTrue(archive.exists())
        self.assertEqual(len(self.lines()), 2)



    def test_summary_aggregates_and_averages(self):
        for prompt in (100, 200, 300):
            usage_log.log_usage(
                {"prompt_tokens": prompt, "completion_tokens": 10, "total_tokens": prompt + 10},
                workspace_root=self.root,
            )
        summary = usage_log.usage_summary(usage_log.read_usage(self.root), recent=2)
        self.assertEqual(summary["requests"], 3)
        self.assertEqual(summary["prompt_tokens"], 600)
        self.assertEqual(summary["avg_prompt_tokens_recent"], 250)
        self.assertEqual(summary["recent_window"], 2)


    def test_logging_failure_never_raises(self):
        with patch.object(usage_log, "usage_log_path", side_effect=RuntimeError("bozuk")):
            self.assertIsNone(self.record())




if __name__ == "__main__":  # pragma: no cover
    unittest.main()
