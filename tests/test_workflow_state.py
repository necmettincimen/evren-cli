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










@pytest.mark.parametrize("text", [
    "password=test-only-secret", 'password="test-only-secret"',
    '{"api_key":"test-only-secret"}', "Bearer test-only-secret",
    "postgresql://user:test-only-secret@192.0.2.1/db",
    "-----BEGIN PRIVATE KEY-----\ntest-only-secret\n-----END PRIVATE KEY-----",  # check_text: ignore
])
def test_secret_forms_are_masked(text):
    assert "test-only-secret" not in redact_text(text)








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





def test_interrupt_correction_continues_without_a_new_brief(tmp_path):
    from src.repl import _take_interrupt_followup

    session = paused_session(tmp_path)
    session.task_goal = "inspect local and remote"
    session.interrupted = True
    kind, text = _take_interrupt_followup(session, lambda _label: "portu 8080 yap")
    assert (kind, text) == ("followup", "portu 8080 yap")
    assert session.interrupted is False
    session.interrupted = True
    assert _take_interrupt_followup(session, lambda _label: "") == ("continue", "")
    session.interrupted = True
    assert _take_interrupt_followup(session, lambda _label: "/help") == ("command", "/help")
    assert session.interrupted is True




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




