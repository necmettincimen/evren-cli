"""
Tolerant parsing and normalization of LLM tool-call arguments.

Models do not always honour the declared JSON Schema. Two failure families
show up in practice:

1. Structured parameters arrive double-encoded as a JSON *string*
   (e.g. ``edits='[{"target_snippet": "..."}]'`` for ``edit_file``).
   Downstream code then does ``edit.get(...)`` on a ``str`` and dies with
   ``'str' object has no attribute 'get'``.
2. Scalar parameters arrive with the wrong JSON type (``start_line="12"``,
   ``all="true"``) or under an aliased key name (``old_text``/``new_text``
   instead of ``target_snippet``/``replacement_snippet``).

This module turns those raw values into the shapes the tool handlers expect
and raises :class:`ToolArgumentError` with an actionable message instead of
letting an ``AttributeError``/``TypeError`` escape into the agent loop.

The module is intentionally dependency-free (no import from ``src.tools``) so
it can be used from both the agent loop and the tool dispatcher.
"""

from __future__ import annotations

import json
from typing import Any

# Keys models commonly emit instead of the declared parameter names.
# Maps alias -> canonical name, per tool ("*" applies to every tool).
PARAMETER_ALIASES: dict[str, dict[str, str]] = {
    "edit_file": {
        "target_snippet": {
            "old_text": "target_snippet",
            "old_string": "target_snippet",
            "old_str": "target_snippet",
            "original_text": "target_snippet",
            "find": "target_snippet",
            "search": "target_snippet",
            "search_text": "target_snippet",
            "context": "target_snippet",
            "target": "target_snippet",
            "old": "target_snippet",
        },
    },
    "*": {
        "path": {"file_path": "path", "filename": "path", "file": "path"},
        "command": {"cmd": "command", "shell": "command"},
    },
}

# Alias map applied to items of ``edit_file.edits``.
EDIT_ITEM_ALIASES: dict[str, str] = {
    "old_text": "target_snippet",
    "old_string": "target_snippet",
    "old_str": "target_snippet",
    "original_text": "target_snippet",
    "find": "target_snippet",
    "search": "target_snippet",
    "search_text": "target_snippet",
    "old": "target_snippet",
    "new_text": "replacement_snippet",
    "new_string": "replacement_snippet",
    "new_str": "replacement_snippet",
    "replacement": "replacement_snippet",
    "replace": "replacement_snippet",
    "new": "replacement_snippet",
    "text": "replacement_snippet",
    "replace_all": "all",
    "replaceAll": "all",
}

_TRUE_STRINGS = {"true", "yes", "y", "1", "on"}
_FALSE_STRINGS = {"false", "no", "n", "0", "off", ""}


class ToolArgumentError(ValueError):
    """Raised when tool arguments cannot be normalized into a usable shape."""


def schema_type_map(tools_schema: list[dict]) -> dict[str, dict[str, str]]:
    """Builds ``{tool_name: {param_name: declared_json_type}}`` from a schema."""
    mapping: dict[str, dict[str, str]] = {}
    for entry in tools_schema or []:
        if not isinstance(entry, dict):
            continue
        function = entry.get("function") or {}
        name = function.get("name")
        if not name:
            continue
        params = (function.get("parameters") or {}).get("properties") or {}
        types: dict[str, str] = {}
        for param, spec in params.items():
            if isinstance(spec, dict) and isinstance(spec.get("type"), str):
                types[param] = spec["type"]
        mapping[name] = types
    return mapping


def schema_required_map(tools_schema: list[dict]) -> dict[str, list[str]]:
    """Builds ``{tool_name: [required_param, ...]}`` from a schema."""
    mapping: dict[str, list[str]] = {}
    for entry in tools_schema or []:
        if not isinstance(entry, dict):
            continue
        function = entry.get("function") or {}
        name = function.get("name")
        if not name:
            continue
        required = (function.get("parameters") or {}).get("required") or []
        mapping[name] = [item for item in required if isinstance(item, str)]
    return mapping


def _looks_like_json(value: str) -> bool:
    stripped = value.strip()
    if len(stripped) < 2:
        return False
    return stripped[0] in "[{" or stripped in ("null", "true", "false")


def load_json_string(value: str, field: str) -> Any:
    """Parses a JSON string value, raising ToolArgumentError on bad JSON."""
    try:
        return json.loads(value)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ToolArgumentError(
            f"'{field}' parametresi JSON dizisi/nesnesi olarak çözümlenemedi "
            f"({exc}). Beklenen: geçerli bir JSON. Gönderilen: {value[:200]!r}"
        ) from exc


def coerce_value(value: Any, expected_type: str, field: str) -> Any:
    """Coerces one value into the type declared by the tool schema."""
    if value is None:
        return None

    if expected_type in ("array", "object"):
        return _coerce_structure(value, expected_type, field)

    if expected_type == "string":
        if isinstance(value, str):
            return value
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    if expected_type == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in _TRUE_STRINGS:
                return True
            if lowered in _FALSE_STRINGS:
                return False
            raise ToolArgumentError(
                f"'{field}' boolean olmalı, geçersiz değer: {value!r}. "
                "Beklenen: true/false."
            )
        if isinstance(value, (int, float)):
            return bool(value)
        raise ToolArgumentError(f"'{field}' boolean olmalı, geçersiz değer: {value!r}.")

    if expected_type in ("integer", "number"):
        if isinstance(value, bool):
            raise ToolArgumentError(f"'{field}' sayı olmalı, geçersiz değer: {value!r}.")
        if isinstance(value, (int, float)):
            return int(value) if expected_type == "integer" else float(value)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                return int(text) if expected_type == "integer" else float(text)
            except ValueError:
                pass
            try:
                number = float(text)
            except ValueError:
                raise ToolArgumentError(
                    f"'{field}' {expected_type} olmalı, geçersiz değer: {value!r}."
                ) from None
            if expected_type == "integer":
                if number != int(number):
                    raise ToolArgumentError(
                        f"'{field}' tam sayı olmalı, ondalıklı değer verildi: {value!r}."
                    )
                return int(number)
            return number
        raise ToolArgumentError(f"'{field}' {expected_type} olmalı, geçersiz değer: {value!r}.")

    return value


def _coerce_structure(value: Any, expected_type: str, field: str) -> Any:
    """Normalizes values declared as array/object (incl. double-encoded JSON)."""
    if isinstance(value, str):
        if not value.strip():
            return None
        if not _looks_like_json(value):
            raise ToolArgumentError(
                f"'{field}' parametresi JSON olarak çözümlenemeyen düz metin: "
                f"{value[:200]!r}. Beklenen: {'bir JSON dizisi' if expected_type == 'array' else 'bir JSON nesnesi'}."
            )
        value = load_json_string(value, field)

    if isinstance(value, tuple):
        value = list(value)

    if expected_type == "array":
        if isinstance(value, dict):
            # A single object where a list was expected: wrap it.
            return [value]
        if isinstance(value, list):
            return value
        raise ToolArgumentError(
            f"'{field}' bir JSON dizisi olmalı, gelen tip: {type(value).__name__}."
        )

    if isinstance(value, dict):
        return value
    raise ToolArgumentError(
        f"'{field}' bir JSON nesnesi olmalı, gelen tip: {type(value).__name__}."
    )


def _apply_aliases(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Renames aliased parameter keys without overwriting canonical values."""
    alias_groups = []
    for key in ("*", tool_name):
        group = PARAMETER_ALIASES.get(key)
        if group:
            alias_groups.append(group)

    result = dict(arguments)
    for group in alias_groups:
        for canonical, aliases in group.items():
            for alias, canonical_name in aliases.items():
                if alias in result and canonical_name not in result:
                    result[canonical_name] = result.pop(alias)
                else:
                    result.pop(alias, None)
    return result


def _normalize_edit_items(edits: Any) -> list[dict[str, Any]]:
    """Normalizes the ``edits`` list of edit_file (aliases + type checks)."""
    if edits is None:
        return []
    if isinstance(edits, dict):
        edits = [edits]
    if not isinstance(edits, list):
        raise ToolArgumentError(
            f"'edits' bir JSON dizisi olmalı, gelen tip: {type(edits).__name__}."
        )

    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(edits, 1):
        if isinstance(item, str):
            if not item.strip():
                continue
            parsed = load_json_string(item, f"edits[{index - 1}]")
            item = parsed

        if not isinstance(item, dict):
            raise ToolArgumentError(
                f"edits[{index - 1}] bir JSON nesnesi olmalı "
                f"(hedef: target_snippet/replacement_snippet), gelen tip: "
                f"{type(item).__name__}."
            )

        edit: dict[str, Any] = {}
        canonical_present = {
            str(key) for key in item.keys() if str(key) not in EDIT_ITEM_ALIASES
        }
        # Canonical keys win over aliases regardless of emission order.
        for key, value in item.items():
            canonical = EDIT_ITEM_ALIASES.get(str(key), str(key))
            if canonical != str(key) and canonical in canonical_present:
                continue
            if canonical in edit and canonical != "all":
                # Keep the first value for a duplicated canonical key.
                continue
            edit[canonical] = value

        target = edit.get("target_snippet", "")
        replacement = edit.get("replacement_snippet", "")
        if not isinstance(target, str) or not isinstance(replacement, str):
            raise ToolArgumentError(
                f"edits[{index - 1}] içinde target_snippet ve replacement_snippet "
                "metin olmalı."
            )
        edit["target_snippet"] = target
        edit["replacement_snippet"] = replacement
        edit["all"] = coerce_value(edit.get("all", False), "boolean", f"edits[{index - 1}].all")
        normalized.append(edit)

    if not normalized:
        raise ToolArgumentError(
            "'edits' boş; en az bir {target_snippet, replacement_snippet} nesnesi gerekir."
        )
    return normalized


def normalize_tool_arguments(
    tool_name: str,
    arguments: Any,
    type_map: dict[str, dict[str, str]] | None = None,
    required_map: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Returns tool arguments coerced to the declared schema types.

    Raises ToolArgumentError with an actionable message when the payload is
    unusable, so callers can report it to the model instead of crashing.
    """
    if arguments is None:
        arguments = {}
    if isinstance(arguments, str):
        if not arguments.strip():
            arguments = {}
        else:
            arguments = load_json_string(arguments, tool_name or "arguments")
    if isinstance(arguments, tuple):
        arguments = list(arguments)
    if isinstance(arguments, dict):
        pass
    elif isinstance(arguments, list) and len(arguments) == 1 and isinstance(arguments[0], dict):
        arguments = arguments[0]
    else:
        raise ToolArgumentError(
            f"'{tool_name}' araç argümanları bir JSON nesnesi olmalı, "
            f"gelen tip: {type(arguments).__name__}."
        )

    result = _apply_aliases(tool_name, arguments)
    declared = (type_map or {}).get(tool_name, {})

    for field in list(result.keys()):
        expected = declared.get(field)
        if expected is None:
            continue
        result[field] = coerce_value(result[field], expected, field)

    if tool_name == "edit_file" and "edits" in result:
        result["edits"] = _normalize_edit_items(result.get("edits"))

    missing = [
        field
        for field in (required_map or {}).get(tool_name, [])
        if field not in result or result.get(field) is None
    ]
    if missing:
        raise ToolArgumentError(
            f"'{tool_name}' için zorunlu parametreler eksik: {', '.join(missing)}."
        )

    return result
