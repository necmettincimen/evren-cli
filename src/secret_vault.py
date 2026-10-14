"""
Keeps user-supplied secrets (bearer tokens, passwords, API keys) out of the model.

A task such as `curl ... -H "Authorization: Bearer <token>"` used to send the
raw token to the LLM, which then copied it into scripts, `agent.md` and
`changelog.md`. The vault swaps each secret for a `{{EVREN_SECRET_n}}`
placeholder before the text reaches the model. `run_command` / `ssh_run` put
the real value back only at execution time and also export it as the
`EVREN_SECRET_n` environment variable, so scripts can read it without storing it.
Tool output is masked again before it returns to the model or the terminal.
"""

from __future__ import annotations

import re

PLACEHOLDER = "{{EVREN_SECRET_%d}}"
_PLACEHOLDER_RE = re.compile(r"\{\{EVREN_SECRET_(\d+)\}\}")

# (pattern, group holding the secret). Values shorter than 8 characters are
# ignored so ordinary words after "token:" are not captured.
_PATTERNS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"(?i)\b(?:bearer|basic|digest)\s+([A-Za-z0-9._~+/=-]{8,})"), 1),
    (re.compile(
        r"""(?i)\b(?:password|passwd|pwd|secret|token|api[_-]?key|private[_-]?token)"""
        r"""["']?\s*[:=]\s*["']?([^\s"',;&]{8,})"""
    ), 1),
    (re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^/\s:@]+:([^@\s/]{4,})@"), 1),
    (re.compile(r"\b(evren_llm_[A-Za-z0-9_-]{8,})"), 1),
)


class SecretVault:
    """Session-scoped map of placeholder number -> secret value."""

    def __init__(self) -> None:
        self._values: dict[int, str] = {}

    def __bool__(self) -> bool:
        return bool(self._values)

    def clear(self) -> None:
        self._values.clear()

    def _number_for(self, value: str) -> int:
        for number, known in self._values.items():
            if known == value:
                return number
        number = len(self._values) + 1
        self._values[number] = value
        return number

    def capture(self, text: str) -> str:
        """Replaces secrets in `text` with placeholders and remembers them."""
        if not text:
            return text
        for pattern, group in _PATTERNS:
            def _swap(match: re.Match[str]) -> str:
                value = match.group(group)
                if _PLACEHOLDER_RE.fullmatch(value):
                    return match.group(0)
                start, end = match.span(group)
                rel_start, rel_end = start - match.start(), end - match.start()
                whole = match.group(0)
                return whole[:rel_start] + PLACEHOLDER % self._number_for(value) + whole[rel_end:]

            text = pattern.sub(_swap, text)
        return self.mask(text)

    def mask(self, text: str) -> str:
        """Replaces every known secret value in `text` with its placeholder."""
        if not text or not self._values:
            return text
        # Longest first so a secret that contains another is masked whole.
        for number, value in sorted(self._values.items(), key=lambda kv: -len(kv[1])):
            text = text.replace(value, PLACEHOLDER % number)
        return text

    def expand(self, text: str) -> str:
        """Puts the real values back (only for the command that is executed)."""
        if not text or not self._values:
            return text
        return _PLACEHOLDER_RE.sub(
            lambda m: self._values.get(int(m.group(1)), m.group(0)), text,
        )

    def env(self) -> dict[str, str]:
        """Environment variables exposing the secrets to executed commands."""
        return {f"EVREN_SECRET_{number}": value for number, value in self._values.items()}

    def usage_note(self) -> str:
        """Instruction appended to the task when secrets were captured."""
        if not self._values:
            return ""
        names = ", ".join(PLACEHOLDER % n for n in sorted(self._values))
        return (
            f"[EVREN GİZLİ DEĞERLER] Görevdeki gizli değerler {names} yer tutucularına alındı; "
            "gerçek değeri göremezsin ve tahmin etme. run_command/ssh_run komutlarında yer "
            "tutucuyu aynen yaz (örn. -H \"Authorization: Bearer {{EVREN_SECRET_1}}\"); CLI çalıştırırken "
            "gerçek değeri koyar. Dosyaya (betik, yapılandırma) yer tutucu veya değer yazma; "
            "betikte ortam değişkeninden oku: PowerShell $env:EVREN_SECRET_1, cmd %EVREN_SECRET_1%, "
            "Python os.environ['EVREN_SECRET_1']. Bu değişkenler her komutta zaten tanımlıdır; "
            "`set`/`export` ile yeniden atama."
        )


_ACTIVE = SecretVault()


def active_vault() -> SecretVault:
    """The vault of the running agent session (tools read it at execution)."""
    return _ACTIVE


def set_active_vault(vault: SecretVault) -> None:
    global _ACTIVE
    _ACTIVE = vault
