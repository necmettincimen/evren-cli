"""
Tool permission gateway for EVREN CLI.

Ports the C# `ToolGateway.cs` idea. Decides whether a given tool call is
permitted under the current `AgentMode`. SSH tools are supported but gated:
`ssh_read_file`/`ssh_list_hosts` are read-only, `ssh_write_file` is a write
tool, and `ssh_run` is treated like `run_command` (read-only verbs only in
ask/plan modes).

Rules:
- normal : everything allowed.
- ask    : read-only tools allowed; write_file/edit_file denied; run_command
           allowed only for read-only verbs (allow-list).
- plan   : source-file writes denied; `create_plan` allowed; read-only tools
           allowed; run_command limited to read-only verbs.

The gateway returns (allowed, reason). Callers surface the reason to the model
so it can adapt instead of silently failing.
"""

from __future__ import annotations

import shlex

from src.modes import AgentMode

# Tools that never modify the workspace.
READ_ONLY_TOOLS = {"view_file", "list_directory", "find_files", "ssh_list_hosts", "ssh_read_file"}

# Tools that write to source files (local or remote).
WRITE_TOOLS = {"write_file", "edit_file", "ssh_write_file"}

# The plan-writing tool (writes only under plans/<name>/plan.md).
PLAN_TOOLS = {"create_plan"}

# Read-only command verbs allowed in ask/plan modes.
READ_ONLY_COMMAND_VERBS = {
    # listing / inspection
    "ls", "dir", "cat", "type", "head", "tail", "less", "more",
    "find", "findstr", "grep", "rg", "select-string",
    "git",  # further restricted below to read-only subcommands
    "pwd", "cd", "echo", "whoami", "hostname",
    "python", "python3", "py",  # allowed only with read-only-ish flags? see note
    "wc", "sort", "uniq", "diff", "tree",
    "get-childitem", "get-content", "get-item", "test-path",
    "where", "which", "stat", "file",
    # remote/system inspection (read-only status checks)
    "uptime", "df", "free", "ps", "top", "uname", "id", "date",
    "env", "netstat", "ss", "ip", "ifconfig", "journalctl",
}

# git subcommands that are read-only.
READ_ONLY_GIT_SUBCOMMANDS = {
    "status", "log", "diff", "show", "branch", "remote", "describe",
    "rev-parse", "ls-files", "blame", "shortlog", "tag",
}

# Shell metacharacters that imply writes/pipelines we do not allow in ask/plan.
SHELL_WRITE_MARKERS = (">", ">>", "|", "&&", "||", ";", "`", "$(")


def _first_token(command: str) -> str:
    """Returns the first token (program/verb) of a command, lowercased."""
    try:
        parts = shlex.split(command, posix=False)
    except ValueError:
        parts = command.split()
    if not parts:
        return ""
    token = parts[0].strip().strip('"').strip("'")
    # Strip a path prefix (e.g. C:\Windows\System32\where.exe -> where).
    token = token.replace("\\", "/").split("/")[-1]
    if token.lower().endswith(".exe"):
        token = token[:-4]
    return token.lower()


def _second_token(command: str) -> str:
    try:
        parts = shlex.split(command, posix=False)
    except ValueError:
        parts = command.split()
    if len(parts) < 2:
        return ""
    return parts[1].strip().strip('"').strip("'").lower()


def is_read_only_command(command: str) -> tuple[bool, str]:
    """Checks whether a shell command is read-only (safe for ask/plan modes)."""
    if not command or not command.strip():
        return False, "Boş komut."

    # Reject shell metacharacters that could chain writes.
    for marker in SHELL_WRITE_MARKERS:
        if marker in command:
            return False, f"Komut '{marker}' içeriyor; ask/plan modunda yalnızca basit salt-okuma komutlarına izin verilir."

    verb = _first_token(command)
    if verb not in READ_ONLY_COMMAND_VERBS:
        return False, f"'{verb}' komutu salt-okuma listesinde değil."

    # git needs a read-only subcommand.
    if verb == "git":
        sub = _second_token(command)
        if sub not in READ_ONLY_GIT_SUBCOMMANDS:
            return False, f"'git {sub}' salt-okuma alt komutu değil."

    # python/py: only allow clearly read-only invocations (e.g. --version, -m pytest is NOT read-only).
    if verb in ("python", "python3", "py"):
        sub = _second_token(command)
        if sub not in ("--version", "-v", "-c"):
            return False, "ask/plan modunda python yalnızca --version veya -c ile sınırlıdır."

    return True, ""


def check_tool_allowed(tool_name: str, arguments: dict, mode: AgentMode) -> tuple[bool, str]:
    """Returns (allowed, reason) for a tool call under the given mode."""
    if mode == AgentMode.NORMAL:
        return True, ""

    # Read-only tools are always allowed.
    if tool_name in READ_ONLY_TOOLS:
        return True, ""

    if mode == AgentMode.ASK:
        if tool_name in WRITE_TOOLS:
            return False, f"'{tool_name}' ask modunda devre dışı (salt-okuma). Yazmak için normal moda geçin."
        if tool_name in PLAN_TOOLS:
            return False, "'create_plan' ask modunda devre dışı. Plan moduna geçin."
        if tool_name in ("run_command", "ssh_run"):
            ok, reason = is_read_only_command(arguments.get("command", ""))
            if not ok:
                return False, f"ask modunda komut reddedildi: {reason}"
            return True, ""
        return False, f"'{tool_name}' ask modunda izinli değil."

    if mode == AgentMode.PLAN:
        if tool_name in WRITE_TOOLS:
            return False, f"'{tool_name}' plan modunda devre dışı (kaynak dosyalara dokunulmaz). Uygulamak için normal moda geçin."
        if tool_name in PLAN_TOOLS:
            return True, ""
        if tool_name in ("run_command", "ssh_run"):
            ok, reason = is_read_only_command(arguments.get("command", ""))
            if not ok:
                return False, f"plan modunda komut reddedildi: {reason}"
            return True, ""
        return False, f"'{tool_name}' plan modunda izinli değil."

    return True, ""
