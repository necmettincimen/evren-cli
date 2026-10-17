"""
Project-level smart context documents for EVREN CLI (`agent.md`, `changelog.md`).

These files live in the directory the CLI is currently working in, so the agent
always knows: where it is, what the project looks like, which conventions apply,
and what was last done here.

Safety contract (see plans/smart-context-docs/plan.md):

- Missing file  -> created from a template.
- Existing file -> only `evren:managed` sections are rewritten, and only when the
  generated content actually changed (hash compare). Human text is never touched.
- A file without managed markers is left completely alone (reported as `adopt`
  suggestion) unless the user explicitly opted in.
- Windows/NTFS is case-insensitive: if the project already has `AGENTS.md`,
  writing `agent.md` would overwrite it. In that case the CLI uses
  `evren-agent.md` and never touches `AGENTS.md`.
- Every write goes through `safe_write_file` (workspace boundary + timestamped
  backup) and every generated body passes `redact_text` (secret masking).
- Any OSError degrades the feature; the CLI never crashes because of docs.
"""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from src.config import get_evren_dir, get_workspace_dir
from src.docs_merge import (
    extract_sections,
    has_managed_sections,
    render_sections,
    section_hash,
    strip_managed,
)

AGENT_DOC_NAME = "agent.md"
AGENT_DOC_ALT_NAME = "evren-agent.md"
CHANGELOG_NAME = "changelog.md"
CHANGELOG_ALT_NAME = "evren-changelog.md"
STATE_FILE = "docs_state.json"
LOCK_FILE = "docs.lock"
WARN_LOG = "logs/docs_warn.log"
AGENTS_REFERENCE_NAME = "AGENTS.md"

LOCK_TTL_SECONDS = 120
MAX_MAP_LINES = 25
# The CLI's own documents must not appear in the project map, otherwise the map
# changes the moment it is written and every run dirties `git status`.
OWN_DOC_NAMES = frozenset({
    "agent.md", "evren-agent.md", "changelog.md", "evren-changelog.md",
})
MAX_CHANGELOG_ENTRY_CHARS = 1200
RECENT_SESSION_ROWS = 10
MAX_AGENTS_REFERENCE_CHARS = 1500

# Section order used when adopting an existing unmanaged document.
AGENT_SECTIONS: tuple[str, ...] = (
    "identity",
    "runtime_env",
    "project_map",
    "conventions",
    "current_focus",
)

# Prompt'a enjeksiyon sırası (dosya şablon sırasından bilinçli olarak farklı).
# Sinyal başına maliyet: conventions/current_focus küçüktür ve doğrudan
# yönlendirir; project_map en hacimli ve en bayat bölümdür, o yüzden sonda
# durur ve bütçe yetmezse ilk o düşer.
CONTEXT_SECTION_PRIORITY: tuple[str, ...] = (
    "conventions",
    "current_focus",
    "identity",
    "runtime_env",
    "project_map",
)
CHANGELOG_SECTIONS: tuple[str, ...] = ("recent",)


@dataclass
class DocsResult:
    """Outcome of one `ensure_project_docs` run (also used for `/docs status`)."""

    root: str = ""
    agent_doc: str = ""
    changelog: str = ""
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    disabled: bool = False
    reason: str = ""
    conflict: str = ""

    @property
    def changed(self) -> bool:
        return bool(self.created or self.updated)

    def summary(self) -> str:
        """One short line for the startup banner; details go to `details()`."""
        if self.disabled:
            return f"kapalı ({self.reason})" if self.reason else "kapalı"
        names = []
        for name in list(self.created) + list(self.updated):
            base = name.split("#", 1)[0]
            if base not in names:
                names.append(base)
        if names:
            return "hazır: " + ", ".join(names)
        return "güncel"

    def details(self) -> list[str]:
        """Notes worth showing at startup; the naming conflict only on creation."""
        out = list(self.notes)
        if self.conflict and self.created:
            out.insert(0, self.conflict)
        return out


# --------------------------------------------------------------------------- #
# configuration / enablement
# --------------------------------------------------------------------------- #

def _env_flag(name: str, default: bool = True) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def docs_enabled(root: Path | None = None) -> tuple[bool, str]:
    """Returns (enabled, reason). Kill switch: EVREN_AUTO_DOCS=false."""
    if not _env_flag("EVREN_AUTO_DOCS", True):
        return False, "EVREN_AUTO_DOCS=false"
    try:
        raw = (get_evren_dir(root) / "docs.json").read_text(encoding="utf-8-sig")
        data = json.loads(raw)
    except FileNotFoundError:
        return True, ""
    except (OSError, json.JSONDecodeError, UnicodeError):
        return True, ""
    if isinstance(data, dict) and data.get("disabled") is True:
        return False, ".evren/docs.json disabled=true"
    return True, ""


# Varsayılan bağlam bütçesi. Her ajan adımı tam sistem prompt'unu yeniden
# gönderdiği için bu bütçe doğrudan adım başına maliyettir: 3500 token ~30
# adımda ~105k token. 1200, conventions + current_focus + kısa bir harita için
# yeterlidir; EVREN_DOCS_TOKENS ile yükseltilir.
DEFAULT_DOCS_TOKENS = 1200


def docs_token_budget() -> int:
    """Token ceiling for all injected doc blocks (0 disables injection)."""
    raw = (os.getenv("EVREN_DOCS_TOKENS") or "").strip()
    if not raw:
        return DEFAULT_DOCS_TOKENS
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_DOCS_TOKENS


# --------------------------------------------------------------------------- #
# state file + best-effort lock
# --------------------------------------------------------------------------- #

def state_path(root: Path | None = None) -> Path:
    return get_evren_dir(root) / STATE_FILE


def load_state(root: Path | None = None) -> dict:
    try:
        data = json.loads(state_path(root).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"schema_version": 1, "sections": {}}
    if not isinstance(data, dict):
        return {"schema_version": 1, "sections": {}}
    data.setdefault("schema_version", 1)
    if not isinstance(data.get("sections"), dict):
        data["sections"] = {}
    return data


def save_state(state: dict, root: Path | None = None) -> None:
    path = state_path(root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="",
        )
        tmp.replace(path)
    except OSError:
        pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _stamp() -> str:
    try:
        from src.version import get_version

        version = get_version()
    except Exception:  # pragma: no cover - version is optional
        version = "?"
    return f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} (EVREN CLI {version})"


def _redact(text: str) -> str:
    try:
        from src.workflow_state import redact_text

        return redact_text(text)
    except Exception:  # pragma: no cover - masking is best effort
        return text


def _warn(root: Path, message: str) -> None:
    """Records why a document was skipped; never raises."""
    try:
        path = get_evren_dir(root) / WARN_LOG
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(f"{_now()} {message}\n")
    except OSError:
        pass


def _acquire_lock(root: Path) -> bool:
    """Best-effort single-writer hint for concurrent CLI sessions."""
    try:
        path = get_evren_dir(root) / LOCK_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            age = datetime.now(timezone.utc).timestamp() - path.stat().st_mtime
            if age < LOCK_TTL_SECONDS:
                return False
        path.write_text(f"{os.getpid()} {_now()}\n", encoding="utf-8", newline="")
        return True
    except OSError:
        return True


def _release_lock(root: Path) -> None:
    try:
        (get_evren_dir(root) / LOCK_FILE).unlink(missing_ok=True)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# path helpers (NTFS case-insensitivity aware)
# --------------------------------------------------------------------------- #

def resolve_doc_name(root: Path, wanted: str = AGENT_DOC_NAME) -> tuple[str, str]:
    """Picks the agent document name that cannot clobber a differently-cased sibling.

    Returns (name, conflict_note). On NTFS `AGENTS.md` and `agent.md` are the
    same file, so a case-mismatched existing file forces `evren-agent.md` and
    the existing file is never written to.
    """
    try:
        names = {entry.name for entry in root.iterdir() if entry.is_file()}
    except OSError:
        return wanted, ""
    if wanted in names:
        return wanted, ""
    lowered = {name.lower(): name for name in names}
    clash = lowered.get(wanted.lower())
    if clash and clash != wanted:
        return AGENT_DOC_ALT_NAME, (
            f"'{clash}' zaten var; NTFS büyük/küçük harf duyarsız olduğu için "
            f"'{wanted}' yerine '{AGENT_DOC_ALT_NAME}' kullanılıyor"
        )
    # A previous run already chose the alternate name: keep using it.
    if wanted not in names and AGENT_DOC_ALT_NAME in lowered:
        return AGENT_DOC_ALT_NAME, ""
    return wanted, ""


def agent_doc_path(root: Path) -> Path:
    name, _note = resolve_doc_name(root)
    return root / name


def changelog_path(root: Path) -> Path:
    """Changelog target that never writes into a differently-cased sibling.

    A repository usually ships `CHANGELOG.md`; on NTFS that is the same file as
    `changelog.md`, so the CLI keeps its own session log in `evren-changelog.md`
    and leaves the human changelog untouched.
    """
    try:
        names = {entry.name for entry in root.iterdir() if entry.is_file()}
    except OSError:
        return root / CHANGELOG_NAME
    if CHANGELOG_NAME in names:
        return root / CHANGELOG_NAME
    lowered = {name.lower(): name for name in names}
    clash = lowered.get(CHANGELOG_NAME.lower())
    if clash and clash != CHANGELOG_NAME:
        return root / CHANGELOG_ALT_NAME
    if CHANGELOG_ALT_NAME in lowered:
        return root / CHANGELOG_ALT_NAME
    return root / CHANGELOG_NAME


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return ""


def _write_text(root: Path, path: Path, text: str) -> bool:
    """Writes through `safe_write_file` (backup only when --backup is enabled)."""
    try:
        from src.file_ops import safe_write_file, backup_enabled

        ok, _backup, error = safe_write_file(
        path, text, root, create_backup=path.exists() and backup_enabled()
    )
        if not ok and error:
            _warn(root, f"yazma reddedildi {path.name}: {error}")
        return bool(ok)
    except OSError as e:
        _warn(root, f"yazma hatası {path.name}: {e}")
        return False


# --------------------------------------------------------------------------- #
# generated section bodies
# --------------------------------------------------------------------------- #

def _git_info(root: Path) -> tuple[str, str]:
    """Returns (branch, last commit) or ("git yok", "") outside a repo."""
    try:
        branch = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        commit = subprocess.run(
            ["git", "-C", str(root), "log", "-1", "--format=%h %ad", "--date=short"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "git yok", ""
    if branch.returncode != 0:
        return "git yok", ""
    return (branch.stdout.strip() or "git yok"), commit.stdout.strip()


def _cli_version() -> str:
    try:
        from src.version import get_version

        return get_version()
    except Exception:  # pragma: no cover - version is optional
        return "?"


def identity_body(root: Path, doc_name: str) -> str:
    return "\n".join(
        [
            f"Bu dosya EVREN CLI tarafından otomatik yönetilir (`{doc_name}`).",
            "`evren:managed` işaretli bloklar CLI her çalıştığında yenilenir;",
            "bu blokların dışındaki metin korunur ve ajana bağlam olarak verilir.",
            "Canlı ortam her zaman bu dosyadan önceliklidir: yol/araç burada yazsa bile",
            "uygulamadan önce salt-okuma ile doğrula.",
        ]
    )


def runtime_env_body(root: Path) -> str:
    shell = os.environ.get("COMSPEC", "cmd.exe") if os.name == "nt" else "/bin/sh"
    branch, commit = _git_info(root)
    lines = [
        f"- Çalışma alanı: {root}",
        f"- İşletim sistemi / kabuk: {platform.system()} / {Path(shell).name}"
        " (PowerShell gerekiyorsa `powershell -NoProfile -Command` ile açıkça çağır)",
        f"- Python: {platform.python_version()} | CLI sürümü: {_cli_version()}",
        f"- Git dalı: {branch}" + (f" | son commit: {commit}" if commit else ""),
        "- Varsayılan model: " + _default_model(),
    ]
    return "\n".join(lines)


def _default_model() -> str:
    try:
        from src.config import EVREN_DEFAULT_MODEL

        return EVREN_DEFAULT_MODEL
    except Exception:  # pragma: no cover - config import is safe
        return "?"


def _cli_owned_entries(root: Path) -> set[str]:
    """Names of entries the CLI itself generated in this project directory.

    Only the `context/` folder carrying our own README signature is treated as
    CLI-owned, so a project's real `context/` directory is never hidden.
    """
    owned = {"skills.md", "commands.md", "lessons.md"}
    try:
        marker = (root / "context" / "README.md").read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return owned
    if marker.startswith("# EVREN CLI destek dosyalar"):
        owned.add("context")
        owned.add("logs")
    return owned


def _cli_owned_tree_names(root: Path) -> set[str]:
    return {name.lower() for name in _cli_owned_entries(root)}
def _is_cli_owned_path(rel_path: str, cli_owned: set[str]) -> bool:
    """True when any path segment is a CLI-generated entry (lowercased names)."""
    parts = [part.lower() for part in Path(rel_path).parts]
    return any(part in cli_owned for part in parts)


def _own_docs_size_kb(root: Path) -> float:
    """Size of the CLI's own documents, subtracted so the map stays run-stable."""
    total = 0.0
    for name in sorted(OWN_DOC_NAMES):
        try:
            path = root / name
            if path.is_file():
                total += path.stat().st_size / 1024.0
        except OSError:
            continue
    return total


def _tree_line_name(line: str) -> str:
    """Extracts the lowercased entry name from a `generate_tree` line ('' if none)."""
    for connector in ("└── ", "├── "):
        if connector in line:
            name = line.split(connector, 1)[1].strip()
            return name.lstrip("📄📁 ").rstrip("/").lower()
    return ""


def project_map_body(root: Path) -> str:
    """Summarizes the project from `project_scanner` (bounded, run-stable output)."""
    try:
        from src.project_scanner import generate_tree, scan_workspace

        stats = scan_workspace(root, max_files=400)
        tree = generate_tree(root, max_depth=2)
    except Exception as e:  # scanner must never break docs
        return f"- Proje haritası çıkarılamadı: {e}"

    cli_owned = _cli_owned_tree_names(root)
    files = [
        name for name in (stats.get("files") or [])
        if Path(name).name.lower() not in OWN_DOC_NAMES
        and not _is_cli_owned_path(name, cli_owned)
    ]
    own_count = len(stats.get("files") or []) - len(files)
    exts = dict(stats.get("extension_counts") or {})
    if own_count:
        exts[".md"] = max(0, exts.get(".md", 0) - own_count)
        if not exts[".md"]:
            exts.pop(".md", None)
    size_kb = round(max(0.0, float(stats.get("total_size_kb", 0)) - _own_docs_size_kb(root)), 1)
    top = sorted(exts.items(), key=lambda item: (-item[1], item[0]))[:8]
    lines = [
        f"- Dosya sayısı (taranan): {len(files)} | boyut: {size_kb} KB",
        "- Uzantılar: " + (", ".join(f"{ext} {count}" for ext, count in top) or "yok"),
    ]
    entry = _entry_points(root, files)
    if entry:
        lines.append("- Giriş noktası: " + entry)
    lines.append(f"- Komutlar: {_command_hints(root)}")
    lines.append("- Ağaç (derinlik 2):")
    tree_lines = [
        line for line in tree.split("\n")
        if _tree_line_name(line) not in OWN_DOC_NAMES
        and _tree_line_name(line) not in cli_owned
    ][:MAX_MAP_LINES]
    lines.extend(f"  {line}" for line in tree_lines)
    if len(tree_lines) >= MAX_MAP_LINES:
        lines.append("  … [ağaç kırpıldı; tam liste için `evren scan`]")
    return "\n".join(lines)


def _entry_points(root: Path, files: list[str]) -> str:
    hints: list[str] = []
    if (root / "pyproject.toml").exists():
        hints.append("`pyproject.toml`")
    for candidate in ("src/cli.py", "cli.py", "main.py", "manage.py", "package.json"):
        if candidate in files or (root / candidate).exists():
            hints.append(f"`{candidate}`")
    packages = sorted({
        path.split("/", 1)[0] for path in files if "/" in path
    })[:6]
    if packages:
        hints.append("paketler: " + ", ".join(f"`{name}/`" for name in packages))
    return " | ".join(hints)


def _command_hints(root: Path) -> str:
    if (root / "pyproject.toml").exists() or (root / "pytest.ini").exists() or (root / "tests").is_dir():
        return "`python -m pytest -q` (test), `python -m compileall src` (sözdizimi)"
    if (root / "package.json").exists():
        return "`npm test` / `npm run build` (package.json betiklerine bakın)"
    return "proje betiği algılanmadı; README'ye bakın"


def conventions_body() -> str:
    return "\n".join(
        [
            "- Değiştirmeden önce oku (`view_file`), hedefli `edit_file` kullan, sonra test et.",
            "- Yeni kod mevcut stille uyumlu olmalı: docstring, tip imzaları, girinti.",
            "- `.env` ve `PROTECTED_FILES` düzenlenmez; sır/anahtar hiçbir belgeye yazılmaz.",
            "- Windows: yol ayıracı `\\`, kabuk `cmd.exe`; `safe_write_file` BOM/CRLF'yi korur.",
            "- Bu dosyanın yönetilen bloklarını `edit_file` ile elle değiştirmeye çalışma;",
            "  gerekirse `/docs refresh` veya `/docs unmark` kullan.",
        ]
    )


def current_focus_body(root: Path, goal: str = "", status: str = "") -> str:
    state = load_state(root)
    session = state.get("session") if isinstance(state.get("session"), dict) else {}
    last_goal = goal.strip() or str(session.get("last_goal") or "") or "(henüz görev kaydı yok)"
    lines = [
        f"- Son oturum: {session.get('last_run') or '(kayıt yok)'}",
        f"- Son görev: {_clip(last_goal, 120)}",
        f"- Durum: {status.strip() or session.get('last_status') or 'bilinmiyor'}",
    ]
    blockers = _open_blockers(root)
    if blockers:
        lines.append("- Açık engeller: " + "; ".join(blockers))
    return "\n".join(lines)


def _open_blockers(root: Path) -> list[str]:
    try:
        from src.tracking import list_tasks
        from src.workflow_state import review_tasks

        flagged = review_tasks(list_tasks("all", root))
    except Exception:
        return []
    out: list[str] = []
    for task in flagged:
        if "blocked" not in (task.get("flags") or []):
            continue
        out.append(f"{task.get('id')}: {_clip(str(task.get('description', '')), 80)}")
    return out[:5]


def _clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# --------------------------------------------------------------------------- #
# agent.md
# --------------------------------------------------------------------------- #

def _agent_skeleton(root: Path, doc_name: str) -> str:
    sections = {
        "identity": identity_body(root, doc_name),
        "runtime_env": runtime_env_body(root),
        "project_map": project_map_body(root),
        "conventions": conventions_body(),
        "current_focus": current_focus_body(root),
    }
    body, _changed, _errors = render_sections("", sections, adopt=True, adopt_order=AGENT_SECTIONS)
    title = root.name or "proje"
    return "\n".join(
        [
            f"# {doc_name} — {title}",
            "",
            body,
            "",
            "## Amaç",
            "",
            "(Bu bölümü siz yazarsınız; CLI dokunmaz. Bu projede ajanın hedefini ve",
            "sizin önceliklerinizi buraya not edin.)",
            "",
            "## Notlar (elle)",
            "",
            "(Buraya yazılanlar korunur ve ajana bağlam olarak gösterilir.)",
            "",
        ]
    ) + "\n"


def agent_sections(root: Path, goal: str = "", status: str = "", doc_name: str = AGENT_DOC_NAME) -> dict[str, str]:
    """Builds every managed body for `agent.md` (already secret-masked)."""
    return {
        "identity": _redact(identity_body(root, doc_name)),
        "runtime_env": _redact(runtime_env_body(root)),
        "project_map": _redact(project_map_body(root)),
        "conventions": _redact(conventions_body()),
        "current_focus": _redact(current_focus_body(root, goal, status)),
    }


def ensure_agent_doc(
    root: Path,
    *,
    goal: str = "",
    status: str = "",
    refresh: bool = False,
    adopt: bool = False,
    result: DocsResult | None = None,
) -> DocsResult:
    """Creates or refreshes the project `agent.md`. Never raises."""
    result = result or DocsResult(root=str(root))
    try:
        path = agent_doc_path(root)
    except OSError as e:
        result.notes.append(f"agent.md adı çözülemedi: {e}")
        return result

    doc_name = path.name
    result.agent_doc = doc_name
    _name, conflict = resolve_doc_name(root, AGENT_DOC_NAME)
    if conflict and doc_name != AGENT_DOC_NAME:
        result.conflict = (
            conflict + f"; '{AGENTS_REFERENCE_NAME}' dosyasına dokunulmadı"
        )

    try:
        if not path.exists():
            text = _redact(_agent_skeleton(root, doc_name))
            if _write_text(root, path, text):
                result.created.append(doc_name)
                _remember_hashes(root, doc_name, text)
            return result

        current = _read_text(path)
        if not has_managed_sections(current):
            if not (adopt or refresh):
                result.skipped.append(doc_name)
                result.notes.append(
                    f"{doc_name} içinde yönetilen blok yok; eklemek için `/docs refresh --adopt`"
                )
                _warn(root, f"{doc_name}: yönetilen blok yok, dokunulmadı")
                return result
            new_text, changed, errors = render_sections(
                current,
                agent_sections(root, goal, status, doc_name),
                adopt=True,
                adopt_order=AGENT_SECTIONS,
            )
        else:
            new_text, changed, errors = render_sections(
                current,
                agent_sections(root, goal, status, doc_name),
            )
        if errors:
            result.skipped.append(doc_name)
            result.notes.append(f"{doc_name}: {errors[0]} (dokunulmadı)")
            _warn(root, f"{doc_name}: {errors[0]}")
            return result
        if not changed:
            result.skipped.append(doc_name)
            return result
        if _write_text(root, path, new_text):
            result.updated.extend(f"{doc_name}#{name}" for name in changed)
            _remember_hashes(root, doc_name, new_text)
    except OSError as e:
        result.notes.append(f"agent.md yazılamadı: {e}")
        _warn(root, f"agent.md OSError: {e}")
    return result


def _remember_hashes(root: Path, doc_name: str, text: str) -> None:
    state = load_state(root)
    sections = state.setdefault("sections", {})
    for name, body in extract_sections(text).items():
        sections[f"{doc_name}.{name}"] = section_hash(body)
    state["updated_at"] = _now()
    save_state(state, root)


# --------------------------------------------------------------------------- #
# changelog.md
# --------------------------------------------------------------------------- #

def _changelog_skeleton(root: Path) -> str:
    body, _changed, _errors = render_sections(
        "", {"recent": "- (henüz kayıt yok)"}, adopt=True, adopt_order=CHANGELOG_SECTIONS
    )
    return "\n".join(
        [
            f"# Changelog — {root.name or 'proje'}",
            "",
            "Bu dosya EVREN CLI tarafından **append-only** güncellenir; eski girişler",
            "değiştirilmez ve silinmez. Aşağıdaki yönetilen blok son oturumların",
            "özetidir ve CLI tarafından yeniden yazılır.",
            "",
            body,
            "",
        ]
    ) + "\n"


def _entry_signature(entry: dict) -> str:
    parts = [
        " ".join((entry.get("goal") or "").split())[:400],
        "|".join(sorted(entry.get("files") or [])),
        ",".join(str(code) for code in (entry.get("exit_codes") or [])),
    ]
    return section_hash("::".join(parts))


def _clean_goal(text: str) -> str:
    """Strips the prompt-frame boilerplate so the entry carries the real task.

    The task goal is stored verbatim, which means every entry began with the
    frame template ("GÖREV / Brief tek cümlelik niyettir… / Çıktı, format ve teslim
    yüzeyidir. / Brief:") and the actual work was pushed past the clip limit.
    Only the known template is removed; a goal without it is returned as-is.
    """
    goal = " ".join((text or "").split())
    for _ in range(3):
        before = goal
        goal = re.sub(r"^(asıl görev|görev)\s*(\(geçmiş bağlam\))?\s*:?\s*", "", goal, flags=re.I)
        goal = re.sub(
            r"^.*?ç\u0131kt\u0131, format ve teslim y\u00fczeyidir\.\s*(brief:)?\s*",
            "",
            goal,
            flags=re.I,
        )
        goal = re.sub(r"^brief\s*:\s*", "", goal, flags=re.I)
        if goal == before:
            break
    return goal.strip(" .-")


def append_changelog_entry(root: Path, entry: dict) -> bool:
    """Appends one session entry. Returns False when nothing was written."""
    if not isinstance(entry, dict):
        return False
    # EVREN_AUTO_DOCS=false must stop every docs write, not only the bootstrap.
    if not docs_enabled(root)[0]:
        return False
    try:
        signature = _entry_signature(entry)
        state = load_state(root)
        if state.get("last_changelog_entry") == signature:
            return False

        path = changelog_path(root)
        created = not path.exists()
        text = _read_text(path) if not created else ""
        if created:
            text = _changelog_skeleton(root)

        goal = _clip(_clean_goal(_redact(entry.get("goal") or "")), 160) or "(görev belirtilmedi)"
        files = entry.get("files") or []
        verification = _clip(_redact(str(entry.get("verification") or "")), 200)

        # Her giriş tam üç alan taşır. `commands` ve `exit_codes` payload'da
        # kalmaya devam eder (mükerrer girişi engelleyen imzayı bunlar üretir),
        # ancak artık yazdırılmazlar: büyük çoğunluğu salt-okuma keşif gürültüsüydü
        # ve sabit geri alma satırı her girişde aynen tekrarlanıyordu.
        block = [
            f"## {_stamp()}",
            "",
            f"- Görev: {goal}",
            f"- Değişen dosyalar: "
            + (", ".join(f"`{name}`" for name in files[:12]) if files else "dosya değişikliği yok"),
            f"- Doğrulama: {verification or 'doğrulama çalıştırılmadı'}",
        ]
        chunk = "\n".join(block)
        if len(chunk) > MAX_CHANGELOG_ENTRY_CHARS:
            chunk = chunk[:MAX_CHANGELOG_ENTRY_CHARS].rstrip() + " …"

        recent = _recent_lines(text, entry, goal, files)
        new_text, changed, errors = render_sections(text, {"recent": recent})
        if errors:
            _warn(root, f"{CHANGELOG_NAME}: {errors[0]}")
            return False
        new_text = new_text.rstrip("\n") + "\n\n" + chunk + "\n"
        if not changed and not created:
            # `recent` already identical; still append the new detailed entry.
            pass
        if not _write_text(root, path, _redact(new_text)):
            return False

        state = load_state(root)
        state["last_changelog_entry"] = signature
        state["updated_at"] = _now()
        save_state(state, root)
        return True
    except OSError as e:
        _warn(root, f"changelog OSError: {e}")
        return False


def _recent_lines(text: str, entry: dict, goal: str, files: list[str]) -> str:
    """Rebuilds the managed 'recent' block (newest last, bounded rows)."""
    existing = extract_sections(text).get("recent", "")
    rows = [
        line for line in existing.split("\n")
        if line.strip().startswith("-") and "henüz kayıt yok" not in line
    ]
    files_part = ", ".join(f"`{name}`" for name in files[:4]) or "dosya değişikliği yok"
    if len(files) > 4:
        files_part += f" (+{len(files) - 4})"
    rows.append(
        f"- {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} | {_clip(goal, 80)}"
        f" | {files_part}"
    )
    return "\n".join(rows[-RECENT_SESSION_ROWS:])


def ensure_changelog(root: Path, result: DocsResult | None = None) -> DocsResult:
    """Creates `changelog.md` when missing. Never raises."""
    result = result or DocsResult(root=str(root))
    result.changelog = changelog_path(root).name
    try:
        path = changelog_path(root)
        # Expected, stable state (the repo ships CHANGELOG.md): explain it once,
        # when the alternative file is created, not as a warning on every start.
        if path.name != CHANGELOG_NAME and not path.exists():
            result.notes.append(
                f"'{path.name}' kullanılıyor: NTFS büyük/küçük harf duyarsız, "
                "mevcut CHANGELOG.md korunuyor"
            )
        if path.exists():
            result.skipped.append(path.name)
            return result
        if _write_text(root, path, _redact(_changelog_skeleton(root))):
            result.created.append(path.name)
    except OSError as e:
        result.notes.append(f"changelog.md yazılamadı: {e}")
    return result


# --------------------------------------------------------------------------- #
# orchestration
# --------------------------------------------------------------------------- #

def ensure_project_docs(
    root: Path | None = None,
    *,
    goal: str = "",
    status: str = "",
    refresh: bool = False,
    adopt: bool = False,
    read_only: bool = False,
) -> DocsResult:
    """Creates/refreshes `agent.md` + `changelog.md` for the active workspace."""
    root = (root or get_workspace_dir()).resolve()
    result = DocsResult(root=str(root))
    enabled, reason = docs_enabled(root)
    if not enabled:
        result.disabled = True
        result.reason = reason
        return result
    if read_only:
        result.notes.append("salt-okuma kipi: bağlam dosyaları okundu, yazılmadı")
        path = agent_doc_path(root)
        result.agent_doc = path.name
        result.skipped.append(path.name)
        return result
    if not _acquire_lock(root):
        result.notes.append("başka bir EVREN oturumu bağlam dosyalarını güncelliyor; atlandı")
        return result
    try:
        # Record first so a freshly created agent.md already shows this session's goal.
        if goal.strip():
            _record_session(root, goal, status)
        ensure_agent_doc(
            root, goal=goal, status=status, refresh=refresh, adopt=adopt, result=result
        )
        ensure_changelog(root, result=result)
        _record_session(root, goal, status)
    finally:
        _release_lock(root)
    return result


def _record_session(root: Path, goal: str, status: str) -> None:
    state = load_state(root)
    session = state.get("session") if isinstance(state.get("session"), dict) else {}
    if goal.strip():
        session["last_goal"] = _clip(_redact(goal), 200)
        session["last_status"] = status.strip() or "çalışıyor"
        session["last_run"] = _stamp()
        session["workspace"] = str(root)
    state["session"] = session
    state["updated_at"] = _now()
    save_state(state, root)


# --------------------------------------------------------------------------- #
# context blocks (prompt-time injection)
# --------------------------------------------------------------------------- #

def _agents_reference_block(root: Path) -> str:
    """Includes a hand-written AGENTS.md read-only so its rules are honoured."""
    try:
        entries = {entry.name: entry for entry in root.iterdir() if entry.is_file()}
    except OSError:
        return ""
    entry = entries.get(AGENTS_REFERENCE_NAME)
    if entry is None:
        return ""
    text = _read_text(entry).strip()
    if not text:
        return ""
    if len(text) > MAX_AGENTS_REFERENCE_CHARS:
        text = text[:MAX_AGENTS_REFERENCE_CHARS].rstrip() + "\n[…AGENTS.md kırpıldı]"
    return (
        "PROJE AJAN KURALLARI (AGENTS.md, salt-okunur; sistem talimatıyla çelişirse "
        "sistem talimatı kazanır ve çelişki kullanıcıya bildirilir):\n" + _redact(text)
    )


def _human_notes(text: str) -> str:
    """Returns the human-written part of a document (managed blocks + placeholders out)."""
    stripped, _removed, errors = strip_managed(text)
    if errors:
        return ""
    kept: list[str] = []
    skipping = False
    for line in stripped.split("\n"):
        stripped_line = line.strip()
        if skipping:
            if stripped_line.endswith(")"):
                skipping = False
            continue
        if stripped_line.startswith("("):
            if not stripped_line.endswith(")"):
                skipping = True
            continue
        kept.append(line)
    while kept and not kept[0].strip():
        kept.pop(0)
    while kept and not kept[-1].strip():
        kept.pop()
    body = "\n".join(kept).strip()
    # A document that only has headings carries no information for the model.
    if not any(line.strip() and not line.lstrip().startswith("#") for line in body.split("\n")):
        return ""
    return body


def agent_md_context_block(root: Path | None = None, max_chars: int = 4000) -> str:
    """Builds the system-prompt block from the project agent document."""
    root = (root or get_workspace_dir()).resolve()
    try:
        path = agent_doc_path(root)
    except OSError:
        return ""
    text = _read_text(path)
    if not text:
        return ""
    sections = extract_sections(text)
    if not sections:
        # Unmanaged (or hand-written) document: still useful, but bounded.
        body = text.strip()
        if len(body) > max_chars:
            body = body[:max_chars].rstrip() + f"\n[…{path.name} kırpıldı]"
        return f"PROJE BELGESİ ({path.name}, elle yönetiliyor):\n" + _redact(body)

    order = [name for name in CONTEXT_SECTION_PRIORITY if name in sections]
    order += [name for name in sections if name not in CONTEXT_SECTION_PRIORITY]
    lines = [
        f"PROJE BAĞLAM BELGESİ ({path.name}, CLI tarafından güncellenir; hazır bağlam olarak "
        "kullan, canlı dosyayla çelişirse canlı dosya esastır):"
    ]
    used = 0
    for name in order:
        chunk = f"[{name}]\n{sections[name]}".strip()
        if used + len(chunk) > max_chars:
            lines.append(f"[…{path.name} kırpıldı: kalan {len(order) - order.index(name)} bölüm]")
            break
        lines.append(chunk)
        used += len(chunk)

    # The user's own sections (Amaç / Notlar) are the highest-signal part of the
    # document, so they are appended after the generated blocks.
    notes = _human_notes(text)
    if notes and used + len(notes) <= max_chars:
        lines.append(f"[{path.name} içindeki elle bölümler]\n{notes}")
    elif notes:
        room = max(0, max_chars - used - 60)
        if room > 80:
            lines.append(
                f"[{path.name} içindeki elle bölümler]\n{notes[:room].rstrip()}\n"
                f"[…elle bölümler kırpıldı: {len(notes) - room} kr]"
            )
    return _redact("\n".join(lines))


def changelog_context_block(root: Path | None = None, max_chars: int = 1500) -> str:
    """Builds the 'recent sessions' block from `changelog.md`."""
    root = (root or get_workspace_dir()).resolve()
    text = _read_text(changelog_path(root))
    if not text:
        return ""
    recent = extract_sections(text).get("recent", "").strip()
    if not recent or "henüz kayıt yok" in recent:
        return ""
    if len(recent) > max_chars:
        recent = recent[-max_chars:].lstrip("-\n ")
        recent = "[…eski girdiler kırpıldı]\n" + recent
    return (
        "PROJE DEĞİŞİKLİK GÜNLÜĞÜ (son işler; geçmiş kayıttır, yeniden doğrulamaya "
        "gerek yok):\n" + _redact(recent)
    )


def docs_context_block(root: Path | None = None, budget_tokens: int | None = None) -> str:
    """Combines agent.md + changelog.md + AGENTS.md within a token budget."""
    root = (root or get_workspace_dir()).resolve()
    budget = docs_token_budget() if budget_tokens is None else budget_tokens
    if budget <= 0:
        return ""
    # ~4 chars/token heuristic; changelog gets the smaller share.
    total_chars = budget * 4
    changelog_chars = min(1500, max(300, total_chars // 5))
    agent_chars = max(800, total_chars - changelog_chars - 1200)

    blocks: list[str] = []
    reference = _agents_reference_block(root)
    if reference:
        blocks.append(reference)
    agent_block = agent_md_context_block(root, max_chars=agent_chars)
    if agent_block:
        blocks.append(agent_block)
    changelog_block = changelog_context_block(root, max_chars=changelog_chars)
    if changelog_block:
        blocks.append(changelog_block)
    return "\n\n".join(blocks)


def docs_digest(root: Path | None = None, max_chars: int = 1200) -> str:
    """Very short project brief for the prompt-preparation (clarify) call."""
    root = (root or get_workspace_dir()).resolve()
    try:
        path = agent_doc_path(root)
    except OSError:
        return ""
    sections = extract_sections(_read_text(path))
    if not sections:
        return ""
    lines = [f"Proje: {root.name} ({root})"]
    for name in ("runtime_env", "project_map", "conventions"):
        body = sections.get(name, "")
        for line in body.split("\n"):
            line = line.strip("- ").strip()
            if line:
                lines.append(line)
    recent = extract_sections(_read_text(changelog_path(root))).get("recent", "")
    tail = [line for line in recent.split("\n") if line.strip().startswith("-")][-3:]
    if tail:
        lines.append("Son işler: " + " / ".join(_clip(line.lstrip("- "), 90) for line in tail))
    digest = "\n".join(lines)
    if len(digest) > max_chars:
        digest = digest[:max_chars].rstrip() + "…"
    return _redact(digest)


# --------------------------------------------------------------------------- #
# maintenance surface (`/docs`, `evren docs`)
# --------------------------------------------------------------------------- #

def docs_status(root: Path | None = None) -> dict:
    """Reports the docs layer for `evren docs status` / `/docs status`."""
    root = (root or get_workspace_dir()).resolve()
    enabled, reason = docs_enabled(root)
    try:
        path = agent_doc_path(root)
    except OSError:
        path = root / AGENT_DOC_NAME
    agent_sections_found = extract_sections(_read_text(path)) if path.exists() else {}
    changelog = changelog_path(root)
    state = load_state(root)
    return {
        "root": str(root),
        "enabled": enabled,
        "reason": reason,
        "agent_doc": str(path) if path.exists() else "",
        "agent_sections": sorted(agent_sections_found),
        "changelog": str(changelog) if changelog.exists() else "",
        "changelog_entries": _read_text(changelog).count("\n## "),
        "state": str(state_path(root)) if state_path(root).exists() else "",
        "token_budget": docs_token_budget(),
    }


def unmark_docs(root: Path | None = None) -> DocsResult:
    """Removes managed blocks (keeps human text). Used by `/docs unmark`."""
    root = (root or get_workspace_dir()).resolve()
    result = DocsResult(root=str(root))
    for name in (agent_doc_path(root).name, changelog_path(root).name):
        path = root / name
        if not path.exists():
            continue
        current = _read_text(path)
        new_text, removed, errors = strip_managed(current)
        if errors:
            result.notes.append(f"{name}: {errors[0]} (dokunulmadı)")
            continue
        if not removed:
            result.skipped.append(name)
            continue
        if _write_text(root, path, new_text):
            result.updated.append(f"{name} (yönetilen bloklar kaldırıldı: {', '.join(removed)})")
    return result
