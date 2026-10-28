"""apply_patch tool: multi-file unified diff with preview, approval and backup."""

from __future__ import annotations

import re
from pathlib import Path

from src.file_ops import backup_file, is_safe_path, resolve_workspace_path

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

SCHEMA = {
    "type": "function",
    "function": {
        "name": "apply_patch",
        "description": "Unified diff (---/+++/@@) ile bir veya birden çok dosyayı tek seferde yamalar; önizleme ve onay gerekir. Yeni dosya için '--- /dev/null'.",
        "parameters": {
            "type": "object",
            "properties": {"patch": {"type": "string", "description": "Unified diff metni"}},
            "required": ["patch"],
        },
    },
}


def parse_patch(text: str) -> list[dict]:
    files, cur, hunk = [], None, None
    lines = text.replace("\r\n", "\n").rstrip("\n").split("\n")
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            old = ln[4:].split("\t")[0].strip()
            new = lines[i + 1][4:].split("\t")[0].strip()
            strip = lambda p: re.sub(r"^[ab]/", "", p)
            cur = {"old": None if old == "/dev/null" else strip(old),
                   "new": None if new == "/dev/null" else strip(new), "hunks": []}
            files.append(cur)
            hunk = None
            i += 2
            continue
        m = _HUNK.match(ln)
        if m and cur is not None:
            hunk = {"start": int(m.group(1)), "lines": []}
            cur["hunks"].append(hunk)
        elif hunk is not None and ln[:1] in (" ", "+", "-"):
            hunk["lines"].append(ln)
        elif hunk is not None and ln == "":
            hunk["lines"].append(" ")
        i += 1
    return files


def _apply(orig: list[str], hunks: list[dict]) -> list[str]:
    out, pos = list(orig), 0
    for h in hunks:
        old = [l[1:] for l in h["lines"] if l[0] in " -"]
        new = [l[1:] for l in h["lines"] if l[0] in " +"]
        guess = max(0, h["start"] - 1 + (len(new) - len(old)) * 0)
        idx = None
        for cand in [guess] + [j for j in range(pos, len(out) - len(old) + 1)]:
            if cand >= pos and out[cand:cand + len(old)] == old:
                idx = cand
                break
        if idx is None:
            for j in range(0, len(out) - len(old) + 1):  # whitespace-tolerant fallback
                if [x.rstrip() for x in out[j:j + len(old)]] == [x.rstrip() for x in old]:
                    idx = j
                    break
        if idx is None:
            raise ValueError(f"hunk @@ -{h['start']} uygulanamadı (bağlam uyuşmuyor)")
        out[idx:idx + len(old)] = new
        pos = idx + len(new)
    return out


def tool_apply_patch(patch: str, workspace_root: Path, auto_approve: bool = False) -> str:
    from src.config import EVREN_AUTO_APPROVE
    from src.ui import confirm_action, print_info

    files = parse_patch(patch or "")
    if not files:
        return "HATA: Geçerli unified diff bulunamadı."
    plan = []  # (target Path, new_text or None for delete, existed)
    for f in files:
        rel = f["new"] or f["old"]
        ok, err = is_safe_path(rel, workspace_root)
        if not ok:
            return f"HATA: {rel}: {err}"
        target = resolve_workspace_path(rel, workspace_root)
        crlf = False
        if f["old"] is None:
            orig = []
        else:
            if not target.exists():
                return f"HATA: Dosya yok: {rel}"
            raw = target.read_bytes().decode("utf-8", errors="replace")
            crlf = "\r\n" in raw
            orig = raw.replace("\r\n", "\n").split("\n")
        try:
            new = _apply(orig, f["hunks"])
        except ValueError as e:
            return f"HATA: {rel}: {e}"
        text = None if f["new"] is None else ("\r\n" if crlf else "\n").join(new)
        plan.append((target, rel, text, f["old"] is not None))
    print_info("Yama önizlemesi:\n" + (patch if len(patch) < 6000 else patch[:6000] + "\n..."))
    if not (auto_approve or EVREN_AUTO_APPROVE):
        if not confirm_action("edit", f"{len(plan)} dosyaya yama uygulansın mı?",
                              detail=patch[:6000], default=False):
            return "İPTAL EDİLDİ: Kullanıcı yamayı onaylamadı."
    done = []
    for target, rel, text, existed in plan:
        if existed:
            try:
                backup_file(target, workspace_root)
            except Exception:
                pass
        if text is None:
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(text.encode("utf-8"))
        done.append(rel)
    return f"{len(done)} dosya yamalandı: " + ", ".join(done)
