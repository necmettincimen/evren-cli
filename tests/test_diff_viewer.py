import unittest
from src.diff_viewer import generate_unified_diff, apply_replacement, apply_edits


class TestDiffViewer(unittest.TestCase):
    def test_generate_diff(self):
        old = "def hello():\n    return 'old'\n"
        new = "def hello():\n    return 'new'\n"
        diff = generate_unified_diff(old, new, "test.py")
        self.assertIn("-    return 'old'", diff)
        self.assertIn("+    return 'new'", diff)

    def test_apply_replacement_exact(self):
        original = "a = 1\nb = 2\nc = 3\n"
        target = "b = 2"
        replacement = "b = 42"
        new_text, success, err = apply_replacement(original, target, replacement)
        self.assertTrue(success)
        self.assertEqual(err, "")
        self.assertIn("b = 42", new_text)

    def test_apply_replacement_duplicate(self):
        original = "print('x')\nprint('x')\n"
        target = "print('x')"
        replacement = "print('y')"
        new_text, success, err = apply_replacement(original, target, replacement)
        self.assertFalse(success)
        self.assertIn("birden fazla kez", err)

    def test_apply_replacement_not_found(self):
        original = "value = 10"
        target = "value = 999"
        replacement = "value = 20"
        new_text, success, err = apply_replacement(original, target, replacement)
        self.assertFalse(success)
        self.assertIn("bulunamadı", err)

    def test_replace_all_flag(self):
        original = "x = 1\nx = 1\n"
        new_text, success, err = apply_replacement(original, "x = 1", "x = 2", replace_all=True)
        self.assertTrue(success)
        self.assertEqual(new_text.count("x = 2"), 2)

    def test_duplicate_without_all_fails(self):
        original = "x = 1\nx = 1\n"
        _, success, err = apply_replacement(original, "x = 1", "x = 2")
        self.assertFalse(success)
        self.assertIn("all", err.lower())

    def test_contextual_error_shows_closest_line(self):
        original = "def foo():\n    return 1\n"
        # Target differs only by indentation.
        _, success, err = apply_replacement(original, "def foo():\nreturn 1", "x")
        self.assertFalse(success)
        self.assertIn("En yakın satır", err)

    def test_apply_edits_multiple(self):
        original = "a = 1\nb = 2\nc = 3\n"
        edits = [
            {"target_snippet": "a = 1", "replacement_snippet": "a = 10"},
            {"target_snippet": "c = 3", "replacement_snippet": "c = 30"},
        ]
        new_text, success, err = apply_edits(original, edits)
        self.assertTrue(success)
        self.assertIn("a = 10", new_text)
        self.assertIn("c = 30", new_text)
        self.assertIn("b = 2", new_text)

    def test_apply_edits_reports_failing_index(self):
        original = "a = 1\n"
        edits = [
            {"target_snippet": "a = 1", "replacement_snippet": "a = 10"},
            {"target_snippet": "zzz", "replacement_snippet": "yyy"},
        ]
        _, success, err = apply_edits(original, edits)
        self.assertFalse(success)
        self.assertIn("Edit #2", err)

    def test_apply_edits_empty(self):
        _, success, err = apply_edits("x", [])
        self.assertFalse(success)


if __name__ == "__main__":
    unittest.main()


class AlreadyAppliedTest(unittest.TestCase):
    def test_reapplied_single_edit_is_reported_not_failed(self):
        from src.diff_viewer import ALREADY_APPLIED, apply_edits

        text = 'READ_ONLY = {\n    "a", "b",\n}\n'
        _new, ok, err = apply_edits(text, [{"target_snippet": '    "a",\n}', "replacement_snippet": '    "a", "b",\n}'}])
        self.assertFalse(ok)
        self.assertTrue(err.startswith(ALREADY_APPLIED))

    def test_mixed_batch_applies_the_rest(self):
        from src.diff_viewer import apply_edits

        text = "x = 1\ny = 2\n"
        new, ok, err = apply_edits(text, [
            {"target_snippet": "x = 0", "replacement_snippet": "x = 1"},  # already there
            {"target_snippet": "y = 2", "replacement_snippet": "y = 3"},
        ])
        self.assertTrue(ok, err)
        self.assertEqual(new, "x = 1\ny = 3\n")

    def test_tool_returns_info_not_error(self):
        import tempfile
        from pathlib import Path
        from src.tools import execute_tool_call

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "m.py").write_text("x = 1\n", encoding="utf-8")
            out = execute_tool_call("edit_file", {
                "path": "m.py", "target_snippet": "x = 0", "replacement_snippet": "x = 1",
            }, workspace_root=root, auto_approve=True)
        self.assertTrue(out.startswith("BİLGİ:"), out)

    def test_genuinely_missing_target_is_still_an_error(self):
        from src.diff_viewer import apply_edits

        _new, ok, err = apply_edits("x = 1\n", [{"target_snippet": "z = 9", "replacement_snippet": "z = 8"}])
        self.assertFalse(ok)
        self.assertNotIn("ZATEN", err)


class MultilineMismatchMessageTest(unittest.TestCase):
    def test_points_at_first_differing_line(self):
        from src.diff_viewer import apply_replacement

        text = 'FORM_INTRO = (\n    "Brief yaz ve Enter\'a bas: görev"\n    "ikinci satır"\n)\n'
        target = 'FORM_INTRO = (\n    "Brief yaz ve Enter\'a bas: görev"\n    "ikinci SATIR"\n)'
        _new, ok, err = apply_replacement(text, target, "X = 1")
        self.assertFalse(ok)
        self.assertIn("En yakın satır (satır 3)", err)
        self.assertIn("ikinci SATIR", err)
