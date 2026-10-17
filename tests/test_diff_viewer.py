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




    def test_duplicate_without_all_fails(self):
        original = "x = 1\nx = 1\n"
        _, success, err = apply_replacement(original, "x = 1", "x = 2")
        self.assertFalse(success)
        self.assertIn("all", err.lower())



    def test_apply_edits_reports_failing_index(self):
        original = "a = 1\n"
        edits = [
            {"target_snippet": "a = 1", "replacement_snippet": "a = 10"},
            {"target_snippet": "zzz", "replacement_snippet": "yyy"},
        ]
        _, success, err = apply_edits(original, edits)
        self.assertFalse(success)
        self.assertIn("Edit #2", err)



if __name__ == "__main__":
    unittest.main()


class AlreadyAppliedTest(unittest.TestCase):
    def test_reapplied_single_edit_is_reported_not_failed(self):
        from src.diff_viewer import ALREADY_APPLIED, apply_edits

        text = 'READ_ONLY = {\n    "a", "b",\n}\n'
        _new, ok, err = apply_edits(text, [{"target_snippet": '    "a",\n}', "replacement_snippet": '    "a", "b",\n}'}])
        self.assertFalse(ok)
        self.assertTrue(err.startswith(ALREADY_APPLIED))





