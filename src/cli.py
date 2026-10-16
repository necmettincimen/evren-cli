"""
Main CLI entry point for EVREN CLI.
Provides both the interactive EVREN TUI mode and scriptable subcommands.
"""

import sys
import os
import argparse
from pathlib import Path

# Fix Windows console encoding (e.g. cp1254 / cp857 / cp437)
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from src.config import (
    EVREN_DEFAULT_MODEL,
    EVREN_DEFAULT_MAX_TOKENS,
    get_workspace_dir,
    check_api_key_configured,
)
from src.version import get_version
from src.ui import (
    print_info,
    print_success,
    print_warning,
    print_error,
    print_markdown,
    print_reasoning,
)



CHAT_SYSTEM = "Sen Windows üzerinde çalışan uzman bir yazılım mühendisi asistanısın."



def _pin_best_key(client) -> None:
    """With several keys, start on the one with the most daily quota left."""
    if len(client.key_pool) < 2:
        return
    try:
        row = client.select_best_key()
    except Exception as e:
        print_warning(f"Anahtar kotaları okunamadı; ilk anahtarla devam: {e}")
        return
    if row:
        from src.api_key_pool import mask_key

        remaining = row.get("remaining_daily_tokens")
        shown = f"{remaining:,}" if isinstance(remaining, int) else remaining
        print_info(f"Aktif API anahtarı: {mask_key(row['key'])} (kalan günlük kota: {shown})")

def load_frame(args):
    """Resolves a five-line frame plus an optional free-text prompt.

    Flags win when present. With neither flags nor a positional prompt, a TTY
    asks the five lines and falls back to a single istek when they are blank.
    Returns None when there is nothing to send.
    """
    from src.prompt_frame import FORM_INTRO, PromptFrame, collect_prompt_frame

    frame = PromptFrame.from_namespace(args)
    prompt = (getattr(args, "prompt", None) or "").strip()
    if frame.is_empty() and not prompt and sys.stdin.isatty():
        print_info(FORM_INTRO)
        collected = collect_prompt_frame(lambda label: input(label))
        if isinstance(collected, str):
            print_warning("Slash komutları yalnızca interaktif REPL içindedir.")
            return None
        frame = collected
        if frame.is_empty():
            prompt = input("İstek > ").strip()
    if frame.is_empty() and not prompt:
        return None
    return frame, prompt


def cmd_chat(args):
    """Executes a single chat completion with optional streaming and file context."""
    from src.api_client import EvrenClient
    from src.terms import ensure_terms_accepted
    from src.project_scanner import build_context_from_files
    from src.prompt_frame import build_user_content, compose_system, delivery_summary

    if not ensure_terms_accepted(interactive=True, silent_ok=True):
        return

    loaded = load_frame(args)
    if loaded is None:
        print_error(
            "Bir istek veya en az bir kalıp alanı gerekli "
            "(--rol, --cerceve, --brief, --kisit, --cikti)."
        )
        return
    frame, prompt = loaded

    client = EvrenClient(default_model=args.model)
    _pin_best_key(client)
    if frame.is_empty():
        system = CHAT_SYSTEM
        user = prompt
    else:
        print_info(f"Kalıp API'ye ayrıldı: {delivery_summary(frame)}")
        system = compose_system(CHAT_SYSTEM, frame)
        user = build_user_content(frame, extra=prompt)

    if args.file:
        context = build_context_from_files(args.file)
        user = f"Aşağıdaki dosya bağlamı verilmiştir:\n\n{context}\n\nİstek:\n{user}"

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    from src.ui import status_spinner, StatusTracker

    if args.stream:
        stream_gen = client.chat_stream(
            messages=messages,
            model=args.model,
            max_tokens=args.max_tokens,
        )
        tracker = StatusTracker(f"Model düşünüyor ve yanıt hazırlanıyor... ({args.model})")
        tracker.__enter__()
        first_chunk = True
        in_reasoning = False
        in_content = False
        try:
            for chunk in stream_gen:
                if first_chunk:
                    tracker.stop()
                    first_chunk = False
                c_type = chunk.get("type")
                content = chunk.get("content", "")
                if c_type == "reasoning":
                    if not in_reasoning:
                        in_reasoning = True
                        if HAVE_RICH and console:
                            console.print("[bold magenta]💭 Düşünme / Akıl Yürütme:[/bold magenta]")
                        else:
                            print("[Düşünme]:")
                    print_reasoning(content, is_chunk=True)
                elif c_type == "content":
                    if in_reasoning or not in_content:
                        if in_reasoning:
                            print("\n")
                        in_content = True
                        in_reasoning = False
                    print(content, end="", flush=True)
                elif c_type == "error":
                    print_error(content)
        finally:
            if first_chunk:
                tracker.stop()
        print("\n")
    else:
        with status_spinner(f"Model düşünüyor... ({args.model})"):
            resp = client.chat_complete(
                messages=messages,
                model=args.model,
                max_tokens=args.max_tokens,
            )
        choice = resp.choices[0].message
        reasoning = getattr(choice, "reasoning", None) or getattr(choice, "reasoning_content", None)
        if reasoning:
            print_reasoning(reasoning)
        print_markdown(choice.content or "")


def cmd_agent(args):
    """Executes a one-shot autonomous multi-step agent task."""
    from src.api_client import EvrenClient
    from src.agent import AgentSession
    from src.terms import ensure_terms_accepted

    if not ensure_terms_accepted(interactive=True, silent_ok=True):
        return

    from src.prompt_frame import delivery_summary

    loaded = load_frame(args)
    if loaded is None:
        print_error(
            "Bir istek veya en az bir kalıp alanı gerekli "
            "(--rol, --cerceve, --brief, --kisit, --cikti)."
        )
        return
    frame, prompt = loaded

    workspace = get_workspace_dir(args.workspace)
    # Smart context docs must exist before the system prompt is composed.
    try:
        from src.cli_home import ensure_cli_docs
        from src.project_docs import ensure_project_docs

        docs_result = ensure_project_docs(workspace)
        ensure_cli_docs()
        print_info(f"Bağlam: {docs_result.summary()}")
        for note in docs_result.details():
            print_warning(f"Bağlam: {note}")
    except Exception as e:
        print_warning(f"Bağlam dosyaları atlandı: {e}")

    client = EvrenClient(default_model=args.model)
    _pin_best_key(client)
    session = AgentSession(client=client, workspace_root=workspace, model=args.model)

    print_info(f"Otonom ajan başlatılıyor • {session.model} • {Path(workspace).name or workspace}")
    try:
        if frame.is_empty():
            session.run_step(prompt, max_steps=args.max_steps, compact=True)
        else:
            print_info(f"Kalıp API'ye ayrıldı: {delivery_summary(frame)}")
            session.run_step(
                session.apply_frame(frame, extra=prompt), max_steps=args.max_steps, compact=True,
            )
    finally:
        # One-shot mode must not leave background command trees orphaned.
        try:
            from src.background import get_manager

            killed = get_manager().cleanup_all()
            if killed:
                print_info(f"Arka planda çalışan {killed} komut kapatılırken durduruldu.")
        except Exception:
            pass


def cmd_docs(args):
    """Inspects or refreshes the smart context documents (agent.md / changelog.md)."""
    from src.cli_home import cli_docs_dir, ensure_cli_docs
    from src.project_docs import docs_status, ensure_project_docs, unmark_docs

    workspace = get_workspace_dir(getattr(args, "workspace", None))
    action = getattr(args, "docs_action", None) or "status"

    if action == "path":
        print(f"Proje belgeleri : {workspace}")
        print(f"CLI belgeleri   : {cli_docs_dir()}")
        return

    if action == "refresh":
        result = ensure_project_docs(
            workspace, refresh=True, adopt=bool(getattr(args, "adopt", False))
        )
        ensure_cli_docs()
        print_success(result.summary())
        return

    if action == "unmark":
        if not getattr(args, "yes", False):
            from src.ui import prompt_confirm

            print_warning(
                "Yönetilen bloklar kaldırılır; elle yazdığınız metin kalır. "
                "Sonraki çalıştırmada CLI bu dosyalara dokunmaz."
            )
            if not prompt_confirm("Yönetilen bloklar kaldırılsın mı?", default=False):
                print_info("Vazgeçildi.")
                return
        result = unmark_docs(workspace)
        print_success(
            result.summary() if (result.updated or result.notes) else "Belge bulunamadı."
        )
        return

    status = docs_status(workspace)
    print_info("Akıllı bağlam dosyaları:")
    print(f"  • Çalışma alanı : {status['root']}")
    print(
        f"  • Etkin         : {'evet' if status['enabled'] else 'hayır'}"
        + (f" ({status['reason']})" if status["reason"] else "")
    )
    print(f"  • Ajan belgesi  : {status['agent_doc'] or 'yok'}")
    if status["agent_sections"]:
        print(f"  • Yönetilen blok: {', '.join(status['agent_sections'])}")
    print(
        f"  • Changelog     : {status['changelog'] or 'yok'}"
        + (f" ({status['changelog_entries']} giriş)" if status["changelog"] else "")
    )
    print(f"  • CLI belgeleri : {cli_docs_dir()}")
    print(f"  • Token bütçesi : {status['token_budget']}")


def cmd_scan(args):
    """Scans and visualizes project file structure."""
    from src.project_scanner import generate_tree, scan_workspace
    from src.ui import status_spinner

    target = Path(args.path).resolve()
    with status_spinner(f"'{target}' taranıyor ve dosya ağacı oluşturuluyor..."):
        tree_str = generate_tree(target)
        stats = scan_workspace(target)

    print(tree_str)
    print_info(f"Toplam dosya: {stats['total_files']} | Boyut: {stats['total_size_kb']} KB")
    print("Uzantı Dağılımı:")
    for ext, count in stats["extension_counts"].items():
        print(f"  • {ext:<10} : {count} dosya")


def cmd_quota(args):
    """Displays token quota for every API key in the pool."""
    from src.api_client import EvrenClient
    from src.repl import print_key_quotas
    from src.ui import status_spinner

    client = EvrenClient()
    with status_spinner("EVREN API kota bilgileri alınıyor..."):
        rows = client.get_all_quotas()
    print_key_quotas(rows)


def cmd_terms(args):
    """Checks and handles terms acceptance for every configured API key."""
    from src.api_client import EvrenClient
    from src.terms import ensure_terms_for_keys

    client = EvrenClient()
    keys = client.key_pool.key_values()
    ensure_terms_for_keys(keys, interactive=True, force=bool(getattr(args, "force", False)))


def cmd_models(args):
    """Lists available models on EVREN API."""
    from src.api_client import EvrenClient
    from src.config import RECOMMENDED_MODELS
    from src.ui import status_spinner

    client = EvrenClient()
    with status_spinner("EVREN API modelleri çekiliyor..."):
        models = client.list_models()
    print_info("EVREN API Modelleri:")
    for m in models:
        rec = RECOMMENDED_MODELS.get(m, "")
        extra = f"  ({rec})" if rec else ""
        print(f"  • {m:<24}{extra}")


def cmd_ssh(args):
    """Manages and uses registered SSH hosts (list / test / run)."""
    from src.ssh_hosts import add_host, remove_host
    from src.ssh_ops import ssh_list_hosts, ssh_run

    workspace = get_workspace_dir(args.workspace)
    action = args.ssh_action

    if action == "list":
        print(ssh_list_hosts(workspace_root=workspace))
    elif action == "add":
        target = args.target
        if "@" in target:
            user, host = target.split("@", 1)
        else:
            user, host = "", target
        add_host(
            alias=args.alias,
            host=host,
            user=user,
            port=args.port,
            workspace_root=workspace,
        )
        print_success(
            f"SSH host eklendi: {args.alias} -> {target}:{args.port} "
            "(tüm çalışma alanlarında geçerli: ~/.evren/ssh_hosts.json)"
        )
    elif action == "remove":
        if remove_host(args.alias, workspace):
            print_success(f"SSH host silindi: {args.alias}")
        else:
            print_warning(f"Host bulunamadı: {args.alias}")
    elif action == "test":
        result = ssh_run(args.alias, "echo evren-ssh-ok", workspace_root=workspace)
        print(result)
    elif action == "run":
        result = ssh_run(args.alias, args.command, workspace_root=workspace)
        print(result)


def cmd_update(args):
    """Pulls the latest version from GitHub and restarts the CLI."""
    from src.updater import perform_update, is_git_checkout, get_project_root, update_is_deferred
    from src.ui import status_spinner

    src_kind = "git checkout" if is_git_checkout(get_project_root()) else "pip paketi"
    print_info(f"Güncelleme kaynağı: {src_kind} (github.com/necmettincimen/evren-cli)")
    deferred = update_is_deferred()
    with status_spinner("En son sürüm çekiliyor ve kuruluyor..."):
        ok, msg = perform_update(restart=False)
    if ok and deferred:
        print_success(msg)
    elif ok:
        print_success("Güncelleme tamamlandı.")
        if not args.no_restart:
            print_info("CLI yeni sürümle yeniden başlatılıyor...")
            from src.updater import restart_process
            restart_process()
    else:
        print_error(f"Güncelleme başarısız: {msg}")


def cmd_rollback(args):
    """Rolls back the target file to the latest backup."""
    from src.file_ops import rollback_last_backup, resolve_workspace_path

    target = resolve_workspace_path(args.file)
    ok, msg = rollback_last_backup(target)
    if ok:
        print_success(msg)
    else:
        print_error(msg)


def _add_frame_args(parser):
    """Shared five-line frame flags. Rol/Çerçeve go to system; the rest to user."""
    parser.add_argument("--rol", help="Kimlik: modelin hangi sesle yazacağı (system)")
    parser.add_argument("--cerceve", help="İş çerçevesi; strateji yalnızca bu alana yüklenir (system)")
    parser.add_argument("--brief", help="Görev, tek cümle (user)")
    parser.add_argument("--kisit", help="Ölçek, tipografi, design system (user)")
    parser.add_argument("--cikti", help="Format ve teslim yüzeyi (user)")


class _EnableBackupAction(argparse.Action):
    """--backup bayrağı: süreç boyunca otomatik yedeklemeyi açar (varsayılan kapalı)."""

    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, True)
        from src.file_ops import set_backup_enabled

        set_backup_enabled(True)


def _add_backup_arg(parser):
    """Adds the explicit --backup flag (backups are off unless this is given)."""
    parser.add_argument(
        "--backup",
        action=_EnableBackupAction,
        nargs=0,
        help="Yazmalarda .evren/backups altına zaman damgalı yedek oluştur (varsayılan: kapalı)",
    )


def main():
    parser = argparse.ArgumentParser(
        prog="evren",
        description="EVREN CLI: AI Development & Project Assistant",
    )
    parser.add_argument(
        "--version",
        "-V",
        action="version",
        version=f"evren-cli {get_version()}",
        help="Sürüm bilgisini göster ve çık",
    )
    parser.add_argument(
        "--workspace",
        "-w",
        help="Proje çalışma dizini yolu (varsayılan: aktif dizin)",
    )
    parser.add_argument(
        "--model",
        "-m",
        default=EVREN_DEFAULT_MODEL,
        help=f"Kullanılacak EVREN modeli (varsayılan: {EVREN_DEFAULT_MODEL})",
    )

    _add_backup_arg(parser)

    subparsers = parser.add_subparsers(dest="subcommand")

    # chat command
    p_chat = subparsers.add_parser("chat", help="Tek seferlik sohbet / soru-cevap")
    p_chat.add_argument(
        "prompt",
        nargs="?",
        help="Serbest istek. Kalıp alanları da doluysa ek istek olarak eklenir",
    )
    _add_frame_args(p_chat)
    p_chat.add_argument("--stream", "-s", action="store_true", help="Yanıtı akıtarak göster")
    p_chat.add_argument("--max-tokens", type=int, default=EVREN_DEFAULT_MAX_TOKENS)
    p_chat.add_argument("--file", "-f", action="append", help="Bağlam olarak eklenecek dosya")
    p_chat.set_defaults(func=cmd_chat)

    # agent command
    p_agent = subparsers.add_parser("agent", help="Otonom çok adımlı kodlama görevi yürüt")
    p_agent.add_argument(
        "prompt",
        nargs="?",
        help="Serbest istek. Kalıp alanları da doluysa ek istek olarak eklenir",
    )
    _add_frame_args(p_agent)
    p_agent.add_argument(
        "--max-steps",
        type=int,
        default=None,
        help="Maksimum adım sayısı (varsayılan: EVREN_MAX_STEPS, 30)",
    )
    _add_backup_arg(p_agent)
    p_agent.set_defaults(func=cmd_agent)

    # scan command
    p_scan = subparsers.add_parser("scan", help="Çalışma alanı dosya ağacını tara")
    p_scan.add_argument("path", nargs="?", default=".", help="Taranacak klasör yolu")
    p_scan.set_defaults(func=cmd_scan)

    # quota command
    p_quota = subparsers.add_parser("quota", help="Günlük kalan token kotasını sorgula")
    p_quota.set_defaults(func=cmd_quota)

    # terms command
    p_terms = subparsers.add_parser("terms", help="Kullanım şartlarını kontrol et veya kabul et")
    p_terms.add_argument("--force", action="store_true", help="Şartları zorla yeniden kabul et")
    p_terms.set_defaults(func=cmd_terms)

    # models command
    p_models = subparsers.add_parser("models", help="EVREN API modellerini listele")
    p_models.set_defaults(func=cmd_models)

    # update command
    p_update = subparsers.add_parser("update", help="En son sürümü GitHub'dan çek ve yeniden başlat")
    p_update.add_argument("--no-restart", action="store_true", help="Güncelleme sonrası yeniden başlatma")
    p_update.set_defaults(func=cmd_update)

    # rollback command
    p_rollback = subparsers.add_parser("rollback", help="Değiştirilen bir dosyayı yedeğinden geri yükle")
    p_rollback.add_argument("file", help="Geri yüklenecek dosya yolu")
    p_rollback.set_defaults(func=cmd_rollback)

    # docs command (smart context documents)
    p_docs = subparsers.add_parser(
        "docs", help="agent.md / changelog.md bağlam dosyalarını yönet"
    )
    p_docs.add_argument(
        "docs_action",
        nargs="?",
        default="status",
        choices=["status", "refresh", "unmark", "path"],
        help="İşlem (varsayılan: status)",
    )
    p_docs.add_argument(
        "--workspace", "-w", default=None, help="Hedef proje dizini"
    )
    p_docs.add_argument(
        "--adopt",
        action="store_true",
        help="Yönetilen blokları olmayan bir agent.md dosyasına ekle",
    )
    p_docs.add_argument(
        "--yes",
        action="store_true",
        help="Onay sormadan uygula (yalnızca unmark için)",
    )
    p_docs.set_defaults(func=cmd_docs)

    # ssh command
    p_ssh = subparsers.add_parser("ssh", help="Kayıtlı SSH host'larını yönet ve kullan")
    ssh_sub = p_ssh.add_subparsers(dest="ssh_action")

    ssh_sub.add_parser("list", help="Kayıtlı SSH host'larını listele")

    p_ssh_add = ssh_sub.add_parser("add", help="Yeni SSH host'u tanımla")
    p_ssh_add.add_argument("alias", help="Host alias'ı (örn: prod-web)")
    p_ssh_add.add_argument("target", help="user@host veya host")
    p_ssh_add.add_argument("--port", type=int, default=22, help="SSH portu (varsayılan: 22)")

    p_ssh_rm = ssh_sub.add_parser("remove", help="Kayıtlı SSH host'unu sil")
    p_ssh_rm.add_argument("alias", help="Silinecek host alias'ı")

    p_ssh_test = ssh_sub.add_parser("test", help="SSH bağlantısını test et")
    p_ssh_test.add_argument("alias", help="Test edilecek host alias'ı")

    p_ssh_run = ssh_sub.add_parser("run", help="Uzak sunucuda komut çalıştır")
    p_ssh_run.add_argument("alias", help="Host alias'ı")
    p_ssh_run.add_argument("command", help="Çalıştırılacak uzak komut")

    p_ssh.set_defaults(func=cmd_ssh, ssh_action="list")

    args = parser.parse_args()

    # If no subcommand provided, start interactive REPL (TUI mode)
    if not args.subcommand:
        try:
            from src.repl import start_repl
            start_repl(workspace_path=args.workspace, initial_model=args.model)
        except ImportError as e:
            print_error(f"Gerekli paketler eksik ({e}). Lütfen 'pip install -r requirements.txt' çalıştırın.")
    else:
        try:
            args.func(args)
        except ImportError as e:
            print_error(f"Gerekli paketler eksik ({e}). Lütfen 'pip install -r requirements.txt' çalıştırın.")


if __name__ == "__main__":
    main()
