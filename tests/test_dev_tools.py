"""Coding-task helpers: grep-like find_files, PowerShell unwrap, syntax check, plan intent."""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.agent import AgentSession, _PLAN_INTENT_RE
from src.tools import _command_argv, execute_tool_call, tool_find_files


class FindFilesGrepTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "src").mkdir()
        (self.root / "src" / "a.py").write_text("x = 1\ndef Get_Skill():\n    pass\n", encoding="utf-8")
        (self.root / "src" / "b.py").write_text("SKILLS = {}\n", encoding="utf-8")

    def test_query_returns_path_line_text_case_insensitive(self):
        out = tool_find_files("*.py", "get_skill", self.root)
        self.assertEqual(out, "src/a.py:2: def Get_Skill():")

    def test_alternatives_with_pipe(self):
        out = tool_find_files("*.py", "get_skill|SKILLS", self.root).splitlines()
        self.assertEqual(out, ["src/a.py:2: def Get_Skill():", "src/b.py:1: SKILLS = {}"])

    def test_path_pattern_and_no_match(self):
        self.assertIn("src/b.py:1:", tool_find_files("src/b.py", "skills", self.root))
        self.assertEqual(tool_find_files("*.py", "yok_boyle", self.root), "Eşleşme bulunamadı.")

    def test_plain_glob_lists_files(self):
        self.assertEqual(tool_find_files("*.py", "", self.root).splitlines(), ["src/a.py", "src/b.py"])


class PowerShellUnwrapTest(unittest.TestCase):
    def test_double_wrapper_is_removed(self):
        argv, _ = _command_argv('powershell -NoProfile -Command "Get-Item . | Select Name"', True)
        self.assertTrue(argv[-1].endswith("Get-Item . | Select Name"))
        self.assertNotIn("-Command \"", argv[-1])

    def test_plain_script_untouched(self):
        argv, _ = _command_argv("Get-Item .", True)
        self.assertTrue(argv[-1].endswith("; Get-Item ."))


class SyntaxCheckTest(unittest.TestCase):
    def test_broken_python_edit_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out = execute_tool_call(
                "write_file", {"path": "m.py", "content": "_R = re.compile(r^\\s*x)\n"},
                workspace_root=root, auto_approve=True,
            )
        self.assertIn("SÖZDİZİMİ HATASI: m.py:1", out)

    def test_valid_python_has_no_report(self):
        with tempfile.TemporaryDirectory() as directory:
            out = execute_tool_call(
                "write_file", {"path": "m.py", "content": "x = 1\n"},
                workspace_root=Path(directory), auto_approve=True,
            )
        self.assertNotIn("HATASI", out)

    def test_broken_json_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            out = execute_tool_call(
                "write_file", {"path": "c.json", "content": "{bad"},
                workspace_root=Path(directory), auto_approve=True,
            )
        self.assertIn("JSON HATASI", out)


class PlanIntentTest(unittest.TestCase):
    def test_intent_words(self):
        for text in ("Planla, uygula ve testleri çalıştır", "önce plan yap", "planlayıp ekle"):
            self.assertTrue(_PLAN_INTENT_RE.search(text), text)
        for text in ("README düzelt", "plan modunu açıkla", "planet listesi"):
            self.assertFalse(_PLAN_INTENT_RE.search(text), text)

    def test_short_brief_with_plan_intent_goes_to_planner(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "EVREN_AUTO_DOCS": "false", "EVREN_AUTO_ROUTE": "true", "EVREN_PLANNER_MODEL": "planner-m",
        }):
            client = MagicMock(default_model="exec-m")
            session = AgentSession(client, workspace_root=Path(directory))
            models = []

            def reply(**kwargs):
                models.append(kwargs["model"])
                return SimpleNamespace(choices=[SimpleNamespace(
                    finish_reason="stop", message=SimpleNamespace(content="1. adım", tool_calls=[]),
                )], usage=None)

            client.chat_stream_assembled.side_effect = reply
            for name in ("print_info", "print_warning", "print_markdown"):
                patcher = patch(f"src.agent.{name}")
                patcher.start()
                self.addCleanup(patcher.stop)
            session.run_step("skill desteği ekle. Planla, uygula ve testleri çalıştır.")
        self.assertEqual(models, ["planner-m", "exec-m"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class EditGuardTest(unittest.TestCase):
    def test_probe_target_is_refused_with_view_file_hint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "m.py").write_text("x = 1\n", encoding="utf-8")
            for target in ("placeholder", "PLACEHOLDER_DO_NOT_MATCH"):
                out = execute_tool_call("edit_file", {
                    "path": "m.py", "target_snippet": target, "replacement_snippet": "y",
                }, workspace_root=root, auto_approve=True)
                self.assertIn("view_file", out, target)

    def test_missing_file_points_to_write_file(self):
        with tempfile.TemporaryDirectory() as directory:
            out = execute_tool_call("edit_file", {
                "path": "yeni.py", "target_snippet": "a", "replacement_snippet": "b",
            }, workspace_root=Path(directory), auto_approve=True)
        self.assertIn("write_file", out)


class PlannedStepBudgetTest(unittest.TestCase):
    def _session(self, directory):
        client = MagicMock(default_model="m")
        return AgentSession(client, workspace_root=Path(directory))

    def test_budget_grows_with_plan_items(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"EVREN_AUTO_DOCS": "false"}):
            session = self._session(directory)
            session._exec_profile = ("m", False)
            plan = "\n".join(f"{i}. adım {i}" for i in range(1, 11))
            session.messages.append({"role": "user", "content": f"iş\n\nUYGULAMA PLANI (planlayıcı model: p):\n{plan}"})
            self.assertEqual(session._planned_step_budget(20), 35)
            session.messages[-1]["content"] += "\n" + "\n".join(f"{i}. ek" for i in range(11, 30))
            self.assertEqual(session._planned_step_budget(20), 40)

    def test_no_plan_keeps_the_default(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"EVREN_AUTO_DOCS": "false"}):
            session = self._session(directory)
            session.messages.append({"role": "user", "content": "1. tek\n2. iki"})
            self.assertEqual(session._planned_step_budget(20), 20)


class ContextDateAndCommitRuleTest(unittest.TestCase):
    def test_execution_context_carries_todays_date_only(self):
        from datetime import date
        from src.config import execution_context_block

        block = execution_context_block(Path("."))
        self.assertIn(f"Bugünün tarihi: {date.today().isoformat()}", block)
        self.assertNotRegex(block, r"\d{2}:\d{2}")  # no clock time: keeps the cache stable

    def test_commit_rule_in_system_prompt(self):
        from src.agent import SYSTEM_PROMPT

        self.assertIn("git log -5", SYSTEM_PROMPT)
        self.assertIn("~72 karakter", SYSTEM_PROMPT)
        self.assertIn("ASCII'ye çevirme", SYSTEM_PROMPT)


class FindFilesRegexTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "m.py").write_text(
            '"""EVREN CLI doc."""\nclass A:\n    def get_x(self):\n        return "2.0.0"\n',
            encoding="utf-8",
        )

    def test_inline_flag_and_escaped_dot_regexes(self):
        self.assertIn("m.py:1:", tool_find_files("*.py", "(?i)evren", self.root))
        self.assertIn("m.py:4:", tool_find_files("*.py", r"2\.0\.0", self.root))
        self.assertIn("m.py:3:", tool_find_files("*.py", r"def (get|set)_x", self.root))

    def test_unbalanced_paren_falls_back_to_literal(self):
        self.assertIn("m.py:3:", tool_find_files("*.py", "def get_x(", self.root))

    def test_indentation_is_kept_for_edit_targets(self):
        out = tool_find_files("*.py", "get_x", self.root)
        self.assertEqual(out, "m.py:3:     def get_x(self):")
