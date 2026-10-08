"""
Process utilities for EVREN CLI.

`subprocess.run(timeout=...)` only kills the direct child process; grandchildren
(e.g. `msbuild`/`node` spawned by a shell) can be left orphaned and keep running.
This module provides a cross-platform process-tree kill, mirroring the C#
`Process.Kill(entireProcessTree: true)` behaviour.

- Windows: `taskkill /F /T /PID <pid>`
- POSIX:   kill the whole process group via `os.killpg` (requires the child to
           be started in its own session/group).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from typing import Any


def _kill_tree_windows(pid: int) -> None:
    """Force-kills a process and all its descendants on Windows."""
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception:
        # Fallback: best-effort direct kill if taskkill is unavailable.
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass


def _kill_tree_posix(pid: int) -> None:
    """Kills the process group of `pid` on POSIX systems."""
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return
    except Exception:
        pgid = None

    try:
        if pgid is not None:
            os.killpg(pgid, signal.SIGKILL)
        else:
            os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except Exception:
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass


def kill_process_tree(pid: int) -> None:
    """Kills a process and its entire descendant tree (cross-platform)."""
    if pid is None or pid <= 0:
        return
    if sys.platform == "win32":
        _kill_tree_windows(pid)
    else:
        _kill_tree_posix(pid)


def _popen_kwargs_for_group() -> dict[str, Any]:
    """Returns Popen kwargs that place the child in its own process group/session."""
    if sys.platform == "win32":
        # CREATE_NEW_PROCESS_GROUP lets us target the whole tree via taskkill /T.
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    # start_new_session=True => new process group => os.killpg works.
    return {"start_new_session": True}


def run_with_tree_kill(
    command: str | list[str],
    *,
    cwd: str | None = None,
    timeout: float | None = None,
    shell: bool = False,
    text: bool = True,
    encoding: str = "utf-8",
    errors: str = "replace",
) -> tuple[int, str, str, bool]:
    """Runs a command and guarantees the whole process tree is killed on timeout.

    Returns (returncode, stdout, stderr, timed_out).
    On timeout, returncode is -1 and timed_out is True.
    """
    kwargs: dict[str, Any] = {
        "cwd": cwd,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": text,
        "encoding": encoding,
        "errors": errors,
        "shell": shell,
    }
    kwargs.update(_popen_kwargs_for_group())

    proc = subprocess.Popen(command, **kwargs)
    try:
        out, err = proc.communicate(timeout=timeout)
        return proc.returncode, out or "", err or "", False
    except subprocess.TimeoutExpired:
        kill_process_tree(proc.pid)
        # Drain whatever was captured before the kill.
        try:
            out, err = proc.communicate(timeout=5)
        except Exception:
            out, err = "", ""
        return -1, out or "", err or "", True
