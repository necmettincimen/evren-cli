"""
EVREN CLI - Antigravity-Style AI Development & Project Assistant for EVREN LLM API
"""

import sys

if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from src.version import __version__  # noqa: E402  (single source of truth)
