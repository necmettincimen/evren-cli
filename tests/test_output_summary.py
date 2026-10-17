"""Output-type summaries, prefix size budget and SSH tool omission."""

import json
import unittest
from unittest import mock

from src.modes import AgentMode
from src.output_summary import summarize_output
from src.tools import TOOLS_SCHEMA, tools_for_mode


def _pytest_output() -> str:
    lines = ["============ test session starts ============"]
    lines += [f"tests/test_a.py::test_ok_{i} PASSED" for i in range(300)]
    for name in ("test_x", "test_y"):
        lines.append(f"______ {name} ______")
        lines += [f"    line {i} of traceback" for i in range(40)]
        lines.append("E   AssertionError: boom")
    lines.append("=========== short test summary info ===========")
    lines.append("FAILED tests/test_a.py::test_x - AssertionError: boom")
    lines.append("FAILED tests/test_a.py::test_y - AssertionError: boom")
    lines.append("========= 2 failed, 300 passed in 4.10s =========")
    return "\n".join(lines)


class OutputSummaryTests(unittest.TestCase):
    def test_pytest_summary_keeps_failures_and_totals(self):
        text = _pytest_output()
        out = summarize_output(text, "pytest -q")
        self.assertIsNotNone(out)
        self.assertLessEqual(len(out), 3200)
        self.assertLess(len(out), len(text) * 0.4)
        for needle in ("test_x", "test_y", "AssertionError: boom", "2 failed, 300 passed"):
            self.assertIn(needle, out)




if __name__ == "__main__":
    unittest.main()
