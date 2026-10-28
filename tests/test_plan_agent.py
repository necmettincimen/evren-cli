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


def test_push_hook_called_after_merge(ws, monkeypatch):
    root, repo = ws
    calls = []
    monkeypatch.setattr(plan_agent, "PUSH_HOOK", lambda s: calls.append(s["repo"]) or "ok")
    _fake(monkeypatch, lambda wd: (wd / "d.txt").write_text("4"))
    out = plan_agent.op_agent({"prompt": "x", "repo": "app", "worktree": True, "commit": True, "push": True,
                               "_label": "t-5"}, root, False, _resolve)
    assert calls == ["app"] and "[push: ok]" in out


def test_no_push_when_nothing_merged(ws, monkeypatch):
    root, repo = ws
    calls = []
    monkeypatch.setattr(plan_agent, "PUSH_HOOK", lambda s: calls.append(1) or "ok")
    _fake(monkeypatch, lambda wd: None)
    with pytest.raises(RuntimeError):
        plan_agent.op_agent({"prompt": "x", "repo": "app", "worktree": True, "push": True, "_label": "t-6"}, root, False, _resolve)
    assert calls == []


def test_contract_options_require_repo(ws, monkeypatch):
    root, repo = ws
    _fake(monkeypatch, lambda wd: None)
    with pytest.raises(ValueError, match="repo"):
        plan_agent.op_agent({"prompt": "x", "worktree": True, "commit": True, "_label": "t-7"}, root, False, _resolve)


def test_main_tree_contamination_is_quarantined(ws, monkeypatch):
    root, repo = ws
    def act(wd):
        (repo / "kirli.txt").write_text("ana agaca yazildi")   # jail dışı yazım
        (wd / "e.txt").write_text("5")
    _fake(monkeypatch, act)
    out = plan_agent.op_agent({"prompt": "x", "repo": "app", "worktree": True, "commit": True, "_label": "t-8"}, root, False, _resolve)
    assert "stash'e alındı" in out and not (repo / "kirli.txt").exists() and (repo / "e.txt").exists()
    stash = subprocess.run(["git", "-C", str(repo), "stash", "list"], capture_output=True, text=True).stdout
    assert "evren-ana-agac-kirlendi" in stash


def test_msys_path_rewritten_in_prompt(ws, monkeypatch):
    root, repo = ws
    seen = {}
    def run_once(cmd, logpath, label, timeout):
        seen["p"] = cmd[cmd.index("-p") + 1]
        wd = Path(cmd[cmd.index("-w") + 1]); (wd / "f.txt").write_text("6")
        logpath.parent.mkdir(parents=True, exist_ok=True); logpath.write_text("ok"); return 0, "ok"
    monkeypatch.setattr(plan_agent, "_run_once", run_once)
    msys = "/" + str(repo)[0].lower() + str(repo)[2:].replace("\\", "/")
    plan_agent.op_agent({"prompt": f"cd {msys} && build", "repo": "app", "worktree": True, "_label": "t-9"}, root, False, _resolve)
    assert ".evren/worktrees/t-9" in seen["p"].split("ANA AĞAÇ")[0].replace("\\", "/")


def test_workdir_scopes_agent_and_moves_report(ws, monkeypatch):
    root, repo = ws
    seen = {}
    def run_once(cmd, logpath, label, timeout):
        seen["w"] = Path(cmd[cmd.index("-w") + 1]); seen["p"] = cmd[cmd.index("-p") + 1]
        (seen["w"] / ".evren-rapor.md").write_text("teshis")
        logpath.parent.mkdir(parents=True, exist_ok=True); logpath.write_text("ok"); return 0, "ok"
    monkeypatch.setattr(plan_agent, "_run_once", run_once)
    out = plan_agent.op_agent({"prompt": "x", "repo": ".", "workdir": "app", "worktree": False, "require_changes": False,
                               "report": "reports/t.md", "_label": "t-10"}, root, False, _resolve)
    assert seen["w"] == repo.resolve() and (root / "reports" / "t.md").read_text() == "teshis" and "rapor: reports/t.md" in out
    assert "HIZ KURALLARI" in seen["p"]


def test_resume_keeps_previous_worktree_work(ws, monkeypatch):
    root, repo = ws
    # 1. koşu: ajan dosya yazar ama süreç "öldürülür" (hata) → worktree ve dal kalır
    calls = {"n": 0}
    def run_once(cmd, logpath, label, timeout):
        wd = Path(cmd[cmd.index("-w") + 1])
        calls["n"] += 1
        logpath.parent.mkdir(parents=True, exist_ok=True); logpath.write_text("ok")
        if calls["n"] == 1:
            (wd / "yarim.txt").write_text("onceki is")
            raise RuntimeError("öldürüldü")
        assert (wd / "yarim.txt").exists()        # devam: önceki iş duruyor
        assert "[DEVAM]" in cmd[cmd.index("-p") + 1]
        (wd / "tamam.txt").write_text("son")
        return 0, "ok"
    monkeypatch.setattr(plan_agent, "_run_once", run_once)
    step = {"prompt": "x", "repo": "app", "worktree": True, "commit": True, "_label": "t-11"}
    with pytest.raises(RuntimeError):
        plan_agent.op_agent(dict(step), root, False, _resolve)
    out = plan_agent.op_agent(dict(step), root, False, _resolve)
    assert "ana ağaca alındı" in out
    assert (repo / "yarim.txt").exists() and (repo / "tamam.txt").exists()


def test_find_bash_prefers_git_bash_on_windows():
    import os
    from src import plan_runner
    found = plan_runner._find_bash()
    if os.name == "nt":
        assert found is None or "system32" not in found.lower()
    else:
        assert found is not None


def _report_fake(monkeypatch, outcome_line, change=False, wt=True):
    def run_once(cmd, logpath, label, timeout):
        wd = Path(cmd[cmd.index("-w") + 1])
        if change:
            (wd / "z.txt").write_text("1")
        if "-p" in cmd and "[RAPOR]" in cmd[cmd.index("-p") + 1]:
            (wd / ".evren-rapor.md").write_text(outcome_line + "\n\nGerekçe...", encoding="utf-8")
        logpath.parent.mkdir(parents=True, exist_ok=True); logpath.write_text("ok"); return 0, "ok"
    monkeypatch.setattr(plan_agent, "_run_once", run_once)


@pytest.mark.parametrize("line,key", [
    ("SONUÇ: ZATEN ÇÖZÜLMÜŞ (commit abc)", "done"),
    ("**SONUÇ:** FRONTEND", "front"),
    ("Sonuç: Kütüphane değişikliği gerekli", "lib"),
    ("# SONUÇ: Belirsiz", "unk"),
    ("sonuç: düzeltildi", "fixed"),
])
def test_outcome_parser(tmp_path, line, key):
    f = tmp_path / "r.md"
    f.write_text(line + "\nx", encoding="utf-8")
    assert plan_agent.report_outcome(f) == key


def test_no_change_outcome_passes_contract(ws, monkeypatch):
    root, repo = ws
    _report_fake(monkeypatch, "SONUÇ: FRONTEND")
    out = plan_agent.op_agent({"prompt": "x", "repo": "app", "worktree": True, "commit": True, "report": "reports/r.md",
                               "_label": "t-12"}, root, False, _resolve)
    assert "[sonuç: front]" in out and (root / "reports" / "r.md").exists()
    assert "ana ağaca alındı" not in out


def test_fixed_outcome_without_change_fails(ws, monkeypatch):
    root, repo = ws
    _report_fake(monkeypatch, "SONUÇ: DÜZELTİLDİ")
    with pytest.raises(RuntimeError, match="değişiklik yok|commit yok"):
        plan_agent.op_agent({"prompt": "x", "repo": "app", "worktree": True, "commit": True, "report": "reports/r2.md",
                             "_label": "t-13"}, root, False, _resolve)


def test_missing_outcome_line_is_retried_then_fails(ws, monkeypatch):
    root, repo = ws
    _report_fake(monkeypatch, "Başlık yok")
    with pytest.raises(RuntimeError, match="sonuç satırı"):
        plan_agent.op_agent({"prompt": "x", "repo": "app", "worktree": True, "commit": True, "report": "reports/r3.md",
                             "_label": "t-14"}, root, False, _resolve)


def test_missing_outcome_line_with_commit_is_recovered_as_partial(ws, monkeypatch):
    root, repo = ws
    _report_fake(monkeypatch, "Başlık yok", change=True)
    out = plan_agent.op_agent({"prompt": "x", "repo": "app", "worktree": True, "commit": True, "report": "reports/r3b.md",
                               "_label": "t-14b"}, root, False, _resolve)
    assert "[sonuç: partial]" in out and (repo / "z.txt").exists()
    assert (root / "reports/r3b.md").read_text(encoding="utf-8").startswith("SONUÇ: KISMİ")


def test_step_limit_continues_with_larger_budget(ws, monkeypatch):
    root, repo = ws
    seen = []
    def run_once(cmd, logpath, label, timeout):
        wd = Path(cmd[cmd.index("-w") + 1])
        seen.append(int(cmd[cmd.index("--max-steps") + 1]))
        logpath.parent.mkdir(parents=True, exist_ok=True)
        if len(seen) == 1:
            (wd / "yarim.txt").write_text("1")
            logpath.write_text("HATA: Ajan maksimum adım sınırına (60) ulaştı"); return 1, "HATA: Ajan maksimum adım sınırına (60) ulaştı"
        assert "[UYARI] Önceki deneme adım sınırına" in cmd[cmd.index("-p") + 1]
        (wd / ".evren-rapor.md").write_text(
            "SONUÇ: DÜZELTİLDİ\n\n## VERİ YOLU\n- a.cs:1 uç\n- b.cs:2 servis\n- c.cs:3 depo\n", encoding="utf-8")
        (wd / "tamam.txt").write_text("2"); logpath.write_text("ok"); return 0, "ok"
    monkeypatch.setattr(plan_agent, "_run_once", run_once)
    out = plan_agent.op_agent({"prompt": "x", "repo": "app", "worktree": True, "commit": True, "report": "reports/r4.md",
                               "max_steps": 60, "_label": "t-15"}, root, False, _resolve)
    assert seen == [60, 90] and "[sonuç: fixed]" in out
    assert (repo / "yarim.txt").exists() and (repo / "tamam.txt").exists()


def _py(code):
    import sys
    return f'"{sys.executable}" -c "{code}"'


def test_verify_gate_blocks_broken_work(ws, monkeypatch):
    root, repo = ws
    _fake(monkeypatch, lambda wd: (wd / "bozuk.txt").write_text("x"))
    with pytest.raises(RuntimeError, match="verify"):
        plan_agent.op_agent({"prompt": "x", "repo": "app", "worktree": True, "commit": True, "_label": "t-16",
                             "verify": _py("import sys; sys.exit(1)")}, root, False, _resolve)
    assert not (repo / "bozuk.txt").exists()          # ana ağaca girmedi


def test_verify_gate_passes_good_work(ws, monkeypatch):
    root, repo = ws
    _fake(monkeypatch, lambda wd: (wd / "iyi.txt").write_text("x"))
    out = plan_agent.op_agent({"prompt": "x", "repo": "app", "worktree": True, "commit": True, "_label": "t-17",
                               "verify": _py("import sys; sys.exit(0)")}, root, False, _resolve)
    assert "ana ağaca alındı" in out and (repo / "iyi.txt").exists()


def test_no_change_retry_carries_previous_findings(ws, monkeypatch):
    # 2026-10-23: the second attempt re-ran the same searches because it only got
    # "previous attempt changed nothing"; the first had already named the fix.
    root, repo = ws
    prompts = []
    def run_once(cmd, logpath, label, timeout):
        wd = Path(cmd[cmd.index("-w") + 1])
        prompts.append(cmd[cmd.index("-p") + 1])
        logpath.parent.mkdir(parents=True, exist_ok=True)
        if len(prompts) == 1:
            text = ("arama çıktısı\n" * 50 + "**Yapılan:** kök neden Validator.cs:60.\n"
                    "**Tek somut sonraki adım:** Validator.cs:60-75 düzenle.")
            logpath.write_text(text, encoding="utf-8"); return 0, text
        (wd / "b.txt").write_text("2")
        logpath.write_text("ok"); return 0, "ok"
    monkeypatch.setattr(plan_agent, "_run_once", run_once)
    plan_agent.op_agent({"prompt": "x", "repo": "app", "worktree": True, "commit": True, "_label": "t-f"},
                        root, False, _resolve)
    assert "ÖNCEKİ DENEMENİN BULGULARI" in prompts[1]
    assert "Validator.cs:60-75 düzenle" in prompts[1]
    assert "arama çıktısı" not in prompts[1]


def test_planner_false_and_env_reach_agent_process(ws, monkeypatch):
    root, repo = ws
    seen = {}
    def run_once(cmd, logpath, label, timeout, env=None):
        seen["env"] = env
        (Path(cmd[cmd.index("-w") + 1]) / "c.txt").write_text("3")
        logpath.parent.mkdir(parents=True, exist_ok=True)
        logpath.write_text("ok"); return 0, "ok"
    monkeypatch.setattr(plan_agent, "_run_once", run_once)
    plan_agent.op_agent({"prompt": "x", "repo": "app", "worktree": True, "commit": True, "planner": False,
                         "env": {"EVREN_X": 1}, "_label": "t-e"}, root, False, _resolve)
    assert seen["env"] == {"EVREN_X": "1", "EVREN_AUTO_ROUTE": "0"}


def test_previous_findings_without_headings_uses_tail():
    out = plan_agent._previous_findings("a" * 10000 + "SON")
    assert out.endswith("SON") and len(out) < 4000
    assert plan_agent._previous_findings("") == ""
