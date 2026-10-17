"""
Extra REPL commands and input helpers (plan cli-stability-speed, Faz 6 and Faz 8 "S").

Contains: project settings (`.evren/settings.json`), persistent role (`/rol`),
`@path` mentions, custom `.md` commands, `/commit /review /init /context /compact
/rewind /doctor`, the "continue" note, the REPL status line / completer and the
headless `evren -p` runner. REPL wiring lives in `src/repl.py`.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from src.ui import print_error, print_info, print_success, print_warning, prompt_confirm

# --------------------------------------------------------------------------- #
# Project settings and the persistent role (6.7)
# --------------------------------------------------------------------------- #


def settings_path(workspace_root) -> Path:
    return Path(workspace_root) / ".evren" / "settings.json"


def load_settings(workspace_root) -> dict[str, Any]:
    try:
        data = json.loads(settings_path(workspace_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(workspace_root, data: dict[str, Any]) -> None:
    path = settings_path(workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_role(workspace_root) -> str:
    return str(load_settings(workspace_root).get("role") or "").strip()


def role_context_block(workspace_root) -> str:
    """System block for the persistent role ('' when unset)."""
    role = get_role(workspace_root)
    return f"KALICI ROL (kullanıcı ayarı, her görevde geçerli)\nRol: {role}" if role else ""


def handle_role(arg: str, session) -> None:
    root = session.workspace_root
    arg = (arg or "").strip()
    if not arg:
        role = get_role(root)
        print_info(f"Kalıcı rol: {role}" if role else "Kalıcı rol ayarlanmamış. Ayarlamak için: /rol <metin>")
        return
    data = load_settings(root)
    if arg.casefold() in ("temizle", "clear", "sil"):
        data.pop("role", None)
        save_settings(root, data)
        session.refresh_context()
        print_success("Kalıcı rol temizlendi.")
        return
    data["role"] = arg
    save_settings(root, data)
    session.refresh_context()
    print_success(f"Kalıcı rol ayarlandı: {arg}")


# --------------------------------------------------------------------------- #
# "devam" flow (6.6)
# --------------------------------------------------------------------------- #

CONTINUE_PHRASES = frozenset({
    "devam", "devam et", "onaylıyorum", "onaylıyorum devam et", "kalan işleri tamamla",
    "kalanları tamamla", "kalan işleri yap",
})


def is_continue_phrase(text: str) -> bool:
    normalized = " ".join(re.sub(r"[.!?,;]+", " ", (text or "").casefold()).split())
    return normalized in CONTINUE_PHRASES


def continue_note(session) -> str:
    """Follow-up text that resumes the last task from its todo list."""
    from src.todos import get_store

    items = get_store().snapshot()
    pending = [i for i in items if i["status"] != "completed"]
    lines = ["Kullanıcı devam etmeni onayladı."]
    if items:
        lines.append(get_store().render())
    if pending:
        nxt = next((i for i in pending if i["status"] == "in_progress"), pending[0])
        lines.append(
            f"Sıradaki madde: {nxt['content']}. Keşfi tekrarlama; doğrudan bu maddeyle "
            "ve kalan bekleyen maddelerle devam et, bitirdikçe todo_write ile işaretle."
        )
    elif items:
        lines.append("Todo listesindeki tüm maddeler tamamlanmış görünüyor; bunu kullanıcıya söyle, yeni iş uydurma.")
    else:
        lines.append(
            "Todo listesi yok. Önceki turun son planına/yanıtına göre uygulanmamış işi bul "
            "(yeniden keşfetme); uygulanmamış madde yoksa bunu söyle."
        )
    return "\n".join(lines)


def resume_last_task(session, run_agent: Callable) -> None:
    if not session.task_goal:
        print_warning("Devam edilecek önceki görev yok; önce bir görev yazın.")
        return
    run_agent(session, continue_note(session), followup=True)


# --------------------------------------------------------------------------- #
# @path mentions (Faz 8)
# --------------------------------------------------------------------------- #

MENTION_MAX_LINES = 400
_MENTION_RE = re.compile(r"(?<![\w@])@([\w./\\-]+)(?::(\d+)-(\d+))?")


def expand_mentions(text: str, workspace_root) -> tuple[str, list[str]]:
    """Appends the content of `@path` / `@path:10-40` mentions; returns (text, warnings)."""
    root = Path(workspace_root)
    blocks: list[str] = []
    warnings: list[str] = []
    seen: set[tuple] = set()
    for m in _MENTION_RE.finditer(text or ""):
        rel, start, end = m.group(1).rstrip(".,;"), m.group(2), m.group(3)
        key = (rel, start, end)
        if key in seen:
            continue
        seen.add(key)
        path = (root / rel)
        try:
            path = path.resolve()
            path.relative_to(root.resolve())
        except (OSError, ValueError):
            continue
        if path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif") and path.is_file():
            blocks.append(f"[@{rel}] görsel dosya: içeriği için view_file aracını kullan.")
            continue
        if not path.is_file():
            # Plain words such as "@kullanici" are not file mentions; warn only for path-like ones.
            if "." in rel or "/" in rel or "\\" in rel:
                warnings.append(f"@{rel} bulunamadı.")
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as e:
            warnings.append(f"@{rel} okunamadı: {e}")
            continue
        first = max(1, int(start)) if start else 1
        last = min(len(lines), int(end)) if end else len(lines)
        chosen = lines[first - 1:last]
        note = ""
        if len(chosen) > MENTION_MAX_LINES:
            chosen = chosen[:MENTION_MAX_LINES]
            note = f"\n... [{MENTION_MAX_LINES} satır sınırı]"
        body = "\n".join(f"{first + i}: {ln}" for i, ln in enumerate(chosen))
        blocks.append(f"[@{rel} satır {first}-{first + len(chosen) - 1}]\n{body}{note}")
    if not blocks:
        return text, warnings
    return text + "\n\n[Eklenen dosyalar]\n" + "\n\n".join(blocks), warnings


# --------------------------------------------------------------------------- #
# Custom commands from .evren/commands/*.md (Faz 8)
# --------------------------------------------------------------------------- #


def custom_command_dirs(workspace_root) -> list[Path]:
    return [Path(workspace_root) / ".evren" / "commands", Path.home() / ".evren" / "commands"]


def load_custom_commands(workspace_root) -> dict[str, dict[str, str]]:
    """Maps '/name' -> {body, desc, path}. Project files win over user-level ones."""
    from src.commands import COMMAND_ALIASES

    found: dict[str, dict[str, str]] = {}
    builtin = set(COMMAND_ALIASES)
    for directory in reversed(custom_command_dirs(workspace_root)):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            name = "/" + path.stem.lower()
            if name in builtin or not re.fullmatch(r"/[a-z][\w-]*", name):
                continue
            try:
                body = path.read_text(encoding="utf-8")
            except OSError:
                continue
            first = next((ln.strip("# ").strip() for ln in body.splitlines() if ln.strip()), "")
            found[name] = {"body": body, "desc": first[:60] or "özel komut", "path": str(path)}
    return found


def refresh_custom_commands(workspace_root) -> dict[str, dict[str, str]]:
    from src.commands import set_dynamic_commands

    cmds = load_custom_commands(workspace_root)
    set_dynamic_commands(cmds.keys())
    return cmds


def render_custom_command(entry: dict[str, str], args: str) -> str:
    body = entry["body"]
    if "$ARGUMENTS" in body:
        return body.replace("$ARGUMENTS", args)
    return body + (f"\n\n{args}" if args else "")


# --------------------------------------------------------------------------- #
# git helpers and /commit /review /init
# --------------------------------------------------------------------------- #


def _git(workspace_root, *args: str, timeout: int = 30) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(workspace_root), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)
    return proc.returncode, (proc.stdout or "") + (proc.stderr if proc.returncode else "")


def _response_text(response: Any) -> str:
    try:
        return (response.choices[0].message.content or "").strip()
    except (AttributeError, IndexError):
        return ""


def handle_commit(session) -> None:
    root = session.workspace_root
    code, _ = _git(root, "rev-parse", "--is-inside-work-tree")
    if code != 0:
        print_warning("Bu dizin bir git deposu değil.")
        return
    _, status = _git(root, "status", "--short")
    if not status.strip():
        print_info("Commit edilecek değişiklik yok.")
        return
    _, staged = _git(root, "diff", "--cached", "--stat")
    use_cached = bool(staged.strip())
    diff_args = ["diff", "--cached"] if use_cached else ["diff", "HEAD"]
    _, stat = _git(root, *diff_args, "--stat")
    _, diff = _git(root, *diff_args)
    _, log = _git(root, "log", "-5", "--format=%s")
    prompt = (
        "Aşağıdaki git değişikliği için TEK bir commit mesajı yaz. Depodaki son commit "
        "mesajlarının biçimini ve dilini izle. Yalnızca mesajı döndür (başlık + gerekirse "
        "kısa gövde), açıklama veya tırnak ekleme.\n\n"
        f"SON 5 COMMIT:\n{log.strip()}\n\nDEĞİŞİKLİK ÖZETİ:\n{stat.strip()}\n\n"
        f"DIFF (kırpılmış):\n{diff[:12000]}"
    )
    try:
        response = session.client.chat_complete(
            messages=[{"role": "user", "content": prompt}], model=session.model,
            max_tokens=400, temperature=0.2, thinking=False,
        )
    except Exception as e:  # network / API failures must not kill the REPL
        print_error(f"Commit mesajı üretilemedi: {e}")
        return
    message = _response_text(response).strip("`").strip()
    if not message:
        print_error("Model boş commit mesajı döndürdü.")
        return
    print_info("Önerilen commit mesajı:")
    print(message)
    scope = "hazırlanmış (staged) değişiklikler" if use_cached else "tüm değişiklikler (git add -A)"
    if not prompt_confirm(f"Commit edilsin mi? Kapsam: {scope}. Push yapılmaz.", default=False):
        print_info("Commit iptal edildi.")
        return
    if not use_cached:
        _git(root, "add", "-A")
    code, out = _git(root, "commit", "-m", message)
    (print_success if code == 0 else print_error)(out.strip().splitlines()[0] if out.strip() else "commit")


def handle_review(session, run_agent: Callable) -> None:
    code, diff = _git(session.workspace_root, "diff", "HEAD", "--stat")
    if code != 0 or not diff.strip():
        print_info("İncelenecek git değişikliği yok.")
        return
    run_agent(
        session,
        "ask: Çalışma dizinindeki değişiklikleri `git diff HEAD` ile incele ve kod incelemesi yap: "
        "hatalar, güvenlik, eksik testler, gereksiz karmaşıklık. Dosya:satır göstererek, önem sırasıyla, "
        "kısa yaz. Dosyaları değiştirme.",
    )


def handle_init(session, run_agent: Callable) -> None:
    target = Path(session.workspace_root) / "AGENTS.md"
    if target.exists() and not prompt_confirm("AGENTS.md zaten var. Üzerine yazılsın mı?", default=False):
        print_info("/init iptal edildi.")
        return
    run_agent(
        session,
        "Bu projeyi kısaca incele (yapı, dil/çatı, test ve derleme komutları, kod stili) ve proje kökünde "
        "AGENTS.md dosyasını write_file ile oluştur: proje özeti, dizin yapısı, kurulum/test/çalıştırma "
        "komutları, kodlama kuralları ve ajan için uyarılar. 80 satırı geçme; sır yazma.",
    )


# --------------------------------------------------------------------------- #
# /context /compact /rewind /doctor
# --------------------------------------------------------------------------- #


def handle_context(session) -> None:
    tm = session.token_manager
    messages = session.messages
    system = sum(tm.estimate_message(m) for m in messages if m.get("role") == "system")
    try:
        from src.tools import tools_for_mode

        tools = tm.estimate_text(json.dumps(tools_for_mode(session.mode), ensure_ascii=False))
    except Exception:
        tools = 0
    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    tool_total = sum(tm.estimate_message(m) for m in tool_msgs)
    other = sum(tm.estimate_message(m) for m in messages if m.get("role") not in ("system", "tool"))
    total = system + tools + tool_total + other
    window = tm.context_window or 1
    print_info("Bağlam kırılımı (tahmini token):")
    print(f"  • Sistem istemi        : {system:,}")
    print(f"  • Araç şemaları        : {tools:,}")
    print(f"  • Konuşma (kullanıcı/model): {other:,}")
    print(f"  • Araç çıktıları       : {tool_total:,} ({len(tool_msgs)} adet)")
    print(f"  • Toplam               : {total:,} / {window:,} (%{round(100 * total / window)})")
    biggest = sorted(tool_msgs, key=tm.estimate_message, reverse=True)[:3]
    if biggest:
        print("  Son/en büyük araç çıktıları:")
        for m in biggest:
            first = str(m.get("content") or "").strip().splitlines()[:1]
            print(f"    - {tm.estimate_message(m):,} tk: {(first[0] if first else '')[:70]}")


def handle_compact(arg: str, session) -> None:
    before = session.token_manager.estimate_messages(session.messages)
    messages = list(session.messages)
    if arg.strip():
        messages.append({"role": "user", "content": f"Sıkıştırırken şuna odaklan: {arg.strip()}"})
    output_budget = session.token_manager.resolve_output_budget(session.model, 4096)
    limit = max(1024, int(before * 0.3))
    compacted = session.compactor.compact(messages, output_budget, max_tokens=limit)
    if arg.strip() and compacted and compacted[-1].get("content", "").startswith("Sıkıştırırken şuna"):
        compacted = compacted[:-1]
    session.messages = compacted
    after = session.token_manager.estimate_messages(session.messages)
    print_success(f"Geçmiş sıkıştırıldı: {before:,} -> {after:,} token (tahmini).")


def handle_rewind(session) -> None:
    from src.file_ops import resolve_workspace_path, rollback_last_backup

    from src.file_ops import TASK_CHANGES

    root = Path(session.workspace_root)
    if TASK_CHANGES:
        names = [Path(k).name for k in TASK_CHANGES]
        print_info("Son görevde değişen dosyalar: " + ", ".join(names))
        if not prompt_confirm("Dosyalar özgün haline döndürülsün mü?", default=False):
            return
        for key, orig in list(TASK_CHANGES.items()):
            fp = Path(key)
            try:
                if orig is None:
                    if fp.exists() and prompt_confirm(f"{fp.name} bu görevde oluşturuldu; silinsin mi?", default=False):
                        fp.unlink()
                        print_success(f"{fp.name}: silindi.")
                else:
                    from src.file_ops import atomic_write_text
                    atomic_write_text(fp, orig.encode("utf-8", errors="surrogateescape").decode("utf-8", errors="replace"))
                    print_success(f"{fp.name}: özgün haline döndürüldü.")
            except OSError as e:
                print_error(f"{fp.name}: geri alınamadı ({type(e).__name__}: {e})")
        TASK_CHANGES.clear()
        return
    files: list[str] = []
    for rec in session.tool_trace:
        path = rec.get("path")
        if path and rec.get("ok") and path not in files:
            files.append(str(path))
    if not files:
        print_info("Son görevde değiştirilen dosya kaydı yok.")
        return
    print_info("Son görevde değişen dosyalar: " + ", ".join(files))
    if not prompt_confirm("Bu dosyalar yedeklerinden geri yüklensin mi?", default=False):
        return
    for rel in files:
        target = resolve_workspace_path(rel, root)
        ok, msg = rollback_last_backup(target, root)
        if ok:
            print_success(msg)
            continue
        # No backup (backups are opt-in): fall back to git for tracked files.
        code, _ = _git(root, "ls-files", "--error-unmatch", rel)
        if code == 0 and prompt_confirm(f"{rel} için yedek yok; git ile son commit haline dönülsün mü?", default=False):
            code, out = _git(root, "checkout", "--", rel)
            (print_success if code == 0 else print_error)(f"{rel}: " + ("git ile geri alındı." if code == 0 else out.strip()))
        else:
            print_warning(f"{rel}: geri alınamadı ({msg})")


def handle_doctor(session) -> None:
    root = Path(session.workspace_root)
    print_info("EVREN tanılama:")
    print(f"  • Python               : {sys.version.split()[0]} ({sys.executable})")
    try:
        out = subprocess.run(["where", "evren"], capture_output=True, text=True, timeout=10).stdout
        paths = [ln for ln in out.splitlines() if ln.strip()]
    except (OSError, subprocess.SubprocessError):
        paths = []
    print(f"  • PATH'te evren        : {len(paths)} adet" + ("" if len(paths) <= 1 else "  (UYARI: birden fazla)"))
    for p in paths:
        print(f"      {p}")
    from src.version import get_version

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    src_ver = ""
    try:
        m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', pyproject.read_text(encoding="utf-8"))
        src_ver = m.group(1) if m else ""
    except OSError:
        pass
    ver = get_version()
    mism = f"  (UYARI: pyproject {src_ver})" if src_ver and src_ver != ver else ""
    print(f"  • Sürüm                : {ver}{mism}")
    client = session.client
    if client.key_pool.is_empty():
        print("  • API erişimi          : anahtar yok, atlandı")
    else:
        try:
            import httpx

            r = httpx.head(client.base_url, timeout=3.0, follow_redirects=True)
            print(f"  • API erişimi          : {client.base_url} -> HTTP {r.status_code}")
        except Exception as e:
            print(f"  • API erişimi          : ulaşılamadı ({type(e).__name__})")
        try:
            from src.repl import print_key_quotas

            print_key_quotas(client.get_all_quotas())
        except Exception as e:
            print(f"  • Anahtar kotaları     : okunamadı ({type(e).__name__})")
    try:
        from src.ssh_hosts import load_hosts

        print(f"  • SSH host sayısı      : {len(load_hosts(root))}")
    except Exception:
        print("  • SSH host sayısı      : okunamadı")
    print(f"  • git                  : {'var' if shutil.which('git') else 'YOK'}")


# --------------------------------------------------------------------------- #
# Status line and completer (Faz 8)
# --------------------------------------------------------------------------- #

COMMAND_DESCRIPTIONS = {
    "/help": "komut listesi", "/exit": "çıkış", "/version": "sürüm", "/model": "model değiştir",
    "/mode": "kip (normal|ask|plan)", "/effort": "düşünme yoğunluğu", "/models": "modelleri listele",
    "/quota": "kalan kota", "/tokens": "token bütçesi", "/context": "bağlam kırılımı",
    "/compact": "geçmişi şimdi sıkıştır", "/commit": "diff'ten commit mesajı + commit",
    "/review": "git diff incelemesi", "/init": "AGENTS.md üret", "/rewind": "son görevi geri al",
    "/doctor": "ortam tanılama", "/rol": "kalıcı rol", "/undo": "dosya yedeğini geri yükle",
    "/clear": "geçmişi temizle", "/todos": "görev listesi", "/steps": "adım tavanı",
    "/auto": "otomatik onay", "/run": "komut çalıştır", "/ssh": "SSH host'ları", "/update": "güncelle",
    "/continue": "duraklayan görevi sürdür", "/keys": "API anahtarları", "/docs": "bağlam dosyaları",
    "/skills": "beceriler", "/skill": "beceri uygula", "/scan": "dizin ağacı", "/files": "bağlam dosyaları",
    "/add": "bağlama dosya ekle", "/drop": "bağlamdan çıkar", "/debug": "görev adım tablosu",
}


def make_completer(workspace_root):
    """prompt_toolkit completer for slash commands (with descriptions)."""
    from prompt_toolkit.completion import Completer, Completion

    class _SlashCompleter(Completer):
        def get_completions(self, document, complete_event):
            text = document.text_before_cursor
            if not text.startswith("/") or " " in text:
                return
            from src.commands import COMMAND_ALIASES, DYNAMIC_COMMANDS

            for name in sorted({*COMMAND_ALIASES, *DYNAMIC_COMMANDS}):
                if name.startswith(text.lower()):
                    yield Completion(
                        name, start_position=-len(text),
                        display_meta=COMMAND_DESCRIPTIONS.get(name, "özel komut" if name in DYNAMIC_COMMANDS else ""),
                    )

    return _SlashCompleter()


def make_status_line(session) -> Callable[[], str]:
    """Bottom toolbar text: model, mode, context %, budget."""
    def render() -> str:
        try:
            rep = session.token_report()
            window = rep["context_window"] or 1
            pct = round(100 * rep["estimated_prompt_tokens"] / window)
            return (
                f" {session.model} | kip: {session.mode.value} | bağlam %{pct} "
                f"({rep['estimated_prompt_tokens']:,}/{window:,}) | sıkıştırma eşiği {rep['compaction_limit']:,} "
            )
        except Exception:
            return f" {session.model} | kip: {session.mode.value} "

    return render


# --------------------------------------------------------------------------- #
# Headless mode: evren -p "..." --output json
# --------------------------------------------------------------------------- #


def run_headless(args) -> int:
    """Runs one task without the REPL. Returns the process exit code."""
    from src.agent import AgentSession
    from src.api_client import EvrenClient
    from src.config import get_workspace_dir

    workspace = get_workspace_dir(args.workspace)
    client = EvrenClient(default_model=args.model)
    from src import settings as _settings

    _settings.set_workspace(workspace)
    _settings.EXTRA_ALLOW[:] = [t.strip() for t in (getattr(args, "allowed_tools", None) or "").split(",") if t.strip()]
    # No forced auto-approve: only --yes (or allowed tools / settings rules) skip prompts.
    session = AgentSession(client=client, workspace_root=workspace, model=args.model,
                           auto_approve=bool(getattr(args, "yes", False)))
    text, _warn = expand_mentions(args.print_prompt, workspace)
    result, code = "", 0
    import contextlib

    quiet = contextlib.redirect_stdout(sys.stderr) if getattr(args, "output", "text") == "json" else contextlib.nullcontext()
    try:
        with quiet:
            result = session.run_step(text, max_steps=getattr(args, "max_steps", None), compact=True) or ""
        if result.startswith(("HATA:", "İPTAL:")):
            code = 1
    except Exception as e:
        result, code = f"HATA: {e}", 1
    if getattr(args, "output", "text") == "json":
        _, status = _git(workspace, "status", "--short")
        changed = [ln[3:].strip() for ln in status.splitlines() if ln.strip()]
        try:
            tokens = session.token_report().get("session_usage")
        except Exception:
            tokens = None
        payload = {
            "result": result, "changed_files": changed, "tokens": tokens,
            "exit_code": code, "model": session.model,
        }
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    elif result:
        sys.stdout.write(result + "\n")
    return code
