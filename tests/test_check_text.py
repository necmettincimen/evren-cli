"""Tests for scripts/check_text.py (control character and secret scanner)."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_text.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_text_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves string annotations through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check_text = _load_module()

# Secret-shaped values are assembled at runtime so this file itself stays clean
# when the scanner runs over the repository.
REAL_KEY = "evren_llm_" + "Kp3Wm9Rt5Yv2Hn7B"
REAL_BEARER = "Bearer " + "Qw3Er5Ty7Ui9Op1As3Df5Gh"
REAL_JWT = "eyJ" + "hbGciOiJIUzI1NiJ9" + "." + "eyJzdWIiOiIxMjM0NTY3" + "." + "SflKwRJSMeKKF2QT4fw"
REAL_PEM = "-----BEGIN " + "RSA PRIVATE KEY-----"
REAL_GHP = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"


class ControlCharTest(unittest.TestCase):

    def test_backspace_is_reported_with_position(self):
        text = "ilk satir\nikinci \x08satir\n"
        self.assertEqual(check_text.find_control_chars(text), [(2, 8, 8)])




    def test_turkish_letters_and_bom_are_fine(self):
        self.assertEqual(check_text.find_control_chars("﻿çğıöşü İĞÖŞÜ"), [])


class SecretTest(unittest.TestCase):
    def kinds(self, text):
        return [kind for _, _, kind, _ in check_text.find_secrets(text)]

    def test_each_pattern_is_detected(self):
        self.assertEqual(self.kinds(f"key = '{REAL_KEY}'"), ["evren_llm anahtarı"])
        self.assertEqual(self.kinds(f"Authorization: {REAL_BEARER}"), ["Bearer belirteci"])
        self.assertEqual(self.kinds(f"jwt={REAL_JWT}"), ["JWT"])
        self.assertEqual(self.kinds(REAL_PEM), ["özel anahtar başlığı"])
        self.assertEqual(self.kinds(f"token: {REAL_GHP}"), ["GitHub belirteci"])


    def test_obvious_fakes_are_allowed(self):
        fakes = [
            "evren_llm_" + "x" * 16,
            "evren_llm_" + "FAKEKEY" + "1234567890",
            "evren_llm_" + "dummyvalue12345",
            "evren_llm_" + "EXAMPLEKEY123456",
            "evren_llm_" + "testkey1234567890",
            "Bearer " + "a" * 5 + "XXX" + "b" * 20,
            "ghp_" + "DuMmY" + "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5",
        ]
        for fake in fakes:
            with self.subTest(fake=fake):
                self.assertEqual(check_text.find_secrets(fake), [])


    def test_ignore_marker_skips_only_that_line(self):
        text = f"a = '{REAL_KEY}'  # check_text: ignore\nb = '{REAL_KEY}'\n"
        hits = check_text.find_secrets(text)
        self.assertEqual([line for line, _, _, _ in hits], [2])






class ScanFileTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_invalid_utf8_is_reported_with_position(self):
        path = self.root / "bozuk.md"
        path.write_bytes(b"satir bir\nsatir \xff iki\n")
        findings = check_text.scan_file(path, display="bozuk.md")
        self.assertEqual(len(findings), 1)
        self.assertEqual((findings[0].line, findings[0].col), (2, 7))
        self.assertEqual(findings[0].kind, "geçersiz UTF-8")






@unittest.skipUnless(shutil.which("git"), "git gerekli")
class TrackedFilesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.git("init", "-q")
        (self.root / "izlenen.py").write_text("a = 1\n", encoding="utf-8")
        (self.root / "yoksayilan.py").write_text("b = 2\n", encoding="utf-8")
        (self.root / ".gitignore").write_text("yoksayilan.py\n", encoding="utf-8")
        self.git("add", "izlenen.py", ".gitignore")
        (self.root / "yeni.py").write_text("c = 3\n", encoding="utf-8")

    def git(self, *args):
        subprocess.run(["git", *args], cwd=str(self.root), check=True, capture_output=True)

    def test_default_lists_only_tracked_files(self):
        self.assertEqual(
            check_text.list_tracked_files(self.root), [".gitignore", "izlenen.py"]
        )




class MainTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def run_main(self, *files):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = check_text.main([str(f) for f in files])
        return code, out.getvalue()


    def test_findings_exit_one_and_report_location(self):
        path = self.root / "kotu.py"
        path.write_text(f"ok = 1\nkey = '{REAL_KEY}'\nx\x08y\n", encoding="utf-8")
        code, out = self.run_main(path)
        self.assertEqual(code, 1)
        self.assertIn("2 bulgu", out)
        self.assertIn(":2:8: olası sır", out)
        self.assertIn(":3:2: kontrol karakteri: U+0008", out)
        self.assertNotIn(REAL_KEY, out)


if __name__ == "__main__":
    unittest.main()
