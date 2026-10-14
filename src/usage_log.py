"""Best-effort API usage logging (plan token-consumption-fixes, Faz 0 / F7).

One JSON line is appended to `.evren/logs/usage.jsonl` for every API response that
reports usage. The record carries **token counters only**: message content, tool
arguments, file paths and API keys are never written here, so the log is safe to
keep on disk (`.evren/.gitignore` already excludes `logs/`).

Every function is best-effort: a logging failure must never break an agent turn.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

USAGE_FILE_NAME = "usage.jsonl"
ARCHIVE_FILE_NAME = "usage.jsonl.1"

# Rotate once the active log reaches this many lines.
MAX_LINES = 5000

# The only fields ever written. Anything else is dropped on purpose.
_FIELDS = (
    "ts",
    "model",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cached_tokens",
    "step",
    "mode",
)

# Line counts are cached per file so rotation does not re-read the log per request.
_line_counts: dict[str, int] = {}


def usage_log_path(workspace_root: Path | None = None) -> Path:
    """Returns `.evren/logs/usage.jsonl` for the given workspace."""
    from src.config import get_evren_dir

    return get_evren_dir(workspace_root) / "logs" / USAGE_FILE_NAME


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_int(value: Any) -> int | None:
    """Coerces a usage field to int; anything non-numeric becomes None."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def build_record(
    usage: Any,
    *,
    model: str = "",
    step: int | None = None,
    mode: str = "",
    ts: str | None = None,
) -> dict[str, Any]:
    """Builds a whitelisted usage record from a raw SDK `usage` object or dict."""
    def field(name: str) -> Any:
        if isinstance(usage, dict):
            return usage.get(name)
        return getattr(usage, name, None)

    total = _as_int(field("total_tokens"))
    prompt = _as_int(field("prompt_tokens"))
    completion = _as_int(field("completion_tokens"))
    if total is None and prompt is not None and completion is not None:
        total = prompt + completion
    # Prompt-cache hits (EVREN bills them at ~3%); shows whether the stable
    # system prefix is actually being reused between agent steps.
    details = field("prompt_tokens_details")
    if isinstance(details, dict):
        cached = _as_int(details.get("cached_tokens"))
    else:
        cached = _as_int(getattr(details, "cached_tokens", None))

    return {
        "ts": ts or _stamp(),
        "model": _redact(str(model))[:64],
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "cached_tokens": cached,
        "step": _as_int(step),
        "mode": _redact(str(mode))[:16],
    }


def _redact(value: str) -> str:
    """Runs free text through the shared secret masker (keys must never be logged)."""
    try:
        from src.workflow_state import redact_text

        return redact_text(value)
    except Exception:
        return value


def _rotate(path: Path) -> None:
    """Moves the active log to `.1`, replacing the previous archive."""
    try:
        archive = path.with_name(ARCHIVE_FILE_NAME)
        if archive.exists():
            archive.unlink()
        path.replace(archive)
    except OSError:
        pass
    _line_counts[str(path)] = 0


def log_usage(
    usage: Any,
    *,
    model: str = "",
    step: int | None = None,
    mode: str = "",
    workspace_root: Path | None = None,
) -> dict[str, Any] | None:
    """Appends one usage line. Returns the record, or None when nothing was usable."""
    try:
        record = build_record(usage, model=model, step=step, mode=mode)
        if all(record[name] is None for name in ("prompt_tokens", "completion_tokens", "total_tokens")):
            return None

        path = usage_log_path(workspace_root)
        path.parent.mkdir(parents=True, exist_ok=True)

        key = str(path)
        if key not in _line_counts:
            try:
                with path.open("r", encoding="utf-8") as handle:
                    _line_counts[key] = sum(1 for _ in handle)
            except OSError:
                _line_counts[key] = 0

        if _line_counts[key] >= MAX_LINES:
            _rotate(path)

        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        _line_counts[key] = _line_counts.get(key, 0) + 1
        return record
    except Exception:
        return None


def read_usage(workspace_root: Path | None = None, limit: int | None = None) -> list[dict[str, Any]]:
    """Reads the active log (newest last). Corrupt lines are skipped silently."""
    try:
        path = usage_log_path(workspace_root)
        with path.open("r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return []

    records: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            records.append({name: data.get(name) for name in _FIELDS})
    if limit is not None and limit >= 0:
        return records[-limit:]
    return records


def usage_summary(records: list[dict[str, Any]] | None = None, *, recent: int = 10) -> dict[str, Any]:
    """Aggregates usage records: totals plus the average of the last `recent` calls."""
    if records is None:
        records = read_usage()
    prompts = [r["prompt_tokens"] for r in records if isinstance(r.get("prompt_tokens"), int)]
    completions = [r["completion_tokens"] for r in records if isinstance(r.get("completion_tokens"), int)]
    totals = [r["total_tokens"] for r in records if isinstance(r.get("total_tokens"), int)]
    cached = sum(r["cached_tokens"] for r in records if isinstance(r.get("cached_tokens"), int))

    tail = prompts[-recent:] if prompts else []
    return {
        "cached_tokens": cached,
        "cache_ratio": round(cached / sum(prompts), 3) if prompts and sum(prompts) else 0.0,
        "requests": len(records),
        "prompt_tokens": sum(prompts),
        "completion_tokens": sum(completions),
        "total_tokens": sum(totals),
        "avg_prompt_tokens_recent": round(sum(tail) / len(tail)) if tail else 0,
        "recent_window": min(len(tail), recent),
    }


def reset_cache() -> None:
    """Drops the cached line counts (used by tests that move the workspace)."""
    _line_counts.clear()
