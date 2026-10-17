"""Tests for the self-update mechanism (src/updater.py)."""

import sys
from unittest.mock import MagicMock, patch
from pathlib import Path

import pytest

from src import updater


@pytest.fixture(autouse=True)
def _isolated_update_log(monkeypatch, tmp_path):
    # Never append test runs (e.g. "--workspace C:/proj") to the real
    # .evren/logs/update.log of the checkout running the suite.
    monkeypatch.setattr(updater, "_log_path", lambda: tmp_path / "update.log")








def test_update_from_git_uses_pull_and_pip(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, cwd=None, timeout=300):
        calls.append(cmd)
        if cmd[:3] == ["git", "rev-parse", "--abbrev-ref"]:
            return 0, "main\n"
        return 0, "Already up to date.\n"

    monkeypatch.setattr(updater, "_run", fake_run)
    ok, msg = updater.update_from_git(tmp_path)
    assert ok is True
    assert any(c[:2] == ["git", "pull"] for c in calls)
    assert any("pip" in c for c in calls)






def test_windows_pip_update_is_deferred_without_running_pip(monkeypatch):
    monkeypatch.setattr(updater.os, "name", "nt")
    monkeypatch.setattr(updater, "is_git_checkout", lambda root=None: False)
    monkeypatch.setattr(updater, "_log", lambda _: None)
    monkeypatch.setattr(updater, "update_from_pip", MagicMock(side_effect=AssertionError))
    restart = MagicMock()
    monkeypatch.setattr(updater, "restart_process", restart)
    with patch.object(updater.subprocess, "Popen") as popen:
        ok, msg = updater.perform_update(restart=True)
    assert ok is True
    assert "kapandıktan sonra" in msg
    restart.assert_not_called()
    args = popen.call_args.args[0]
    assert args[:2] == [sys.executable, "-c"]
    assert args[3] == str(updater.os.getpid())
    assert args[5:] == updater._pip_command()








def test_perform_update_failure_does_not_restart(monkeypatch):
    monkeypatch.setattr(updater, "is_git_checkout", lambda root=None: False)
    monkeypatch.setattr(updater, "update_is_deferred", lambda root=None: False)
    monkeypatch.setattr(updater, "update_from_pip", lambda: (False, "boom"))
    called = {"restart": False}
    monkeypatch.setattr(updater, "restart_process", lambda: called.__setitem__("restart", True))
    ok, msg = updater.perform_update(restart=True)
    assert ok is False
    assert called["restart"] is False




@pytest.mark.parametrize("arguments, expected", [
    (["update"], []),
    (["update", "--no-restart"], []),
    (["--workspace", r"C:\proj", "--model", "test-model", "update"],
     ["--workspace", r"C:\proj", "--model", "test-model"]),
    (["-w", "update", "-m", "update", "update"], ["-w", "update", "-m", "update"]),
    (["--workspace=update", "update"], ["--workspace=update"]),
    (["--workspace", r"C:\proj"], ["--workspace", r"C:\proj"]),
])
def test_restart_does_not_replay_update(monkeypatch, arguments, expected):
    captured = {}
    monkeypatch.setattr(updater.os, "execv", lambda exe, args: captured.update(args=args))
    monkeypatch.setattr(updater, "_log", lambda _: None)
    monkeypatch.setattr(sys, "argv", ["evren", *arguments])
    with patch.object(updater.os, "chdir") as chdir:
        updater.restart_process()
    assert captured["args"] == [sys.executable, "-m", "src.cli", *expected]
    chdir.assert_not_called()


def test_cli_update_runs_once_then_restart_enters_repl(monkeypatch, tmp_path):
    from src import cli

    captured = {}
    monkeypatch.setattr(sys, "argv", ["evren", "--workspace", str(tmp_path), "update"])
    monkeypatch.setattr(updater, "_log", lambda _: None)
    monkeypatch.setattr(updater.os, "execv", lambda exe, args: captured.update(args=args))
    update = MagicMock(return_value=(True, "ok"))
    monkeypatch.setattr(updater, "perform_update", update)
    cli.main()
    assert captured["args"]
    monkeypatch.setattr(sys, "argv", ["evren", *captured["args"][3:]])
    with patch("src.repl.start_repl") as repl:
        cli.main()
    update.assert_called_once_with(restart=False)
    repl.assert_called_once()
    assert repl.call_args.kwargs["workspace_path"] == str(tmp_path)




