import unittest

from src.templates import (
    TEMPLATES,
    list_templates,
    get_template,
    audiences,
)


class TestTemplates(unittest.TestCase):


    def test_list_by_audience(self):
        items = list_templates("team")
        self.assertTrue(items)
        self.assertTrue(all(t.audience == "team" for t in items))



    def test_get_case_insensitive(self):
        self.assertIsNotNone(get_template("ADR"))

    def test_get_missing(self):
        self.assertIsNone(get_template("nope"))





if __name__ == "__main__":
    unittest.main()
