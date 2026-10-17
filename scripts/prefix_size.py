"""Prints the fixed per-request prefix size: system prompt + tool schemas."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agent import SYSTEM_PROMPT  # noqa: E402
from src.tools import TOOLS_SCHEMA  # noqa: E402


def measure() -> dict:
    per_tool = {
        t["function"]["name"]: len(json.dumps(t, ensure_ascii=False, separators=(",", ":")))
        for t in TOOLS_SCHEMA
    }
    return {"system": len(SYSTEM_PROMPT), "tools": per_tool, "schema_total": sum(per_tool.values())}


def main() -> None:
    m = measure()
    print(f"system prompt: {m['system']} chars")
    for name, size in m["tools"].items():
        print(f"  {name}: {size}")
    total = m["system"] + m["schema_total"]
    print(f"schemas: {m['schema_total']} chars; total: {total} chars (~{int(total / 3.5)} tokens)")


if __name__ == "__main__":
    main()
