"""
Self-update mechanism for EVREN CLI.

`/update` komutu (veya `evren update`) çağrıldığında bu modül:

  1. Kurulumun bir git checkout'u mu yoksa pip ile kurulmuş bir paket mi
     olduğunu tespit eder.
  2. Git checkout ise `git pull --ff-only` ile en son sürümü çeker ve
     editable kurulumu tazeler.
  3. Aksi halde kalıcı bir kaynak klonunu (%LOCALAPPDATA%/evren-cli/source) ilk
     seferde klonlar, sonra yalnızca yeni commit'leri çeker ve paketi o klondan kurar.
  4. İsteğe bağlı olarak süreci yeni kodla yeniden başlatır (os.execv).

Tüm işlemler .evren/logs/update.log dosyasına kaydedilir.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------

REPO_OWNER = "necmettincimen"
REPO_NAME = "evren-cli"
REPO_HTTPS = f"https://github.com/{REPO_OWNER}/{REPO_NAME}"
REPO_GIT_URL = f"{REPO_HTTPS}.git"
SOURCE_BRANCH = "master"

# pip ile kurulum için kullanılan hedef (git+https). Artık yalnızca bilgi amaçlı;
# kurulum yerel kaynak klonundan yapılır (bkz. sync_source_clone).
PIP_TARGET = f"git+{REPO_GIT_URL}"


def get_project_root() -> Path:
    """Kurulu paketin kök dizinini (repo kökü) döndürür."""
    return Path(__file__).resolve().parents[1]


def is_git_checkout(root: Path | None = None) -> bool:
    """Kurulumun bir git çalışma kopyası olup olmadığını döndürür."""
    root = root or get_project_root()
    return (root / ".git").exists()


def _log_path() -> Path:
    """Güncelleme günlük dosyasının yolunu döndürür."""
    root = get_project_root()
    log_dir = root / ".evren" / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Yazılamıyorsa geçici dizine düş.
        log_dir = Path(os.environ.get("TEMP", ".")) / "evren-logs"
        log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "update.log"


def _log(message: str) -> None:
    """Güncelleme adımlarını günlük dosyasına yazar (hataları yutar)."""
    try:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with _log_path().open("a", encoding="utf-8") as fh:
            fh.write(f"[{stamp}] {message}\n")
    except Exception:
        pass


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 300) -> tuple[int, str]:
    """Bir komutu çalıştırır ve (returncode, birleşik çıktı) döndürür."""
    _log(f"RUN: {' '.join(cmd)} (cwd={cwd})")
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        _log(f"EXIT {proc.returncode}: {output.strip()[:2000]}")
        return proc.returncode, output
    except FileNotFoundError as e:
        _log(f"ERROR: komut bulunamadı: {e}")
        return 127, f"Komut bulunamadı: {cmd[0]} ({e})"
    except subprocess.TimeoutExpired:
        _log("ERROR: zaman aşımı")
        return -1, "İşlem zaman aşımına uğradı."
    except Exception as e:  # pragma: no cover - beklenmeyen durumlar
        _log(f"ERROR: {e}")
        return -1, str(e)


def _git_branch(root: Path) -> str:
    """Aktif git dalını döndürür (bulunamazsa 'main')."""
    code, out = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root, timeout=30)
    branch = out.strip().splitlines()[-1].strip() if out.strip() else ""
    if code == 0 and branch and branch != "HEAD":
        return branch
    return "main"


def update_from_git(root: Path | None = None) -> tuple[bool, str]:
    """Git checkout'u en son sürüme günceller ve editable kurulumu tazeler."""
    root = root or get_project_root()
    branch = _git_branch(root)

    code, out = _run(["git", "pull", "--ff-only", "origin", branch], cwd=root)
    if code != 0:
        return False, f"git pull başarısız oldu:\n{out.strip()}"

    # Bağımlılıklar / entry point'ler değişmiş olabilir; editable kurulumu tazele.
    code2, out2 = _run(
        [sys.executable, "-m", "pip", "install", "-e", ".", "--quiet"],
        cwd=root,
    )
    if code2 != 0:
        return False, f"Bağımlılıklar güncellenemedi:\n{out2.strip()}"

    summary = out.strip() or "Zaten güncel."
    return True, summary


def update_from_pip() -> tuple[bool, str]:
    """Kaynak klonunu günceller, paketi yerel klondan kurar."""
    ok, msg = sync_source_clone()
    if not ok:
        return False, msg
    code, out = _run(_pip_command(), timeout=600)
    if code != 0:
        return False, f"pip kurulumu başarısız oldu:\n{out.strip()}"
    return True, out.strip() or "Paket güncellendi."


_DEFERRED_PIP_SCRIPT = r"""
import ctypes, subprocess, sys, time
from datetime import datetime

pid, log_path, cmd = int(sys.argv[1]), sys.argv[2], sys.argv[3:]

def log(message):
    try:
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}\n")
    except OSError:
        pass

kernel32 = ctypes.windll.kernel32
handle = kernel32.OpenProcess(0x00100000, False, pid)
if handle:
    kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)
    kernel32.CloseHandle(handle)
time.sleep(2)
print("EVREN CLI güncelleniyor...", flush=True)
code = 1
for attempt in range(1, 6):
    code = subprocess.run(cmd).returncode
    log(f"DEFERRED PIP attempt {attempt}: EXIT {code}")
    if code == 0:
        break
    time.sleep(3)
print("Güncelleme tamamlandı; evren komutunu yeniden açabilirsiniz."
      if code == 0 else
      "Güncelleme başarısız. Ayrıntılar için yukarıdaki pip çıktısına bakın.", flush=True)
input("Pencereyi kapatmak için Enter'a basın...")
sys.exit(code)
"""


def update_is_deferred(root: Path | None = None) -> bool:
    """Windows pip installs cannot replace the running evren.exe in-process."""
    return os.name == "nt" and not is_git_checkout(root or get_project_root())


def get_source_dir() -> Path:
    """Kalıcı kaynak klonunun yolu."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "evren-cli" / "source"


def sync_source_clone() -> tuple[bool, str]:
    """Kaynak klonunu son commit'e getirir.

    İlk seferde sığ bir klon alınır; sonraki güncellemelerde yalnızca yeni
    commit'ler indirilir ve paket dosyaları yeniden indirilmez. Git, pip'in
    build izolasyonundaki PyPI isteğinin aksine kurumsal proxy'de 407 almaz.
    """
    target = get_source_dir()
    if (target / ".git").exists():
        action = "git fetch"
        code, out = _run(
            ["git", "-C", str(target), "fetch", "--depth", "1", "origin", SOURCE_BRANCH],
            timeout=300,
        )
        if code == 0:
            code, out = _run(
                ["git", "-C", str(target), "reset", "--hard", "FETCH_HEAD"], timeout=120
            )
    else:
        action = "git clone"
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        code, out = _run(
            ["git", "clone", "--depth", "1", "--branch", SOURCE_BRANCH, REPO_GIT_URL, str(target)],
            timeout=600,
        )
    if code != 0:
        return False, f"{action} başarısız oldu:\n{out.strip()}"
    return True, out.strip() or "Kaynak güncel."


def _pip_command() -> list[str]:
    """Yerel klondan kurulum. Build izolasyonu kapalıdır: setuptools PyPI'dan indirilmez.

    `--upgrade` yalnızca sürümü değişen paketleri ve eksik bağımlılıkları indirir.
    """
    return [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "--no-build-isolation",
        str(get_source_dir()),
    ]


def schedule_deferred_pip_update() -> tuple[bool, str]:
    """Run pip in a new console after this process releases evren.exe."""
    # Git işlemi bu süreçte yapılır (evren.exe'ye dokunmaz); pip ertelenir.
    ok, msg = sync_source_clone()
    if not ok:
        _log(f"ERROR: kaynak klonu güncellenemedi: {msg}")
        return False, msg
    args = [
        sys.executable, "-c", _DEFERRED_PIP_SCRIPT,
        str(os.getpid()), str(_log_path()), *_pip_command(),
    ]
    _log(f"SCHEDULE DEFERRED: {' '.join(_pip_command())}")
    try:
        subprocess.Popen(
            args,
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            close_fds=True,
        )
    except OSError as e:
        _log(f"ERROR: ertelenmiş güncelleme başlatılamadı: {e}")
        return False, f"Ertelenmiş güncelleme başlatılamadı: {e}"
    return True, (
        "Güncelleme ayrı pencerede, EVREN CLI kapandıktan sonra çalışacak. "
        "Tamamlandığında evren komutunu yeniden açın."
    )


def perform_update(restart: bool = True) -> tuple[bool, str]:
    """Güncellemeyi gerçekleştirir ve isteğe bağlı olarak süreci yeniden başlatır.

    Döndürür: (başarılı_mı, mesaj). `restart=True` ise ve güncelleme başarılıysa
    bu fonksiyon normalde geri dönmez; süreç yeni kodla yeniden başlatılır.
    """
    root = get_project_root()
    _log("=== Güncelleme başlatıldı ===")

    if is_git_checkout(root):
        ok, msg = update_from_git(root)
    elif update_is_deferred(root):
        ok, msg = schedule_deferred_pip_update()
        _log("=== Güncelleme ertelendi ===" if ok else "=== Güncelleme BAŞARISIZ ===")
        return ok, msg
    else:
        ok, msg = update_from_pip()

    if not ok:
        _log("=== Güncelleme BAŞARISIZ ===")
        return False, msg

    _log("=== Güncelleme tamamlandı ===")

    if restart:
        restart_process()
        # restart_process normalde geri dönmez; dönerse bilgilendir.
        return True, "Güncelleme tamamlandı. Yeniden başlatma için lütfen CLI'ı elle açın."

    return True, msg


def restart_process() -> None:
    """Mevcut süreci yeni kodla yeniden başlatır (os.execv ile yerinde)."""
    # Preserve global options, but never replay the update subcommand.
    # Option values can themselves be "update", so do not filter by value.
    forwarded = sys.argv[1:]
    index = 0
    while index < len(forwarded):
        token = forwarded[index]
        if token in ("--workspace", "-w", "--model", "-m"):
            index += 2
        elif token.startswith(("--workspace=", "--model=")) or (
            token.startswith(("-w", "-m")) and len(token) > 2
        ):
            index += 1
        else:
            if token == "update":
                forwarded = forwarded[:index]
            break
    args = [sys.executable, "-m", "src.cli", *forwarded]
    _log(f"RESTART: {' '.join(args)}")

    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass

    os.execv(sys.executable, args)
