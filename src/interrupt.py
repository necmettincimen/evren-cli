"""Esc-to-stop and input queueing while the agent runs (Windows msvcrt polling)."""

from __future__ import annotations

import sys
import threading
import time

STOP = threading.Event()
QUEUE: list[str] = []


class RunWatcher:
    """Context manager: polls the keyboard during a run. Esc sets STOP; typed lines are queued."""

    def __init__(self) -> None:
        self._done = threading.Event()
        self._t: threading.Thread | None = None

    def __enter__(self):
        STOP.clear()
        try:
            import msvcrt  # noqa: F401
        except ImportError:
            return self
        if not sys.stdin.isatty():
            return self
        self._t = threading.Thread(target=self._poll, daemon=True)
        self._t.start()
        return self

    def __exit__(self, *exc):
        self._done.set()
        return False

    def _poll(self) -> None:
        import msvcrt

        buf = ""
        while not self._done.is_set():
            if not msvcrt.kbhit():
                time.sleep(0.05)
                continue
            ch = msvcrt.getwch()
            if ch == "\x1b":
                STOP.set()
                buf = ""
            elif ch in ("\r", "\n"):
                if buf.strip():
                    QUEUE.append(buf.strip())
                buf = ""
            elif ch == "\x08":
                buf = buf[:-1]
            elif ch in ("\x00", "\xe0"):
                msvcrt.getwch()
            elif ch >= " ":
                buf += ch


def pop_queued() -> str | None:
    return QUEUE.pop(0) if QUEUE else None
