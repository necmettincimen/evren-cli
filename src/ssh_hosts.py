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


def user_hosts_path() -> Path:
    """The machine-wide registry `~/.evren/ssh_hosts.json`."""
    return Path.home() / ".evren" / SSH_HOSTS_FILENAME


def workspace_hosts_path(workspace_root: Path | None = None) -> Path:
    """The project registry `<workspace>/.evren/ssh_hosts.json`."""
    return get_evren_dir(workspace_root or get_workspace_dir()) / SSH_HOSTS_FILENAME


def get_ssh_hosts_path(workspace_root: Path | None = None) -> Path:
    """Returns the registry file that takes precedence for this workspace.

    Kept for callers that show "where hosts live"; lookups use `load_hosts`,
    which merges both files.
    """
    local = workspace_hosts_path(workspace_root)
    return local if local.exists() else user_hosts_path()


def load_hosts(workspace_root: Path | None = None) -> dict[str, SshHost]:
    """Loads the registered SSH hosts: machine-wide + project, project wins.

    Previously only one file was read: a project `.evren/ssh_hosts.json` hid
    every machine-wide host, so an alias registered while working in another
    directory (e.g. `koyu3-k8s`) was "not registered" here.
    Raises ValueError with a clear message when a file is malformed.
    """
    hosts: dict[str, SshHost] = {}
    seen: set[Path] = set()
    for path in (user_hosts_path(), workspace_hosts_path(workspace_root)):
        try:
            key = path.resolve()
        except OSError:
            key = path
        if key in seen:
            continue
        seen.add(key)
        hosts.update(_read_registry(path))
    return hosts


def suggest_aliases(alias: str, hosts: dict[str, SshHost], limit: int = 3) -> list[str]:
    """Registered aliases that look like `alias` (typo / missing suffix)."""
    import difflib

    wanted = alias.casefold()
    names = list(hosts)
    close = difflib.get_close_matches(wanted, [n.casefold() for n in names], n=limit, cutoff=0.6)
    picked = [n for n in names if n.casefold() in close]
    for name in names:
        low = name.casefold()
        if name not in picked and (low in wanted or wanted in low):
            picked.append(name)
    return picked[:limit]


def unknown_alias_message(alias: str, workspace_root: Path | None = None) -> str:
    """Error text for an unregistered alias, with the registered alternatives."""
    try:
        hosts = load_hosts(workspace_root)
    except ValueError as e:
        return f"HATA: {e}"
    if not hosts:
        return (
            f"HATA: '{alias}' kayıtlı bir SSH host'u değil ve hiç kayıtlı host yok. "
            "Kullanıcıdan '/ssh add <alias> <user@host>' ile eklemesini iste."
        )
    message = f"HATA: '{alias}' kayıtlı bir SSH host'u değil. Kayıtlı: {', '.join(sorted(hosts))}."
    near = suggest_aliases(alias, hosts)
    if near:
        details = "; ".join(
            f"{name} = {hosts[name].target}" + (f" ({hosts[name].description})" if hosts[name].description else "")
            for name in near
        )
        message += (
            f" Benzer: {details}. Benzer ad farklı bir makine olabilir: açıklama hedefle açıkça "
            "uyuşmuyorsa ask_user ile hangi alias olduğunu sor; uyuşuyorsa onu kullan."
        )
    return message


def _read_registry(path: Path) -> dict[str, SshHost]:
    """Reads one registry file; {} when it does not exist."""
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


def save_hosts(
    hosts: dict[str, SshHost], workspace_root: Path | None = None, *, scope: str = "workspace",
) -> Path:
    """Writes one registry file (`scope`: "workspace" or "user")."""
    path = user_hosts_path() if scope == "user" else workspace_hosts_path(workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)
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
    scope: str = "user",
) -> SshHost:
    """Adds or updates a host. Hosts are machine-wide by default (`scope="user"`)
    so the alias works from every workspace; `scope="workspace"` keeps it local."""
    path = user_hosts_path() if scope == "user" else workspace_hosts_path(workspace_root)
    hosts = _read_registry(path)
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
    save_hosts(hosts, workspace_root, scope=scope)
    return new_host


def remove_host(alias: str, workspace_root: Path | None = None) -> bool:
    """Removes a host from whichever registry holds it. Returns True if removed."""
    removed = False
    for scope in ("workspace", "user"):
        path = user_hosts_path() if scope == "user" else workspace_hosts_path(workspace_root)
        hosts = _read_registry(path)
        if alias in hosts:
            del hosts[alias]
            save_hosts(hosts, workspace_root, scope=scope)
            removed = True
    return removed
