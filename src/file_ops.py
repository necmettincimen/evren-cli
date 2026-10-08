"""
Safe file operations and backup/rollback management for EVREN CLI.
Enforces workspace boundaries, prevents path traversal, creates automatic backups,
and protects critical files.
"""

import shutil
from datetime import datetime
from pathlib import Path
from src.config import get_workspace_dir, get_backup_dir, ensure_evren_dirs

FORBIDDEN_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__"}
PROTECTED_FILES = {".env", ".env.local"}


def is_safe_path(target: Path | str, workspace_root: Path | None = None) -> tuple[bool, str]:
    """
    Checks if a target path is safe to access/write within the workspace.
    Returns (is_safe, error_reason).
    """
    root = (workspace_root or get_workspace_dir()).resolve()
    target_path = Path(target)

    # Convert relative path to absolute relative to workspace root
    if not target_path.is_absolute():
        resolved = (root / target_path).resolve()
    else:
        resolved = target_path.resolve()

    # Boundary check: must be strictly inside workspace root
    try:
        resolved.relative_to(root)
    except ValueError:
        return False, f"Güvenlik İhlali: Hedef yol ({resolved}) çalışma dizini ({root}) dışında."

    # Check forbidden directories
    for part in resolved.parts:
        if part in FORBIDDEN_DIRS:
            return False, f"Güvenlik Uyarısı: '{part}' sistem/bağımlılık klasörüne erişim engellendi."

    return True, ""


def resolve_workspace_path(path_str: str, workspace_root: Path | None = None) -> Path:
    """Resolves a string path safely within the workspace."""
    root = (workspace_root or get_workspace_dir()).resolve()
    p = Path(path_str)
    if not p.is_absolute():
        return (root / p).resolve()
    return p.resolve()


def backup_file(target_path: Path, workspace_root: Path | None = None) -> Path:
    """
    Creates a timestamped backup copy in .evren/backups.
    Returns the backup Path.
    """
    root = (workspace_root or get_workspace_dir()).resolve()
    ensure_evren_dirs(root)
    backup_root = get_backup_dir(root)

    rel_name = target_path.relative_to(root).as_posix().replace("/", "_").replace("\\", "_")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"{rel_name}.{timestamp}.bak"
    backup_filepath = backup_root / backup_filename

    shutil.copy2(target_path, backup_filepath)
    return backup_filepath


def rollback_last_backup(target_path: Path, workspace_root: Path | None = None) -> tuple[bool, str]:
    """
    Restores the most recent backup for target_path.
    Returns (success, message).
    """
    root = (workspace_root or get_workspace_dir()).resolve()
    backup_root = get_backup_dir(root)

    if not backup_root.exists():
        return False, "Yedek klasörü (.evren/backups) bulunamadı."

    rel_name = target_path.relative_to(root).as_posix().replace("/", "_").replace("\\", "_")
    pattern = f"{rel_name}.*.bak"
    backups = sorted(backup_root.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)

    if not backups:
        return False, f"{target_path.name} için geri yüklenecek yedek bulunamadı."

    latest_backup = backups[0]
    shutil.copy2(latest_backup, target_path)
    return True, f"{target_path.name} dosyası {latest_backup.name} yedeğinden geri yüklendi."


def read_file_with_meta(target_path: Path | str, workspace_root: Path | None = None, max_chars: int = 150000) -> tuple[str, dict, str]:
    """
    Reads a text file and returns (content, meta, error_message).

    `meta` captures encoding details needed to write the file back faithfully:
    - has_bom: whether the file started with a UTF-8 BOM.
    - newline: the dominant line ending ("\r\n" or "\n").
    """
    root = (workspace_root or get_workspace_dir()).resolve()
    resolved = resolve_workspace_path(str(target_path), root)

    safe, reason = is_safe_path(resolved, root)
    if not safe:
        return "", {}, reason

    if not resolved.exists():
        return "", {}, f"Dosya bulunamadı: {resolved}"

    if not resolved.is_file():
        return "", {}, f"Belirtilen yol bir dosya değil: {resolved}"

    try:
        raw = resolved.read_bytes()
    except Exception as e:
        return "", {}, f"Dosya okuma hatası: {e}"

    has_bom = raw.startswith(b"\xef\xbb\xbf")
    if has_bom:
        raw = raw[3:]

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")

    newline = "\r\n" if "\r\n" in text else "\n"
    meta = {"has_bom": has_bom, "newline": newline}

    if len(text) > max_chars:
        text = text[:max_chars] + f"\n\n... [Dosya çok büyük olduğu için {max_chars} karakterde kesildi]"

    return text, meta, ""


def safe_read_file(target_path: Path | str, workspace_root: Path | None = None, max_chars: int = 150000) -> tuple[str, str]:
    """
    Safely reads a text file within the workspace.
    Returns (content, error_message).
    """
    text, _meta, err = read_file_with_meta(target_path, workspace_root, max_chars)
    return text, err


def safe_write_file(
    target_path: Path | str,
    content: str,
    workspace_root: Path | None = None,
    create_backup: bool = True,
    preserve_meta: dict | None = None,
) -> tuple[bool, Path | None, str]:
    """
    Safely writes text to target_path with automated backup.
    Returns (success, backup_path_or_none, error_message).

    If `preserve_meta` is provided (from read_file_with_meta), the original BOM
    and line-ending style are restored so edits do not silently convert CRLF->LF
    or strip a BOM.
    """
    root = (workspace_root or get_workspace_dir()).resolve()
    resolved = resolve_workspace_path(str(target_path), root)

    safe, reason = is_safe_path(resolved, root)
    if not safe:
        return False, None, reason

    if resolved.name in PROTECTED_FILES:
        return False, None, f"'{resolved.name}' kritik ortam dosyası doğrudan üzerine yazılamaz."

    # Backup if file already exists
    backup_path = None
    if resolved.exists() and create_backup:
        backup_path = backup_file(resolved, root)

    # Ensure parent directory exists
    resolved.parent.mkdir(parents=True, exist_ok=True)

    # Restore original encoding details (BOM + line endings) if known.
    data = content
    if preserve_meta:
        newline = preserve_meta.get("newline")
        if newline == "\r\n":
            # Normalize to LF first, then convert, to avoid doubling.
            data = data.replace("\r\n", "\n").replace("\n", "\r\n")
        if preserve_meta.get("has_bom"):
            data = "\ufeff" + data

    try:
        # newline="" disables universal-newline translation so CRLF/LF are
        # written exactly as provided (prevents Windows turning \r\n into \r\r\n).
        resolved.write_text(data, encoding="utf-8", newline="")
        return True, backup_path, ""
    except Exception as e:
        return False, backup_path, f"Dosya yazma hatası: {e}"
