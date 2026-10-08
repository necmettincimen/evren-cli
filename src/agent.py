"""
Autonomous Agent Loop for EVREN CLI (Antigravity Architecture).
Executes multi-step coding workflows, calls tools, handles reasoning tokens,
and ensures safe user approvals for Windows development.
"""

import json
from pathlib import Path
from typing import Any

from src.api_client import EvrenClient
from src.tools import TOOLS_SCHEMA, execute_tool_call
from src.config import get_workspace_dir, EVREN_DEFAULT_MAX_TOKENS
from src.token_manager import TokenManager
from src.history_compactor import HistoryCompactor
from src.modes import AgentMode, parse_mode_prefix
from src.prompt_frame import PromptFrame, build_user_content, compose_system
from src.tool_gateway import check_tool_allowed
from src.ui import (
    print_tool_call,
    print_tool_result,
    print_reasoning,
    print_markdown,
    print_info,
    print_warning,
    print_error,
)

SYSTEM_PROMPT = """Sen Antigravity felsefesiyle çalışan, EVREN LLM API destekli uzman bir yazılım mühendisi asistanısın.
Windows ortamında kullanıcının aktif proje çalışma alanında geliştirme, hata ayıklama, refactoring ve test yaparsın.

ZORUNLU NETLEŞTİRME (CLARIFY) ADIMI — HER PROMPT İÇİN İLK ADIM:
0. Her kullanıcı isteğinde, işe başlamadan ÖNCE bir netleştirme adımı çalıştır. İsteği analiz et ve belirsiz/eksik/çok anlamlı noktaları tespit et.
   - Belirsizlik varsa (amaç, kapsam, hedef dosya/modül, beklenen çıktı, riskli işlem, eksik girdi/kısıt) MUTLAKA 'ask_user' aracıyla kullanıcıya sor. Tahmin etme, varsayımda bulunma.
   - Netleştirme tamamlanmadan yazma işlemlerine (edit_file/write_file/run_command) BAŞLAMA.
   - İstek zaten tamamen açık, tek anlamlı ve düşük riskliyse veya kullanıcı açıkça "soru sorma, doğrudan yap" dediyse bu adımı atlayabilirsin; ancak emin olmadığın bir nokta kalırsa yine sor.
   - Netleştirme sonrası anlaşılan işi kısa bir özetle teyit et; gerekirse kind=confirm ile onay al.

TEMEL ÇALIŞMA KURALLARI:
1. İncelemeden değiştirme: Bir dosyayı değiştirmeden önce mutlaka 'view_file' veya 'list_directory' ile dosyanın güncel halini oku.
2. Cerrahi değişiklikler yap: Tüm dosyayı baştan yazmak yerine 'edit_file' aracını kullanarak yalnızca değişmesi gereken kod bloğunu hedefle. Mevcut yorum satırlarını, kod stilini ve girintileri koru.
3. Windows uyumluluğu: Yolları doğru formatta işle, PowerShell/cmd uyumlu komutlar öner.
4. Güvenlik ve yedekleme: Kritik değişikliklerden önce kullanıcıya diff gösterileceğini ve otomatik yedek alınacağını bil.
5. Doğrulama: Kod değişikliklerinden sonra 'run_command' ile ilgili testleri veya sözdizimi kontrollerini çalıştır.

HIZ VE VERİMLİLİK (ÖNEMLİ):
- Önce netleştir (yukarıdaki clarify adımı), sonra uzun uzun düşünmeden doğrudan harekete geç ve KOD YAZ. Gereksiz açıklama, tekrar veya uzun analiz yapma.
- Her turda mümkün olduğunca ÇOK iş bitir: birden çok dosyayı tek turda oku/düzenle, ilgisiz araç çağrılarını birleştir.
- Aynı dosyayı tekrar tekrar okuma; bir kez oku, sonra düzenle. Gereksiz doğrulama turlarından kaçın.
- Kısa ve öz konuş; asıl çıktın çalışan koddur. Açıklamayı en fazla birkaç cümleyle sınırla.
- Bir görev tamamlandığında ekstra iş uydurma; sonucu özetle ve bitir.

ÇALIŞMA KİPLERİ:
- normal: Tüm araçlar açık (oku + yaz + çalıştır).
- ask: Salt-okuma. 'write_file'/'edit_file' kapalı; 'run_command' yalnızca salt-okuma komutlarıyla sınırlı.
- plan: Kaynak dosyalara dokunulmaz. Yalnızca 'create_plan' ile plans/<ad>/plan.md yazılır.
Bir araç reddedilirse ("REDDEDİLDİ: ..."), kip kısıtlaması nedeniyle engellendiğini anla ve kullanıcıdan normal moda geçmesini iste.

UZAK SUNUCU (SSH) İŞLEMLERİ:
- Yalnızca 'ssh_list_hosts' ile listelenen kayıtlı alias'lara bağlanabilirsin; rastgele host/IP kullanma.
- Önce 'ssh_list_hosts' ile mevcut host'ları gör, sonra 'ssh_read_file' veya 'ssh_run' ile durum tespiti yap.
- 'ssh_run' ask/plan modunda yalnızca salt-okuma komutlarıyla sınırlıdır; yazma gerektiren işler için normal mod gerekir.
- 'ssh_write_file' yalnızca host'ta allow_write=true ise çalışır ve kullanıcı onayı ister; üretim sunucularında önce salt-okuma ile doğrula.
- Tüm uzak işlemler .evren/logs/ssh_audit.log dosyasına kaydedilir.
"""


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
        self.system_prompt = self._compose_system(self.base_system_prompt)
        self.frame = PromptFrame()
        self.auto_approve = auto_approve if auto_approve is not None else EVREN_AUTO_APPROVE
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt}
        ]
        self.active_context_files: set[str] = set()

        # Token management: estimate + calibrate, and compact history when needed.
        self.token_manager = TokenManager()
        self.compactor = HistoryCompactor(self.token_manager)
        self.last_usage: dict[str, Any] | None = None

        # Working mode (normal / ask / plan). Persisted across turns; can be
        # overridden per-turn via an "ask:" / "plan:" message prefix.
        self.mode: AgentMode = AgentMode.NORMAL

    def _compose_system(self, base: str) -> str:
        """Appends persistent memory and language-mode blocks to a base prompt."""
        blocks: list[str] = []
        try:
            from src.memory import memory_context_block
            mem = memory_context_block(self.workspace_root)
            if mem:
                blocks.append(mem)
        except Exception:
            pass
        try:
            from src.language import language_context_block
            lang = language_context_block(self.workspace_root)
            if lang:
                blocks.append(lang)
        except Exception:
            pass
        if not blocks:
            return base
        return base.rstrip() + "\n\n" + "\n\n".join(blocks)

    def refresh_context(self):
        """Rebuilds the system prompt from base + memory + language blocks."""
        self.system_prompt = self._compose_system(self.base_system_prompt)
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0]["content"] = self.system_prompt

    def set_model(self, new_model: str):
        self.model = new_model

    def set_mode(self, mode: AgentMode):
        self.mode = mode

    def set_auto_approve(self, auto_approve: bool):
        self.auto_approve = auto_approve

    def add_context_file(self, rel_path: str):
        self.active_context_files.add(rel_path)

    def remove_context_file(self, rel_path: str):
        self.active_context_files.discard(rel_path)

    def clear_history(self):
        self.frame = PromptFrame()
        self.system_prompt = self._compose_system(self.base_system_prompt)
        self.messages = [{"role": "system", "content": self.system_prompt}]
        self.active_context_files.clear()
        self.last_usage = None

    def apply_frame(self, frame: PromptFrame, extra: str = "") -> str:
        """Merges Rol/Çerçeve into the system message and returns the user text.

        Identity fields persist across turns until /clear. Blank fields on this
        turn do not erase a previously set Rol or Çerçeve. Brief, Kısıt, and
        Çıktı belong only to this turn.
        """
        if frame.rol:
            self.frame.rol = frame.rol
        if frame.cerceve:
            self.frame.cerceve = frame.cerceve
        self.system_prompt = compose_system(self._compose_system(self.base_system_prompt), self.frame)
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0]["content"] = self.system_prompt
        return build_user_content(frame, extra=extra)

    def apply_skill(self, name: str, context: str = "") -> str:
        """Returns the user content for applying a catalog skill."""
        from src.skills import get_skill

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

    def _record_usage(self, response: Any) -> None:
        """Stores the last server-reported usage for the /tokens report."""
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        self.last_usage = {
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        }

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
            "remaining_context": self.token_manager.remaining_context(self.messages, output_budget),
            "calibration": round(self.token_manager.calibration, 3),
            "last_usage": self.last_usage,
        }

    def run_step(self, user_input: str, max_steps: int = 30) -> str:
        """
        Executes an agentic goal loop.
        The model can inspect files, produce code diffs, run tests, and iterate
        until the task is fully accomplished or max_steps is reached.
        """
        # Per-turn mode override via "ask:" / "plan:" prefix.
        turn_mode, cleaned_input = parse_mode_prefix(user_input)
        effective_mode = turn_mode or self.mode

        # Inject active context files if any
        prompt_with_context = cleaned_input
        if self.active_context_files:
            from src.project_scanner import build_context_from_files
            ctx_str = build_context_from_files(list(self.active_context_files), self.workspace_root)
            prompt_with_context = f"{cleaned_input}\n\n[Aktif Dosya Bağlamı]:\n{ctx_str}"

        self.messages.append({"role": "user", "content": prompt_with_context})

        step_count = 0
        final_answer = ""

        while step_count < max_steps:
            step_count += 1

            # Compact history before each request so the context budget is
            # never exceeded (truncate tools -> drop middle -> system+last).
            output_budget = self.token_manager.resolve_output_budget(
                self.model, EVREN_DEFAULT_MAX_TOKENS
            )
            self.messages = self.compactor.compact(self.messages, output_budget)

            status_msg = (
                f"Model düşünüyor... [{self.model}]"
                if step_count == 1
                else f"[Adım {step_count}/{max_steps}] Model analiz ediyor... [{self.model}]"
            )

            try:
                from src.ui import StatusTracker, HAVE_RICH, console
                estimated = self.token_manager.estimate_messages(self.messages)

                # Stream the response so the terminal never looks frozen: tokens
                # (reasoning + content) appear live, then we get an assembled
                # response object with the full content/tool_calls for the loop.
                tracker = StatusTracker(status_msg)
                tracker.__enter__()
                stream_state = {"first": True, "in_reasoning": False, "in_content": False}

                def _on_reasoning(text: str):
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
                    if stream_state["first"]:
                        tracker.stop()
                        stream_state["first"] = False
                    if stream_state["in_reasoning"]:
                        print("\n")
                        stream_state["in_reasoning"] = False
                    stream_state["in_content"] = True
                    print(text, end="", flush=True)

                try:
                    response = self.client.chat_stream_assembled(
                        messages=self.messages,
                        model=self.model,
                        tools=TOOLS_SCHEMA,
                        on_reasoning=_on_reasoning,
                        on_content=_on_content,
                    )
                finally:
                    if stream_state["first"]:
                        tracker.stop()
                    if stream_state["in_content"]:
                        print()  # finish the streamed line

                # Calibrate the estimator against server-reported usage.
                self.token_manager.calibrate_from_response(estimated, response)
                self._record_usage(response)
            except Exception as e:
                print_error(f"Model çağrısı sırasında hata oluştu: {e}")
                return f"Hata: {e}"

            choice = response.choices[0]
            message = choice.message

            # Reasoning was already streamed live via the callback; only print
            # it here if the server returned it without streaming deltas.
            reasoning = getattr(message, "reasoning", None) or getattr(message, "reasoning_content", None)
            if reasoning and not stream_state["in_content"] and not stream_state["in_reasoning"]:
                print_reasoning(reasoning)

            # Check if there are tool calls
            tool_calls = getattr(message, "tool_calls", None)

            if tool_calls:
                # Append assistant message with tool calls
                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": message.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in tool_calls
                    ],
                }
                self.messages.append(assistant_msg)

                # Text alongside a tool call was already streamed live; only
                # render it here if it never came through as stream deltas.
                if message.content and not stream_state["in_content"]:
                    print_markdown(message.content)

                # Execute each tool call
                for tc in tool_calls:
                    fn_name = tc.function.name
                    try:
                        args = json.loads(tc.function.arguments)
                    except Exception:
                        args = {}

                    print_tool_call(fn_name, args)

                    # Mode permission gate: deny disallowed tools with a reason
                    # the model can react to (instead of executing them).
                    allowed, deny_reason = check_tool_allowed(fn_name, args, effective_mode)
                    if not allowed:
                        result = f"REDDEDİLDİ: {deny_reason}"
                        print_tool_result(fn_name, deny_reason, is_error=True)
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "name": fn_name,
                            "content": result,
                        })
                        continue

                    # Execute tool call cleanly without spinner wrap (prevents terminal input freeze & diff distortion)
                    result = execute_tool_call(
                        name=fn_name,
                        arguments=args,
                        workspace_root=self.workspace_root,
                        auto_approve=self.auto_approve,
                    )

                    # ask_user with a positive confirm in plan mode -> switch to normal.
                    if fn_name == "ask_user" and effective_mode == AgentMode.PLAN:
                        if result.startswith("Kullanıcı ONAYLADI"):
                            self.mode = AgentMode.NORMAL
                            effective_mode = AgentMode.NORMAL
                            print_info("Kullanıcı onayladı: plan modundan normal moda geçildi.")

                    is_err = result.startswith("HATA:") or result.startswith("İPTAL:") or result.startswith("REDDEDİLDİ:")
                    summary = result.splitlines()[0] if result else "Tamamlandı"
                    print_tool_result(fn_name, summary, is_error=is_err)

                    # Add tool response to messages
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": fn_name,
                        "content": result,
                    })

                # Loop continues to let the model react to the tool outputs!
                continue

            # No tool calls: model has provided its final response
            final_content = message.content or ""
            self.messages.append({"role": "assistant", "content": final_content})
            final_answer = final_content
            # Content was already streamed live; only render markdown if it
            # never arrived as stream deltas (e.g. empty stream).
            if final_content and not stream_state["in_content"]:
                print_markdown(final_content)
            break

        if step_count >= max_steps:
            print_warning(f"Ajan maksimum adım sınırına ({max_steps}) ulaştı.")

        return final_answer
