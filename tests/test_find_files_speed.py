import subprocess
from pathlib import Path

import pytest

from src import tools


def _mk(p: Path, text="class A {}"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture
def tree(tmp_path):
    root = tmp_path.resolve()
    _mk(root / "app" / "src" / "Real.cs", "class NeedleReal {}")
    _mk(root / "app" / "bin" / "Debug" / "Gen.cs", "class NeedleBin {}")
    _mk(root / "app" / "obj" / "Gen2.cs", "class NeedleObj {}")
    _mk(root / "web" / "node_modules" / "lib" / "x.js", "NeedleNode")
    _mk(root / "web" / "src" / "a.js", "NeedleWeb")
    return root


def test_junk_dirs_are_pruned_not_walked(tree, monkeypatch):
    # rglob'a hiç dokunulmamalı: budanmış yürüyüş
    monkeypatch.setattr(Path, "rglob", lambda *a, **k: (_ for _ in ()).throw(AssertionError("rglob kullanıldı")))
    out = tools.tool_find_files("*.cs", "Needle", workspace_root=tree)
    assert "NeedleReal" in out and "NeedleBin" not in out and "NeedleObj" not in out
    out2 = tools.tool_find_files("*.js", "Needle", workspace_root=tree)
    assert "NeedleWeb" in out2 and "NeedleNode" not in out2


def test_gitignored_files_skipped_in_nested_repo(tree):
    repo = tree / "svc"
    _mk(repo / "keep.cs", "class NeedleKeep {}")
    _mk(repo / "secret" / "ignored.cs", "class NeedleIgnored {}")
    (repo / ".gitignore").write_text("secret/\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True, capture_output=True)
    out = tools.tool_find_files("*.cs", "Needle", workspace_root=tree)
    assert "NeedleKeep" in out and "NeedleIgnored" not in out


def test_path_glob_pattern(tree):
    out = tools.tool_find_files("src/*.cs", "", workspace_root=tree)
    assert "app/src/Real.cs" in out and "Gen.cs" not in out


def test_speed_grep_adds_excludes_only_to_recursive():
    f = tools._speed_grep
    assert "--exclude-dir=node_modules" in f("grep -rn foo /d/x --include=*.cs")
    assert "--exclude-dir=bin" in f("cd a && grep -rl 'x' . | head")
    assert f("grep -n foo file.txt") == "grep -n foo file.txt"
    assert f("grep -rn x . --exclude-dir=foo") == "grep -rn x . --exclude-dir=foo"
    assert f("git grep -n x") == "git grep -n x"
    assert f("ls | grep -v obj").count("exclude-dir") == 0
