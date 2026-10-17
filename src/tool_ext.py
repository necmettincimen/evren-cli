"""Extension tools: apply_patch dispatch, MCP tools (lazy), optional web_search."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

WEB_SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Yapılandırılmış arama ucuyla web'de arar; başlık/url/özet listesi döner.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    },
}


def extra_schemas(workspace_root) -> list[dict]:
    from src.settings import load_merged

    out = []
    if (load_merged(workspace_root).get("webSearch") or {}).get("url"):
        out.append(WEB_SEARCH_SCHEMA)
    try:
        from src.mcp_client import schemas

        out.extend(schemas(workspace_root))
    except Exception:
        pass
    return out


def web_search(query: str, workspace_root) -> str:
    from src.settings import load_merged

    cfg = load_merged(workspace_root).get("webSearch") or {}
    url = str(cfg.get("url") or "")
    if not url:
        return "HATA: webSearch.url ayarlı değil."
    url = url.replace("{q}", urllib.parse.quote(query))
    try:
        with urllib.request.urlopen(url, timeout=20) as r:  # noqa: S310
            data = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        return f"HATA: arama başarısız: {e}"
    items = data if isinstance(data, list) else (
        data.get("results") or data.get("items") or data.get("web", {}).get("results") or [])
    lines = []
    for it in items[:8]:
        if isinstance(it, dict):
            snippet = str(it.get("snippet") or it.get("content") or it.get("description") or "")[:200]
            lines.append(f"- {it.get('title', '')} | {it.get('url') or it.get('link', '')} | {snippet}")
    return "\n".join(lines) or "Sonuç yok."


def dispatch(name: str, arguments: dict, root, auto_approve: bool):
    """Returns a result string, or None when `name` is not an extension tool."""
    if name == "apply_patch":
        from src.apply_patch import tool_apply_patch

        return tool_apply_patch(arguments.get("patch", ""), root, auto_approve)
    if name == "web_search":
        return web_search(arguments.get("query", ""), root)
    if name.startswith("mcp__"):
        from src.mcp_client import call_tool

        return call_tool(name, arguments, root, auto_approve)
    return None
