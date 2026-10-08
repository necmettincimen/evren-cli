"""
Self-update mechanism for EVREN CLI.

`/update` komutu (veya `evren update`) çağrıldığında bu modül:

  1. Kurulumun bir git checkout'u mu yoksa pip ile kurulmuş bir paket mi
     olduğunu tespit eder.
  2. Git checkout ise `git pull --ff-only` ile en son sürümü çeker ve
     editable kurulumu tazeler.
  3. Aksi halde paketi doğrudan GitHub deposundan yeniden kurar
     (`pip install --upgrade git+https://github.com/necmettincimen/evren-cli.git`).
  4. İsteğe bağlı olarak süreci yeni kodla yeniden başlatır (os.execv).

Tüm işlemler .evren/logs/update.log dosyasına kaydedilir.
"""

from __future__ import annotations

import os
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

# pip ile kurulum için kullanılan hedef (git+https).
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
    """Paketi doğrudan GitHub deposundan yeniden kurar."""
    code, out = _run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--upgrade",
            "--force-reinstall",
            "--no-cache-dir",
            PIP_TARGET,
        ],
        timeout=600,
    )
    if code != 0:
        return False, f"pip kurulumu başarısız oldu:\n{out.strip()}"
    return True, out.strip() or "Paket güncellendi."


def perform_update(restart: bool = True) -> tuple[bool, str]:
    """Güncellemeyi gerçekleştirir ve isteğe bağlı olarak süreci yeniden başlatır.

    Döndürür: (başarılı_mı, mesaj). `restart=True` ise ve güncelleme başarılıysa
    bu fonksiyon normalde geri dönmez; süreç yeni kodla yeniden başlatılır.
    """
    root = get_project_root()
    _log("=== Güncelleme başlatıldı ===")

    if is_git_checkout(root):
        ok, msg = update_from_git(root)
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
    root = get_project_root()
    try:
        os.chdir(root)
    except OSError:
        pass

    # Orijinal argümanları koru (ör. --workspace, --model).
    forwarded = sys.argv[1:]
    args = [sys.executable, "-m", "src.cli", *forwarded]
    _log(f"RESTART: {' '.join(args)}")

    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass

    os.execv(sys.executable, args)
