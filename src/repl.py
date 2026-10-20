"""
Interactive REPL (Read-Eval-Print Loop) for EVREN CLI.
Provides the EVREN TUI terminal experience, slash commands,
context management, model switching, and real-time agent interaction.
"""

import re
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
from src.commands import is_command_input, unknown_command_message
from src.extras import (
    expand_mentions, handle_commit, handle_compact, handle_context, handle_doctor,
    handle_init, handle_review, handle_rewind, handle_role, is_continue_phrase,
    make_completer, make_status_line, refresh_custom_commands, render_custom_command,
    resume_last_task,
)
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


def _config_source_info(client) -> dict:
    """Config/API key origin for the banner; never includes key values."""
    try:
        from src.config import config_source_report

        pool_size = None
        if client is not None:
            # `ApiKeyPool.keys` is a property returning a list; stay tolerant of a
            # method-style implementation too.
            keys = getattr(getattr(client, "key_pool", None), "keys", None)
            if callable(keys):
                keys = keys()
            if keys is not None:
                pool_size = len(keys)
        return config_source_report(pool_size)
    except Exception:
        return {}


def _bootstrap_docs(workspace_root) -> tuple[str, list[str]]:
    """Creates/refreshes the smart context docs at startup.

    Returns (lines, details): one short summary line plus any problem notes
    (conflicts, write errors) that are only printed when something is off.
    Never raises: a docs failure must not stop the CLI from starting.
    """
    try:
        from src.cli_home import ensure_cli_docs
        from src.project_docs import ensure_project_docs

        # The CLI's own skills.md / commands.md must still be refreshed; only
        # the "CLI bağlamı: <path>" line was startup noise.
        ensure_cli_docs()
        result = ensure_project_docs(workspace_root)
        return result.summary(), result.details()
    except Exception as e:
        return f"bağlam dosyaları atlandı: {e}", []


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
    if is_command_input(text):
        return ("command", text)
    session.interrupted = False
    if text:
        return ("followup", text)
    return ("continue", "")


def _guidance_context(workspace_root) -> str:
    """Compact context for the prompt-preparation call.

    The guidance request is a separate API call made before the agent runs, so
    its whole payload is pure overhead. It only needs enough project context to
    ask a project-specific question: the one-line digest plus the workspace path
    is enough, and it is markedly smaller than the full execution context block.
    """
    try:
        from src.project_docs import docs_digest

        digest = docs_digest(workspace_root)
    except Exception:
        digest = ""
    lines = [f"Çalışma dizini: {workspace_root}"]
    if digest:
        lines.append("PROJE ÖZETİ (kısa; canlı ortam önceliklidir):")
        lines.append(digest)
    return "\n".join(lines)


def _collect_frame(session: AgentSession, pt_session, previous=None):
    def guidance(frame, clarify):
        label = "Brief netleştiriliyor..." if clarify else "Boş alanlar için öneriler hazırlanıyor..."
        with status_spinner(label):
            return request_frame_guidance(
                session.client, session.model, frame, clarify,
                _guidance_context(session.workspace_root),
            )

    return collect_guided_prompt_frame(
        lambda label: _read_line(pt_session, label), guidance, previous,
    )


def _run_agent(
    session: AgentSession, prompt: str | None = None, *, followup: bool = False,
    compact: bool = False,
) -> None:
    """Run or continue, then offer opt-in persistence outside auto-approval.

    `compact=True` is for text the user typed or pasted: a big paste is saved to
    .evren/tmp and summarized (skill, template and playbook prompts stay whole).
    """
    from src.interrupt import RunWatcher, pop_queued

    try:
        with RunWatcher():
            _run_agent_inner(session, prompt, followup=followup, compact=compact)
        _offer_plan_execution(session)
    finally:
        try:
            from src.sessions import save_session

            save_session(session)
        except Exception:
            pass
    queued = pop_queued()
    if queued and not session.interrupted:
        print_info(f"Sıradaki istek çalıştırılıyor: {queued}")
        _run_agent(session, queued, compact=True)


def _offer_plan_execution(session: AgentSession) -> None:
    """Plan mode flow: after a plan was written, offer to apply it in the same session."""
    import time as _t

    from src.modes import AgentMode

    if session.mode != AgentMode.PLAN or session.interrupted:
        return
    plans = sorted(Path(session.workspace_root).glob("plans/*/plan.md"), key=lambda p: p.stat().st_mtime)
    if not plans or _t.time() - plans[-1].stat().st_mtime > 600:
        return
    if not (getattr(session, "_plan_seen", None) != str(plans[-1].stat().st_mtime)):
        return
    session._plan_seen = str(plans[-1].stat().st_mtime)
    try:
        ans = input("\nPlanı uygula? [e/H]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return
    if ans not in ("e", "evet", "y", "yes"):
        return
    session.mode = AgentMode.NORMAL
    print_info("Normal moda geçildi.")
    rel = plans[-1].relative_to(session.workspace_root).as_posix()
    try:
        plan_text = plans[-1].read_text(encoding="utf-8")
    except OSError:
        plan_text = ""
    steps = [ln for ln in plan_text.splitlines() if re.match(r"^\s*(\d{1,2}[.)]|[-*])\s+\S", ln)]
    # A two-step plan was applied with four todo_write calls and a full test run.
    if len(steps) > 3:
        session._plan_to_todos(plan_text)
        how = "Adımlar görev listesine yüklendi; maddeleri bitirdikçe toplu işaretle."
    else:
        how = "Plan kısa: todo_write kullanma, doğrudan uygula."
    _run_agent(
        session,
        f"{rel} planını uygula (içerik aşağıda; dosyayı yeniden okuma). {how} "
        "Doğrulamada yalnız ilgili testleri çalıştır.\n\n" + plan_text[:6000],
    )


def _run_agent_inner(
    session: AgentSession, prompt: str | None = None, *, followup: bool = False,
    compact: bool = False,
) -> None:
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
        session.run_step(prompt, compact=compact)

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


HELP_LEGACY = """
[bold cyan]Eski (Foreman) komutlar - hâlâ çalışır:[/bold cyan]
  [green]/diagnose [ad][/green]        Arıza triyajı çalıştırır (örn: /diagnose build-failure)
  [green]/template [ad][/green]        Çıktı şablonunu doldurur (örn: /template adr)
  [green]/track [filtre][/green]       Görevleri listeler (all|open|<durum>)
  [green]/task add <id> <açıklama>[/green]  Yeni takip görevi ekler
  [green]/progress <id> <yüzde>[/green]     Görev ilerlemesini günceller
  [green]/playbook [ad][/green]        Playbook'ları listeler veya başlatır
  [green]/resume[/green]               Kaydedilmiş playbook adımından devam eder
  [green]/checkpoint [save|clear][/green]  Onaylı kalıcı görev kaydı
  [green]/blockers[/green]             Engelleri ve çözüm yönlendirmelerini gösterir
  [green]/check-in [update][/green]     Açık/gecikmiş/duraklamış görevleri gözden geçirir
  [green]/decisions [add][/green]       Onaylı mimari karar günlüğü
"""


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
  [green]/debug [N][/green]            Son görevin adım tablosunu gösterir (istek, araç, süre; N = sondan N. görev)
  [green]!komut[/green]                Komutu ajanla aynı kabukta (Git Bash) çalıştırır; çıktı bir sonraki göreve bağlam olur
  [green]/docs [alt komut][/green]      agent.md/changelog.md durumu; refresh|unmark|path
  [green]/keys[/green]                 API anahtar havuzunu listeler (maskeli) ve yönetir
  [green]/keys add/remove[/green]      Anahtar ekler/çıkarır (~/.evren-cli/config.json)
  [green]/keys next[/green]            Aktif anahtarı sabitleyerek sıradakine geçer
  [green]/terms[/green]                Tüm anahtarlar için şartları denetler / kabul eder (/terms force)
  [green]/skills [kategori][/green]    Kullanılabilir mühendislik becerilerini listeler
  [green]/skill <beceri>[/green]       Bir beceriyi (yerleşik veya kullanıcı) uygular (örn: /skill root-cause-debug; eşdeğeri /apply)
  [green]/memory[/green]               Proje hafızasını (profil + notlar) gösterir
  [green]/memory set <k> <v>[/green]   Profil alanı kaydeder (örn: /memory set stack FastAPI)
  [green]/memory note <metin>[/green]  Hafızaya not ekler
  [green]/language [ad][/green]        Çıktı dilini ayarlar (örn: /language tr)
  [green]/scan [klasör][/green]        Çalışma alanını tarar, dosya ağacını ve istatistikleri çıkarır
  [green]/files[/green]                İstem bağlamına eklenmiş aktif dosyaları listeler
  [green]/add <dosya>[/green]          Bir dosyayı doğrudan istem bağlamına ekler
  [green]/drop <dosya>[/green]         Bir dosyayı istem bağlamından çıkarır
  [green]/undo <dosya>[/green]         Belirtilen dosyayı .evren/backups yedeğinden geri yükler
  [green]/auto [on/off][/green]        Otomatik dosya onay modunu açar veya kapatır
  [green]/run <komut>[/green]          PowerShell/terminal komutunu doğrudan çalıştırır
  [green]/bg[/green]                   Arka planda çalışan/çalışmış komutları listeler (run_in_background)
  [green]/todos[/green]                Ajanın güncel görev listesini (todo checklist) gösterir
  [green]/ssh[/green]                  Kayıtlı SSH host'larını listeler
  [green]/ssh add <alias> <user@host> [port][/green]  Yeni SSH host'u tanımlar
  [green]/ssh remove <alias>[/green]   Kayıtlı SSH host'unu siler
  [green]/ssh test <alias>[/green]     SSH bağlantısını test eder
  [green]/update[/green]               En son sürümü GitHub'dan çeker ve CLI'ı yeniden başlatır
  [green]/clear[/green]                Mevcut sohbet ve bağlam geçmişini sıfırlar
  [green]/continue[/green]             Adım sınırında duraklayan görevi geçmişle sürdürür (Brief: devam)
  [green]/execute <yanıt>[/green]       Formu atlayarak mevcut göreve onay/takip yanıtı gönderir
  [green]/context[/green]              Bağlam kırılımı (sistem, araç şeması, geçmiş, araç çıktıları)
  [green]/compact [odak][/green]       Geçmişi şimdi sıkıştırır
  [green]/commit[/green]               Diff'ten commit mesajı üretir, onayınla commit eder (push yok)
  [green]/review[/green]               git diff'i ask kipinde inceletir
  [green]/init[/green]                 Proje için AGENTS.md üretir
  [green]/rewind[/green]               Son görevin değiştirdiği dosyaları geri alır
  [green]/doctor[/green]               Ortam tanılaması (Python, PATH, sürüm, API, kota, SSH)
  [green]/rol [metin|temizle][/green]  Kalıcı rol (.evren/settings.json); her görevde uygulanır
  [green]/help legacy[/green]          Eski (Foreman) komutları listeler
  [green]/exit[/green] veya [green]/quit[/green]       Uygulamadan çıkar

[bold]Görev kalıbı (her tur):[/bold]
  Görev doğrudan ajana gider; Brief'te boş Enter önceki kalıbı korur.
  Büyük brief'ler önce planlayıcı modelde planlanır, sonra uygulayıcı modelde uygulanır.
  Rol/Çerçeve/Kısıt/Çıktı alanları için EVREN_FORM_FIELDS=true, görev öncesi netleştirme
  için EVREN_CLARIFY_ROUNDS=2 ayarla.
  Mevcut görevde "yap artık" / "onay veriyorum" formu atlar; diğer takip yanıtları: /execute <yanıt>.
  Öneride Enter=kabul, metin=düzenle, -=boş bırak. Tümü boşsa [green]İstek >[/green] sorulur.
  Slash komutunu ilk satıra (Brief) yaz. Örnek kalıp:
    01 Brief    timeout süresini 45 saniyeye çıkar
    02 Rol      kıdemli Windows mühendisi
    03 Çerçeve  cerrahi yama, mevcut stili koru
    04 Kısıt    yalnız src/api_client.py
    05 Çıktı    çalışan kod ve pytest

[bold]Serbest istek:[/bold]
  Kalıbı boş geçince yazdığın metin eskisi gibi tek prompt olarak gider.
  İlk sözcüğü kayıtlı bir komut değilse (örn. yapıştırılan /src/x.csproj : error ...) metin görev sayılır.
  Uzun yapıştırmalar (80 satır / 6000 karakter üstü) .evren/tmp/paste-N.txt'ye kaydedilir;
  modele baş/son satırlar ve hata satırları gider (EVREN_PASTE_MAX_LINES, EVREN_PASTE_MAX_CHARS).
"""


def _handle_debug_command(arg: str, workspace_root) -> None:
    """`/debug [last] [N]` — step table of the N-th last task (.evren/logs/trace.jsonl)."""
    from src.trace import print_last_task

    words = [word for word in (arg or "").split() if word.lower() != "last"]
    if words and not words[0].isdigit():
        print_warning("Kullanım: /debug [last] [N]  (N: sondan N. görev, varsayılan 1)")
        return
    print_last_task(workspace_root, int(words[0]) if words else 1)


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
        print_success(
            f"SSH host eklendi: {alias} -> {target}:{port} (varsayılan: salt-okuma; "
            "tüm çalışma alanlarında geçerli: ~/.evren/ssh_hosts.json)"
        )
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


def show_help(topic: str = ""):
    if topic.strip().lower() == "legacy":
        if HAVE_RICH:
            console.print(HELP_LEGACY)
        else:
            print(re.sub(r"\[/?[a-z ]+\]", "", HELP_LEGACY))
        return
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
  /debug [N]    - Son görevin adım tablosu (sondan N. görev)
  !komut        - Kabuk komutu çalıştır (Git Bash); çıktı ajana bağlam olur
  /steps [n]    - Adım tavanını göster veya ayarla (tasarruf: düşük tut)
  /keys         - API anahtar havuzunu yönet
  /keys next    - Sıradaki API anahtarına geç
  /terms        - Şartları kontrol et / kabul et
  /skills       - Mühendislik becerilerini listele
  /docs         - agent.md / changelog.md bağlam dosyaları
  /skill <b>    - Bir beceriyi uygula (/apply eşdeğeri)
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
  /todos        - Ajanın güncel görev listesini göster
  /ssh          - SSH host'larını listele/yönet
  /update       - En son sürümü çek ve yeniden başlat
  /clear        - Geçmişi temizle
  /exit         - Çıkış
""")


def start_repl(workspace_path: str | None = None, initial_model: str | None = None,
               continue_last: bool = False):
    """Starts the interactive EVREN CLI REPL."""
    workspace_root = get_workspace_dir(workspace_path)
    evren_dir = get_evren_dir(workspace_root)
    evren_dir.mkdir(parents=True, exist_ok=True)

    # Smart context docs: create/refresh agent.md + changelog.md (project) and the
    # CLI's own skills/lessons/commands files before the agent prompt is composed.
    docs_summary, docs_details = _bootstrap_docs(workspace_root)
    print_info(f"Bağlam: {docs_summary}")
    for note in docs_details:
        print_warning(f"Bağlam: {note}")

    client = EvrenClient(default_model=initial_model)

    # 4.2: quota + terms check runs in the background so the prompt appears at once.
    def _startup_checks() -> None:
        try:
            quota_data = None
            if len(client.key_pool) > 1:
                try:
                    quota_data = client.select_best_key()
                except Exception:
                    quota_data = None
            if quota_data is None:
                quota_data = client.get_quota()
            if len(client.key_pool) > 1 and quota_data.get("masked") is None:
                from src.api_key_pool import mask_key

                quota_data["masked"] = mask_key(client.api_key)
            remaining = quota_data.get("remaining", quota_data.get("remaining_daily_tokens", "Bilinmiyor"))
            limit = quota_data.get("limit")
            if isinstance(remaining, int) and isinstance(limit, int):
                qs = f"{remaining:,} / {limit:,}"
            elif isinstance(remaining, int):
                qs = f"{remaining:,}"
            else:
                qs = str(remaining)
            if quota_data.get("masked"):
                qs += f" (anahtar {quota_data['masked']})"
            print_info(f"Kota: {qs}")
        except Exception as e:
            print_warning(f"Kota okunamadı: {type(e).__name__}: {e}")

    try:
        ensure_terms_accepted(interactive=True, api_key=client.api_key, silent_ok=True)
    except Exception as e:
        print_warning(f"Kullanım şartları uyarısı ({type(e).__name__}): {e}")

    import threading

    threading.Thread(target=_startup_checks, daemon=True).start()
    quota_str = "kontrol ediliyor..."

    session = AgentSession(client=client, workspace_root=workspace_root, model=initial_model)

    print_banner(
        model=session.model,
        workspace_path=str(workspace_root),
        quota_info=quota_str,
        config_info=_config_source_info(client),
    )
    print_info(FORM_INTRO)
    from src.workflow_state import checkpoint_path
    if checkpoint_path(workspace_root).exists():
        print_info("Kalıcı checkpoint bulundu. İncelemek için /checkpoint, onaylı yükleme için /continue.")

    # History setup
    history_file = evren_dir / "history.txt"
    pt_session = None
    if HAVE_PT and sys.stdin.isatty():
        # prompt_toolkit needs a real Windows console; under Git Bash/mintty,
        # piped input or CI it raised NoConsoleScreenBufferError and the REPL
        # never started. Fall back to plain input() in those cases.
        try:
            pt_session = PromptSession(
                history=FileHistory(str(history_file)),
                auto_suggest=AutoSuggestFromHistory(),
                completer=make_completer(workspace_root),
                complete_while_typing=True,
                bottom_toolbar=make_status_line(session),
            )
        except Exception:
            pt_session = None

    from src import settings as _settings

    _settings.set_workspace(workspace_root)
    if continue_last:
        from src.sessions import list_sessions, load_session

        found = list_sessions(workspace_root, 1)
        if found and load_session(session, found[0]):
            print_info(f"Son oturum yüklendi: {found[0].stem}")
        else:
            print_warning("Devam ettirilecek oturum yok.")
    try:
        _repl_loop(session, client, workspace_root, pt_session)
    finally:
        try:
            from src.sessions import save_session

            save_session(session)
            from src.mcp_client import shutdown

            shutdown()
        except Exception:
            pass
        # Kill any still-running background command trees so the REPL never
        # orphans processes started via run_command(run_in_background=true).
        try:
            from src.background import get_manager

            killed = get_manager().cleanup_all()
            if killed:
                print_info(f"Arka planda çalışan {killed} komut kapatılırken durduruldu.")
        except Exception:
            pass


class _ReplCtx:
    """Shared state passed to slash-command handlers."""

    def __init__(self, session, client, workspace_root, pt_session):
        self.session = session
        self.client = client
        self.workspace_root = workspace_root
        self.pt_session = pt_session


# Handlers return True to leave the REPL loop; None/False keeps looping.

def _cmd_exit(ctx, arg):
    print_info("EVREN CLI sonlandırılıyor. İyi çalışmalar!")
    return True


def _cmd_help(ctx, arg):
    show_help(arg)


def _cmd_version(ctx, arg):
    from src.version import get_version, get_changelog_notes
    ver = get_version()
    print_info(f"EVREN CLI Sürümü: v{ver}")
    notes = get_changelog_notes(ver, max_items=20)
    if notes:
        print(f"\nv{ver} Sürüm Notları:")
        for note in notes:
            print(f"  • {note}")
        print()


def _cmd_model(ctx, arg):
    session = ctx.session
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


def _cmd_mode(ctx, arg):
    from src.modes import AgentMode, mode_label
    session = ctx.session
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


def _cmd_effort(ctx, arg):
    client = ctx.client
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


def _cmd_models(ctx, arg):
    with status_spinner("EVREN API modelleri çekiliyor..."):
        models = ctx.client.list_models()
    print("\nKullanılabilir Modeller:")
    for m in models:
        desc = RECOMMENDED_MODELS.get(m, "")
        extra = f" ({desc})" if desc else ""
        print(f"  • {m}{extra}")
    print()


def _cmd_quota(ctx, arg):
    with status_spinner("Kota ve şartlar sorgulanıyor..."):
        rows = ctx.client.get_all_quotas()
        t_status = get_terms_status()
    print_key_quotas(rows)
    print_info(
        f"Şartlar Durumu: v{t_status.get('current_version')} "
        f"(Kabul: {t_status.get('accepted')})"
    )


def _cmd_terms(ctx, arg):
    # Do NOT wrap the interactive accept flow in a spinner —
    # Rich Live would hide prints and break the confirm prompt.
    force = arg.strip().lower() in ("force", "--force", "-f")
    keys = ctx.client.key_pool.key_values()
    if not keys:
        print_warning("Havuzda API anahtarı yok.")
    else:
        ensure_terms_for_keys(keys, interactive=True, force=force)


def _cmd_keys(ctx, arg):
    _handle_keys_command(arg, ctx.client)


def _cmd_tokens(ctx, arg):
    rep = ctx.session.token_report()
    print_info("Bağlam / Token Bütçesi:")
    print(f"  • Mesaj sayısı         : {rep['messages']}")
    print(f"  • Tahmini prompt token : {rep['estimated_prompt_tokens']:,}")
    print(f"  • Çıktı bütçesi        : {rep['output_budget']:,}")
    print(f"  • Bağlam penceresi     : {rep['context_window']:,}")
    print(f"  • Sıkıştırma eşiği     : {rep.get('compaction_limit', 0):,} "
          f"(bütçe {rep.get('context_budget', 0):,}, pencerenin %{int(rep.get('compaction_ratio', 1) * 100)})")
    print(f"  • Kalan bağlam         : {rep['remaining_context']:,}")
    print(f"  • Kalibrasyon oranı    : {rep['calibration']}")
    if rep["last_usage"]:
        u = rep["last_usage"]
        print(f"  • Son sunucu kullanımı : prompt={u['prompt_tokens']}, "
              f"completion={u['completion_tokens']}, total={u['total_tokens']}")
    totals = rep.get("session_usage") or {}
    if rep.get("session_requests"):
        print(f"  • Oturum isteği       : {rep['session_requests']}")
        print(f"  • Oturum toplamı      : prompt={totals.get('prompt_tokens', 0):,}, "
              f"completion={totals.get('completion_tokens', 0):,}, "
              f"total={totals.get('total_tokens', 0):,}")
    try:
        from src.usage_log import read_usage, usage_summary

        summary = usage_summary(read_usage(ctx.workspace_root))
        if summary["requests"]:
            print(f"  • Kalıcı log (usage)  : {summary['requests']} istek, "
                  f"toplam {summary['total_tokens']:,} token, "
                  f"son {summary['recent_window']} istekte ort. prompt "
                  f"{summary['avg_prompt_tokens_recent']:,}, "
                  f"önbellek isabeti %{round(summary.get('cache_ratio', 0) * 100)}")
    except Exception:
        pass


def _cmd_debug(ctx, arg):
    _handle_debug_command(arg, ctx.workspace_root)


def _cmd_steps(ctx, arg):
    from src.config import default_max_steps

    session = ctx.session
    if not arg.strip():
        current = session.max_steps or default_max_steps()
        print_info(
            f"Adım tavanı: {current} (env varsayılanı: {default_max_steps()}). "
            "Değiştirmek için: /steps <1-100>"
        )
        return
    try:
        value = int(arg.strip())
    except ValueError:
        print_error("Geçersiz sayı. Örnek: /steps 20")
        return
    if not 1 <= value <= 100:
        print_error("Adım tavanı 1-100 arasında olmalı.")
        return
    session.max_steps = value
    print_info(
        f"Adım tavanı bu oturum için {value}. "
        "Kalıcı değişken: set EVREN_MAX_STEPS=<n>"
    )


def _cmd_docs(ctx, arg):
    _handle_docs_command(arg, ctx.session, ctx.workspace_root)


def _cmd_skills(ctx, arg):
    from src.skills import (
        list_skills,
        categories,
        reload_user_skills,
        user_skills_dir,
        USER_SKILL_WARNINGS,
    )

    if (arg or "").strip().lower() == "reload":
        loaded = reload_user_skills()
        print_info(
            f"Kullanıcı becerileri yeniden yüklendi: {len(loaded)} adet "
            f"({user_skills_dir()})"
        )
        for w in USER_SKILL_WARNINGS:
            print_warning(f"Atlanan dosya: {w}")
        return
    items = list_skills(arg or None)
    if not items:
        print_warning(f"'{arg}' kategorisinde beceri yok. Kategoriler: {', '.join(categories())}")
    else:
        print_info(f"Beceriler ({len(items)}):")
        for s in items:
            tag = "[user] " if s.source == "user" else ""
            print(f"  • {s.name:<22} [{s.category}] {tag}{s.title}")
        print("\nUygulamak için: /skill <beceri> (veya /apply <beceri>)")
        print(f"Kullanıcı becerileri dizini: {user_skills_dir()} (yeniden yükleme: /skills reload)")


def _cmd_skill(ctx, arg):
    if not arg:
        print_warning("Kullanım: /skill <beceri_adı>")
        return
    try:
        content = ctx.session.apply_skill(arg)
    except ValueError as e:
        from src.skills import user_skills_dir

        print_error(
            f"{e}. Kullanıcı becerisi eklemek için: "
            f"{user_skills_dir()} altına <ad>.md yazın, sonra /skills reload"
        )
    else:
        print_info(f"Beceri uygulanıyor: {arg}")
        _run_agent(ctx.session, content)


def _cmd_diagnose(ctx, arg):
    from src.diagnostics import list_diagnostics, get_diagnostic, match_by_symptom
    if not arg:
        print_info("Arıza Triyajları:")
        for d in list_diagnostics():
            print(f"  • {d.name:<22} {d.title}")
        print("\nÇalıştırmak için: /diagnose <ad>")
        return
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
        _run_agent(ctx.session, ctx.session.apply_diagnostic(diag.name))


def _cmd_template(ctx, arg):
    from src.templates import list_templates, get_template, audiences
    if not arg:
        print_info("Çıktı Şablonları:")
        for t in list_templates():
            print(f"  • {t.name:<22} [{t.audience}] {t.title}")
        print(f"\nKitleler: {', '.join(audiences())}")
        print("Doldurmak için: /template <ad>")
        return
    tmpl = get_template(arg)
    if tmpl is None:
        print_error(f"Şablon bulunamadı: {arg}")
    else:
        print_info(f"Şablon dolduruluyor: {tmpl.title}")
        _run_agent(ctx.session, ctx.session.apply_template(tmpl.name))


def _cmd_memory(ctx, arg):
    from src.memory import load_memory, set_profile, add_note
    session, workspace_root = ctx.session, ctx.workspace_root
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


def _cmd_track(ctx, arg):
    from src.tracking import list_tasks, progress_summary
    items = list_tasks(arg or "all", ctx.workspace_root)
    summary = progress_summary(ctx.workspace_root)
    print_info(f"Görevler ({len(items)}) — tamamlanma: %{summary['percent']}")
    if not items:
        print("  (görev yok)")
    for t in items:
        print(f"  • [{t.get('status')}] {t.get('id')}: {t.get('description')} (%{t.get('progress', 0)})")
    print("\nKomutlar: /task add <id> <açıklama> | /progress <id> <yüzde>")


def _cmd_task(ctx, arg):
    from src.tracking import add_task
    parts = arg.split(maxsplit=2)
    if len(parts) < 3 or parts[0].lower() != "add":
        print_warning("Kullanım: /task add <id> <açıklama>")
    else:
        try:
            add_task(parts[1], parts[2], workspace_root=ctx.workspace_root)
        except ValueError as e:
            print_error(str(e))
        else:
            print_success(f"Görev eklendi: {parts[1]}")


def _cmd_progress(ctx, arg):
    from src.tracking import update_task
    parts = arg.split(maxsplit=2)
    if len(parts) < 2 or not parts[1].isdigit():
        print_warning("Kullanım: /progress <id> <yüzde> [not]")
    else:
        note = parts[2] if len(parts) > 2 else ""
        try:
            item = update_task(parts[0], progress=int(parts[1]), note=note, workspace_root=ctx.workspace_root)
        except ValueError as e:
            print_error(str(e))
        else:
            print_success(f"{item['id']} → %{item['progress']} ({item['status']})")


def _cmd_playbook(ctx, arg):
    from src.playbooks import list_playbooks, get_playbook, save_checkpoint
    if not arg:
        print_info("Playbook'lar:")
        for pb in list_playbooks():
            print(f"  • {pb.name:<12} {pb.title} ({len(pb.steps)} adım)")
        print("\nBaşlatmak için: /playbook <ad>")
        return
    pb = get_playbook(arg)
    if pb is None:
        print_error(f"Playbook bulunamadı: {arg}")
    else:
        save_checkpoint(pb.name, 0, ctx.workspace_root)
        print_info(f"Playbook başlatıldı: {pb.title} (adım 1/{len(pb.steps)})")
        _run_agent(ctx.session, ctx.session.apply_playbook_step(pb.name, 0))


def _cmd_resume(ctx, arg):
    if (arg or "").strip().lower() != "playbook":
        from src.sessions import handle_resume

        handle_resume(ctx.session)
        return
    from src.playbooks import load_checkpoint, get_playbook, save_checkpoint
    workspace_root = ctx.workspace_root
    cp = load_checkpoint(workspace_root)
    if not cp:
        print_warning("Kaydedilmiş playbook adımı yok.")
        return
    pb = get_playbook(cp["playbook"])
    step = cp.get("next_step", 0)
    if pb is None or step >= len(pb.steps):
        print_info("Playbook tamamlanmış görünüyor.")
    else:
        print_info(f"Devam ediliyor: {pb.title} (adım {step + 1}/{len(pb.steps)})")
        _run_agent(ctx.session, ctx.session.apply_playbook_step(pb.name, step))
        save_checkpoint(pb.name, step + 1, workspace_root)


def _cmd_language(ctx, arg):
    from src.language import get_language, set_language
    if not arg:
        current = get_language(ctx.workspace_root) or "(ayarlanmamış)"
        print_info(f"Çıktı dili: {current}")
        print("Değiştirmek için: /language <tr|en|de|...>")
        return
    try:
        canonical = set_language(arg, ctx.workspace_root)
    except ValueError as e:
        print_error(str(e))
    else:
        print_success(f"Çıktı dili ayarlandı: {canonical}")
        ctx.session.refresh_context()


def _cmd_scan(ctx, arg):
    target_dir = arg if arg else str(ctx.workspace_root)
    with status_spinner(f"'{target_dir}' klasörü taranıyor..."):
        tree_str = generate_tree(Path(target_dir))
        stats = scan_workspace(Path(target_dir))
    print(tree_str)
    print_info(f"Toplam {stats['total_files']} dosya ({stats['total_size_kb']} KB).")


def _cmd_files(ctx, arg):
    session = ctx.session
    if not session.active_context_files:
        print_info("Bağlamda dosya yok. Eklemek için: /add <dosya_yolu>")
    else:
        print("\nAktif Bağlam Dosyaları:")
        for f in sorted(session.active_context_files):
            print(f"  • {f}")
        print()


def _cmd_add(ctx, arg):
    if not arg:
        print_warning("Kullanım: /add <dosya_yolu>")
    else:
        ctx.session.add_context_file(arg)
        print_success(f"'{arg}' bağlama eklendi.")


def _cmd_drop(ctx, arg):
    if not arg:
        print_warning("Kullanım: /drop <dosya_yolu>")
    else:
        ctx.session.remove_context_file(arg)
        print_info(f"'{arg}' bağlamdan çıkarıldı.")


def _cmd_undo(ctx, arg):
    if not arg:
        print_warning("Kullanım: /undo <dosya_yolu>")
    else:
        target = resolve_workspace_path(arg, ctx.workspace_root)
        ok, msg = rollback_last_backup(target, ctx.workspace_root)
        if ok:
            print_success(msg)
        else:
            print_error(msg)


def _cmd_auto(ctx, arg):
    session = ctx.session
    if not arg:
        state_str = "AÇIK (Değişiklikler otomatik uygulanır)" if session.auto_approve else "KAPALI (Her değişiklikte [Y/n] onayı istenir)"
        print_info(f"Otomatik Onay Modu: {state_str}")
        print("[dim]Değiştirmek için: /auto on veya /auto off[/dim]")
    elif arg.lower() in ("on", "true", "1", "ac", "aç"):
        session.set_auto_approve(True)
        print_success("Otomatik onay modu AÇILDI. Kod değişiklikleri otomatik uygulanacak (yedekleme varsayılan olarak kapalı; --backup ile açılır).")
    elif arg.lower() in ("off", "false", "0", "kapat"):
        session.set_auto_approve(False)
        print_warning("Otomatik onay modu KAPATILDI. Her dosya değişikliğinde kullanıcı onayı istenecek.")
    else:
        print_warning("Kullanım: /auto [on|off]")


def _cmd_run(ctx, arg):
    if not arg:
        print_warning("Kullanım: /run <komut>")
    else:
        from src.tools import tool_run_command
        out = tool_run_command(arg, ctx.workspace_root, auto_approve=ctx.session.auto_approve)
        print(out)


def _cmd_bg(ctx, arg):
    from src.background import get_manager

    records = get_manager().list()
    if not records:
        print_info("Arka planda çalıştırılmış komut yok. Ajan run_command(run_in_background=true) ile başlatabilir.")
    else:
        print("\nArka Plan Komutları:")
        for rec in records:
            print(f"  {rec.id:<6} {rec.status_line():<28} {rec.elapsed_seconds():>5}s  {rec.command[:70]}")
        print("  Durdurmak için: /bg stop <id>\n")
    if arg.lower().startswith("stop"):
        bg_target = arg.split(maxsplit=1)[1].strip() if len(arg.split(maxsplit=1)) > 1 else ""
        if not bg_target:
            print_warning("Kullanım: /bg stop <id>")
        elif get_manager().stop(bg_target):
            print_success(f"Arka plan komutu durduruldu: {bg_target}")
        else:
            print_warning(f"Bilinmeyen arka plan id: {bg_target}")


def _cmd_todos(ctx, arg):
    from src.todos import get_store

    checklist = get_store().render()
    print(checklist if checklist else "Görev listesi boş.")


def _cmd_ssh(ctx, arg):
    _handle_ssh_command(arg, ctx.workspace_root)


def _cmd_update(ctx, arg):
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
        return True
    if ok:
        print_success("Güncelleme tamamlandı. CLI yeni sürümle yeniden başlatılıyor...")
        from src.updater import restart_process
        restart_process()
    else:
        print_error(f"Güncelleme başarısız: {msg}")


def _cmd_commit(ctx, arg):
    handle_commit(ctx.session)


def _cmd_review(ctx, arg):
    handle_review(ctx.session, _run_agent)


def _cmd_init(ctx, arg):
    handle_init(ctx.session, _run_agent)


def _cmd_context(ctx, arg):
    handle_context(ctx.session)


def _cmd_compact(ctx, arg):
    handle_compact(arg, ctx.session)


def _cmd_rewind(ctx, arg):
    handle_rewind(ctx.session)


def _cmd_doctor(ctx, arg):
    handle_doctor(ctx.session)


def _cmd_rol(ctx, arg):
    handle_role(arg, ctx.session)


def _cmd_agent(ctx, arg):
    from src.agent_library import handle_agent
    handle_agent(arg, ctx.session)


def _cmd_agents(ctx, arg):
    from src.agent_library import handle_agents
    handle_agents(arg, ctx.session)


def _cmd_team(ctx, arg):
    from src.agent_library import handle_team
    handle_team(arg, ctx.session, _run_agent)


def _cmd_iterate(ctx, arg):
    from src.iteration import IterationRunner, status_text
    arg = (arg or "").strip()
    if arg in ("", "durum", "status"):
        print_info(status_text(ctx.workspace_root) if arg else "Kullanım: /iterate <hedef> | /iterate devam | /iterate durum")
        return
    resume = arg in ("devam", "resume")
    try:
        runner = IterationRunner(ctx.session, ctx.workspace_root, "" if resume else arg,
                                 item_steps=40, resume=resume)
        runner.run()
    except ValueError as e:
        print_warning(str(e))
    except KeyboardInterrupt:
        print_warning("İterasyon durduruldu; '/iterate devam' ile sürdürebilirsiniz.")
    finally:
        ctx.session.clear_history()


def _cmd_clear(ctx, arg):
    from src.workflow_state import clear_checkpoint
    clear_checkpoint(ctx.workspace_root)
    ctx.session.clear_history()
    print_success("Sohbet geçmişi, kalıp ve aktif bağlam temizlendi.")


# Command name -> handler. Names stay string literals (aliases listed
# explicitly) so tools can compare this table with src/commands.py.
# Note: "/resume" lists saved sessions; "/resume playbook" resumes a playbook.
_COMMAND_TABLE = {
    "/exit": _cmd_exit, "/quit": _cmd_exit, "/q": _cmd_exit,
    "/help": _cmd_help, "/h": _cmd_help, "/?": _cmd_help,
    "/version": _cmd_version, "/ver": _cmd_version, "/v": _cmd_version,
    "/model": _cmd_model,
    "/mode": _cmd_mode,
    "/effort": _cmd_effort,
    "/models": _cmd_models,
    "/quota": _cmd_quota, "/status": _cmd_quota,
    "/terms": _cmd_terms,
    "/keys": _cmd_keys,
    "/tokens": _cmd_tokens,
    "/debug": _cmd_debug,
    "/steps": _cmd_steps,
    "/docs": _cmd_docs,
    "/skills": _cmd_skills,
    "/apply": _cmd_skill, "/skill": _cmd_skill,
    "/diagnose": _cmd_diagnose,
    "/template": _cmd_template,
    "/memory": _cmd_memory,
    "/track": _cmd_track,
    "/task": _cmd_task,
    "/progress": _cmd_progress,
    "/playbook": _cmd_playbook,
    "/resume": _cmd_resume,
    "/language": _cmd_language,
    "/scan": _cmd_scan,
    "/files": _cmd_files,
    "/add": _cmd_add,
    "/drop": _cmd_drop,
    "/undo": _cmd_undo, "/rollback": _cmd_undo,
    "/auto": _cmd_auto,
    "/run": _cmd_run,
    "/bg": _cmd_bg,
    "/todos": _cmd_todos,
    "/ssh": _cmd_ssh,
    "/update": _cmd_update,
    "/commit": _cmd_commit,
    "/review": _cmd_review,
    "/init": _cmd_init,
    "/context": _cmd_context,
    "/compact": _cmd_compact,
    "/rewind": _cmd_rewind,
    "/doctor": _cmd_doctor,
    "/rol": _cmd_rol,
    "/agent": _cmd_agent,
    "/agents": _cmd_agents,
    "/team": _cmd_team,
    "/iterate": _cmd_iterate,
    "/iterasyon": _cmd_iterate,
    "/clear": _cmd_clear,
}


def _repl_loop(session: AgentSession, client: EvrenClient, workspace_root, pt_session) -> None:
    """Interactive command loop; returns on /exit or EOF. Cleanup lives in start_repl."""
    workspace_root = Path(workspace_root)
    last_frame = None
    custom_commands = refresh_custom_commands(workspace_root)
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
                    _run_agent(
                        session, session.apply_frame(collected, replace_identity=True),
                        compact=True,
                    )
                    continue
                else:
                    user_input = _read_line(pt_session, "İstek > ")
                    if not user_input:
                        continue

            # `!komut`: the user runs a command in the agent's shell (Git Bash).
            from src.shell_mode import is_shell_input, run_user_shell

            if is_shell_input(user_input):
                run_user_shell(user_input, session)
                continue

            # Check slash commands
            if user_input.strip().casefold() in ("devam", "/continue") or (
                is_continue_phrase(user_input) and session.pending_mode is None
            ):
                if session.pending_mode is None and session.task_goal:
                    resume_last_task(session, _run_agent)
                    continue
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
            if is_command_input(user_input):
                parts = user_input.split(maxsplit=1)
                cmd = parts[0].lower()
                arg = parts[1].strip() if len(parts) > 1 else ""
                if cmd == "/execute":
                    if not arg:
                        print_warning("Kullanım: /execute <onay veya takip yanıtı>")
                    elif is_continue_phrase(arg) and session.pending_mode is None:
                        resume_last_task(session, _run_agent)
                    else:
                        _run_agent(session, arg, followup=True)
                    continue
                if _handle_workflow_command(cmd, arg, session, lambda label: _read_line(pt_session, label)):
                    continue

                ctx = _ReplCtx(session, client, workspace_root, pt_session)
                if cmd in custom_commands and (cmd not in _COMMAND_TABLE or cmd == "/clear"):
                    _run_agent(session, render_custom_command(custom_commands[cmd], arg), compact=True)
                elif cmd in _COMMAND_TABLE:
                    if _COMMAND_TABLE[cmd](ctx, arg):
                        break
                else:
                    print_warning(unknown_command_message(cmd))
                continue

            # 7.7: a pasted existing image path becomes an @-mention
            _raw = user_input.strip().strip('"').strip("'")
            if _raw and len(_raw) < 260 and Path(_raw).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                try:
                    _ip = Path(_raw).expanduser()
                    if not _ip.is_absolute():
                        _ip = Path(workspace_root) / _ip
                    if _ip.is_file():
                        try:
                            _rel = _ip.resolve().relative_to(Path(workspace_root).resolve()).as_posix()
                        except ValueError:
                            _rel = str(_ip.resolve())
                        user_input = f"@{_rel} bu görseli incele ve içindeki metni/hatayı açıkla."
                except OSError:
                    pass
            mention = re.match(r"@([A-Za-z0-9][\w-]*)\s+(\S.*)", user_input, re.S)
            if mention:
                from src.agent_library import get_agent, run_with_agent
                if get_agent(mention.group(1), workspace_root):
                    run_with_agent(session, mention.group(1), mention.group(2), _run_agent)
                    continue

            # Regular prompt: run autonomous agent loop (expand @file mentions first)
            user_input, mention_warnings = expand_mentions(user_input, workspace_root)
            for note in mention_warnings:
                print_warning(note)
            _run_agent(session, user_input, compact=True)

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
