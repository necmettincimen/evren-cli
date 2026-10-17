"""
Rich terminal UI components and formatters for EVREN CLI.
Provides EVREN's terminal aesthetics, markdown rendering,
tool execution banners, reasoning stream blocks, and diffs.
"""

import re
import sys
from pathlib import Path
from src.config import get_ui_theme

FORM_PALETTES = {
    "light": {"question": "#005a9c", "suggestion": "#146b32", "decision": "#8a4400"},
    "dark": {"question": "#66d9ef", "suggestion": "#8fdf8f", "decision": "#ffd166"},
}


def form_palette() -> dict[str, str]:
    """Colors for form borders and labels on the user's selected terminal theme."""
    return FORM_PALETTES[get_ui_theme()]


try:
    from rich.console import Console
    from rich.markup import escape
    from rich.panel import Panel
    from rich.markdown import Markdown
    from rich.table import Table
    from rich.syntax import Syntax
    from rich.text import Text
    from rich.theme import Theme
    HAVE_RICH = True
except ImportError:
    HAVE_RICH = False

    def escape(text: str) -> str:
        return text

custom_theme = None
if HAVE_RICH:
    custom_theme = Theme({
        "info": "cyan",
        "warning": "yellow",
        "error": "bold red",
        "success": "bold green",
        "reasoning": "italic magenta",
        "tool": "bold blue",
        "highlight": "bold cyan",
        "subtle": "dim white",
    })
    console = Console(theme=custom_theme)
else:
    console = None


_ACTIVE_STATUS = None


class StatusTracker:
    """Manages an animated loading spinner with dynamic text updates."""

    def __init__(self, message: str = "İşlem yapılıyor..."):
        self.message = message
        self._status = None

    def __enter__(self):
        global _ACTIVE_STATUS
        _ACTIVE_STATUS = self
        if HAVE_RICH and console:
            self._status = console.status(f"[bold cyan]✦ {escape(str(self.message))}[/bold cyan]", spinner="dots")
            self._status.start()
        else:
            print(f"[*] {self.message}...", end="", flush=True)
        return self

    def update(self, new_message: str):
        self.message = new_message
        if self._status:
            self._status.update(f"[bold cyan]✦ {escape(str(new_message))}[/bold cyan]")
        else:
            print(f"\n[*] {new_message}...", end="", flush=True)

    def stop(self):
        global _ACTIVE_STATUS
        if _ACTIVE_STATUS is self:
            _ACTIVE_STATUS = None
        if self._status:
            try:
                self._status.stop()
            except Exception:
                pass
            self._status = None

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()


def status_spinner(message: str = "İşlem yapılıyor..."):
    """Context manager that displays a live animated progress spinner."""
    return StatusTracker(message)


def _shorten_path(path: str, limit: int = 60) -> str:
    """Compact a path for the banner: home directory as ~, long tails clipped.

    Full workspace and config paths used to wrap inside the banner panel and
    break its layout; the shortened form keeps every line on one row.
    """
    text = str(path or "").strip()
    if not text:
        return text
    try:
        home = str(Path.home())
        if text == home:
            text = "~"
        elif text.lower().startswith(home.lower() + ("\\" if "\\" in text else "/")):
            text = "~" + text[len(home):]
    except Exception:
        pass
    if len(text) > limit:
        text = "…" + text[-(limit - 1):]
    return text


def _short_name(path: str) -> str:
    """Bare file name for config paths (e.g. 'C:\\...\\.env' -> '.env').

    Banner config paths never wrapped even as '~/.env', so they are shown as
    file names only; the full paths remain available via /config-style output.
    """
    text = str(path or "").strip()
    if not text:
        return text
    return text.replace("/", "\\").rstrip("\\").split("\\")[-1] or text


_MD_BOLD_RE = None


def _clean_release_note(note: str, limit: int = 56) -> str:
    """Strip raw markdown markers and fit a release note on a single line."""
    text = strip_markdown(str(note))
    text = re.sub(r"\s+", " ", text).strip().lstrip("#-• ").strip()
    if len(text) > limit:
        cut = text[:limit]
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        text = cut.rstrip(" ,;:.·") + "…"
    return text


def _config_line(config_info: dict) -> str:
    """Single-line config source summary (file names only; never key values).

    When the API keys come from the same .env that is already printed, the key
    source is written as just ".env" instead of repeating the full path.
    """
    env_file = str(config_info.get("env_file") or "")
    key_source = str(config_info.get("key_source", ""))
    if "(" in key_source:
        key_source = key_source.split("(", 1)[0].strip()
    if env_file and key_source.lower() == env_file.lower():
        key_part = ".env"
    else:
        key_part = _short_name(key_source) or "bulunamadı"
    return (
        f"Yapılandırma: .env={_short_name(env_file) or 'yok'}"
        f" | config={_short_name(config_info.get('config_json', '')) or 'yok'}"
        f"{' (yok)' if not config_info.get('config_json_exists') else ''}"
        f" | anahtar={key_part} ({config_info.get('key_count', 0)} anahtar)"
    )


def print_banner(
    model: str,
    workspace_path: str,
    quota_info: str = "",
    config_info: dict | None = None,
):
    """Displays the EVREN CLI welcome banner with version and release notes.

    `config_info` (from `config.config_source_report()`) shows which .env /
    config.json the API keys were read from. Only paths and counts are shown;
    key values never reach the terminal. Paths are shortened and release notes
    are stripped of markdown so every banner line fits the terminal width.
    """
    from src.version import get_version, get_changelog_notes

    version = get_version()
    notes = [_clean_release_note(n) for n in get_changelog_notes(version, max_items=4)][:4]

    if config_info is None:
        try:
            from src.config import config_source_report

            config_info = config_source_report()
        except Exception:
            config_info = None

    safe_model = escape(str(model))
    safe_workspace = escape(_shorten_path(str(workspace_path)))
    safe_quota = escape(str(quota_info))
    if HAVE_RICH:
        title = (
            "[bold cyan]✦ EVREN CLI[/bold cyan] "
            f"[dim]v{version} • AI Development & Project Assistant[/dim]"
        )
        body = (
            f"[bold]Model:[/bold] [green]{safe_model}[/green]   "
            f"[bold]Çalışma Alanı:[/bold] [yellow]{safe_workspace}[/yellow]"
        )
        if quota_info:
            body += f"\n[bold]Kalan Günlük Kota:[/bold] [cyan]{safe_quota}[/cyan]"
        if config_info:
            body += f"\n[bold]Yapılandırma:[/bold] [dim]{escape(_config_line(config_info).split(': ', 1)[1])}[/dim]"
        if notes:
            body += f"\n\n[bold cyan]v{version} Sürüm Notları:[/bold cyan]"
            for note in notes:
                body += f"\n  [dim]•[/dim] {escape(str(note))}"

        panel = Panel(
            body,
            title=title,
            border_style="cyan",
            expand=False,
            padding=(1, 2),
        )
        console.print(panel)
    else:
        print("=" * 70)
        print(f"✦ EVREN CLI v{version} - AI Development & Project Assistant")
        print(f"Model: {model} | Çalışma Alanı: {_shorten_path(str(workspace_path), 48)}")
        if quota_info:
            print(f"Kalan Kota: {quota_info}")
        if config_info:
            print(_config_line(config_info))
        if notes:
            print(f"\nv{version} Sürüm Notları:")
            for note in notes:
                print(f"  • {note}")
        print("=" * 70)


def print_info(message: str):
    if HAVE_RICH:
        console.print(f"[info]ℹ[/info] {escape(str(message))}")
    else:
        print(f"[*] {message}")


def print_success(message: str):
    if HAVE_RICH:
        console.print(f"[success]✔[/success] {escape(str(message))}")
    else:
        print(f"[+] {message}")


def print_warning(message: str):
    if HAVE_RICH:
        console.print(f"[warning]⚠[/warning] {escape(str(message))}")
    else:
        print(f"[!] {message}")


def print_error(message: str):
    if HAVE_RICH:
        console.print(f"[error]✖ {escape(str(message))}[/error]")
    else:
        print(f"[X] {message}")


def print_markdown(content: str):
    """Renders markdown in the terminal cleanly."""
    if HAVE_RICH:
        md = Markdown(content)
        console.print(md)
    else:
        print(strip_markdown(content))


_MD_BOLD_RE = None
_MD_CODE_RE = None
_MD_HEADING_RE = None
_MD_BULLET_RE = None


def strip_markdown(text: str) -> str:
    """Plain-text fallback: removes the markdown marks a terminal reader stumbles
    on (**bold**, `code`, # headings) without touching the text itself."""
    global _MD_BOLD_RE, _MD_CODE_RE, _MD_HEADING_RE, _MD_BULLET_RE
    if _MD_BOLD_RE is None:
        import re

        _MD_BOLD_RE = re.compile(r"(\*\*|__)")
        _MD_CODE_RE = re.compile(r"`{1,3}[^`]*`{1,3}")
        _MD_HEADING_RE = re.compile(r"(?m)^\s{0,3}#{1,6}\s+")
        _MD_BULLET_RE = re.compile(r"(?m)^(\s*)[-*]\s+")
    text = _MD_CODE_RE.sub(lambda m: m.group(0).strip("`"), text)
    text = _MD_BOLD_RE.sub("", text)
    text = _MD_HEADING_RE.sub("", text)
    text = _MD_BULLET_RE.sub(lambda m: m.group(1) + "• ", text)
    return text


# Streaming state. On a real terminal with rich the answer is rendered
# progressively as markdown (rich Live), so it neither shows raw ** marks nor
# looks frozen until the end. Without rich it is printed line by line with the
# marks stripped. When stdout is not a terminal (logs, pipes) the whole answer
# is rendered once at the end.
_STREAM: dict = {"buffer": [], "live": None, "pending": ""}


def _stream_to_terminal() -> bool:
    return bool(HAVE_RICH and console is not None and console.is_terminal)


def print_markdown_stream(chunk: str):
    """Shows one streamed content delta (see _STREAM for the three modes)."""
    _STREAM["buffer"].append(chunk)
    if _stream_to_terminal():
        if _STREAM["live"] is None:
            from rich.live import Live

            live = Live(console=console, refresh_per_second=8, vertical_overflow="visible")
            live.start()
            _STREAM["live"] = live
            _STREAM["last"] = 0.0
        # Throttle: re-render markdown at most every 120 ms (flush renders the rest).
        import time as _t
        now = _t.monotonic()
        if now - _STREAM.get("last", 0.0) >= 0.12:
            _STREAM["last"] = now
            _STREAM["live"].update(Markdown("".join(_STREAM["buffer"])))
    elif not HAVE_RICH:
        _STREAM["pending"] += chunk
        while "\n" in _STREAM["pending"]:
            line, _STREAM["pending"] = _STREAM["pending"].split("\n", 1)
            print(strip_markdown(line), flush=True)


def flush_markdown():
    """Finishes a streamed answer in whichever mode it was shown."""
    if not _STREAM["buffer"]:
        return
    content = "".join(_STREAM["buffer"])
    live, pending = _STREAM["live"], _STREAM["pending"]
    _STREAM.update(buffer=[], live=None, pending="")
    if live is not None:
        live.update(Markdown(content))
        live.stop()
    elif HAVE_RICH:
        print_markdown(content)
    elif pending:
        print(strip_markdown(pending), flush=True)


def markdown_stream_active() -> bool:
    """True while a streamed answer has not been finished yet."""
    return bool(_STREAM["buffer"])


def print_reasoning(text: str, is_chunk: bool = False):
    """Prints model reasoning / chain-of-thought tokens."""
    if is_chunk:
        if HAVE_RICH:
            console.print(Text(text, style="reasoning"), end="", highlight=False)
        else:
            print(text, end="", flush=True)
    else:
        if HAVE_RICH:
            panel = Panel(
                Text(text, style="reasoning"),
                title="[bold magenta]💭 Düşünme / Akıl Yürütme[/bold magenta]",
                border_style="magenta",
                padding=(0, 1),
            )
            console.print(panel)
        else:
            print(f"\n--- [Düşünme] ---\n{text}\n-----------------\n")


def print_command_output(line: str, stream: str = "stdout"):
    """Prints a single line of live command output (stdout/stderr).

    stderr lines are dimmed/red-tinted so they stand out from normal output.
    The trailing newline is preserved so output flows naturally.
    """
    text = line.rstrip("\n")
    if HAVE_RICH:
        # soft_wrap: never insert hard line breaks at the console width, which
        # split every row of wide tables (kubectl custom-columns, ls -l, ...)
        # into several broken lines.
        if stream == "stderr":
            console.print(Text(text, style="dim red"), highlight=False, soft_wrap=True)
        else:
            console.print(Text(text), highlight=False, soft_wrap=True)
    else:
        if stream == "stderr":
            print(f"\033[2;31m{text}\033[0m")
        else:
            print(text)


# Tool-argument noise: False/None/empty values and values equal to the schema
# default carry no information for the user and used to flood the tool line.
def _is_default_arg(tool_name: str, key: str, value) -> bool:
    if value is None or value is False:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    if isinstance(value, (list, dict)) and not value:
        return True
    try:
        from src.tools import TOOLS_SCHEMA

        for tool in TOOLS_SCHEMA:
            fn = tool.get("function", {})
            if fn.get("name") != tool_name:
                continue
            props = fn.get("parameters", {}).get("properties", {})
            spec = props.get(key)
            if spec and "default" in spec:
                return value == spec["default"]
    except Exception:
        pass
    return False


def format_tool_args(tool_name: str, arguments: dict) -> str:
    """Builds the compact argument summary shown on the tool-call line.

    Skips False/None/empty arguments and arguments equal to their schema
    default; short values first so a long `command` cannot push `alias=` out.
    """
    kept = {k: v for k, v in arguments.items() if not _is_default_arg(tool_name, k, v)}
    ordered = sorted(kept.items(), key=lambda kv: len(repr(kv[1])))
    arg_summary = ", ".join(f"{k}={repr(v)}" for k, v in ordered)
    if len(arg_summary) > 100:
        arg_summary = arg_summary[:97] + "..."
    return arg_summary


def print_tool_call(tool_name: str, arguments: dict, auto_approved: bool = False):
    """Displays a tool invocation block.

    `auto_approved` is accepted for call-site symmetry with the approval flow;
    the tool line itself is always the single lightning line (warnings are
    printed separately only when a real confirmation is required).
    """
    arg_summary = format_tool_args(tool_name, arguments)
    if HAVE_RICH:
        console.print(
            f"[tool]⚡ Araç Çağrısı:[/tool] [bold white]{escape(str(tool_name))}[/bold white]"
            f"([dim]{escape(arg_summary)}[/dim])"
        )
    else:
        print(f">> [Araç] {tool_name}({arg_summary})")


def print_tool_result(tool_name: str, result_summary: str, is_error: bool = False):
    """Displays the result of a tool execution."""
    if is_error:
        if HAVE_RICH:
            console.print(f"  [error]✖ {escape(str(tool_name))} hatası:[/error] {escape(str(result_summary))}")
        else:
            print(f"  [X] {tool_name} error: {result_summary}")
    else:
        if HAVE_RICH:
            console.print(
                f"  [success]✔ {escape(str(tool_name))} tamamlandı:[/success] "
                f"[dim]{escape(str(result_summary))}[/dim]"
            )
        else:
            print(f"  [+] {tool_name} completed: {result_summary}")


def print_diff(diff_text: str, filename: str = ""):
    """Renders a colorized unified diff in the terminal."""
    if not diff_text.strip():
        print_info("Değişiklik yok.")
        return

    if HAVE_RICH:
        syntax = Syntax(diff_text, "diff", theme="monokai", line_numbers=True)
        panel = Panel(
            syntax,
            title=f"[bold yellow]Fark Önizleme (Diff): {escape(str(filename))}[/bold yellow]",
            border_style="yellow",
            padding=(0, 1),
        )
        console.print(panel)
    else:
        print(f"\n--- DIFF: {filename} ---")
        for line in diff_text.splitlines():
            if line.startswith("+"):
                print(f"\033[92m{line}\033[0m")
            elif line.startswith("-"):
                print(f"\033[91m{line}\033[0m")
            else:
                print(line)
        print("------------------------\n")


def print_run_summary(steps: int, seconds: float, tokens: int):
    """One-line end-of-task summary: steps, wall time and token usage."""
    line = f"Özet: {steps} adım • {seconds:.1f}s • {tokens} token"
    if HAVE_RICH:
        console.print(f"[subtle]{escape(line)}[/subtle]")
    else:
        print(line)


def prompt_confirm(prompt_msg: str, default: bool = False) -> bool:
    """Asks user for yes/no confirmation in terminal safely without spinner interference."""
    global _ACTIVE_STATUS
    active = _ACTIVE_STATUS
    if active:
        active.stop()

    choices = "[Y/n]" if default else "[y/N]"
    full_prompt = f"\n{prompt_msg} {choices}: "
    try:
        sys.stdout.flush()
        sys.stderr.flush()
        ans = input(full_prompt).strip().lower()
        if not ans:
            return default
        return ans in ("y", "yes", "evet", "e")
    except (KeyboardInterrupt, EOFError):
        print("\nİşlem iptal edildi.")
        return False
