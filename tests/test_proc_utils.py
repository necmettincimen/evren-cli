import sys
import time
import unittest

from src.proc_utils import (
    run_with_tree_kill,
    kill_process_tree,
    stream_with_tree_kill,
)


def _drain(gen):
    """Consumes a stream_with_tree_kill generator, returning (lines, return_value)."""
    lines = []
    try:
        while True:
            lines.append(next(gen))
    except StopIteration as stop:
        return lines, stop.value


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


class TestStreamWithTreeKill(unittest.TestCase):
    def test_streams_stdout_and_stderr_live(self):
        script = (
            "import sys\n"
            "print('out1', flush=True)\n"
            "print('err1', file=sys.stderr, flush=True)\n"
            "print('out2', flush=True)\n"
        )
        lines, (code, out, err, timed_out) = _drain(
            stream_with_tree_kill([sys.executable, "-c", script], timeout=30)
        )
        self.assertFalse(timed_out)
        self.assertEqual(code, 0)
        # Lines are yielded with their stream tag.
        self.assertIn(("stdout", "out1\n"), lines)
        self.assertIn(("stderr", "err1\n"), lines)
        self.assertIn(("stdout", "out2\n"), lines)
        # Full output is still captured in the return value.
        self.assertIn("out1", out)
        self.assertIn("out2", out)
        self.assertIn("err1", err)

    def test_stream_timeout_kills_tree(self):
        lines, (code, out, err, timed_out) = _drain(
            stream_with_tree_kill(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                timeout=1,
            )
        )
        self.assertTrue(timed_out)
        self.assertEqual(code, -1)


if __name__ == "__main__":
    unittest.main()
