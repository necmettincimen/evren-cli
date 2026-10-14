"""
Marker-based managed-section merging for EVREN CLI smart context docs.

The CLI keeps two living documents in the *project* directory (`agent.md`,
`changelog.md`) and a few support documents in its own home directory
(`skills.md`, `lessons.md`, `commands.md`). Those files are shared with humans:
people write free-form notes in them and the CLI must never destroy that text.

This module is the safe merge core. A document is split into ordinary text and
*managed sections* delimited by HTML-comment markers:

    <!-- evren:managed:start section=runtime_env -->
    ...content the CLI owns and may rewrite...
    <!-- evren:managed:end section=runtime_env -->

Rules enforced here (pure string handling, no file I/O):

- Only the inside of a managed section is ever rewritten; everything outside is
  copied byte-for-byte.
- A document without markers is untouchable: `render_sections` reports no change.
- Malformed markers (nested, unknown end, unclosed) make the whole document
  read-only for this run instead of risking a partial rewrite.
- CRLF / LF and a leading BOM are preserved.

Hashing helpers let callers skip writing when nothing actually changed, so
repeated CLI runs leave `git status` clean.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

MARKER_PREFIX = "evren:managed"
SECTION_NAME_MAX = 40

SECTION_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_MARKER_RE = re.compile(
    r"^[ \t]*<!--[ \t]*evren:managed:(start|end)[ \t]+section=([A-Za-z0-9_-]+)[ \t]*-->[ \t]*$"
)

# Returned by render/strip helpers when the document must not be touched.
UNTOUCHABLE = "dokümanda yönetilen blok yok"
MALFORMED = "bozuk iç içe yönetilen blok işareti"


class MergeError(ValueError):
    """Raised only for programmer errors (bad section name), never for bad input docs."""


@dataclass(frozen=True)
class SectionSpan:
    """Line span of one managed section (start/end marker lines included)."""

    name: str
    start_line: int  # index of the start marker line
    end_line: int    # index of the end marker line
    body: tuple[str, ...]

    @property
    def body_text(self) -> str:
        return "\n".join(self.body)


def is_valid_section_name(name: str) -> bool:
    """Section names are lowercase, short, and marker-safe."""
    return bool(SECTION_NAME_RE.match(name or ""))


def start_marker(name: str) -> str:
    if not is_valid_section_name(name):
        raise MergeError(f"Geçersiz bölüm adı: {name!r}")
    return f"<!-- {MARKER_PREFIX}:start section={name} -->"


def end_marker(name: str) -> str:
    if not is_valid_section_name(name):
        raise MergeError(f"Geçersiz bölüm adı: {name!r}")
    return f"<!-- {MARKER_PREFIX}:end section={name} -->"


def wrap_section(name: str, body: str) -> str:
    """Builds one complete managed section block (no trailing newline)."""
    lines = [start_marker(name)]
    lines.extend(_body_lines(body))
    lines.append(end_marker(name))
    return "\n".join(lines)


def section_hash(body: str) -> str:
    """Stable content fingerprint used to skip no-op writes.

    Normalized exactly like a rendered body (newlines + blank padding), so the
    hash of a produced body matches the hash of the same body read back.
    """
    normalized = "\n".join(_body_lines(body))
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def normalize_newlines(text: str) -> str:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


def _body_lines(body: str) -> list[str]:
    """Normalizes a body into marker-adjacent lines without blank padding."""
    lines = normalize_newlines(body or "").split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def _split(text: str) -> tuple[list[str], str, bool]:
    """Splits text into lines plus the newline/BOM style needed to rebuild it."""
    raw = text or ""
    has_bom = raw.startswith("\ufeff")
    body = raw[1:] if has_bom else raw
    newline = "\r\n" if "\r\n" in body else "\n"
    lines = normalize_newlines(body).split("\n")
    return lines, newline, has_bom


def _join(lines: list[str], newline: str, has_bom: bool) -> str:
    text = newline.join(lines)
    return ("\ufeff" + text) if has_bom else text


def parse_sections(text: str) -> tuple[list[SectionSpan], list[str]]:
    """Returns (sections, errors). Errors mean the document is read-only."""
    lines, _newline, _bom = _split(text)
    sections: list[SectionSpan] = []
    errors: list[str] = []
    open_name: str | None = None
    open_index = -1

    for index, line in enumerate(lines):
        match = _MARKER_RE.match(line)
        if not match:
            continue
        kind, name = match.group(1), match.group(2)
        if kind == "start":
            if open_name is not None:
                errors.append(
                    f"satır {index + 1}: '{name}' bloğu '{open_name}' bloğu içinde açıldı"
                )
                return [], errors
            if not is_valid_section_name(name):
                errors.append(f"satır {index + 1}: geçersiz bölüm adı '{name}'")
                return [], errors
            if any(section.name == name for section in sections):
                errors.append(f"satır {index + 1}: '{name}' bölümü tekrar ediyor")
                return [], errors
            open_name, open_index = name, index
            continue

        # kind == "end"
        if open_name is None:
            errors.append(f"satır {index + 1}: açık blok olmadan '{name}' kapatıldı")
            return [], errors
        if name != open_name:
            errors.append(
                f"satır {index + 1}: '{name}' kapatıldı ama açık blok '{open_name}'"
            )
            return [], errors
        sections.append(
            SectionSpan(
                name=open_name,
                start_line=open_index,
                end_line=index,
                body=tuple(lines[open_index + 1:index]),
            )
        )
        open_name, open_index = None, -1

    if open_name is not None:
        errors.append(f"satır {open_index + 1}: '{open_name}' bloğu kapatılmadı")
        return [], errors
    return sections, errors


def has_managed_sections(text: str) -> bool:
    """True when the document already carries CLI-managed markers."""
    sections, errors = parse_sections(text)
    return bool(sections) and not errors


def extract_sections(text: str) -> dict[str, str]:
    """Maps section name -> body text. Empty dict when markers are malformed."""
    sections, errors = parse_sections(text)
    if errors:
        return {}
    return {section.name: section.body_text for section in sections}


def render_sections(
    text: str,
    sections: dict[str, str],
    *,
    adopt: bool = False,
    adopt_order: tuple[str, ...] | None = None,
) -> tuple[str, list[str], list[str]]:
    """Replaces managed section bodies; optionally appends missing ones.

    Returns (new_text, changed_names, errors). A non-empty `errors` list means
    `new_text` equals the input and the caller must not write.
    """
    requested = {name: body for name, body in (sections or {}).items() if name}
    for name in requested:
        if not is_valid_section_name(name):
            raise MergeError(f"Geçersiz bölüm adı: {name!r}")

    existing, errors = parse_sections(text)
    if errors:
        return text, [], [MALFORMED]
    spans = {section.name: section for section in existing}

    if not spans and not adopt:
        return text, [], [UNTOUCHABLE]
    if not requested and not adopt:
        return text, [], []

    lines, newline, has_bom = _split(text)
    changed: list[str] = []

    # Replace from the last section backwards so earlier indices stay valid.
    for name in sorted(spans, key=lambda key: spans[key].start_line, reverse=True):
        if name not in requested:
            continue
        span = spans[name]
        new_body = _body_lines(requested[name])
        old_body = list(span.body)
        if old_body == new_body:
            continue
        lines[span.start_line + 1:span.end_line] = new_body
        changed.append(name)

    # Already-managed documents accept new sections (a newer CLI version may add
    # one). Unmanaged documents only gain markers when the user opted in.
    missing = [name for name in requested if name not in spans]
    if missing:
        order = list(adopt_order or ()) + [
            name for name in sorted(missing) if name not in (adopt_order or ())
        ]
        for name in order:
            if name not in missing:
                continue
            block = [start_marker(name)]
            block.extend(_body_lines(requested[name]))
            block.append(end_marker(name))
            _append_block(lines, block)
            changed.append(name)

    if not changed:
        return text, [], []
    return _join(lines, newline, has_bom), sorted(changed), []


def _append_block(lines: list[str], block: list[str]) -> None:
    """Appends a marker block, keeping exactly one blank line before it."""
    while lines and not lines[-1].strip():
        lines.pop()
    if lines:
        lines.append("")
    lines.extend(block)
    lines.append("")


def strip_managed(text: str) -> tuple[str, list[str], list[str]]:
    """Removes managed blocks (markers + their bodies). Used by `/docs unmark`."""
    existing, errors = parse_sections(text)
    if errors:
        return text, [], [MALFORMED]
    if not existing:
        return text, [], []

    lines, newline, has_bom = _split(text)
    removed = sorted(section.name for section in existing)
    for section in sorted(existing, key=lambda item: item.start_line, reverse=True):
        del lines[section.start_line:section.end_line + 1]

    # Collapse the blank-line runs the removals leave behind.
    cleaned: list[str] = []
    for line in lines:
        if not line.strip() and cleaned and not cleaned[-1].strip():
            continue
        cleaned.append(line)
    while cleaned and not cleaned[-1].strip():
        cleaned.pop()
    if cleaned:
        cleaned.append("")
    return _join(cleaned, newline, has_bom), removed, []
