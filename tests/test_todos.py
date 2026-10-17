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



class TestTodoWriteTool:
    def _call(self, todos):
        return execute_tool_call("todo_write", {"todos": todos})


    def test_two_in_progress_returns_error(self):
        result = self._call([
            {"content": "a", "status": "in_progress"},
            {"content": "b", "status": "in_progress"},
        ])
        assert result.startswith("HATA")
        assert get_store().snapshot() == []




    def test_gateway_allows_in_all_modes(self):
        for mode in AgentMode:
            allowed, _ = check_tool_allowed("todo_write", {"todos": []}, mode)
            assert allowed



