"""
Implementation tracking for EVREN CLI.

Ports the Foreman "implementation tracking" idea: every confirmed task becomes
a trackable item with a status, progress and history. This closes the gap
between "the agent suggested it" and "it actually got done".

Stored as versioned JSON under `.evren/tasks.json`. Pure stdlib, atomic writes.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from src.config import get_evren_dir

SCHEMA_VERSION = 1

# Six states, mirroring Foreman's implementation lifecycle.
STATUSES = (
    "not-started",
    "in-progress",
    "blocked",
    "completed",
    "abandoned",
    "deferred",
)

# Statuses considered "open" (still need attention).
OPEN_STATUSES = ("not-started", "in-progress", "blocked", "deferred")


def _tasks_path(workspace_root: Path | None = None) -> Path:
    return get_evren_dir(workspace_root) / "tasks.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _empty_state() -> dict:
    return {"schema_version": SCHEMA_VERSION, "items": [], "updated_at": _now()}


def load_tasks(workspace_root: Path | None = None) -> dict:
    """Loads the task store, returning an empty state if missing/invalid."""
    path = _tasks_path(workspace_root)
    if not path.exists():
        return _empty_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return _empty_state()
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return _empty_state()
    state = _empty_state()
    state["items"] = data["items"]
    state["schema_version"] = data.get("schema_version", SCHEMA_VERSION)
    state["updated_at"] = data.get("updated_at", state["updated_at"])
    return state


def save_tasks(state: dict, workspace_root: Path | None = None) -> Path:
    """Atomically writes the task store and returns its path."""
    path = _tasks_path(workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    state = dict(state)
    state["schema_version"] = SCHEMA_VERSION
    state["updated_at"] = _now()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)
    return path


def _find(state: dict, task_id: str) -> dict | None:
    for item in state["items"]:
        if item.get("id") == task_id:
            return item
    return None


def add_task(
    task_id: str,
    description: str,
    deadline: str = "",
    workspace_root: Path | None = None,
) -> dict:
    """Adds a new task. Raises ValueError on empty/duplicate id."""
    task_id = (task_id or "").strip()
    description = (description or "").strip()
    if not task_id:
        raise ValueError("Görev kimliği (id) boş olamaz.")
    if not description:
        raise ValueError("Görev açıklaması boş olamaz.")

    state = load_tasks(workspace_root)
    if _find(state, task_id):
        raise ValueError(f"'{task_id}' kimliği zaten var.")

    item = {
        "id": task_id,
        "description": description,
        "status": "not-started",
        "progress": 0,
        "deadline": (deadline or "").strip(),
        "created_at": _now(),
        "updated_at": _now(),
        "history": [{"at": _now(), "event": "created"}],
    }
    state["items"].append(item)
    save_tasks(state, workspace_root)
    return item


def update_task(
    task_id: str,
    status: str | None = None,
    progress: int | None = None,
    note: str = "",
    workspace_root: Path | None = None,
) -> dict:
    """Updates a task's status/progress and appends a history entry."""
    state = load_tasks(workspace_root)
    item = _find(state, task_id)
    if item is None:
        raise ValueError(f"Görev bulunamadı: {task_id}")

    if status is not None:
        status = status.strip().lower()
        if status not in STATUSES:
            raise ValueError(
                f"Geçersiz durum: '{status}'. Geçerli: {', '.join(STATUSES)}"
            )
        item["status"] = status
        if status == "completed":
            item["progress"] = 100

    if progress is not None:
        item["progress"] = max(0, min(100, int(progress)))
        if item["progress"] == 100 and item.get("status") != "completed":
            item["status"] = "completed"
        elif item["progress"] > 0 and item.get("status") == "not-started":
            item["status"] = "in-progress"

    item["updated_at"] = _now()
    event = {"at": _now(), "event": "updated"}
    if status:
        event["status"] = status
    if progress is not None:
        event["progress"] = item["progress"]
    if note:
        event["note"] = note.strip()
    item.setdefault("history", []).append(event)

    save_tasks(state, workspace_root)
    return item


def list_tasks(filter_by: str = "all", workspace_root: Path | None = None) -> list[dict]:
    """Lists tasks. `filter_by` is 'all', 'open', or one of STATUSES."""
    state = load_tasks(workspace_root)
    items = state["items"]
    key = (filter_by or "all").strip().lower()
    if key == "all":
        return items
    if key == "open":
        return [i for i in items if i.get("status") in OPEN_STATUSES]
    return [i for i in items if i.get("status") == key]


def remove_task(task_id: str, workspace_root: Path | None = None) -> bool:
    """Removes a task by id. Returns True if something was removed."""
    state = load_tasks(workspace_root)
    before = len(state["items"])
    state["items"] = [i for i in state["items"] if i.get("id") != task_id]
    if len(state["items"]) == before:
        return False
    save_tasks(state, workspace_root)
    return True


def progress_summary(workspace_root: Path | None = None) -> dict:
    """Returns counts per status plus overall completion percentage."""
    state = load_tasks(workspace_root)
    items = state["items"]
    counts = {s: 0 for s in STATUSES}
    for item in items:
        status = item.get("status", "not-started")
        if status in counts:
            counts[status] += 1
    total = len(items)
    completed = counts.get("completed", 0)
    percent = round((completed / total) * 100) if total else 0
    return {"total": total, "counts": counts, "percent": percent}
