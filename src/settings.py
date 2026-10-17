"""
Layered settings, permission rules and shell hooks (plan Faz 8).

Layers (later wins / lists concatenate): ~/.evren/settings.json,
<ws>/.evren/settings.json, <ws>/.evren/settings.local.json.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

_WS: Path | None = None
EXTRA_ALLOW: list[str] = []  # CLI --allowed-tools (tool names)
_CURRENT: tuple[str, dict] | None = None  # tool call awaiting an approval prompt


def set_workspace(root) -> None:
    global _WS
    _WS = Path(root)


def _home_file() -> Path:
    try:
        from src.cli_home import cli_docs_dir

        base = Path(cli_docs_dir()).parent
        if (base / "settings.json").exists():
            return base / "settings.json"
    except Exception:
        pass
    return Path.home() / ".evren" / "settings.json"


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _merge(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        elif isinstance(v, list) and isinstance(out.get(k), list):
            out[k] = out[k] + [x for x in v if x not in out[k]]
        else:
            out[k] = v
    return out


def load_merged(workspace_root=None) -> dict:
    ws = Path(workspace_root) if workspace_root else _WS
    data = _read(_home_file())
    if ws:
        data = _merge(data, _read(ws / ".evren" / "settings.json"))
        data = _merge(data, _read(ws / ".evren" / "settings.local.json"))
    return data


# ---------------------------------------------------------------- permissions

_RULE_RE = re.compile(r"^\s*([\w\-]+)\s*(?:\((.*)\))?\s*$", re.S)


def _subject(tool: str, args: dict) -> str:
    if tool == "ssh_run":
        cmd = args.get("command") or ("preset:" + str(args.get("preset") or ""))
        return f"{args.get('alias', '')}:{cmd}"
    if tool in ("ssh_write_file", "ssh_read_file"):
        return f"{args.get('alias', '')}:{args.get('remote_path', '')}"
    for key in ("command", "path", "url", "name", "query", "q"):
        if args.get(key):
            return str(args[key])
    return ""


def _rule_matches(rule: str, tool: str, args: dict) -> bool:
    m = _RULE_RE.match(str(rule))
    if not m or m.group(1) != tool:
        return False
    pat = m.group(2)
    if pat is None or pat.strip() in ("", "*"):
        return True
    pat = pat.strip()
    if tool == "ssh_run":
        pat = re.sub(r"\s*,\s*", ":", pat, count=1)
    subj = _subject(tool, args).replace("\\", "/")
    return fnmatch.fnmatchcase(subj, pat.replace("\\", "/"))


def check_permission(tool: str, args: dict, workspace_root=None) -> str | None:
    """Returns "deny", "allow" or None (ask the user). Deny wins."""
    perms = load_merged(workspace_root).get("permissions") or {}
    args = args or {}
    if tool in EXTRA_ALLOW:
        return "allow"
    for rule in perms.get("deny") or []:
        if _rule_matches(rule, tool, args):
            return "deny"
    for rule in perms.get("allow") or []:
        if _rule_matches(rule, tool, args):
            return "allow"
    return None


def rule_for(tool: str, args: dict) -> str:
    subj = _subject(tool, args or {})
    if not subj:
        return tool
    if tool == "run_command":
        words = subj.split()
        subj = (" ".join(words[:2]) if len(words) > 1 else subj) + "*"
        if len(words) > 1 and words[0] not in ("git", "npm", "kubectl", "docker", "pip", "python"):
            subj = words[0] + " *"
    elif tool == "ssh_run":
        alias, _, cmd = subj.partition(":")
        w = cmd.split()
        subj = f"{alias}:{' '.join(w[:2])}*"
    return f"{tool}({subj})"


def add_allow_rule(rule: str, workspace_root=None) -> None:
    ws = Path(workspace_root) if workspace_root else _WS
    if not ws:
        return
    path = ws / ".evren" / "settings.local.json"
    data = _read(path)
    allow = data.setdefault("permissions", {}).setdefault("allow", [])
    if rule not in allow:
        allow.append(rule)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def set_current(tool: str | None, args: dict | None = None) -> None:
    global _CURRENT
    _CURRENT = (tool, args or {}) if tool else None


def current_call() -> tuple[str, dict] | None:
    return _CURRENT


def remember_current() -> str | None:
    """Called by prompt_confirm on the 'a' answer; stores an allow rule."""
    if not _CURRENT:
        return None
    rule = rule_for(*_CURRENT)
    add_allow_rule(rule)
    return rule


# ---------------------------------------------------------------------- hooks


def _masked_args(args: dict) -> str:
    try:
        from src.secret_vault import active_vault

        return active_vault().mask(json.dumps(args, ensure_ascii=False))[:8000]
    except Exception:
        return "{}"


def _hooks(kind: str, tool: str, workspace_root=None) -> list[str]:
    out = []
    for h in (load_merged(workspace_root).get("hooks") or {}).get(kind) or []:
        if not isinstance(h, dict) or not h.get("command"):
            continue
        matcher = str(h.get("matcher") or "*")
        if matcher == "*" or fnmatch.fnmatchcase(tool, matcher) or re.fullmatch(matcher, tool or ""):
            out.append(str(h["command"]))
    return out


def _run_hook(cmd: str, env_extra: dict, workspace_root) -> tuple[int, str]:
    env = {**os.environ, **env_extra}
    try:
        p = subprocess.run(
            cmd, shell=True, cwd=str(workspace_root or _WS or "."), env=env,
            capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace",
        )
        return p.returncode, (p.stdout or p.stderr or "").strip()
    except Exception as e:
        return 0, f"hook hatası: {e}"


def run_pre_tool(tool: str, args: dict, workspace_root=None) -> str | None:
    """Returns a block reason when a pre_tool hook exits non-zero, else None."""
    env = {"EVREN_TOOL_NAME": tool, "EVREN_TOOL_ARGS": _masked_args(args)}
    for cmd in _hooks("pre_tool", tool, workspace_root):
        code, out = _run_hook(cmd, env, workspace_root)
        if code != 0:
            return out or f"pre_tool hook engelledi (çıkış {code})"
    return None


def run_post_tool(tool: str, args: dict, workspace_root=None) -> None:
    env = {"EVREN_TOOL_NAME": tool, "EVREN_TOOL_ARGS": _masked_args(args)}
    for cmd in _hooks("post_tool", tool, workspace_root):
        _run_hook(cmd, env, workspace_root)


def run_stop(workspace_root=None) -> None:
    for cmd in _hooks("stop", "", workspace_root):
        _run_hook(cmd, {"EVREN_TOOL_NAME": "", "EVREN_TOOL_ARGS": "{}"}, workspace_root)
