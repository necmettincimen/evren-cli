"""
Slash-command registry and input classification.

The REPL used to treat every input starting with "/" as a command, so a pasted
build error such as `/src/Portal.Api/Portal.Api.csproj : error CS0246: ...` was
rejected with "Bilinmeyen komut". Input is a command only when its first
whitespace-delimited token is a registered command name or alias. A token that
merely looks like a command (`/cleare`) is still routed to the command handler so
it can warn and suggest the closest name; path-like tokens and multi-line
pastes are task text.

`COMMAND_ALIASES` is the single list of command names; keep it in sync with the
dispatch chain in `repl._repl_loop` (tests/test_paste_input.py checks this).
"""

from __future__ import annotations

import difflib
import re

# canonical command -> aliases. Every name here must be handled by the REPL.
COMMAND_ALIASES: dict[str, tuple[str, ...]] = {
    "/exit": ("/quit", "/q"),
    "/help": ("/h", "/?"),
    "/version": ("/ver", "/v"),
    "/model": (),
    "/mode": (),
    "/effort": (),
    "/models": (),
    "/quota": ("/status",),
    "/terms": (),
    "/keys": (),
    "/tokens": (),
    "/steps": (),
    "/docs": (),
    "/debug": (),
    "/skills": (),
    "/skill": ("/apply",),
    "/diagnose": (),
    "/template": (),
    "/memory": (),
    "/track": (),
    "/task": (),
    "/progress": (),
    "/playbook": (),
    "/resume": (),
    "/language": (),
    "/scan": (),
    "/files": (),
    "/add": (),
    "/drop": (),
    "/undo": ("/rollback",),
    "/auto": (),
    "/run": (),
    "/bg": (),
    "/todos": (),
    "/ssh": (),
    "/update": (),
    "/clear": (),
    "/continue": (),
    "/execute": (),
    "/checkpoint": (),
    "/blockers": (),
    "/check-in": (),
    "/decisions": (),
    "/commit": (),
    "/review": (),
    "/init": (),
    "/context": (),
    "/compact": (),
    "/rewind": (),
    "/doctor": (),
    "/rol": (),
}

# Legacy (Foreman-era) commands: still working, listed only under "/help legacy".
LEGACY_COMMANDS: tuple[str, ...] = (
    "/blockers", "/decisions", "/diagnose", "/playbook", "/progress", "/template",
    "/track", "/task", "/checkpoint", "/check-in",
)

# Custom commands loaded from .evren/commands/*.md (registered at runtime).
DYNAMIC_COMMANDS: set[str] = set()


def set_dynamic_commands(names) -> None:
    DYNAMIC_COMMANDS.clear()
    DYNAMIC_COMMANDS.update(n.lower() for n in names)

KNOWN_COMMANDS: frozenset[str] = frozenset(
    name
    for canonical, aliases in COMMAND_ALIASES.items()
    for name in (canonical, *aliases)
)

# A bare "/word" (letters, digits, "_" or "-"; or "/?"). Anything with another
# "/", a backslash, ".", ":" etc. after the first character is path-like text.
_COMMAND_LIKE_RE = re.compile(r"/(?:[^\W\d_]|\?)[\w-]*")

# Similarity (difflib ratio) for "/word args" to count as a mistyped command.
_NEAR_MISS_CUTOFF = 0.8


def first_token(text: str) -> str:
    """The first whitespace-delimited token of `text` ("" when blank)."""
    parts = (text or "").split(None, 1)
    return parts[0] if parts else ""


def is_command(text: str) -> bool:
    """True when the first token is a registered command name or alias."""
    token = first_token(text).lower()
    return token in KNOWN_COMMANDS or token in DYNAMIC_COMMANDS


def suggest_command(token: str, cutoff: float = 0.6) -> str | None:
    """Closest registered command for a mistyped token, e.g. /cleare -> /clear."""
    matches = difflib.get_close_matches(
        (token or "").lower(), sorted(KNOWN_COMMANDS), n=1, cutoff=cutoff,
    )
    return matches[0] if matches else None


def is_unknown_command(text: str) -> bool:
    """True for a single-line "/word ..." input that looks like a mistyped command.

    A lone `/foo` qualifies. With arguments it must also be a near miss of a
    known command (`/cleare all`); otherwise `/home is full` stays task text.
    """
    stripped = (text or "").strip()
    if not stripped or "\n" in stripped or "\r" in stripped:
        return False
    token = first_token(stripped)
    if token.lower() in KNOWN_COMMANDS or token.lower() in DYNAMIC_COMMANDS or not _COMMAND_LIKE_RE.fullmatch(token):
        return False
    return stripped == token or suggest_command(token, _NEAR_MISS_CUTOFF) is not None


def is_command_input(text: str) -> bool:
    """True when `text` must go to the slash-command handler instead of the agent.

    That is a known command, or a mistyped one (the handler warns and suggests).
    Pasted paths, build output and any multi-line text starting with "/" are
    task text.
    """
    return is_command(text) or is_unknown_command(text)


def unknown_command_message(cmd: str) -> str:
    """Turkish warning for an unknown command, with a did-you-mean hint."""
    hint = suggest_command(cmd)
    suggestion = f" Bunu mu demek istediniz: {hint}?" if hint else ""
    return f"Bilinmeyen komut: '{cmd}'.{suggestion} Komut listesi için /help yazın."
