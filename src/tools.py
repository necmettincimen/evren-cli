"""
Agent tool definitions and executor for EVREN CLI.
Provides OpenAI-compatible function schemas and execution logic for
file reading, writing, surgical editing, directory inspection, and command running.
"""

from pathlib import Path
from typing import Any

from src.config import get_workspace_dir, EVREN_AUTO_APPROVE
from src.file_ops import (
    safe_read_file,
    safe_write_file,
    read_file_with_meta,
    resolve_workspace_path,
    is_safe_path,
)
from src.diff_viewer import generate_unified_diff, apply_replacement, apply_edits
from src.proc_utils import run_with_tree_kill, stream_with_tree_kill
from src.ui import (
    print_diff,
    prompt_confirm,
    print_warning,
    print_info,
    print_command_output,
)

# OpenAI Function Calling Tools Specification
TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "view_file",
            "description": "Çalışma alanındaki bir dosyanın içeriğini satır numaralarıyla birlikte okur.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Okunacak dosyanın göreceli yolu (örn: src/main.py)",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "Başlangıç satır numarası (1-indeksli, isteğe bağlı)",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Bitiş satır numarası (isteğe bağlı)",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Dosya içindeki belirli bir kod bloğunu (veya birden çok bloğu) yeni kodla cerrahi olarak değiştirir. Değişiklik öncesi diff gösterilir ve yedek alınır. CRLF/BOM korunur.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Düzenlenecek dosyanın göreceli yolu (örn: src/api_client.py)",
                    },
                    "target_snippet": {
                        "type": "string",
                        "description": "Dosya içinden birebir eşleşmesi gereken eski kod parçası (tek edit için)",
                    },
                    "replacement_snippet": {
                        "type": "string",
                        "description": "Eski kod parçasının yerine geçecek yeni kod (tek edit için)",
                    },
                    "all": {
                        "type": "boolean",
                        "description": "true ise target_snippet'in TÜM eşleşmelerini değiştirir (varsayılan: false).",
                        "default": False,
                    },
                    "edits": {
                        "type": "array",
                        "description": "Çoklu düzenleme listesi. Her öğe: {target_snippet, replacement_snippet, all?}. Verilirse target_snippet/replacement_snippet yok sayılır.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "target_snippet": {"type": "string"},
                                "replacement_snippet": {"type": "string"},
                                "all": {"type": "boolean", "default": False},
                            },
                            "required": ["target_snippet", "replacement_snippet"],
                        },
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Yeni bir dosya oluşturur veya mevcut bir dosyayı tamamen yeni içerikle yazar. Otomatik yedek alır.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Oluşturulacak veya yazılacak dosyanın yolu (örn: tests/test_new.py)",
                    },
                    "content": {
                        "type": "string",
                        "description": "Dosyaya yazılacak tam metin içeriği",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "Belirtilen klasördeki dosyaları ve alt dizinleri listeler.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Listelenecek klasör yolu (varsayılan: .)",
                        "default": ".",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "Alt klasörleri de özyinelemeli listelesin mi?",
                        "default": False,
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_files",
            "description": "Dosya adına göre (glob pattern) veya dosya içeriğinde metin arar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Arama deseni (örn: *.py veya test_*.py)",
                    },
                    "query": {
                        "type": "string",
                        "description": "İçerikte aranacak metin (isteğe bağlı)",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Windows PowerShell/Terminal ortamında güvenli komut çalıştırır (test, derleme, lint vb.). Çalıştırmadan önce kullanıcı onayı istenir.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Çalıştırılacak komut (örn: pytest, npm test, python -m unittest)",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Zaman aşımı saniye cinsinden (varsayılan 180, en fazla 600).",
                        "default": 180,
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_plan",
            "description": "Plan modunda bir uygulama planı yazar (plans/<ad>/plan.md). Kaynak dosyalara dokunmaz.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Plan adı (klasör adı olur, örn: refactor-api-client)",
                    },
                    "content": {
                        "type": "string",
                        "description": "Plan içeriği (Markdown)",
                    },
                },
                "required": ["name", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ssh_list_hosts",
            "description": "Kayıtlı SSH host'larını (alias'ları) listeler. Uzak işlem yapmadan önce hangi host'ların tanımlı olduğunu görmek için kullanın.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ssh_run",
            "description": "Kayıtlı bir uzak sunucuda (alias ile) komut çalıştırır. ask/plan modunda yalnızca salt-okuma komutlarına izin verilir. Çalıştırmadan önce kullanıcı onayı istenir.",
            "parameters": {
                "type": "object",
                "properties": {
                    "alias": {
                        "type": "string",
                        "description": "Kayıtlı SSH host alias'ı (örn: prod-web)",
                    },
                    "command": {
                        "type": "string",
                        "description": "Uzak sunucuda çalıştırılacak komut (örn: systemctl status nginx)",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Zaman aşımı saniye cinsinden (varsayılan 60, en fazla 600).",
                        "default": 60,
                    },
                },
                "required": ["alias", "command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ssh_read_file",
            "description": "Kayıtlı bir uzak sunucudaki bir dosyayı okur (salt-okuma). Her modda izinlidir.",
            "parameters": {
                "type": "object",
                "properties": {
                    "alias": {
                        "type": "string",
                        "description": "Kayıtlı SSH host alias'ı",
                    },
                    "remote_path": {
                        "type": "string",
                        "description": "Uzak sunucudaki dosya yolu (örn: /etc/nginx/nginx.conf)",
                    },
                },
                "required": ["alias", "remote_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ssh_write_file",
            "description": "Kayıtlı bir uzak sunucudaki bir dosyaya yazar. Host'ta allow_write=true olmalı ve kullanıcı onayı gerekir. ask/plan modunda devre dışıdır.",
            "parameters": {
                "type": "object",
                "properties": {
                    "alias": {
                        "type": "string",
                        "description": "Kayıtlı SSH host alias'ı",
                    },
                    "remote_path": {
                        "type": "string",
                        "description": "Yazılacak uzak dosya yolu",
                    },
                    "content": {
                        "type": "string",
                        "description": "Dosyaya yazılacak tam metin içeriği",
                    },
                },
                "required": ["alias", "remote_path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": "Belirsizlikleri netleştirmek (clarify) veya onay almak için kullanıcıya soru sorar. Her promptta işe başlamadan önce belirsiz noktaları bu araçla netleştir; tahmin etme. kind=confirm ise olumlu yanıt plan→normal geçişini tetikler.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "Kullanıcıya sorulacak soru",
                    },
                    "kind": {
                        "type": "string",
                        "description": "Soru tipi: 'confirm' (evet/hayır) veya 'text' (serbest metin)",
                        "enum": ["confirm", "text"],
                        "default": "confirm",
                    },
                },
                "required": ["question"],
            },
        },
    },
]


def tool_view_file(path: str, start_line: int | None = None, end_line: int | None = None, workspace_root: Path | None = None) -> str:
    root = workspace_root or get_workspace_dir()
    content, err = safe_read_file(path, root)
    if err:
        return f"HATA: {err}"

    lines = content.splitlines()
    total_lines = len(lines)

    s = max(1, start_line or 1)
    e = min(total_lines, end_line or total_lines)

    if s > total_lines:
        return f"HATA: Başlangıç satırı ({s}) dosya satır sayısından ({total_lines}) büyük."

    numbered_lines = [f"{idx:4d} | {lines[idx-1]}" for idx in range(s, e + 1)]
    header = f"--- {path} (Satırlar {s}-{e} / Toplam {total_lines}) ---\n"
    return header + "\n".join(numbered_lines)


def tool_edit_file(
    path: str,
    target_snippet: str = "",
    replacement_snippet: str = "",
    workspace_root: Path | None = None,
    auto_approve: bool = False,
    all: bool = False,
    edits: list[dict] | None = None,
) -> str:
    root = workspace_root or get_workspace_dir()

    # Read with metadata so BOM/CRLF are preserved on write.
    old_content, meta, err = read_file_with_meta(path, root)
    if err:
        return f"HATA: Dosya okunamadı: {err}"

    # Build the edit list: either the multi-edit list or a single edit.
    if edits:
        edit_list = edits
    else:
        edit_list = [{
            "target_snippet": target_snippet,
            "replacement_snippet": replacement_snippet,
            "all": all,
        }]

    new_content, success, rep_err = apply_edits(old_content, edit_list)
    if not success:
        return f"HATA: Değişiklik uygulanamadı: {rep_err}"

    diff = generate_unified_diff(old_content, new_content, filename=path)
    print_diff(diff, filename=path)

    # Prompt user for confirmation unless auto_approve
    if not (auto_approve or EVREN_AUTO_APPROVE):
        approved = prompt_confirm(f"'{path}' dosyasındaki değişiklik uygulansın mı?", default=True)
        if not approved:
            return "İPTAL EDİLDİ: Kullanıcı bu değişikliği onaylamadı. Lütfen aynı değişikliği tekrar denemeyin ve kullanıcıya ne yapmak istediğini sorun."

    write_ok, backup_path, write_err = safe_write_file(
        path, new_content, root, create_backup=True, preserve_meta=meta
    )
    if not write_ok:
        return f"HATA: Dosya kaydedilemedi: {write_err}"

    backup_info = f" (Yedek: {backup_path.name})" if backup_path else ""
    edit_count = len(edit_list)
    count_info = f" ({edit_count} düzenleme)" if edit_count > 1 else ""
    return f"BAŞARILI: '{path}' dosyası başarıyla güncellendi{count_info}{backup_info}."


def tool_write_file(
    path: str,
    content: str,
    workspace_root: Path | None = None,
    auto_approve: bool = False,
) -> str:
    root = workspace_root or get_workspace_dir()
    resolved = resolve_workspace_path(path, root)

    if resolved.exists():
        old_content, _ = safe_read_file(path, root)
        diff = generate_unified_diff(old_content, content, filename=path)
        print_diff(diff, filename=f"{path} (Üzerine Yazılacak)")
    else:
        print_info(f"Yeni dosya oluşturuluyor: {path} ({len(content)} karakter)")

    if not (auto_approve or EVREN_AUTO_APPROVE):
        action_name = "üzerine yazılsın" if resolved.exists() else "oluşturulsun"
        approved = prompt_confirm(f"'{path}' dosyası {action_name} mı?", default=True)
        if not approved:
            return "İPTAL EDİLDİ: Kullanıcı dosya yazma işlemini onaylamadı. Lütfen aynı işlemi tekrar denemeyin."

    write_ok, backup_path, write_err = safe_write_file(path, content, root, create_backup=True)
    if not write_ok:
        return f"HATA: Dosya yazılamadı: {write_err}"

    backup_info = f" (Eski hali yedeklendi: {backup_path.name})" if backup_path else ""
    return f"BAŞARILI: '{path}' başarıyla kaydedildi{backup_info}."


def tool_list_directory(path: str = ".", recursive: bool = False, workspace_root: Path | None = None) -> str:
    root = workspace_root or get_workspace_dir()
    resolved = resolve_workspace_path(path, root)

    safe, reason = is_safe_path(resolved, root)
    if not safe:
        return f"HATA: {reason}"

    if not resolved.exists():
        return f"HATA: Klasör bulunamadı: {resolved}"

    if not resolved.is_dir():
        return f"HATA: Belirtilen yol bir klasör değil: {resolved}"

    entries = []
    iterator = resolved.rglob("*") if recursive else resolved.iterdir()
    for item in iterator:
        # Skip forbidden parts
        if any(p in (".git", ".venv", "node_modules", "__pycache__", ".evren") for p in item.parts):
            continue
        rel = item.relative_to(root).as_posix()
        kind = "📁 DIR " if item.is_dir() else "📄 FILE"
        entries.append(f"{kind}  {rel}")

    return "\n".join(sorted(entries)[:100]) or "Klasör boş."


def tool_find_files(pattern: str, query: str = "", workspace_root: Path | None = None) -> str:
    root = workspace_root or get_workspace_dir()
    matches = []

    for path in root.rglob(pattern):
        if any(p in (".git", ".venv", "node_modules", "__pycache__", ".evren") for p in path.parts):
            continue
        if not path.is_file():
            continue

        rel = path.relative_to(root).as_posix()
        if query:
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
                if query in text:
                    matches.append(f"{rel} (Eşleşti)")
            except Exception:
                pass
        else:
            matches.append(rel)

        if len(matches) >= 50:
            matches.append("... [50 eşleşme sınırına ulaşıldı]")
            break

    return "\n".join(matches) or "Eşleşen dosya bulunamadı."


DEFAULT_COMMAND_TIMEOUT = 180
MAX_COMMAND_TIMEOUT = 600
# Output truncation: keep head + tail so exit context is preserved.
OUTPUT_HEAD_CHARS = 3000
OUTPUT_TAIL_CHARS = 1000


def _truncate_output(text: str) -> str:
    """Keeps the head and tail of command output, dropping the middle."""
    if len(text) <= OUTPUT_HEAD_CHARS + OUTPUT_TAIL_CHARS:
        return text
    head = text[:OUTPUT_HEAD_CHARS]
    tail = text[-OUTPUT_TAIL_CHARS:]
    dropped = len(text) - OUTPUT_HEAD_CHARS - OUTPUT_TAIL_CHARS
    return f"{head}\n... [{dropped} karakter kırpıldı] ...\n{tail}"


def tool_run_command(
    command: str,
    workspace_root: Path | None = None,
    auto_approve: bool = False,
    timeout_seconds: int | None = None,
) -> str:
    root = workspace_root or get_workspace_dir()

    # Clamp the timeout to a safe range.
    timeout = timeout_seconds or DEFAULT_COMMAND_TIMEOUT
    timeout = max(1, min(timeout, MAX_COMMAND_TIMEOUT))

    print_warning(f"Ajan terminal komutu çalıştırmak istiyor:\n  > {command}")

    if not (auto_approve or EVREN_AUTO_APPROVE):
        approved = prompt_confirm("Bu komutun çalıştırılmasına izin veriyor musunuz?", default=False)
        if not approved:
            return "İPTAL EDİLDİ: Kullanıcı terminal komutunun çalıştırılmasını reddetti."

    try:
        # stream_with_tree_kill yields output lines live (so the user can watch
        # progress) and guarantees grandchildren (msbuild/node) are killed on
        # timeout, not just the direct shell child. Its return value carries the
        # full captured output + exit status.
        gen = stream_with_tree_kill(
            command,
            cwd=str(root),
            timeout=timeout,
            shell=True,
        )
        code, out, err, timed_out = -1, "", "", False
        try:
            while True:
                stream_name, line = next(gen)
                print_command_output(line, stream=stream_name)
        except StopIteration as stop:
            code, out, err, timed_out = stop.value

        out = out.strip()
        err = err.strip()

        if timed_out:
            return f"HATA: Komut zaman aşımına uğradı ({timeout} saniye). Süreç ağacı sonlandırıldı."

        result_lines = [f"Çıkış Kodu: {code}"]
        if out:
            result_lines.append(f"STDOUT:\n{_truncate_output(out)}")
        if err:
            result_lines.append(f"STDERR:\n{_truncate_output(err)}")
        return "\n".join(result_lines)
    except Exception as e:
        return f"HATA: Komut çalıştırılamadı: {e}"


def tool_create_plan(
    name: str,
    content: str,
    workspace_root: Path | None = None,
    auto_approve: bool = False,
) -> str:
    """Writes a plan to plans/<name>/plan.md (plan mode only)."""
    root = workspace_root or get_workspace_dir()

    # Sanitize the plan name to a safe folder segment.
    safe_name = "".join(c for c in name if c.isalnum() or c in ("-", "_")).strip()
    if not safe_name:
        return "HATA: Geçersiz plan adı."

    rel_path = f"plans/{safe_name}/plan.md"
    resolved = resolve_workspace_path(rel_path, root)

    safe, reason = is_safe_path(resolved, root)
    if not safe:
        return f"HATA: {reason}"

    if resolved.exists():
        old_content, _ = safe_read_file(rel_path, root)
        diff = generate_unified_diff(old_content, content, filename=rel_path)
        print_diff(diff, filename=f"{rel_path} (Üzerine Yazılacak)")
    else:
        print_info(f"Yeni plan oluşturuluyor: {rel_path}")

    if not (auto_approve or EVREN_AUTO_APPROVE):
        approved = prompt_confirm(f"'{rel_path}' planı kaydedilsin mi?", default=True)
        if not approved:
            return "İPTAL EDİLDİ: Kullanıcı plan kaydını onaylamadı."

    write_ok, backup_path, write_err = safe_write_file(rel_path, content, root, create_backup=True)
    if not write_ok:
        return f"HATA: Plan kaydedilemedi: {write_err}"

    return f"BAŞARILI: Plan '{rel_path}' dosyasına kaydedildi."


def tool_ask_user(
    question: str,
    kind: str = "confirm",
    workspace_root: Path | None = None,
) -> tuple[str, bool]:
    """Asks the user a mid-turn question.

    Returns (result_text, confirmed). `confirmed` is True only for a positive
    confirm answer (used to trigger plan -> normal transition).
    """
    if kind == "text":
        try:
            answer = input(f"\n[Model sorusu] {question}\n> ").strip()
        except (KeyboardInterrupt, EOFError):
            return "İPTAL EDİLDİ: Kullanıcı yanıt vermedi.", False
        return f"Kullanıcı yanıtı: {answer}", False

    # confirm
    confirmed = prompt_confirm(f"[Model sorusu] {question}", default=False)
    if confirmed:
        return "Kullanıcı ONAYLADI.", True
    return "Kullanıcı REDDETTİ.", False


def execute_tool_call(
    name: str,
    arguments: dict[str, Any],
    workspace_root: Path | None = None,
    auto_approve: bool = False,
) -> str:
    """Dispatches a tool call to the respective handler."""
    root = workspace_root or get_workspace_dir()

    if name == "view_file":
        return tool_view_file(
            path=arguments.get("path", ""),
            start_line=arguments.get("start_line"),
            end_line=arguments.get("end_line"),
            workspace_root=root,
        )
    elif name == "edit_file":
        return tool_edit_file(
            path=arguments.get("path", ""),
            target_snippet=arguments.get("target_snippet", ""),
            replacement_snippet=arguments.get("replacement_snippet", ""),
            workspace_root=root,
            auto_approve=auto_approve,
            all=arguments.get("all", False),
            edits=arguments.get("edits"),
        )
    elif name == "write_file":
        return tool_write_file(
            path=arguments.get("path", ""),
            content=arguments.get("content", ""),
            workspace_root=root,
            auto_approve=auto_approve,
        )
    elif name == "list_directory":
        return tool_list_directory(
            path=arguments.get("path", "."),
            recursive=arguments.get("recursive", False),
            workspace_root=root,
        )
    elif name == "find_files":
        return tool_find_files(
            pattern=arguments.get("pattern", "*"),
            query=arguments.get("query", ""),
            workspace_root=root,
        )
    elif name == "run_command":
        return tool_run_command(
            command=arguments.get("command", ""),
            workspace_root=root,
            auto_approve=auto_approve,
            timeout_seconds=arguments.get("timeout_seconds"),
        )
    elif name == "create_plan":
        return tool_create_plan(
            name=arguments.get("name", ""),
            content=arguments.get("content", ""),
            workspace_root=root,
            auto_approve=auto_approve,
        )
    elif name == "ssh_list_hosts":
        from src.ssh_ops import ssh_list_hosts
        return ssh_list_hosts(workspace_root=root)
    elif name == "ssh_run":
        from src.ssh_ops import ssh_run
        alias = arguments.get("alias", "")
        command = arguments.get("command", "")
        print_warning(f"Ajan uzak sunucuda komut çalıştırmak istiyor:\n  [{alias}] > {command}")
        if not (auto_approve or EVREN_AUTO_APPROVE):
            approved = prompt_confirm(
                f"'{alias}' sunucusunda bu komutun çalıştırılmasına izin veriyor musunuz?",
                default=False,
            )
            if not approved:
                return "İPTAL EDİLDİ: Kullanıcı uzak komutun çalıştırılmasını reddetti."
        return ssh_run(
            alias=alias,
            command=command,
            timeout_seconds=arguments.get("timeout_seconds"),
            workspace_root=root,
        )
    elif name == "ssh_read_file":
        from src.ssh_ops import ssh_read_file
        return ssh_read_file(
            alias=arguments.get("alias", ""),
            remote_path=arguments.get("remote_path", ""),
            workspace_root=root,
        )
    elif name == "ssh_write_file":
        from src.ssh_ops import ssh_write_file
        alias = arguments.get("alias", "")
        remote_path = arguments.get("remote_path", "")
        content = arguments.get("content", "")
        print_warning(f"Ajan uzak sunucuda dosya yazmak istiyor:\n  [{alias}] > {remote_path} ({len(content)} karakter)")
        if not (auto_approve or EVREN_AUTO_APPROVE):
            approved = prompt_confirm(
                f"'{alias}:{remote_path}' uzak dosyası yazılsın mı?",
                default=False,
            )
            if not approved:
                return "İPTAL EDİLDİ: Kullanıcı uzak dosya yazma işlemini onaylamadı."
        return ssh_write_file(
            alias=alias,
            remote_path=remote_path,
            content=content,
            workspace_root=root,
        )
    elif name == "ask_user":
        result, _confirmed = tool_ask_user(
            question=arguments.get("question", ""),
            kind=arguments.get("kind", "confirm"),
            workspace_root=root,
        )
        return result
    else:
        return f"HATA: Tanınmayan araç çağrısı: '{name}'"
