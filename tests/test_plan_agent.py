import subprocess
from pathlib import Path

import pytest

from src import plan_agent


def _git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


def _resolve(root, rel):
    return (root / rel).resolve()


@pytest.fixture
def ws(tmp_path):
    root = tmp_path.resolve()
    repo = root / "app"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "a.txt").write_text("1")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return root, repo


def _fake(monkeypatch, action, text="bitti"):
    def run_once(cmd, logpath, label, timeout):
        wd = Path(cmd[cmd.index("-w") + 1])
        action(wd)
        logpath.parent.mkdir(parents=True, exist_ok=True)
        logpath.write_text(text, encoding="utf-8")
        return 0, text
    monkeypatch.setattr(plan_agent, "_run_once", run_once)


def test_asks_question():
    assert plan_agent.asks_question("Rapor hazır.\nHangisiyle devam edeyim?")
    assert plan_agent.asks_question("kalan 3 iş için onayın var mı?")
    assert not plan_agent.asks_question("Neden? Çünkü X.\nTamamlandı: commit abc123.")
    assert not plan_agent.asks_question("")


def test_worktree_merges_and_cleans(ws, monkeypatch):
    root, repo = ws
    def act(wd):
        (wd / "b.txt").write_text("2")
        _git(wd, "add", "b.txt")
        _git(wd, "commit", "-q", "-m", "yeni")
    _fake(monkeypatch, act)
    out = plan_agent.op_agent({"prompt": f"{repo} içinde çalış", "repo": "app", "worktree": True, "commit": True,
                               "_label": "t-1"}, root, False, _resolve)
    assert "ana ağaca alındı" in out and "reports/agent-logs/t-1-a1.log" in out
    assert (repo / "b.txt").read_text() == "2"
    assert not (root / ".evren" / "worktrees" / "t-1").exists()


def test_worktree_dirty_is_committed_by_evren(ws, monkeypatch):
    root, repo = ws
    _fake(monkeypatch, lambda wd: (wd / "c.txt").write_text("3"))
    plan_agent.op_agent({"prompt": "x", "repo": "app", "worktree": True, "commit": True, "_label": "t-2",
                         "commit_message": "evren commit"}, root, False, _resolve)
    log = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%s"], capture_output=True, text=True).stdout.strip()
    assert log == "evren commit" and (repo / "c.txt").exists()


def test_question_without_work_fails_after_retry(ws, monkeypatch):
    root, repo = ws
    _fake(monkeypatch, lambda wd: None, text="Devam edeyim mi?")
    with pytest.raises(RuntimeError, match="sözleşmeyi karşılamadı"):
        plan_agent.op_agent({"prompt": "x", "repo": "app", "_label": "t-3"}, root, False, _resolve)
    assert (root / "reports" / "agent-logs" / "t-3-a2.log").exists()


def test_report_contract(ws, monkeypatch):
    root, repo = ws
    def act(wd):
        (wd / "reports").mkdir(exist_ok=True)
        (wd / "reports" / "r.md").write_text("sonuç")
    _fake(monkeypatch, act)
    out = plan_agent.op_agent({"prompt": "x", "repo": ".", "require_changes": False, "report": "reports/r.md",
                               "_label": "t-4"}, root, False, _resolve)
    assert "rapor: reports/r.md" in out
