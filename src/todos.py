"""
Session-scoped todo list for the agent (Claude Code TodoWrite analogue).

The agent maintains a short task checklist via the `todo_write` tool; each item
has a `content` and a `status` (pending / in_progress / completed). At most one
item may be `in_progress` at a time. The list lives in memory for the current
process only (no persistence) and is rendered as a compact checklist both after
every tool update and via the REPL `/todos` command.
"""

from __future__ import annotations

from dataclasses import dataclass

VALID_STATUSES = ("pending", "in_progress", "completed")

# Compact checklist markers: [ ] pending, [~] in progress, [x] completed.
_STATUS_MARKERS = {"pending": " ", "in_progress": "~", "completed": "x"}


@dataclass
class TodoItem:
    """A single checklist entry."""

    content: str
    status: str = "pending"


class TodoStore:
    """In-memory todo list enforcing the single in_progress rule."""

    def __init__(self) -> None:
        self._items: list[TodoItem] = []

    def write(self, items: list[dict] | list[TodoItem]) -> None:
        """Replaces the whole list with `items` (dicts or TodoItems).

        Raises ValueError when the payload is malformed or contains more than
        one `in_progress` item; on error the previous list is left untouched.
        """
        parsed: list[TodoItem] = []
        for raw in items or []:
            if isinstance(raw, TodoItem):
                content, status = raw.content, raw.status
            elif isinstance(raw, dict):
                content = str(raw.get("content", "")).strip()
                status = str(raw.get("status", "pending")).strip().lower()
            else:
                raise ValueError(
                    "Her todo maddesi {content, status} alanlı bir nesne olmalı."
                )
            if not content:
                raise ValueError("Todo maddesinin 'content' alanı boş olamaz.")
            if status not in VALID_STATUSES:
                raise ValueError(
                    f"Geçersiz durum: '{status}'. İzin verilenler: {', '.join(VALID_STATUSES)}."
                )
            parsed.append(TodoItem(content=content, status=status))

        in_progress = [item for item in parsed if item.status == "in_progress"]
        if len(in_progress) > 1:
            raise ValueError(
                f"Aynı anda yalnızca bir madde in_progress olabilir ({len(in_progress)} verildi)."
            )
        self._items = parsed

    def render(self) -> str:
        """Returns the compact checklist text (empty string when no items)."""
        if not self._items:
            return ""
        done = sum(1 for item in self._items if item.status == "completed")
        lines = [f"Görev listesi ({done}/{len(self._items)} tamamlandı):"]
        for item in self._items:
            marker = _STATUS_MARKERS[item.status]
            lines.append(f"[{marker}] {item.content} ({item.status})")
        return "\n".join(lines)

    def snapshot(self) -> list[dict]:
        """Returns a plain-data copy of the current list."""
        return [{"content": item.content, "status": item.status} for item in self._items]

    def clear(self) -> None:
        """Empties the list (used by tests and session resets)."""
        self._items = []


def get_store() -> TodoStore:
    """Returns the TodoStore of the current session context."""
    from src.session_context import current

    return current().todos
