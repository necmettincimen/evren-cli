"""
History compaction for EVREN CLI.

When the conversation grows past the context budget, shrink it in escalating
stages. Smart mode (default, EVREN_SMART_COMPACT) is built for prompt caching:
compaction is RARE and DEEP, going down to a fraction of the limit so the
following steps only append to a stable prefix.

Smart stages (compact down to the target):
1. Old tool results (all but the last 2 units) -> one-line `[özet]` stubs.
2. Oldest units -> ONE `[Önceki adımların özeti]` user message (cheap
   summarizer call, deterministic fallback); first task and last 4 units stay.
Then the classic last-resort stages:
3. Truncate large tool outputs (head + tail).
4. Drop middle turns, keeping assistant+tool pairs atomic.
5. Keep only the system prompt + the most recent turn.

The system prompt is always preserved.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from src.token_manager import TokenManager

# Tool output truncation limits (characters).
TOOL_HEAD_CHARS = 1500
TOOL_TAIL_CHARS = 500
TOOL_TRUNCATION_MARKER = "\n... [araç çıktısı bağlam için kırpıldı] ...\n"

STUB_PREFIX = "[özet] "
SUMMARY_PREFIX = "[Önceki adımların özeti]"
STUB_MIN_CHARS = 240
KEEP_RAW_UNITS_STUB = 2
KEEP_RAW_UNITS_SUMMARY = 4
FALLBACK_SUMMARY_CHARS = 3500

_KEY_LINE_RE = re.compile(r"(\d+\s+(failed|passed|error)|FAIL|Error|error|HATA)")
_RANGE_RE = re.compile(r"Satırlar (\d+)-(\d+)")


def _truncate_tool_content(content: str) -> str:
    """Keeps the head and tail of a long tool output, dropping the middle."""
    if len(content) <= TOOL_HEAD_CHARS + TOOL_TAIL_CHARS:
        return content
    head = content[:TOOL_HEAD_CHARS]
    tail = content[-TOOL_TAIL_CHARS:]
    return head + TOOL_TRUNCATION_MARKER + tail


# ---- stubs and summaries ---------------------------------------------------

def _kr(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def build_stub(name: str, args: dict[str, Any], result: str) -> str:
    """One-line description of a tool call and its outcome."""
    size = f"({_kr(len(result))} kr)"
    if name == "view_file":
        if result.startswith("HATA"):
            return f"{STUB_PREFIX}view_file {args.get('path', '?')} → {_clip(_first_line(result), 100)} {size}"
        match = _RANGE_RE.search(result[:300])
        rng = f" {match.group(1)}-{match.group(2)}" if match else ""
        return f"{STUB_PREFIX}view_file {args.get('path', '?')}{rng} okundu {size}"
    if name in ("run_command", "ssh_run"):
        cmd = _clip(str(args.get("command", "")), 60)
        head = _first_line(result)
        if head.startswith("Çıkış Kodu"):
            outcome = head.replace("Çıkış Kodu:", "Çıkış Kodu")
            lines = [ln.strip() for ln in result.splitlines()[1:] if ln.strip()]
            key = next((ln for ln in reversed(lines) if _KEY_LINE_RE.search(ln)), "")
            if not key and not outcome.endswith(" 0") and lines:
                key = lines[-1]
            if key:
                outcome += ", " + _clip(key, 80)
        else:
            outcome = _clip(head, 100)
        return f'{STUB_PREFIX}{name} "{cmd}" → {outcome} {size}'
    shown = []
    for value in args.values():
        if isinstance(value, str) and value:
            shown.append(_clip(value, 50))
        if len(shown) == 2:
            break
    arg_text = (" " + " ".join(shown)) if shown else ""
    return f"{STUB_PREFIX}{name}{arg_text} → {_clip(_first_line(result), 100)} {size}"


def _call_index(messages: list[dict[str, Any]]) -> dict[str, tuple[str, dict[str, Any]]]:
    index: dict[str, tuple[str, dict[str, Any]]] = {}
    for m in messages:
        for tc in m.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function", {})
            raw = fn.get("arguments", "")
            try:
                args = json.loads(raw) if isinstance(raw, str) and raw else (raw or {})
            except ValueError:
                args = {}
            index[str(tc.get("id"))] = (str(fn.get("name", "?")), args if isinstance(args, dict) else {})
    return index


def group_units(body: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Splits messages into atomic units: [user*] assistant [tool*]."""
    units: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for m in body:
        role = m.get("role")
        if current:
            last = current[-1].get("role")
            has_assistant = any(x.get("role") == "assistant" for x in current)
            if (role == "user" and last != "user") or (role == "assistant" and has_assistant):
                units.append(current)
                current = []
        current.append(m)
    if current:
        units.append(current)
    return units


def stub_tool_results(
    messages: list[dict[str, Any]], keep_units: int = KEEP_RAW_UNITS_STUB,
) -> list[dict[str, Any]]:
    """Replaces old tool results (all but the last `keep_units` units) by stubs."""
    calls = _call_index(messages)
    start = 1 if messages and messages[0].get("role") == "system" else 0
    units = group_units(messages[start:])
    protected = {id(m) for unit in units[-keep_units:] for m in unit} if keep_units else set()
    out = []
    for m in messages:
        content = m.get("content")
        if (
            m.get("role") == "tool"
            and isinstance(content, str)
            and id(m) not in protected
            and not content.startswith(STUB_PREFIX)
            and len(content) > STUB_MIN_CHARS
        ):
            name, args = calls.get(str(m.get("tool_call_id")), (str(m.get("name") or "araç"), {}))
            m = dict(m)
            m["content"] = build_stub(name, args, content)
        out.append(m)
    return out


def _stub_lines(messages: list[dict[str, Any]]) -> tuple[str, str, list[str]]:
    """(first task text, last final answer, step stub lines) of a message list."""
    calls = _call_index(messages)
    task = answer = ""
    stubs: list[str] = []
    for m in messages:
        content = m.get("content") if isinstance(m.get("content"), str) else ""
        role = m.get("role")
        if role == "user" and not task:
            task = content
        elif role == "assistant" and content.strip() and not m.get("tool_calls"):
            answer = content
        elif role == "tool":
            if content.startswith(STUB_PREFIX):
                stubs.append(content[len(STUB_PREFIX):])
            else:
                name, args = calls.get(str(m.get("tool_call_id")), (str(m.get("name") or "araç"), {}))
                stubs.append(build_stub(name, args, content)[len(STUB_PREFIX):])
    return task, answer, stubs


def deterministic_summary(messages: list[dict[str, Any]]) -> str:
    """Summary without an API call: task texts, assistant notes and tool stubs."""
    calls = _call_index(messages)
    lines: list[str] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content") if isinstance(m.get("content"), str) else ""
        if role == "user" and content:
            if content.startswith(SUMMARY_PREFIX):
                lines.append(_clip(content[len(SUMMARY_PREFIX):], 1200))
            else:
                lines.append("- Görev: " + _clip(content, 200))
        elif role == "assistant" and content.strip():
            lines.append("- Ajan: " + _clip(content, 200))
        elif role == "tool":
            if content.startswith(STUB_PREFIX):
                lines.append("- " + content[len(STUB_PREFIX):])
            else:
                name, args = calls.get(str(m.get("tool_call_id")), (str(m.get("name") or "araç"), {}))
                lines.append("- " + build_stub(name, args, content)[len(STUB_PREFIX):])
    text = "\n".join(lines)
    if len(text) > FALLBACK_SUMMARY_CHARS:
        text = "…\n" + text[-FALLBACK_SUMMARY_CHARS:]
    return text


def collapse_task(messages: list[dict[str, Any]]) -> str:
    """Deterministic one-paragraph summary of one finished task's messages."""
    task, answer, stubs = _stub_lines(messages)
    parts = [f"[Önceki görev özeti] Görev: {_clip(task, 300)}"]
    if answer:
        parts.append(f"Sonuç: {_clip(answer, 600)}")
    if stubs:
        parts.append("Adımlar:\n" + "\n".join("- " + _clip(s, 160) for s in stubs[-25:]))
    return "\n".join(parts)


class HistoryCompactor:
    """Compacts a message history to fit within a token budget."""

    def __init__(
        self,
        token_manager: TokenManager | None = None,
        *,
        smart: bool = False,
        summarizer: Callable[[list[dict[str, Any]]], str | None] | None = None,
        target_ratio: float = 0.5,
    ):
        self.token_manager = token_manager or TokenManager()
        self.smart = smart
        self.summarizer = summarizer
        self.target_ratio = target_ratio
        # Number of smart compaction events (hysteresis diagnostics).
        self.events = 0

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

        if self.smart:
            self.events += 1
            target = int(limit * self.target_ratio)
            working = stub_tool_results(working)
            if self._fits(working, output_budget, target):
                return working
            working = self._stage_summarize_middle(working)
            if self._fits(working, output_budget, limit):
                return working

        # Truncate large tool outputs.
        working = self._stage_truncate_tools(working)
        if self._fits(working, output_budget, limit):
            return working

        # Drop middle turns (keep system + recent), atomically.
        working = self._stage_drop_middle(working, output_budget, limit)
        if self._fits(working, output_budget, limit):
            return working

        # Keep only system + last turn.
        working = self._stage_system_and_last(working)
        return working

    # ---- stages --------------------------------------------------------

    def _stage_summarize_middle(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Replaces the oldest units by ONE summary message; keeps task + last units."""
        system = [m for m in messages if m.get("role") == "system"]
        body = [m for m in messages if m.get("role") != "system"]
        first_user = next((i for i, m in enumerate(body) if m.get("role") == "user"), None)
        if first_user is None:
            return messages
        head, rest = body[: first_user + 1], body[first_user + 1:]
        units = group_units(rest)
        if len(units) <= KEEP_RAW_UNITS_SUMMARY:
            return messages
        old = [m for unit in units[:-KEEP_RAW_UNITS_SUMMARY] for m in unit]
        recent = [m for unit in units[-KEEP_RAW_UNITS_SUMMARY:] for m in unit]
        text = None
        if self.summarizer is not None:
            try:
                text = self.summarizer(old)
            except Exception:
                text = None
        if not text:
            text = deterministic_summary(old)
        return system + head + [{"role": "user", "content": f"{SUMMARY_PREFIX}\n{text}"}] + recent

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
