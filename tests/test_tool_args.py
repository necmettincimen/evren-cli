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


class TestSchemaMaps(unittest.TestCase):
    def test_type_map_covers_edit_file(self):
        self.assertEqual(TYPE_MAP["edit_file"]["edits"], "array")
        self.assertEqual(TYPE_MAP["edit_file"]["all"], "boolean")
        self.assertEqual(TYPE_MAP["view_file"]["start_line"], "integer")

    def test_required_map(self):
        self.assertEqual(REQUIRED_MAP["view_file"], ["path"])
        self.assertIn("content", REQUIRED_MAP["write_file"])


class TestCoerceValue(unittest.TestCase):
    def test_array_from_stringified_json(self):
        out = coerce_value('[{"a": 1}]', "array", "edits")
        self.assertEqual(out, [{"a": 1}])

    def test_array_from_single_object(self):
        self.assertEqual(coerce_value('{"a": 1}', "array", "edits"), [{"a": 1}])

    def test_object_from_stringified_json(self):
        self.assertEqual(coerce_value('{"a": 1}', "object", "meta"), {"a": 1})

    def test_empty_string_becomes_none(self):
        self.assertIsNone(coerce_value("   ", "array", "edits"))

    def test_plain_text_array_rejected(self):
        with self.assertRaises(ToolArgumentError) as ctx:
            coerce_value("def foo(): pass", "array", "edits")
        self.assertIn("JSON", str(ctx.exception))

    def test_malformed_json_array_rejected(self):
        with self.assertRaises(ToolArgumentError) as ctx:
            coerce_value('[{"target_snippet": ', "array", "edits")
        self.assertIn("çözümlenemedi", str(ctx.exception))

    def test_integer_from_string(self):
        self.assertEqual(coerce_value("12", "integer", "start_line"), 12)

    def test_integer_rejects_float_string(self):
        with self.assertRaises(ToolArgumentError):
            coerce_value("1.5", "integer", "start_line")

    def test_boolean_from_strings(self):
        self.assertIs(coerce_value("true", "boolean", "all"), True)
        self.assertIs(coerce_value("False", "boolean", "all"), False)
        self.assertIs(coerce_value("1", "boolean", "all"), True)

    def test_boolean_rejects_garbage(self):
        with self.assertRaises(ToolArgumentError):
            coerce_value("maybe", "boolean", "all")

    def test_wrong_container_type_rejected(self):
        with self.assertRaises(ToolArgumentError) as ctx:
            coerce_value(42, "array", "edits")
        self.assertIn("JSON dizisi", str(ctx.exception))


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

    def test_canonical_key_wins_over_alias(self):
        args = normalize_tool_arguments(
            "edit_file",
            {
                "path": "a.py",
                "edits": '[{"old_text": "alias", "target_snippet": "real", "new_text": "y"}]',
            },
            TYPE_MAP,
            REQUIRED_MAP,
        )
        self.assertEqual(args["edits"][0]["target_snippet"], "real")

    def test_aliased_top_level_path(self):
        args = normalize_tool_arguments(
            "view_file", {"file_path": "a.py", "start_line": "3"}, TYPE_MAP, REQUIRED_MAP
        )
        self.assertEqual(args["path"], "a.py")
        self.assertEqual(args["start_line"], 3)

    def test_arguments_as_json_string(self):
        args = normalize_tool_arguments(
            "view_file", '{"path": "a.py"}', TYPE_MAP, REQUIRED_MAP
        )
        self.assertEqual(args, {"path": "a.py"})

    def test_none_and_empty_arguments(self):
        self.assertEqual(normalize_tool_arguments("ssh_list_hosts", None, TYPE_MAP, REQUIRED_MAP), {})
        self.assertEqual(normalize_tool_arguments("ssh_list_hosts", "  ", TYPE_MAP, REQUIRED_MAP), {})

    def test_empty_target_snippet_is_preserved(self):
        args = normalize_tool_arguments(
            "edit_file", {"path": "a.py", "target_snippet": ""}, TYPE_MAP, REQUIRED_MAP
        )
        self.assertEqual(args["target_snippet"], "")

    def test_missing_required_parameter_reported(self):
        with self.assertRaises(ToolArgumentError) as ctx:
            normalize_tool_arguments("view_file", {"start_line": 1}, TYPE_MAP, REQUIRED_MAP)
        self.assertIn("path", str(ctx.exception))

    def test_non_object_arguments_rejected(self):
        with self.assertRaises(ToolArgumentError) as ctx:
            normalize_tool_arguments("view_file", '["a.py"]', TYPE_MAP, REQUIRED_MAP)
        self.assertIn("JSON nesnesi", str(ctx.exception))

    def test_empty_edits_list_rejected(self):
        with self.assertRaises(ToolArgumentError) as ctx:
            normalize_tool_arguments("edit_file", {"path": "a.py", "edits": "[]"}, TYPE_MAP, REQUIRED_MAP)
        self.assertIn("boş", str(ctx.exception))

    def test_edits_item_string_of_non_json_rejected(self):
        with self.assertRaises(ToolArgumentError):
            normalize_tool_arguments(
                "edit_file", {"path": "a.py", "edits": "not json"}, TYPE_MAP, REQUIRED_MAP
            )

    def test_unknown_extra_keys_are_kept(self):
        args = normalize_tool_arguments(
            "view_file", {"path": "a.py", "reason": "check"}, TYPE_MAP, REQUIRED_MAP
        )
        self.assertEqual(args["reason"], "check")


class TestApplyEditsGuards(unittest.TestCase):
    def test_string_edit_item_gives_clear_error(self):
        text, ok, err = apply_edits("hello", ["target"])
        self.assertFalse(ok)
        self.assertIn("bir nesne olmalı", err)
        self.assertEqual(text, "hello")

    def test_non_string_snippets_give_clear_error(self):
        text, ok, err = apply_edits("hello", [{"target_snippet": 5, "replacement_snippet": "y"}])
        self.assertFalse(ok)
        self.assertIn("metin olmalı", err)
        self.assertEqual(text, "hello")

    def test_valid_edits_still_apply(self):
        text, ok, err = apply_edits(
            "a\nb\n", [{"target_snippet": "a", "replacement_snippet": "z"}]
        )
        self.assertTrue(ok)
        self.assertEqual(err, "")
        self.assertEqual(text, "z\nb\n")


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

    def test_edit_file_with_bad_edits_json_returns_clear_error(self):
        (self.workspace / "a.py").write_text("x\n", encoding="utf-8")
        result = execute_tool_call(
            "edit_file",
            {"path": "a.py", "edits": '[{"target_snippet": '},
            workspace_root=self.workspace,
            auto_approve=True,
        )
        self.assertTrue(result.startswith("HATA:"))
        self.assertIn("geçersiz araç argümanları", result)
        self.assertNotIn("AttributeError", result)
        self.assertNotIn("has no attribute", result)

    def test_edit_file_with_scalar_edits_returns_clear_error(self):
        (self.workspace / "a.py").write_text("x\n", encoding="utf-8")
        result = execute_tool_call(
            "edit_file",
            {"path": "a.py", "edits": "just text"},
            workspace_root=self.workspace,
            auto_approve=True,
        )
        self.assertTrue(result.startswith("HATA:"))
        self.assertIn("geçersiz araç argümanları", result)

    def test_missing_required_path_returns_clear_error(self):
        result = execute_tool_call(
            "view_file", {"start_line": "2"}, workspace_root=self.workspace
        )
        self.assertTrue(result.startswith("HATA:"))
        self.assertIn("zorunlu parametreler eksik", result)

    def test_view_file_string_line_numbers_work(self):
        (self.workspace / "n.txt").write_text("1\n2\n3\n", encoding="utf-8")
        result = execute_tool_call(
            "view_file",
            {"path": "n.txt", "start_line": "2", "end_line": "3"},
            workspace_root=self.workspace,
        )
        self.assertIn("2 | 2", result)
        self.assertIn("3 | 3", result)

    def test_list_directory_string_flags_work(self):
        (self.workspace / "sub").mkdir()
        result = execute_tool_call(
            "list_directory", {"path": ".", "recursive": "true"}, workspace_root=self.workspace
        )
        self.assertIn("sub", result)


if __name__ == "__main__":
    unittest.main()
