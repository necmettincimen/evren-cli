"""
SSH host registry for EVREN CLI.

To keep remote operations safe and auditable, the agent may only connect to
hosts that the user has explicitly registered as aliases in
`~/.evren/ssh_hosts.json` (or `<workspace>/.evren/ssh_hosts.json`). Random
targets are never allowed.

Each host entry looks like:

    {
      "prod-web": {
        "host": "10.0.0.5",
        "user": "deploy",
        "port": 22,
        "identity_file": "C:\\Users\\me\\.ssh\\id_ed25519",
        "description": "Production web server",
        "allow_write": false
      }
    }

`allow_write` defaults to False so that remote writes are opt-in per host.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from src.config import get_evren_dir, get_workspace_dir


SSH_HOSTS_FILENAME = "ssh_hosts.json"


@dataclass
class SshHost:
    """A single registered SSH target."""

    alias: str
    host: str
    user: str = ""
    port: int = 22
    identity_file: str = ""
    description: str = ""
    allow_write: bool = False

    @property
    def target(self) -> str:
        """Returns the `user@host` (or just `host`) SSH target string."""
        return f"{self.user}@{self.host}" if self.user else self.host

    def to_dict(self) -> dict:
        return {
            "host": self.host,
            "user": self.user,
            "port": self.port,
            "identity_file": self.identity_file,
            "description": self.description,
            "allow_write": self.allow_write,
        }

    @classmethod
    def from_dict(cls, alias: str, data: dict) -> "SshHost":
        return cls(
            alias=alias,
            host=str(data.get("host", "")).strip(),
            user=str(data.get("user", "")).strip(),
            port=int(data.get("port", 22) or 22),
            identity_file=str(data.get("identity_file", "")).strip(),
            description=str(data.get("description", "")).strip(),
            allow_write=bool(data.get("allow_write", False)),
        )


def get_ssh_hosts_path(workspace_root: Path | None = None) -> Path:
    """Returns the path to the SSH hosts registry file.

    Prefers the workspace-local `.evren/ssh_hosts.json` when it exists,
    otherwise falls back to the user-level `~/.evren/ssh_hosts.json`.
    """
    root = workspace_root or get_workspace_dir()
    local = get_evren_dir(root) / SSH_HOSTS_FILENAME
    if local.exists():
        return local
    return Path.home() / ".evren" / SSH_HOSTS_FILENAME


def load_hosts(workspace_root: Path | None = None) -> dict[str, SshHost]:
    """Loads the registered SSH hosts.

    Returns an empty dict when the file does not exist. Raises ValueError with
    a clear message when the file exists but is malformed.
    """
    path = get_ssh_hosts_path(workspace_root)
    if not path.exists():
        return {}

    try:
        raw = path.read_text(encoding="utf-8")
    except Exception as e:
        raise ValueError(f"SSH host dosyası okunamadı ({path}): {e}")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"SSH host dosyası geçersiz JSON içeriyor ({path}): {e}")

    if not isinstance(data, dict):
        raise ValueError(f"SSH host dosyası bir nesne ({{alias: {{...}}}}) olmalı: {path}")

    hosts: dict[str, SshHost] = {}
    for alias, entry in data.items():
        if not isinstance(entry, dict):
            continue
        host = SshHost.from_dict(alias, entry)
        if host.host:
            hosts[alias] = host
    return hosts


def get_host(alias: str, workspace_root: Path | None = None) -> SshHost | None:
    """Returns the host for the given alias, or None if not registered."""
    return load_hosts(workspace_root).get(alias)


def save_hosts(hosts: dict[str, SshHost], workspace_root: Path | None = None) -> Path:
    """Writes the hosts registry back to disk (workspace-local by default)."""
    root = workspace_root or get_workspace_dir()
    evren_dir = get_evren_dir(root)
    evren_dir.mkdir(parents=True, exist_ok=True)
    path = evren_dir / SSH_HOSTS_FILENAME
    payload = {alias: h.to_dict() for alias, h in hosts.items()}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def add_host(
    alias: str,
    host: str,
    user: str = "",
    port: int = 22,
    identity_file: str = "",
    description: str = "",
    allow_write: bool = False,
    workspace_root: Path | None = None,
) -> SshHost:
    """Adds or updates a host in the workspace-local registry."""
    hosts = load_hosts(workspace_root)
    new_host = SshHost(
        alias=alias,
        host=host,
        user=user,
        port=port,
        identity_file=identity_file,
        description=description,
        allow_write=allow_write,
    )
    hosts[alias] = new_host
    save_hosts(hosts, workspace_root)
    return new_host


def remove_host(alias: str, workspace_root: Path | None = None) -> bool:
    """Removes a host from the workspace-local registry. Returns True if removed."""
    hosts = load_hosts(workspace_root)
    if alias not in hosts:
        return False
    del hosts[alias]
    save_hosts(hosts, workspace_root)
    return True
