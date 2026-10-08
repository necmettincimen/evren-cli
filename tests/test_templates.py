import unittest

from src.templates import (
    TEMPLATES,
    list_templates,
    get_template,
    audiences,
)


class TestTemplates(unittest.TestCase):
    def test_catalog_not_empty(self):
        self.assertGreater(len(TEMPLATES), 0)

    def test_list_all(self):
        self.assertEqual(len(list_templates()), len(TEMPLATES))

    def test_list_by_audience(self):
        items = list_templates("team")
        self.assertTrue(items)
        self.assertTrue(all(t.audience == "team" for t in items))

    def test_list_unknown_audience(self):
        self.assertEqual(list_templates("nope"), [])

    def test_get_exact(self):
        t = get_template("adr")
        self.assertIsNotNone(t)
        self.assertEqual(t.name, "adr")

    def test_get_case_insensitive(self):
        self.assertIsNotNone(get_template("ADR"))

    def test_get_missing(self):
        self.assertIsNone(get_template("nope"))

    def test_get_empty(self):
        self.assertIsNone(get_template(""))

    def test_prompt_block_contains_body(self):
        t = get_template("incident-postmortem")
        block = t.prompt_block("bağlam")
        self.assertIn("ÇIKTI ŞABLONU", block)
        self.assertIn("ŞABLON", block)
        self.assertIn("bağlam", block)

    def test_audiences_sorted_unique(self):
        auds = audiences()
        self.assertEqual(auds, sorted(set(auds)))


if __name__ == "__main__":
    unittest.main()
