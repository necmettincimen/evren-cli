import sys
import time
import unittest

from src.proc_utils import run_with_tree_kill, kill_process_tree


class TestProcUtils(unittest.TestCase):
    def test_normal_command(self):
        code, out, err, timed_out = run_with_tree_kill(
            [sys.executable, "-c", "print('hello')"],
            timeout=30,
        )
        self.assertFalse(timed_out)
        self.assertEqual(code, 0)
        self.assertIn("hello", out)

    def test_timeout_kills_process(self):
        # A child that sleeps far longer than the timeout.
        code, out, err, timed_out = run_with_tree_kill(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            timeout=1,
        )
        self.assertTrue(timed_out)
        self.assertEqual(code, -1)

    def test_timeout_kills_grandchild(self):
        # Parent spawns a grandchild that sleeps; both must be killed.
        script = (
            "import subprocess, sys, time;"
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']);"
            "time.sleep(60)"
        )
        start = time.time()
        code, out, err, timed_out = run_with_tree_kill(
            [sys.executable, "-c", script],
            timeout=1,
        )
        elapsed = time.time() - start
        self.assertTrue(timed_out)
        # Should return promptly after the timeout, not wait for the sleepers.
        self.assertLess(elapsed, 20)

    def test_kill_process_tree_invalid_pid(self):
        # Should be a no-op and not raise.
        kill_process_tree(0)
        kill_process_tree(-1)


if __name__ == "__main__":
    unittest.main()
