import unittest
import tempfile
import shutil
from pathlib import Path

from src.file_ops import (
    is_safe_path,
    safe_write_file,
    safe_read_file,
    read_file_with_meta,
    backup_file,
    rollback_last_backup,
)


class TestFileOps(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = Path(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_safe_path_boundary(self):
        inside_file = self.workspace / "src" / "main.py"
        safe, _ = is_safe_path(inside_file, self.workspace)
        self.assertTrue(safe)

        outside_file = self.workspace.parent / "secret.txt"
        safe_out, reason = is_safe_path(outside_file, self.workspace)
        self.assertFalse(safe_out)
        self.assertIn("çalışma dizini", reason)

    def test_forbidden_directories(self):
        git_file = self.workspace / ".git" / "config"
        safe, reason = is_safe_path(git_file, self.workspace)
        self.assertFalse(safe)
        self.assertIn("engellendi", reason)

    def test_safe_write_and_read(self):
        target = self.workspace / "test.txt"
        ok, backup, err = safe_write_file(target, "Merhaba Dunya", self.workspace)
        self.assertTrue(ok)
        self.assertIsNone(backup)

        content, read_err = safe_read_file(target, self.workspace)
        self.assertEqual(read_err, "")
        self.assertEqual(content, "Merhaba Dunya")

    def test_backup_and_rollback(self):
        target = self.workspace / "test.txt"
        safe_write_file(target, "Version 1", self.workspace)

        # Overwrite triggers backup
        ok, backup, _ = safe_write_file(target, "Version 2", self.workspace, create_backup=True)
        self.assertTrue(ok)
        self.assertIsNotNone(backup)
        self.assertTrue(backup.exists())

        # Verify content is now Version 2
        content, _ = safe_read_file(target, self.workspace)
        self.assertEqual(content, "Version 2")

        # Rollback
        roll_ok, msg = rollback_last_backup(target, self.workspace)
        self.assertTrue(roll_ok)

        # Verify content is restored to Version 1
        content, _ = safe_read_file(target, self.workspace)
        self.assertEqual(content, "Version 1")

    def test_read_meta_crlf(self):
        target = self.workspace / "crlf.txt"
        target.write_bytes(b"line1\r\nline2\r\n")
        content, meta, err = read_file_with_meta(target, self.workspace)
        self.assertEqual(err, "")
        self.assertEqual(meta["newline"], "\r\n")
        self.assertFalse(meta["has_bom"])

    def test_read_meta_bom(self):
        target = self.workspace / "bom.txt"
        target.write_bytes(b"\xef\xbb\xbfhello")
        content, meta, err = read_file_with_meta(target, self.workspace)
        self.assertEqual(err, "")
        self.assertTrue(meta["has_bom"])
        self.assertEqual(content, "hello")

    def test_write_preserves_crlf(self):
        target = self.workspace / "crlf.txt"
        target.write_bytes(b"a\r\nb\r\n")
        _, meta, _ = read_file_with_meta(target, self.workspace)
        safe_write_file(target, "a\r\nc\r\n", self.workspace, preserve_meta=meta)
        raw = target.read_bytes()
        self.assertIn(b"\r\n", raw)
        self.assertNotIn(b"\r\r\n", raw)

    def test_write_preserves_bom(self):
        target = self.workspace / "bom.txt"
        target.write_bytes(b"\xef\xbb\xbfhello")
        _, meta, _ = read_file_with_meta(target, self.workspace)
        safe_write_file(target, "world", self.workspace, preserve_meta=meta)
        raw = target.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))


if __name__ == "__main__":
    unittest.main()
