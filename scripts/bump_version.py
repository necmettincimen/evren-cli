"""
EVREN CLI - Sürüm Artırma Betiği (bump_version.py)

Tek komutla proje sürümünü artırır ve ilgili tüm dosyaları tutarlı biçimde
günceller:

  1. pyproject.toml          -> [project] version alanı
  2. src/version.py          -> _FALLBACK_VERSION sabiti
  3. CHANGELOG.md            -> yeni sürüm başlığı ve (isteğe bağlı) notlar

Kullanım (proje kökünden):
    python scripts/bump_version.py patch
    python scripts/bump_version.py minor
    python scripts/bump_version.py major
    python scripts/bump_version.py --set 1.2.3
    python scripts/bump_version.py minor --notes "Yeni özellik X" --notes "Hata düzeltmesi Y"
    python scripts/bump_version.py patch --dry-run

SemVer kuralı: MAJOR.MINOR.PATCH
"""

from __future__ import annotations

import argparse
import datetime as _dt
import re
import sys
from pathlib import Path

# Proje kökü: bu betik scripts/ altında olduğundan bir üst dizin.
ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
VERSION_PY = ROOT / "src" / "version.py"
CHANGELOG = ROOT / "CHANGELOG.md"

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def read_current_version() -> str:
    """pyproject.toml'dan mevcut sürümü okur."""
    text = PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise RuntimeError("pyproject.toml içinde 'version' alanı bulunamadı.")
    return match.group(1)


def parse_version(version: str) -> tuple[int, int, int]:
    match = _VERSION_RE.match(version.strip())
    if not match:
        raise ValueError(f"Geçersiz sürüm biçimi: '{version}' (beklenen: MAJOR.MINOR.PATCH)")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def bump(version: str, part: str) -> str:
    major, minor, patch = parse_version(version)
    if part == "major":
        major, minor, patch = major + 1, 0, 0
    elif part == "minor":
        minor, patch = minor + 1, 0
    elif part == "patch":
        patch += 1
    else:  # pragma: no cover - argparse zaten kısıtlar
        raise ValueError(f"Bilinmeyen artırma türü: {part}")
    return f"{major}.{minor}.{patch}"


def update_pyproject(new_version: str) -> None:
    text = PYPROJECT.read_text(encoding="utf-8")
    new_text, count = re.subn(
        r'^(version\s*=\s*)"[^"]+"',
        rf'\g<1>"{new_version}"',
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if count == 0:
        raise RuntimeError("pyproject.toml güncellenemedi (version alanı bulunamadı).")
    PYPROJECT.write_text(new_text, encoding="utf-8")


def update_version_py(new_version: str) -> None:
    text = VERSION_PY.read_text(encoding="utf-8")
    new_text, count = re.subn(
        r'^(_FALLBACK_VERSION\s*=\s*)"[^"]+"',
        rf'\g<1>"{new_version}"',
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if count == 0:
        raise RuntimeError("src/version.py güncellenemedi (_FALLBACK_VERSION bulunamadı).")
    VERSION_PY.write_text(new_text, encoding="utf-8")


def prepend_changelog(new_version: str, notes: list[str]) -> None:
    """CHANGELOG.md'ye yeni sürüm bölümünü ekler (en üste, başlıklardan sonra)."""
    today = _dt.date.today().isoformat()
    lines = [f"## [{new_version}] - {today}", ""]
    if notes:
        lines.append("### Değişti")
        for note in notes:
            lines.append(f"- {note}")
    else:
        lines.append("### Değişti")
        lines.append("- (Bu sürümdeki değişiklikleri buraya yazın.)")
    lines.append("")
    new_section = "\n".join(lines) + "\n"

    if CHANGELOG.exists():
        text = CHANGELOG.read_text(encoding="utf-8")
    else:
        text = "# Değişiklik Günlüğü (Changelog)\n\n"

    # İlk "## [" başlığının hemen öncesine ekle; yoksa dosya sonuna.
    match = re.search(r"^##\s+\[", text, re.MULTILINE)
    if match:
        text = text[: match.start()] + new_section + text[match.start():]
    else:
        if not text.endswith("\n"):
            text += "\n"
        text += "\n" + new_section
    CHANGELOG.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bump_version",
        description="EVREN CLI sürümünü artırır ve ilgili dosyaları günceller.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "part",
        nargs="?",
        choices=["major", "minor", "patch"],
        help="Artırılacak SemVer bileşeni",
    )
    group.add_argument(
        "--set",
        dest="explicit",
        metavar="X.Y.Z",
        help="Sürümü doğrudan belirtilen değere ayarla",
    )
    parser.add_argument(
        "--notes",
        action="append",
        default=[],
        help="CHANGELOG'a eklenecek madde (birden çok kez verilebilir)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Hiçbir dosyayı değiştirmeden yalnızca yeni sürümü göster",
    )

    args = parser.parse_args(argv)

    current = read_current_version()
    if args.explicit:
        parse_version(args.explicit)  # doğrulama
        new_version = args.explicit
    else:
        new_version = bump(current, args.part)

    print(f"Mevcut sürüm : {current}")
    print(f"Yeni sürüm   : {new_version}")

    if args.dry_run:
        print("[dry-run] Hiçbir dosya değiştirilmedi.")
        return 0

    update_pyproject(new_version)
    update_version_py(new_version)
    prepend_changelog(new_version, args.notes)

    print("Güncellendi  : pyproject.toml, src/version.py, CHANGELOG.md")
    print("İpucu        : CHANGELOG.md'deki yeni bölümü düzenleyip commit'leyin.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
