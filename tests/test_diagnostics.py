import unittest

from src.diagnostics import (
    DIAGNOSTICS,
    list_diagnostics,
    get_diagnostic,
    match_by_symptom,
)


class TestDiagnostics(unittest.TestCase):


    def test_get_exact(self):
        d = get_diagnostic("build-failure")
        self.assertIsNotNone(d)
        self.assertEqual(d.name, "build-failure")


    def test_get_missing(self):
        self.assertIsNone(get_diagnostic("nope"))



    def test_match_by_symptom(self):
        hits = match_by_symptom("Build kırıldı")
        names = [d.name for d in hits]
        self.assertIn("build-failure", names)




if __name__ == "__main__":
    unittest.main()
