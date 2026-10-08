import unittest
import tempfile
import shutil
from pathlib import Path

from src.tools import (
    tool_view_file,
    tool_write_file,
    tool_edit_file,
    tool_list_directory,
    tool_find_files,
    tool_create_plan,
    execute_tool_call,
)


class TestTools(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = Path(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_view_file(self):
        test_file = self.workspace / "example.py"
        test_file.write_text("line1\nline2\nline3\nline4\n", encoding="utf-8")

        result = tool_view_file("example.py", start_line=2, end_line=3, workspace_root=self.workspace)
        self.assertIn("line2", result)
        self.assertIn("line3", result)
        self.assertNotIn("line1", result)

    def test_write_and_edit_file(self):
        test_file = "script.py"
        write_res = tool_write_file(test_file, "def foo():\n    return 1\n", workspace_root=self.workspace, auto_approve=True)
        self.assertIn("BAŞARILI", write_res)

        edit_res = tool_edit_file(
            test_file,
            target_snippet="return 1",
            replacement_snippet="return 100",
            workspace_root=self.workspace,
            auto_approve=True,
        )
        self.assertIn("BAŞARILI", edit_res)

        content = (self.workspace / test_file).read_text(encoding="utf-8")
        self.assertIn("return 100", content)

    def test_list_and_find(self):
        (self.workspace / "folder").mkdir()
        (self.workspace / "folder" / "sub.txt").write_text("hello world", encoding="utf-8")

        list_res = tool_list_directory(".", recursive=True, workspace_root=self.workspace)
        self.assertIn("folder/sub.txt", list_res)

        find_res = tool_find_files("*.txt", query="world", workspace_root=self.workspace)
        self.assertIn("folder/sub.txt", find_res)

    def test_execute_tool_dispatch(self):
        res = execute_tool_call("view_file", {"path": "non_existent.py"}, workspace_root=self.workspace)
        self.assertTrue(res.startswith("HATA:"))

    def test_create_plan(self):
        res = tool_create_plan(
            "my-plan",
            "# Plan\n\nAdım 1",
            workspace_root=self.workspace,
            auto_approve=True,
        )
        self.assertIn("BAŞARILI", res)
        plan_file = self.workspace / "plans" / "my-plan" / "plan.md"
        self.assertTrue(plan_file.exists())
        self.assertIn("Adım 1", plan_file.read_text(encoding="utf-8"))

    def test_create_plan_invalid_name(self):
        res = tool_create_plan("!!!", "content", workspace_root=self.workspace, auto_approve=True)
        self.assertTrue(res.startswith("HATA:"))

    def test_execute_create_plan_dispatch(self):
        res = execute_tool_call(
            "create_plan",
            {"name": "dispatch-plan", "content": "# x"},
            workspace_root=self.workspace,
            auto_approve=True,
        )
        self.assertIn("BAŞARILI", res)


if __name__ == "__main__":
    unittest.main()
