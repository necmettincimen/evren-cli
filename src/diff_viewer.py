"""
Diff generation and surgical search-and-replace utilities for EVREN CLI.
Generates unified diffs and validates replacements before applying to files.
"""

import difflib


def generate_unified_diff(
    old_content: str,
    new_content: str,
    filename: str = "file",
) -> str:
    """Generates standard unified diff string between old and new contents."""
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)

    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        n=3,
    )
    return "".join(diff)


# Prefix of the "edit is already in the file" outcome (not a failure).
ALREADY_APPLIED = "ZATEN UYGULANMIŞ"


def _already_applied(original_text: str, target_snippet: str, replacement_snippet: str) -> bool:
    """True when the target is gone but the replacement is already present.

    Models often re-send an edit they applied a turn earlier; the plain "not
    found" error made them re-read the file and retry (3 wasted steps seen).
    """
    replacement = replacement_snippet.replace("\r\n", "\n").strip()
    if not replacement:
        return False
    text = original_text.replace("\r\n", "\n")
    return replacement in text and target_snippet.replace("\r\n", "\n").strip() not in text


def _contextual_not_found_error(original_text: str, target_snippet: str) -> str:
    """Builds a helpful error showing the closest matching region.

    Highlights indentation/whitespace differences so the model can correct its
    target snippet instead of guessing.
    """
    target_lines = target_snippet.splitlines()
    if not target_lines:
        return "Değiştirilmek istenen kod parçası dosyada bulunamadı."

    # Find the line whose stripped form best matches the first target line.
    first = target_lines[0].strip()
    best_idx = -1
    best_score = 0.0
    orig_lines = original_text.splitlines()
    for i, line in enumerate(orig_lines):
        stripped = line.strip()
        if not stripped or not first:
            continue
        # Simple similarity via common prefix ratio.
        common = 0
        for a, b in zip(stripped, first):
            if a == b:
                common += 1
            else:
                break
        score = common / max(len(stripped), len(first))
        if score > best_score:
            best_score = score
            best_idx = i

    if best_idx >= 0 and best_score > 0.5:
        # Multi-line target whose first line matches: point at the first line
        # that actually differs. Showing only the (identical) first line left
        # the model retrying the same edit blind.
        if orig_lines[best_idx].strip() == first and len(target_lines) > 1:
            for offset, expected in enumerate(target_lines[1:], 1):
                idx = best_idx + offset
                actual = orig_lines[idx] if idx < len(orig_lines) else "<dosya sonu>"
                if actual != expected:
                    return (
                        "Değiştirilmek istenen kod parçası dosyada birebir bulunamadı. "
                        f"İlk satır (satır {best_idx + 1}) eşleşiyor; En yakın satır (satır {idx + 1}) "
                        "ilk farkın olduğu yer:\n"
                        f"  Dosyada : {actual!r}\n"
                        f"  Aranan  : {expected!r}\n"
                        "Hedef parçayı bu satırdan itibaren dosyadaki metinle birebir düzelt."
                    )
        actual = orig_lines[best_idx]
        expected = target_lines[0]
        return (
            "Değiştirilmek istenen kod parçası dosyada birebir bulunamadı. "
            f"En yakın satır (satır {best_idx + 1}):\n"
            f"  Dosyada : {actual!r}\n"
            f"  Aranan  : {expected!r}\n"
            "Girinti/boşluk farkı olabilir; lütfen dosyanın güncel içeriğini okuyup "
            "satırları birebir kopyalayın."
        )

    return (
        "Değiştirilmek istenen kod parçası dosyada bulunamadı. "
        "Lütfen dosyanın güncel içeriğini okuyup satırları birebir kontrol edin."
    )


def _leading_ws(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def _indent_tolerant_replace(text: str, target: str, replacement: str) -> str | None:
    """Matches `target` ignoring indentation/trailing spaces; re-indents the replacement.

    Models often copy a block with a different base indent (8 vs 4 spaces in a
    JSON list), then fail twice and fall back to line ranges. Only a single,
    unambiguous match is accepted.
    """
    t_lines = [ln for ln in target.split("\n")]
    while t_lines and not t_lines[-1].strip():
        t_lines.pop()
    while t_lines and not t_lines[0].strip():
        t_lines.pop(0)
    if not t_lines:
        return None
    key = [ln.strip() for ln in t_lines]
    lines = text.split("\n")
    hits = [
        i for i in range(len(lines) - len(key) + 1)
        if all(lines[i + j].strip() == key[j] for j in range(len(key)))
    ]
    if len(hits) != 1:
        return None
    start = hits[0]
    file_base = _leading_ws(lines[start])
    target_base = _leading_ws(t_lines[0])
    out = []
    for ln in replacement.split("\n"):
        if ln.strip() and ln.startswith(target_base):
            ln = file_base + ln[len(target_base):]
        elif ln.strip() and not target_base:
            ln = file_base + ln
        out.append(ln)
    return "\n".join(lines[:start] + out + lines[start + len(key):])


def apply_replacement(
    original_text: str,
    target_snippet: str,
    replacement_snippet: str,
    *,
    replace_all: bool = False,
) -> tuple[str, bool, str]:
    """
    Replaces target_snippet with replacement_snippet in original_text.
    Returns (new_text, success, error_message).

    If replace_all is True, every occurrence is replaced (the `all` flag).
    Otherwise, the target must appear exactly once.
    """
    if not target_snippet:
        return original_text, False, "Hedef değiştirilecek metin (target_snippet) boş olamaz."

    # Exact match check
    count = original_text.count(target_snippet)
    if count == 1:
        new_text = original_text.replace(target_snippet, replacement_snippet, 1)
        return new_text, True, ""
    elif count > 1:
        if replace_all:
            new_text = original_text.replace(target_snippet, replacement_snippet)
            return new_text, True, ""
        return (
            original_text,
            False,
            f"Değiştirilmek istenen metin dosyada birden fazla kez ({count} kez) bulundu. "
            "Tümünü değiştirmek için 'all' bayrağını kullanın veya daha belirgin bir bağlam parçası belirtin.",
        )

    # Line-ending normalized check (\r\n vs \n)
    normalized_orig = original_text.replace("\r\n", "\n")
    normalized_target = target_snippet.replace("\r\n", "\n")
    normalized_replace = replacement_snippet.replace("\r\n", "\n")

    count_norm = normalized_orig.count(normalized_target)
    if count_norm == 1:
        new_norm = normalized_orig.replace(normalized_target, normalized_replace, 1)
        # Restore CRLF if original had CRLF
        if "\r\n" in original_text:
            return new_norm.replace("\n", "\r\n"), True, ""
        return new_norm, True, ""
    elif count_norm > 1 and replace_all:
        new_norm = normalized_orig.replace(normalized_target, normalized_replace)
        if "\r\n" in original_text:
            return new_norm.replace("\n", "\r\n"), True, ""
        return new_norm, True, ""

    fuzzy = _indent_tolerant_replace(normalized_orig, normalized_target, normalized_replace)
    if fuzzy is not None:
        return (fuzzy.replace("\n", "\r\n") if "\r\n" in original_text else fuzzy), True, ""

    if _already_applied(original_text, target_snippet, replacement_snippet):
        return original_text, False, (
            f"{ALREADY_APPLIED}: replacement_snippet dosyada zaten var, hedef metin artık yok. "
            "Bu değişiklik daha önce uygulanmış; tekrar deneme, sıradaki işe geç."
        )
    return original_text, False, _contextual_not_found_error(original_text, target_snippet)


def apply_edits(
    original_text: str,
    edits: list[dict],
) -> tuple[str, bool, str]:
    """Applies a list of edits sequentially.

    Each edit is a dict with keys: target_snippet, replacement_snippet, and an
    optional `all` boolean. Returns (new_text, success, error_message).
    """
    if not edits:
        return original_text, False, "Düzenleme listesi boş olamaz."

    current = original_text
    already: list[int] = []
    for idx, edit in enumerate(edits, 1):
        if not isinstance(edit, dict):
            return (
                original_text,
                False,
                f"Edit #{idx} bir nesne olmalı (target_snippet/replacement_snippet), "
                f"gelen tip: {type(edit).__name__}.",
            )
        target = edit.get("target_snippet", "")
        replacement = edit.get("replacement_snippet", "")
        if not isinstance(target, str) or not isinstance(replacement, str):
            return (
                original_text,
                False,
                f"Edit #{idx}: target_snippet ve replacement_snippet metin olmalı "
                f"(gelen: {type(target).__name__}/{type(replacement).__name__}).",
            )
        replace_all = bool(edit.get("all", False))

        new_text, ok, err = apply_replacement(
            current, target, replacement, replace_all=replace_all
        )
        if not ok and err.startswith(ALREADY_APPLIED):
            already.append(idx)
            continue
        if not ok:
            return original_text, False, f"Edit #{idx} başarısız: {err}"
        current = new_text

    if already and current == original_text:
        numbers = ", ".join(f"#{n}" for n in already)
        return original_text, False, (
            f"{ALREADY_APPLIED}: düzenleme {numbers} dosyada zaten uygulanmış; dosya değişmedi. "
            "Tekrar deneme, sıradaki işe geç."
        )
    return current, True, ""
