"""
Single source of truth for the EVREN CLI version.

The version is read from the installed package metadata (pyproject.toml),
falling back to a hardcoded value when the package is not installed
(e.g. running directly from a source checkout).
"""

import re
from pathlib import Path

_FALLBACK_VERSION = "3.7.0"

# CHANGELOG.md lives at the repository root (one level above the src package).
_CHANGELOG_PATH = Path(__file__).resolve().parents[1] / "CHANGELOG.md"

_PYPROJECT_PATH = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _source_checkout_version() -> str | None:
    """Version from pyproject.toml when running from a source checkout.

    An editable install (`pip install -e .`) freezes the metadata version at
    install time, so after every bump `evren --version` and the banner kept
    showing the old number (1.0.1 while the code was 2.0.0) and looked like
    the update had not been applied.
    """
    try:
        text = _PYPROJECT_PATH.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else None


def _installed_version() -> str | None:
    try:
        from importlib.metadata import version as _pkg_version, PackageNotFoundError
    except ImportError:  # pragma: no cover - Python < 3.8
        return None
    try:
        return _pkg_version("evren-cli")
    except PackageNotFoundError:
        return None


__version__ = _source_checkout_version() or _installed_version() or _FALLBACK_VERSION


def get_version() -> str:
    """Returns the current EVREN CLI version string."""
    return __version__


def get_changelog_notes(version: str | None = None, max_items: int = 6) -> list[str]:
    """Returns the changelog bullet points for the given (or current) version.

    Parses CHANGELOG.md and extracts the list items under the matching
    ``## [x.y.z]`` heading. Returns an empty list if the file or section
    cannot be found.
    """
    target = version or __version__
    try:
        text = _CHANGELOG_PATH.read_text(encoding="utf-8")
    except OSError:
        return []

    # Find the heading for the requested version, e.g. "## [0.1.0] - 2026-10-04"
    heading_re = re.compile(
        r"^##\s+\[" + re.escape(target) + r"\].*$", re.MULTILINE
    )
    match = heading_re.search(text)
    if not match:
        return []

    # Collect lines until the next "## " heading.
    section = text[match.end():]
    next_heading = re.search(r"^##\s+", section, re.MULTILINE)
    if next_heading:
        section = section[: next_heading.start()]

    notes: list[str] = []
    for line in section.splitlines():
        stripped = line.strip()
        if stripped.startswith(("- ", "* ")):
            notes.append(stripped[2:].strip())
            if len(notes) >= max_items:
                break
    return notes
