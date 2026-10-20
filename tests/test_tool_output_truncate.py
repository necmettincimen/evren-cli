"""Komut çıktısı kırpma: 200 KB'lık gövde bağlama gömülmemeli."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.ssh_ops import BIG_OUTPUT_CHARS, spill_long_output


class BigOutputTest(unittest.TestCase):
    def test_huge_blob_is_not_embedded(self):
        text = "x" * 200_000
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch("src.ui.print_command_output"):
                result = spill_long_output(text, root, "cmd", shown_live=True)
            saved = list((root / ".evren" / "tmp").glob("cmd-*.txt"))
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0].read_text(encoding="utf-8"), text)
        self.assertNotIn("x" * 100, result)
        self.assertLess(len(result), 1000)
        self.assertIn("200000 karakter", result)
        self.assertIn("1 satır", result)

    def test_medium_output_keeps_head_and_tail(self):
        text = "\n".join(f"satır {i}" for i in range(2000))
        self.assertGreater(len(text), 8000)
        self.assertLess(len(text), BIG_OUTPUT_CHARS)
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch("src.ui.print_command_output"):
                result = spill_long_output(text, Path(directory), "cmd", shown_live=True)
        self.assertIn("satır 0", result)
        self.assertIn("satır 1999", result)
        self.assertIn("karakter kırpıldı", result)

    def test_short_output_untouched(self):
        self.assertEqual(spill_long_output("kısa çıktı", None, "cmd"), "kısa çıktı")


if __name__ == "__main__":
    unittest.main()
