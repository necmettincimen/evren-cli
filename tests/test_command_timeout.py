"""run_command katmanının timeout davranışı.

Kabul ölçütü: 400 sn'lik bir sleep, 20 sn'lik timeout ile kesilmeli; sonuç
"zaman aşımı" olmalı, çağrı ~20 sn'de dönmeli ve süreç ağacı ölü kalmalı.
"""

import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from src.proc_utils import stream_with_tree_kill
from src.tools import tool_run_command


def _drain(gen):
    """Consumes a stream_with_tree_kill generator, returning (lines, return_value)."""
    lines = []
    try:
        while True:
            lines.append(next(gen))
    except StopIteration as stop:
        return lines, stop.value


class TestStreamTimeout(unittest.TestCase):
    def test_sleep_is_cut_at_timeout(self):
        start = time.time()
        _, (code, out, err, timed_out) = _drain(
            stream_with_tree_kill(
                [sys.executable, "-c", "import time; time.sleep(400)"],
                timeout=2,
            )
        )
        elapsed = time.time() - start
        self.assertTrue(timed_out)
        self.assertEqual(code, -1)
        # Returns right after the deadline instead of waiting for the sleep.
        self.assertLess(elapsed, 20)


class TestRunCommandTimeout(unittest.TestCase):
    def test_tool_run_command_reports_timeout(self):
        with mock.patch("src.tools.print_command_output"), \
             mock.patch("src.tools.request_approval", return_value=None):
            start = time.time()
            result = tool_run_command(
                'python -c "import time; time.sleep(400)"',
                workspace_root=Path.cwd(),
                auto_approve=True,
                timeout_seconds=2,
            )
            elapsed = time.time() - start
        self.assertIn("zaman aşımı", result)
        self.assertLess(elapsed, 20)

    def test_timeout_reports_exit_code_124_and_message(self):
        """The model must see exit code 124 plus the exact timeout wording."""
        with mock.patch("src.tools.print_command_output"), \
             mock.patch("src.tools.request_approval", return_value=None):
            result = tool_run_command(
                'python -c "import time; time.sleep(400)"',
                workspace_root=Path.cwd(),
                auto_approve=True,
                timeout_seconds=2,
            )
        self.assertTrue(result.startswith("Çıkış Kodu: 124"), result)
        self.assertIn(
            "Komut 2 sn sonra zaman aşımına uğradı (süreç ağacı öldürüldü)", result
        )
        self.assertNotIn("Çıkış Kodu: -1", result)


if __name__ == "__main__":
    unittest.main()
