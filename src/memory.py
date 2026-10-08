"""
Persistent project memory for EVREN CLI.

Ports the Foreman "memory" idea to a coding assistant: a small, versioned JSON
store under `.evren/memory.json` that survives across sessions. It holds the
project profile (stack, conventions, goals) and free-form notes the agent can
recall, so the assistant does not start from scratch every run.

The store is intentionally simple and dependency-free. It is written atomically
and kept out of version control via the `.evren/.gitignore` created by config.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from src.config import get_evren_dir, get_workspace_dir

SCHEMA_VERSION = 1

# Profile keys the agent may set. Kept small and explicit to avoid drift.
PROFILE_KEYS = (
    "project_name",
    "stack",
    "language",
    "conventions",
    "goal",
)


def _memory_path(workspace_root: Path | None = None) -> Path:
    return get_evren_dir(workspace_root) / "memory.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _empty_state() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "profile": {},
        "notes": [],
        "updated_at": _now(),
    }


def load_memory(workspace_root: Path | None = None) -> dict:
    """Loads the memory store, returning an empty state if missing/invalid."""
    path = _memory_path(workspace_root)
    if not path.exists():
        return _empty_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return _empty_state()
    if not isinstance(data, dict):
        return _empty_state()
    # Normalize shape so callers can rely on the keys.
    state = _empty_state()
    state["profile"] = data.get("profile", {}) if isinstance(data.get("profile"), dict) else {}
    state["notes"] = data.get("notes", []) if isinstance(data.get("notes"), list) else []
    state["schema_version"] = data.get("schema_version", SCHEMA_VERSION)
    state["updated_at"] = data.get("updated_at", state["updated_at"])
    return state


def save_memory(state: dict, workspace_root: Path | None = None) -> Path:
    """Atomically writes the memory store and returns its path."""
    path = _memory_path(workspace_root)
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


def set_profile(key: str, value: str, workspace_root: Path | None = None) -> dict:
    """Sets a profile field and persists. Unknown keys are still stored."""
    state = load_memory(workspace_root)
    key = (key or "").strip()
    if not key:
        raise ValueError("Profil anahtarı boş olamaz.")
    state["profile"][key] = (value or "").strip()
    save_memory(state, workspace_root)
    return state


def add_note(text: str, workspace_root: Path | None = None) -> dict:
    """Appends a timestamped note and persists."""
    text = (text or "").strip()
    if not text:
        raise ValueError("Not boş olamaz.")
    state = load_memory(workspace_root)
    state["notes"].append({"text": text, "at": _now()})
    save_memory(state, workspace_root)
    return state


def clear_memory(workspace_root: Path | None = None) -> dict:
    """Resets the store to an empty state and persists."""
    state = _empty_state()
    save_memory(state, workspace_root)
    return state


def memory_context_block(workspace_root: Path | None = None, max_notes: int = 10) -> str:
    """Builds a compact context block to inject into the agent system prompt.

    Returns an empty string when there is nothing worth recalling.
    """
    state = load_memory(workspace_root)
    profile = state.get("profile", {})
    notes = state.get("notes", [])
    if not profile and not notes:
        return ""

    lines = ["PROJE HAFIZASI (önceki oturumlardan):"]
    for key in PROFILE_KEYS:
        if profile.get(key):
            lines.append(f"  {key}: {profile[key]}")
    # Include any extra profile keys the user added.
    for key, value in profile.items():
        if key not in PROFILE_KEYS and value:
            lines.append(f"  {key}: {value}")
    if notes:
        lines.append("  Notlar:")
        for note in notes[-max_notes:]:
            lines.append(f"    - {note.get('text', '')}")
    return "\n".join(lines)
