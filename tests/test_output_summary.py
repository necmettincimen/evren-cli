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

    def test_grep_and_build_and_plain(self):
        grep = "\n".join(f"src/f{i % 7}.py:{i}: foo" for i in range(500))
        out = summarize_output(grep, "")
        self.assertIn("toplam 500 eşleşme, 7 dosya", out)
        build = "\n".join(
            ["Build started"] + [f"a.cs({i}): error CS0103: x" for i in range(5)] * 3
            + ["noise"] * 200 + ["Build FAILED.", "5 Error(s)"]
        )
        out = summarize_output(build, "dotnet build")
        self.assertIn("error CS0103", out)
        self.assertIn("5 Error(s)", out)
        self.assertIsNone(summarize_output("\n".join(["plain line"] * 300), "dir"))

    def test_prefix_budget_and_ssh_omission(self):
        from src.agent import SYSTEM_PROMPT

        self.assertLessEqual(len(SYSTEM_PROMPT), 3500)
        sizes = sum(len(json.dumps(t, ensure_ascii=False, separators=(",", ":"))) for t in TOOLS_SCHEMA)
        self.assertLessEqual(sizes, 8032 * 1.1)
        names = {t["function"]["name"] for t in tools_for_mode(AgentMode.NORMAL, include_ssh=False)}
        self.assertFalse({n for n in names if n.startswith("ssh_")})
        self.assertIn("run_command", names)
        with_ssh = {t["function"]["name"] for t in tools_for_mode(AgentMode.NORMAL)}
        self.assertIn("ssh_run", with_ssh)
        # Stable within a session: repeated calls give the same list.
        self.assertEqual(tools_for_mode(AgentMode.NORMAL, include_ssh=False),
                         tools_for_mode(AgentMode.NORMAL, include_ssh=False))


if __name__ == "__main__":
    unittest.main()
