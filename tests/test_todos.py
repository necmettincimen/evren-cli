"""Tests for the session todo list (todo_write tool + /todos command)."""

import pytest

from src.todos import TodoStore, get_store
from src.tools import execute_tool_call, TOOLS_SCHEMA, tools_for_mode
from src.tool_gateway import check_tool_allowed
from src.modes import AgentMode


@pytest.fixture(autouse=True)
def _clean_store():
    get_store().clear()
    yield
    get_store().clear()


class TestTodoStore:
    def test_single_in_progress_rule(self):
        store = TodoStore()
        with pytest.raises(ValueError):
            store.write([
                {"content": "a", "status": "in_progress"},
                {"content": "b", "status": "in_progress"},
            ])
        # previous list untouched on error
        store.write([{"content": "a", "status": "pending"}])
        assert len(store.snapshot()) == 1

    def test_invalid_status_rejected(self):
        store = TodoStore()
        with pytest.raises(ValueError):
            store.write([{"content": "a", "status": "done"}])

    def test_empty_content_rejected(self):
        store = TodoStore()
        with pytest.raises(ValueError):
            store.write([{"content": "  ", "status": "pending"}])

    def test_render_contains_all_markers(self):
        store = TodoStore()
        store.write([
            {"content": "yaz", "status": "completed"},
            {"content": "oku", "status": "in_progress"},
            {"content": "test et", "status": "pending"},
        ])
        text = store.render()
        assert "[x] yaz" in text
        assert "[~] oku" in text
        assert "[ ] test et" in text
        assert "1/3 tamamlandı" in text

    def test_render_empty(self):
        assert TodoStore().render() == ""


class TestTodoWriteTool:
    def _call(self, todos):
        return execute_tool_call("todo_write", {"todos": todos})

    def test_returns_checklist(self):
        result = self._call([
            {"content": "adım 1", "status": "completed"},
            {"content": "adım 2", "status": "in_progress"},
        ])
        assert "[x] adım 1" in result
        assert "[~] adım 2" in result

    def test_two_in_progress_returns_error(self):
        result = self._call([
            {"content": "a", "status": "in_progress"},
            {"content": "b", "status": "in_progress"},
        ])
        assert result.startswith("HATA")
        assert get_store().snapshot() == []

    def test_missing_todos_returns_error(self):
        assert execute_tool_call("todo_write", {}).startswith("HATA")

    def test_empty_list_clears(self):
        self._call([{"content": "a"}])
        result = self._call([])
        assert "temizlendi" in result
        assert get_store().snapshot() == []

    def test_schema_present_in_every_mode(self):
        names = [t["function"]["name"] for t in TOOLS_SCHEMA]
        assert "todo_write" in names
        for mode in ("normal", "ask", "plan"):
            assert "todo_write" in [t["function"]["name"] for t in tools_for_mode(mode)]

    def test_gateway_allows_in_all_modes(self):
        for mode in AgentMode:
            allowed, _ = check_tool_allowed("todo_write", {"todos": []}, mode)
            assert allowed

    def test_store_shared_with_repl_view(self):
        self._call([{"content": "paylaşılan madde", "status": "in_progress"}])
        assert "[~] paylaşılan madde" in get_store().render()


def test_agent_prints_full_checklist_and_clear_resets_it():
    import os
    import tempfile
    from pathlib import Path
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    from src.agent import AgentSession
    from src.todos import get_store

    get_store().clear()
    call = SimpleNamespace(id="t", function=SimpleNamespace(
        name="todo_write",
        arguments='{"todos": [{"content": "oku", "status": "completed"}, '
                  '{"content": "yaz", "status": "in_progress"}]}',
    ))
    replies = [
        SimpleNamespace(choices=[SimpleNamespace(finish_reason="tool_calls", message=SimpleNamespace(
            content="", tool_calls=[call]))], usage=None),
        SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(
            content="bitti", tool_calls=[]))], usage=None),
    ]
    client = MagicMock(default_model="m")
    client.chat_stream_assembled.side_effect = replies
    with tempfile.TemporaryDirectory() as directory, \
            patch.dict(os.environ, {"EVREN_AUTO_DOCS": "false"}), \
            patch("src.agent.print_tool_result") as shown, \
            patch("src.agent.print_tool_call"), patch("src.agent.print_info"):
        session = AgentSession(client, workspace_root=Path(directory))
        session.run_step("iki adımlı iş")
        printed = shown.call_args.args[1]
        assert "[x] oku" in printed
        assert "[~] yaz" in printed
        session.clear_history()
    assert get_store().render() == ""
