"""Tiny mtime-keyed cache for small config files read on every request.

Settings, agent definitions and similar files were parsed again for each
model request and each tool call. The cache returns the parsed value while the
file's (mtime, size) is unchanged, so edits are still picked up immediately.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

_CACHE: dict[tuple[str, str], tuple[tuple, Any]] = {}


def _stamp(path: Path) -> tuple:
    try:
        st = path.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return ("missing",)


def cached(kind: str, path: Path, loader: Callable[[Path], Any]) -> Any:
    """`loader(path)` result, re-run only when the file changed."""
    key = (kind, str(path))
    stamp = _stamp(path)
    hit = _CACHE.get(key)
    if hit and hit[0] == stamp:
        return hit[1]
    value = loader(path)
    _CACHE[key] = (stamp, value)
    return value


def dir_stamp(directory: Path) -> tuple:
    """Changes when any *.md file in `directory` is added, removed or edited."""
    try:
        return tuple(sorted((p.name, *_stamp(p)) for p in directory.glob("*.md")))
    except OSError:
        return ()


def reset_cache() -> None:
    _CACHE.clear()
