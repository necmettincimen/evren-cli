"""
Background command support for EVREN CLI.

Ports the Claude-Code "run in background" idea: `run_command` with
`run_in_background=true` starts the process detached from the agent turn and
returns an id immediately. The agent later polls with `check_background`
(incremental output since the last read + status/exit code) or terminates the
whole process tree with `stop_background`. When the REPL exits,
`cleanup_all()` kills every still-running background process tree so nothing
is orphaned.

Output (stdout+stderr merged) is drained by a daemon reader thread into an
in-memory buffer; `check` returns only the bytes appended since the previous
read. The buffer is capped so a chatty background job cannot exhaust RAM.
"""

from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from src.proc_utils import kill_process_tree, _popen_kwargs_for_group

# Hard cap on retained output per background process (characters). When the
# reader thread exceeds this, the oldest half is dropped with a marker.
MAX_BUFFER_CHARS = 200_000
# Truncate each `check` reply so a huge backlog does not blow the model context.
MAX_CHECK_CHARS = 8_000


@dataclass
class BackgroundProcess:
    """One running (or finished) background command."""

    id: str
    command: str
    proc: subprocess.Popen
    started_at: float = field(default_factory=time.monotonic)
    exit_code: int | None = None
    stopped: bool = False
    _buffer: list[str] = field(default_factory=list, repr=False)
    _chars: int = 0
    _read_offset: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _reader: threading.Thread | None = field(default=None, repr=False)

    # -- reader thread side -------------------------------------------------
    def append(self, text: str) -> None:
        with self._lock:
            self._buffer.append(text)
            self._chars += len(text)
            if self._chars > MAX_BUFFER_CHARS:
                # Drop the oldest half, keeping the read offset meaningful by
                # clamping it to the new start.
                keep: list[str] = []
                kept = 0
                for chunk in reversed(self._buffer):
                    keep.append(chunk)
                    kept += len(chunk)
                    if kept >= MAX_BUFFER_CHARS // 2:
                        break
                keep.reverse()
                dropped = self._chars - kept
                self._buffer = ["[eski çıktı kırpıldı]\n"] + keep
                self._chars = kept + len("[eski çıktı kırpıldı]\n")
                self._read_offset = max(0, self._read_offset - dropped)

    # -- consumer side -------------------------------------------------------
    def new_output(self) -> str:
        """Returns output appended since the previous call and advances the cursor."""
        with self._lock:
            text = "".join(self._buffer)
            chunk = text[self._read_offset:]
            self._read_offset = len(text)
            return chunk

    def running(self) -> bool:
        return self.proc.poll() is None

    def status_line(self) -> str:
        code = self.proc.poll()
        if code is None:
            return "çalışıyor"
        if self.exit_code is None:
            self.exit_code = code
        return f"bitti (çıkış kodu: {self.exit_code})" + (" [durduruldu]" if self.stopped else "")

    def elapsed_seconds(self) -> int:
        return int(time.monotonic() - self.started_at)


class BackgroundManager:
    """Process-wide registry of background commands started via run_command."""

    def __init__(self) -> None:
        self._processes: dict[str, BackgroundProcess] = {}
        self._counter = 0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ API
    def start(
        self,
        command: str,
        *,
        cwd: str | None = None,
        shell: bool = False,
        argv: str | list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> BackgroundProcess:
        """Starts `command` (or prebuilt `argv`) in the background, returns the record."""
        with self._lock:
            self._counter += 1
            bg_id = f"bg-{self._counter}"

        kwargs: dict[str, Any] = {
            "cwd": cwd,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,  # merged: single ordered stream
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "shell": shell,
            "bufsize": 1,
        }
        if env:
            # Merge, never replace: a bare {EVREN_SECRET_1: ...} dropped PATH and
            # every background command failed to find python/npm/git.
            import os

            kwargs["env"] = {**os.environ, **env}
        kwargs.update(_popen_kwargs_for_group())

        proc = subprocess.Popen(argv if argv is not None else command, **kwargs)
        record = BackgroundProcess(id=bg_id, command=command, proc=proc)
        record._reader = threading.Thread(
            target=self._drain, args=(record,), daemon=True, name=f"bg-reader-{bg_id}"
        )
        record._reader.start()
        self._processes[bg_id] = record
        return record

    def get(self, bg_id: str) -> BackgroundProcess | None:
        return self._processes.get((bg_id or "").strip())

    def check(self, bg_id: str) -> tuple[str, str, int | None]:
        """Returns (new_output, status_text, exit_code) for `bg_id`.

        exit_code is None while still running. Raises KeyError when unknown.
        """
        record = self.get(bg_id)
        if record is None:
            raise KeyError(bg_id)
        output = record.new_output()
        code = record.proc.poll()
        if code is not None and record.exit_code is None:
            record.exit_code = code
        return output, record.status_line(), record.exit_code

    def stop(self, bg_id: str) -> bool:
        """Kills the whole process tree. Returns False when id is unknown."""
        record = self.get(bg_id)
        if record is None:
            return False
        if record.proc.poll() is None:
            record.stopped = True
            kill_process_tree(record.proc.pid)
            try:
                record.proc.wait(timeout=5)
            except Exception:
                pass
        if record.exit_code is None:
            record.exit_code = record.proc.returncode
        return True

    def list(self) -> list[BackgroundProcess]:
        return list(self._processes.values())

    def cleanup_all(self) -> int:
        """Kills every still-running background tree; returns how many were killed."""
        killed = 0
        for record in self.list():
            if record.proc.poll() is None:
                self.stop(record.id)
                killed += 1
        return killed

    # -------------------------------------------------------------- helpers
    @staticmethod
    def _drain(record: BackgroundProcess) -> None:
        """Reader-thread body: copies the merged stream into the record buffer."""
        try:
            stream = record.proc.stdout
            if stream:
                for line in iter(stream.readline, ""):
                    record.append(line)
                try:
                    stream.close()
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            try:
                record.proc.wait(timeout=10)
            except Exception:
                pass
            if record.exit_code is None:
                record.exit_code = record.proc.returncode


# Process-wide singleton shared by tools.py and repl.py.
_manager: BackgroundManager | None = None
_manager_lock = threading.Lock()


def get_manager() -> BackgroundManager:
    """Returns the process-wide BackgroundManager singleton."""
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = BackgroundManager()
        return _manager
