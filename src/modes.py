"""
Working modes for EVREN CLI.

Ports the C# `AgentMode.cs` concept (without the ssh/psql tools, which are
intentionally out of scope for the safety promise):

- normal : full tool access (read + write + run).
- ask    : read-only. write_file/edit_file are disabled; run_command is limited
           to an allow-list of read-only verbs.
- plan   : no source-file writes. The `create_plan` tool writes to
           `plans/<name>/plan.md` only.

A mode can be set per-turn via a message prefix ("ask: ...", "plan: ...") or
persistently via the `/mode <ask|plan|normal>` command.
"""

from __future__ import annotations

from enum import Enum


class AgentMode(str, Enum):
    NORMAL = "normal"
    ASK = "ask"
    PLAN = "plan"


# Message prefixes that switch the mode for a single turn.
MODE_PREFIXES = {
    "ask:": AgentMode.ASK,
    "plan:": AgentMode.PLAN,
}


def parse_mode_prefix(text: str) -> tuple[AgentMode | None, str]:
    """Detects a leading mode prefix ("ask:" / "plan:").

    Returns (mode_or_none, stripped_text).
    """
    stripped = text.lstrip()
    lowered = stripped.lower()
    for prefix, mode in MODE_PREFIXES.items():
        if lowered.startswith(prefix):
            return mode, stripped[len(prefix):].strip()
    return None, text


def mode_label(mode: AgentMode) -> str:
    return {
        AgentMode.NORMAL: "normal",
        AgentMode.ASK: "ask (salt-okuma)",
        AgentMode.PLAN: "plan (yalnızca plan yazımı)",
    }.get(mode, mode.value)
