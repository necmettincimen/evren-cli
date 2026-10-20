"""clone_tree: bir dizini kopyalayıp dosya/klasör adlarında ve içerikte toplu yeniden adlandırır.

Mekanik "şablon repodan yeni repo" işlerini (portal-esmm-* -> portal-egp-*) LLM adımı harcamadan yapar.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

DEFAULT_EXCLUDE = [".git", "bin", "obj", "node_modules", "__pycache__", ".vs", ".idea"]

SCHEMA = {
    "type": "function",
    "function": {
        "name": "clone_tree",
        "description": (
            "src dizinini dst'ye kopyalar; replacements sırayla dosya/klasör adlarına VE metin dosyası içeriğine uygulanır "
            "(Esmm->Egp, esmm->egp, ESMM->EGP gibi tam eşleşme; sıra önemli). exclude varsayılan: .git,bin,obj,node_modules. "
            "dry_run=true yalnız rapor verir. Mekanik kopyala-yeniden adlandır işlerinde elle dosya dosya yazmak yerine bunu kullan."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "src": {"type": "string"},
                "dst": {"type": "string", "description": "Boş ya da olmayan hedef dizin; mevcut dosyaların üzerine yazılmaz (skip)"},
                "replacements": {
                    "type": "array",
                    "items": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 2},
                    "description": '[["Esmm","Egp"],["esmm","egp"],["ESMM","EGP"]]',
                },
                "exclude": {"type": "array", "items": {"type": "string"}},
                "dry_run": {"type": "boolean"},
            },
            "required": ["src", "dst", "replacements"],
        },
    },
}


def _apply(text: str, reps: list[tuple[str, str]]) -> str:
    # tek geçişte (sıra = alternasyon önceliği): önceki değişimin çıktısı tekrar dönüştürülmez
    if not reps:
        return text
    pattern = re.compile("|".join(re.escape(a) for a, _ in reps if a))
    table = dict(reps)
    return pattern.sub(lambda m: table[m.group(0)], text)


def _is_text(data: bytes) -> bool:
    return b"\x00" not in data[:4096]


def tool_clone_tree(arguments: dict, root) -> str:
    from src.file_ops import is_safe_path

    root = Path(root).resolve()
    src = (root / str(arguments.get("src", ""))).resolve()
    dst = (root / str(arguments.get("dst", ""))).resolve()
    for p, ro in ((src, True), (dst, False)):
        ok, reason = is_safe_path(p, root, read_only=ro)
        if not ok:
            return f"HATA: yol reddedildi: {p} ({reason})"
    if not src.is_dir():
        return f"HATA: kaynak dizin yok: {src}"
    if dst == src or src in dst.parents:
        return "HATA: hedef kaynağın içinde/aynısı olamaz."
    reps = [(str(a), str(b)) for a, b in (arguments.get("replacements") or []) if a]
    if not reps:
        return "HATA: replacements boş."
    excl = set(arguments.get("exclude") or DEFAULT_EXCLUDE)
    dry = bool(arguments.get("dry_run"))
    files = changed = skipped = 0
    for path in sorted(src.rglob("*")):
        rel = path.relative_to(src)
        if any(part in excl for part in rel.parts) or path.is_dir():
            continue
        target = dst / Path(*[_apply(part, reps) for part in rel.parts])
        files += 1
        if target.exists():
            skipped += 1
            continue
        data = path.read_bytes()
        if _is_text(data):
            try:
                text = data.decode("utf-8")
                new = _apply(text, reps)
                if new != text:
                    changed += 1
                data = new.encode("utf-8")
            except UnicodeDecodeError:
                pass
        if not dry:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    verb = "KURU ÇALIŞTIRMA" if dry else "BAŞARILI"
    return f"{verb}: {files} dosya ({changed} içerik dönüştü, {skipped} atlandı/mevcut) -> {dst}"
