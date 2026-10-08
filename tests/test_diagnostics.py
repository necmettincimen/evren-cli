import unittest

from src.diagnostics import (
    DIAGNOSTICS,
    list_diagnostics,
    get_diagnostic,
    match_by_symptom,
)


class TestDiagnostics(unittest.TestCase):
    def test_catalog_not_empty(self):
        self.assertGreater(len(DIAGNOSTICS), 0)

    def test_list_all(self):
        self.assertEqual(len(list_diagnostics()), len(DIAGNOSTICS))

    def test_get_exact(self):
        d = get_diagnostic("build-failure")
        self.assertIsNotNone(d)
        self.assertEqual(d.name, "build-failure")

    def test_get_case_insensitive(self):
        self.assertIsNotNone(get_diagnostic("BUILD-FAILURE"))

    def test_get_missing(self):
        self.assertIsNone(get_diagnostic("nope"))

    def test_get_empty(self):
        self.assertIsNone(get_diagnostic(""))

    def test_prompt_block(self):
        d = get_diagnostic("flaky-tests")
        block = d.prompt_block("bağlam")
        self.assertIn("TRIYAJ", block)
        self.assertIn("bağlam", block)
        self.assertIn("Triyaj soruları", block)

    def test_match_by_symptom(self):
        hits = match_by_symptom("Build kırıldı")
        names = [d.name for d in hits]
        self.assertIn("build-failure", names)

    def test_match_by_symptom_empty(self):
        self.assertEqual(match_by_symptom(""), [])

    def test_match_by_symptom_no_hit(self):
        self.assertEqual(match_by_symptom("zzz bilinmeyen"), [])


if __name__ == "__main__":
    unittest.main()
