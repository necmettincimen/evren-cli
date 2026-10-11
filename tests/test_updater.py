"""Tests for the self-update mechanism (src/updater.py)."""

import sys
from unittest.mock import MagicMock, patch
from pathlib import Path

import pytest

from src import updater


def test_repo_constants():
    assert updater.REPO_HTTPS == "https://github.com/necmettincimen/evren-cli"
    assert updater.REPO_GIT_URL.endswith(".git")
    assert updater.PIP_TARGET.startswith("git+https://github.com/necmettincimen/evren-cli")


def test_get_project_root_contains_src():
    root = updater.get_project_root()
    assert (root / "src").is_dir()


def test_is_git_checkout_detects_git_dir(tmp_path):
    assert updater.is_git_checkout(tmp_path) is False
    (tmp_path / ".git").mkdir()
    assert updater.is_git_checkout(tmp_path) is True


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


def test_update_from_git_reports_failure(monkeypatch, tmp_path):
    def fake_run(cmd, cwd=None, timeout=300):
        if cmd[:3] == ["git", "rev-parse", "--abbrev-ref"]:
            return 0, "main\n"
        return 1, "conflict"

    monkeypatch.setattr(updater, "_run", fake_run)
    ok, msg = updater.update_from_git(tmp_path)
    assert ok is False
    assert "başarısız" in msg.lower()


def test_update_from_pip_target(monkeypatch):
    captured = {}

    def fake_run(cmd, cwd=None, timeout=300):
        captured["cmd"] = cmd
        return 0, "installed"

    monkeypatch.setattr(updater, "_run", fake_run)
    ok, _ = updater.update_from_pip()
    assert ok is True
    assert updater.PIP_TARGET in captured["cmd"]


def test_perform_update_no_restart(monkeypatch, tmp_path):
    monkeypatch.setattr(updater, "is_git_checkout", lambda root=None: False)
    monkeypatch.setattr(updater, "update_from_pip", lambda: (True, "ok"))
    ok, msg = updater.perform_update(restart=False)
    assert ok is True
    assert msg == "ok"


def test_perform_update_failure_does_not_restart(monkeypatch):
    monkeypatch.setattr(updater, "is_git_checkout", lambda root=None: False)
    monkeypatch.setattr(updater, "update_from_pip", lambda: (False, "boom"))
    called = {"restart": False}
    monkeypatch.setattr(updater, "restart_process", lambda: called.__setitem__("restart", True))
    ok, msg = updater.perform_update(restart=True)
    assert ok is False
    assert called["restart"] is False


def test_restart_process_execv(monkeypatch):
    captured = {}

    def fake_execv(exe, args):
        captured["exe"] = exe
        captured["args"] = args

    monkeypatch.setattr(updater.os, "execv", fake_execv)
    monkeypatch.setattr(updater.os, "chdir", lambda p: None)
    monkeypatch.setattr(sys, "argv", ["evren", "--workspace", "C:/proj"])
    updater.restart_process()
    assert captured["exe"] == sys.executable
    assert captured["args"][:3] == [sys.executable, "-m", "src.cli"]
    assert "--workspace" in captured["args"]


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


def test_no_restart_does_not_exec(monkeypatch):
    from src import cli

    monkeypatch.setattr(sys, "argv", ["evren", "update", "--no-restart"])
    monkeypatch.setattr(updater, "perform_update", lambda **_: (True, "ok"))
    with patch.object(updater, "restart_process") as restart:
        cli.main()
    restart.assert_not_called()
