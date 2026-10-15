"""End-to-end REPL startup smoke test: start_repl must open and exit cleanly.

A UI refactor renamed a startup variable and left one stale reference; the
REPL then died with NameError on every start while all unit tests passed.
"""

import builtins
from unittest.mock import MagicMock, patch


def test_repl_starts_runs_slash_commands_and_exits(tmp_path, monkeypatch, capsys):
    from src import repl

    monkeypatch.setenv("EVREN_AUTO_DOCS", "false")
    fake_client = MagicMock()
    fake_client.default_model = "test-model"
    fake_client.api_key = "k"
    fake_client.key_pool = []
    fake_client.get_quota.return_value = {"remaining": 10, "limit": 100}
    inputs = iter(["/help", "/todos", "/bg", "/skills", "/exit"])

    with patch.object(repl, "EvrenClient", return_value=fake_client), \
            patch.object(repl, "ensure_terms_accepted", return_value=True), \
            patch.object(repl, "HAVE_PT", False), \
            patch.object(builtins, "input", lambda *a, **k: next(inputs)):
        repl.start_repl(workspace_path=str(tmp_path))

    out = capsys.readouterr().out
    assert "EVREN CLI" in out
    assert "Görev listesi boş." in out
    assert "sonlandırılıyor" in out
