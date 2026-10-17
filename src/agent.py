"""
Autonomous Agent Loop for EVREN CLI.
Executes multi-step coding workflows, calls tools, handles reasoning tokens,
and ensures safe user approvals for Windows development.
"""

import json
import re
import time
from pathlib import Path
from typing import Any

from src.api_client import EvrenClient, normalize_chat_messages
from src.errors import EvrenApiError
from src.tools import TOOLS_SCHEMA, execute_tool_call, tools_for_mode
from src import settings as _settings
from src.tool_ext import extra_schemas as _ext_schemas


def _check_esc() -> None:
    from src.interrupt import STOP

    if STOP.is_set():
        STOP.clear()
        raise KeyboardInterrupt

from src.config import (
    get_workspace_dir,
    execution_context_block,
    git_context_block,
    EVREN_DEFAULT_MAX_TOKENS,
    default_max_steps,
    auto_route_enabled,
    planner_model,
    executor_model,
    route_min_tokens,
    planner_max_steps,
    executor_thinking,
)
from src.models import context_window_for
from src.token_manager import TokenManager
from src.history_compactor import HistoryCompactor
from src.modes import AgentMode, parse_mode_prefix
from src.prompt_frame import PromptFrame, build_user_content, compose_system
from src.tool_gateway import check_tool_allowed
from src.secret_vault import SecretVault, set_active_vault
from src import session_context
from src.paste import compact_paste, paste_info
from src.trace import TaskTrace
from src.ui import (
    print_tool_call,
    print_tool_result,
    print_reasoning,
    print_markdown,
    print_markdown_stream,
    flush_markdown,
    markdown_stream_active,
    print_run_summary,
    print_info,
    print_warning,
    print_error,
)

# Empty or truncated-tool turns. A single miss is normal for thinking models;
# giving up on the second one aborts real tasks. Budget doubles on each miss.
_MAX_STALLS = 4

# Sub-agent (delegate) instruction: the clean-context prompt the delegated
# research task runs under. {task} is substituted at delegation time.
_DELEGATE_INSTRUCTION = (
    "[ALT GÖREV] Ana ajan sana bağımsız bir araştırma görevi devretti. Temiz "
    "bağlamsın: ana konuşmayı göremezsin; görev metni kendi başına yeterlidir. "
    "Yalnızca salt-okuma araçlarıyla (view_file, find_files, list_directory, "
    "salt-okuma run_command, web_fetch) çalış; yazma yapma ve kullanıcıya sorma. "
    "En fazla birkaç adımda kanıt topla (dosya:satır referanslarıyla) ve son "
    "yanıtını 3-8 cümlelik kısa bir özet olarak yaz; özet ana ajanın geçmişine "
    "girecek, ham çıktı değil sonuç isteniyor.\n\nGÖREV:\n{task}"
)


def _stall_token_cap() -> int:
    """Ceiling for the doubled output budget on repeated stalls.

    The default model is a reasoning model: thinking and answer share this one
    budget, and every retry re-sends the whole context. The base budget for
    qwen3.8-flash-next is 8192 (see `token_manager`), so a 16k ceiling allows
    exactly one doubling; `EVREN_STALL_TOKEN_CAP` changes it (1024-32768).
    """
    import os

    raw = (os.getenv("EVREN_STALL_TOKEN_CAP") or "").strip()
    if not raw:
        return 16384
    try:
        value = int(raw)
    except ValueError:
        return 16384
    return value if 1024 <= value <= 32768 else 16384


def _tool_arguments_complete(tool_calls) -> bool:
    """True when every tool call has a complete JSON object in arguments."""
    if not tool_calls:
        return False
    for call in tool_calls:
        function = getattr(call, "function", None)
        raw = getattr(function, "arguments", None) or ""
        if isinstance(raw, dict):
            continue
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            return False
        if not isinstance(parsed, dict):
            return False
    return True


SYSTEM_PROMPT = """Sen EVREN LLM API destekli, kullanıcının yerel proje çalışma alanında çalışan uzman bir yazılım mühendisi ajanısın.

ÇALIŞMA TARZI:
- Hızlı ve kararlı ol: kısa düşün, ilk yararlı aracı hemen çağır; bağımsız araçları aynı turda birlikte çağır.
- Makul, geri alınabilir varsayımları kendin yap ve son yanıtta tek satırla belirt. Araçla bulunabilecek bilgiyi sorma. 'ask_user' yalnızca bloklayıcı belirsizlikte veya geri dönüşü zor/riskli
  işlemde (veri silme, üretim değişikliği, sır) kullanılır; verilmiş yanıt ve onayı tekrar sorma.
- Aynı başarısız çağrıyı yeni kanıt olmadan tekrarlama; iki denemede olmazsa başka yol dene veya engeli bildir.
- 3+ adımlı işlerde 'todo_write' ile kısa liste tut (her seferinde tam liste; başlarken in_progress, bitince completed).
- Çok dosyaya yayılan bağımsız araştırmayı 'delegate' ile devret; tek dosya/tek arama için kullanma.
- İstenen çıktıya ulaşınca DUR. Listeleme/kontrol/sorgu isteğinde kök neden analizi, log çekme, düzeltme gibi ek iş
  yapma; gerekirse tek satır öneri yaz. Son yanıt: yapılan iş, doğrulama sonucu, kalan engel.
- Öneri/teşhis isteğinde önce TEK komutla geniş salt-okuma anlık görüntü al, gerekirse 1-2 hedefli komut ekle. Her
  öneriyi gözlenen kanıta dayandır, bakmadığın konuda "kontrol edilmedi" de; düzeltmeyi yalnızca öner.
- Sorgu/salt-okuma işinde çalışma alanına dosya yazma; veriyi tek komutta süz. Deneme komutu (`echo test`) veya eşleşmeyecek deneme edit_file gönderme; betik gerekirse .evren/tmp/ altına yaz.
- Kodda çağıracağın fonksiyonun var olduğunu önce doğrula (find_files "def ad"); API uydurma.
- Kullanıcı onayı araç izinlerini, salt-okuma kipini ve komut onayını geçersiz kılmaz.

DOSYA VE KOMUTLAR:
- Değiştirmeden önce oku. Kod aramak için find_files(query="a|b") kullan (girintili satır döner, tek satırlık
  değişiklikte doğrudan edit_file hedefi olur), sonra view_file'ı yalnızca ilgili aralıkla çağır. "SÖZDİZİMİ HATASI" sonucunu önce düzelt.
- 'edit_file' ile cerrahi değişiklik yap; yorum, stil ve girintiyi koru.
- Kod değişikliğinden sonra ilgili testi/sözdizimi kontrolünü 'run_command' ile çalıştır; başarıyı yalnızca doğrulanmış sonuca göre bildir.
- Windows'ta PowerShell sözdizimi için run_command use_powershell=true, Python kodu için use_python=true ver
  (yalnızca kodu yaz); elle powershell -Command / python -c sarmalama.
- CHANGELOG'da yayımlanmış sürüm notları geçmiştir, değiştirme; yeni notu [Unreleased]'a yaz.
- Commit: önce `git log -5` ile mesaj biçimine uy; başlık en fazla ~72 karakter ve NE değiştiğini anlatsın,
  ayrıntı gövdeye (`git commit -m "<başlık>" -m "- madde"`). ASCII'ye çevirme. İstenmedikçe push yapma. `git stash` kullanma (çalışma ağacındaki işi geri alır).

YEREL / UZAK HEDEF:
- Varsayılan hedef yerel makinedir ("bu host", "bu hosts", "bu makine", "burada", "localhost", "127.0.0.1", "::1" = yerel): run_command/view_file/edit_file
  kullan. SSH'ı yalnızca kullanıcı açıkça uzak sunucu/kayıtlı alias isterse kullan; alias verildiyse doğrudan
  'ssh_run'/'ssh_read_file' çağır, hedef belirsizse 'ssh_list_hosts'. Rastgele host/IP kullanma. 'ssh_write_file' yalnızca allow_write=true host'ta ve onayla çalışır.

KİPLER: normal: tüm araçlar. ask: salt-okuma. plan: yalnızca 'create_plan' ile plans/<ad>/plan.md yazılır.
"REDDEDİLDİ: ..." kip kısıtıdır; tekrarlama, kullanıcıdan normal moda geçmesini iste.

PROJE BELGELERİ: agent.md/changelog.md/AGENTS.md özetleri hazır bağlamdır; canlı dosyayla çelişirse canlı dosya
ve sistem talimatı kazanır.
"""

# Domain notes that used to ride on every request. They are only relevant to
# HTTP / Argo CD work, so they are attached to the task text when it mentions
# those topics instead of inflating the system prompt for every other task.



# (file stem in src/domain_notes, triggers): attached when one trigger occurs.
_DOMAIN_NOTES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("http_argo", ("argo", "curl", "/api/v1", "http://", "https://", "resource-tree")),
    ("drone", ("drone",)),
    ("k8s", ("kubectl", "k8s", "kubernetes", "pod", "namespace", "deployment")),
)


def _load_domain_note(stem: str) -> str:
    try:
        path = Path(__file__).parent / "domain_notes" / f"{stem}.md"
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


# Kept as module constants for callers that imported them before 3.5.
HTTP_ARGO_NOTES = _load_domain_note("http_argo")
DRONE_NOTES = _load_domain_note("drone")
K8S_NOTES = _load_domain_note("k8s")


PLANNER_INSTRUCTION ="""[EVREN PLANLAYICI] Bu turda yalnızca uygulama planı çıkar; hiçbir dosyayı değiştirme.
Keşfi az turda yap: ilk turda ilgili anahtar kelimeleri TEK find_files(query="a|b|c") ile ara ve bağımsız
okumaları aynı turda birlikte çağır; ikinci turda yalnızca gereken aralıkları view_file(start_line,end_line) ile oku.
findstr/grep kullanma, dosyaları baştan sona okuma.
Kullanıcıya soru sorma: belirsiz noktayı "Varsayım:" satırıyla yaz.
Son yanıtın yalnızca plan olsun: en fazla 10 numaralı adım; her adımda hedef dosya ve satır aralığı/fonksiyon,
yapılacak değişiklik ve doğrulama komutu. Uygulayıcı keşfi tekrarlamasın diye gördüğün mevcut fonksiyon
adlarını ve satır numaralarını plana yaz. Uzun gerekçe yazma."""


def execution_brief(plan: str, planner: str) -> str:
    """Text appended to the task so the executor applies the plan directly."""
    return (
        f"UYGULAMA PLANI (planlayıcı model: {planner}):\n{plan.strip()}\n\n"
        "Bu planı sırayla uygula. Planı yeniden tartışma veya baştan analiz etme; yalnızca "
        "bir adım canlı kodla çelişirse o adımı uyarla. Bağımsız adımları aynı turda çağır."
    )


_LISTING_RE = re.compile(r"(?i)\b(listele|göster|goster|yazdır|getir|list|show)")
_ANALYSIS_RE = re.compile(
    r"(?i)(analiz|karşılaştır|kontrol|neden|hangi|bul\b|süz|filtre|sadece|yalnızca|olmayan|eksik|hatalı)"
)


_PLAN_INTENT_RE = re.compile(
    r"(?i)\b(planla\w*|plan\s+(yap|çıkar|hazırla|oluştur)\w*|önce\s+plan)"
)


def is_listing_task(text: str) -> bool:
    """True for plain "list/show X" requests (no filtering or analysis asked)."""
    from src.intent import clef_label

    label = clef_label(text, ["listing", "analysis", "other"])
    if label:
        return label == "listing"
    return bool(_LISTING_RE.search(text or "")) and not _ANALYSIS_RE.search(text or "")


def ssh_target_note(text: str, workspace_root) -> str:
    """Names the registered SSH host(s) a task mentions, so the work goes there.

    Without it the model sometimes ran `kubectl` locally with run_command,
    where an unrelated kubeconfig answered with another cluster's pods.
    """
    try:
        from src.ssh_hosts import load_hosts

        hosts = load_hosts(workspace_root)
    except Exception:
        return ""
    lowered = (text or "").casefold()
    hits = [
        alias for alias in sorted(hosts, key=len, reverse=True)
        if re.search(rf"(?<![\w-]){re.escape(alias.casefold())}(?![\w-])", lowered)
    ]
    if not hits:
        return ""
    targets = ", ".join(f"'{a}' ({hosts[a].target})" for a in hits)
    return (
        f"[EVREN] HEDEF: Görevde kayıtlı SSH host'u geçiyor: {targets}. Bu host'taki işleri "
        "ssh_run/ssh_read_file ile o alias üzerinde yap; kubectl/helm/systemctl gibi komutları yerel "
        "run_command ile çalıştırma (yerel makine farklı bir sistemdir)."
    )


# Commands that can change files, so an identical later call may legitimately
# return something new. Deliberately not the ask-mode gateway whitelist: that
# one rejects `git grep` and pipes, which made every looping grep reset the
# repeat guard.
_MUTATING_COMMAND_RE = re.compile(
    r"(?ix)"
    r"\bgit\s+(?:-C\s+\S+\s+)?(?:checkout|switch|pull|merge|rebase|reset|apply|am|stash|cherry-pick|revert|"
    r"commit|restore|clean|mv|rm|fetch)\b"
    r"|\b(?:pip|pip3|npm|pnpm|yarn|dotnet|cargo|go|poetry|uv)\s+(?:install|add|remove|uninstall|restore|"
    r"update|upgrade|new|publish|tool)\b"
    r"|(?:^|[\s;&|(])(?:rm|del|erase|rmdir|rd|mv|move|cp|copy|xcopy|robocopy|mkdir|md|ren|rename|touch|"
    r"truncate|tee)\b"
    r"|\bsed\s+-i\b"
    r"|\b(?:Set-Content|Add-Content|Out-File|New-Item|Remove-Item|Copy-Item|Move-Item|Rename-Item)\b"
)
_REDIRECT_RE = re.compile(r"(?<![0-9&<>])>>?\s*(?!&|nul\b|NUL\b|/dev/null|\$null)\S")


def command_may_modify_files(command: str) -> bool:
    """Heuristic: True when a shell command can change files on disk."""
    return bool(_MUTATING_COMMAND_RE.search(command or "") or _REDIRECT_RE.search(command or ""))


# Tools whose repeated identical calls are legitimate (polling, user turns).
from src.tool_registry import REPEAT_EXEMPT as _REG_REPEAT
_REPEAT_EXEMPT_TOOLS = frozenset(_REG_REPEAT | {"stop_background"})
# Blocked/flagged repeats after which the next turn must answer without tools.
_LOOP_BLOCK_LIMIT = 3


_CODE_EXTS = frozenset({".py", ".cs", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".kt"})
_TEST_CMD_RE = re.compile(
    r"(?i)\b(pytest|unittest|dotnet\s+(test|build)|npm\s+(run\s+)?(test|build)|pnpm\s+(run\s+)?test|"
    r"yarn\s+test|cargo\s+(test|build|check)|go\s+(test|build|vet)|mvn\s+(test|verify)|gradle\w*\s+(test|build)|"
    r"tsc|compileall|py_compile)\b"
)


def detect_test_command(workspace_root) -> str:
    """Best-effort project test command, or an empty string."""
    try:
        root = Path(workspace_root)
        if any(root.glob("*.sln")) or any(root.glob("**/*.csproj")):
            return "dotnet test"
        pkg = root / "package.json"
        if pkg.is_file():
            scripts = (json.loads(pkg.read_text(encoding="utf-8")).get("scripts") or {})
            if "test" in scripts:
                return "npm test"
        if (root / "tests").is_dir() and any(root.glob("**/*.py")):
            return "pytest"
    except Exception:
        pass
    return ""


def verification_note(workspace_root) -> str:
    """Transient reminder asking the model to run tests; empty if no command found."""
    command = detect_test_command(workspace_root)
    if not command:
        return ""
    return (
        f"[EVREN] DOĞRULAMA: Kod dosyası değiştirdin ama bundan sonra test/derleme çalıştırmadın. "
        f"Bitirmeden önce `{command}` komutunu çalıştır; hata varsa düzelt, geçerse kısa özetle bitir."
    )


def domain_hints(text: str) -> str:
    """Returns topic notes relevant to this task text, or an empty string."""
    lowered = (text or "").casefold()
    chunks = [
        _load_domain_note(stem) for stem, triggers in _DOMAIN_NOTES
        if any(trigger in lowered for trigger in triggers)
    ]
    return "\n\n".join(c for c in chunks if c)


class AgentSession:
    """Manages an interactive or autonomous multi-step agent session."""

    def __init__(
        self,
        client: EvrenClient,
        workspace_root: Path | None = None,
        model: str | None = None,
        system_prompt: str | None = None,
        auto_approve: bool | None = None,
    ):
        from src.config import EVREN_AUTO_APPROVE
        self.client = client
        self.workspace_root = workspace_root or get_workspace_dir()
        self.model = model or client.default_model
        self.base_system_prompt = system_prompt or SYSTEM_PROMPT
        self._git_block = self._read_git_block()
        self.system_prompt = self._compose_system(self.base_system_prompt)
        self.frame = PromptFrame()
        self.auto_approve = auto_approve if auto_approve is not None else EVREN_AUTO_APPROVE
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt}
        ]
        self.active_context_files: set[str] = set()

        # Token management: estimate + calibrate, and compact history when needed.
        from src.config import compact_target_ratio, smart_compact_enabled

        self.token_manager = TokenManager(context_window=context_window_for(self.model))
        self._window_model = self.model
        self.compactor = HistoryCompactor(
            self.token_manager,
            smart=smart_compact_enabled(),
            summarizer=self._summarize_for_compaction,
            target_ratio=compact_target_ratio(),
        )
        # Read-repeat memory (see _read_repeat): (path, start, end) -> record.
        self._reads: dict[tuple, dict[str, Any]] = {}
        self._step_no = 0
        self.last_usage: dict[str, Any] | None = None
        # Session-wide server-reported totals (feeds /tokens and usage.jsonl).
        self.usage_totals: dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        self.usage_requests: int = 0
        self._compaction_notified = False
        # Optional per-session step ceiling set by `/steps`; None = EVREN_MAX_STEPS.
        self.max_steps: int | None = None
        # Thinking switch for the running phase: None = server default,
        # False = `thinking: false` (fast executor). A stalled step always
        # retries with thinking off.
        self._thinking: bool | None = None
        # (model, thinking) of the last routed task, reused by /continue and
        # follow-ups so they keep running on the executor.
        self._exec_profile: tuple[str, bool | None] | None = None
        # User-supplied secrets live here, never in the prompt (see secret_vault).
        # The session context holds the other formerly module-level state
        # (todos, background manager, user skills, stream buffer).
        self.context = session_context.current()
        self.vault = SecretVault()
        # Set once a plain "list X" task has shown its full output to the user:
        # the next turn may only summarise (tool_choice="none").
        self._listing_done = False
        # Repeat guard (see _repeat_guard): identical tool calls within a task.
        self._reset_repeat_guard()

        # Working mode (normal / ask / plan). Persisted across turns; can be
        # overridden per-turn via an "ask:" / "plan:" message prefix.
        self.mode: AgentMode = AgentMode.NORMAL
        self.pending_mode: AgentMode | None = None
        self.interrupted = False
        self.task_goal = ""
        # Lightweight audit trail of the current task (feeds changelog.md).
        self.tool_trace: list[dict[str, Any]] = []
        # Step trace of the running task (.evren/logs/trace.jsonl, see src/trace.py).
        self._trace_writer: TaskTrace | None = None

    def _read_git_block(self) -> str:
        try:
            return git_context_block(self.workspace_root)
        except Exception:
            return ""

    def _compose_system(self, base: str) -> str:
        """Appends runtime environment, project docs, memory and language blocks."""
        blocks: list[str] = [execution_context_block(self.workspace_root)]
        # Git summary is read once per session in __init__ (and on /clear).
        # _compose_system also runs for every REPL task (apply_frame), refresh
        # and checkpoint restore; re-running git there would change the system
        # message after each task that edits files and break the prompt cache.
        if self._git_block:
            blocks.append(self._git_block)
        try:
            from src.cli_home import cli_docs_context_block
            from src.project_docs import docs_context_block

            for block in (
                docs_context_block(self.workspace_root),
                cli_docs_context_block(),
            ):
                if block:
                    blocks.append(block)
        except Exception:
            pass
        from src.workflow_state import decision_context

        try:
            decisions = decision_context(self.workspace_root)
        except (OSError, ValueError) as e:
            print_warning(f"Karar günlüğü bağlama yüklenemedi: {e}")
        else:
            if decisions:
                blocks.append(decisions)
        try:
            from src.memory import memory_context_block
            mem = memory_context_block(self.workspace_root)
            if mem:
                blocks.append(mem)
        except Exception:
            pass
        try:
            from src.extras import role_context_block
            role = role_context_block(self.workspace_root)
            if role:
                blocks.append(role)
        except Exception:
            pass
        try:
            from src.agent_library import agent_context_block
            ag = agent_context_block(self.workspace_root)
            if ag:
                blocks.append(ag)
        except Exception:
            pass
        try:
            from src.language import language_context_block
            lang = language_context_block(self.workspace_root)
            if lang:
                blocks.append(lang)
        except Exception:
            pass
        return base.rstrip() + "\n\n" + "\n\n".join(blocks)

    def _active_agent_def(self):
        """The main agent chosen with `/agent`, or None."""
        try:
            from src.agent_library import active_agent

            return active_agent(self.workspace_root)
        except Exception:
            return None

    def _agent_tools(self, tools: list[dict]) -> list[dict]:
        """Restricts advertised tools to the main agent's list (if set)."""
        try:
            from src.agent_library import active_agent
            ag = active_agent(self.workspace_root)
        except Exception:
            return tools
        if not ag or not ag.tools:
            return tools
        allowed = set(ag.tools)
        return [t for t in tools if (t.get("function") or {}).get("name") in allowed] or tools

    def refresh_context(self):
        """Rebuilds the system prompt from base + memory + language blocks."""
        self.system_prompt = compose_system(self._compose_system(self.base_system_prompt), self.frame)
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0]["content"] = self.system_prompt

    def set_model(self, new_model: str):
        self.model = new_model
        self._sync_window()

    def _sync_window(self) -> None:
        """Keeps the token manager's context window in step with the model."""
        if getattr(self, "_window_model", None) != self.model:
            self._window_model = self.model
            self.token_manager.context_window = context_window_for(self.model)

    def set_mode(self, mode: AgentMode):
        self.mode = mode

    def _permission_gate(self, fn_name: str, args: dict) -> tuple[bool, str]:
        """Returns (allowed_by_rule, refusal_message). Also records the call for the 'a' answer."""
        _settings.set_workspace(self.workspace_root)
        verdict = _settings.check_permission(fn_name, args, self.workspace_root)
        if verdict == "deny":
            return False, "ayar dosyasındaki izin kuralı bu aracı engelliyor (permissions.deny)."
        reason = _settings.run_pre_tool(fn_name, args, self.workspace_root)
        if reason:
            return False, f"pre_tool hook engelledi: {reason}"
        _settings.set_current(fn_name, args)
        return verdict == "allow", ""

    def set_auto_approve(self, auto_approve: bool):
        self.auto_approve = auto_approve

    def add_context_file(self, rel_path: str):
        self.active_context_files.add(rel_path)

    def remove_context_file(self, rel_path: str):
        self.active_context_files.discard(rel_path)

    def clear_history(self):
        from src.todos import get_store

        get_store().clear()
        self.frame = PromptFrame()
        self._git_block = self._read_git_block()  # a cleared session starts fresh
        self.system_prompt = self._compose_system(self.base_system_prompt)
        self.messages = [{"role": "system", "content": self.system_prompt}]
        self.active_context_files.clear()
        self.last_usage = None
        self.pending_mode = None
        self.interrupted = False
        self.task_goal = ""
        self._thinking = None
        self._exec_profile = None
        self.vault.clear()

    def _close_open_tool_calls(self, note: str) -> None:
        """Adds a tool result for every call the model already sent."""
        answered = {
            message.get("tool_call_id")
            for message in self.messages
            if message.get("role") == "tool"
        }
        for message in reversed(self.messages):
            if message.get("role") != "assistant":
                continue
            calls = message.get("tool_calls") or []
            if not calls:
                return
            for call in calls:
                call_id = call.get("id")
                if call_id in answered:
                    continue
                function = call.get("function") or {}
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": function.get("name", ""),
                    "content": note,
                })
            return

    def note_interrupt(self, mode: AgentMode) -> None:
        """Keeps the task history so a short correction can continue it."""
        self._close_open_tool_calls(
            "İPTAL: Kullanıcı Ctrl+C ile işlemi durdurdu. Çalışan komut sonlandırıldı. "
            "Geçmiş korunuyor; aynı komutu yeniden başlatma."
        )
        self.messages.append({
            "role": "assistant",
            "content": (
                "Kullanıcı işlemi Ctrl+C ile durdurdu. Konuşma geçmişi ve tamamlanan "
                "adımlar duruyor. Kullanıcı küçük bir düzeltme ekleyebilir."
            ),
        })
        self.pending_mode = mode
        self.interrupted = True

    def continue_task(self, max_steps: int | None = None) -> str:
        """Resumes a step-limited task with history intact and its mode preserved."""
        if self.pending_mode is None:
            error = "HATA: Devam edilecek adım sınırında duraklamış görev yok."
            print_warning(error)
            return error
        mode = self.mode
        goal = self.task_goal
        self.mode = self.pending_mode
        try:
            return self.run_step(
                f"Asıl görev (geçmiş bağlam):\n{goal}\n\n"
                "Önceki adım sınırında duraklayan göreve mevcut geçmiş ve son araç "
                "sonuçlarından devam et. Brief/netleştirme formunu tekrar başlatma. "
                "Tamamlanan işleri tekrarlama; önce kalan işi ve son engeli değerlendir, "
                "kaydedilmiş durum eski olabileceği için değişiklikten önce canlı ortamı "
                "salt-okuma ile doğrula; sonra sıradaki küçük doğrulanabilir adımı uygula. Eksik izin veya "
                "kimlik bilgilerini varsayma; gerekli engeli açıkça bildir.",
                max_steps=max_steps,
                route=False,
            )
        finally:
            self.mode = mode
            self.task_goal = goal

    def save_checkpoint(self) -> Path:
        from src.workflow_state import save_checkpoint

        if self.pending_mode is None:
            raise ValueError("Kaydedilecek duraklamış görev yok.")
        return save_checkpoint(
            self.workspace_root, self.messages, self.pending_mode, self.model, vars(self.frame),
            self.task_goal,
        )

    def execute_followup(self, text: str, max_steps: int | None = None) -> str:
        """Use existing task context without preparing a new brief or changing permissions."""
        if not self.task_goal or not any(m.get("role") == "user" for m in self.messages):
            error = "HATA: Uygulanacak önceki görev yok; önce somut bir Brief yazın."
            print_warning(error)
            return error
        # A follow-up can be a big paste too (fresh build errors after "devam").
        session_context.set_current(self.context)
        set_active_vault(self.vault)
        text = self._compact_pasted_text(self.vault.capture(text))
        goal = self.task_goal
        mode = self.mode
        self.mode = self.pending_mode or self.mode
        try:
            return self.run_step(
                f"Asıl görev:\n{goal}\n\nKullanıcının takip yanıtı:\n{text}\n\n"
                "Mevcut geçmiş, netleştirme yanıtları ve kullanıcı onaylarıyla ilerle. "
                "Görevin ne olduğunu tekrar sorma; aynı formu veya onayları tekrarlama. "
                "Son onayı geçmişteki onay bekleyen maddelerle eşleştir; kapsam dışı "
                "iş veya eksik araç izni için onay varsayma. Salt-okuma kipini ve "
                "SSH yazma izinlerini koru. Yeni gerçek engel yoksa sıradaki uygulama "
                "adımını yap; engel varsa somut nedeni ve gereken tek eylemi bildir.",
                max_steps=max_steps,
                route=False,
            )
        finally:
            self.task_goal = goal
            self.mode = mode

    def restore_checkpoint(self) -> bool:
        from src.workflow_state import load_checkpoint

        state = load_checkpoint(self.workspace_root)
        if state is None:
            return False
        frame = PromptFrame(**state["frame"])
        system = compose_system(self._compose_system(self.base_system_prompt), frame)
        self.frame = frame
        self.system_prompt = system
        self.messages = [{"role": "system", "content": system}, *state["messages"]]
        self.model = state["model"]
        self.pending_mode = AgentMode(state["mode"])
        self.task_goal = state["goal"]
        return True

    def apply_frame(
        self, frame: PromptFrame, extra: str = "", *, replace_identity: bool = False,
    ) -> str:
        """Merges Rol/Çerçeve into the system message and returns the user text.

        Identity fields persist across turns until /clear. Blank fields on this
        turn do not erase a previously set Rol or Çerçeve. Brief, Kısıt, and
        Çıktı belong only to this turn. The guided form uses replace_identity
        to respect explicitly rejected or cleared identity fields.
        """
        if replace_identity:
            self.frame = PromptFrame()
        if frame.rol:
            self.frame.rol = frame.rol
        if frame.cerceve:
            self.frame.cerceve = frame.cerceve
        self.system_prompt = compose_system(self._compose_system(self.base_system_prompt), self.frame)
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0]["content"] = self.system_prompt
        return build_user_content(frame, extra=extra)

    def apply_skill(self, name: str, context: str = "") -> str:
        """Returns the user content for applying a catalog or user skill."""
        from src.skills import get_skill, reload_user_skills, _user_skills

        skill = get_skill(name)
        if skill is None and not _user_skills():
            # User skills may not be loaded yet (e.g. first turn) — try once.
            reload_user_skills()
            skill = get_skill(name)
        if skill is None:
            raise ValueError(f"Beceri bulunamadı: {name}")
        return skill.prompt_block(context)

    def apply_playbook_step(self, name: str, step_index: int, context: str = "") -> str:
        """Returns the user content for a single playbook step (0-indexed)."""
        from src.playbooks import get_playbook

        pb = get_playbook(name)
        if pb is None:
            raise ValueError(f"Playbook bulunamadı: {name}")
        return pb.step_prompt(step_index, context)

    def apply_diagnostic(self, name: str, context: str = "") -> str:
        """Returns the user content for running a diagnostic triage."""
        from src.diagnostics import get_diagnostic

        diag = get_diagnostic(name)
        if diag is None:
            raise ValueError(f"Triyaj bulunamadı: {name}")
        return diag.prompt_block(context)

    def apply_template(self, name: str, context: str = "") -> str:
        """Returns the user content for filling an output template."""
        from src.templates import get_template

        tmpl = get_template(name)
        if tmpl is None:
            raise ValueError(f"Şablon bulunamadı: {name}")
        return tmpl.prompt_block(context)

    def _summarize_for_compaction(self, messages: list[dict[str, Any]]) -> str | None:
        """Cheap summarizer call for the compactor; None on any failure."""
        from src.config import summary_model
        from src.summarizer import summarize_messages

        model = summary_model()
        saved = self.last_usage

        def _record(response: Any) -> None:
            self._record_usage(response, model=model)
            self.last_usage = saved

        return summarize_messages(self.client, messages, model, on_response=_record)

    def _record_usage(self, response: Any, *, step: int | None = None, model: str | None = None) -> None:
        """Stores the last server-reported usage and appends it to the usage log.

        Only token counters reach `.evren/logs/usage.jsonl`; message content and
        tool arguments are never written. Logging failures are ignored so a
        reporting problem can never break an agent turn.
        """
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        self.last_usage = {
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        }
        for key, value in self.last_usage.items():
            if isinstance(value, int) and not isinstance(value, bool):
                self.usage_totals[key] = self.usage_totals.get(key, 0) + value
        self.usage_requests += 1
        try:
            from src.usage_log import log_usage

            log_usage(
                usage,
                model=model or self.model,
                step=step,
                mode=getattr(self.mode, "value", str(self.mode)),
                workspace_root=self.workspace_root,
            )
        except Exception:
            pass

    @staticmethod
    def compaction_ratio() -> float:
        """Fraction of the context window at which compaction starts."""
        from src.config import compaction_ratio

        return compaction_ratio()

    def compaction_limit(self) -> int:
        """Token ceiling that triggers compaction for this session's model."""
        from src.config import context_budget

        window_limit = int(self.token_manager.context_window * self.compaction_ratio())
        return max(1024, min(context_budget(), window_limit))

    @staticmethod
    def _context_budget() -> int:
        from src.config import context_budget

        return context_budget()

    def token_report(self) -> dict[str, Any]:
        """Builds a token/context report for the /tokens command."""
        output_budget = self.token_manager.resolve_output_budget(
            self.model, EVREN_DEFAULT_MAX_TOKENS
        )
        estimated = self.token_manager.estimate_messages(self.messages)
        return {
            "messages": len(self.messages),
            "estimated_prompt_tokens": estimated,
            "output_budget": output_budget,
            "context_window": self.token_manager.context_window,
            "compaction_limit": self.compaction_limit(),
            "compaction_ratio": self.compaction_ratio(),
            "context_budget": self._context_budget(),
            "remaining_context": self.token_manager.remaining_context(self.messages, output_budget),
            "calibration": round(self.token_manager.calibration, 3),
            "last_usage": self.last_usage,
            "session_requests": self.usage_requests,
            "session_usage": dict(self.usage_totals),
        }

    # ------------------------------------------------------------------ #
    # Task audit trail -> changelog.md
    # ------------------------------------------------------------------ #

    _TRACE_FILE_TOOLS = {"edit_file": "path", "write_file": "path"}

    def _reset_trace(self) -> None:
        self.tool_trace = []

    def _note_tool_call(self, name: str, args: dict, result: str) -> None:
        """Records one executed tool call for the end-of-task changelog entry."""
        try:
            if len(self.tool_trace) >= 200:
                return
            ok = not (
                result.startswith("HATA:")
                or result.startswith("İPTAL:")
                or result.startswith("REDDEDİLDİ:")
            )
            record: dict[str, Any] = {"name": name, "ok": ok}
            path_key = self._TRACE_FILE_TOOLS.get(name)
            if path_key and isinstance(args.get(path_key), str):
                record["path"] = args[path_key]
            if name == "run_command":
                command = str(args.get("command", ""))[:200]
                record["command"] = command
                record["exit_ok"] = ok and "Çıkış Kodu: 0" in result[:40]
                tail_lines = [line for line in result.splitlines() if line.strip()]
                record["summary"] = (tail_lines[-1] if tail_lines else "")[:160]
            self.tool_trace.append(record)
        except Exception:
            pass

    # Step trace -> .evren/logs/trace.jsonl. Not to be confused with `tool_trace`
    # above (the changelog audit); TaskTrace swallows its own errors.

    def _trace_task_begin(self, text: str, mode: AgentMode) -> None:
        try:
            self._trace_writer = TaskTrace(self.workspace_root)
            self._trace_writer.start(
                model=self.model, mode=getattr(mode, "value", str(mode)), text=text,
            )
        except Exception:
            self._trace_writer = None

    def _trace_request(self, response: Any, started: float, *, error: bool = False) -> None:
        if self._trace_writer is None:
            return
        try:
            finish_reason = response.choices[0].finish_reason
        except Exception:
            finish_reason = None
        self._trace_writer.request(
            getattr(response, "usage", None),
            ms=(time.perf_counter() - started) * 1000,
            finish_reason=finish_reason,
            model=self.model,
            error=error,
        )

    def _trace_tool(
        self, name: str, args: Any, started: float, result: str, *, blocked: bool = False,
    ) -> None:
        if self._trace_writer is not None:
            self._trace_writer.tool(
                name, args, ms=(time.perf_counter() - started) * 1000, result=result, blocked=blocked,
            )

    def _trace_task_end(self, result: str | None) -> None:
        writer, self._trace_writer = self._trace_writer, None
        if writer is not None:
            writer.end(result)

    def changelog_entry(self, final_text: str = "") -> dict:
        """Builds the changelog payload for the task that just finished."""
        files: list[str] = []
        commands: list[str] = []
        exit_codes: list[int] = []
        verification = ""
        for record in self.tool_trace:
            path = record.get("path")
            if path and record.get("ok") and path not in files:
                files.append(str(path))
            command = record.get("command")
            if command:
                if command not in commands:
                    commands.append(command)
                exit_codes.append(0 if record.get("exit_ok") else 1)
                if "pytest" in command and not verification:
                    tail = str(record.get("summary") or "")
                    verification = f"`{command}`" + (f" -> {tail}" if tail else "")
        if not verification:
            for message in reversed(self.messages):
                if message.get("role") == "assistant" and message.get("content"):
                    first = str(message["content"]).strip().splitlines()[0] if message["content"] else ""
                    verification = first[:200]
                    break
        note = " ".join((final_text or "").split())[:200]
        return {
            "goal": self.task_goal,
            "files": files,
            "commands": commands,
            "exit_codes": exit_codes,
            "verification": verification,
            "note": note,
        }

    def _sync_docs(self, *, goal: str, status: str, read_only: bool) -> None:
        """Refreshes agent.md/changelog.md around a task. Never raises."""
        try:
            from src.project_docs import ensure_project_docs

            ensure_project_docs(
                self.workspace_root,
                goal=goal,
                status=status,
                read_only=read_only,
            )
        except Exception:
            pass

    def _flush_changelog(self, final_text: str) -> None:
        try:
            from src.project_docs import append_changelog_entry

            append_changelog_entry(self.workspace_root, self.changelog_entry(final_text))
        except Exception:
            pass

    def _finish_task(self, result_text: str, effective_mode: AgentMode) -> str:
        """Persists the task outcome into the project docs, then returns the result."""
        if effective_mode is AgentMode.NORMAL and not self.interrupted:
            failed = result_text.startswith("HATA:")
            self._flush_changelog(result_text)
            self._sync_docs(
                goal=self.task_goal,
                status="blokaj" if failed else "tamam",
                read_only=False,
            )
        return result_text

    def _compact_pasted_text(self, text: str) -> str:
        """Shrinks an oversized (already secret-masked) paste; see src/paste.py."""
        compact, saved = compact_paste(text, self.workspace_root)
        if saved is not None:
            print_info(paste_info(text, saved))
        return compact

    def _include_ssh_tools(self) -> bool:
        """SSH tools are sent only when hosts are registered; decided once per session."""
        cached = getattr(self, "_ssh_tools_cached", None)
        if cached is None:
            try:
                from src.ssh_hosts import load_hosts

                cached = bool(load_hosts(self.workspace_root))
            except Exception:
                cached = True
            self._ssh_tools_cached = cached
        return cached

    def run_step(
        self, user_input: str, max_steps: int | None = None, *, route: bool = True,
        compact: bool = False,
    ) -> str:
        """
        Executes an agentic goal loop.
        The model can inspect files, produce code diffs, run tests, and iterate
        until the task is fully accomplished or max_steps is reached.

        `max_steps` defaults to `EVREN_MAX_STEPS` (30): every step re-sends the
        whole system prompt and tool schemas, so a lower ceiling directly cuts the
        base cost. Raise it with `--max-steps`, `/steps <n>` or the env var.

        With `route=True` a large brief in normal mode is first planned by the
        planner model (read-only, few steps) and then executed by the executor
        model with thinking off; plan mode runs on the planner model. `/continue`
        and follow-ups pass `route=False` and keep the last executor profile.

        `compact=True` marks text the user typed or pasted (not a skill or
        template prompt): above the paste limits it is saved to
        `.evren/tmp/paste-N.txt` and only a compact form joins the history.
        """
        agent_def = self._active_agent_def()
        if max_steps is None and self.max_steps is None and agent_def and agent_def.max_steps:
            max_steps = agent_def.max_steps
        explicit_steps = max_steps is not None or self.max_steps is not None
        if max_steps is None:
            max_steps = self.max_steps or default_max_steps()
        if max_steps < 1:
            raise ValueError("max_steps en az 1 olmalıdır.")

        # Per-turn mode override via "ask:" / "plan:" prefix.
        turn_mode, cleaned_input = parse_mode_prefix(user_input)
        # Secrets are swapped for placeholders before anything (model, docs,
        # changelog, checkpoints) sees the task text.
        session_context.set_current(self.context)
        set_active_vault(self.vault)
        cleaned_input = self.vault.capture(cleaned_input)
        if compact:
            # After capture: the saved paste file then holds placeholders only.
            cleaned_input = self._compact_pasted_text(cleaned_input)
        effective_mode = turn_mode or self.mode
        self.pending_mode = None
        self.interrupted = False
        self.task_goal = cleaned_input
        self._compaction_notified = False
        self._reset_trace()
        self._task_start = time.perf_counter()
        self._task_usage_base = int(self.usage_totals.get("total_tokens") or 0)
        # Read-only modes keep the docs contract: read, never write.
        self._sync_docs(
            goal=cleaned_input,
            status="çalışıyor",
            read_only=effective_mode is not AgentMode.NORMAL,
        )

        # Inject active context files if any
        prompt_with_context = cleaned_input
        if self.active_context_files:
            from src.project_scanner import build_context_from_files
            ctx_str = build_context_from_files(list(self.active_context_files), self.workspace_root)
            prompt_with_context = f"{cleaned_input}\n\n[Aktif Dosya Bağlamı]:\n{ctx_str}"
        brief_for_routing = prompt_with_context
        sent_before = "".join(
            str(m.get("content") or "") for m in self.messages if m.get("role") == "user"
        )
        self._listing_done = False
        self._reset_repeat_guard()
        # Outputs of earlier tasks in this session are re-sent on every step of
        # the new one; keep only their head and tail (a REPL session reached 270
        # messages of old grep output and ~70k prompt tokens per step).
        self._trim_previous_tool_outputs()
        self._collapse_previous_task()
        self._reads = {}
        for notes in (
            domain_hints(cleaned_input),
            ssh_target_note(cleaned_input, self.workspace_root),
            self.vault.usage_note(),
        ):
            for block in (notes.split("\n\n") if notes else []):
                if block and block not in sent_before:
                    prompt_with_context = f"{prompt_with_context}\n\n{block}"

        self.messages.append({"role": "user", "content": prompt_with_context})

        step_count = 0
        stalled_steps = 0
        text_continuations = 0

        session_model = self.model
        if agent_def and agent_def.model:
            # Restored in `finally` like routing's borrowed models.
            self.model = agent_def.model
            self._sync_window()
        self._trace_task_begin(cleaned_input, effective_mode)
        from src.file_ops import reset_task_changes
        reset_task_changes()
        outcome: str | None = None  # stays None when an exception escapes
        try:
            # Size is judged on what the user wrote (plus attached files), not on
            # the domain/target notes EVREN appended, which made short briefs
            # like "pod durumları için öneri ver" look "large" and get planned.
            self._prepare_phase(
                route, effective_mode, prompt_with_context, brief_text=brief_for_routing,
            )
            if not explicit_steps:
                max_steps = self._planned_step_budget(max_steps)
            outcome = self._finish_task(
                self._run_steps(
                    max_steps, effective_mode, step_count, stalled_steps, text_continuations,
                ),
                effective_mode,
            )
            return outcome
        except KeyboardInterrupt:
            self.note_interrupt(effective_mode)
            print_warning(
                "İşlem Ctrl+C ile durduruldu. Geçmiş duruyor; düzeltme yazarak devam edebilirsiniz."
            )
            outcome = "İPTAL: Kullanıcı Ctrl+C ile durdurdu. Geçmiş korunuyor."
            return outcome
        finally:
            # The session model (/model) is what the user chose; routing only
            # borrows other models for the duration of one task.
            self.model = session_model
            self._thinking = None
            self._trace_task_end(outcome)

    # ------------------------------------------------------------------ #
    # Planner / executor routing
    # ------------------------------------------------------------------ #

    def _should_route(self, text: str) -> bool:
        if not auto_route_enabled():
            return False
        if planner_model() == executor_model(self.model):
            return False
        # An explicit "planla / plan yap" asks for planning however short the
        # brief is; size alone sent such multi-file features straight to the
        # executor, which spent 15 of 20 steps exploring.
        from src.intent import clef_label

        if _PLAN_INTENT_RE.search(text or ""):
            return True
        return self.token_manager.estimate_text(text) >= route_min_tokens()

    def _prepare_phase(
        self, route: bool, mode: AgentMode, task_text: str, *, brief_text: str | None = None,
    ) -> None:
        """Chooses model and thinking for this task; may run the planning phase."""
        brief_text = task_text if brief_text is None else brief_text
        if not route:
            if self._exec_profile is not None:
                self.model, self._thinking = self._exec_profile
            elif mode is AgentMode.NORMAL and not executor_thinking():
                self._thinking = False
            return
        self._exec_profile = None
        if mode is AgentMode.PLAN:
            if auto_route_enabled():
                self.model = planner_model()
            return
        if mode is not AgentMode.NORMAL:
            return
        # Acting needs no hidden chain-of-thought: tool calls and edits come out
        # directly, which is the main speed and token win on reasoning models.
        if not executor_thinking():
            self._thinking = False
        if not self._should_route(brief_text):
            return
        planner = planner_model()
        executor = executor_model(self.model)
        print_info(
            f"Büyük brief (~{self.token_manager.estimate_text(brief_text)} token): "
            f"plan için {planner} modeline geçiliyor."
        )
        plan = self._plan_phase(task_text, planner)
        self.model = executor
        if not plan:
            print_warning(f"Plan çıkarılamadı; doğrudan {executor} ile uygulanıyor.")
            return
        self._exec_profile = (executor, self._thinking)
        # The plan joins the task message so it survives into the executor's
        # history (and /continue) without a separate turn.
        self.messages[-1] = {
            **self.messages[-1],
            "content": f"{self.messages[-1]['content']}\n\n{execution_brief(plan, planner)}" + (
                "\n\nPLANLAYICININ OKUDUĞU DOSYALAR (güncel; yeniden okuma, doğrudan düzenle):\n"
                + self._planner_reads if getattr(self, "_planner_reads", "") else ""
            ),
        }
        print_info(f"Plan hazır; uygulama için {executor} modeline geçiliyor.")

    # ------------------------------------------------------------------ #
    # Sub-agent delegation (Task-style)
    # ------------------------------------------------------------------ #

    _DELEGATE_DEFAULT_STEPS = 6
    _DELEGATE_MAX_STEPS = 12

    _delegate_tools: "set[str] | None" = None

    def _filter_delegate_tools(self, schemas: list) -> list:
        """Restricts advertised tools while a sub-agent with a tool list runs."""
        allowed = self._delegate_tools
        if allowed is None:
            return schemas
        return [t for t in schemas
                if t.get("function", {}).get("name") in allowed
                and t.get("function", {}).get("name") != "delegate"]

    def _run_delegate(self, task: str, max_steps: int | None = None,
                      agent: str | None = None) -> str:
        """Runs an independent research sub-task in a clean, read-only session.

        Reuses the existing `_run_steps` loop (same pattern as `_plan_phase`):
        `self.messages` is temporarily swapped for a fresh system+task pair and
        the loop runs under `AgentMode.ASK`, so the sub-agent only has read-only
        tools; `delegate` itself is hidden from ASK schemas and denied by the
        gateway, so it cannot recurse. The parent history is restored in
        `finally`; only the sub-agent's final summary text returns to the
        parent, as the tool result.
        """
        task = (task or "").strip()
        if not task:
            return "HATA: delegate için 'task' boş olamaz."
        try:
            steps = int(max_steps) if max_steps not in (None, "") else self._DELEGATE_DEFAULT_STEPS
        except (TypeError, ValueError):
            steps = self._DELEGATE_DEFAULT_STEPS
        # Optional agent definition (9.2/9.3); "researcher" = legacy read-only.
        agent_name = (agent or "").strip() or "researcher"
        adef = None
        if agent_name != "researcher":
            try:
                from src.agent_library import get_agent

                adef = get_agent(agent_name, self.workspace_root)
            except Exception:
                adef = None
            if adef is None:
                return f"HATA: delegate için '{agent_name}' adlı alt ajan bulunamadı."
            if max_steps in (None, "") and getattr(adef, "max_steps", None):
                steps = int(adef.max_steps)
        steps = max(1, min(self._DELEGATE_MAX_STEPS, steps))
        allowed_tools = None
        if adef is not None and adef.tools is not None:
            # def.tools ∩ tools the parent's ASK-level sub-run permits; never delegate.
            allowed_tools = {t for t in adef.tools if t != "delegate"}
        instruction = _DELEGATE_INSTRUCTION.format(task=task)
        if adef is not None and getattr(adef, "prompt", ""):
            instruction = f"{adef.prompt}\n\n{instruction}"

        saved = (self.messages, self.model, self._thinking, self.pending_mode,
                 self.task_goal, self._compaction_notified, self._listing_done)
        self.messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": instruction},
        ]
        if adef is not None and getattr(adef, "model", None):
            self.model = adef.model
        if adef is not None and getattr(adef, "thinking", None) is not None:
            self._thinking = adef.thinking
        self._delegate_tools = allowed_tools
        print_info(f"Alt ajan {agent_name} ({steps} adım, salt-okuma): {task[:100]}")
        try:
            result = self._run_steps(steps, AgentMode.ASK, 0, 0, 0)
        finally:
            self._delegate_tools = None
            (self.messages, self.model, self._thinking, self.pending_mode,
             self.task_goal, self._compaction_notified, self._listing_done) = saved
        text = (result or "").strip()
        if not text:
            return "ALT AJAN ÖZETİ: (boş yanıt)"
        return f"ALT AJAN ÖZETİ:\n{text}"

    def _planned_step_budget(self, max_steps: int) -> int:
        """Grows the executor's step ceiling with the size of the plan.

        A 10-item feature plan ran out of the default 20 steps half-way (code
        written, cleanup and tests missing). Roughly 3 steps per plan item plus
        a margin, capped at 40; never lower than the default.
        """
        if self._exec_profile is None or not self.messages:
            return max_steps
        content = str(self.messages[-1].get("content") or "")
        marker = content.find("UYGULAMA PLANI")
        if marker < 0:
            return max_steps
        items = len(re.findall(r"(?m)^\s*\d{1,2}[.)]\s", content[marker:]))
        if not items:
            return max_steps
        return max(max_steps, min(40, 3 * items + 5))

    def _plan_phase(self, task_text: str, planner: str) -> str | None:
        """Runs a short read-only loop on the planner model; returns the plan text."""
        self._planner_reads = ""
        saved_messages, saved_model, saved_thinking = self.messages, self.model, self._thinking
        self.messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": f"{PLANNER_INSTRUCTION}\n\nGÖREV:\n{task_text}"},
        ]
        self.model = planner
        self._thinking = None
        planner_messages = self.messages
        try:
            result = self._run_steps(planner_max_steps(), AgentMode.ASK, 0, 0, 0)
        finally:
            self.messages, self.model, self._thinking = saved_messages, saved_model, saved_thinking
            self.pending_mode = None
        text = (result or "").strip()
        if not text or text.startswith(("HATA:", "Hata:", "İPTAL")):
            return None
        self._plan_to_todos(text)
        self._planner_reads = self._collect_planner_reads(planner_messages)
        return text

    @staticmethod
    def _collect_planner_reads(messages: list[dict], budget: int = 40_000) -> str:
        """view_file outputs the planner already saw, for the executor.

        A real run: the planner read four test files (140 s), then the executor
        spent its first 7 steps reading the same files again before editing.
        """
        names = {}
        for m in messages:
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {} if isinstance(tc, dict) else {}
                if fn.get("name") == "view_file":
                    names[tc.get("id")] = True
        parts, used = [], 0
        for m in messages:
            if m.get("role") != "tool" or m.get("tool_call_id") not in names:
                continue
            content = str(m.get("content") or "")
            if not content or content.startswith(("HATA", "BİLGİ")) or used + len(content) > budget:
                continue
            parts.append(content)
            used += len(content)
        return "\n\n".join(parts)

    @staticmethod
    def _plan_to_todos(plan: str) -> None:
        """Loads the planner's numbered steps into the todo store (plan 3.4)."""
        try:
            from src.todos import get_store

            steps = []
            for line in plan.splitlines():
                m = re.match(r"^\s*(?:\*\*)?(\d{1,2})[.)]\s+(.+)$", line)
                if m:
                    steps.append(m.group(2).strip().strip("*").strip()[:200])
            if len(steps) < 2:
                return
            items = [{"content": t, "status": "pending"} for t in steps[:10] if t]
            items[0]["status"] = "in_progress"
            get_store().write(items)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Repeat guard
    # ------------------------------------------------------------------ #

    def _reset_repeat_guard(self) -> None:
        self._call_epochs: dict[str, int] = {}
        self._output_seen: dict[tuple[str, int], int] = {}
        self._write_epoch = 0
        self._loop_blocks = 0
        # Verification gate (plan 3.3): code written, no test/build ran since.
        self._code_written = False
        self._verified_since_write = False
        self._verify_nudged = False
        self._verify_note = ""
        # Progress guard: steps since the last file change while a todo list
        # still has open items (a planned change task that only explores).
        self._idle_steps = 0
        self._idle_epoch = 0

    @staticmethod
    def _call_signature(fn_name: str, args: dict) -> str:
        try:
            return fn_name + ":" + json.dumps(args, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            return fn_name + ":" + repr(sorted(args.items()))

    def _repeat_guard(self, fn_name: str, args: dict) -> str:
        """Refuses an identical call already made since the last write.

        A REPL session ran the same `git grep` 25+ times per turn with the
        same output, burning every step and ~2M tokens. Re-running after an
        edit (tests, a fresh grep) stays allowed: writes bump the epoch.
        """
        if fn_name in _REPEAT_EXEMPT_TOOLS:
            return ""
        signature = self._call_signature(fn_name, args)
        if self._call_epochs.get(signature) == self._write_epoch:
            self._loop_blocks += 1
            return (
                "TEKRAR ENGELLENDİ: Bu çağrı bu görevde aynı argümanlarla zaten çalıştı ve o zamandan beri "
                "hiçbir dosya değişmedi; sonucu yukarıda. Aynı aracı aynı argümanla tekrar çağırma: bulduğun "
                "bilgiyle sıradaki adıma geç (edit_file ile düzenle, testi çalıştır) ya da sonucu bildir."
            )
        self._call_epochs[signature] = self._write_epoch
        return ""

    def _after_tool(self, fn_name: str, args: dict, result: str) -> str:
        """Tracks writes and flags results already seen in this task."""
        if fn_name in ("edit_file", "write_file", "ssh_write_file") and result.startswith("BAŞARILI"):
            self._write_epoch += 1
            if Path(str(args.get("path", ""))).suffix.lower() in _CODE_EXTS:
                self._code_written = True
                self._verified_since_write = False
            return result
        if fn_name == "run_command" and _TEST_CMD_RE.search(str(args.get("command", ""))):
            self._verified_since_write = True
        if fn_name in ("run_command", "ssh_run") and command_may_modify_files(str(args.get("command", ""))):
            # git checkout/pull, installs, rm/mv, redirects...: files may differ now.
            self._write_epoch += 1
            return result
        if fn_name in _REPEAT_EXEMPT_TOOLS or len(result) < 200:
            return result
        key = (fn_name, hash(result))
        seen = self._output_seen.get(key, 0) + 1
        self._output_seen[key] = seen
        if seen >= 2:
            self._loop_blocks += 1
            return result + (
                f"\n[EVREN] Bu çıktının aynısı bu görevde {seen}. kez geldi; aynı bilgiyi yeniden arama, "
                "sıradaki adıma geç."
            )
        return result

    _TASK_NOISE_PREFIXES = ("[EVREN]", "Önceki yanıt token", "[Önceki")

    def _collapse_previous_task(self) -> None:
        """At a task boundary, folds the previous task into one deterministic summary.

        Only when the history is past half of the limit; no API call. Runs once
        per task (an early, deep change) so it does not disturb the cached prefix
        between steps.
        """
        try:
            if not self.compactor.smart:
                return
            used = self.token_manager.estimate_messages(self.messages)
            if used <= self.compaction_limit() // 2:
                return
            start = None
            for i in range(len(self.messages) - 1, 0, -1):
                m = self.messages[i]
                content = m.get("content")
                if (
                    m.get("role") == "user"
                    and isinstance(content, str)
                    and not content.startswith(self._TASK_NOISE_PREFIXES)
                ):
                    start = i
                    break
            if start is None or start <= 1:
                return
            from src.history_compactor import collapse_task

            summary = collapse_task(self.messages[start:])
            self.messages = self.messages[:start] + [{"role": "user", "content": summary}]
        except Exception:
            pass

    def _read_repeat(self, fn_name: str, args: dict) -> str:
        """Answers an unchanged re-read of the same file range without resending it."""
        if fn_name != "view_file":
            return ""
        key = self._read_key(args)
        record = self._reads.get(key) if key else None
        if not record:
            return ""
        digest = self._file_digest(str(args.get("path", "")))
        if digest is None or digest != record["digest"]:
            return ""
        for m in self.messages:
            if m.get("role") == "tool" and m.get("tool_call_id") == record["call_id"]:
                content = m.get("content")
                if isinstance(content, str) and not content.startswith(("[özet]", "BİLGİ:")) \
                        and "[araç çıktısı bağlam için kırpıldı]" not in content:
                    rng = self._range_text(args)
                    return (
                        f"BİLGİ: {args.get('path')} {rng} adım {record['step']}'te okundu ve o zamandan beri "
                        "değişmedi; yukarıdaki çıktıyı kullan."
                    )
        return ""

    @staticmethod
    def _read_key(args: dict) -> tuple | None:
        path = args.get("path")
        if not isinstance(path, str) or not path:
            return None
        return (path, args.get("start_line"), args.get("end_line"))

    @staticmethod
    def _range_text(args: dict) -> str:
        start, end = args.get("start_line"), args.get("end_line")
        if start is None and end is None:
            return "(ilk 250 satır)"
        return f"{start or 1}-{end or 'son'}"

    def _file_digest(self, path: str) -> str | None:
        import hashlib

        try:
            target = (Path(self.workspace_root) / path).resolve()
            return hashlib.sha1(target.read_bytes()).hexdigest()
        except Exception:
            return None

    def _note_read(self, fn_name: str, args: dict, result: str, call_id: str) -> None:
        if fn_name != "view_file" or result.startswith(("HATA", "BİLGİ:", "TEKRAR")):
            return
        key = self._read_key(args)
        digest = self._file_digest(str(args.get("path", ""))) if key else None
        if key and digest:
            self._reads[key] = {"digest": digest, "step": self._step_no, "call_id": call_id}

    def _trim_previous_tool_outputs(self) -> None:
        from src.history_compactor import _truncate_tool_content

        for message in self.messages:
            if message.get("role") == "tool" and isinstance(message.get("content"), str):
                message["content"] = _truncate_tool_content(message["content"])

    @staticmethod
    def _step_notes(step_count: int, max_steps: int, stalled_steps: int) -> str:
        """Short per-step reminders; empty on an ordinary step."""
        notes: list[str] = []
        if max_steps >= 8 and max_steps - step_count == 3:
            # Two runs ended with edits done but tests never run: leave room for it.
            notes.append(
                f"[EVREN] ADIM UYARISI: 3 adım kaldı ({step_count}/{max_steps}). Değişiklikleri "
                "toparla ve şimdi doğrulamayı (test/kontrol komutu) çalıştır; yeni keşif başlatma."
            )
        if max_steps > 1 and max_steps - step_count <= 1:
            notes.append(
                f"[EVREN] ADIM UYARISI: adım sınırına geldin ({step_count}/{max_steps}). "
                "Yeni araç turu açma; sonucu, kalan işi ve tek sonraki adımı kısa bildir."
            )
        if stalled_steps:
            notes.append(
                "[EVREN] Önceki yanıt kesildi veya boş kaldı. Uzun düşünmeyi tekrarlama; "
                "gerekli aracı hemen tam argümanlarla çağır veya engeli kısa bildir."
            )
        return "\n".join(notes)

    def _print_task_summary(self, steps: int) -> None:
        """One-line end-of-task summary (steps • seconds • task tokens).

        Never raises: a cosmetic summary must not break the agent loop.
        """
        try:
            start = getattr(self, "_task_start", None)
            if start is None:
                return
            self._task_start = None  # print once per task
            seconds = time.perf_counter() - start
            tokens = int(self.usage_totals.get("total_tokens") or 0) - int(
                getattr(self, "_task_usage_base", 0) or 0
            )
            print_run_summary(steps, seconds, max(0, tokens))
        except Exception:
            pass

    _PARALLEL_SAFE = {"view_file", "find_files", "list_directory", "web_fetch", "ssh_read_file", "search_code"}

    def _prefetch_readonly(self, tool_calls) -> dict:
        """4.1: runs a turn's calls in parallel when ALL are read-only (max 4 workers)."""
        if len(tool_calls) < 2 or any(tc.function.name not in self._PARALLEL_SAFE for tc in tool_calls):
            return {}
        from concurrent.futures import ThreadPoolExecutor

        jobs = []
        for tc in tool_calls:
            raw = tc.function.arguments
            try:
                args = raw if isinstance(raw, dict) else json.loads(raw or "{}")
            except (json.JSONDecodeError, TypeError):
                return {}
            if not isinstance(args, dict):
                return {}
            jobs.append((tc.id, tc.function.name, args))

        def _one(job):
            _id, name, args = job
            try:
                return _id, execute_tool_call(
                    name=name, arguments=args, workspace_root=self.workspace_root,
                    auto_approve=self.auto_approve,
                )
            except Exception as e:  # fall back to the sequential path
                return _id, None

        with ThreadPoolExecutor(max_workers=4) as pool:
            return {i: r for i, r in pool.map(_one, jobs) if r is not None}

    def _prepare_request(self, step_count: int, stalled_steps: int, text_continuations: int) -> int:
        """Compact history before a request; return the output token budget."""
        self._step_no = step_count
        self._sync_window()
        output_budget = self.token_manager.resolve_output_budget(
            self.model, EVREN_DEFAULT_MAX_TOKENS
        )
        repeats = max(stalled_steps, text_continuations)
        if repeats:
            output_budget = min(output_budget * (2 ** repeats), _stall_token_cap())
        # Compact early: waiting until the window is completely full makes every
        # step re-send the whole history, so cost grows quadratically. Compacting
        # at a fraction of the window keeps the repeated prefix small.
        compact_limit = self.compaction_limit()
        if repeats >= 2:
            # A second stall usually means the context itself is the problem.
            # Shrinking it first is cheaper than paying for a bigger answer
            # budget on an oversized prompt.
            compact_limit = max(1024, compact_limit // 2)
        before = len(self.messages)
        self.messages = self.compactor.compact(
            self.messages, output_budget, max_tokens=compact_limit
        )
        if len(self.messages) < before and not self._compaction_notified:
            self._compaction_notified = True
            print_info(
                f"Bağlam {compact_limit:,} token bütçesini aştı, sıkıştırıldı "
                f"({before} mesaj -> {len(self.messages)}). "
                "Ayar: EVREN_CONTEXT_BUDGET, EVREN_SMART_COMPACT=false (eski davranış)."
            )
        return output_budget

    def _turn_notes(self, step_count: int, max_steps: int, stalled_steps: int) -> str:
        """Transient notes for the trailing user message of this request."""
        step_notes = self._step_notes(step_count, max_steps, stalled_steps)
        if self._loop_blocks >= _LOOP_BLOCK_LIMIT:
            step_notes = (step_notes + "\n" if step_notes else "") + (
                "[EVREN] DÖNGÜ: aynı araç çağrılarını tekrarlıyorsun. Bu tur araç kullanma; "
                "şimdiye kadarki bulgularla yapılanı, kalan işi ve tek somut sonraki adımı yaz."
            )
        if self._listing_done:
            step_notes = (step_notes + "\n" if step_notes else "") + (
                "[EVREN] Liste kullanıcının terminalinde tam gösterildi. Araç çağırma; "
                "tabloyu yeniden yazmadan 1-3 cümlelik kısa özetle bitir."
            )
        if self._verify_note:
            step_notes = (step_notes + "\n" if step_notes else "") + self._verify_note
            self._verify_note = ""
        idle = self._idle_note()
        if idle:
            step_notes = (step_notes + "\n" if step_notes else "") + idle
        return step_notes

    def _idle_note(self) -> str:
        """Nudges a planned change task that keeps reading/testing without editing.

        A real run spent 10+ steps re-running the same test suite with small
        flag changes; the repeat guard only catches identical calls.
        """
        if getattr(self, "_write_epoch", 0) != getattr(self, "_idle_epoch", 0):
            self._idle_epoch = self._write_epoch
            self._idle_steps = 0
            return ""
        self._idle_steps = getattr(self, "_idle_steps", 0) + 1
        try:
            from src.todos import get_store

            open_items = [t for t in get_store().snapshot() if t["status"] != "completed"]
        except Exception:
            open_items = []
        if not open_items or self._idle_steps not in (4, 7, 10):
            return ""
        nxt = next((t["content"] for t in open_items if t["status"] == "in_progress"), open_items[0]["content"])
        return (
            f"[EVREN] İLERLEME YOK: {self._idle_steps} adımdır hiçbir dosya değişmedi. Keşfi ve aynı "
            f"testi yeniden koşmayı bırak. Şimdi şu maddeyi uygula (edit_file/write_file): {nxt[:160]}. "
            "Uygulayamıyorsan engeli tek cümleyle bildir."
        )

    def _stream_model(
        self,
        step_count: int,
        max_steps: int,
        effective_mode: AgentMode,
        stalled_steps: int,
        output_budget: int,
    ) -> tuple[Any, dict]:
        """Stream one model response; return (response, stream_state)."""
        from src.ui import StatusTracker, HAVE_RICH, console

        status_msg = (
            f"Model düşünüyor... [{self.model}]"
            if step_count == 1
            else f"[Adım {step_count}/{max_steps}] Model analiz ediyor... [{self.model}]"
        )
        request_started = time.perf_counter()
        try:
            estimated = self.token_manager.estimate_messages(self.messages)

            # Stream the response so the terminal never looks frozen: tokens
            # (reasoning + content) appear live, then we get an assembled
            # response object with the full content/tool_calls for the loop.
            tracker = StatusTracker(status_msg)
            tracker.__enter__()
            stream_state = {"first": True, "in_reasoning": False, "in_content": False}

            def _on_reasoning(text: str):
                _check_esc()
                if stream_state["first"]:
                    tracker.stop()
                    stream_state["first"] = False
                if not stream_state["in_reasoning"]:
                    stream_state["in_reasoning"] = True
                    if stream_state["in_content"]:
                        print()
                    if HAVE_RICH and console:
                        console.print("[bold magenta]💭 Düşünme / Akıl Yürütme:[/bold magenta]")
                    else:
                        print("[Düşünme]:")
                print_reasoning(text, is_chunk=True)

            def _on_content(text: str):
                _check_esc()
                if stream_state["first"]:
                    tracker.stop()
                    stream_state["first"] = False
                if stream_state["in_reasoning"]:
                    print("\n")
                    stream_state["in_reasoning"] = False
                stream_state["in_content"] = True
                # Buffer the deltas; the response is rendered once at the end
                # as clean markdown, so raw ** marks never hit the screen.
                print_markdown_stream(text)

            try:
                # Turn notes go into a transient trailing message, never into the
                # system message: the system prefix and the whole history must
                # stay byte-identical between steps so the server's prompt cache
                # keeps hitting. Nothing is sent on an ordinary step.
                step_notes = self._turn_notes(step_count, max_steps, stalled_steps)
                outgoing = list(self.messages)
                if step_notes:
                    outgoing.append({"role": "user", "content": step_notes})
                response = self.client.chat_stream_assembled(
                    messages=normalize_chat_messages(outgoing),
                    model=self.model,
                    max_tokens=output_budget,
                    tools=self._filter_delegate_tools(self._agent_tools(
                        tools_for_mode(effective_mode, include_ssh=self._include_ssh_tools())
                        + (_ext_schemas(self.workspace_root) if effective_mode == AgentMode.NORMAL else [])
                    )),
                    # Same tool list keeps the cached prefix; "none" only forbids calls.
                    tool_choice="none" if (
                        self._listing_done or self._loop_blocks >= _LOOP_BLOCK_LIMIT
                    ) else None,
                    on_reasoning=_on_reasoning,
                    on_content=_on_content,
                    # A stall almost always means the hidden reasoning ate the
                    # budget; retrying with thinking off breaks the loop.
                    thinking=False if stalled_steps else self._thinking,
                )
            finally:
                if stream_state["first"]:
                    tracker.stop()
                if stream_state["in_content"] and markdown_stream_active():
                    flush_markdown()

            # Calibrate the estimator against server-reported usage.
            self.token_manager.calibrate_from_response(estimated, response)
            self._record_usage(response, step=step_count)
            self._trace_request(response, request_started)
        except Exception:
            self._trace_request(None, request_started, error=True)
            raise
        return response, stream_state

    def _tool_call_message(self, message: Any, tool_calls) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": message.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": (
                            tc.function.arguments
                            if isinstance(tc.function.arguments, str)
                            else json.dumps(tc.function.arguments or {}, ensure_ascii=False)
                        ),
                    },
                }
                for tc in tool_calls
            ],
        }

    def _tool_reply(self, tc, fn_name: str, content: str) -> None:
        self.messages.append({
            "role": "tool", "tool_call_id": tc.id, "name": fn_name, "content": content,
        })

    @staticmethod
    def _parse_tool_args(raw_args, fn_name: str) -> dict:
        if isinstance(raw_args, dict):
            args = raw_args
        elif raw_args is None or (isinstance(raw_args, str) and not raw_args.strip()):
            args = {}
        else:
            args = json.loads(raw_args)
        if not isinstance(args, dict):
            raise ValueError(
                f"Araç argümanları bir JSON nesnesi olmalı, gelen tip: "
                f"{type(args).__name__}."
            )
        return args

    def _guard_tool_call(
        self, tc, fn_name: str, args: dict, effective_mode: AgentMode, t_tool: float,
    ) -> tuple[bool, bool]:
        """Run mode/permission/delegate/repeat guards.

        Returns (handled, perm_allow); handled means a tool reply was already added.
        """
        # Mode permission gate: deny disallowed tools with a reason
        # the model can react to (instead of executing them).
        allowed, deny_reason = check_tool_allowed(fn_name, args, effective_mode)
        if not allowed:
            result = f"REDDEDİLDİ: {deny_reason}"
            print_tool_result(fn_name, deny_reason, is_error=True)
            self._trace_tool(fn_name, args, t_tool, result)
            self._tool_reply(tc, fn_name, result)
            return True, False

        # Settings permission rules + pre_tool hooks (src/settings.py).
        perm_allow, perm_msg = self._permission_gate(fn_name, args)
        if perm_msg:
            result = f"REDDEDİLDİ: {perm_msg}"
            print_tool_result(fn_name, perm_msg, is_error=True)
            self._trace_tool(fn_name, args, t_tool, result)
            self._tool_reply(tc, fn_name, result)
            return True, perm_allow

        # Delegation is handled by the session itself: it swaps the
        # message history and reuses _run_steps, so it must never
        # reach the stateless execute_tool_call dispatcher. It sits
        # AFTER the mode gate: the sub-agent runs in ASK mode, where
        # the gate denies `delegate`, which is what stops recursion.
        if fn_name == "delegate":
            result = self._run_delegate(
                str(args.get("task") or ""), args.get("max_steps"),
                args.get("agent"),
            )
            is_err = result.startswith("HATA:")
            summary = (result.splitlines()[0] if result else "Tamamlandı")
            print_tool_result(fn_name, summary, is_error=is_err)
            self._note_tool_call(fn_name, args, result)
            self._trace_tool(fn_name, args, t_tool, result)
            self._tool_reply(tc, fn_name, result)
            return True, perm_allow

        blocked = self._repeat_guard(fn_name, args)
        if blocked:
            print_tool_result(fn_name, "tekrar eden çağrı engellendi", is_error=True)
            self._trace_tool(fn_name, args, t_tool, blocked, blocked=True)
            self._tool_reply(tc, fn_name, blocked)
            return True, perm_allow

        repeated = self._read_repeat(fn_name, args)
        if repeated:
            print_tool_result(fn_name, "değişmemiş dosya yeniden okunmadı")
            self._note_tool_call(fn_name, args, repeated)
            self._trace_tool(fn_name, args, t_tool, repeated)
            self._tool_reply(tc, fn_name, repeated)
            return True, perm_allow
        return False, perm_allow

    def _run_one_tool(
        self, tc, prefetched: dict, effective_mode: AgentMode,
    ) -> AgentMode:
        """Execute a single tool call; return the (possibly switched) mode."""
        fn_name = tc.function.name
        t_tool = time.perf_counter()
        raw_args = tc.function.arguments
        try:
            args = self._parse_tool_args(raw_args, fn_name)
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            result = f"HATA: '{fn_name}' için geçersiz araç argümanları: {e}"
            print_tool_result(fn_name, result, is_error=True)
            self._trace_tool(fn_name, {"raw": raw_args}, t_tool, result)
            self._tool_reply(tc, fn_name, result)
            return effective_mode

        print_tool_call(fn_name, args, auto_approved=self.auto_approve)

        handled, perm_allow = self._guard_tool_call(tc, fn_name, args, effective_mode, t_tool)
        if handled:
            return effective_mode

        # Execute tool call cleanly without spinner wrap (prevents terminal input freeze & diff distortion)
        result = prefetched.get(tc.id)
        if result is None:
            result = execute_tool_call(
                name=fn_name,
                arguments=args,
                workspace_root=self.workspace_root,
                auto_approve=self.auto_approve or perm_allow,
            )
        _settings.set_current(None)
        _settings.run_post_tool(fn_name, args, self.workspace_root)

        # ask_user with a positive confirm in plan mode -> switch to normal.
        if fn_name == "ask_user" and effective_mode == AgentMode.PLAN:
            if result.startswith("Kullanıcı ONAYLADI"):
                self.mode = AgentMode.NORMAL
                effective_mode = AgentMode.NORMAL
                print_info("Kullanıcı onayladı: plan modundan normal moda geçildi.")

        # A file or command output may still echo a known secret.
        result = self.vault.mask(result)
        result = self._after_tool(fn_name, args, result)
        self._note_read(fn_name, args, result, tc.id)
        if "[EVREN] Tam çıktı (" in result and is_listing_task(self.task_goal):
            self._listing_done = True
        is_err = result.startswith("HATA:") or result.startswith("İPTAL:") or result.startswith("REDDEDİLDİ:")
        summary = result.splitlines()[0] if result else "Tamamlandı"
        if fn_name == "todo_write" and not is_err:
            # The checklist is the point of the tool: show every item,
            # not just the "Görev listesi (1/3 tamamlandı):" header.
            summary = result
        print_tool_result(fn_name, summary, is_error=is_err)
        self._note_tool_call(fn_name, args, result)
        self._trace_tool(fn_name, args, t_tool, result)
        self._tool_reply(tc, fn_name, result)
        return effective_mode

    def _classify_response(self, response: Any) -> tuple[str, Any, Any]:
        """Return (kind, message, error) with kind in
        content_filter | truncated | stalled | ok."""
        choice = response.choices[0]
        message = choice.message
        finish_reason = getattr(choice, "finish_reason", None)
        if finish_reason == "content_filter":
            return "content_filter", message, None
        # A length-limited stream can include incomplete tool arguments.
        # Never persist or execute that partial call. A finished tool call or
        # a cut-but-usable answer is progress and must not abort the task.
        content_text = (message.content or "").strip()
        tool_calls_now = getattr(message, "tool_calls", None) or []
        arguments_complete = _tool_arguments_complete(tool_calls_now)
        if finish_reason == "length" and content_text and not tool_calls_now:
            return "truncated", message, None
        stalled = (not arguments_complete) and (
            finish_reason == "length" or (not content_text and not tool_calls_now)
        )
        return ("stalled" if stalled else "ok"), message, None

    def _run_steps(
        self,
        max_steps: int,
        effective_mode: AgentMode,
        step_count: int,
        stalled_steps: int,
        text_continuations: int,
    ) -> str:
        while step_count < max_steps:
            step_count += 1
            output_budget = self._prepare_request(step_count, stalled_steps, text_continuations)
            try:
                response, stream_state = self._stream_model(
                    step_count, max_steps, effective_mode, stalled_steps, output_budget,
                )
            except Exception as e:
                if isinstance(e, EvrenApiError) and e.retryable and stalled_steps + 1 < _MAX_STALLS:
                    stalled_steps += 1
                    print_warning(
                        "Model çağrısı bağlantı veya sunucu hatasıyla kesildi; "
                        f"yeniden deneniyor ({stalled_steps}/{_MAX_STALLS})."
                    )
                    continue
                detail = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
                print_error(f"Model çağrısı sırasında hata oluştu: {detail}")
                return f"Hata: {detail}"

            kind, message, _ = self._classify_response(response)
            if kind == "content_filter":
                error = "HATA: Model yanıtı içerik filtresi nedeniyle durdu; görev tamamlanmadı."
                print_error(error)
                self.messages.append({"role": "assistant", "content": error})
                return error
            if kind == "truncated":
                self.messages.append({"role": "assistant", "content": message.content or ""})
                self.messages.append({
                    "role": "user",
                    "content": (
                        "Önceki yanıt token sınırında kesildi ve tamamlanmış sayılmadı. "
                        "Kaldığın yerden devam et; yazılmış kısmı tekrarlama. "
                        "Tek küçük adım için aracı tam argümanlarla çağır veya kısa sonucu yaz."
                    ),
                })
                stalled_steps = 0
                text_continuations += 1
                print_warning("Yanıt token sınırında kesildi; kaldığı yerden devam ediliyor.")
                continue
            if kind == "stalled":
                stalled_steps += 1
                if stalled_steps >= _MAX_STALLS:
                    error = (
                        "HATA: Model art arda kesilmiş veya boş yanıt verdi; görev tamamlanmadı. "
                        "Bağlam çok büyük olabilir: `/tokens` ile bütçeyi incele, "
                        "`/steps <n>` ile adım tavanını düşür veya yeni bir oturum aç. "
                        f"Çıktı tavanı: EVREN_STALL_TOKEN_CAP={_stall_token_cap()}."
                    )
                    print_error(error)
                    self.messages.append({"role": "assistant", "content": error})
                    return error
                print_warning(
                    "Yanıt kesildi veya boş kaldı; daha geniş token bütçesiyle "
                    "sıradaki küçük adım için yeniden yönlendiriliyor."
                )
                continue
            stalled_steps = 0
            text_continuations = 0

            # Reasoning was already streamed live via the callback; only print
            # it here if the server returned it without streaming deltas.
            reasoning = getattr(message, "reasoning", None) or getattr(message, "reasoning_content", None)
            if reasoning and not stream_state["in_content"] and not stream_state["in_reasoning"]:
                print_reasoning(reasoning)

            tool_calls = getattr(message, "tool_calls", None)
            if tool_calls:
                self.messages.append(self._tool_call_message(message, tool_calls))
                # Text alongside a tool call was already streamed live; only
                # render it here if it never came through as stream deltas.
                if message.content and not stream_state["in_content"]:
                    print_markdown(message.content)
                prefetched = self._prefetch_readonly(tool_calls)
                for tc in tool_calls:
                    effective_mode = self._run_one_tool(tc, prefetched, effective_mode)
                # Loop continues to let the model react to the tool outputs!
                continue

            # No tool calls: model has provided its final response
            final_content = message.content or ""
            self.messages.append({"role": "assistant", "content": final_content})
            if (
                effective_mode == AgentMode.NORMAL
                and self._code_written
                and not self._verified_since_write
                and not self._verify_nudged
                and step_count < max_steps
            ):
                self._verify_nudged = True
                note = verification_note(self.workspace_root)
                if note:
                    self._verify_note = note
                    continue
            # Content was already streamed live; only render markdown if it
            # never arrived as stream deltas (e.g. empty stream).
            if final_content and not stream_state["in_content"]:
                print_markdown(final_content)
            self._print_task_summary(step_count)
            _settings.run_stop(self.workspace_root)
            return final_content

        self.pending_mode = effective_mode
        error = (
            f"HATA: Ajan maksimum adım sınırına ({max_steps}) ulaştı; görev tamamlanmadı. "
            "Geçmiş korunuyor; devam veya /continue ile sürdürebilirsiniz."
        )
        print_warning(error)
        self.messages.append({"role": "assistant", "content": error})
        self._print_task_summary(step_count)
        return error
