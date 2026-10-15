"""
CLI-owned support documents for EVREN CLI (its own home directory).

Unlike `agent.md` / `changelog.md`, which live in the *project* the CLI is
working on, these documents belong to the CLI installation itself:

- `skills.md`    markdown mirror of the in-code skill catalog + a hand-written
                 "Özel Beceriler" section the CLI only ever reads.
- `lessons.md`   short cross-session lessons the CLI appends after failures.
- `commands.md`  generated reference of slash commands and subcommands.
- `context/README.md` explains the folder to humans.

The install directory is frequently read-only on Windows (Program Files, a
site-packages install), so the directory is resolved with a three-step fallback
and every write is best-effort: a failure degrades the feature, never the CLI.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path

from src.docs_merge import extract_sections, render_sections, section_hash

SKILLS_FILE = "skills.md"
LESSONS_FILE = "lessons.md"
COMMANDS_FILE = "commands.md"
CONTEXT_README = "context/README.md"
LESSONS_ARCHIVE = "logs/lessons_archive.md"

MAX_LESSONS = 40
SPECIAL_HEADING = "## Özel Beceriler"
MAX_CONTEXT_CHARS = 2500

# Written once into a fresh home so a human knows what is generated vs. hand-written.
CONTEXT_README_BODY = """# EVREN CLI destek dosyaları

Bu klasör EVREN CLI'nin **kendi** bağlam dosyalarını tutar; üzerinde çalışılan
projeye ait değildirler.

| Dosya | Kim yazar |
|---|---|
| `skills.md` | Yönetilen blok CLI (kod kataloğundan üretilir); `## Özel Beceriler` yalnızca insana ait |
| `lessons.md` | CLI başarısız/blokajlı görev sonrası tek satır ders ekler |
| `commands.md` | CLI, slash komut ve alt komut referansını üretir |

Elle yazılan bölümler CLI tarafından asla silinmez. Konumu değiştirmek için
`EVREN_CLI_HOME` ortam değişkenini kullanın.
"""


def _is_writable(directory: Path) -> bool:
    """Best-effort writability probe (Windows ACLs make `exists()` insufficient)."""
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".evren-write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _candidate_dirs() -> list[Path]:
    """Ordered candidates: explicit env, git/package checkout, user home."""
    candidates: list[Path] = []
    override = (os.getenv("EVREN_CLI_HOME") or "").strip()
    if override:
        candidates.append(Path(override).expanduser())
    try:
        from src.updater import get_project_root

        root = get_project_root()
    except Exception:  # pragma: no cover - updater must always import
        root = Path(__file__).resolve().parents[1]
    candidates.append(root)
    candidates.append(Path.home() / ".evren" / "cli_home")
    return candidates


def cli_docs_dir() -> Path:
    """Returns the first writable CLI home directory (never raises)."""
    for candidate in _candidate_dirs():
        try:
            if _is_writable(candidate):
                return candidate.resolve()
        except OSError:
            continue
    # Last resort: the temp directory. Still writable in practice, still isolated.
    return Path(os.environ.get("TEMP", ".")).resolve()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _stamp() -> str:
    try:
        from src.version import get_version

        version = get_version()
    except Exception:  # pragma: no cover - version is optional
        version = "?"
    return f"Otomatik üretildi: {_now()} (EVREN CLI {version})"


# --------------------------------------------------------------------------- #
# skills.md
# --------------------------------------------------------------------------- #

def skills_catalog_body() -> str:
    """Renders the skill catalog (built-in + user) as a markdown table."""
    try:
        from src.skills import list_skills, reload_user_skills, user_skills_dir

        reload_user_skills()
        items = list_skills()
        skills_dir = user_skills_dir()
    except Exception:
        return "Katalog okunamadı; `src/skills.py` içine bakın."
    lines = [
        "Kod içi katalog: `src/skills.py`; kullanıcı becerileri `"
        f"{skills_dir}` altında `<ad>.md` dosyaları olarak tanımlanır.",
        "Aşağıdaki tablo her ikisinden üretilir.",
        "",
        "| Beceri | Kategori | Kaynak | Ne zaman |",
        "|---|---|---|---|",
    ]
    for skill in items:
        when = (skill.when_to_use or "").replace("|", "/")
        lines.append(f"| `{skill.name}` | {skill.category} | {skill.source} | {when} |")
    lines.append("")
    lines.append(f"Toplam {len(items)} beceri. Ayrıntı: REPL'de `/skills`.")
    return "\n".join(lines)


def _skills_skeleton() -> str:
    return "\n".join(
        [
            "# skills.md — EVREN CLI Beceri Kataloğu",
            "",
            render_sections("", {"catalog": skills_catalog_body()}, adopt=True)[0],
            "",
            SPECIAL_HEADING,
            "",
            "<!-- Bu bölümü siz yazarsınız; CLI hiç değiştirmez, yalnızca okur ve",
            "     ajan bağlamına katar. Biçim: `### ad`, sonra `Amaç:`, `Ne zaman:`,",
            "     `Adımlar:` satırları. -->",
            "",
        ]
    ) + "\n"


def _free_section(text: str, heading: str) -> str:
    """Returns the text under `heading` up to the next `## ` heading."""
    lines = (text or "").replace("\r\n", "\n").split("\n")
    collected: list[str] = []
    inside = False
    for line in lines:
        if line.strip() == heading:
            inside = True
            continue
        if inside:
            if line.startswith("## "):
                break
            collected.append(line)
    return "\n".join(collected).strip()


_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def custom_skills_text(root: Path | None = None) -> str:
    """Reads the hand-written skills section (empty when absent/unreadable).

    HTML comments are dropped so the skeleton's usage note never reaches the
    model; everything the human typed stays verbatim.
    """
    try:
        text = ((root or cli_docs_dir()) / SKILLS_FILE).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return ""
    body = _free_section(text, SPECIAL_HEADING)
    return _COMMENT_RE.sub("", body).strip()


# --------------------------------------------------------------------------- #
# lessons.md
# --------------------------------------------------------------------------- #

def _lessons_skeleton() -> str:
    return "\n".join(
        [
            "# lessons.md — EVREN CLI Oturum Dersleri",
            "",
            "Kısa, uygulanabilir dersler. CLI yalnızca yönetilen bloğu günceller;",
            "en yeni 40 satır burada durur, eskiler `logs/lessons_archive.md`e taşınır.",
            "",
            render_sections("", {"lessons": "- (henüz ders yok)"}, adopt=True)[0],
            "",
        ]
    ) + "\n"


def _lesson_lines(body: str) -> list[str]:
    return [line for line in body.split("\n") if line.strip().startswith("-")]


def append_lesson(text: str, root: Path | None = None) -> bool:
    """Appends one dated lesson line; archives the overflow. False on any failure."""
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return False
    try:
        from src.workflow_state import redact_text

        text = redact_text(text)
    except Exception:  # pragma: no cover - masking is best effort
        pass
    home = root or cli_docs_dir()
    path = home / LESSONS_FILE
    try:
        if not path.exists():
            home.mkdir(parents=True, exist_ok=True)
            path.write_text(_lessons_skeleton(), encoding="utf-8", newline="")
        current = path.read_text(encoding="utf-8-sig")
        sections = extract_sections(current)
        if "lessons" not in sections:
            return False
        lines = _lesson_lines(sections["lessons"])
        entry = f"- [{_now()}] {text}"
        if entry in lines:
            return False
        lines.append(entry)
        if len(lines) > MAX_LESSONS:
            overflow, lines = lines[:-MAX_LESSONS], lines[-MAX_LESSONS:]
            _archive_lines(home, overflow)
        new_text, changed, errors = render_sections(current, {"lessons": "\n".join(lines)})
        if errors or not changed:
            return False
        path.write_text(new_text, encoding="utf-8", newline="")
        return True
    except OSError:
        return False


def _archive_lines(home: Path, lines: list[str]) -> None:
    """Moves overflow lessons to the archive instead of deleting them."""
    try:
        archive = home / LESSONS_ARCHIVE
        archive.parent.mkdir(parents=True, exist_ok=True)
        with archive.open("a", encoding="utf-8", newline="\n") as stream:
            for line in lines:
                stream.write(line + "\n")
    except OSError:
        pass


def lessons_text(root: Path | None = None, max_lines: int = 12) -> str:
    """Returns the newest managed lesson lines (empty when unavailable)."""
    try:
        text = ((root or cli_docs_dir()) / LESSONS_FILE).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return ""
    body = extract_sections(text).get("lessons", "")
    lines = _lesson_lines(body)
    lines = [line for line in lines if "henüz ders yok" not in line]
    return "\n".join(lines[-max_lines:])


# --------------------------------------------------------------------------- #
# commands.md
# --------------------------------------------------------------------------- #

_SLASH_LINE_RE = re.compile(
    r"\[green\](?P<cmd>/.+?)\[/green\][ \t]+(?P<desc>.+?)[ \t]*$", re.MULTILINE
)
# Only top-level subcommands; nested ssh actions belong to `evren ssh`.
_SUBCOMMAND_RE = re.compile(
    r"subparsers\.add_parser\(\s*\"(?P<name>[a-z-]+)\"\s*,\s*help=\"(?P<desc>[^\"]+)\""
)


def slash_commands() -> list[tuple[str, str]]:
    """Parses slash command help out of `src/repl.py` source (single source of truth)."""
    try:
        source = (Path(__file__).resolve().parent / "repl.py").read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return []
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    for match in _SLASH_LINE_RE.finditer(source):
        cmd = match.group("cmd").strip()
        desc = re.sub(r"\s+", " ", match.group("desc")).strip()
        name = cmd.split(maxsplit=1)[0]
        if name in seen:
            continue
        seen.add(name)
        rows.append((cmd, desc))
    return rows


def subcommands() -> list[tuple[str, str]]:
    """Parses argparse subcommand help out of `src/cli.py` source."""
    try:
        source = (Path(__file__).resolve().parent / "cli.py").read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return []
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    for match in _SUBCOMMAND_RE.finditer(source):
        name = match.group("name")
        if name in seen:
            continue
        seen.add(name)
        rows.append((name, match.group("desc").strip()))
    return rows


def _cell(text: str) -> str:
    """Escapes pipe characters so help strings cannot break a markdown table."""
    return (text or "").replace("|", "\\|")


def commands_body() -> str:
    """Builds the generated command reference table."""
    slash = slash_commands()
    subs = subcommands()
    lines = ["### Slash komutları (REPL)", ""]
    if slash:
        lines += ["| Komut | Açıklama |", "|---|---|"]
        lines += [f"| `{_cell(cmd)}` | {_cell(desc)} |" for cmd, desc in slash]
    else:
        lines.append("(slash komutları okunamadı)")
    lines += ["", "### Alt komutlar (`evren <komut>`)", ""]
    if subs:
        lines += ["| Komut | Açıklama |", "|---|---|"]
        lines += [f"| `evren {_cell(name)}` | {_cell(desc)} |" for name, desc in subs]
    else:
        lines.append("(alt komutlar okunamadı)")
    return "\n".join(lines)


def _commands_skeleton() -> str:
    return "\n".join(
        [
            "# commands.md — EVREN CLI Komut Referansı",
            "",
            render_sections("", {"commands": commands_body()}, adopt=True)[0],
            "",
            "## Takma Adlar",
            "",
            "Bu bölüm size ait; CLI yalnızca yukarıdaki yönetilen bloğu günceller.",
            "",
        ]
    ) + "\n"


# --------------------------------------------------------------------------- #
# ensure / context
# --------------------------------------------------------------------------- #

def _ensure_file(home: Path, name: str, skeleton: str, managed: dict[str, str]) -> str:
    """Creates or refreshes one document. Returns created|updated|skipped|error."""
    path = home / name
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(skeleton, encoding="utf-8", newline="")
            return "created"
        current = path.read_text(encoding="utf-8-sig")
        new_text, changed, errors = render_sections(current, managed)
        if errors or not changed:
            return "skipped"
        path.write_text(new_text, encoding="utf-8", newline="")
        return "updated"
    except OSError:
        return "error"


def ensure_cli_docs(root: Path | None = None) -> dict:
    """Creates/refreshes the CLI home documents. Never raises."""
    home = root or cli_docs_dir()
    result: dict = {"dir": str(home), "files": {}, "disabled": False}
    try:
        home.mkdir(parents=True, exist_ok=True)
    except OSError:
        result["disabled"] = True
        result["reason"] = "CLI bağlam klasörü oluşturulamadı"
        return result

    result["files"][SKILLS_FILE] = _ensure_file(
        home, SKILLS_FILE, _skills_skeleton(), {"catalog": skills_catalog_body()}
    )
    # lessons.md is only created once there is something to say; keep it absent otherwise.
    if (home / LESSONS_FILE).exists():
        result["files"][LESSONS_FILE] = "skipped"
    result["files"][COMMANDS_FILE] = _ensure_file(
        home, COMMANDS_FILE, _commands_skeleton(), {"commands": commands_body()}
    )
    result["files"][CONTEXT_README] = _ensure_file(
        home, CONTEXT_README, CONTEXT_README_BODY, {}
    )
    result["hashes"] = {
        SKILLS_FILE: section_hash(skills_catalog_body()),
        COMMANDS_FILE: section_hash(commands_body()),
    }
    return result


def cli_docs_context_block(root: Path | None = None, max_chars: int = MAX_CONTEXT_CHARS) -> str:
    """Builds the system-prompt block from the CLI home documents."""
    custom = custom_skills_text(root)
    lessons = lessons_text(root)
    if not custom and not lessons:
        return ""
    lines = ["CLI BECERİ VE DENEYİM KATMANI (CLI'nin kendi dosyalarından):"]
    if custom:
        lines.append("Özel beceriler (kullanıcı tanımlı, kod kataloğunu tamamlayıcı):")
        lines.append(custom)
    if lessons:
        lines.append("Önceki oturum dersleri:")
        lines.append(lessons)
    block = "\n".join(lines)
    if len(block) > max_chars:
        block = block[:max_chars].rstrip() + f"\n[…docs kırpıldı: {len(block) - max_chars} kr]"
    return block
