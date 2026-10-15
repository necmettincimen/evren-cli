"""Tests for the session-start git awareness block (config.git_context_block)."""

import subprocess
from unittest import mock

import pytest

from src.config import git_context_block


def _have_git() -> bool:
    try:
        subprocess.run(
            ["git", "--version"], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return True


needs_git = pytest.mark.skipif(not _have_git(), reason="git not installed")


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    )


@pytest.fixture()
def git_repo(tmp_path):
    """A throwaway git repo with one commit, a modified file and an untracked file."""
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "a.txt").write_text("v1\n", encoding="utf-8")
    _git(tmp_path, "add", "a.txt")
    _git(tmp_path, "commit", "-m", "initial commit")
    (tmp_path / "a.txt").write_text("v2\n", encoding="utf-8")
    (tmp_path / "new.txt").write_text("x\n", encoding="utf-8")
    return tmp_path


@needs_git
def test_git_block_reports_branch_commit_and_status(git_repo):
    block = git_context_block(git_repo)
    assert block
    assert "GIT DURUMU" in block
    assert "Dal: main" in block
    assert "Ana dal tahmini: main" in block
    assert "initial commit" in block
    # one modified tracked file + one untracked file
    assert "Değişen dosya: 1" in block
    assert "İzlenmeyen dosya: 1" in block


@needs_git
def test_git_block_empty_outside_repo(tmp_path):
    assert git_context_block(tmp_path) == ""


def test_git_block_empty_when_git_missing():
    with mock.patch("src.config._git_run", return_value=None):
        assert git_context_block(".") == ""


def test_git_block_passes_two_second_timeout(tmp_path):
    calls = []

    def fake_run(cmd, cwd=None, capture_output=None, text=None, timeout=None, **kw):
        calls.append((cmd, timeout))
        proc = mock.Mock()
        proc.returncode = 0
        proc.stdout = "true" if cmd[1:3] == ["rev-parse", "--is-inside-work-tree"] else ""
        return proc

    with mock.patch("subprocess.run", side_effect=fake_run):
        git_context_block(tmp_path)

    assert calls, "no git command was executed"
    assert all(timeout == 2.0 for _, timeout in calls)


def test_git_block_swallows_timeout():
    with mock.patch(
        "subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="git", timeout=2)
    ):
        assert git_context_block(".") == ""


def test_git_summary_is_read_once_per_session(tmp_path, monkeypatch):
    """apply_frame / refresh_context must not re-run git (system prompt cache)."""
    from unittest.mock import MagicMock

    import src.agent as agent_mod
    from src.prompt_frame import PromptFrame

    calls = []

    def fake_block(root):
        calls.append(root)
        return f"GIT DURUMU (#{len(calls)})"

    monkeypatch.setenv("EVREN_AUTO_DOCS", "false")
    monkeypatch.setattr(agent_mod, "git_context_block", fake_block)
    session = agent_mod.AgentSession(MagicMock(default_model="m"), workspace_root=tmp_path)
    first = session.system_prompt
    session.apply_frame(PromptFrame(brief="görev 1"))
    session.apply_frame(PromptFrame(brief="görev 2"))
    session.refresh_context()
    assert len(calls) == 1
    assert "GIT DURUMU (#1)" in session.system_prompt
    assert session.system_prompt == first
    session.clear_history()
    assert len(calls) == 2
