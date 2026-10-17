"""Minimal stdio MCP client (JSON-RPC 2.0, newline-delimited): initialize, tools/list, tools/call."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading

PROTOCOL = "2024-11-05"


class McpServer:
    def __init__(self, name: str, command: str, args=None, env=None, cwd=None):
        self.name, self.command, self.args = name, command, list(args or [])
        self.env, self.cwd = dict(env or {}), cwd
        self.proc: subprocess.Popen | None = None
        self._id = 0
        self._q: queue.Queue = queue.Queue()
        self.tools: list[dict] = []

    def start(self) -> None:
        if self.proc and self.proc.poll() is None:
            return
        self.proc = subprocess.Popen(
            [self.command, *self.args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, cwd=self.cwd, env={**os.environ, **self.env},
            text=True, encoding="utf-8", bufsize=1, shell=(os.name == "nt"),
        )
        threading.Thread(target=self._reader, daemon=True).start()
        self._request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                     "clientInfo": {"name": "evren", "version": "1"}})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.tools = self._request("tools/list", {}).get("tools", [])

    def _reader(self) -> None:
        for line in self.proc.stdout:
            try:
                self._q.put(json.loads(line))
            except ValueError:
                continue

    def _send(self, msg: dict) -> None:
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def _request(self, method: str, params: dict, timeout: float = 60) -> dict:
        self._id += 1
        rid = self._id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        while True:
            try:
                msg = self._q.get(timeout=timeout)
            except queue.Empty:
                raise TimeoutError(f"MCP '{self.name}' yanıt vermedi ({method})")
            if msg.get("id") == rid:
                if "error" in msg:
                    raise RuntimeError(str(msg["error"].get("message", msg["error"])))
                return msg.get("result") or {}

    def call(self, tool: str, arguments: dict) -> str:
        self.start()
        res = self._request("tools/call", {"name": tool, "arguments": arguments}, timeout=120)
        text = "\n".join(c.get("text", "") for c in res.get("content", []) if c.get("type") == "text")
        return ("HATA: " if res.get("isError") else "") + (text or json.dumps(res.get("content", []))[:4000])

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


_SERVERS: dict[str, McpServer] = {}


def configured(workspace_root) -> dict:
    from src.settings import load_merged

    cfg = load_merged(workspace_root).get("mcpServers") or {}
    return cfg if isinstance(cfg, dict) else {}


def get_server(name: str, workspace_root) -> McpServer | None:
    cfg = configured(workspace_root).get(name)
    if not isinstance(cfg, dict) or not cfg.get("command"):
        return None
    if name not in _SERVERS:
        _SERVERS[name] = McpServer(name, cfg["command"], cfg.get("args"), cfg.get("env"), str(workspace_root))
    return _SERVERS[name]


def schemas(workspace_root) -> list[dict]:
    """Tool schemas for all configured servers (servers start lazily on first listing)."""
    out = []
    for name in configured(workspace_root):
        srv = get_server(name, workspace_root)
        if not srv:
            continue
        try:
            srv.start()
        except Exception:
            continue
        for t in srv.tools:
            out.append({"type": "function", "function": {
                "name": f"mcp__{name}__{t['name']}",
                "description": (t.get("description") or "")[:300] + f" (MCP: {name})",
                "parameters": t.get("inputSchema") or {"type": "object", "properties": {}},
            }})
    return out


def call_tool(full_name: str, arguments: dict, workspace_root, auto_approve: bool) -> str:
    from src.config import EVREN_AUTO_APPROVE
    from src.ui import prompt_confirm

    _, server, tool = full_name.split("__", 2)
    srv = get_server(server, workspace_root)
    if not srv:
        return f"HATA: MCP sunucusu tanımlı değil: {server}"
    if not (auto_approve or EVREN_AUTO_APPROVE):
        if not prompt_confirm(f"MCP aracı '{full_name}' çalıştırılsın mı?", default=False):
            return "İPTAL EDİLDİ: Kullanıcı MCP aracını onaylamadı."
    try:
        return srv.call(tool, arguments)
    except Exception as e:
        return f"HATA: MCP çağrısı başarısız: {e}"


def shutdown() -> None:
    for s in _SERVERS.values():
        s.stop()
