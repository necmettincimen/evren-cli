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
    for idx, edit in enumerate(edits, 1):
        target = edit.get("target_snippet", "")
        replacement = edit.get("replacement_snippet", "")
        replace_all = bool(edit.get("all", False))

        new_text, ok, err = apply_replacement(
            current, target, replacement, replace_all=replace_all
        )
        if not ok:
            return original_text, False, f"Edit #{idx} başarısız: {err}"
        current = new_text

    return current, True, ""
