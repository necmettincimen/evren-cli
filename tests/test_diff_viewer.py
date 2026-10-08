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
