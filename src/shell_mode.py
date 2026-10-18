"""`!komut` in the REPL: run a command yourself in the agent's shell.

Same shell as the agent's run_command (Git Bash on Windows when installed), same
working directory, live output, Ctrl+C kills the whole process tree. A trimmed
copy of the output joins the conversation so the next task can refer to it
("bu hatayı düzelt") without pasting it again.
"""

from __future__ import annotations

from pathlib import Path

SHELL_NOTE_CHARS = 4000


def is_shell_input(text: str) -> bool:
    stripped = (text or "").lstrip()
    return stripped.startswith("!") and len(stripped.strip()) > 1


def run_user_shell(text: str, session, timeout: int = 600) -> int:
    """Runs `!cmd` and records the result for the agent. Returns the exit code."""
    from src.proc_utils import kill_process_tree, stream_with_tree_kill
    from src.tools import _command_argv
    from src.ui import print_info, print_warning

    command = text.lstrip()[1:].strip()
    root = Path(getattr(session, "workspace_root", None) or ".")
    try:
        argv, use_shell = _command_argv(command, False)
    except Exception as e:  # cmd.exe multi-line rejection and similar
        print_warning(str(e))
        return 1
    lines: list[str] = []
    code, timed_out = 0, False
    gen = stream_with_tree_kill(argv, cwd=str(root), timeout=timeout, shell=use_shell)
    try:
        while True:
            try:
                _stream, line = next(gen)
            except StopIteration as stop:
                code, _out, _err, timed_out = stop.value
                break
            print(line, end="" if line.endswith("\n") else "\n")
            lines.append(line.rstrip("\n"))
    except KeyboardInterrupt:
        try:
            gen.close()
        except Exception:
            pass
        print_warning("Komut durduruldu (Ctrl+C).")
        code = 130
    if timed_out:
        print_warning(f"Komut {timeout} sn sonra durduruldu.")
    print_info(f"Çıkış kodu: {code}")
    _remember(session, command, code, lines)
    return code


def _remember(session, command: str, code: int, lines: list[str]) -> None:
    """Adds the command and a trimmed output to the history as user context."""
    body = "\n".join(lines)
    if len(body) > SHELL_NOTE_CHARS:
        head, tail = body[: SHELL_NOTE_CHARS // 2], body[-SHELL_NOTE_CHARS // 2:]
        body = f"{head}\n... [{len(lines)} satırın ortası kırpıldı] ...\n{tail}"
    try:
        vault = getattr(session, "vault", None)
        if vault is not None:
            body = vault.mask(body)
        session.messages.append({
            "role": "user",
            "content": (
                f"[Kullanıcının kendi çalıştırdığı kabuk komutu — bilgi için, yanıt gerekmez]\n"
                f"$ {command}\nÇıkış kodu: {code}\n{body}"
            ),
        })
    except Exception:
        pass
