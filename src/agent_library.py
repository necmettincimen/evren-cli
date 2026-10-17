"""Agent library (Faz 9): markdown agent definitions with front-matter.

Load order (later overrides earlier by name): built-in `src/agents/*.md`,
user `<cli_home>/agents/`, project `<workspace>/.evren/agents/`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from src.ui import print_warning

BUILTIN_DIR = Path(__file__).resolve().parent / "agents"
SOURCES = ("yerleşik", "kullanıcı", "proje")
_NAME_RE = re.compile(r"^[A-Za-z0-9][\w-]*$")


@dataclass
class AgentDef:
    name: str
    description: str = ""
    tools: list[str] | None = None
    model: str = ""
    thinking: bool = False
    max_steps: int = 15
    prompt: str = ""
    source: str = "yerleşik"
    path: str = ""


def _scalar(value: str):
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        return [p.strip().strip("'\"") for p in inner.split(",") if p.strip()]
    low = value.lower()
    if low in ("true", "evet"):
        return True
    if low in ("false", "hayır", "hayir"):
        return False
    return value.strip("'\"")


def parse_agent_text(text: str) -> tuple[dict, str]:
    """Splits `---` front-matter (simple YAML subset) from the body."""
    text = text.lstrip("﻿").replace("\r\n", "\n")
    if not text.startswith("---"):
        raise ValueError("front-matter yok")
    parts = text.split("\n---", 1)
    if len(parts) < 2:
        raise ValueError("front-matter kapanmıyor")
    meta: dict = {}
    for line in parts[0].splitlines()[1:]:
        line = re.sub(r"\s+#.*$", "", line).rstrip()
        if not line.strip():
            continue
        if ":" not in line:
            raise ValueError(f"geçersiz satır: {line!r}")
        key, _, val = line.partition(":")
        meta[key.strip().lower()] = _scalar(val)
    body = parts[1].split("\n", 1)[1] if "\n" in parts[1] else ""
    return meta, body.strip()


def parse_agent_file(path: Path, source: str) -> AgentDef:
    meta, body = parse_agent_text(path.read_text(encoding="utf-8"))
    name = str(meta.get("name") or path.stem).strip()
    if not _NAME_RE.match(name):
        raise ValueError(f"geçersiz ad: {name!r}")
    tools = meta.get("tools")
    if isinstance(tools, str):
        tools = [t.strip() for t in tools.split(",") if t.strip()] or None
    try:
        steps = int(meta.get("max_steps") or 15)
    except (TypeError, ValueError):
        raise ValueError("max_steps sayı olmalı")
    if not body:
        raise ValueError("talimat gövdesi boş")
    return AgentDef(
        name=name, description=str(meta.get("description") or ""),
        tools=tools if isinstance(tools, list) else None,
        model=str(meta.get("model") or ""), thinking=meta.get("thinking") is True,
        max_steps=max(1, min(60, steps)), prompt=body, source=source, path=str(path),
    )


def agent_dirs(workspace_root) -> list[tuple[str, Path]]:
    dirs: list[tuple[str, Path]] = [("yerleşik", BUILTIN_DIR)]
    try:
        from src.cli_home import cli_docs_dir
        dirs.append(("kullanıcı", cli_docs_dir() / "agents"))
    except Exception:
        pass
    if workspace_root:
        dirs.append(("proje", Path(workspace_root) / ".evren" / "agents"))
    return dirs


_AGENTS_CACHE: dict[str, tuple[tuple, dict]] = {}


def load_agents(workspace_root=None) -> dict[str, AgentDef]:
    """All agent definitions; re-parsed only when an agents/*.md file changes."""
    from src.file_cache import dir_stamp

    dirs = agent_dirs(workspace_root)
    stamp = tuple((str(d), dir_stamp(d)) for _, d in dirs)
    key = str(workspace_root)
    hit = _AGENTS_CACHE.get(key)
    if hit and hit[0] == stamp:
        return dict(hit[1])
    agents = _load_agents_uncached(dirs)
    _AGENTS_CACHE[key] = (stamp, agents)
    return dict(agents)


def _load_agents_uncached(dirs) -> dict[str, AgentDef]:
    agents: dict[str, AgentDef] = {}
    for source, directory in dirs:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            try:
                agent = parse_agent_file(path, source)
            except (OSError, ValueError) as e:
                print_warning(f"Ajan dosyası atlandı ({path.name}): {e}")
                continue
            agents[agent.name] = agent
    return agents


def get_agent(name: str, workspace_root=None) -> AgentDef | None:
    return load_agents(workspace_root).get((name or "").strip().lstrip("@"))


AGENT_TEMPLATE = """---
name: {name}
description: Bu ajanın görevi (tek satır)
tools: [view_file, find_files, list_directory, run_command, todo_write]
model:
thinking: false
max_steps: 15
---
Sen {name} ajanısın. Rolünü ve çalışma kurallarını burada yaz.
"""


def active_agent(workspace_root) -> AgentDef | None:
    from src.extras import load_settings
    name = str(load_settings(workspace_root).get("agent") or "").strip()
    return get_agent(name, workspace_root) if name else None


def agent_context_block(workspace_root) -> str:
    """System block for the session's main agent ('' when unset)."""
    agent = active_agent(workspace_root)
    return f"ANA AJAN: {agent.name}\n{agent.prompt}" if agent else ""


def handle_agent(arg: str, session) -> None:
    from src.extras import load_settings, save_settings
    from src.ui import print_error, print_info, print_success
    root = session.workspace_root
    arg = (arg or "").strip()
    settings = load_settings(root)
    if not arg:
        cur = settings.get("agent")
        print_info(f"Ana ajan: {cur}" if cur else "Ana ajan ayarlanmamış. Ayarlamak için: /agent <ad>")
        return
    if arg.lower() in ("kapat", "off", "yok"):
        settings.pop("agent", None)
        save_settings(root, settings)
        session.refresh_context()
        print_success("Ana ajan kapatıldı.")
        return
    if not get_agent(arg, root):
        print_error(f"Ajan bulunamadı: {arg}. Liste için /agents")
        return
    settings["agent"] = arg.lstrip("@")
    save_settings(root, settings)
    session.refresh_context()
    print_success(f"Ana ajan: {settings['agent']}")


def handle_agents(arg: str, session) -> None:
    from src.ui import print_error, print_info, print_success
    root = session.workspace_root
    parts = (arg or "").split()
    sub = parts[0].lower() if parts else "list"
    if sub in ("list", "liste"):
        agents = load_agents(root)
        for source in SOURCES:
            group = [a for a in agents.values() if a.source == source]
            if not group:
                continue
            print_info(f"[{source}]")
            for a in sorted(group, key=lambda x: x.name):
                print(f"  {a.name:<14} {a.description}")
    elif sub in ("show", "göster", "goster") and len(parts) > 1:
        a = get_agent(parts[1], root)
        if not a:
            print_error(f"Ajan bulunamadı: {parts[1]}")
            return
        print_info(f"{a.name} [{a.source}] model={a.model or 'oturum'} adım={a.max_steps}")
        print(f"Araçlar: {', '.join(a.tools) if a.tools else 'tümü'}\n{a.prompt}")
    elif sub in ("new", "yeni") and len(parts) > 1:
        name = parts[1]
        if not _NAME_RE.match(name):
            print_error("Geçersiz ajan adı.")
            return
        path = Path(root) / ".evren" / "agents" / f"{name}.md"
        if path.exists():
            print_error(f"Zaten var: {path}")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(AGENT_TEMPLATE.format(name=name), encoding="utf-8")
        print_success(f"Şablon oluşturuldu: {path}")
    elif sub in ("reload", "yenile"):
        print_success(f"{len(load_agents(root))} ajan yüklendi.")
        session.refresh_context()
    else:
        print_info("Kullanım: /agents [show <ad> | new <ad> | reload]")


def run_with_agent(session, name: str, task: str, run_agent) -> bool:
    """Runs `task` once with agent `name` as temporary main agent."""
    from src.extras import load_settings, save_settings
    from src.ui import print_error
    root = session.workspace_root
    if not get_agent(name, root):
        print_error(f"Ajan bulunamadı: {name}")
        return False
    settings = load_settings(root)
    previous = settings.get("agent")
    settings["agent"] = name
    save_settings(root, settings)
    session.refresh_context()
    try:
        run_agent(session, task, compact=True)
    finally:
        s2 = load_settings(root)
        if previous:
            s2["agent"] = previous
        else:
            s2.pop("agent", None)
        save_settings(root, s2)
        session.refresh_context()
    return True


def handle_team(task: str, session, run_agent) -> None:
    """Sequential product-owner -> fullstack -> tester pipeline."""
    from src.ui import print_info, print_warning
    task = (task or "").strip()
    if not task:
        print_warning("Kullanım: /team <görev>")
        return
    steps = [
        ("product-owner", "Şu görevi analiz et; kabul kriterleri ve kısa bir uygulama planı üret (kod değiştirme):\n{t}"),
        ("fullstack", "Görev: {t}\nPlanı uygula ve değişikliği doğrula. Son yanıtın kısa bir özet olsun."),
        ("tester", "Görev: {t}\nYapılan değişikliği test et/doğrula ve sonucu kısa raporla."),
    ]
    summary = ""
    for name, tpl in steps:
        print_info(f"/team adımı: {name}")
        prompt = tpl.format(t=task)
        if summary:
            prompt += f"\n\nÖNCEKİ ADIM ÖZETİ:\n{summary[:1500]}"
        if not run_with_agent(session, name, prompt, run_agent):
            return
        last = next((m for m in reversed(session.messages)
                     if m.get("role") == "assistant" and m.get("content")), None)
        summary = str(last.get("content")) if last else ""
