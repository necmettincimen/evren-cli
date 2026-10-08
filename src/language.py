"""
Language mode for EVREN CLI.

Ports the Foreman "language mode" idea: the assistant keeps thinking in code
and English internally, but produces its prose in the user's chosen language.
The setting is persisted in the project memory profile so it survives sessions.

This module only builds the instruction block; the caller injects it into the
agent system prompt.
"""

from __future__ import annotations

from pathlib import Path

from src.memory import load_memory, set_profile

# Common aliases -> canonical display name.
_LANGUAGE_ALIASES = {
    "tr": "Türkçe",
    "turkish": "Türkçe",
    "türkçe": "Türkçe",
    "turkce": "Türkçe",
    "en": "English",
    "english": "English",
    "ingilizce": "English",
    "de": "Deutsch",
    "german": "Deutsch",
    "almanca": "Deutsch",
    "fr": "Français",
    "french": "Français",
    "fransızca": "Français",
    "es": "Español",
    "spanish": "Español",
    "ispanyolca": "Español",
}


def resolve_language(name: str) -> str:
    """Maps an alias/code to a canonical language name (or the input as-is)."""
    key = (name or "").strip().lower()
    if not key:
        return ""
    return _LANGUAGE_ALIASES.get(key, name.strip())


def get_language(workspace_root: Path | None = None) -> str:
    """Returns the persisted output language, or '' when unset."""
    state = load_memory(workspace_root)
    return state.get("profile", {}).get("language", "")


def set_language(name: str, workspace_root: Path | None = None) -> str:
    """Persists the output language and returns the canonical name."""
    canonical = resolve_language(name)
    if not canonical:
        raise ValueError("Dil adı boş olamaz.")
    set_profile("language", canonical, workspace_root)
    return canonical


def language_context_block(workspace_root: Path | None = None) -> str:
    """Builds the instruction block for the agent system prompt.

    Returns '' when no language is set (default behaviour unchanged).
    """
    lang = get_language(workspace_root)
    if not lang:
        return ""
    return (
        f"DİL MODU: Tüm açıklama ve raporlarını {lang} dilinde yaz. "
        "Kod, tanımlayıcılar ve teknik terimler İngilizce kalır; "
        "yalnızca kullanıcıya dönük düzyazı seçilen dilde olur."
    )
