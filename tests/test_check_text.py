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
    def test_clean_text_has_no_hits(self):
        self.assertEqual(check_text.find_control_chars("merhaba\r\n\tdünya\n"), [])

    def test_backspace_is_reported_with_position(self):
        text = "ilk satir\nikinci \x08satir\n"
        self.assertEqual(check_text.find_control_chars(text), [(2, 8, 8)])

    def test_nul_del_escape_and_form_feed_are_reported(self):
        hits = check_text.find_control_chars("a\x00b\x7fc\x1bd\x0ce")
        self.assertEqual([cp for _, _, cp in hits], [0, 0x7F, 0x1B, 0x0C])

    def test_crlf_does_not_shift_line_numbers(self):
        hits = check_text.find_control_chars("bir\r\niki\r\nuc\x08\r\n")
        self.assertEqual(hits, [(3, 3, 8)])

    def test_c1_controls_are_reported(self):
        self.assertEqual(check_text.find_control_chars("x\x85y")[0][2], 0x85)

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

    def test_short_values_are_not_secrets(self):
        self.assertEqual(check_text.find_secrets("evren_llm_kisa"), [])
        self.assertEqual(check_text.find_secrets("Bearer kisa"), [])
        self.assertEqual(check_text.find_secrets("ghp_kisa"), [])

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

    def test_is_fake_markers(self):
        for value in ("a***b", "xXx", "my-Example", "FAKE", "Dummy", "latest"):
            with self.subTest(value=value):
                self.assertTrue(check_text.is_fake(value))
        self.assertFalse(check_text.is_fake(REAL_KEY))

    def test_ignore_marker_skips_only_that_line(self):
        text = f"a = '{REAL_KEY}'  # check_text: ignore\nb = '{REAL_KEY}'\n"
        hits = check_text.find_secrets(text)
        self.assertEqual([line for line, _, _, _ in hits], [2])

    def test_column_is_one_based_match_start(self):
        line, col, _, value = check_text.find_secrets(f"ab {REAL_KEY}")[0]
        self.assertEqual((line, col, value), (1, 4, REAL_KEY))

    def test_mask_value_never_returns_full_secret(self):
        masked = check_text.mask_value(REAL_KEY)
        self.assertNotIn(REAL_KEY, masked)
        self.assertNotIn(REAL_KEY[6:], masked)
        self.assertEqual(check_text.mask_value("abc"), "***")


class ScanTextTest(unittest.TestCase):
    def test_findings_are_sorted_and_never_hold_the_secret(self):
        text = f"x = '{REAL_KEY}'\nbozuk\x08\n"
        findings = check_text.scan_text("a.py", text)
        self.assertEqual([f.line for f in findings], [1, 2])
        self.assertEqual(findings[1].kind, "kontrol karakteri")
        self.assertEqual(findings[1].detail, "U+0008")
        self.assertEqual(findings[1].format(), "a.py:2:6: kontrol karakteri: U+0008")
        for finding in findings:
            self.assertNotIn(REAL_KEY, finding.format())

    def test_clean_text_returns_empty(self):
        self.assertEqual(check_text.scan_text("a.py", "print('merhaba')\n"), [])


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

    def test_missing_file_is_ignored(self):
        self.assertEqual(check_text.scan_file(self.root / "yok.py"), [])

    def test_oversized_file_is_skipped(self):
        path = self.root / "buyuk.txt"
        path.write_bytes(b"\x08" * 64)
        original = check_text.MAX_FILE_BYTES
        check_text.MAX_FILE_BYTES = 16
        try:
            self.assertEqual(check_text.scan_file(path), [])
        finally:
            check_text.MAX_FILE_BYTES = original

    def test_scan_paths_filters_extensions_and_missing_files(self):
        (self.root / "iyi.py").write_text("x = 1\n", encoding="utf-8")
        (self.root / "kotu.py").write_bytes(b"x = 1\x08\n")
        (self.root / "ikili.bin").write_bytes(b"\x00\x01\x02")
        findings = list(
            check_text.scan_paths(self.root, ["iyi.py", "kotu.py", "ikili.bin", "yok.md"])
        )
        self.assertEqual([f.path for f in findings], ["kotu.py"])

    def test_is_text_file_extensions(self):
        for name in ("a.py", "B.MD", "c.json", "d.toml", "e.yml", "f.yaml", "g.txt", "h.ps1", "i.sh"):
            with self.subTest(name=name):
                self.assertTrue(check_text.is_text_file(name))
        for name in ("a.png", "b.exe", "Makefile", "c.lock"):
            with self.subTest(name=name):
                self.assertFalse(check_text.is_text_file(name))


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

    def test_untracked_option_adds_new_but_not_ignored_files(self):
        self.assertEqual(
            check_text.list_tracked_files(self.root, include_untracked=True),
            [".gitignore", "izlenen.py", "yeni.py"],
        )

    def test_not_a_git_repo_raises(self):
        with tempfile.TemporaryDirectory() as other:
            # Stop git from discovering a repository above the temp directory.
            ceiling = {"GIT_CEILING_DIRECTORIES": str(Path(other).parent)}
            with mock.patch.dict(os.environ, ceiling):
                with self.assertRaises(RuntimeError):
                    check_text.list_tracked_files(Path(other))


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

    def test_clean_file_exits_zero(self):
        path = self.root / "temiz.py"
        path.write_text("print('merhaba')\n", encoding="utf-8")
        code, out = self.run_main(path)
        self.assertEqual(code, 0)
        self.assertIn("check_text: temiz", out)

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
