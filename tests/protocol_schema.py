"""protocol/stdio-v1.schema.json doğrulayıcıları (testler için)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "protocol" / "stdio-v1.schema.json"


@lru_cache(maxsize=1)
def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=2)
def _validator(kind: str):
    import jsonschema

    s = schema()
    sub = {"$schema": s["$schema"], "$defs": s["$defs"], "$ref": f"#/$defs/{kind}"}
    return jsonschema.Draft202012Validator(sub)


def _check(kind: str, msg: dict) -> None:
    errors = sorted(_validator(kind).iter_errors(msg), key=lambda e: e.path)
    if errors:
        detail = "; ".join(f"{list(e.path)}: {e.message}" for e in errors[:3])
        raise AssertionError(f"{kind} şemaya uymuyor ({msg.get('type')}): {detail}\n{json.dumps(msg, ensure_ascii=False)[:500]}")


def validate_event(msg: dict) -> None:
    if msg.get("type") not in schema()["x-event-types"]:
        raise AssertionError(f"şemada olmayan olay tipi: {msg.get('type')}")
    _check("event", msg)


def validate_command(msg: dict) -> None:
    if msg.get("type") not in schema()["x-command-types"]:
        raise AssertionError(f"şemada olmayan komut tipi: {msg.get('type')}")
    _check("command", msg)
