"""Tests for the background command manager (src/background.py).

Uses real short-lived python processes; no mocking of subprocess so the
reader thread, incremental output and tree-kill paths are exercised for real.
"""

import sys
import time
import unittest

from src.background import BackgroundManager, get_manager


def _wait_done(mgr: BackgroundManager, bg_id: str, timeout: float = 20.0):
    """Polls check() until the process finishes; returns (output, status, code)."""
    deadline = time.monotonic() + timeout
    output = ""
    while time.monotonic() < deadline:
        chunk, status, code = mgr.check(bg_id)
        output += chunk
        if code is not None:
            return output, status, code
        time.sleep(0.05)
    raise AssertionError(f"process {bg_id} did not finish within {timeout}s: {status}")


class TestBackgroundManager(unittest.TestCase):
    def setUp(self):
        # Fresh manager per test so ids and records never leak between tests.
        self.mgr = BackgroundManager()

    # ------------------------------------------------------------- start/check
    def test_start_and_check_output_and_exit_code(self):
        rec = self.mgr.start(
            "print", argv=[sys.executable, "-c", "print('satir-1'); print('satir-2')"]
        )
        self.assertEqual(rec.id, "bg-1")
        output, status, code = _wait_done(self.mgr, rec.id)
        self.assertEqual(code, 0)
        self.assertIn("satir-1", output)
        self.assertIn("satir-2", output)
        self.assertIn("bitti", status)
        self.assertIn("çıkış kodu: 0", status)

    def test_check_returns_incremental_output(self):
        # Prints one line, blocks until killed; stdin is closed by the reader
        # setup so the child must not depend on it.
        rec = self.mgr.start(
            "incremental",
            argv=[
                sys.executable,
                "-c",
                "import time\n"
                "print('ilk', flush=True)\n"
                "time.sleep(600)\n"
                "print('ikinci', flush=True)\n",
            ],
        )
        # First read must eventually contain only the first line.
        deadline = time.monotonic() + 20
        first = ""
        while time.monotonic() < deadline and "ilk" not in first:
            first += self.mgr.check(rec.id)[0]
            time.sleep(0.05)
        self.assertIn("ilk", first)
        self.assertNotIn("ikinci", first)

        # Second read right after must not repeat what we already consumed.
        chunk, status, code = self.mgr.check(rec.id)
        self.assertEqual(chunk, "")
        self.assertIsNone(code)
        self.assertIn("çalışıyor", status)

        # After stop, the process is gone and no phantom output appears.
        self.mgr.stop(rec.id)
        rest, _, _ = self.mgr.check(rec.id)
        self.assertNotIn("ilk", rest)

    def test_check_unknown_id_raises_keyerror(self):
        with self.assertRaises(KeyError):
            self.mgr.check("bg-404")

    def test_nonzero_exit_code_reported(self):
        rec = self.mgr.start("fail", argv=[sys.executable, "-c", "import sys; sys.exit(3)"])
        _, status, code = _wait_done(self.mgr, rec.id)
        self.assertEqual(code, 3)
        self.assertIn("çıkış kodu: 3", status)

    def test_stderr_merged_into_output(self):
        rec = self.mgr.start(
            "stderr", argv=[sys.executable, "-c", "import sys; print('hata', file=sys.stderr)"]
        )
        output, _, code = _wait_done(self.mgr, rec.id)
        self.assertEqual(code, 0)
        self.assertIn("hata", output)

    # -------------------------------------------------------------------- stop
    def test_stop_kills_long_running_process(self):
        rec = self.mgr.start(
            "long", argv=[sys.executable, "-c", "import time; time.sleep(600)"]
        )
        self.assertTrue(rec.running())
        self.assertTrue(self.mgr.stop(rec.id))
        self.assertFalse(rec.running())
        self.assertTrue(rec.stopped)
        self.assertIn("durduruldu", rec.status_line())

    def test_stop_unknown_id_returns_false(self):
        self.assertFalse(self.mgr.stop("bg-404"))

    def test_stop_finished_process_is_idempotent(self):
        rec = self.mgr.start("quick", argv=[sys.executable, "-c", "print('ok')"])
        _wait_done(self.mgr, rec.id)
        self.assertTrue(self.mgr.stop(rec.id))
        self.assertFalse(rec.stopped)  # was already finished, not killed

    # ------------------------------------------------------------- cleanup_all
    def test_cleanup_all_kills_running_processes(self):
        r1 = self.mgr.start("s1", argv=[sys.executable, "-c", "import time; time.sleep(600)"])
        r2 = self.mgr.start("s2", argv=[sys.executable, "-c", "import time; time.sleep(600)"])
        r3 = self.mgr.start("done", argv=[sys.executable, "-c", "print('bitti')"])
        _wait_done(self.mgr, r3.id)

        killed = self.mgr.cleanup_all()
        self.assertEqual(killed, 2)
        self.assertFalse(r1.running())
        self.assertFalse(r2.running())

    def test_cleanup_all_with_nothing_running(self):
        self.assertEqual(self.mgr.cleanup_all(), 0)

    # ------------------------------------------------------------------ buffer
    def test_new_output_empty_when_nothing_appended(self):
        rec = self.mgr.start("quiet", argv=[sys.executable, "-c", "print('tek')"])
        _wait_done(self.mgr, rec.id)
        self.assertEqual(rec.new_output(), "")

    def test_list_returns_records(self):
        self.mgr.start("a", argv=[sys.executable, "-c", "print('a')"])
        self.mgr.start("b", argv=[sys.executable, "-c", "print('b')"])
        ids = [r.id for r in self.mgr.list()]
        self.assertEqual(ids, ["bg-1", "bg-2"])


class TestSingleton(unittest.TestCase):
    def test_get_manager_returns_same_instance(self):
        self.assertIs(get_manager(), get_manager())


if __name__ == "__main__":
    unittest.main()


def test_secret_env_keeps_path_and_approval_is_required(monkeypatch, tmp_path):
    """Background jobs keep PATH when secrets are exported and honour approval."""
    import sys
    from src import tools
    from src.background import get_manager
    from src.secret_vault import SecretVault, set_active_vault

    vault = SecretVault()
    vault.capture("token=gizlideger123456")
    set_active_vault(vault)
    try:
        monkeypatch.setattr(tools, "EVREN_AUTO_APPROVE", False)
        monkeypatch.setattr(tools, "prompt_confirm", lambda *a, **k: False)
        refused = tools.execute_tool_call(
            "run_command", {"command": "echo x", "run_in_background": True},
            workspace_root=tmp_path, auto_approve=False,
        )
        assert refused.startswith("İPTAL")

        code = "import os; print('PATH' in os.environ, os.environ.get('EVREN_SECRET_1') == 'gizlideger123456')"
        started = tools.execute_tool_call(
            "run_command", {"command": code, "use_python": True, "run_in_background": True},
            workspace_root=tmp_path, auto_approve=True,
        )
        bg_id = started.split("id: ")[1].split(")")[0]
        record = get_manager().get(bg_id)
        record.proc.wait(timeout=20)
        record._reader.join(timeout=5)
        out, _status, exit_code = get_manager().check(bg_id)
        assert exit_code == 0
        assert "True True" in out
    finally:
        set_active_vault(SecretVault())
        get_manager().cleanup_all()


def test_check_waits_for_completion_and_returns_early(tmp_path):
    import sys
    import time
    from src import tools
    from src.background import get_manager

    started = tools.execute_tool_call(
        "run_command",
        {"command": "import time; time.sleep(1); print('bitti-ok')", "use_python": True, "run_in_background": True},
        workspace_root=tmp_path, auto_approve=True,
    )
    bg_id = started.split("id: ")[1].split(")")[0]
    try:
        t0 = time.monotonic()
        out = tools.execute_tool_call("check_background", {"id": bg_id}, workspace_root=tmp_path)
        elapsed = time.monotonic() - t0
        assert "bitti-ok" in out and "Çıkış Kodu: 0" in out
        assert elapsed < 10  # returned when the job ended, not after the full 20 s
        quick = tools.execute_tool_call("check_background", {"id": bg_id, "wait_seconds": 0}, workspace_root=tmp_path)
        assert "yeni çıktı yok" in quick
    finally:
        get_manager().cleanup_all()
