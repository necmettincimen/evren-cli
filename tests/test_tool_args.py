"""Tests for tolerant tool-argument normalization (stringified JSON params)."""

import tempfile
import shutil
import unittest
from pathlib import Path

from src.diff_viewer import apply_edits
from src.tool_args import (
    ToolArgumentError,
    coerce_value,
    normalize_tool_arguments,
    schema_required_map,
    schema_type_map,
)
from src.tools import TOOLS_SCHEMA, execute_tool_call


TYPE_MAP = schema_type_map(TOOLS_SCHEMA)
REQUIRED_MAP = schema_required_map(TOOLS_SCHEMA)




class TestCoerceValue(unittest.TestCase):
    def test_array_from_stringified_json(self):
        out = coerce_value('[{"a": 1}]', "array", "edits")
        self.assertEqual(out, [{"a": 1}])




    def test_plain_text_array_rejected(self):
        with self.assertRaises(ToolArgumentError) as ctx:
            coerce_value("def foo(): pass", "array", "edits")
        self.assertIn("JSON", str(ctx.exception))


    def test_integer_from_string(self):
        self.assertEqual(coerce_value("12", "integer", "start_line"), 12)


    def test_boolean_from_strings(self):
        self.assertIs(coerce_value("true", "boolean", "all"), True)
        self.assertIs(coerce_value("False", "boolean", "all"), False)
        self.assertIs(coerce_value("1", "boolean", "all"), True)




class TestNormalizeToolArguments(unittest.TestCase):
    def test_stringified_edits_become_list_of_dicts(self):
        args = normalize_tool_arguments(
            "edit_file",
            {
                "path": "a.py",
                "edits": '[{"target_snippet": "x", "replacement_snippet": "y", "all": "true"}]',
            },
            TYPE_MAP,
            REQUIRED_MAP,
        )
        self.assertIsInstance(args["edits"], list)
        self.assertIsInstance(args["edits"][0], dict)
        self.assertIs(args["edits"][0]["all"], True)

    def test_aliased_edit_keys_are_renamed(self):
        args = normalize_tool_arguments(
            "edit_file",
            {"path": "a.py", "edits": '[{"old_text": "x", "new_text": "y"}]'},
            TYPE_MAP,
            REQUIRED_MAP,
        )
        self.assertEqual(args["edits"][0]["target_snippet"], "x")
        self.assertEqual(args["edits"][0]["replacement_snippet"], "y")
        self.assertNotIn("old_text", args["edits"][0])
        self.assertNotIn("new_text", args["edits"][0])





    def test_empty_target_snippet_is_preserved(self):
        args = normalize_tool_arguments(
            "edit_file", {"path": "a.py", "target_snippet": ""}, TYPE_MAP, REQUIRED_MAP
        )
        self.assertEqual(args["target_snippet"], "")

    def test_missing_required_parameter_reported(self):
        with self.assertRaises(ToolArgumentError) as ctx:
            normalize_tool_arguments("view_file", {"start_line": 1}, TYPE_MAP, REQUIRED_MAP)
        self.assertIn("path", str(ctx.exception))






class TestApplyEditsGuards(unittest.TestCase):
    def test_string_edit_item_gives_clear_error(self):
        text, ok, err = apply_edits("hello", ["target"])
        self.assertFalse(ok)
        self.assertIn("bir nesne olmalı", err)
        self.assertEqual(text, "hello")




class TestExecuteToolCallNormalization(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = Path(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_edit_file_with_stringified_edits_succeeds(self):
        (self.workspace / "page.xsl").write_text("<root/>\n", encoding="utf-8")
        result = execute_tool_call(
            "edit_file",
            {
                "path": "page.xsl",
                "edits": '[{"old_text": "<root/>", "new_text": "<html><body/></html>"}]',
            },
            workspace_root=self.workspace,
            auto_approve=True,
        )
        self.assertIn("BAŞARILI", result)
        self.assertIn("<html>", (self.workspace / "page.xsl").read_text(encoding="utf-8"))




    def test_view_file_string_line_numbers_work(self):
        (self.workspace / "n.txt").write_text("1\n2\n3\n", encoding="utf-8")
        result = execute_tool_call(
            "view_file",
            {"path": "n.txt", "start_line": "2", "end_line": "3"},
            workspace_root=self.workspace,
        )
        self.assertIn("2 | 2", result)
        self.assertIn("3 | 3", result)



if __name__ == "__main__":
    unittest.main()
