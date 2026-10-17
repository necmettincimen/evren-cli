"""Per-task step trace (plan cli-stability-speed, Faz 0 / 0.3).

Every agent task appends a compact record stream to `.evren/logs/trace.jsonl`:
`task_start`, one `request` per model call, one `tool` per tool call and a
`task_end`. `evren debug last` (and the `/debug` REPL command) renders the last
task as a step table, so loops and wasted steps are found without hand-parsing
logs.

The trace carries counters, timings and short **masked** summaries only. Free
text goes through the active secret vault and the shared redactor before it is
written, and tool results are never stored (only their length). Every writer is
best-effort: a tracing failure must never break an agent turn.

Step numbers are task-wide: they count model requests, so the planner and
delegate loops that run inside one task continue the same sequence.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TRACE_FILE_NAME = "trace.jsonl"
ARCHIVE_FILE_NAME = "trace.jsonl.1"

# The active log is moved to `.1` (replacing it) when a task starts and the file
# is already larger than this.
MAX_BYTES = 5 * 1024 * 1024

PREVIEW_CHARS = 120
ARGS_CHARS = 160
_ARG_VALUE_CHARS = 80
# Text is capped before masking so a huge argument (a whole file body) stays cheap.
_SCRUB_CAP = 4000
_SIGNATURE_CAP = 200_000

# A tool result that begins like this counts as a failed call.
_ERROR_RE = re.compile(
    r"^\s*(?:HATA|Hata|hata|error|Error|ERROR|İPTAL|REDDEDİLDİ|TEKRAR ENGELLENDİ)\b"
)

_OUTCOME_LABELS = {
    "ok": "tamam",
    "error": "hata",
    "max_steps": "adım sınırı",
    "interrupted": "iptal",
    "exception": "beklenmeyen hata",
}


def trace_path(workspace_root: Path | None = None) -> Path:
    """Returns `.evren/logs/trace.jsonl` for the given workspace."""
    from src.config import get_evren_dir

    return get_evren_dir(workspace_root) / "logs" / TRACE_FILE_NAME


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# Masking
# --------------------------------------------------------------------------- #


def _scrub(value: Any, cap: int) -> str:
    """Caps `value` and passes it through the secret vault and the shared redactor."""
    text = "" if value is None else str(value)
    text = text[:cap]
    try:
        from src.secret_vault import active_vault

        text = active_vault().mask(text)
    except Exception:
        pass
    try:
        from src.workflow_state import redact_text

        return redact_text(text)
    except Exception:
        # Better an empty field than an unmasked one.
        return ""


def _clean(value: Any, limit: int) -> str:
    """Masked, single-line text of at most `limit` characters."""
    text = " ".join(_scrub(value, _SCRUB_CAP).split())
    if len(text) > limit:
        return text[: max(0, limit - 1)] + "…"
    return text


def summarize_args(args: Any) -> str:
    """Short masked `key=value` summary of tool arguments (at most ARGS_CHARS)."""
    if not isinstance(args, dict):
        return _clean(args, ARGS_CHARS)
    parts: list[str] = []
    for key, value in args.items():
        if isinstance(value, (list, tuple)):
            shown = f"[{len(value)}]"
        elif isinstance(value, dict):
            shown = "{" + str(len(value)) + "}"
        else:
            shown = _clean(value, _ARG_VALUE_CHARS)
        parts.append(shown if key == "command" else f"{key}={shown}")
    return _clean(" ".join(parts), ARGS_CHARS)


def _signature(name: str, args: Any) -> str:
    """Stable short hash of the masked call, used to count identical repeats."""
    try:
        raw = f"{name}:" + json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        raw = f"{name}:{args!r}"
    return hashlib.sha1(_scrub(raw, _SIGNATURE_CAP).encode("utf-8", "replace")).hexdigest()[:10]


def classify_outcome(result: str | None) -> str:
    """Maps the text a task returned to a machine-readable outcome."""
    if result is None:
        return "exception"
    if result.startswith("İPTAL"):
        return "interrupted"
    if result.startswith("HATA: Ajan maksimum adım"):
        return "max_steps"
    if result.startswith(("HATA", "Hata")):
        return "error"
    return "ok"


def _usage_fields(usage: Any) -> dict[str, int | None]:
    """Token counters of a server `usage` object; all None when it is missing."""
    empty: dict[str, int | None] = {
        "prompt_tokens": None, "cached_tokens": None, "completion_tokens": None,
    }
    if usage is None:
        return empty
    try:
        from src.usage_log import build_record

        record = build_record(usage)
    except Exception:
        return empty
    return {name: record.get(name) for name in empty}


def _rotate_if_needed(path: Path) -> None:
    """Moves an oversized active log to `.1`, replacing the previous archive."""
    try:
        if path.stat().st_size <= MAX_BYTES:
            return
    except OSError:
        return
    try:
        path.replace(path.with_name(ARCHIVE_FILE_NAME))
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Writer
# --------------------------------------------------------------------------- #


class TaskTrace:
    """Appends the trace records of one agent task. Never raises."""

    def __init__(self, workspace_root: Path | None = None, *, enabled: bool | None = None) -> None:
        from src.config import trace_enabled

        self.enabled = trace_enabled() if enabled is None else bool(enabled)
        self.path = trace_path(workspace_root)
        self.task_id = uuid.uuid4().hex[:10]
        self.step = 0          # model requests so far, failed attempts included
        self.requests = 0
        self.responses = 0     # requests that returned a response
        self._started = time.perf_counter()
        self._ended = False
        self._model = ""

    def _write(self, record: dict[str, Any]) -> None:
        if not self.enabled:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if record.get("type") == "task_start":
                _rotate_if_needed(self.path)
            line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line)
        except Exception:
            # OSError (read-only disk, locked file) or an unserialisable value:
            # losing one trace line is fine, breaking the agent turn is not.
            pass

    def start(self, *, model: str = "", mode: str = "", text: str = "") -> None:
        self._model = _clean(model, 64)
        self._write({
            "type": "task_start",
            "task_id": self.task_id,
            "ts": _stamp(),
            "model": self._model,
            "mode": _clean(mode, 16),
            "text_preview": _clean(text, PREVIEW_CHARS),
        })

    def request(
        self,
        usage: Any = None,
        *,
        ms: float = 0.0,
        finish_reason: Any = None,
        model: str = "",
        error: bool = False,
    ) -> None:
        """Records one model request; call it once per attempt, failed ones included."""
        try:
            self.step += 1
            self.requests += 1
            if not error:
                self.responses += 1
            reason = "error" if error else (
                _clean(finish_reason, 32) if isinstance(finish_reason, str) else None
            )
            self._write({
                "type": "request",
                "task_id": self.task_id,
                "step": self.step,
                "model": _clean(model, 64) if model else self._model,
                **_usage_fields(usage),
                "ms": int(ms),
                "finish_reason": reason,
            })
        except Exception:
            pass

    def tool(
        self,
        name: str,
        args: Any,
        *,
        ms: float = 0.0,
        result: Any = "",
        blocked: bool = False,
    ) -> None:
        """Records one tool call; `blocked` is set when the repeat guard refused it."""
        try:
            text = result if isinstance(result, str) else ("" if result is None else str(result))
            self._write({
                "type": "tool",
                "task_id": self.task_id,
                "step": self.step,
                "tool": _clean(name, 48),
                "args": summarize_args(args),
                "sig": _signature(str(name), args),
                "ms": int(ms),
                "result_chars": len(text),
                "blocked": bool(blocked),
                "ok": not blocked and not _ERROR_RE.match(text),
            })
        except Exception:
            pass

    def end(self, result: str | None = None) -> None:
        """Closes the task (once). `result=None` means an exception escaped."""
        if self._ended:
            return
        self._ended = True
        try:
            self._write({
                "type": "task_end",
                "task_id": self.task_id,
                "steps": self.responses,
                "requests": self.requests,
                "ms": int((time.perf_counter() - self._started) * 1000),
                "outcome": classify_outcome(result),
            })
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Reader and `debug last` renderer
# --------------------------------------------------------------------------- #


def _read_records(path: Path) -> list[dict[str, Any]]:
    """Reads a trace file; corrupt lines and foreign records are skipped silently."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
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
        if isinstance(data, dict) and isinstance(data.get("task_id"), str) and data.get("type"):
            records.append(data)
    return records


def _group_tasks(records: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Groups records by task id, ordered by each task's first record."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(record["task_id"], []).append(record)
    return list(grouped.values())


def read_tasks(workspace_root: Path | None = None, *, count: int = 1) -> list[list[dict[str, Any]]]:
    """Returns the traced tasks, oldest first; the archive is read only when needed."""
    path = trace_path(workspace_root)
    records = _read_records(path)
    tasks = _group_tasks(records)
    if len(tasks) < count:
        older = _read_records(path.with_name(ARCHIVE_FILE_NAME))
        if older:
            tasks = _group_tasks(older + records)
    return tasks


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def summarize_task(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregates one task's records into the numbers shown under the table."""
    start = next((r for r in records if r.get("type") == "task_start"), {})
    end = next((r for r in reversed(records) if r.get("type") == "task_end"), None)
    requests = [r for r in records if r.get("type") == "request"]
    tools = [r for r in records if r.get("type") == "tool"]

    prompts = [p for p in (_int_or_none(r.get("prompt_tokens")) for r in requests) if p is not None]
    prompt_total = sum(prompts) if prompts else None   # None: the server reported no usage
    pairs = [
        (_int_or_none(r.get("prompt_tokens")), _int_or_none(r.get("cached_tokens")))
        for r in requests
    ]
    pairs = [(p, c) for p, c in pairs if p is not None and c is not None]
    paired_prompt = sum(p for p, _ in pairs)
    cache_pct = round(100 * sum(c for _, c in pairs) / paired_prompt) if paired_prompt else None

    seen: set[tuple[Any, Any]] = set()
    repeated = 0
    for record in tools:
        key = (record.get("tool"), record.get("sig") or record.get("args"))
        if key in seen:
            repeated += 1
        else:
            seen.add(key)

    if end is not None and _int_or_none(end.get("ms")) is not None:
        total_ms = end["ms"]
    else:
        # Unfinished task: delegate rows already include their inner requests.
        total_ms = sum(_int_or_none(r.get("ms")) or 0 for r in requests) + sum(
            _int_or_none(r.get("ms")) or 0 for r in tools if r.get("tool") != "delegate"
        )

    return {
        "task_id": (records[0].get("task_id") if records else ""),
        "ts": start.get("ts") or "",
        "model": start.get("model") or "",
        "mode": start.get("mode") or "",
        "text_preview": start.get("text_preview") or "",
        "finished": end is not None,
        "outcome": (end or {}).get("outcome") or "",
        "requests": len(requests),
        "tool_calls": len(tools),
        "prompt_tokens": prompt_total,
        "cache_pct": cache_pct,
        "repeated": repeated,
        "blocked": sum(1 for r in tools if r.get("blocked")),
        "failed": sum(1 for r in tools if r.get("ok") is False and not r.get("blocked")),
        "total_ms": total_ms,
    }


def _fmt_int(value: Any) -> str:
    number = _int_or_none(value)
    return "—" if number is None else f"{number:,}"


def table_rows(records: list[dict[str, Any]]) -> list[tuple[str, str, str, str, str, str, str]]:
    """Table rows (step, kind, detail, ms, result chars, status, style) in trace order."""
    rows: list[tuple[str, str, str, str, str, str, str]] = []
    # The task model is in the header; a request row names its model only when it
    # differs (planner phase, routed executor).
    task_model = next((r.get("model") for r in records if r.get("type") == "task_start"), None)
    for record in records:
        kind = record.get("type")
        step = _fmt_int(record.get("step"))
        if kind == "request":
            prompt = _int_or_none(record.get("prompt_tokens"))
            if prompt is None:
                usage = "kullanım bilgisi yok"
            else:
                cached = _int_or_none(record.get("cached_tokens"))
                cache = f" (önbellek %{round(100 * cached / prompt)})" if cached is not None and prompt else ""
                usage = f"prompt {prompt:,}{cache} • çıktı {_fmt_int(record.get('completion_tokens'))}"
            model = str(record.get("model") or "")
            detail = " • ".join(
                part for part in (
                    model if model != task_model else "", usage, str(record.get("finish_reason") or ""),
                ) if part
            )
            rows.append((step, "istek", detail, _fmt_int(record.get("ms")), "", "", "dim"))
        elif kind == "tool":
            if record.get("blocked"):
                status, style = "ENGELLENDİ", "red"
            elif record.get("ok") is False:
                status, style = "hata", "yellow"
            else:
                status, style = "", ""
            rows.append((
                step,
                str(record.get("tool") or "?"),
                str(record.get("args") or ""),
                _fmt_int(record.get("ms")),
                _fmt_int(record.get("result_chars")),
                status,
                style,
            ))
    return rows


def summary_line(summary: dict[str, Any]) -> str:
    """The one-line totals shown under the table."""
    pct = summary["cache_pct"]
    seconds = (summary["total_ms"] or 0) / 1000
    outcome = _OUTCOME_LABELS.get(summary["outcome"], summary["outcome"]) if summary["finished"] else (
        "tamamlanmadı (görev sürüyor ya da süreç kapandı)"
    )
    return (
        f"Özet: {summary['requests']} istek • prompt {_fmt_int(summary['prompt_tokens'])} token • "
        f"önbellek {'—' if pct is None else f'%{pct}'} • "
        f"{summary['repeated']} tekrarlanan çağrı • {summary['blocked']} engellenen çağrı • "
        f"toplam {seconds:.1f} sn • sonuç: {outcome}"
    )


def header_line(summary: dict[str, Any]) -> str:
    parts = [f"Görev {summary['task_id']}"]
    for key in ("ts", "model"):
        if summary[key]:
            parts.append(summary[key])
    if summary["mode"]:
        parts.append(f"kip: {summary['mode']}")
    return " • ".join(parts)


def print_last_task(
    workspace_root: Path | None = None,
    task_index: int = 1,
    console: Any = None,
) -> bool:
    """Prints the step table of the `task_index`-th last task. Returns True when shown."""
    from src import ui

    if task_index < 1:
        ui.print_warning("Görev sırası 1 veya daha büyük olmalı (1 = son görev).")
        return False
    tasks = read_tasks(workspace_root, count=task_index)
    if not tasks:
        from src.config import trace_enabled

        hint = "" if trace_enabled() else " İz yazımı EVREN_TRACE=false ile kapalı."
        ui.print_info(
            "Henüz görev izi yok (.evren/logs/trace.jsonl bulunamadı veya boş). "
            f"Bir ajan görevi çalıştırınca adımlar burada görünür.{hint}"
        )
        return False
    if task_index > len(tasks):
        ui.print_warning(f"Yalnızca {len(tasks)} görev izi var; sondan {task_index}. görev bulunamadı.")
        return False

    records = tasks[-task_index]
    summary = summarize_task(records)
    rows = table_rows(records)
    header = header_line(summary)
    totals = summary_line(summary)
    preview = summary["text_preview"]

    target = console if console is not None else ui.console
    if not ui.HAVE_RICH or target is None:
        print(header)
        if preview:
            print(f"İstek: {preview}")
        print("Adım\tTür\tÖzet\tms\tSonuç kr.\tDurum")
        for step, kind, detail, ms, chars, status, _style in rows:
            print(f"{step}\t{kind}\t{detail}\t{ms}\t{chars}\t{status}")
        print(totals)
        return True

    from rich import box
    from rich.table import Table
    from rich.text import Text

    # Only the detail column may wrap, so a narrow terminal never squeezes the
    # step, time and size columns away. Cells are Text objects: args can hold
    # square brackets (`[REDACTED]`) that rich would otherwise read as markup.
    table = Table(box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False, expand=False)
    table.add_column("Adım", justify="right", no_wrap=True)
    table.add_column("Tür", no_wrap=True)
    table.add_column("Özet", overflow="fold", min_width=20, max_width=90)
    table.add_column("ms", justify="right", no_wrap=True)
    table.add_column("Sonuç kr.", justify="right", no_wrap=True)
    table.add_column("Durum", no_wrap=True)
    for step, kind, detail, ms, chars, status, style in rows:
        table.add_row(
            Text(step), Text(kind), Text(detail), Text(ms), Text(chars), Text(status), style=style or None,
        )
    target.print(Text(header, style="bold"))
    if preview:
        target.print(Text(f"İstek: {preview}", style="dim"))
    target.print(table)
    target.print(Text(totals, style="bold"))
    return True
