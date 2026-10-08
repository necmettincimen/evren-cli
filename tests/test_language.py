import tempfile
import unittest
from pathlib import Path

from src.language import (
    resolve_language,
    get_language,
    set_language,
    language_context_block,
)


class TestLanguage(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_resolve_alias(self):
        self.assertEqual(resolve_language("tr"), "Türkçe")
        self.assertEqual(resolve_language("EN"), "English")

    def test_resolve_unknown_passthrough(self):
        self.assertEqual(resolve_language("Klingon"), "Klingon")

    def test_resolve_empty(self):
        self.assertEqual(resolve_language(""), "")

    def test_set_and_get_language(self):
        canonical = set_language("tr", self.ws)
        self.assertEqual(canonical, "Türkçe")
        self.assertEqual(get_language(self.ws), "Türkçe")

    def test_set_empty_raises(self):
        with self.assertRaises(ValueError):
            set_language("", self.ws)

    def test_context_block_empty_when_unset(self):
        self.assertEqual(language_context_block(self.ws), "")

    def test_context_block_when_set(self):
        set_language("en", self.ws)
        block = language_context_block(self.ws)
        self.assertIn("DİL MODU", block)
        self.assertIn("English", block)


if __name__ == "__main__":
    unittest.main()
