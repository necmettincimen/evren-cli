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
