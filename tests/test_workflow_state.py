import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.agent import AgentSession
from src.modes import AgentMode
from src.prompt_frame import PromptFrame
from src.tracking import add_task, list_tasks, resolve_blocker, set_blocker
from src.workflow_state import (
    add_decision, checkpoint_path, clear_checkpoint, decision_context,
    load_checkpoint, load_decisions, redact_text, review_tasks,
)


def paused_session(root):
    session = AgentSession(MagicMock(default_model="test-model"), root)
    session.apply_frame(PromptFrame(rol="engineer"))
    session.messages.extend([
        {"role": "user", "content": "inspect local and remote"},
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": "call-1", "type": "function", "function": {
                "name": "run_command",
                "arguments": json.dumps({"command": "echo test", "password": "test-only-secret"}),
            },
        }]},
        {"role": "tool", "name": "run_command", "tool_call_id": "call-1",
         "content": "postgresql://user:test-only-secret@192.0.2.1/db"},
        {"role": "assistant", "content": "step limit"},
    ])
    session.pending_mode = AgentMode.ASK
    return session


def test_checkpoint_roundtrip_masks_credentials_without_executing(tmp_path):
    session = paused_session(tmp_path)
    path = session.save_checkpoint()
    text = path.read_text(encoding="utf-8")
    assert "test-only-secret" not in text
    assert "[REDACTED]" in text
    state = load_checkpoint(tmp_path)
    assert all(m["role"] != "system" for m in state["messages"])
    args = json.loads(state["messages"][1]["tool_calls"][0]["function"]["arguments"])
    assert args["password"] == "[REDACTED]"
    restored = AgentSession(MagicMock(default_model="other-model"), tmp_path)
    with patch("src.agent.execute_tool_call") as execute:
        assert restored.restore_checkpoint()
    execute.assert_not_called()
    assert restored.pending_mode == AgentMode.ASK
    assert restored.model == "test-model"
    assert "Rol: engineer" in restored.messages[0]["content"]
    assert restored.messages[1:] == state["messages"]
    assert restored.task_goal == "inspect local and remote"


def test_checkpoint_missing_and_clear(tmp_path):
    assert load_checkpoint(tmp_path) is None
    assert not AgentSession(MagicMock(default_model="test"), tmp_path).restore_checkpoint()
    clear_checkpoint(tmp_path)
    paused_session(tmp_path).save_checkpoint()
    clear_checkpoint(tmp_path)
    assert not checkpoint_path(tmp_path).exists()


@pytest.mark.parametrize("field, value", [
    ("workspace", "other"), ("mode", "invalid"), ("messages", []),
    ("frame", {"rol": 2}), ("model", ""), ("schema_version", 9), ("saved_at", None),
    ("goal", None),
])
def test_checkpoint_rejects_invalid_fields_without_mutating_session(tmp_path, field, value):
    paused_session(tmp_path).save_checkpoint()
    state = json.loads(checkpoint_path(tmp_path).read_text(encoding="utf-8"))
    state[field] = value
    checkpoint_path(tmp_path).write_text(json.dumps(state), encoding="utf-8")
    session = AgentSession(MagicMock(default_model="test"), tmp_path)
    before = session.messages[:]
    with pytest.raises(ValueError):
        session.restore_checkpoint()
    assert session.messages == before
    assert session.pending_mode is None


def test_checkpoint_rejects_orphan_tool_and_corrupt_json(tmp_path):
    session = paused_session(tmp_path)
    session.messages = [{"role": "tool", "tool_call_id": "orphan", "content": "test"}]
    session.save_checkpoint()
    with pytest.raises(ValueError, match="eşleşmiyor"):
        load_checkpoint(tmp_path)
    checkpoint_path(tmp_path).write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="okunamadı"):
        load_checkpoint(tmp_path)


def test_checkpoint_cannot_save_without_pending_task(tmp_path):
    with pytest.raises(ValueError):
        AgentSession(MagicMock(default_model="test"), tmp_path).save_checkpoint()


@pytest.mark.parametrize("text", [
    "password=test-only-secret", 'password="test-only-secret"',
    '{"api_key":"test-only-secret"}', "Bearer test-only-secret",
    "postgresql://user:test-only-secret@192.0.2.1/db",
    "-----BEGIN PRIVATE KEY-----\ntest-only-secret\n-----END PRIVATE KEY-----",
])
def test_secret_forms_are_masked(text):
    assert "test-only-secret" not in redact_text(text)


def test_decisions_are_validated_persisted_and_injected_without_losing_frame(tmp_path):
    assert load_decisions(tmp_path) == []
    with pytest.raises(ValueError):
        add_decision(tmp_path, "", "reason", "risk")
    session = AgentSession(MagicMock(default_model="test"), tmp_path)
    session.apply_frame(PromptFrame(rol="engineer"))
    add_decision(tmp_path, "partition writes", "avoid conflicts", "needs routing")
    assert len(load_decisions(tmp_path)) == 1
    session.refresh_context()
    assert "partition writes" in session.messages[0]["content"]
    assert "Rol: engineer" in session.messages[0]["content"]
    assert "yürütme izni değildir" in decision_context(tmp_path)


def test_review_deadlines_stalled_and_closed_tasks():
    now = datetime(2026, 10, 11, tzinfo=timezone.utc)
    old = (now - timedelta(days=14)).isoformat()
    items = [
        {"id": "stale", "status": "in-progress", "updated_at": old, "deadline": "2026-10-10"},
        {"id": "blocked", "status": "blocked", "updated_at": now.isoformat()},
        {"id": "done", "status": "completed"},
        {"id": "abandoned", "status": "abandoned"},
    ]
    reviewed = review_tasks(items, now)
    assert len(reviewed) == 2
    assert reviewed[0]["flags"] == ["overdue", "stalled"]
    assert reviewed[1]["flags"] == ["blocked"]
    with pytest.raises(ValueError, match="tarihi"):
        review_tasks([{"id": "bad", "status": "in-progress", "deadline": "bad"}], now)


def test_blocker_add_resolve_and_validation(tmp_path):
    add_task("registry", "inspect image access", workspace_root=tmp_path)
    with pytest.raises(ValueError):
        set_blocker("registry", "guessed", "evidence", tmp_path)
    with pytest.raises(ValueError):
        set_blocker("missing", "dns", "NXDOMAIN", tmp_path)
    item = set_blocker("registry", "auth", "HTTP 401 token=test-only-secret", tmp_path)
    assert item["status"] == "blocked"
    assert "test-only-secret" not in item["blocker"]["evidence"]
    assert "secret" in item["blocker"]["route"]
    assert list_tasks("blocked", tmp_path)[0]["id"] == "registry"
    with pytest.raises(ValueError):
        resolve_blocker("registry", "", tmp_path)
    item = resolve_blocker("registry", "access verified", tmp_path)
    assert item["status"] == "in-progress"
    assert "blocker" not in item


def test_workflow_commands_require_confirmation_for_writes(tmp_path):
    from src.repl import _handle_workflow_command

    session = paused_session(tmp_path)
    add_task("registry", "inspect", workspace_root=tmp_path)
    with patch("src.repl.prompt_confirm", return_value=False):
        assert _handle_workflow_command("/checkpoint", "save", session, MagicMock())
        assert _handle_workflow_command("/blockers", "add registry auth HTTP401", session, MagicMock())
        answers = iter(["write routing", "reason", "risk"])
        assert _handle_workflow_command("/decisions", "add", session, lambda _: next(answers))
    assert not checkpoint_path(tmp_path).exists()
    assert load_decisions(tmp_path) == []
    assert list_tasks("all", tmp_path)[0]["status"] == "not-started"


def test_check_in_applies_only_confirmed_updates(tmp_path):
    from src.repl import _handle_workflow_command

    session = paused_session(tmp_path)
    add_task("task", "inspect", workspace_root=tmp_path)
    answers = iter(["in-progress", "25", "test passed"])
    with patch("src.repl.prompt_confirm", return_value=True):
        _handle_workflow_command("/check-in", "update", session, lambda _: next(answers))
    item = list_tasks("all", tmp_path)[0]
    assert item["status"] == "in-progress"
    assert item["progress"] == 25
    answers = iter(["completed", "20", ""])
    with patch("src.repl.prompt_confirm") as confirm, patch("src.repl.print_error") as error:
        _handle_workflow_command("/check-in", "update", session, lambda _: next(answers))
    error.assert_called_once()
    confirm.assert_not_called()
    assert list_tasks("all", tmp_path)[0]["progress"] == 25


def test_agent_wrapper_saves_only_on_confirmation_and_clears_on_success(tmp_path):
    from src.repl import _run_agent

    session = paused_session(tmp_path)
    with patch.object(session, "continue_task"), patch("src.repl.prompt_confirm", return_value=False):
        _run_agent(session)
    assert not checkpoint_path(tmp_path).exists()
    with patch.object(session, "continue_task"), patch("src.repl.prompt_confirm", return_value=True):
        _run_agent(session)
    assert checkpoint_path(tmp_path).exists()
    session.pending_mode = None
    with patch.object(session, "continue_task"):
        _run_agent(session)
    assert not checkpoint_path(tmp_path).exists()


def test_decision_corruption_is_explicit_not_silent(tmp_path):
    path = tmp_path / ".evren" / "decisions.json"
    path.parent.mkdir()
    path.write_text('{"schema_version":1,"items":[{}]}', encoding="utf-8")
    with pytest.raises(ValueError, match="Karar günlüğü"):
        load_decisions(tmp_path)

def test_followup_wrapper_routes_without_form_or_automatic_approval(tmp_path):
    from src.repl import _run_agent

    session = paused_session(tmp_path)
    with patch.object(session, "execute_followup") as execute, \
         patch.object(session, "run_step") as run, \
         patch("src.repl.prompt_confirm", return_value=False):
        _run_agent(session, "yap artık", followup=True)
    execute.assert_called_once_with("yap artık")
    run.assert_not_called()
    assert not checkpoint_path(tmp_path).exists()


def test_followup_without_in_memory_task_keeps_saved_checkpoint(tmp_path):
    from src.repl import _run_agent

    session = paused_session(tmp_path)
    session.save_checkpoint()
    saved = checkpoint_path(tmp_path).read_bytes()
    session.clear_history()
    with patch.object(session.client, "chat_stream_assembled") as request:
        _run_agent(session, "yap artık", followup=True)
    request.assert_not_called()
    assert checkpoint_path(tmp_path).read_bytes() == saved


def test_all_blocker_routes_use_registered_diagnostics():
    from src.diagnostics import get_diagnostic
    from src.workflow_state import BLOCKER_ROUTES

    assert get_diagnostic("dependency-hell") is not None
    assert "/diagnose dependency-hell" in BLOCKER_ROUTES["dependency"]
