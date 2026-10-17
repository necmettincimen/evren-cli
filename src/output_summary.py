"""
Output-type aware summaries for long command results.

Pure functions, no I/O. `summarize_output(text, command)` returns a compact
summary for recognised output kinds (test runs, grep-like listings, builds) or
None when the output is not recognised, so the caller keeps its generic
head/tail truncation. The caller is responsible for saving the full text.
"""

from __future__ import annotations

import re
from collections import Counter

SUMMARY_MAX_CHARS = 3000
BUILD_MAX_CHARS = 3500
FAILURE_BLOCK_LINES = 15
TAIL_LINES = 20
BUILD_TAIL_LINES = 10
GREP_SHOWN_LINES = 80
GREP_MIN_LINES = 100
GREP_FILE_COUNTS_SHOWN = 40

_TEST_CMD = re.compile(
    r"(?i)\b(pytest|py\.test|unittest|jest|vitest|mocha|phpunit|rspec)\b"
    r"|\bdotnet\s+test\b|\bnpm\s+(run\s+)?test\b|\bnpx\s+(jest|vitest)\b"
    r"|\bgo\s+test\b|\bcargo\s+test\b|\bpnpm\s+(run\s+)?test\b|\byarn\s+test\b"
)
_TEST_MARKERS = re.compile(
    r"(?m)^=+ .*\b(passed|failed|error|errors|no tests ran)\b.* =+\s*$"
    r"|^Ran \d+ tests? in "
    r"|^(Passed|Failed)!\s+-\s+Failed:"
    r"|^test result: "
    r"|^Tests:\s+.*\b(passed|failed)\b"
)
_BUILD_CMD = re.compile(
    r"(?i)\bdotnet\s+(build|publish|msbuild)\b|\bmsbuild\b|\bnpm\s+run\s+build\b"
    r"|\btsc\b|\bcargo\s+build\b|\bvite\s+build\b|\bpnpm\s+(run\s+)?build\b|\bgradlew?\s+build\b|\bmvn\b"
)
_BUILD_MARKERS = re.compile(r"(?m)\b(error|warning)\s+(CS|TS|MSB|NU|CA|FS|BC)\d+\b|^Build (succeeded|FAILED)")
_GREP_CMD = re.compile(r"(?i)\b(grep|findstr|rg|ripgrep|ag|select-string)\b|\bgit\s+grep\b")
_GREP_LINE = re.compile(r"^(?:[A-Za-z]:[\\/])?[^\s:][^:]*:\d+:")
_GREP_LINE_NONUM = re.compile(r"^(?:[A-Za-z]:[\\/])?[^\s:][^:]*:\S")

# Failure block headers: the block runs until the next header / separator.
_FAIL_HEADERS = (
    re.compile(r"^_{3,} .+ _{3,}$"),                       # pytest
    re.compile(r"^(FAIL|ERROR): \S"),                      # unittest
    re.compile(r"^\s*Failed \S+"),                         # dotnet test
    re.compile(r"^\s*● .+"),                               # jest
    re.compile(r"^--- FAIL: "),                            # go test
    re.compile(r"^---- \S+ stdout ----$"),                 # cargo test
)
_BLOCK_END = re.compile(r"^(={3,}|-{20,}|Ran \d+ tests?|Failed!|Passed!)")
_SUMMARY_LINE = re.compile(
    r"^=+ .*\b(passed|failed|error|errors|no tests ran|short test summary)\b.* =+\s*$"
    r"|^Ran \d+ tests? in "
    r"|^(OK|FAILED)( \(.*\))?\s*$"
    r"|^(Passed|Failed)!"
    r"|^\s*(Total tests|Passed|Failed|Skipped|Total|Duration):"
    r"|^test result: "
    r"|^Tests:\s|^Test Suites:|^Snapshots:|^\s*Time:"
    r"|^(ok|FAIL|PASS)\s+\S+\s"
    r"|^(FAILED|ERROR) \S"
)
_BUILD_LINE = re.compile(
    r"(?i)\b(error|warning|hata|uyarı)\b|\bFAILED\b|^Build (succeeded|FAILED)"
    r"|\d+ (Warning|Error)\(s\)|Time Elapsed"
)


def _cap(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 40].rstrip() + "\n... [özet kısaltıldı]"


def _dedupe(lines: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        key = line.strip()
        if key in seen:
            continue
        seen.add(key)
        out.append(line)
    return out


def is_test_output(text: str, command: str = "") -> bool:
    return bool(_TEST_CMD.search(command or "")) or bool(_TEST_MARKERS.search(text))


def is_build_output(text: str, command: str = "") -> bool:
    return bool(_BUILD_CMD.search(command or "")) or bool(_BUILD_MARKERS.search(text))


def _grep_lines(lines: list[str], command: str) -> list[str] | None:
    """Returns the matching lines if the output looks like a match listing."""
    non_empty = [ln for ln in lines if ln.strip()]
    if len(non_empty) < GREP_MIN_LINES:
        return None
    pat = _GREP_LINE
    hits = [ln for ln in non_empty if pat.match(ln)]
    if len(hits) < 0.7 * len(non_empty) and _GREP_CMD.search(command or ""):
        pat = _GREP_LINE_NONUM
        hits = [ln for ln in non_empty if pat.match(ln)]
    if len(hits) < 0.7 * len(non_empty):
        return None
    return hits


def _file_of(line: str) -> str:
    m = re.match(r"^((?:[A-Za-z]:[\\/])?[^:]+):", line)
    return m.group(1) if m else line[:60]


def summarize_grep(hits: list[str], text: str) -> str:
    shown = hits[:GREP_SHOWN_LINES]
    rest = hits[GREP_SHOWN_LINES:]
    files = Counter(_file_of(h) for h in hits)
    out = list(shown)
    out.append(f"... toplam {len(hits)} eşleşme, {len(files)} dosya")
    if rest:
        counts = Counter(_file_of(h) for h in rest)
        parts = [f"{name}: {n}" for name, n in counts.most_common(GREP_FILE_COUNTS_SHOWN)]
        extra = len(counts) - len(parts)
        tail = f" (+{extra} dosya daha)" if extra > 0 else ""
        out.append(f"Gösterilmeyen {len(rest)} eşleşmenin dosya başına sayısı: " + ", ".join(parts) + tail)
    return "\n".join(out)


def summarize_tests(lines: list[str]) -> str:
    blocks: list[list[str]] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if any(p.match(line) for p in _FAIL_HEADERS) and not _BLOCK_END.match(line):
            block = [line]
            j = i + 1
            while j < n and len(block) < FAILURE_BLOCK_LINES:
                nxt = lines[j]
                if _BLOCK_END.match(nxt) or any(p.match(nxt) for p in _FAIL_HEADERS):
                    break
                block.append(nxt)
                j += 1
            blocks.append(block)
            # Skip the rest of this block's (dropped) body.
            while j < n and not (
                _BLOCK_END.match(lines[j]) or any(p.match(lines[j]) for p in _FAIL_HEADERS)
            ):
                j += 1
            i = j
        else:
            i += 1

    summary = [ln for ln in lines if _SUMMARY_LINE.match(ln)]
    fail_lines = _dedupe([ln for ln in summary if re.match(r"^(FAILED|ERROR) \S", ln)])[:30]
    other_summary = _dedupe([ln for ln in summary if not re.match(r"^(FAILED|ERROR) \S", ln)])[:15]
    tail_text = "Son satırlar:\n" + "\n".join(l[:200] for l in lines[-TAIL_LINES:])
    head_parts: list[str] = []
    if other_summary:
        head_parts.append("Özet satırları:\n" + "\n".join(other_summary))
    if fail_lines:
        head_parts.append("\n".join(fail_lines))
    fixed = len("\n".join(head_parts)) + len(tail_text) + 120
    budget = max(600, SUMMARY_MAX_CHARS - fixed)
    parts: list[str] = []
    if blocks:
        per_block = max(250, budget // max(1, len(blocks)))
        parts.append(f"Başarısız test blokları ({len(blocks)}):")
        used = 0
        shown = 0
        for block in blocks:
            text = _cap("\n".join(block), per_block)
            if used + len(text) > budget and shown:
                break
            parts.append(text)
            used += len(text)
            shown += 1
        if shown < len(blocks):
            parts.append(f"... {len(blocks) - shown} başarısız blok daha (tam çıktıda)")
    parts.extend(head_parts)
    parts.append(tail_text)
    return "\n".join(parts)


def summarize_build(lines: list[str]) -> str:
    kept = _dedupe([ln.strip() for ln in lines if ln.strip() and _BUILD_LINE.search(ln)])
    tail = lines[-BUILD_TAIL_LINES:]
    budget = BUILD_MAX_CHARS - sum(len(t) + 1 for t in tail) - 200
    shown: list[str] = []
    used = 0
    for ln in kept:
        if used + len(ln) + 1 > budget:
            break
        shown.append(ln)
        used += len(ln) + 1
    parts = [f"Hata/uyarı satırları ({len(kept)} benzersiz):"] + shown
    if len(shown) < len(kept):
        parts.append(f"... {len(kept) - len(shown)} satır daha (tam çıktıda)")
    parts.append("Son satırlar:")
    parts.extend(tail)
    return "\n".join(parts)


def summarize_output(text: str, command: str = "") -> str | None:
    """Summary of a long command output, or None to keep generic truncation."""
    lines = text.splitlines()
    if len(lines) < 2:
        return None
    hits = _grep_lines(lines, command)
    if hits is not None and not is_test_output(text, command):
        return summarize_grep(hits, text)
    if is_test_output(text, command):
        result = summarize_tests(lines)
    elif is_build_output(text, command):
        result = summarize_build(lines)
    else:
        return None
    return result
