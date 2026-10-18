"""
Agent tool definitions and executor for EVREN CLI.
Provides OpenAI-compatible function schemas and execution logic for
file reading, writing, surgical editing, directory inspection, and command running.
"""

import os
import re
import subprocess
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
from src.tool_args import (
    ToolArgumentError,
    normalize_tool_arguments,
    schema_required_map,
    schema_type_map,
)
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
            "description": "Dosyayı satır numaralarıyla okur (aralıksız ilk 250, aralıkla en fazla 400 satır). Büyük dosyada önce find_files ile yeri bul.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Dosya yolu",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "Başlangıç satırı (1'den)",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Bitiş satırı",
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
            "description": "Dosyada kod bloğunu (veya `edits` ile birden çok bloğu) cerrahi değiştirir; diff gösterilir, yedek alınır, CRLF/BOM korunur.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Dosya yolu",
                    },
                    "target_snippet": {
                        "type": "string",
                        "description": "Birebir eşleşecek eski kod",
                    },
                    "replacement_snippet": {
                        "type": "string",
                        "description": "Yeni kod",
                    },
                    "all": {
                        "type": "boolean",
                        "description": "true: tüm eşleşmeler",
                        "default": False,
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "Satır aralığı kipi: değiştirilecek ilk satır (1 tabanlı); target_snippet gerekmez, replacement_snippet bu satırların yerine geçer",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Satır aralığı kipi: son satır (dahil)",
                    },
                    "edits": {
                        "type": "array",
                        "description": "Çoklu düzenleme; verilirse target/replacement_snippet yok sayılır",
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
            "description": "Dosya oluşturur, tamamen yeniden yazar ya da append=true ile sonuna ekler.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Dosya yolu",
                    },
                    "content": {
                        "type": "string",
                        "description": "Tam içerik (append=true ise eklenecek metin)",
                    },
                    "append": {
                        "type": "boolean",
                        "description": "true: içeriği dosyanın sonuna ekle",
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
            "description": "Klasördeki dosya ve alt dizinleri listeler.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Klasör yolu",
                        "default": ".",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "Özyinelemeli",
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
            "description": "Dosya adına göre (glob) arar; query verilirse içerikte arar ve `yol:satır: metin` döner (büyük/küçük harf duyarsız, a|b alternatif, regex olur). Kod aramada findstr/grep yerine bunu kullan.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Dosya deseni (*.py)",
                    },
                    "query": {
                        "type": "string",
                        "description": "İçerikte aranacak metin (a|b|c)",
                    },
                    "context": {
                        "type": "integer",
                        "description": "Eşleşmenin önü/arkasından gösterilecek satır (0-15); view_file tahminini önler",
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
            "description": "YEREL makinede, çalışma alanında komut çalıştırır (test, derleme, sistem kontrolü). Kabuk bağlam bloğunda yazar (Windows'ta Git Bash varsa o: POSIX komutları, çok satır olur; yoksa cmd.exe). PowerShell için use_powershell=true, Python kodu için use_python=true. Çıktı uzunsa özetlenir; hedefli komut kullan.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Komut",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Zaman aşımı sn (varsayılan 180, en çok 600)",
                        "default": 180,
                    },
                    "use_python": {
                        "type": "boolean",
                        "description": "true: command Python kodudur",
                    },
                    "use_powershell": {
                        "type": "boolean",
                        "description": "true: komut PowerShell betiğidir",
                    },
                    "run_in_background": {
                        "type": "boolean",
                        "description": "true: komut arka planda başlar ve hemen bir id (örn: bg-1) döner; beklemez. Çıktıyı/durumu check_background ile artımlı oku, gerekirse stop_background ile ağacı öldür.",
                        "default": False,
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_background",
            "description": "run_in_background ile başlatılan komutun yeni çıktısını ve durumunu döner; çalışıyorsa bitmesini wait_seconds (varsayılan 20) kadar bekler. Art arda hızlı çağırma.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Arka plan kimliği (bg-1)",
                    },
                    "wait_seconds": {
                        "type": "integer",
                        "description": "Bekleme sn (varsayılan 20, 0 = hemen, en çok 300)",
                    },
                },
                "required": ["id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stop_background",
            "description": "Arka plan komutunu ve süreç ağacını sonlandırır.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Arka plan kimliği",
                    },
                },
                "required": ["id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_plan",
            "description": "Plan modunda plans/<ad>/plan.md yazar; kaynak dosyalara dokunmaz.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Plan adı (klasör adı)",
                    },
                    "content": {
                        "type": "string",
                        "description": "Markdown plan",
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
            "description": "Kayıtlı SSH alias'larını listeler. Yalnızca UZAK sunucu işlemi için; bu host/localhost yereldir.",
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
            "description": "Yalnızca açık UZAK hedef isteğinde kayıtlı sunucuda (alias) komut çalıştırır; yerel için run_command. ask/plan modunda salt-okuma; çalıştırmadan önce onay istenir.",
            "parameters": {
                "type": "object",
                "properties": {
                    "alias": {
                        "type": "string",
                        "description": "SSH alias",
                    },
                    "command": {
                        "type": "string",
                        "description": "Uzak komut; preset verilirse boş bırak",
                    },
                    "preset": {
                        "type": "string",
                        "enum": ["k8s_health", "k8s_resources"],
                        "description": "Hazır salt-okuma komutu (command yerine)",
                    },
                    "namespace": {
                        "type": "string",
                        "description": "preset için namespace (boş = tümü)",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Zaman aşımı sn (varsayılan 60, en çok 600)",
                        "default": 60,
                    },
                },
                "required": ["alias"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ssh_read_file",
            "description": "Yalnızca UZAK hedefte kayıtlı sunucudaki dosyayı okur (salt-okuma, her modda).",
            "parameters": {
                "type": "object",
                "properties": {
                    "alias": {
                        "type": "string",
                        "description": "SSH alias",
                    },
                    "remote_path": {
                        "type": "string",
                        "description": "Uzak dosya yolu",
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
            "description": "Yalnızca UZAK hedefte sunucudaki dosyaya yazar; host'ta allow_write=true ve kullanıcı onayı gerekir. ask/plan'da kapalı.",
            "parameters": {
                "type": "object",
                "properties": {
                    "alias": {
                        "type": "string",
                        "description": "SSH alias",
                    },
                    "remote_path": {
                        "type": "string",
                        "description": "Uzak dosya yolu",
                    },
                    "content": {
                        "type": "string",
                        "description": "Tam içerik",
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
            "description": "Yalnızca gerçek bloklayıcı belirsizlik veya yeni risk için sorar; yanıtlanmış soruyu tekrarlama. kind=confirm olumlu yanıtı plan→normal geçişi tetikler.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "Soru",
                    },
                    "kind": {
                        "type": "string",
                        "description": "confirm (evet/hayır) veya text",
                        "enum": ["confirm", "text"],
                        "default": "confirm",
                    },
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todo_write",
            "description": "Çok adımlı işte yapılacaklar listesini tutar; her güncellemede TÜM listeyi gönder (content + status). Aynı anda tek in_progress. Her madde için değil, birkaç madde bitince toplu güncelle.",
            "parameters": {
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "description": "Tam liste; boş dizi temizler",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {"type": "string", "description": "Görev"},
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "completed"],
                                    "default": "pending",
                                },
                            },
                            "required": ["content"],
                        },
                    },
                },
                "required": ["todos"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "http/https URL'yi GET ile indirir; HTML okunur metne çevrilir, JSON/düz metin olduğu gibi. Salt-okuma, boyut ve zaman sınırlı.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "http/https adresi",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Zaman aşımı sn (varsayılan 15, en çok 60)",
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "Azami karakter (varsayılan 20000)",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delegate",
            "description": "Bağımsız araştırma görevini temiz bağlamlı salt-okuma alt ajana devreder; yalnızca kısa özet döner. Yalnızca normal modda.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "Kendi başına anlaşılır araştırma görevi",
                    },
                    "max_steps": {
                        "type": "integer",
                        "description": "Azami adım (varsayılan 6, en çok 12)",
                    },
                    "agent": {
                        "type": "string",
                        "description": "Alt ajan tanımı adı (varsayılan: researcher, salt-okuma)",
                    },
                },
                "required": ["task"],
            },
        },
    },
]

from src.apply_patch import SCHEMA as _APPLY_PATCH_SCHEMA
TOOLS_SCHEMA.append(_APPLY_PATCH_SCHEMA)

# Derived once from TOOLS_SCHEMA: {tool: {param: declared json type}} and
# {tool: [required params]}. Used to normalize lenient model arguments.
SEARCH_CODE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_code",
        "description": "Anlamsal kod arama (salt-okuma): doğal dil sorgusuyla depoda ilgili kod parçalarını path:satır aralığıyla döndürür. 'X nerede/nasıl kullanılıyor' soruları için.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Aranan şeyin doğal dil tarifi"}},
            "required": ["query"],
        },
    },
}
TOOL_ARGUMENT_TYPES = schema_type_map(TOOLS_SCHEMA + [SEARCH_CODE_SCHEMA])
TOOL_ARGUMENT_REQUIRED = schema_required_map(TOOLS_SCHEMA + [SEARCH_CODE_SCHEMA])

# Tools that are useless outside `normal` mode: they are always denied by the
# gateway, so sending their schemas only wastes prompt tokens on every step.
# `delegate` is also hidden in ask/plan: the sub-agent runs in ASK mode, so an
# advertised schema there would only invite recursive delegation attempts that
# the gateway denies anyway.
from src.tool_registry import ASK_HIDDEN as _REG_ASK_HIDDEN, PLAN_HIDDEN as _REG_PLAN_HIDDEN
ASK_HIDDEN_TOOLS = set(_REG_ASK_HIDDEN)
PLAN_HIDDEN_TOOLS = set(_REG_PLAN_HIDDEN)

SSH_TOOL_NAMES = {"ssh_list_hosts", "ssh_run", "ssh_read_file", "ssh_write_file"}


def tools_for_mode(mode, include_ssh: bool = True) -> list[dict]:
    """Returns the tool schemas that should be advertised for `mode`.

    `normal` returns the full TOOLS_SCHEMA unchanged. `ask` and `plan` drop the
    write tools that the gateway would deny anyway, which cuts several hundred
    tokens from every step of a read-only session. `tool_gateway` stays the
    authority: this only avoids paying for schemas that can never be used.
    """
    tools = _tools_for_mode_base(mode, include_ssh)
    from src.vision_search import semantic_search_enabled

    return tools + [SEARCH_CODE_SCHEMA] if semantic_search_enabled() else tools


def _tools_for_mode_base(mode, include_ssh: bool = True) -> list[dict]:
    from src.modes import AgentMode

    if mode == AgentMode.ASK:
        hidden = set(ASK_HIDDEN_TOOLS)
    elif mode == AgentMode.PLAN:
        hidden = set(PLAN_HIDDEN_TOOLS)
    elif include_ssh:
        return TOOLS_SCHEMA
    else:
        hidden = set()
    if not include_ssh:
        hidden |= SSH_TOOL_NAMES
    return [t for t in TOOLS_SCHEMA if t.get("function", {}).get("name") not in hidden]


# view_file window: lines returned without a range / with an explicit range,
# and a hard character ceiling for files with very long lines.
VIEW_DEFAULT_LINES = 250
VIEW_MAX_LINES = 400
VIEW_MAX_CHARS = 24000


def tool_view_file(path: str, start_line: int | None = None, end_line: int | None = None, workspace_root: Path | None = None) -> str:
    root = workspace_root or get_workspace_dir()
    if Path(path).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        from src.vision_search import describe_image

        try:
            img = resolve_workspace_path(path, root)
        except Exception as e:
            return f"HATA: {e}"
        if not img.is_file():
            return f"HATA: Görsel bulunamadı: {path}"
        return describe_image(img, path)
    content, err = safe_read_file(path, root)
    if err:
        return f"HATA: {err}"

    lines = content.splitlines()
    total_lines = len(lines)

    s = max(1, start_line or 1)
    e = min(total_lines, end_line or total_lines)

    if s > total_lines:
        return f"HATA: Başlangıç satırı ({s}) dosya satır sayısından ({total_lines}) büyük."

    # Every tool result is re-sent on each later step, so one unbounded read of a
    # large file is paid for many times. Cap the window and say how to continue.
    limit = VIEW_DEFAULT_LINES if start_line is None and end_line is None else VIEW_MAX_LINES
    clipped = e - s + 1 > limit
    if clipped:
        e = s + limit - 1
    numbered_lines = [f"{idx:4d} | {lines[idx-1]}" for idx in range(s, e + 1)]
    body = "\n".join(numbered_lines)
    if len(body) > VIEW_MAX_CHARS:
        body = body[:VIEW_MAX_CHARS].rsplit("\n", 1)[0]
        e = s + body.count("\n")
        clipped = True
    header = f"--- {path} (Satırlar {s}-{e} / Toplam {total_lines}) ---\n"
    if clipped:
        body += (
            f"\n... [kırpıldı: {e + 1}-{total_lines} okunmadı. Gerekirse find_files(query=...) ile "
            "yeri bul ve view_file'ı start_line/end_line ile çağır]"
        )
    return header + body


_PROBE_TARGET_RE = re.compile(r"(?i)^\s*(placeholder\w*|x|xx+|test|todo|dummy|foo|\.\.\.)\s*$")


def _is_probe_target(target: object) -> bool:
    return isinstance(target, str) and bool(_PROBE_TARGET_RE.match(target))


def request_approval(kind: str, auto_approve: bool = False, **info) -> str | None:
    """Central approval gate for every tool that needs user consent.

    Returns None when approved (or auto-approved), otherwise the refusal text
    the tool must return to the model.
    """
    if auto_approve or EVREN_AUTO_APPROVE:
        return None
    if kind == "edit":
        ok = prompt_confirm(f"'{info['path']}' dosyasındaki değişiklik uygulansın mı?", default=True)
        deny = "İPTAL EDİLDİ: Kullanıcı bu değişikliği onaylamadı. Lütfen aynı değişikliği tekrar denemeyin ve kullanıcıya ne yapmak istediğini sorun."
    elif kind == "write":
        ok = prompt_confirm(f"'{info['path']}' dosyası {info['action']} mı?", default=True)
        deny = "İPTAL EDİLDİ: Kullanıcı dosya yazma işlemini onaylamadı. Lütfen aynı işlemi tekrar denemeyin."
    elif kind == "command":
        print_warning(f"Ajan terminal komutu çalıştırmak istiyor:\n  > {info['shown']}")
        ok = prompt_confirm("Bu komutun çalıştırılmasına izin veriyor musunuz?", default=False)
        deny = "İPTAL EDİLDİ: Kullanıcı terminal komutunun çalıştırılmasını reddetti."
    elif kind == "background":
        print_warning(f"Ajan arka planda terminal komutu çalıştırmak istiyor:\n  > {info['shown']}")
        ok = prompt_confirm("Bu komutun arka planda çalıştırılmasına izin veriyor musunuz?", default=False)
        deny = "İPTAL EDİLDİ: Kullanıcı arka plan komutunun çalıştırılmasını reddetti."
    elif kind == "plan":
        ok = prompt_confirm(f"'{info['path']}' planı kaydedilsin mi?", default=True)
        deny = "İPTAL EDİLDİ: Kullanıcı plan kaydını onaylamadı."
    elif kind == "ssh_run":
        print_warning(f"Ajan uzak sunucuda komut çalıştırmak istiyor:\n  [{info['alias']}] > {info['shown']}")
        ok = prompt_confirm(f"'{info['alias']}' sunucusunda bu komutun çalıştırılmasına izin veriyor musunuz?", default=False)
        deny = "İPTAL EDİLDİ: Kullanıcı uzak komutun çalıştırılmasını reddetti."
    elif kind == "ssh_write":
        print_warning(f"Ajan uzak sunucuda dosya yazmak istiyor:\n  [{info['alias']}] > {info['remote_path']} ({info['size']} karakter)")
        ok = prompt_confirm(f"'{info['alias']}:{info['remote_path']}' uzak dosyası yazılsın mı?", default=False)
        deny = "İPTAL EDİLDİ: Kullanıcı uzak dosya yazma işlemini onaylamadı."
    else:
        return None
    return None if ok else deny


def tool_edit_file(
    path: str,
    target_snippet: str = "",
    replacement_snippet: str = "",
    workspace_root: Path | None = None,
    auto_approve: bool = False,
    all: bool = False,
    edits: list[dict] | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
) -> str:
    root = workspace_root or get_workspace_dir()

    # Read with metadata so BOM/CRLF are preserved on write.
    old_content, meta, err = read_file_with_meta(path, root)
    if err:
        try:
            missing = not resolve_workspace_path(path, root).exists()
        except Exception:
            missing = False
        if missing:
            return (
                f"HATA: '{path}' yok; edit_file yalnızca var olan dosyayı düzenler. "
                "Yeni dosya için write_file kullan."
            )
        return f"HATA: Dosya okunamadı: {err}"

    use_range = start_line not in (None, "") and not edits
    if use_range:
        try:
            first = int(start_line)
            last = int(end_line) if end_line not in (None, "") else first
        except (TypeError, ValueError):
            return "HATA: start_line/end_line tam sayı olmalı."
        lines = old_content.splitlines(keepends=True)
        if first < 1 or last < first or first > len(lines):
            return f"HATA: Geçersiz satır aralığı {first}-{last} (dosya {len(lines)} satır)."
        last = min(last, len(lines))
        eol = "\r\n" if "\r\n" in old_content else "\n"
        block = replacement_snippet.replace("\r\n", "\n").replace("\n", eol)
        if block and not block.endswith(("\n", "\r")) and lines[last - 1].endswith(("\n", "\r")):
            block += eol
        new_content = "".join(lines[: first - 1]) + block + "".join(lines[last:])
        if new_content == old_content:
            return f"BİLGİ: '{path}' {first}-{last}. satırlar zaten bu içerikte."
        edit_list = [None]
        probes = []
    else:
        probes = None
    probes = probes if probes is not None else [
        (e.get("target_snippet") if isinstance(e, dict) else "") for e in (edits or [])
    ] or [target_snippet]
    if any(_is_probe_target(t) for t in probes):
        # Seen in practice: target_snippet='placeholder' / 'PLACEHOLDER_DO_NOT_MATCH'
        # sent on purpose to peek at the file through the "closest line" error.
        return (
            "HATA: target_snippet yer tutucu/deneme metni gibi görünüyor. Dosyayı görmek için "
            "view_file (start_line/end_line) kullan; edit_file'a yalnızca dosyadaki gerçek metni ver."
        )

    # Build the edit list: either the multi-edit list or a single edit.
    if use_range:
        success, rep_err = True, ""
    else:
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
        from src.diff_viewer import ALREADY_APPLIED

        if rep_err.startswith(ALREADY_APPLIED):
            # Not an error: the file already has this change.
            return f"BİLGİ: '{path}' {rep_err}"
        return f"HATA: Değişiklik uygulanamadı: {rep_err}"

    diff = generate_unified_diff(old_content, new_content, filename=path)
    print_diff(diff, filename=path)

    # Prompt user for confirmation unless auto_approve
    denied = request_approval("edit", auto_approve, path=path)
    if denied:
        return denied

    write_ok, backup_path, write_err = safe_write_file(
        path, new_content, root, preserve_meta=meta
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
    append: bool = False,
) -> str:
    root = workspace_root or get_workspace_dir()
    resolved = resolve_workspace_path(path, root)
    if append and resolved.exists():
        # Appending through PowerShell Add-Content rewrote README's line endings
        # and encoding, so the whole file showed as changed and the model looped.
        old, _ = safe_read_file(path, root)
        eol = "\r\n" if "\r\n" in old else "\n"
        if old and not old.endswith("\n"):
            old += eol
        if eol == "\r\n":
            content = content.replace("\r\n", "\n").replace("\n", "\r\n")
        content = old + content

    if resolved.exists():
        old_content, _ = safe_read_file(path, root)
        diff = generate_unified_diff(old_content, content, filename=path)
        print_diff(diff, filename=f"{path} (Üzerine Yazılacak)")
    else:
        print_info(f"Yeni dosya oluşturuluyor: {path} ({len(content)} karakter)")

    action_name = "üzerine yazılsın" if resolved.exists() else "oluşturulsun"
    denied = request_approval("write", auto_approve, path=path, action=action_name)
    if denied:
        return denied

    write_ok, backup_path, write_err = safe_write_file(path, content, root)
    if not write_ok:
        return f"HATA: Dosya yazılamadı: {write_err}"

    backup_info = f" (Eski hali yedeklendi: {backup_path.name})" if backup_path else ""
    return f"BAŞARILI: '{path}' başarıyla kaydedildi{backup_info}."


def tool_list_directory(path: str = ".", recursive: bool = False, workspace_root: Path | None = None) -> str:
    root = workspace_root or get_workspace_dir()
    resolved = resolve_workspace_path(path, root)

    safe, reason = is_safe_path(resolved, root, read_only=True)
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


_REGEX_HINT_RE = re.compile(r"\\[.dswbDSWB()\[\]{}+*?^$]|\(\?|[.*+?]\*|\[[^\]]+\]|\([^()]+\|[^()]+\)|\^|\$$")


def _query_matcher(query: str, needles: list[str]):
    """Returns line -> bool for find_files (regex when the query is one)."""
    if query and _REGEX_HINT_RE.search(query):
        try:
            compiled = re.compile(query, re.IGNORECASE)
        except re.error:
            compiled = None
        if compiled is not None:
            return lambda line: compiled.search(line) is not None
    return lambda line: any(n in line.casefold() for n in needles)


# find_files content search limits (grep -n style output).
FIND_MAX_LINES = 80
FIND_MAX_PER_FILE = 12
FIND_LINE_CHARS = 180


def _git_ignored(root: Path, paths: list[Path]) -> set[str]:
    """Relative posix paths ignored by .gitignore (empty set outside a git repo)."""
    if not paths or not (root / ".git").exists():
        return set()
    rels = [p.relative_to(root).as_posix() for p in paths]
    try:
        proc = subprocess.run(
            ["git", "check-ignore", "--stdin", "-z"], cwd=str(root), input="\0".join(rels) + "\0",
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    return {x for x in proc.stdout.split("\0") if x}


def _looks_binary(path: Path) -> bool:
    """True when the first KB holds a NUL byte."""
    try:
        with open(path, "rb") as fh:
            return b"\0" in fh.read(1024)
    except OSError:
        return False


def _context_blocks(rel: str, lines: list[str], matcher, context: int) -> list[str]:
    """Matching lines with `context` neighbours, merged, `--` between blocks."""
    context = max(1, min(15, int(context)))
    hit_idx = [i for i, ln in enumerate(lines) if matcher(ln)][:FIND_MAX_PER_FILE]
    if not hit_idx:
        return []
    out: list[str] = []
    last = -1
    hits = set(hit_idx)
    for i in hit_idx:
        start, end = max(0, i - context), min(len(lines), i + context + 1)
        if start <= last:
            start = last + 1
        elif out:
            out.append("--")
        for j in range(start, end):
            sep = ":" if j in hits else "-"
            out.append(f"{rel}{sep}{j + 1}{sep} {lines[j].rstrip()[:FIND_LINE_CHARS]}")
        last = max(last, end - 1)
    return out


def tool_find_files(
    pattern: str, query: str = "", workspace_root: Path | None = None, context: int = 0,
) -> str:
    """Finds files by glob; with `query`, returns matching lines like `grep -n`.

    The content search used to answer only "path (Eşleşti)", so the model
    fell back to repeated `findstr` calls to learn line numbers. It now prints
    `path:line: text` (case-insensitive, `|` separates alternatives).
    """
    root = workspace_root or get_workspace_dir()
    skip = (".git", ".venv", "venv", "node_modules", "__pycache__", ".evren")
    needles = [q.casefold() for q in (query or "").split("|") if q.strip()]
    # Models write regexes ("(?i)evren", "2\\.0\\.0", "def (get|set)_x"); the
    # literal-only search answered "no match" and sent them to findstr. Use the
    # query as a case-insensitive regex when it compiles and looks like one,
    # otherwise keep the literal "a|b" behaviour.
    matcher = _query_matcher(query or "", needles)
    files: list[str] = []
    lines_out: list[str] = []
    truncated = False

    candidates = [
        path for path in sorted(root.rglob(pattern or "*"))
        if not any(p in skip for p in path.parts) and path.is_file()
    ]
    ignored = _git_ignored(root, candidates)
    for path in candidates:
        rel = path.relative_to(root).as_posix()
        if rel in ignored or _looks_binary(path):
            continue
        if not needles:
            files.append(rel)
            if len(files) >= 100:
                files.append("... [100 dosya sınırına ulaşıldı]")
                break
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        hits = 0
        if context:
            # grep -C: a 2000-line repl.py task guessed six view_file ranges to
            # find one table; showing the neighbourhood answers in one call.
            block = _context_blocks(rel, text.splitlines(), matcher, context)
            if block:
                lines_out.extend(block)
                if len(lines_out) >= FIND_MAX_LINES * 3:
                    lines_out.append("... [satır sınırı; aramayı daralt]")
                    break
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if matcher(line):
                hits += 1
                if hits > FIND_MAX_PER_FILE:
                    lines_out.append(f"{rel}: ... [bu dosyada daha fazla eşleşme var]")
                    break
                # Keep the indentation: the line can then be used verbatim as an
                # edit_file target without a separate view_file round trip.
                lines_out.append(f"{rel}:{number}: {line.rstrip()[:FIND_LINE_CHARS]}")
                if len(lines_out) >= FIND_MAX_LINES:
                    truncated = True
                    break
        if truncated:
            lines_out.append(f"... [{FIND_MAX_LINES} satır sınırı; aramayı daralt]")
            break

    if needles:
        return "\n".join(lines_out) or "Eşleşme bulunamadı."
    return "\n".join(files) or "Eşleşen dosya bulunamadı."


def tool_web_fetch(url: str, timeout: int | None = None, max_chars: int | None = None) -> str:
    """Fetches an http(s) URL and returns readable text (see src.web_fetch).

    Secret placeholders (e.g. a token in a query string) are expanded only for
    the request; the result is masked again before it reaches the model.
    """
    from src.secret_vault import active_vault
    from src.web_fetch import fetch_url

    vault = active_vault()
    return vault.mask(fetch_url(vault.expand(url or ""), timeout=timeout, max_chars=max_chars))


DEFAULT_COMMAND_TIMEOUT = 180
MAX_COMMAND_TIMEOUT = 600
# Output truncation: keep head + tail so exit context is preserved.
OUTPUT_HEAD_CHARS = 6000
OUTPUT_TAIL_CHARS = 2000


def _truncate_output(text: str) -> str:
    """Keeps the head and tail of command output, dropping the middle."""
    if len(text) <= OUTPUT_HEAD_CHARS + OUTPUT_TAIL_CHARS:
        return text
    head = text[:OUTPUT_HEAD_CHARS]
    tail = text[-OUTPUT_TAIL_CHARS:]
    dropped = len(text) - OUTPUT_HEAD_CHARS - OUTPUT_TAIL_CHARS
    return f"{head}\n... [{dropped} karakter kırpıldı] ...\n{tail}"


_INLINE_PYTHON_RE = re.compile(
    r'^\s*(?:python3?|py)(?:\.exe)?\s+-c\s+(["\'])(?P<code>.*)\1\s*$', re.DOTALL,
)


_POWERSHELL_WRAPPER_RE = re.compile(
    r'^\s*(?:powershell|pwsh)(?:\.exe)?(?:\s+-(?:NoProfile|NonInteractive|NoLogo|ExecutionPolicy\s+\S+))*'
    r'\s+-Command\s+(?P<q>["\'])(?P<body>.*)(?P=q)\s*$',
    re.IGNORECASE | re.DOTALL,
)


class MultilineShellCommand(ValueError):
    """A multi-line command that cmd.exe would silently mangle."""


_WIN_FILTER_RE = re.compile(r"(\|\s*)(find|sort)(?=\s+(?:/[a-zA-Z]|\"))", re.IGNORECASE)


def _pin_windows_filters(command: str) -> str:
    """`| find /c ...` and `| sort /r` must hit the Windows tools.

    With Git's usr/bin on PATH, `find /c /v ""` ran GNU find over the whole C:
    drive: one "count the files" task spent ~3 minutes and 100K tokens.
    """
    root = os.environ.get("SystemRoot", r"C:\Windows")
    return _WIN_FILTER_RE.sub(lambda m: f"{m.group(1)}{root}\\System32\\{m.group(2).lower()}.exe", command)


def _command_argv(
    command: str, use_powershell: bool, use_python: bool = False,
) -> tuple[str | list[str], bool]:
    """Returns (command, use_shell) for the requested shell.

    use_powershell=True runs the text as a PowerShell script through an argv list,
    so the model never has to nest quotes inside `powershell -Command "..."`
    under cmd.exe (the most common cause of broken Windows commands).
    use_python=True runs the text as Python code via `sys.executable -c`.

    cmd.exe treats a newline as the end of the command, so a multi-line
    `python -c "..."` used to run as a glued-together, broken one-liner. That form
    is rewritten to the argv route; any other multi-line cmd command is rejected
    with a clear message instead of executing garbage.
    """
    import sys

    if use_python:
        return [sys.executable, "-c", command], False
    if not use_powershell and os.name == "nt":
        from src.config import git_bash_path, windows_shell

        shell = windows_shell()
        if shell == "bash":
            # POSIX semantics, multi-line scripts and the GNU tools the model
            # expects; no cmd quoting or `find /c` pitfalls.
            return [git_bash_path(), "-c", command], False
        if shell == "powershell":
            use_powershell = True
    if not use_powershell:
        if os.name == "nt" and "\n" in command.strip():
            match = _INLINE_PYTHON_RE.match(command)
            if match:
                code = match.group("code")
                if match.group(1) == '"':
                    code = code.replace('\\"', '"')
                return [sys.executable, "-c", code], False
            raise MultilineShellCommand(
                "cmd.exe çok satırlı komut çalıştıramaz. Python kodu için use_python=true, "
                "PowerShell için use_powershell=true ver ya da satırları && ile birleştir."
            )
        if os.name == "nt":
            command = _pin_windows_filters(command)
        return command, True
    exe = "powershell" if os.name == "nt" else "pwsh"
    # use_powershell=true plus a hand-written `powershell -Command "..."`
    # wrapper nested two shells and failed to parse; unwrap it.
    wrapped = _POWERSHELL_WRAPPER_RE.match(command)
    if wrapped:
        command = wrapped.group("body")
        if wrapped.group("q") == '"':
            command = command.replace('\\"', '"')
    script = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; " + command
    return [exe, "-NoProfile", "-NonInteractive", "-Command", script], False


def tool_run_command(
    command: str,
    workspace_root: Path | None = None,
    auto_approve: bool = False,
    timeout_seconds: int | None = None,
    use_powershell: bool = False,
    use_python: bool = False,
) -> str:
    root = workspace_root or get_workspace_dir()

    # Clamp the timeout to a safe range.
    timeout = timeout_seconds or DEFAULT_COMMAND_TIMEOUT
    timeout = max(1, min(timeout, MAX_COMMAND_TIMEOUT))

    from src.secret_vault import active_vault

    vault = active_vault()
    # The model only ever sees placeholders; the real secret is inserted here
    # and never printed. With auto-approve on, the single ⚡ tool-call line is
    # the whole announcement; the warning is only for real confirmation prompts.
    denied = request_approval("command", auto_approve, shown=vault.mask(command))
    if denied:
        return denied

    try:
        # stream_with_tree_kill yields output lines live (so the user can watch
        # progress) and guarantees grandchildren (msbuild/node) are killed on
        # timeout, not just the direct shell child. Its return value carries the
        # full captured output + exit status.
        try:
            argv, use_shell = _command_argv(vault.expand(command), use_powershell, use_python)
        except MultilineShellCommand as e:
            return f"HATA: {e}"
        extra = {"env": vault.env()} if vault else {}
        gen = stream_with_tree_kill(
            argv,
            cwd=str(root),
            timeout=timeout,
            shell=use_shell,
            **extra,
        )
        code, out, err, timed_out = -1, "", "", False
        try:
            while True:
                stream_name, line = next(gen)
                print_command_output(vault.mask(line), stream=stream_name)
        except StopIteration as stop:
            code, out, err, timed_out = stop.value

        out = vault.mask(out.strip())
        err = vault.mask(err.strip())

        if timed_out:
            return f"HATA: Komut zaman aşımına uğradı ({timeout} saniye). Süreç ağacı sonlandırıldı."

        result_lines = [f"Çıkış Kodu: {code}"]
        if out:
            from src.ssh_ops import spill_long_output

            # Output was already streamed live; only save it and tell the model.
            result_lines.append(
                f"STDOUT:\n{spill_long_output(out, root, 'cmd', shown_live=True, command=command)}"
            )
        if err:
            result_lines.append(f"STDERR:\n{_truncate_output(err)}")
        return "\n".join(result_lines)
    except Exception as e:
        return f"HATA: Komut çalıştırılamadı: {e}"


def _start_background_command(
    command: str,
    root: Path,
    use_powershell: bool,
    use_python: bool,
    auto_approve: bool = False,
) -> str:
    """Starts a run_command job in the background and returns its id immediately.

    Same approval gate as a foreground run_command: the background path used to
    skip it, so with auto-approve off a command still ran without asking.
    """
    from src.background import get_manager
    from src.secret_vault import active_vault

    vault = active_vault()
    denied = request_approval("background", auto_approve, shown=vault.mask(command))
    if denied:
        return denied
    try:
        argv, use_shell = _command_argv(vault.expand(command), use_powershell, use_python)
    except MultilineShellCommand as e:
        return f"HATA: {e}"
    extra = {"env": vault.env()} if vault else {}
    try:
        record = get_manager().start(
            vault.mask(command), cwd=str(root), shell=use_shell, argv=argv, **extra
        )
    except Exception as e:
        return f"HATA: Arka plan komutu başlatılamadı: {e}"
    return (
        f"BAŞARILI: Arka plan komutu başlatıldı (id: {record.id}). "
        "Çıktı/durum için check_background, sonlandırmak için stop_background kullan."
    )


CHECK_DEFAULT_WAIT = 20
CHECK_MAX_WAIT = 300


def tool_check_background(id: str = "", wait_seconds: Any = None) -> str:  # noqa: A002 - şema parametresi 'id'
    """Returns incremental output + status for a background job.

    While the job runs it first waits up to `wait_seconds` (default 20) for it
    to finish, returning as soon as it does. Without the wait the model polled
    18 times in a row and spent its whole step budget before the tests ended.
    """
    import time

    from src.background import MAX_CHECK_CHARS, get_manager
    from src.secret_vault import active_vault

    vault = active_vault()
    try:
        wait = CHECK_DEFAULT_WAIT if wait_seconds in (None, "") else int(wait_seconds)
    except (TypeError, ValueError):
        wait = CHECK_DEFAULT_WAIT
    wait = max(0, min(CHECK_MAX_WAIT, wait))
    record = get_manager().get(id)
    if record is not None and wait:
        deadline = time.monotonic() + wait
        while record.running() and time.monotonic() < deadline:
            time.sleep(0.2)
        if not record.running() and record._reader is not None:
            record._reader.join(timeout=2)  # let the reader flush the tail
    try:
        output, status, code = get_manager().check(id)
    except KeyError:
        known = ", ".join(r.id for r in get_manager().list()) or "(kayıtlı yok)"
        return f"HATA: Bilinmeyen arka plan id: '{id}'. Kayıtlı id'ler: {known}"
    output = vault.mask(output)
    if len(output) > MAX_CHECK_CHARS:
        output = output[:MAX_CHECK_CHARS] + "\n[çıktı kırpıldı]"
    lines = [f"Durum: {status}"]
    if code is not None:
        lines.append(f"Çıkış Kodu: {code}")
    lines.append(f"Yeni Çıktı:\n{output if output.strip() else '(son okumadan bu yana yeni çıktı yok)'}")
    return "\n".join(lines)


def tool_stop_background(id: str = "") -> str:  # noqa: A002 - şema parametresi 'id'
    """Kills a background job's whole process tree."""
    from src.background import get_manager

    if not get_manager().stop(id):
        known = ", ".join(r.id for r in get_manager().list()) or "(kayıtlı yok)"
        return f"HATA: Bilinmeyen arka plan id: '{id}'. Kayıtlı id'ler: {known}"
    return f"BAŞARILI: Arka plan komutu '{id}' ve süreç ağacı sonlandırıldı."


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

    denied = request_approval("plan", auto_approve, path=rel_path)
    if denied:
        return denied

    write_ok, backup_path, write_err = safe_write_file(rel_path, content, root)
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


def _syntax_report(path: str, root: Path) -> str:
    """Compiles an edited .py / parses an edited .json; '' when fine.

    An edit that broke the syntax used to surface only when tests ran, often
    after the step budget was gone, leaving the file unimportable.
    """
    try:
        target = resolve_workspace_path(path, root)
    except Exception:
        return ""
    suffix = target.suffix.lower()
    if suffix not in (".py", ".json") or not target.is_file():
        return ""
    try:
        source = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""
    try:
        if suffix == ".py":
            compile(source, str(target), "exec")
        else:
            import json as _json

            _json.loads(source)
    except SyntaxError as e:
        line = (e.text or "").rstrip()
        return (
            f"\n⚠ SÖZDİZİMİ HATASI: {path}:{e.lineno}: {e.msg}" + (f"\n    {line}" if line else "")
            + "\nDosya şu an içe aktarılamaz; bir sonraki adımda önce bunu düzelt (view_file ile satırı oku)."
        )
    except ValueError as e:
        return f"\n⚠ JSON HATASI: {path}: {e}. Bir sonraki adımda önce bunu düzelt."
    return ""


def tool_todo_write(todos: Any = None) -> str:
    """Replaces the session todo list and returns the rendered checklist.

    The returned text is printed by the agent loop as the tool result, so the
    user sees the checklist update on the terminal after every write.
    """
    from src.todos import get_store

    if todos is None or not isinstance(todos, list):
        return "HATA: 'todos' alanı {content, status} nesnelerinden oluşan bir dizi olmalı."
    try:
        get_store().write(todos)
    except ValueError as exc:
        return f"HATA: {exc}"
    return get_store().render() or "Görev listesi temizlendi."


_TOOL_NAME_ALIASES = {
    "read_file": "view_file", "cat": "view_file", "open_file": "view_file",
    "grep": "find_files", "search_files": "find_files", "search": "find_files",
    "ls": "list_directory", "list_dir": "list_directory", "list_files": "list_directory",
    "bash": "run_command", "shell": "run_command", "execute_command": "run_command",
    "create_file": "write_file", "str_replace": "edit_file", "replace_in_file": "edit_file",
}


def execute_tool_call(
    name: str,
    arguments: dict[str, Any],
    workspace_root: Path | None = None,
    auto_approve: bool = False,
) -> str:
    """Dispatches a tool call to the respective handler."""
    root = workspace_root or get_workspace_dir()
    # Names models borrow from other agents; a wrong name cost a whole step.
    name = _TOOL_NAME_ALIASES.get(name, name)

    # Models sometimes send structured parameters as double-encoded JSON
    # strings ("edits='[{...}]'") or use aliased key names. Normalize them to
    # the declared schema types here so handlers always receive dict/list.
    try:
        arguments = normalize_tool_arguments(
            name,
            arguments,
            type_map=TOOL_ARGUMENT_TYPES,
            required_map=TOOL_ARGUMENT_REQUIRED,
        )
    except ToolArgumentError as exc:
        return f"HATA: '{name}' için geçersiz araç argümanları: {exc}"

    if name == "search_code":
        from src.vision_search import search_code

        return search_code(str(arguments.get("query", "")), root)
    if name == "view_file":
        return tool_view_file(
            path=arguments.get("path", ""),
            start_line=arguments.get("start_line"),
            end_line=arguments.get("end_line"),
            workspace_root=root,
        )
    elif name == "edit_file":
        result = tool_edit_file(
            path=arguments.get("path", ""),
            target_snippet=arguments.get("target_snippet", ""),
            replacement_snippet=arguments.get("replacement_snippet", ""),
            workspace_root=root,
            auto_approve=auto_approve,
            all=arguments.get("all", False),
            edits=arguments.get("edits"),
            start_line=arguments.get("start_line"),
            end_line=arguments.get("end_line"),
        )
        if result.startswith("BAŞARILI"):
            result += _syntax_report(arguments.get("path", ""), root)
        return result
    elif name == "write_file":
        result = tool_write_file(
            path=arguments.get("path", ""),
            content=arguments.get("content", ""),
            workspace_root=root,
            auto_approve=auto_approve,
            append=bool(arguments.get("append")),
        )
        if result.startswith("BAŞARILI"):
            result += _syntax_report(arguments.get("path", ""), root)
        return result
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
            context=int(arguments.get("context") or 0),
        )
    elif name == "run_command":
        if arguments.get("run_in_background") is True:
            return _start_background_command(
                command=arguments.get("command", ""),
                root=root,
                use_powershell=arguments.get("use_powershell") is True,
                use_python=arguments.get("use_python") is True,
                auto_approve=auto_approve,
            )
        return tool_run_command(
            command=arguments.get("command", ""),
            workspace_root=root,
            auto_approve=auto_approve,
            timeout_seconds=arguments.get("timeout_seconds"),
            use_powershell=arguments.get("use_powershell") is True,
            use_python=arguments.get("use_python") is True,
        )
    elif name == "check_background":
        return tool_check_background(
            id=arguments.get("id", ""), wait_seconds=arguments.get("wait_seconds"),
        )
    elif name == "stop_background":
        return tool_stop_background(id=arguments.get("id", ""))
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
        from src.secret_vault import active_vault

        vault = active_vault()
        alias = arguments.get("alias", "")
        command = arguments.get("command", "")
        preset = (arguments.get("preset") or "").strip()
        if preset:
            from src.remote_presets import build_preset

            try:
                command = build_preset(preset, arguments.get("namespace"))
            except ValueError as e:
                return f"HATA: {e}"
            shown = f"preset={preset}" + (f" namespace={arguments.get('namespace')}" if arguments.get("namespace") else "")
        else:
            if not command.strip():
                return "HATA: ssh_run için 'command' veya 'preset' gerekli."
            shown = vault.mask(command)
        denied = request_approval("ssh_run", auto_approve, alias=alias, shown=shown)
        if denied:
            return denied
        return vault.mask(ssh_run(
            alias=alias,
            command=vault.expand(command),
            timeout_seconds=arguments.get("timeout_seconds"),
            workspace_root=root,
        ))
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
        denied = request_approval("ssh_write", auto_approve, alias=alias,
                                  remote_path=remote_path, size=len(content))
        if denied:
            return denied
        return ssh_write_file(
            alias=alias,
            remote_path=remote_path,
            content=content,
            workspace_root=root,
        )
    elif name == "web_fetch":
        return tool_web_fetch(
            url=arguments.get("url", ""),
            timeout=arguments.get("timeout"),
            max_chars=arguments.get("max_chars"),
        )
    elif name == "todo_write":
        return tool_todo_write(todos=arguments.get("todos"))
    elif name == "ask_user":
        result, _confirmed = tool_ask_user(
            question=arguments.get("question", ""),
            kind=arguments.get("kind", "confirm"),
            workspace_root=root,
        )
        return result
    else:
        from src.tool_ext import dispatch as _ext_dispatch
        ext = _ext_dispatch(name, arguments, root, auto_approve)
        if ext is not None:
            return ext
        return f"HATA: Tanınmayan araç çağrısı: '{name}'"
