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



if __name__ == "__main__":
    unittest.main()
