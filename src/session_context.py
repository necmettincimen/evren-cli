"""Per-session mutable state (2.5).

Everything that used to be a module-level mutable singleton (the active secret
vault, the todo store, the background-command manager, the user-skill cache and
the markdown stream buffer) lives in a SessionContext. The old module-level
accessors (`active_vault()`, `get_store()`, `get_manager()`, ...) are thin
wrappers that delegate to the *current* context, so callers keep working.
"""

from __future__ import annotations

import threading
from typing import Any


class SessionContext:
    """Holds the mutable state of one agent session."""

    def __init__(self) -> None:
        # Imported lazily: those modules import this one for their accessors.
        from src.secret_vault import SecretVault
        from src.todos import TodoStore

        self.vault = SecretVault()
        self.todos = TodoStore()
        self.background: Any = None  # BackgroundManager, created on first use
        self.background_lock = threading.Lock()
        self.user_skills: dict = {}
        self.stream: dict = {"buffer": [], "live": None, "pending": ""}


_default: SessionContext | None = None
_current: SessionContext | None = None
_lock = threading.Lock()


def default_context() -> SessionContext:
    """The process-wide fallback context (created on first use)."""
    global _default
    with _lock:
        if _default is None:
            _default = SessionContext()
        return _default


def current() -> SessionContext:
    """The context the running session uses (the default one if none is set)."""
    ctx = _current
    return ctx if ctx is not None else default_context()


def set_current(ctx: SessionContext | None) -> None:
    """Make `ctx` current; None falls back to the default context."""
    global _current
    _current = ctx
