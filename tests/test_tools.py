import unittest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch

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

    def test_web_fetch_schema_present(self):
        from src.tools import TOOLS_SCHEMA

        entry = next(
            (t for t in TOOLS_SCHEMA if t.get("function", {}).get("name") == "web_fetch"), None
        )
        self.assertIsNotNone(entry)
        params = entry["function"]["parameters"]
        self.assertIn("url", params["properties"])
        self.assertEqual(params["required"], ["url"])

    def test_web_fetch_dispatch_mocked(self):
        # Dispatch must route to tool_web_fetch without touching the network.
        with patch("src.tools.tool_web_fetch", return_value="SAHTE ÇIKTI") as m:
            res = execute_tool_call("web_fetch", {"url": "https://example.com"})
        self.assertEqual(res, "SAHTE ÇIKTI")
        m.assert_called_once_with(url="https://example.com", timeout=None, max_chars=None)

    def test_web_fetch_invalid_url_via_dispatch(self):
        # Real fetch_url path, but no network: scheme validation rejects it first.
        res = execute_tool_call("web_fetch", {"url": "ftp://example.com/x"})
        self.assertTrue(res.startswith("HATA:"))

    def test_edit_file_large_file_keeps_tail_and_size(self):
        # A large file must not be rewritten from a truncated read: the tail
        # survives and only the edited line changes (same byte size here).
        big = "".join(f"LINE_{i:05d} = {i}\n" for i in range(12000))
        self.assertGreater(len(big), 150000)
        target = self.workspace / "big.py"
        target.write_bytes(big.encode("utf-8"))
        before = target.stat().st_size

        res = tool_edit_file(
            "big.py",
            target_snippet="LINE_00000 = 0\n",
            replacement_snippet="LINE_00000 = 0\nEVREN_SHELL_LABEL = 'x'\n",
            workspace_root=self.workspace,
            auto_approve=True,
        )
        self.assertIn("BAŞARILI", res)

        content = target.read_text(encoding="utf-8")
        self.assertIn("EVREN_SHELL_LABEL = 'x'", content)
        self.assertIn("LINE_11999 = 11999", content)
        self.assertNotIn("karakterde kesildi", content)
        self.assertEqual(target.stat().st_size, before + len("EVREN_SHELL_LABEL = 'x'\n"))

    def test_edit_file_reports_diff_summary_and_skips_verification(self):
        # The edit result must carry a diff summary and say no verification is
        # needed, so the model does not spend extra steps re-reading the file.
        target = self.workspace / "summary.py"
        target.write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")

        res = tool_edit_file(
            "summary.py",
            target_snippet="b = 2\n",
            replacement_snippet="b = 20\nd = 4\n",
            workspace_root=self.workspace,
            auto_approve=True,
        )
        self.assertIn("BAŞARILI", res)
        self.assertIn("Diff özeti:", res)
        self.assertIn("+2/-1 satır", res)
        self.assertIn("doğrulama gerekmez", res.lower())
        self.assertIn("geri kalanı değişmedi", res)
        self.assertEqual(target.read_text(encoding="utf-8"), "a = 1\nb = 20\nd = 4\nc = 3\n")

    def test_edit_file_writes_only_changed_span(self):
        # The unchanged head/tail must not be re-emitted: only the span is written.
        from src.file_ops import write_changed_lines

        target = self.workspace / "span.txt"
        old = "a\nb\nc\nd\n"
        target.write_bytes(old.encode("utf-8"))
        ok, _backup, err = write_changed_lines("span.txt", old, "a\nB\nc\nd\n", self.workspace)
        self.assertTrue(ok, err)
        self.assertEqual(target.read_text(encoding="utf-8"), "a\nB\nc\nd\n")

        ok, _backup, err = write_changed_lines("span.txt", "a\nB\nc\nd\n", "a\nc\nd\n", self.workspace)
        self.assertTrue(ok, err)
        self.assertEqual(target.read_text(encoding="utf-8"), "a\nc\nd\n")


if __name__ == "__main__":
    unittest.main()
