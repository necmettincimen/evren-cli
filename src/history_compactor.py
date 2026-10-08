"""
History compaction for EVREN CLI.

Ports the C# `HistoryCompactor` behaviour: when the conversation grows past the
context budget, shrink it in three escalating stages:

1. Truncate large tool outputs (keep head + tail).
2. Drop middle turns — but always keep assistant+tool pairs atomic so the
   OpenAI message contract (tool_call_id must follow its assistant message)
   is never broken.
3. Keep only the system prompt + the most recent turn.

The system prompt is always preserved.
"""

from __future__ import annotations

from typing import Any

from src.token_manager import TokenManager

# Tool output truncation limits (characters).
TOOL_HEAD_CHARS = 1500
TOOL_TAIL_CHARS = 500
TOOL_TRUNCATION_MARKER = "\n... [araç çıktısı bağlam için kırpıldı] ...\n"


def _truncate_tool_content(content: str) -> str:
    """Keeps the head and tail of a long tool output, dropping the middle."""
    if len(content) <= TOOL_HEAD_CHARS + TOOL_TAIL_CHARS:
        return content
    head = content[:TOOL_HEAD_CHARS]
    tail = content[-TOOL_TAIL_CHARS:]
    return head + TOOL_TRUNCATION_MARKER + tail


class HistoryCompactor:
    """Compacts a message history to fit within a token budget."""

    def __init__(self, token_manager: TokenManager | None = None):
        self.token_manager = token_manager or TokenManager()

    def compact(
        self,
        messages: list[dict[str, Any]],
        output_budget: int,
        *,
        max_tokens: int | None = None,
    ) -> list[dict[str, Any]]:
        """Returns a compacted copy of `messages` that fits the budget.

        `max_tokens` overrides the token manager's context window when provided.
        """
        if not messages:
            return messages

        # Work on a shallow copy so callers' history is not mutated.
        working = [dict(m) for m in messages]

        limit = max_tokens if max_tokens is not None else self.token_manager.context_window
        if self._fits(working, output_budget, limit):
            return working

        # Stage 1: truncate large tool outputs.
        working = self._stage_truncate_tools(working)
        if self._fits(working, output_budget, limit):
            return working

        # Stage 2: drop middle turns (keep system + recent), atomically.
        working = self._stage_drop_middle(working, output_budget, limit)
        if self._fits(working, output_budget, limit):
            return working

        # Stage 3: keep only system + last turn.
        working = self._stage_system_and_last(working)
        return working

    # ---- stages --------------------------------------------------------

    def _stage_truncate_tools(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = []
        for m in messages:
            if m.get("role") == "tool" and isinstance(m.get("content"), str):
                m = dict(m)
                m["content"] = _truncate_tool_content(m["content"])
            result.append(m)
        return result

    def _stage_drop_middle(
        self,
        messages: list[dict[str, Any]],
        output_budget: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Drops middle turns while keeping assistant+tool pairs atomic.

        Structure: [system] + body. We keep the system message and trim the body
        from the front (oldest first), never splitting an assistant message from
        its following tool responses.
        """
        if not messages:
            return messages

        system_msgs = [m for m in messages if m.get("role") == "system"]
        body = [m for m in messages if m.get("role") != "system"]

        # Group body into atomic units: an assistant message plus any tool
        # messages that immediately follow it form one unit.
        units: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        for m in body:
            if m.get("role") == "assistant" and current:
                units.append(current)
                current = [m]
            else:
                current.append(m)
        if current:
            units.append(current)

        # Drop oldest units until it fits (always keep at least the last unit).
        while len(units) > 1:
            candidate = system_msgs + [msg for unit in units for msg in unit]
            if self._fits(candidate, output_budget, limit):
                return candidate
            units.pop(0)

        return system_msgs + [msg for unit in units for msg in unit]

    def _stage_system_and_last(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]
        if not non_system:
            return system_msgs
        # Keep the last atomic unit (assistant + its tool responses, or last user).
        last = non_system[-1]
        # If the last message is a tool response, walk back to include its
        # assistant parent so the contract stays valid.
        if last.get("role") == "tool":
            idx = len(non_system) - 1
            while idx > 0 and non_system[idx].get("role") == "tool":
                idx -= 1
            return system_msgs + non_system[idx:]
        return system_msgs + [last]

    # ---- helpers -------------------------------------------------------

    def _fits(self, messages: list[dict[str, Any]], output_budget: int, limit: int) -> bool:
        used = self.token_manager.estimate_messages(messages)
        return used + output_budget <= limit
