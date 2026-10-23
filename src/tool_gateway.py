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

import re
import shlex

from src.modes import AgentMode

# Tools that never modify the workspace. `ask_user` only talks to the user; it is
# advertised in every mode, so denying it in ask/plan made the model loop on
# "REDDEDİLDİ" while the prompt told it to ask.
# `todo_write` only mutates the in-memory session checklist (never workspace
# files), so it is treated like `ask_user`: allowed in every mode.
from src.tool_registry import READ_ONLY as _REG_RO, WRITE as _REG_WRITE
READ_ONLY_TOOLS = set(_REG_RO)

# Tools that write to source files (local or remote).
WRITE_TOOLS = set(_REG_WRITE)

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
    # Searches in tracked files: the fastest read-only search, used by most agents.
    "grep", "cat-file", "ls-tree", "merge-base", "reflog",
}

# Shell metacharacters that imply writes/pipelines we do not allow in ask/plan.
SHELL_WRITE_MARKERS = (">", ">>", "|", "&&", "||", ";", "`", "$(")
# Command substitution executes even inside double quotes: always refused.
_SUBSHELL_MARKERS = ("`", "$(", "<(")
# Never allowed outside quotes, not even inside a chain of read-only parts.
_NEVER_READ_ONLY_MARKERS = (">", "\n")
_QUOTED_RE = re.compile(r"\"[^\"]*\"|'[^']*'")
# Chain separators between independently checked parts (outside quotes).
_CHAIN_SEP_RE = re.compile(r"&&|\|\||;|\|")


def _split_chain(command: str) -> list[str]:
    """Splits on && || ; | outside single/double quotes (`grep -E "a|b"` stays one part)."""
    parts, buf, quote, i = [], [], "", 0
    while i < len(command):
        ch = command[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        sep = _CHAIN_SEP_RE.match(command, i)
        if sep:
            parts.append("".join(buf))
            buf = []
            i = sep.end()
            continue
        buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


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


def _git_subcommand(command: str) -> tuple[str, list[str]]:
    """`git [-C dir] [-c k=v] [--no-pager] <sub> args...` -> (sub, args)."""
    try:
        parts = shlex.split(command, posix=False)
    except ValueError:
        parts = command.split()
    i = 1
    while i < len(parts):
        tok = parts[i].strip('"').strip("'")
        if tok in ("-C", "-c", "--git-dir", "--work-tree"):
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        return tok.lower(), [p.strip('"').strip("'") for p in parts[i + 1:]]
    return "", []


# Subcommands that list by default but change refs/config with arguments.
_GIT_WRITE_FLAGS = {
    "branch": {"-d", "-D", "-m", "-M", "-c", "-C", "-f", "--delete", "--move", "--copy", "--force",
               "-u", "--set-upstream-to", "--unset-upstream", "--edit-description"},
    "tag": {"-d", "--delete", "-a", "-s", "-u", "-f", "--force", "-m", "-F", "--annotate", "--sign"},
    "reflog": {"expire", "delete"},
}
_GIT_LIST_FLAGS = {
    "branch": {"-a", "-r", "-v", "-vv", "--all", "--remotes", "--list", "-l", "--contains", "--merged",
               "--no-merged", "--show-current", "--points-at", "--sort", "--format", "--no-color", "--color"},
    "tag": {"-l", "--list", "-n", "--contains", "--merged", "--no-merged", "--points-at", "--sort", "--format"},
}


def _git_listing_only(sub: str, args: list[str]) -> str:
    """Refuses `git branch -D x`, `git tag v1`, `git remote add ...` in read-only modes."""
    if any(a.startswith("--output") for a in args):
        return f"'git {sub} --output' dosya yazar; ask/plan modunda kullanılamaz."
    if sub in _GIT_WRITE_FLAGS and any(a.split("=")[0] in _GIT_WRITE_FLAGS[sub] for a in args):
        return f"'git {sub}' bu bayrakla değişiklik yapar; ask/plan modunda yalnız listeleme."
    if sub in ("branch", "tag"):
        listing = any(a.split("=")[0] in _GIT_LIST_FLAGS[sub] for a in args)
        positional = [a for a in args if not a.startswith("-")]
        if positional and not listing:
            return f"'git {sub} <ad>' yeni ref oluşturur; ask/plan modunda yalnız listeleme."
    if sub == "remote" and args and args[0] not in ("-v", "--verbose", "show", "get-url"):
        return "'git remote' yalnız listeleme (-v, show, get-url) ile kullanılabilir."
    return ""


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
    # Models may send non-string values here; normalize instead of crashing.
    if command is None:
        command = ""
    elif not isinstance(command, str):
        command = str(command)

    if not command or not command.strip():
        return False, "Boş komut."

    # `git log ...; echo ---; git show ...` or `grep ... | head`: a chain is
    # read-only when every part is (planners lost most of their 4 steps to
    # refused chains). Redirects, subshells and backticks stay refused.
    for marker in _SUBSHELL_MARKERS:
        if marker in command:  # runs even inside double quotes
            return False, f"Komut '{marker}' içeriyor; ask/plan modunda alt kabuk çalıştırılamaz."
    if not any(m in _QUOTED_RE.sub("", command) for m in _NEVER_READ_ONLY_MARKERS):
        parts = _split_chain(command)
        if len(parts) > 1:
            for part in parts:
                ok, reason = is_read_only_command(part)
                if not ok:
                    return False, reason
            return True, ""

    # Reject shell metacharacters that could chain writes (quoted text such as
    # a grep pattern "a|b" is data, not shell syntax).
    bare = _QUOTED_RE.sub("", command)
    for marker in SHELL_WRITE_MARKERS:
        if marker in bare:
            return False, f"Komut '{marker}' içeriyor; ask/plan modunda yalnızca basit salt-okuma komutlarına izin verilir."

    verb = _first_token(command)
    if verb not in READ_ONLY_COMMAND_VERBS:
        return False, f"'{verb}' komutu salt-okuma listesinde değil."

    # git needs a read-only subcommand.
    if verb == "git":
        sub, rest = _git_subcommand(command)
        if sub not in READ_ONLY_GIT_SUBCOMMANDS:
            return False, f"'git {sub}' salt-okuma alt komutu değil."
        why = _git_listing_only(sub, rest)
        if why:
            return False, why

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

    # Delegation spawns a sub-agent session; read-only modes must not fork one.
    # The sub-agent itself runs in ASK, so it can never re-enter delegate.
    if tool_name == "delegate":
        return False, "'delegate' yalnızca normal modda kullanılabilir."

    if tool_name == "ssh_run" and (arguments or {}).get("preset"):
        from src.remote_presets import READ_ONLY_PRESETS

        if arguments["preset"] in READ_ONLY_PRESETS:
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
            if tool_name == "run_command" and (arguments or {}).get("run_in_background") is True:
                return False, "ask modunda arka plan komutu başlatılamaz (run_in_background). Normal moda geçin."
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
            if tool_name == "run_command" and (arguments or {}).get("run_in_background") is True:
                return False, "plan modunda arka plan komutu başlatılamaz (run_in_background). Normal moda geçin."
            ok, reason = is_read_only_command(arguments.get("command", ""))
            if not ok:
                return False, f"plan modunda komut reddedildi: {reason}"
            return True, ""
        return False, f"'{tool_name}' plan modunda izinli değil."

    return True, ""
