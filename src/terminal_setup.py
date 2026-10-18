"""`evren setup-terminal`: open EVREN in Git Bash inside Windows Terminal.

Git Bash's own window (mintty) is not a Windows console, so prompt_toolkit
input, Esc and approval prompts misbehave there. Windows Terminal runs bash.exe
through ConPTY and keeps them working. The profile is added as a Windows
Terminal *fragment* (a separate file), so the user's settings.json is never
edited and removal is deleting one file.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

PROFILE_NAME = "EVREN (Git Bash)"
PROFILE_GUID = "{6f3c8a52-4e7b-5d0e-9a1c-0e5e7c1e2b30}"


def fragment_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return base / "Microsoft" / "Windows Terminal" / "Fragments" / "EVREN" / "evren.json"


def shortcut_path() -> Path:
    return Path(os.environ.get("USERPROFILE") or Path.home()) / "Desktop" / "EVREN.lnk"


def _git_icon(bash: str) -> str | None:
    root = Path(bash).resolve().parents[1]  # ...\Git\bin\bash.exe -> ...\Git
    for cand in (root / "mingw64" / "share" / "git" / "git-for-windows.ico", root / "git-bash.exe"):
        if cand.exists():
            return str(cand)
    return None


def build_profile(bash: str, start_dir: str) -> dict:
    profile = {
        "guid": PROFILE_GUID,
        "name": PROFILE_NAME,
        # Login shell so PATH/aliases match the user's Git Bash; stay in bash
        # after `evren` exits instead of closing the tab.
        "commandline": f'"{bash}" --login -i -c "evren; exec bash -i"',
        "startingDirectory": start_dir,
        "hidden": False,
    }
    icon = _git_icon(bash)
    if icon:
        profile["icon"] = icon
    return profile


def setup(start_dir: str | None = None, *, shortcut: bool = False) -> list[str]:
    """Writes the fragment (and optional desktop shortcut); returns report lines."""
    from src.config import git_bash_path

    if os.name != "nt":
        return ["Bu komut yalnızca Windows içindir; diğer sistemlerde terminalinizde `evren` çalıştırın."]
    bash = git_bash_path()
    if not bash:
        return ["HATA: Git Bash bulunamadı. Git for Windows'u kurun ya da EVREN_GIT_BASH ile yolu verin."]
    start = str(Path(start_dir).resolve()) if start_dir else "%USERPROFILE%"
    path = fragment_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"profiles": [build_profile(bash, start)]}, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    lines = [
        f"Windows Terminal profili eklendi: '{PROFILE_NAME}' ({path})",
        f"Kabuk: {bash} | Başlangıç klasörü: {start}",
        "Windows Terminal'i yeniden açın; profil açılır menüde görünür.",
    ]
    if not shutil.which("wt"):
        lines.append("Uyarı: Windows Terminal (wt.exe) bulunamadı; Microsoft Store'dan kurabilirsiniz.")
    if shortcut:
        lines.append(_create_shortcut(bash))
    if not shutil.which("evren"):
        lines.append("Uyarı: 'evren' PATH'te değil; profil açılınca komut bulunamayabilir (pip install -e .).")
    return lines


def _create_shortcut(bash: str) -> str:
    target_wt = shutil.which("wt")
    link = shortcut_path()
    if target_wt:
        target, arguments = target_wt, f'-p "{PROFILE_NAME}"'
    else:
        target, arguments = bash, '--login -i -c "evren; exec bash -i"'
    script = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:EVREN_LNK);"
        "$s.TargetPath=$env:EVREN_TARGET;$s.Arguments=$env:EVREN_ARGS;$s.Save()"
    )
    env = {**os.environ, "EVREN_LNK": str(link), "EVREN_TARGET": target, "EVREN_ARGS": arguments}
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                       env=env, check=True, capture_output=True, timeout=30)
        return f"Masaüstü kısayolu oluşturuldu: {link}"
    except Exception as e:
        return f"Uyarı: kısayol oluşturulamadı ({type(e).__name__})."


def remove() -> list[str]:
    out = []
    for p in (fragment_path(), shortcut_path()):
        if p.exists():
            p.unlink()
            out.append(f"Silindi: {p}")
    try:
        fragment_path().parent.rmdir()
    except OSError:
        pass
    return out or ["Kaldırılacak EVREN terminal profili yok."]
