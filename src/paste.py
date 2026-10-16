"""
Big-paste handling.

Users paste whole build, test and scan outputs (hundreds of lines) as task text.
That text entered the history and was re-sent on every agent step, so one paste
could cost ~12K tokens per step. A paste above the configured limits
(EVREN_PASTE_MAX_LINES / EVREN_PASTE_MAX_CHARS) is saved to
`.evren/tmp/paste-N.txt` and the model gets a compact form instead: a marker
line, the first and last lines, the error-like lines from the middle, and a note
that the full text can be read with `view_file`.

The text must already be secret-masked (vault capture) when it gets here, so the
file on disk holds placeholders, never the secrets themselves.
"""

from __future__ import annotations

import re
from pathlib import Path

from src.config import ensure_evren_dirs, get_evren_dir, paste_max_chars, paste_max_lines

HEAD_LINES = 15
TAIL_LINES = 15
MAX_IMPORTANT_LINES = 60
LINE_CAP = 300
# Soft cap for the head + middle + tail body (about 3K tokens); the marker and
# the closing note come on top.
BODY_BUDGET = 10_000

# Case-insensitive; "high"/"critical" are severity words of scanners (npm audit,
# trivy), "warning CS" is the C# compiler warning prefix.
_IMPORTANT_RE = re.compile(
    r"error|fail|exception|traceback|warning CS|CVE-|critical|\bhigh\b|panic|fatal",
    re.IGNORECASE,
)
_PASTE_FILE_RE = re.compile(r"paste-(\d+)\.txt")


def _split_lines(text: str) -> tuple[str, list[str]]:
    """Normalizes line endings; returns (normalized text, its lines)."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    if len(lines) > 1 and lines[-1] == "":
        lines.pop()
    return normalized, lines


def _clip(line: str, cap: int = LINE_CAP) -> str:
    line = line.rstrip()
    if len(line) <= cap:
        return line
    return f"{line[:cap]}... [+{len(line) - cap} karakter]"


def _relative_path(path: Path) -> str:
    return f".evren/tmp/{path.name}"


def _next_number(tmp_dir: Path) -> int:
    highest = 0
    try:
        for entry in tmp_dir.iterdir():
            match = _PASTE_FILE_RE.fullmatch(entry.name)
            if match:
                highest = max(highest, int(match.group(1)))
    except OSError:
        pass
    return highest + 1


def _save_paste(normalized: str, workspace_root: Path) -> tuple[int, Path] | None:
    """Writes the full paste to the next free `paste-N.txt`; None when that fails."""
    try:
        # Also keeps `.evren/.gitignore` (which lists tmp/) up to date.
        ensure_evren_dirs(workspace_root)
        tmp_dir = get_evren_dir(workspace_root) / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        number = _next_number(tmp_dir)
        while True:
            path = tmp_dir / f"paste-{number}.txt"
            try:
                # "x" never overwrites a paste saved by a concurrent session.
                with open(path, "x", encoding="utf-8", newline="\n") as handle:
                    handle.write(normalized)
            except FileExistsError:
                number += 1
                continue
            return number, path
    except OSError:
        return None


def _important_lines(middle: list[str], seen: set[str]) -> list[str]:
    """Distinct error-like lines of the omitted middle, in their original order."""
    found: list[str] = []
    for line in middle:
        key = " ".join(line.split())
        if not key or key in seen or not _IMPORTANT_RE.search(line):
            continue
        seen.add(key)
        found.append(_clip(line))
    return found


def _render(lines: list[str], chars: int, number: int, rel_path: str) -> str:
    total = len(lines)
    marker = f"[Yapıştırılan metin #{number}: {total} satır, {chars} karakter → {rel_path}]"
    note = (
        f"[EVREN] Metnin tamamı {rel_path} dosyasında. Ayrıntı gerekirse view_file ile bu "
        "yoldan oku (satır aralığı vererek, bölüm bölüm); yapıştırılan metni yeniden yazma."
    )
    if total <= HEAD_LINES + TAIL_LINES:
        head, middle, tail = lines, [], []
    else:
        head, middle, tail = lines[:HEAD_LINES], lines[HEAD_LINES:-TAIL_LINES], lines[-TAIL_LINES:]

    parts = [marker]
    parts.append(f"İlk {len(head)} satır:" if tail else "Satırlar (uzun satırlar kısaltıldı):")
    parts.extend(_clip(line) for line in head)
    omitted_at = len(parts)
    if tail:
        parts.append(f"Son {len(tail)} satır:")
        parts.extend(_clip(line) for line in tail)
    parts.append(note)

    if middle:
        seen = {" ".join(line.split()) for line in (*head, *tail)}
        important = _important_lines(middle, seen)
        # Body = everything between the marker and the closing note.
        used = sum(len(part) + 1 for part in parts[1:-1])
        chosen: list[str] = []
        for line in important[:MAX_IMPORTANT_LINES]:
            if used + len(line) + 1 > BODY_BUDGET:
                break
            chosen.append(line)
            used += len(line) + 1
        header = f"... {len(middle)} satır atlandı."
        if chosen:
            header += f" Orta bölümdeki hata/uyarı satırları ({len(chosen)}/{len(important)}):"
        parts[omitted_at:omitted_at] = [header, *chosen]
    return "\n".join(parts)


def compact_paste(
    text: str,
    workspace_root: Path | str,
    *,
    max_lines: int | None = None,
    max_chars: int | None = None,
) -> tuple[str, Path | None]:
    """Compacts an oversized pasted task text.

    Returns (text_for_the_model, saved_file). Text within the limits comes back
    unchanged with None. Above them the full text is written to
    `<workspace>/.evren/tmp/paste-N.txt` and the compact form is returned with
    that path. If the file cannot be written (or the compact form would not be
    shorter), the original text is returned unchanged with None: never lose
    information silently.

    Pass the text after secret masking so the saved file holds no secrets.
    """
    if not text:
        return text, None
    limit_lines = paste_max_lines() if max_lines is None else max_lines
    limit_chars = paste_max_chars() if max_chars is None else max_chars
    normalized, lines = _split_lines(text)
    if len(lines) <= limit_lines and len(normalized) <= limit_chars:
        return text, None

    saved = _save_paste(normalized, Path(workspace_root))
    if saved is None:
        return text, None
    number, path = saved
    compact = _render(lines, len(normalized), number, _relative_path(path))
    if len(compact) >= len(text):
        try:
            path.unlink()
        except OSError:
            pass
        return text, None
    return compact, path


def paste_info(text: str, path: Path) -> str:
    """One-line Turkish notice shown to the user after a paste was compacted."""
    normalized, lines = _split_lines(text)
    return (
        f"Yapıştırılan metin ({len(lines)} satır, {len(normalized)} karakter) "
        f"{_relative_path(path)} dosyasına kaydedildi; modele özeti gidiyor "
        "(tamamı view_file ile okunabilir)."
    )
