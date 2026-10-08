"""
SSH remote operations for EVREN CLI.

Executes commands and file operations on registered remote hosts using the
system `ssh` client (no extra dependency; works with Windows 10+ OpenSSH).

Safety model:
- Only aliases registered in `~/.evren/ssh_hosts.json` may be targeted.
- `BatchMode=yes` avoids hanging on password prompts (key/agent auth only).
- `StrictHostKeyChecking=accept-new` trusts new hosts but warns on changes.
- Remote writes require the host's `allow_write` flag AND user approval.
- Every remote operation is appended to `.evren/logs/ssh_audit.log`.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

from src.config import get_evren_dir, get_workspace_dir
from src.proc_utils import run_with_tree_kill, kill_process_tree
from src.ssh_hosts import SshHost, get_host

DEFAULT_SSH_TIMEOUT = 60
MAX_SSH_TIMEOUT = 600
SSH_OUTPUT_HEAD_CHARS = 3000
SSH_OUTPUT_TAIL_CHARS = 1000


def _truncate_output(text: str) -> str:
    """Keeps the head and tail of remote output, dropping the middle."""
    if len(text) <= SSH_OUTPUT_HEAD_CHARS + SSH_OUTPUT_TAIL_CHARS:
        return text
    head = text[:SSH_OUTPUT_HEAD_CHARS]
    tail = text[-SSH_OUTPUT_TAIL_CHARS:]
    dropped = len(text) - SSH_OUTPUT_HEAD_CHARS - SSH_OUTPUT_TAIL_CHARS
    return f"{head}\n... [{dropped} karakter kırpıldı] ...\n{tail}"


def build_ssh_command(host: SshHost, remote_command: str) -> list[str]:
    """Builds the `ssh` argv for running a remote command on `host`."""
    argv = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=15",
    ]
    if host.port and host.port != 22:
        argv += ["-p", str(host.port)]
    if host.identity_file:
        argv += ["-i", host.identity_file]
    argv.append(host.target)
    argv.append(remote_command)
    return argv


def _audit_log(workspace_root: Path | None, alias: str, action: str, detail: str, code: int | None = None) -> None:
    """Appends a line to the SSH audit log (best-effort, never raises)."""
    try:
        root = workspace_root or get_workspace_dir()
        log_dir = get_evren_dir(root) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        code_str = f" exit={code}" if code is not None else ""
        line = f"[{ts}] host={alias} action={action}{code_str} :: {detail}\n"
        with (log_dir / "ssh_audit.log").open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def ssh_run(
    alias: str,
    command: str,
    timeout_seconds: int | None = None,
    workspace_root: Path | None = None,
) -> str:
    """Runs a command on a registered remote host.

    Returns a human-readable result string (mirrors `tool_run_command`).
    """
    host = get_host(alias, workspace_root)
    if host is None:
        return f"HATA: '{alias}' kayıtlı bir SSH host'u değil. Önce '/ssh add' ile tanımlayın."

    timeout = timeout_seconds or DEFAULT_SSH_TIMEOUT
    timeout = max(1, min(timeout, MAX_SSH_TIMEOUT))

    argv = build_ssh_command(host, command)
    try:
        code, out, err, timed_out = run_with_tree_kill(
            argv,
            cwd=str(workspace_root or get_workspace_dir()),
            timeout=timeout,
            shell=False,
        )
    except FileNotFoundError:
        return "HATA: 'ssh' istemcisi bulunamadı. Windows'ta OpenSSH Client özelliğini kurun."
    except Exception as e:
        return f"HATA: SSH komutu çalıştırılamadı: {e}"

    out = (out or "").strip()
    err = (err or "").strip()

    if timed_out:
        _audit_log(workspace_root, alias, "run", f"TIMEOUT: {command}", code=-1)
        return f"HATA: SSH komutu zaman aşımına uğradı ({timeout} saniye). Süreç ağacı sonlandırıldı."

    _audit_log(workspace_root, alias, "run", command, code=code)

    result_lines = [f"[{alias}] Çıkış Kodu: {code}"]
    if out:
        result_lines.append(f"STDOUT:\n{_truncate_output(out)}")
    if err:
        result_lines.append(f"STDERR:\n{_truncate_output(err)}")
    return "\n".join(result_lines)


def ssh_read_file(
    alias: str,
    remote_path: str,
    max_chars: int = 150000,
    workspace_root: Path | None = None,
) -> str:
    """Reads a remote file via `cat` (read-only)."""
    host = get_host(alias, workspace_root)
    if host is None:
        return f"HATA: '{alias}' kayıtlı bir SSH host'u değil."

    # Quote the path to avoid shell interpretation on the remote side.
    safe_path = remote_path.replace("'", "'\\''")
    argv = build_ssh_command(host, f"cat '{safe_path}'")
    try:
        code, out, err, timed_out = run_with_tree_kill(
            argv,
            cwd=str(workspace_root or get_workspace_dir()),
            timeout=DEFAULT_SSH_TIMEOUT,
            shell=False,
        )
    except FileNotFoundError:
        return "HATA: 'ssh' istemcisi bulunamadı."
    except Exception as e:
        return f"HATA: Uzak dosya okunamadı: {e}"

    if timed_out:
        _audit_log(workspace_root, alias, "read", f"TIMEOUT: {remote_path}", code=-1)
        return f"HATA: Uzak dosya okuma zaman aşımına uğradı ({remote_path})."

    _audit_log(workspace_root, alias, "read", remote_path, code=code)

    if code != 0:
        return f"HATA: Uzak dosya okunamadı ({remote_path}): {(err or '').strip()}"

    content = out or ""
    if len(content) > max_chars:
        content = content[:max_chars] + f"\n\n... [Uzak dosya {max_chars} karakterde kesildi]"
    return f"--- {alias}:{remote_path} ---\n{content}"


def ssh_write_file(
    alias: str,
    remote_path: str,
    content: str,
    workspace_root: Path | None = None,
) -> str:
    """Writes a remote file via stdin (`cat > path`).

    Requires the host's `allow_write` flag. Content is sent over stdin so it is
    never interpreted by the remote shell (avoids injection).
    """
    host = get_host(alias, workspace_root)
    if host is None:
        return f"HATA: '{alias}' kayıtlı bir SSH host'u değil."

    if not host.allow_write:
        return (
            f"HATA: '{alias}' host'unda uzak yazma kapalı (allow_write=false). "
            "Bilinçli olarak açmak için ssh_hosts.json'da allow_write=true yapın."
        )

    safe_path = remote_path.replace("'", "'\\''")
    argv = build_ssh_command(host, f"cat > '{safe_path}'")

    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(workspace_root or get_workspace_dir()),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return "HATA: 'ssh' istemcisi bulunamadı."
    except Exception as e:
        return f"HATA: Uzak dosya yazılamadı: {e}"

    try:
        out, err = proc.communicate(input=content, timeout=DEFAULT_SSH_TIMEOUT)
    except subprocess.TimeoutExpired:
        kill_process_tree(proc.pid)
        try:
            proc.communicate(timeout=5)
        except Exception:
            pass
        _audit_log(workspace_root, alias, "write", f"TIMEOUT: {remote_path}", code=-1)
        return f"HATA: Uzak dosya yazma zaman aşımına uğradı ({remote_path})."

    code = proc.returncode
    _audit_log(workspace_root, alias, "write", remote_path, code=code)

    if code != 0:
        return f"HATA: Uzak dosya yazılamadı ({remote_path}): {(err or '').strip()}"

    return f"BAŞARILI: '{alias}:{remote_path}' uzak dosyası yazıldı ({len(content)} karakter)."


def ssh_list_hosts(workspace_root: Path | None = None) -> str:
    """Lists registered SSH hosts (identity file paths masked)."""
    from src.ssh_hosts import load_hosts

    try:
        hosts = load_hosts(workspace_root)
    except ValueError as e:
        return f"HATA: {e}"

    if not hosts:
        return "Kayıtlı SSH host'u yok. Eklemek için: /ssh add <alias> <user@host> [port]"

    lines = []
    for alias, h in sorted(hosts.items()):
        write_flag = "yazma-açık" if h.allow_write else "salt-okuma"
        desc = f" — {h.description}" if h.description else ""
        lines.append(f"  • {alias:<16} {h.target}:{h.port} [{write_flag}]{desc}")
    return "Kayıtlı SSH Host'ları:\n" + "\n".join(lines)
