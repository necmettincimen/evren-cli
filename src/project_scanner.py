"""
Workspace scanner and file structure analyzer for EVREN CLI.
Traverses project directories, ignores binaries/cache, and builds tree summaries.
"""

from pathlib import Path
from src.config import get_workspace_dir
from src.file_ops import safe_read_file

IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "dist",
    "build",
    ".evren",
    ".idea",
    ".vscode",
}

CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".scss",
    ".json", ".md", ".txt", ".toml", ".yaml", ".yml", ".cs", ".cpp",
    ".c", ".h", ".hpp", ".go", ".rs", ".sql", ".sh", ".ps1", ".bat",
    ".xml", ".ini", ".cfg",
}


def should_include_path(path: Path) -> bool:
    """Checks if a file or directory should be included in project analysis."""
    for part in path.parts:
        if part in IGNORED_DIRS:
            return False
    return True


def scan_workspace(
    workspace_root: Path | None = None,
    max_files: int = 150,
    max_file_size_kb: int = 250,
) -> dict:
    """
    Scans the workspace and returns structured metadata:
    - files: list of relative Path strings
    - extension_counts: dict of extension -> count
    - total_size_kb: total project size in KB
    """
    root = (workspace_root or get_workspace_dir()).resolve()
    scanned_files = []
    ext_counts: dict[str, int] = {}
    total_size = 0

    for path in root.rglob("*"):
        if not path.is_file():
            continue

        if not should_include_path(path):
            continue

        suffix = path.suffix.lower()
        if suffix not in CODE_EXTENSIONS and path.name not in ("Dockerfile", "Makefile", "LICENSE"):
            continue

        try:
            size_kb = path.stat().st_size / 1024.0
        except OSError:
            continue

        if size_kb > max_file_size_kb:
            continue

        rel_path = path.relative_to(root).as_posix()
        scanned_files.append(rel_path)
        ext_counts[suffix or "no_ext"] = ext_counts.get(suffix or "no_ext", 0) + 1
        total_size += size_kb

        if len(scanned_files) >= max_files:
            break

    return {
        "root": str(root),
        "files": sorted(scanned_files),
        "extension_counts": ext_counts,
        "total_files": len(scanned_files),
        "total_size_kb": round(total_size, 1),
    }


def generate_tree(workspace_root: Path | None = None, max_depth: int = 3) -> str:
    """Generates an ASCII file tree for the project."""
    root = (workspace_root or get_workspace_dir()).resolve()
    lines = [f"📁 {root.name}/"]

    def _recurse(directory: Path, prefix: str = "", depth: int = 1):
        if depth > max_depth:
            lines.append(f"{prefix}└── ... [derinlik sınırı]")
            return

        try:
            entries = sorted(
                [p for p in directory.iterdir() if should_include_path(p)],
                key=lambda p: (p.is_file(), p.name.lower())
            )
        except PermissionError:
            return

        for index, item in enumerate(entries):
            is_last = (index == len(entries) - 1)
            connector = "└── " if is_last else "├── "
            child_prefix = "    " if is_last else "│   "

            if item.is_dir():
                lines.append(f"{prefix}{connector}📁 {item.name}/")
                _recurse(item, prefix + child_prefix, depth + 1)
            else:
                lines.append(f"{prefix}{connector}📄 {item.name}")

    _recurse(root)
    return "\n".join(lines)


def build_context_from_files(file_paths: list[str | Path], workspace_root: Path | None = None) -> str:
    """Reads given files and builds a concatenated context block for prompt inclusion."""
    root = (workspace_root or get_workspace_dir()).resolve()
    sections = []

    for fp in file_paths:
        content, err = safe_read_file(fp, root)
        if err:
            sections.append(f"--- DOSYA: {fp} (HATA: {err}) ---")
        else:
            sections.append(f"--- DOSYA: {fp} ---\n```{Path(fp).suffix.lstrip('.')}\n{content}\n```")

    return "\n\n".join(sections)
