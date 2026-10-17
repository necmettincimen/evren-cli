"""Cheap summarizer for history compaction (plan cli-stability-speed, item 7.4).

One non-streaming call on a fast model with `thinking: false`. It is best
effort: any failure (no network, rate limit, timeout, odd response) returns
None and the compactor falls back to a deterministic summary.
"""

from __future__ import annotations

import concurrent.futures
import json
from typing import Any, Callable

SUMMARY_MAX_TOKENS = 600
SUMMARY_MAX_CHARS = 1200
SUMMARY_TIMEOUT_SECONDS = 20.0
# Input given to the summarizer is capped so the call itself stays cheap.
TRANSCRIPT_MAX_CHARS = 14000
_PART_CHARS = 700

SUMMARY_INSTRUCTION = (
    "Aşağıda bir yazılım ajanının önceki adımlarının dökümü var. Devam eden iş için "
    "kısa bir özet yaz (en fazla 1200 karakter, Türkçe, madde işaretli). Şunları koru: "
    "dosya yolları, alınan kararlar, görülen hatalar ve çözümleri, kalan işler. "
    "Gereksiz ayrıntıyı, araç çıktılarının içeriğini ve selamlaşmayı yazma. "
    "Yalnızca özeti döndür."
)


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_transcript(messages: list[dict[str, Any]], max_chars: int = TRANSCRIPT_MAX_CHARS) -> str:
    """Flattens messages into a compact text; the newest parts win when capped."""
    lines: list[str] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        text = content if isinstance(content, str) else ""
        if role == "assistant":
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                args = fn.get("arguments", "")
                if not isinstance(args, str):
                    args = json.dumps(args, ensure_ascii=False)
                lines.append(f"ajan araç: {fn.get('name', '?')} {_clip(args, 160)}")
            if text.strip():
                lines.append(f"ajan: {_clip(text, _PART_CHARS)}")
        elif role == "tool":
            lines.append(f"sonuç: {_clip(text, 260)}")
        elif role == "user":
            lines.append(f"kullanıcı: {_clip(text, _PART_CHARS)}")
    out = "\n".join(lines)
    return out if len(out) <= max_chars else "…\n" + out[-max_chars:]


def _extract_text(response: Any) -> str | None:
    try:
        content = response.choices[0].message.content
    except Exception:
        return None
    if not isinstance(content, str):
        return None
    content = content.strip()
    return content or None


def summarize_messages(
    client: Any,
    messages: list[dict[str, Any]],
    model: str,
    *,
    timeout: float = SUMMARY_TIMEOUT_SECONDS,
    on_response: Callable[[Any], None] | None = None,
) -> str | None:
    """Summarises `messages` in at most ~1200 chars; None on any failure."""
    if client is None or not messages:
        return None
    transcript = render_transcript(messages)
    if not transcript.strip():
        return None
    request = [
        {"role": "system", "content": SUMMARY_INSTRUCTION},
        {"role": "user", "content": transcript},
    ]
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(
            client.chat_complete,
            messages=request,
            model=model,
            max_tokens=SUMMARY_MAX_TOKENS,
            temperature=0.2,
            max_retries=1,
            thinking=False,
        )
        response = future.result(timeout=timeout)
        text = _extract_text(response)
        if text is None:
            return None
        if on_response is not None:
            try:
                on_response(response)
            except Exception:
                pass
        return _clip_summary(text)
    except Exception:
        return None
    finally:
        pool.shutdown(wait=False)


def _clip_summary(text: str) -> str:
    text = text.strip()
    if len(text) <= SUMMARY_MAX_CHARS:
        return text
    return text[: SUMMARY_MAX_CHARS - 1].rstrip() + "…"
