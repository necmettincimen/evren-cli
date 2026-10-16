"""Runs every `tests/scenarios/*.json` end to end (see tests/scenario_harness.py).

One JSON file = one generated test method, so adding a scenario needs no
Python. A scenario with an `xfail` reason documents a known-open bug: it is
reported as an expected failure and turns red ("unexpected success") the day
the bug is fixed, which is the cue to delete the `xfail` key.
"""

import json
import tempfile
import unittest
from pathlib import Path

from tests.scenario_harness import (
    ScenarioError,
    check_expectations,
    discover_scenarios,
    load_scenario,
    run_and_check,
    run_scenario,
    validate_scenario,
)

# The regressions that unit tests missed (plan: cli-stability-speed, item 0.1).
REGRESSION_SCENARIOS = (
    "repl_startup",
    "cli_docs_bootstrap",
    "background_needs_approval",
    "secret_env_keeps_path",
    "delegate_mode_gate",
    "ask_user_allowed_in_ask_mode",
    "git_grep_repeat_guard",
    "json_regex_escapes",
)


class ScenarioTests(unittest.TestCase):
    """Test methods are generated below, one per scenario file."""


def _make_test(path: Path, xfail_reason: str = ""):
    def test(self):
        scenario = load_scenario(path)
        result, failures = run_and_check(scenario)
        if failures:
            self.fail(
                f"scenario '{scenario['name']}' failed:\n  - "
                + "\n  - ".join(failures)
                + f"\n\n{scenario['description']}\n\n--- trace ---\n{result.trace()}"
            )

    test.__doc__ = f"Scenario file {path.name}" + (f" [known open bug: {xfail_reason}]" if xfail_reason else "")
    return test


def _register_scenarios() -> None:
    for path in discover_scenarios():
        try:
            reason = json.loads(path.read_text(encoding="utf-8")).get("xfail") or ""
        except (OSError, ValueError, AttributeError):
            reason = ""  # a broken file still gets a test; it fails with a clear schema error
        method = _make_test(path, str(reason))
        if reason:
            method = unittest.expectedFailure(method)
        setattr(ScenarioTests, f"test_{path.stem}", method)


_register_scenarios()


class ScenarioCatalogTests(unittest.TestCase):
    def test_every_scenario_file_follows_the_schema(self):
        paths = discover_scenarios()
        self.assertTrue(paths, "no scenario files found")
        for path in paths:
            with self.subTest(scenario=path.name):
                load_scenario(path)

    def test_regression_scenarios_are_present(self):
        names = {path.stem for path in discover_scenarios()}
        self.assertLessEqual(set(REGRESSION_SCENARIOS), names)


class HarnessSelfTests(unittest.TestCase):
    """The harness must fail loudly; a harness that cannot fail proves nothing."""

    BASE = {
        "name": "inline",
        "description": "inline harness check",
        "user_inputs": ["merhaba"],
        "model_replies": [{"content": "selam"}],
        "expect": {"final_contains": "selam", "request_count": 1},
    }

    def _run(self, **overrides):
        scenario = {**self.BASE, **overrides}
        validate_scenario(scenario)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            result = run_scenario(scenario, Path(directory))
            return result, check_expectations(result)

    def test_a_matching_scenario_passes(self):
        _result, failures = self._run()
        self.assertEqual(failures, [])

    def test_wrong_expectations_are_reported(self):
        _result, failures = self._run(expect={"final_contains": "başka", "request_count": 3})
        self.assertEqual(len(failures), 2, failures)

    def test_missing_model_reply_is_a_failure(self):
        _result, failures = self._run(model_replies=[], expect={"request_count": 1})
        self.assertTrue(any("exhausted" in f for f in failures), failures)

    def test_unconsumed_model_reply_is_a_failure(self):
        _result, failures = self._run(
            model_replies=[{"content": "selam"}, {"content": "fazla"}],
        )
        self.assertTrue(any("never consumed" in f for f in failures), failures)

    def test_tool_calls_reach_the_real_dispatcher(self):
        result, failures = self._run(
            workspace_files={"a.txt": "bir\niki\n"},
            model_replies=[
                {"tool_calls": [{"name": "view_file", "arguments": {"path": "a.txt"}}]},
                {"content": "okudum"},
            ],
            expect={"executed_tools": ["view_file"], "messages_contain": ["iki"], "request_count": 2},
        )
        self.assertEqual(failures, [])
        self.assertFalse(result.executed[0]["faked"])

    def test_faked_tool_results_skip_execution(self):
        result, failures = self._run(
            model_replies=[
                {"tool_calls": [{"name": "view_file", "arguments": {"path": "yok.txt"}}]},
                {"content": "tamam"},
            ],
            tool_results={"*": "SAHTE-SONUÇ"},
            expect={"executed_count": 1, "messages_contain": ["SAHTE-SONUÇ"]},
        )
        self.assertEqual(failures, [])
        self.assertTrue(result.executed[0]["faked"])

    def test_auto_approve_is_off_whatever_the_developer_env_says(self):
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"EVREN_AUTO_APPROVE": "true"}), \
                patch("src.config.EVREN_AUTO_APPROVE", True), \
                patch("src.tools.EVREN_AUTO_APPROVE", True):
            result, failures = self._run(
                model_replies=[
                    {"tool_calls": [{"name": "write_file", "arguments": {"path": "x.txt", "content": "k"}}]},
                    {"content": "bitti"},
                ],
                approvals=["y"],
                expect={"confirm_prompts": 1, "files": {"x.txt": {"equals": "k"}}},
            )
        self.assertEqual(failures, [])

    def test_unknown_keys_and_bad_values_are_rejected(self):
        for bad in (
            {"colour": "red"},
            {"kind": "web"},
            {"mode": "yolo"},
            {"expect": {"nonsense": 1}},
            {"expect": {}},
            {"user_inputs": []},
            {"model_replies": [{"tool_calls": []}]},
            {"workspace_files": {"../escape.txt": "x"}},
            {"tool_results": {"first": "x"}},
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(ScenarioError):
                    validate_scenario({**self.BASE, **bad})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
