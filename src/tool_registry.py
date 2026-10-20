"""Single source of truth for tool metadata (2.1).

One record per tool. The old module-level sets (READ_ONLY_TOOLS,
ASK_HIDDEN_TOOLS, ...) are derived from here and kept as aliases.
Handlers are resolved lazily by name from src.tools to avoid import cycles.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolSpec:
    name: str
    handler: str = ""            # attribute name in src.tools (lazy)
    read_only: bool = False      # never modifies the workspace
    mutates: bool = False        # writes source files (local/remote)
    needs_approval: bool = False
    modes: frozenset = frozenset({"normal", "ask", "plan"})  # where advertised
    repeat_exempt: bool = False  # identical repeated calls are legitimate


_ALL = frozenset({"normal", "ask", "plan"})
_NORMAL = frozenset({"normal"})

TOOL_REGISTRY: dict[str, ToolSpec] = {s.name: s for s in [
    ToolSpec("view_file", "tool_view_file", read_only=True),
    ToolSpec("list_directory", "tool_list_directory", read_only=True),
    ToolSpec("find_files", "tool_find_files", read_only=True),
    ToolSpec("edit_file", "tool_edit_file", mutates=True, needs_approval=True, modes=_NORMAL),
    ToolSpec("write_file", "tool_write_file", mutates=True, needs_approval=True, modes=_NORMAL),
    ToolSpec("run_command", "tool_run_command", needs_approval=True),
    ToolSpec("check_background", "tool_check_background", read_only=True, repeat_exempt=True),
    ToolSpec("stop_background", "tool_stop_background", repeat_exempt=True),
    ToolSpec("create_plan", "tool_create_plan", needs_approval=True),
    ToolSpec("ssh_list_hosts", "", read_only=True),
    ToolSpec("ssh_run", "", needs_approval=True),
    ToolSpec("ssh_read_file", "", read_only=True),
    ToolSpec("ssh_write_file", "", mutates=True, needs_approval=True, modes=_NORMAL),
    ToolSpec("web_fetch", "tool_web_fetch", read_only=True),
    ToolSpec("search_code", "tool_search_code", read_only=True),
    ToolSpec("web_search", "tool_web_search", read_only=True),
    ToolSpec("apply_patch", "tool_apply_patch", mutates=True, needs_approval=True, modes=_NORMAL),
    ToolSpec("clone_tree", "", mutates=True, needs_approval=True, modes=_NORMAL),
    ToolSpec("todo_write", "tool_todo_write", read_only=True, repeat_exempt=True),
    ToolSpec("ask_user", "tool_ask_user", read_only=True, repeat_exempt=True),
    ToolSpec("delegate", "", modes=_NORMAL, repeat_exempt=True),
]}


def names_where(pred) -> frozenset:
    return frozenset(n for n, s in TOOL_REGISTRY.items() if pred(s))


READ_ONLY = names_where(lambda s: s.read_only)
WRITE = names_where(lambda s: s.mutates)
REPEAT_EXEMPT = names_where(lambda s: s.repeat_exempt)
ASK_HIDDEN = names_where(lambda s: "ask" not in s.modes)
PLAN_HIDDEN = names_where(lambda s: "plan" not in s.modes)
