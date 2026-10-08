"""
Rich terminal UI components and formatters for EVREN CLI.
Provides Antigravity-style terminal aesthetics, markdown rendering,
tool execution banners, reasoning stream blocks, and diffs.
"""

import sys
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.markdown import Markdown
    from rich.table import Table
    from rich.syntax import Syntax
    from rich.text import Text
    from rich.theme import Theme
    HAVE_RICH = True
except ImportError:
    HAVE_RICH = False

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
            self._status = console.status(f"[bold cyan]✦ {self.message}[/bold cyan]", spinner="dots")
            self._status.start()
        else:
            print(f"[*] {self.message}...", end="", flush=True)
        return self

    def update(self, new_message: str):
        self.message = new_message
        if self._status:
            self._status.update(f"[bold cyan]✦ {new_message}[/bold cyan]")
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


def print_banner(model: str, workspace_path: str, quota_info: str = ""):
    """Displays the Antigravity-style welcome banner with version and release notes."""
    from src.version import get_version, get_changelog_notes

    version = get_version()
    notes = get_changelog_notes(version)

    if HAVE_RICH:
        title = (
            "[bold cyan]✦ EVREN CLI[/bold cyan] "
            f"[dim]v{version} • Antigravity-Style AI Development Assistant[/dim]"
        )
        body = (
            f"[bold]Model:[/bold] [green]{model}[/green]   "
            f"[bold]Çalışma Alanı:[/bold] [yellow]{workspace_path}[/yellow]\n"
            f"[dim]EVREN LLM API Tabanlı Otonom Kodlama ve Proje Asistanı[/dim]"
        )
        if quota_info:
            body += f"\n[bold]Kalan Günlük Kota:[/bold] [cyan]{quota_info}[/cyan]"
        if notes:
            body += f"\n\n[bold cyan]v{version} Sürüm Notları:[/bold cyan]"
            for note in notes:
                body += f"\n  [dim]•[/dim] {note}"

        panel = Panel(
            body,
            title=title,
            border_style="cyan",
            expand=False,
            padding=(1, 2),
        )
        console.print(panel)
        console.print("[dim]Komut listesi için [bold]/help[/bold], çıkmak için [bold]/exit[/bold] yazın.[/dim]\n")
    else:
        print("=" * 70)
        print(f"✦ EVREN CLI v{version} - Antigravity AI Assistant")
        print(f"Model: {model} | Workspace: {workspace_path}")
        if quota_info:
            print(f"Kalan Kota: {quota_info}")
        if notes:
            print(f"\nv{version} Sürüm Notları:")
            for note in notes:
                print(f"  • {note}")
        print("Yardım için /help, çıkmak için /exit")
        print("=" * 70)


def print_info(message: str):
    if HAVE_RICH:
        console.print(f"[info]ℹ[/info] {message}")
    else:
        print(f"[*] {message}")


def print_success(message: str):
    if HAVE_RICH:
        console.print(f"[success]✔[/success] {message}")
    else:
        print(f"[+] {message}")


def print_warning(message: str):
    if HAVE_RICH:
        console.print(f"[warning]⚠[/warning] {message}")
    else:
        print(f"[!] {message}")


def print_error(message: str):
    if HAVE_RICH:
        console.print(f"[error]✖ {message}[/error]")
    else:
        print(f"[X] {message}")


def print_markdown(content: str):
    """Renders markdown in the terminal cleanly."""
    if HAVE_RICH:
        md = Markdown(content)
        console.print(md)
    else:
        print(content)


def print_reasoning(text: str, is_chunk: bool = False):
    """Prints model reasoning / chain-of-thought tokens."""
    if is_chunk:
        if HAVE_RICH:
            console.print(f"[reasoning]{text}[/reasoning]", end="", highlight=False)
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


def print_tool_call(tool_name: str, arguments: dict):
    """Displays a tool invocation block."""
    arg_summary = ", ".join(f"{k}={repr(v)}" for k, v in arguments.items())
    if len(arg_summary) > 100:
        arg_summary = arg_summary[:97] + "..."
    if HAVE_RICH:
        console.print(f"[tool]⚡ Araç Çağrısı:[/tool] [bold white]{tool_name}[/bold white]([dim]{arg_summary}[/dim])")
    else:
        print(f">> [Araç] {tool_name}({arg_summary})")


def print_tool_result(tool_name: str, result_summary: str, is_error: bool = False):
    """Displays the result of a tool execution."""
    if is_error:
        if HAVE_RICH:
            console.print(f"  [error]✖ {tool_name} hatası:[/error] {result_summary}")
        else:
            print(f"  [X] {tool_name} error: {result_summary}")
    else:
        if HAVE_RICH:
            console.print(f"  [success]✔ {tool_name} tamamlandı:[/success] [dim]{result_summary}[/dim]")
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
            title=f"[bold yellow]Fark Önizleme (Diff): {filename}[/bold yellow]",
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
