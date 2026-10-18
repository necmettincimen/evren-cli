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
import queue
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Generator


# pid -> Windows job handle. `taskkill /T` misses MSYS (Git Bash) children: a
# timed-out `bash -c "sleep 40"` left sleep.exe running, and its open stdout
# pipe made the kill path wait another 5 s. Every process started in the job
# (grandchildren included) dies with TerminateJobObject.
_JOBS: dict[int, int] = {}


def attach_job(proc) -> None:
    """Puts a freshly started Windows process into its own job object."""
    if sys.platform != "win32" or proc is None:
        return
    try:
        import ctypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = ctypes.c_void_p
        k32.OpenProcess.restype = ctypes.c_void_p
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return
        handle = k32.OpenProcess(0x0101, False, proc.pid)  # TERMINATE | SET_QUOTA
        ok = bool(handle) and k32.AssignProcessToJobObject(ctypes.c_void_p(job), ctypes.c_void_p(handle))
        if handle:
            k32.CloseHandle(ctypes.c_void_p(handle))
        if ok:
            _JOBS[proc.pid] = job
        else:
            k32.CloseHandle(ctypes.c_void_p(job))
    except Exception:
        pass


def release_job(pid: int) -> None:
    """Closes the job handle of a finished process (its children keep running)."""
    job = _JOBS.pop(pid, None)
    if job:
        try:
            import ctypes

            ctypes.WinDLL("kernel32").CloseHandle(ctypes.c_void_p(job))
        except Exception:
            pass


def _terminate_job(pid: int) -> bool:
    job = _JOBS.pop(pid, None)
    if not job:
        return False
    try:
        import ctypes

        k32 = ctypes.WinDLL("kernel32")
        k32.TerminateJobObject(ctypes.c_void_p(job), 1)
        k32.CloseHandle(ctypes.c_void_p(job))
        return True
    except Exception:
        return False


def _kill_tree_windows(pid: int) -> None:
    """Force-kills a process and all its descendants on Windows."""
    _terminate_job(pid)
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
    attach_job(proc)
    try:
        out, err = proc.communicate(timeout=timeout)
        release_job(proc.pid)
        return proc.returncode, out or "", err or "", False
    except KeyboardInterrupt:
        kill_process_tree(proc.pid)
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
        raise
    except subprocess.TimeoutExpired:
        kill_process_tree(proc.pid)
        # Drain whatever was captured before the kill.
        try:
            out, err = proc.communicate(timeout=5)
        except Exception:
            out, err = "", ""
        return -1, out or "", err or "", True


def _reader_thread(stream, sink: "queue.Queue[tuple[str, str | None]]", tag: str) -> None:
    """Reads lines from `stream` and pushes (tag, line) tuples into `sink`.

    A final (tag, None) sentinel is pushed when the stream reaches EOF so the
    consumer knows this stream is finished.
    """
    try:
        for line in iter(stream.readline, ""):
            sink.put((tag, line))
    except Exception:
        pass
    finally:
        try:
            stream.close()
        except Exception:
            pass
        sink.put((tag, None))


def stream_with_tree_kill(
    command: str | list[str],
    *,
    cwd: str | None = None,
    timeout: float | None = None,
    shell: bool = False,
    encoding: str = "utf-8",
    errors: str = "replace",
    env: dict[str, str] | None = None,
) -> Generator[tuple[str, str], None, tuple[int, str, str, bool]]:
    """Runs a command and yields output lines live as they are produced.

    Yields ``(stream_name, line)`` tuples where ``stream_name`` is ``"stdout"``
    or ``"stderr"`` and ``line`` includes its trailing newline. This lets the
    caller render command output in real time instead of waiting for the whole
    process to finish.

    The generator's return value (via ``StopIteration.value``) is the same
    ``(returncode, stdout, stderr, timed_out)`` tuple as ``run_with_tree_kill``,
    so callers can still capture the full output and exit status.

    On timeout the whole process tree is killed (grandchildren included).
    """
    kwargs: dict[str, Any] = {
        "cwd": cwd,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": encoding,
        "errors": errors,
        "shell": shell,
        "bufsize": 1,  # line-buffered so output arrives promptly
    }
    if env:
        kwargs["env"] = {**os.environ, **env}
    kwargs.update(_popen_kwargs_for_group())

    proc = subprocess.Popen(command, **kwargs)
    attach_job(proc)

    sink: "queue.Queue[tuple[str, str | None]]" = queue.Queue()
    threads = [
        threading.Thread(target=_reader_thread, args=(proc.stdout, sink, "stdout"), daemon=True),
        threading.Thread(target=_reader_thread, args=(proc.stderr, sink, "stderr"), daemon=True),
    ]
    for t in threads:
        t.start()

    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    open_streams = 2
    timed_out = False
    deadline = (time.monotonic() + timeout) if timeout else None

    try:
        while open_streams > 0:
            # Compute how long we may block waiting for the next line.
            wait = 0.2
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                wait = min(wait, remaining)

            try:
                tag, line = sink.get(timeout=wait)
            except queue.Empty:
                # No output yet; check whether the process already exited.
                if proc.poll() is not None and sink.empty():
                    break
                continue

            if line is None:
                open_streams -= 1
                continue

            if tag == "stdout":
                stdout_parts.append(line)
            else:
                stderr_parts.append(line)
            yield tag, line

        if timed_out:
            kill_process_tree(proc.pid)

        # Wait for the process to finish (or be reaped after the kill).
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            kill_process_tree(proc.pid)
            try:
                proc.wait(timeout=5)
            except Exception:
                pass

        # Drain any remaining buffered lines so nothing is lost.
        while True:
            try:
                tag, line = sink.get_nowait()
            except queue.Empty:
                break
            if line is None:
                continue
            if tag == "stdout":
                stdout_parts.append(line)
            else:
                stderr_parts.append(line)
            yield tag, line

    except KeyboardInterrupt:
        kill_process_tree(proc.pid)
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
        raise
    finally:
        for t in threads:
            t.join(timeout=1)

    if timed_out:
        return -1, "".join(stdout_parts), "".join(stderr_parts), True
    return proc.returncode, "".join(stdout_parts), "".join(stderr_parts), False
