"""Optional intent classification through the EVREN `clef` model (plan 7.3).

Any failure (network, timeout, unexpected payload) returns None so callers
fall back to their regex heuristics.
"""

from __future__ import annotations

import json
import os

_TIMEOUT = 3.0


def _extract_label(data, labels: list[str]) -> str | None:
    """Finds one of `labels` in the gateway answer, whatever its exact shape."""
    if isinstance(data, str):
        low = data.strip().lower()
        for label in labels:
            if low == label.lower():
                return label
        for label in labels:
            if label.lower() in low:
                return label
        return None
    if isinstance(data, dict):
        for key in ("label", "intent", "class", "category", "result", "output", "text", "content"):
            if key in data:
                found = _extract_label(data[key], labels)
                if found:
                    return found
        for value in data.values():
            if isinstance(value, (dict, list)):
                found = _extract_label(value, labels)
                if found:
                    return found
        return None
    if isinstance(data, list):
        for item in data:
            found = _extract_label(item, labels)
            if found:
                return found
    return None


def classify(text: str, labels: list[str], client=None) -> str | None:
    """Returns the best label for `text`, or None on any failure."""
    if not text or not labels:
        return None
    try:
        import httpx

        from src.config import EVREN_BASE_URL, get_ssl_verify
        from src.api_key_pool import parse_api_keys

        base = (getattr(client, "base_url", None) or EVREN_BASE_URL).rstrip("/")
        key = getattr(client, "api_key", None)
        if not key:
            keys = parse_api_keys()
            key = keys[0] if keys else ""
        if not key:
            return None
        payload = {"model": "clef", "input": text[:2000], "labels": list(labels)}
        resp = httpx.post(
            f"{base}/systemone",
            headers={"X-API-Key": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=_TIMEOUT,
            verify=get_ssl_verify(),
        )
        if resp.status_code != 200:
            return None
        try:
            data = resp.json()
        except ValueError:
            data = resp.text
        return _extract_label(data, list(labels))
    except Exception:
        return None


def clef_label(text: str, labels: list[str]) -> str | None:
    """`classify` gated by EVREN_CLEF_INTENT (default off)."""
    if (os.getenv("EVREN_CLEF_INTENT") or "").strip().lower() not in ("1", "true", "yes", "on"):
        return None
    return classify(text, labels)
