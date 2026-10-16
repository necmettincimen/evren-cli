"""
EVREN CLI - Metin Dosyası Denetimi (check_text.py)

Git'in izlediği metin dosyalarını iki şey için tarar:

  1. Kontrol karakterleri: \\n, \\r ve \\t dışındaki her kontrol karakteri
     (örn. regex kaçışının "\\b" -> backspace olarak bozulması). Bir regex'i
     bash heredoc ile yazınca bu bozulma sessizce oluşabiliyor.
  2. Açık sırlar: evren_llm_ anahtarları, Bearer/JWT belirteçleri, özel anahtar
     başlıkları ve GitHub belirteçleri.

Sahte değerler (içinde ***, xxx, example, fake, dummy, test geçenler) ve
`# check_text: ignore` işaretli satırlar sır sayılmaz.

Rapor bulunan sırrı ASLA tam yazmaz; yalnızca dosya:satır:sütun ve kısaltılmış
bir iz gösterir. Bir bulgu varsa çıkış kodu 1, temizse 0'dır.

Kullanım (proje kökünden):
    python scripts/check_text.py                  # git ls-files ile izlenen dosyalar
    python scripts/check_text.py --untracked      # izlenmeyen ama yoksayılmayanlar da
    python scripts/check_text.py a.py docs/b.md   # yalnızca verilen dosyalar
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

ROOT = Path(__file__).resolve().parents[1]

TEXT_EXTENSIONS = frozenset(
    {".py", ".md", ".json", ".toml", ".yml", ".yaml", ".txt", ".ps1", ".sh"}
)

# Bu işaret bir satırda varsa o satırdaki sır eşleşmeleri yok sayılır.
IGNORE_MARKER = "check_text: ignore"

ALLOWED_CONTROLS = frozenset({"\n", "\r", "\t"})

# Eşleşen değerin içinde bunlardan biri geçiyorsa sahte kabul edilir (büyük/küçük harf duyarsız).
FAKE_MARKERS = ("***", "xxx", "example", "fake", "dummy", "test")

# Çok büyük dosyaları atla (üretilmiş veri / kilit dosyası).
MAX_FILE_BYTES = 5 * 1024 * 1024

SECRET_PATTERNS: dict[str, "re.Pattern[str]"] = {
    "evren_llm anahtarı": re.compile(r"evren_llm_[A-Za-z0-9]{12,}"),
    "Bearer belirteci": re.compile(r"Bearer [A-Za-z0-9._-]{20,}"),
    "JWT": re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    "özel anahtar başlığı": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "GitHub belirteci": re.compile(r"ghp_[A-Za-z0-9]{30,}"),
}


@dataclass(frozen=True)
class Finding:
    """One problem found in a file. `detail` never holds a full secret."""

    path: str
    line: int
    col: int
    kind: str
    detail: str

    def format(self) -> str:
        return f"{self.path}:{self.line}:{self.col}: {self.kind}: {self.detail}"


def is_fake(value: str) -> bool:
    """True when a matched value is an obvious placeholder."""
    lowered = value.lower()
    return any(marker in lowered for marker in FAKE_MARKERS)


def mask_value(value: str) -> str:
    """Shortens a matched value so reports never expose a full secret."""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:6]}...({len(value)} kr)"


def find_control_chars(text: str) -> list[tuple[int, int, int]]:
    """Returns (line, col, codepoint) for every disallowed control character.

    Lines and columns are 1-based; only "\\n" starts a new line, so a lone
    "\\r" never shifts the numbering.
    """
    hits: list[tuple[int, int, int]] = []
    line, col = 1, 0
    for ch in text:
        col += 1
        if ch == "\n":
            line += 1
            col = 0
            continue
        if ch in ALLOWED_CONTROLS:
            continue
        if unicodedata.category(ch) == "Cc":
            hits.append((line, col, ord(ch)))
    return hits


def find_secrets(text: str) -> list[tuple[int, int, str, str]]:
    """Returns (line, col, kind, matched_value) for each non-fake secret-like match."""
    hits: list[tuple[int, int, str, str]] = []
    for lineno, raw in enumerate(text.split("\n"), 1):
        if IGNORE_MARKER in raw:
            continue
        for kind, pattern in SECRET_PATTERNS.items():
            for match in pattern.finditer(raw):
                value = match.group(0)
                if is_fake(value):
                    continue
                hits.append((lineno, match.start() + 1, kind, value))
    return hits


def scan_text(path: str, text: str) -> list[Finding]:
    """Scans already-decoded text and returns findings sorted by position."""
    findings: list[Finding] = []
    for line, col, codepoint in find_control_chars(text):
        findings.append(
            Finding(path, line, col, "kontrol karakteri", f"U+{codepoint:04X}")
        )
    for line, col, kind, value in find_secrets(text):
        findings.append(Finding(path, line, col, f"olası sır ({kind})", mask_value(value)))
    findings.sort(key=lambda f: (f.line, f.col))
    return findings


def scan_file(path: Path, display: str | None = None) -> list[Finding]:
    """Reads one file as UTF-8 and scans it. Undecodable bytes are a finding."""
    shown = display or str(path)
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return []
        raw = path.read_bytes()
    except OSError:
        return []
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        prefix = raw[: exc.start]
        line = prefix.count(b"\n") + 1
        col = exc.start - (prefix.rfind(b"\n") + 1) + 1
        return [Finding(shown, line, col, "geçersiz UTF-8", f"bayt konumu {exc.start}")]
    return scan_text(shown, text)


def list_tracked_files(root: Path, include_untracked: bool = False) -> list[str]:
    """Lists git-tracked (optionally also untracked, non-ignored) paths relative to root."""
    args = ["git", "ls-files", "-z"]
    if include_untracked:
        args += ["--cached", "--others", "--exclude-standard"]
    result = subprocess.run(args, cwd=str(root), capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(
            "git ls-files çalışmadı: " + result.stderr.decode("utf-8", errors="replace").strip()
        )
    names = result.stdout.decode("utf-8", errors="replace").split("\0")
    return sorted({name for name in names if name})


def is_text_file(name: str) -> bool:
    return Path(name).suffix.lower() in TEXT_EXTENSIONS


def scan_paths(root: Path, names: Iterable[str]) -> Iterator[Finding]:
    """Scans each text-extension path (relative to root) that exists on disk."""
    for name in names:
        if not is_text_file(name):
            continue
        path = root / name
        if not path.is_file():
            continue
        yield from scan_file(path, display=name.replace("\\", "/"))


def _relative_to_root(item: str) -> str:
    """Path relative to the repo root when inside it, else the absolute path."""
    resolved = Path(item).resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return str(resolved)


def _configure_stdout() -> None:
    # Windows runners default to a legacy code page; the report is Turkish.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    _configure_stdout()
    parser = argparse.ArgumentParser(
        description="Git'in izlediği metin dosyalarını kontrol karakteri ve sır için tarar."
    )
    parser.add_argument("files", nargs="*", help="Taranacak dosyalar (varsayılan: git ls-files)")
    parser.add_argument(
        "--untracked",
        action="store_true",
        help="İzlenmeyen ama .gitignore dışındaki dosyaları da tara",
    )
    args = parser.parse_args(argv)

    if args.files:
        # Explicit paths are relative to the caller's cwd, not to the repo root.
        names = [_relative_to_root(item) for item in args.files]
    else:
        try:
            names = list_tracked_files(ROOT, include_untracked=args.untracked)
        except (RuntimeError, OSError) as exc:
            print(f"check_text: {exc}", file=sys.stderr)
            return 2

    findings = list(scan_paths(ROOT, names))
    if not findings:
        print("check_text: temiz")
        return 0

    print(f"check_text: {len(findings)} bulgu")
    for finding in findings:
        print(f"  {finding.format()}")
    print(
        "\nSahte değerler için içine 'test', 'fake', 'EXAMPLE' ya da '***' koyun; "
        f"satıra '# {IGNORE_MARKER}' da eklenebilir."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
