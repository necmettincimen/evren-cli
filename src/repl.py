"""
Interactive REPL (Read-Eval-Print Loop) for EVREN CLI.
Provides the Antigravity TUI terminal experience, slash commands,
context management, model switching, and real-time agent interaction.
"""

import sys
from pathlib import Path

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
    HAVE_PT = True
except ImportError:
    HAVE_PT = False

from src.config import (
    get_workspace_dir,
    get_evren_dir,
    RECOMMENDED_MODELS,
    execution_context_block,
)
from src.errors import EvrenApiError
from src.api_client import EvrenClient
from src.agent import AgentSession
from src.project_scanner import scan_workspace, generate_tree
from src.file_ops import rollback_last_backup, resolve_workspace_path
from src.terms import ensure_terms_accepted, ensure_terms_for_keys, get_terms_status
from src.prompt_frame import (
    FORM_INTRO,
    collect_guided_prompt_frame,
    request_frame_guidance,
    delivery_summary,
)
from src.ui import (
    print_banner,
    print_info,
    print_success,
    print_warning,
    print_error,
    status_spinner,
    console,
    HAVE_RICH,
    form_palette,
    prompt_confirm,
)


def _docs_context(workspace_root) -> str:
    """Short project brief appended to the prompt-preparation call (may be empty)."""
    try:
        from src.project_docs import docs_digest

        digest = docs_digest(workspace_root)
    except Exception:
        return ""
    if not digest:
        return ""
    return (
        "\n\nPROJE ÖZETİ (agent.md/changelog.md'den; bayat olabilir, uygulamadan önce "
        "doğrula. Netleştirme sorularını ve alan önerilerini bu projeye göre üret;\n"
        "bu özetten okunabilen teknik ayrıntılar için kullanıcıya soru sorma):\n"
        + digest
    )


def _bootstrap_docs(workspace_root) -> str:
    """Creates/refreshes the smart context docs at startup. Returns a one-line summary.

    Never raises: a docs failure must not stop the CLI from starting.
    """
    try:
        from src.cli_home import cli_docs_dir, ensure_cli_docs
        from src.project_docs import ensure_project_docs

        result = ensure_project_docs(workspace_root)
        ensure_cli_docs()
        if result.disabled:
            return result.summary()
        return f"{result.summary()} | CLI bağlamı: {cli_docs_dir()}"
    except Exception as e:
        return f"bağlam dosyaları atlandı: {e}"


def _read_line(pt_session, label: str) -> str:
    color = ""
    if "\n" in label:
        text, label = label.rsplit("\n", 1)
        if HAVE_RICH and console:
            from rich.panel import Panel
            from rich.text import Text

            palette = form_palette()
            if label == "Karar > ":
                title, color = "Netleştirme kararı", palette["decision"]
            elif label == "Yanıt > ":
                title, color = "Netleştirme", palette["question"]
            else:
                title, color = "Alan önerisi", palette["suggestion"]
            console.print(Panel(
                Text(text, overflow="fold"), title=title,
                border_style=color, padding=(1, 2),
            ))
        else:
            print(text)
    if pt_session:
        prompt = [(f"bold fg:{color}", label)] if color else label
        return pt_session.prompt(prompt).strip()
    if color:
        from rich.text import Text

        console.print(Text(label, style=f"bold {color}"), end="")
        return input().strip()
    return input(label).strip()


def _take_interrupt_followup(session: AgentSession, read_line) -> tuple[str, str] | None:
    """Reads a correction after Ctrl+C without starting a new brief.

    Returns None when the session is not waiting on an interrupt.
    ("continue", "") resumes the saved task. ("followup", text) adds a
    correction. ("command", text) is a slash command and keeps the pause.
    """
    if not session.interrupted or not session.task_goal:
        return None
    print_info(
        "Geçmiş duruyor. Düzeltme yazıp Enter'a basın; boş Enter kaldığı yerden devam eder."
    )
    text = (read_line("Düzeltme > ") or "").strip()
    if text.startswith("/"):
        return ("command", text)
    session.interrupted = False
    if text:
        return ("followup", text)
    return ("continue", "")


def _collect_frame(session: AgentSession, pt_session, previous=None):
    def guidance(frame, clarify):
        label = "Brief netleştiriliyor..." if clarify else "Boş alanlar için öneriler hazırlanıyor..."
        with status_spinner(label):
            return request_frame_guidance(
                session.client, session.model, frame, clarify,
                execution_context_block(session.workspace_root) + _docs_context(session.workspace_root),
            )

    return collect_guided_prompt_frame(
        lambda label: _read_line(pt_session, label), guidance, previous,
    )


def _run_agent(session: AgentSession, prompt: str | None = None, *, followup: bool = False) -> None:
    """Run or continue, then offer opt-in persistence outside auto-approval."""
    from src.workflow_state import clear_checkpoint

    if followup:
        if prompt is None:
            raise ValueError("Takip yanıtı boş olamaz.")
        session.execute_followup(prompt)
        if not session.task_goal:
            return
    elif prompt is None:
        session.continue_task()
    else:
        clear_checkpoint(session.workspace_root)
        session.run_step(prompt)

    if session.interrupted:
        return
    if session.pending_mode is None:
        clear_checkpoint(session.workspace_root)
    elif prompt_confirm(
        "Duraklayan görevi diske kaydedelim mi? Yaygın sırlar maskelenir fakat tam "
        "gizlilik garantisi yoktur; hassas görevde reddedin.", default=False,
    ):
        session.save_checkpoint()
        print_success("Checkpoint kaydedildi. Sonraki oturumda /continue ile onaylı yüklenebilir.")


def _handle_workflow_command(cmd: str, arg: str, session: AgentSession, read_line) -> bool:
    """Interactive workflow actions. Mutations always require explicit confirmation."""
    if cmd not in ("/checkpoint", "/blockers", "/check-in", "/decisions"):
        return False
    from src.workflow_state import (
        BLOCKER_ROUTES, clear_checkpoint, load_checkpoint,
        add_decision, load_decisions, review_tasks,
    )
    from src.tracking import list_tasks, set_blocker, resolve_blocker, update_task, STATUSES

    root = session.workspace_root
    try:
        if cmd == "/checkpoint":
            if arg == "save":
                if prompt_confirm("Görev geçmişini maskeleyerek diske kaydet? Hassas içerikte reddedin.", default=False):
                    session.save_checkpoint()
                    print_success("Checkpoint kaydedildi.")
            elif arg == "clear":
                if prompt_confirm("Kalıcı checkpoint silinsin mi?", default=False):
                    clear_checkpoint(root)
                    print_success("Kalıcı checkpoint silindi.")
            elif not arg:
                state = load_checkpoint(root)
                if state is None:
                    print_info("Kalıcı checkpoint yok.")
                else:
                    print_info(f"Checkpoint: {state['saved_at']} | {state['model']} | {state['mode']}")
                print_info("Komutlar: /checkpoint save | /checkpoint clear | /continue")
            else:
                raise ValueError("Kullanım: /checkpoint [save|clear]")
        elif cmd == "/decisions":
            if arg == "add":
                decision = read_line("Karar > ")
                reason = read_line("Gerekçe > ")
                risk = read_line("Risk ve sınırlar > ")
                if not all(value.strip() for value in (decision, reason, risk)):
                    raise ValueError("Karar, gerekçe ve risk boş olamaz.")
                if prompt_confirm("Bu karar günlüğe kaydedilsin ve ajan bağlamına eklensin mi?", default=False):
                    item = add_decision(root, decision, reason, risk)
                    session.refresh_context()
                    print_success(f"Karar kaydedildi: {item['id']}")
            elif not arg:
                items = load_decisions(root)
                print_info(f"Onaylı kararlar ({len(items)}):")
                for item in items:
                    print(f"  {item['id']}: {item['decision']}\n    Gerekçe: {item['reason']}\n    Risk: {item['risk']}")
                print_info("Yeni kayıt: /decisions add")
            else:
                raise ValueError("Kullanım: /decisions [add]")
        elif cmd == "/blockers":
            parts = arg.split(maxsplit=3)
            if parts and parts[0] == "add":
                if len(parts) != 4:
                    raise ValueError("Kullanım: /blockers add <görev-id> <kategori> <kanıt>")
                if prompt_confirm("Bu engeli görev kaydına ekle?", default=False):
                    set_blocker(parts[1], parts[2], parts[3], root)
                    print_success("Engel kaydedildi.")
            elif parts and parts[0] == "resolve":
                pieces = arg.split(maxsplit=2)
                if len(pieces) != 3:
                    raise ValueError("Kullanım: /blockers resolve <görev-id> <çözüm notu>")
                if prompt_confirm("Engeli çözüldü olarak işaretle?", default=False):
                    resolve_blocker(pieces[1], pieces[2], root)
                    print_success("Engel çözüldü; görev devam ediyor.")
            else:
                if arg and arg not in BLOCKER_ROUTES:
                    raise ValueError(f"Kategoriler: {', '.join(BLOCKER_ROUTES)}")
                items = review_tasks(list_tasks("all", root))
                for item in items:
                    blocker = item.get("blocker", {}) if item.get("status") == "blocked" else {}
                    if not ({"blocked", "stalled"} & set(item["flags"])):
                        continue
                    if arg and blocker.get("category") != arg:
                        continue
                    print_info(f"{item['id']} [{', '.join(item['flags'])}]")
                    print(f"  Kanıt: {blocker.get('evidence', 'Kayıtlı kanıt yok; neden varsayılmadı.')}")
                    print(f"  Öneri: {blocker.get('route', 'Görev durumunu gözden geçir; /check-in kullan.')}")
                print_info(f"Kategoriler: {', '.join(BLOCKER_ROUTES)}")
                print_info("/blockers add <id> <kategori> <kanıt> | /blockers resolve <id> <not>")
        else:
            if arg not in ("", "update"):
                raise ValueError("Kullanım: /check-in [update]")
            items = review_tasks(list_tasks("all", root))
            print_info(f"Açık görev gözden geçirmesi ({len(items)}):")
            for item in items:
                print(f"  {item['id']} [{item['status']}] %{item['progress']} "
                      f"| {', '.join(item['flags']) or 'open'} | {item['description']}")
                if arg != "update":
                    continue
                status = read_line(f"{item['id']} durum ({', '.join(STATUSES)}; Enter=koru) > ").strip()
                raw_progress = read_line("İlerleme 0-100 (Enter=koru) > ").strip()
                note = read_line("Gözden geçirme notu (Enter=atla) > ").strip()
                if status and status not in STATUSES:
                    raise ValueError("Geçersiz görev durumu.")
                if raw_progress and (not raw_progress.isdigit() or not 0 <= int(raw_progress) <= 100):
                    raise ValueError("İlerleme 0-100 arasında tam sayı olmalıdır.")
                if raw_progress and (
                    (status == "completed" and int(raw_progress) != 100)
                    or (status and status != "completed" and int(raw_progress) == 100)
                ):
                    raise ValueError("Durum ve ilerleme çelişiyor: completed yalnızca %100 olmalıdır.")
                if status or raw_progress or note:
                    if prompt_confirm(f"{item['id']} güncellensin mi?", default=False):
                        from src.workflow_state import redact_text

                        update_task(item["id"], status=status or None,
                                    progress=int(raw_progress) if raw_progress else None,
                                    note=redact_text(note), workspace_root=root)
                        print_success(f"Görev güncellendi: {item['id']}")
            print_info("Onaylı güncelleme: /check-in update")
    except (OSError, ValueError) as e:
        print_error(f"İş akışı işlemi başarısız: {e}")
    return True


def _fmt_quota_num(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def print_key_quotas(rows: list[dict]) -> None:
    """Pretty-prints per-key quota rows from EvrenClient.get_all_quotas()."""
    print_info(f"API Kota ({len(rows)} anahtar):")
    for i, row in enumerate(rows, 1):
        marker = " ← aktif" if row.get("active") else ""
        print(f"  {i}. {row.get('masked', '?'):<24}{marker}")
        if row.get("error"):
            print(f"     hata: {row['error']}")
            continue
        rem = _fmt_quota_num(row.get("remaining", row.get("remaining_daily_tokens")))
        limit = _fmt_quota_num(row.get("limit"))
        used = _fmt_quota_num(row.get("used"))
        reset = row.get("reset") or "—"
        level = row.get("level") or "—"
        parts = [
            f"kalan={rem}",
            f"limit={limit}",
            f"kullanılan={used}",
            f"reset={reset}",
            f"seviye={level}",
        ]
        if row.get("window_minutes") is not None:
            parts.append(f"pencere={row['window_minutes']}dk")
        if row.get("remaining_cr") is not None:
            parts.append(f"kredi={_fmt_quota_num(row['remaining_cr'])}")
        print("     " + "  ".join(parts))


HELP_TEXT = """
[bold cyan]EVREN CLI - Kullanılabilir Komutlar:[/bold cyan]

[bold]Slash Komutları:[/bold]
  [green]/help[/green]                 Bu yardım menüsünü görüntüler
  [green]/version[/green]              Sürüm bilgisini ve sürüm notlarını gösterir
  [green]/model <isim>[/green]         Aktif modeli değiştirir veya mevcut modelleri listeler
  [green]/mode <kip>[/green]           Çalışma kipini değiştirir (normal | ask | plan)
  [green]/effort <seviye>[/green]      Düşünme yoğunluğunu ayarlar (low | medium | high | none)
  [green]/models[/green]               EVREN API'deki tüm modelleri çeker ve listeler
  [green]/quota[/green]                Tüm API anahtarlarının kalan/limit/reset kotasını gösterir
  [green]/tokens[/green]               Bağlam/token bütçesi raporunu gösterir
  [green]/docs [alt komut][/green]      agent.md/changelog.md durumu; refresh|unmark|path
  [green]/keys[/green]                 API anahtar havuzunu listeler (maskeli) ve yönetir
  [green]/keys add/remove[/green]      Anahtar ekler/çıkarır (~/.evren-cli/config.json)
  [green]/keys next[/green]            Aktif anahtarı sabitleyerek sıradakine geçer
  [green]/terms[/green]                Tüm anahtarlar için şartları denetler / kabul eder (/terms force)
  [green]/skills [kategori][/green]    Kullanılabilir mühendislik becerilerini listeler
  [green]/apply <beceri>[/green]       Bir beceriyi mevcut göreve uygular (örn: /apply root-cause-debug)
  [green]/diagnose [ad][/green]        Arıza triyajı çalıştırır (örn: /diagnose build-failure)
  [green]/template [ad][/green]        Çıktı şablonunu doldurur (örn: /template adr)
  [green]/memory[/green]               Proje hafızasını (profil + notlar) gösterir
  [green]/memory set <k> <v>[/green]   Profil alanı kaydeder (örn: /memory set stack FastAPI)
  [green]/memory note <metin>[/green]  Hafızaya not ekler
  [green]/track [filtre][/green]       Görevleri listeler (all|open|<durum>)
  [green]/task add <id> <açıklama>[/green]  Yeni takip görevi ekler
  [green]/progress <id> <yüzde>[/green]     Görev ilerlemesini günceller
  [green]/playbook [ad][/green]        Playbook'ları listeler veya başlatır
  [green]/resume[/green]               Kaydedilmiş playbook adımından devam eder
  [green]/language [ad][/green]        Çıktı dilini ayarlar (örn: /language tr)
  [green]/scan [klasör][/green]        Çalışma alanını tarar, dosya ağacını ve istatistikleri çıkarır
  [green]/files[/green]                İstem bağlamına eklenmiş aktif dosyaları listeler
  [green]/add <dosya>[/green]          Bir dosyayı doğrudan istem bağlamına ekler
  [green]/drop <dosya>[/green]         Bir dosyayı istem bağlamından çıkarır
  [green]/undo <dosya>[/green]         Belirtilen dosyayı .evren/backups yedeğinden geri yükler
  [green]/auto [on/off][/green]        Otomatik dosya onay modunu açar veya kapatır
  [green]/run <komut>[/green]          PowerShell/terminal komutunu doğrudan çalıştırır
  [green]/ssh[/green]                  Kayıtlı SSH host'larını listeler
  [green]/ssh add <alias> <user@host> [port][/green]  Yeni SSH host'u tanımlar
  [green]/ssh remove <alias>[/green]   Kayıtlı SSH host'unu siler
  [green]/ssh test <alias>[/green]     SSH bağlantısını test eder
  [green]/update[/green]               En son sürümü GitHub'dan çeker ve CLI'ı yeniden başlatır
  [green]/clear[/green]                Mevcut sohbet ve bağlam geçmişini sıfırlar
  [green]/continue[/green]             Adım sınırında duraklayan görevi geçmişle sürdürür (Brief: devam)
  [green]/execute <yanıt>[/green]       Formu atlayarak mevcut göreve onay/takip yanıtı gönderir
  [green]/checkpoint [save|clear][/green]  Onaylı kalıcı görev kaydı
  [green]/blockers[/green]             Engelleri ve çözüm yönlendirmelerini gösterir
  [green]/check-in [update][/green]     Açık/gecikmiş/duraklamış görevleri gözden geçirir
  [green]/decisions [add][/green]       Onaylı mimari karar günlüğü
  [green]/exit[/green] veya [green]/quit[/green]       Uygulamadan çıkar

[bold]Beş satırlık kalıp (her tur):[/bold]
  Rol ve Çerçeve system mesajına, Brief / Kısıt / Çıktı user mesajına gider.
  Önce Brief, sonra netleştirme, ardından boş alan önerileri.
  Yeni Brief önceki alanları temizler; Brief'te Enter önceki kalıbı korur.
  Öneride Enter=kabul, metin=düzenle, -=boş bırak. Tümü boşsa [green]İstek >[/green] sorulur.
  Slash komutunu ilk satıra (Brief) yaz. Örnek kalıp:
    01 Brief    timeout süresini 45 saniyeye çıkar
    02 Rol      kıdemli Windows mühendisi
    03 Çerçeve  cerrahi yama, mevcut stili koru
    04 Kısıt    yalnız src/api_client.py
    05 Çıktı    çalışan kod ve pytest

[bold]Serbest istek:[/bold]
  Kalıbı boş geçince yazdığın metin eskisi gibi tek prompt olarak gider.
"""


def _handle_docs_command(arg: str, session, workspace_root) -> None:
    """`/docs [status|refresh|unmark|path]` — smart context document control."""
    from src.project_docs import docs_status, ensure_project_docs, unmark_docs

    sub = (arg or "").strip().lower()
    if sub in ("", "status"):
        status = docs_status(workspace_root)
        print_info("Akıllı bağlam dosyaları:")
        print(f"  • Çalışma alanı   : {status['root']}")
        print(f"  • Etkin           : {'evet' if status['enabled'] else 'hayır'}"
              + (f" ({status['reason']})" if status["reason"] else ""))
        print(f"  • Ajan belgesi    : {status['agent_doc'] or 'yok'}")
        if status["agent_sections"]:
            print(f"  • Yönetilen blok  : {', '.join(status['agent_sections'])}")
        print(f"  • Changelog       : {status['changelog'] or 'yok'}"
              + (f" ({status['changelog_entries']} giriş)" if status["changelog"] else ""))
        print(f"  • Durum dosyası   : {status['state'] or 'yok'}")
        print(f"  • Token bütçesi   : {status['token_budget']}")
        print("\nKomutlar: /docs refresh [--adopt] | /docs unmark | /docs path")
        return

    if sub in ("path", "dir"):
        from src.cli_home import cli_docs_dir

        print(f"Proje belgeleri : {workspace_root}")
        print(f"CLI belgeleri   : {cli_docs_dir()}")
        return

    if sub.startswith("refresh"):
        adopt = "--adopt" in sub
        if not prompt_confirm(
            "agent.md yönetilen blokları ve changelog başlığı yenilensin mi?",
            default=True,
        ):
            print_info("Vazgeçildi.")
            return
        result = ensure_project_docs(workspace_root, refresh=True, adopt=adopt)
        session.refresh_context()
        print_success(result.summary())
        return

    if sub == "unmark":
        print_warning(
            "Yönetilen bloklar kaldırılır; elle yazdığınız metin kalır. "
            "Sonraki çalıştırmada CLI bu dosyalara dokunmaz."
        )
        if not prompt_confirm("Yönetilen bloklar kaldırılsın mı?", default=False):
            print_info("Vazgeçildi.")
            return
        result = unmark_docs(workspace_root)
        session.refresh_context()
        print_success(result.summary() if (result.updated or result.notes) else "Belge bulunamadı.")
        return

    print_warning("Kullanım: /docs [status|refresh [--adopt]|unmark|path]")


def _handle_keys_command(arg: str, client: EvrenClient):
    """Handles the /keys slash command: list, add, remove, reset the key pool."""
    from src.api_key_pool import KeyState

    pool = client.key_pool
    parts = arg.split(maxsplit=1)
    sub = parts[0].lower() if parts else ""
    sub_arg = parts[1].strip() if len(parts) > 1 else ""

    if sub == "add":
        if not sub_arg:
            print_warning("Kullanım: /keys add <api_anahtarı>")
            return
        if pool.add_key(sub_arg):
            from src.config import save_api_keys

            path = save_api_keys(pool.key_values())
            print_success(f"Yeni API anahtarı eklendi ve kaydedildi: {path}")
        else:
            print_warning("Anahtar zaten havuzda veya geçersiz.")
        return

    if sub == "remove":
        if not sub_arg:
            print_warning("Kullanım: /keys remove <api_anahtarı>")
            return
        if pool.remove_key(sub_arg):
            from src.config import save_api_keys

            path = save_api_keys(pool.key_values())
            if pool.current_key:
                client.api_key = pool.current_key
                client.openai_client = pool.get_client(pool.current_key)
            print_success(f"API anahtarı çıkarıldı ve config güncellendi: {path}")
        else:
            print_warning("Anahtar havuzda bulunamadı.")
        return

    if sub == "reset":
        pool.reset_states()
        print_success("Tüm anahtar durumları sıfırlandı (READY).")
        return

    if sub in ("next", "switch"):
        if len(pool) < 2:
            print_warning("Havuzda geçilecek başka anahtar yok.")
            return
        pooled = pool.rotate()
        if pooled is None:
            print_warning("Şu an kullanılabilir anahtar yok (hepsi beklemede/devre dışı).")
            return
        # Keep backward-compat client fields in sync for /models, /quota, etc.
        client.api_key = pooled.key
        client.openai_client = pool.get_client(pooled.key)
        print_success(f"Sıradaki anahtara geçildi: {pooled.masked}")
        return

    # Default: list the pool (masked).
    if pool.is_empty():
        print_info("Anahtar havuzu boş. Eklemek için: /keys add <anahtar>")
        return

    state_labels = {
        KeyState.READY: "hazır",
        KeyState.COOLING: "beklemede",
        KeyState.DISABLED: "devre dışı",
    }
    print_info(f"API Anahtar Havuzu ({len(pool)} anahtar):")
    current = pool.current_key
    for i, pk in enumerate(pool.keys, 1):
        label = state_labels.get(pk.state, pk.state.value)
        extra = ""
        if pk.state == KeyState.COOLING:
            wait = pk.next_available_in()
            extra = f" (~{wait:.0f} sn)"
        elif pk.state == KeyState.DISABLED and pk.last_error:
            extra = f" ({pk.last_error})"
        marker = " ← aktif" if pk.key == current else ""
        print(f"  {i}. {pk.masked:<24} [{label}]{extra}{marker}")
    print("\nKomutlar: /keys add <anahtar> | /keys remove <anahtar> | /keys next | /keys reset")


def _handle_ssh_command(arg: str, workspace_root: Path):
    """Handles the /ssh slash command: list, add, remove, test registered hosts."""
    from src.ssh_hosts import load_hosts, add_host, remove_host
    from src.ssh_ops import ssh_list_hosts, ssh_run

    parts = arg.split()
    sub = parts[0].lower() if parts else ""

    if sub == "add":
        # /ssh add <alias> <user@host> [port]
        if len(parts) < 3:
            print_warning("Kullanım: /ssh add <alias> <user@host> [port]")
            return
        alias = parts[1]
        target = parts[2]
        port = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 22
        if "@" in target:
            user, host = target.split("@", 1)
        else:
            user, host = "", target
        add_host(alias=alias, host=host, user=user, port=port, workspace_root=workspace_root)
        print_success(f"SSH host eklendi: {alias} -> {target}:{port} (varsayılan: salt-okuma)")
        return

    if sub == "remove":
        if len(parts) < 2:
            print_warning("Kullanım: /ssh remove <alias>")
            return
        if remove_host(parts[1], workspace_root):
            print_success(f"SSH host silindi: {parts[1]}")
        else:
            print_warning(f"Host bulunamadı: {parts[1]}")
        return

    if sub == "test":
        if len(parts) < 2:
            print_warning("Kullanım: /ssh test <alias>")
            return
        with status_spinner(f"'{parts[1]}' bağlantısı test ediliyor..."):
            result = ssh_run(parts[1], "echo evren-ssh-ok", workspace_root=workspace_root)
        print(result)
        return

    # Default: list hosts
    print(ssh_list_hosts(workspace_root=workspace_root))
    print("\nKomutlar: /ssh add <alias> <user@host> [port] | /ssh remove <alias> | /ssh test <alias>")


def show_help():
    if HAVE_RICH:
        console.print(HELP_TEXT)
    else:
        print("""
EVREN CLI Komutları:
  /help         - Yardım menüsü
  /version      - Sürüm bilgisi ve sürüm notları
  /model [ad]   - Model değiştir
  /mode <kip>   - Çalışma kipi (normal|ask|plan)
  /effort <s>   - Düşünme yoğunluğu (low|medium|high|none)
  /models       - Modelleri listele
  /quota        - Kalan token kotası
  /tokens       - Bağlam/token bütçesi raporu
  /keys         - API anahtar havuzunu yönet
  /keys next    - Sıradaki API anahtarına geç
  /terms        - Şartları kontrol et / kabul et
  /skills       - Mühendislik becerilerini listele
  /docs         - agent.md / changelog.md bağlam dosyaları
  /apply <b>    - Bir beceriyi uygula
  /diagnose     - Arıza triyajı çalıştır
  /template     - Çıktı şablonunu doldur
  /memory       - Proje hafızasını göster/yönet
  /track        - Görevleri listele
  /task add     - Görev ekle
  /progress     - Görev ilerlemesini güncelle
  /playbook     - Playbook listele/başlat
  /resume       - Playbook adımından devam et
  /language     - Çıktı dilini ayarla
  /scan         - Proje dosya ağacını tara
  /files        - Aktif bağlam dosyaları
  /add <dosya>  - Dosya bağlamı ekle
  /drop <dosya> - Dosya bağlamı çıkar
  /undo <dosya> - Yedeği geri yükle (rollback)
  /run <komut>  - Terminal komutu çalıştır
  /ssh          - SSH host'larını listele/yönet
  /update       - En son sürümü çek ve yeniden başlat
  /clear        - Geçmişi temizle
  /exit         - Çıkış
""")


def start_repl(workspace_path: str | None = None, initial_model: str | None = None):
    """Starts the interactive Antigravity CLI REPL."""
    workspace_root = get_workspace_dir(workspace_path)
    evren_dir = get_evren_dir(workspace_root)
    evren_dir.mkdir(parents=True, exist_ok=True)

    # Smart context docs: create/refresh agent.md + changelog.md (project) and the
    # CLI's own skills/lessons/commands files before the agent prompt is composed.
    docs_result = _bootstrap_docs(workspace_root)

    client = EvrenClient(default_model=initial_model)

    # Initial check: terms status & quota (active key summary for the banner)
    with status_spinner("EVREN API bağlantısı ve kota doğrulanıyor..."):
        quota_data = client.get_quota()
    remaining = quota_data.get("remaining", quota_data.get("remaining_daily_tokens", "Bilinmiyor"))
    limit = quota_data.get("limit")
    if isinstance(remaining, int) and isinstance(limit, int):
        quota_str = f"{remaining:,} / {limit:,}"
    elif isinstance(remaining, int):
        quota_str = f"{remaining:,}"
    else:
        quota_str = str(remaining)

    # Ensure terms accepted for the active key (quiet when already OK).
    try:
        ensure_terms_accepted(
            interactive=True,
            api_key=client.api_key,
            silent_ok=True,
        )
    except Exception as e:
        print_warning(f"Kullanım şartları uyarısı: {e}")

    session = AgentSession(client=client, workspace_root=workspace_root, model=initial_model)

    print_banner(
        model=session.model,
        workspace_path=str(workspace_root),
        quota_info=quota_str,
    )
    print_info(FORM_INTRO)
    print_info(f"Bağlam dosyaları: {docs_result}")
    from src.workflow_state import checkpoint_path
    if checkpoint_path(workspace_root).exists():
        print_info("Kalıcı checkpoint bulundu. İncelemek için /checkpoint, onaylı yükleme için /continue.")

    # History setup
    history_file = evren_dir / "history.txt"
    pt_session = None
    if HAVE_PT:
        pt_session = PromptSession(
            history=FileHistory(str(history_file)),
            auto_suggest=AutoSuggestFromHistory(),
        )

    last_frame = None
    while True:
        try:
            mode_tag = "" if session.mode.value == "normal" else f" ({session.mode.value})"
            print(f"evren [{session.model}]{mode_tag}")

            paused = _take_interrupt_followup(session, lambda label: _read_line(pt_session, label))
            if paused is not None:
                kind, text = paused
                if kind == "followup":
                    _run_agent(session, text, followup=True)
                    continue
                if kind == "continue":
                    _run_agent(session)
                    continue
                user_input = text
            else:
                user_input = None

            if user_input is None:
                try:
                    collected = _collect_frame(session, pt_session, last_frame)
                except (EvrenApiError, ValueError) as e:
                    print_error(f"Prompt hazırlama başarısız; görev başlatılmadı: {e}")
                    continue
                if isinstance(collected, str):
                    user_input = collected
                elif not collected.is_empty():
                    last_frame = collected
                    print_info(f"Kalıp API'ye ayrıldı: {delivery_summary(collected)}")
                    from src.workflow_state import clear_checkpoint
                    clear_checkpoint(workspace_root)
                    _run_agent(session, session.apply_frame(collected, replace_identity=True))
                    continue
                else:
                    user_input = _read_line(pt_session, "İstek > ")
                    if not user_input:
                        continue

            # Check slash commands
            if user_input.strip().casefold() in ("devam", "/continue"):
                if session.pending_mode is None:
                    from src.workflow_state import checkpoint_path
                    if checkpoint_path(workspace_root).exists():
                        if not prompt_confirm(
                            "Kalıcı checkpoint yüklensin mi? Maskelenmiş bilgiler yeniden sağlanmalıdır.",
                            default=False,
                        ):
                            continue
                        session.restore_checkpoint()
                _run_agent(session)
                continue
            if user_input.startswith("/"):
                parts = user_input.split(maxsplit=1)
                cmd = parts[0].lower()
                arg = parts[1].strip() if len(parts) > 1 else ""
                if cmd == "/execute":
                    if not arg:
                        print_warning("Kullanım: /execute <onay veya takip yanıtı>")
                    else:
                        _run_agent(session, arg, followup=True)
                    continue
                if _handle_workflow_command(cmd, arg, session, lambda label: _read_line(pt_session, label)):
                    continue

                if cmd in ("/exit", "/quit", "/q"):
                    print_info("EVREN CLI sonlandırılıyor. İyi çalışmalar!")
                    break

                elif cmd in ("/help", "/h", "/?"):
                    show_help()

                elif cmd in ("/version", "/ver", "/v"):
                    from src.version import get_version, get_changelog_notes
                    ver = get_version()
                    print_info(f"EVREN CLI Sürümü: v{ver}")
                    notes = get_changelog_notes(ver, max_items=20)
                    if notes:
                        print(f"\nv{ver} Sürüm Notları:")
                        for note in notes:
                            print(f"  • {note}")
                        print()

                elif cmd == "/model":
                    if not arg:
                        print_info(f"Aktif Model: {session.model}")
                        print("\nÖnerilen Modeller:")
                        for m_id, desc in RECOMMENDED_MODELS.items():
                            marker = " ✔" if m_id == session.model else ""
                            print(f"  • {m_id:<22} : {desc}{marker}")
                        print("\nModel değiştirmek için: /model <model_adı>")
                    else:
                        session.set_model(arg)
                        print_success(f"Model değiştirildi: {arg}")

                elif cmd == "/mode":
                    from src.modes import AgentMode, mode_label
                    if not arg:
                        print_info(f"Aktif Kip: {mode_label(session.mode)}")
                        print("  • normal : Tüm araçlar açık")
                        print("  • ask    : Salt-okuma (yazma kapalı)")
                        print("  • plan   : Yalnızca plan yazımı")
                        print("\nDeğiştirmek için: /mode <normal|ask|plan>")
                    elif arg.lower() in ("normal", "ask", "plan"):
                        session.set_mode(AgentMode(arg.lower()))
                        print_success(f"Çalışma kipi değiştirildi: {mode_label(session.mode)}")
                    else:
                        print_warning("Geçersiz kip. Kullanım: /mode <normal|ask|plan>")

                elif cmd == "/effort":
                    valid = ("low", "medium", "high", "none")
                    if not arg:
                        current = client.reasoning_effort or "none (sunucu varsayılanı)"
                        print_info(f"Aktif Düşünme Yoğunluğu: {current}")
                        print("  • low    : En hızlı, koda en çok token (önerilen)")
                        print("  • medium : Dengeli")
                        print("  • high   : Derin analiz (yavaş)")
                        print("  • none   : Sunucu varsayılanı")
                        print("\nDeğiştirmek için: /effort <low|medium|high|none>")
                    elif arg.lower() in valid:
                        client.reasoning_effort = None if arg.lower() == "none" else arg.lower()
                        shown = client.reasoning_effort or "none (sunucu varsayılanı)"
                        print_success(f"Düşünme yoğunluğu ayarlandı: {shown}")
                    else:
                        print_warning("Geçersiz değer. Kullanım: /effort <low|medium|high|none>")

                elif cmd == "/models":
                    with status_spinner("EVREN API modelleri çekiliyor..."):
                        models = client.list_models()
                    print("\nKullanılabilir Modeller:")
                    for m in models:
                        desc = RECOMMENDED_MODELS.get(m, "")
                        extra = f" ({desc})" if desc else ""
                        print(f"  • {m}{extra}")
                    print()

                elif cmd in ("/quota", "/status"):
                    with status_spinner("Kota ve şartlar sorgulanıyor..."):
                        rows = client.get_all_quotas()
                        t_status = get_terms_status()
                    print_key_quotas(rows)
                    print_info(
                        f"Şartlar Durumu: v{t_status.get('current_version')} "
                        f"(Kabul: {t_status.get('accepted')})"
                    )

                elif cmd == "/terms":
                    # Do NOT wrap the interactive accept flow in a spinner —
                    # Rich Live would hide prints and break the confirm prompt.
                    force = arg.strip().lower() in ("force", "--force", "-f")
                    keys = client.key_pool.key_values()
                    if not keys:
                        print_warning("Havuzda API anahtarı yok.")
                    else:
                        ensure_terms_for_keys(keys, interactive=True, force=force)

                elif cmd == "/keys":
                    _handle_keys_command(arg, client)

                elif cmd == "/tokens":
                    rep = session.token_report()
                    print_info("Bağlam / Token Bütçesi:")
                    print(f"  • Mesaj sayısı         : {rep['messages']}")
                    print(f"  • Tahmini prompt token : {rep['estimated_prompt_tokens']:,}")
                    print(f"  • Çıktı bütçesi        : {rep['output_budget']:,}")
                    print(f"  • Bağlam penceresi     : {rep['context_window']:,}")
                    print(f"  • Kalan bağlam         : {rep['remaining_context']:,}")
                    print(f"  • Kalibrasyon oranı    : {rep['calibration']}")
                    if rep["last_usage"]:
                        u = rep["last_usage"]
                        print(f"  • Son sunucu kullanımı : prompt={u['prompt_tokens']}, "
                              f"completion={u['completion_tokens']}, total={u['total_tokens']}")

                elif cmd == "/docs":
                    _handle_docs_command(arg, session, workspace_root)

                elif cmd == "/skills":
                    from src.skills import list_skills, categories
                    items = list_skills(arg or None)
                    if not items:
                        print_warning(f"'{arg}' kategorisinde beceri yok. Kategoriler: {', '.join(categories())}")
                    else:
                        print_info(f"Beceriler ({len(items)}):")
                        for s in items:
                            print(f"  • {s.name:<22} [{s.category}] {s.title}")
                        print("\nUygulamak için: /apply <beceri>")

                elif cmd == "/apply":
                    if not arg:
                        print_warning("Kullanım: /apply <beceri_adı>")
                    else:
                        try:
                            content = session.apply_skill(arg)
                        except ValueError as e:
                            print_error(str(e))
                        else:
                            print_info(f"Beceri uygulanıyor: {arg}")
                            _run_agent(session, content)

                elif cmd == "/diagnose":
                    from src.diagnostics import list_diagnostics, get_diagnostic, match_by_symptom
                    if not arg:
                        print_info("Arıza Triyajları:")
                        for d in list_diagnostics():
                            print(f"  • {d.name:<22} {d.title}")
                        print("\nÇalıştırmak için: /diagnose <ad>")
                    else:
                        diag = get_diagnostic(arg)
                        if diag is None:
                            hits = match_by_symptom(arg)
                            if hits:
                                print_info("Belirtiye uyan triyajlar:")
                                for d in hits:
                                    print(f"  • {d.name:<22} {d.title}")
                            else:
                                print_error(f"Triyaj bulunamadı: {arg}")
                        else:
                            print_info(f"Triyaj başlatılıyor: {diag.title}")
                            _run_agent(session, session.apply_diagnostic(diag.name))

                elif cmd == "/template":
                    from src.templates import list_templates, get_template, audiences
                    if not arg:
                        print_info("Çıktı Şablonları:")
                        for t in list_templates():
                            print(f"  • {t.name:<22} [{t.audience}] {t.title}")
                        print(f"\nKitleler: {', '.join(audiences())}")
                        print("Doldurmak için: /template <ad>")
                    else:
                        tmpl = get_template(arg)
                        if tmpl is None:
                            print_error(f"Şablon bulunamadı: {arg}")
                        else:
                            print_info(f"Şablon dolduruluyor: {tmpl.title}")
                            _run_agent(session, session.apply_template(tmpl.name))

                elif cmd == "/memory":
                    from src.memory import load_memory, set_profile, add_note
                    parts = arg.split(maxsplit=2)
                    sub = parts[0].lower() if parts else ""
                    if sub == "set":
                        if len(parts) < 3:
                            print_warning("Kullanım: /memory set <anahtar> <değer>")
                        else:
                            set_profile(parts[1], parts[2], workspace_root)
                            print_success(f"Hafıza güncellendi: {parts[1]} = {parts[2]}")
                            session.refresh_context()
                    elif sub == "note":
                        if len(parts) < 2:
                            print_warning("Kullanım: /memory note <metin>")
                        else:
                            add_note(parts[1], workspace_root)
                            print_success("Not hafızaya eklendi.")
                            session.refresh_context()
                    else:
                        state = load_memory(workspace_root)
                        profile = state.get("profile", {})
                        notes = state.get("notes", [])
                        print_info("Proje Hafızası:")
                        if profile:
                            for k, v in profile.items():
                                print(f"  • {k}: {v}")
                        else:
                            print("  (profil boş)")
                        if notes:
                            print("  Notlar:")
                            for n in notes[-10:]:
                                print(f"    - {n.get('text', '')}")
                        print("\nKomutlar: /memory set <k> <v> | /memory note <metin>")

                elif cmd == "/track":
                    from src.tracking import list_tasks, progress_summary
                    items = list_tasks(arg or "all", workspace_root)
                    summary = progress_summary(workspace_root)
                    print_info(f"Görevler ({len(items)}) — tamamlanma: %{summary['percent']}")
                    if not items:
                        print("  (görev yok)")
                    for t in items:
                        print(f"  • [{t.get('status')}] {t.get('id')}: {t.get('description')} (%{t.get('progress', 0)})")
                    print("\nKomutlar: /task add <id> <açıklama> | /progress <id> <yüzde>")

                elif cmd == "/task":
                    from src.tracking import add_task
                    parts = arg.split(maxsplit=2)
                    if len(parts) < 3 or parts[0].lower() != "add":
                        print_warning("Kullanım: /task add <id> <açıklama>")
                    else:
                        try:
                            add_task(parts[1], parts[2], workspace_root=workspace_root)
                        except ValueError as e:
                            print_error(str(e))
                        else:
                            print_success(f"Görev eklendi: {parts[1]}")

                elif cmd == "/progress":
                    from src.tracking import update_task
                    parts = arg.split(maxsplit=2)
                    if len(parts) < 2 or not parts[1].isdigit():
                        print_warning("Kullanım: /progress <id> <yüzde> [not]")
                    else:
                        note = parts[2] if len(parts) > 2 else ""
                        try:
                            item = update_task(parts[0], progress=int(parts[1]), note=note, workspace_root=workspace_root)
                        except ValueError as e:
                            print_error(str(e))
                        else:
                            print_success(f"{item['id']} → %{item['progress']} ({item['status']})")

                elif cmd == "/playbook":
                    from src.playbooks import list_playbooks, get_playbook, save_checkpoint
                    if not arg:
                        print_info("Playbook'lar:")
                        for pb in list_playbooks():
                            print(f"  • {pb.name:<12} {pb.title} ({len(pb.steps)} adım)")
                        print("\nBaşlatmak için: /playbook <ad>")
                    else:
                        pb = get_playbook(arg)
                        if pb is None:
                            print_error(f"Playbook bulunamadı: {arg}")
                        else:
                            save_checkpoint(pb.name, 0, workspace_root)
                            print_info(f"Playbook başlatıldı: {pb.title} (adım 1/{len(pb.steps)})")
                            _run_agent(session, session.apply_playbook_step(pb.name, 0))

                elif cmd == "/resume":
                    from src.playbooks import load_checkpoint, get_playbook, save_checkpoint
                    cp = load_checkpoint(workspace_root)
                    if not cp:
                        print_warning("Kaydedilmiş playbook adımı yok.")
                    else:
                        pb = get_playbook(cp["playbook"])
                        step = cp.get("next_step", 0)
                        if pb is None or step >= len(pb.steps):
                            print_info("Playbook tamamlanmış görünüyor.")
                        else:
                            print_info(f"Devam ediliyor: {pb.title} (adım {step + 1}/{len(pb.steps)})")
                            _run_agent(session, session.apply_playbook_step(pb.name, step))
                            save_checkpoint(pb.name, step + 1, workspace_root)

                elif cmd == "/language":
                    from src.language import get_language, set_language
                    if not arg:
                        current = get_language(workspace_root) or "(ayarlanmamış)"
                        print_info(f"Çıktı dili: {current}")
                        print("Değiştirmek için: /language <tr|en|de|...>")
                    else:
                        try:
                            canonical = set_language(arg, workspace_root)
                        except ValueError as e:
                            print_error(str(e))
                        else:
                            print_success(f"Çıktı dili ayarlandı: {canonical}")
                            session.refresh_context()

                elif cmd == "/scan":
                    target_dir = arg if arg else str(workspace_root)
                    with status_spinner(f"'{target_dir}' klasörü taranıyor..."):
                        tree_str = generate_tree(Path(target_dir))
                        stats = scan_workspace(Path(target_dir))
                    print(tree_str)
                    print_info(f"Toplam {stats['total_files']} dosya ({stats['total_size_kb']} KB).")

                elif cmd == "/files":
                    if not session.active_context_files:
                        print_info("Bağlamda dosya yok. Eklemek için: /add <dosya_yolu>")
                    else:
                        print("\nAktif Bağlam Dosyaları:")
                        for f in sorted(session.active_context_files):
                            print(f"  • {f}")
                        print()

                elif cmd == "/add":
                    if not arg:
                        print_warning("Kullanım: /add <dosya_yolu>")
                    else:
                        session.add_context_file(arg)
                        print_success(f"'{arg}' bağlama eklendi.")

                elif cmd == "/drop":
                    if not arg:
                        print_warning("Kullanım: /drop <dosya_yolu>")
                    else:
                        session.remove_context_file(arg)
                        print_info(f"'{arg}' bağlamdan çıkarıldı.")

                elif cmd in ("/undo", "/rollback"):
                    if not arg:
                        print_warning("Kullanım: /undo <dosya_yolu>")
                    else:
                        target = resolve_workspace_path(arg, workspace_root)
                        ok, msg = rollback_last_backup(target, workspace_root)
                        if ok:
                            print_success(msg)
                        else:
                            print_error(msg)

                elif cmd == "/auto":
                    if not arg:
                        state_str = "AÇIK (Değişiklikler otomatik uygulanır ve yedeklenir)" if session.auto_approve else "KAPALI (Her değişiklikte [Y/n] onayı istenir)"
                        print_info(f"Otomatik Onay Modu: {state_str}")
                        print("[dim]Değiştirmek için: /auto on veya /auto off[/dim]")
                    elif arg.lower() in ("on", "true", "1", "ac", "aç"):
                        session.set_auto_approve(True)
                        print_success("Otomatik onay modu AÇILDI. Kod değişiklikleri otomatik uygulanacak (yedekler .evren/backups klasöründe saklanır).")
                    elif arg.lower() in ("off", "false", "0", "kapat"):
                        session.set_auto_approve(False)
                        print_warning("Otomatik onay modu KAPATILDI. Her dosya değişikliğinde kullanıcı onayı istenecek.")
                    else:
                        print_warning("Kullanım: /auto [on|off]")

                elif cmd == "/run":
                    if not arg:
                        print_warning("Kullanım: /run <komut>")
                    else:
                        from src.tools import tool_run_command
                        out = tool_run_command(arg, workspace_root, auto_approve=session.auto_approve)
                        print(out)

                elif cmd == "/ssh":
                    _handle_ssh_command(arg, workspace_root)

                elif cmd == "/update":
                    from src.updater import (
                        perform_update, is_git_checkout, get_project_root, update_is_deferred,
                    )
                    src_kind = "git checkout" if is_git_checkout(get_project_root()) else "pip paketi"
                    print_info(f"Güncelleme kaynağı: {src_kind} (github.com/necmettincimen/evren-cli)")
                    deferred = update_is_deferred()
                    with status_spinner("En son sürüm çekiliyor ve kuruluyor..."):
                        ok, msg = perform_update(restart=False)
                    if ok and deferred:
                        print_success(msg)
                        print_info("EVREN CLI güncelleme için kapatılıyor.")
                        break
                    if ok:
                        print_success("Güncelleme tamamlandı. CLI yeni sürümle yeniden başlatılıyor...")
                        from src.updater import restart_process
                        restart_process()
                    else:
                        print_error(f"Güncelleme başarısız: {msg}")

                elif cmd == "/clear":
                    from src.workflow_state import clear_checkpoint
                    clear_checkpoint(workspace_root)
                    session.clear_history()
                    print_success("Sohbet geçmişi, kalıp ve aktif bağlam temizlendi.")

                else:
                    print_warning(f"Bilinmeyen komut: '{cmd}'. Komut listesi için /help yazın.")
                continue

            # Regular prompt: run autonomous agent loop
            _run_agent(session, user_input)

        except KeyboardInterrupt:
            if session.task_goal:
                print("\nİşlem durduruldu. Geçmiş duruyor; düzeltme yazarak devam edebilirsiniz.")
            else:
                print("\nİşlem durduruldu. Çıkmak için /exit yazabilirsiniz.")
        except EOFError:
            print("\nÇıkış yapılıyor...")
            break
        except Exception as e:
            print_error(f"Beklenmeyen hata: {e}")
