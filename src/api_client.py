"""
EVREN LLM API Client with OpenAI SDK compatibility, streaming with keep-alive filtering,
reasoning token extraction, quota header tracking, and automatic retry handling.
"""

import time
import math
import json
import httpx
from typing import Generator, Any
from openai import OpenAI

from src.config import (
    EVREN_API_KEY,
    EVREN_BASE_URL,
    EVREN_DEFAULT_MODEL,
    EVREN_DEFAULT_MAX_TOKENS,
    EVREN_DEFAULT_TEMPERATURE,
    EVREN_REASONING_EFFORT,
    get_ssl_verify,
)
from src.terms import ensure_terms_accepted
from src.errors import (
    RateLimitError,
    QuotaExhaustedError,
    TermsNotAcceptedError,
    map_sdk_exception,
)
from src.api_key_pool import ApiKeyPool

# SDK-level timeouts (seconds). Streaming uses a longer read timeout because
# reasoning models can pause between tokens.
REQUEST_TIMEOUT = httpx.Timeout(connect=20.0, read=120.0, write=30.0, pool=20.0)
STREAM_TIMEOUT = httpx.Timeout(connect=20.0, read=300.0, write=30.0, pool=20.0)


class _StreamedFunction:
    """Accumulates a streamed tool-call function (name + partial arguments)."""

    def __init__(self):
        self.name = ""
        self.arguments = ""


class _StreamedToolCall:
    """Accumulates a single streamed tool call across delta chunks."""

    def __init__(self, index: int):
        self.index = index
        self.id = ""
        self.type = "function"
        self.function = _StreamedFunction()


class _StreamedMessage:
    """Assembled assistant message produced from a stream."""

    def __init__(self):
        self.content = ""
        self.reasoning = ""
        self.tool_calls: list[_StreamedToolCall] = []


class _StreamedChoice:
    def __init__(self, message: _StreamedMessage):
        self.message = message


class _StreamedResponse:
    """Mimics the OpenAI response shape so callers can stay unchanged."""

    def __init__(self, message: _StreamedMessage, usage: Any = None):
        self.choices = [_StreamedChoice(message)]
        self.usage = usage


class EvrenClient:
    """High-level client for EVREN LLM API."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        default_model: str | None = None,
        key_pool: "ApiKeyPool | None" = None,
    ):
        self.base_url = (base_url or EVREN_BASE_URL).rstrip("/")
        self.default_model = default_model or EVREN_DEFAULT_MODEL
        self.last_remaining_tokens: int | None = None
        # Reasoning yoğunluğu (runtime'da /effort ile değiştirilebilir).
        self.reasoning_effort: str | None = EVREN_REASONING_EFFORT

        # Build the key pool. An explicit api_key overrides env-based discovery.
        if key_pool is not None:
            self.key_pool = key_pool
        elif api_key:
            self.key_pool = ApiKeyPool([api_key], base_url=self.base_url)
        else:
            self.key_pool = ApiKeyPool(base_url=self.base_url)

        # No key found anywhere: prompt the user interactively (and persist it)
        # instead of crashing. Other settings (base_url, model, max_tokens,
        # temperature) already fall back to their defaults above.
        if self.key_pool.is_empty():
            from src.config import prompt_for_api_key

            entered = prompt_for_api_key()
            if entered:
                self.key_pool = ApiKeyPool([entered], base_url=self.base_url)

        if self.key_pool.is_empty():
            raise ValueError(
                "EVREN_API_KEY bulunamadı. Lütfen .env dosyanızı veya ortam değişkeninizi ayarlayın."
            )

        # Backward-compat: expose the first key as `api_key` and a default client.
        # max_retries=0: we implement our own backoff/failover on top of the SDK,
        # so the SDK must NOT retry internally (avoids double-retry stacking).
        self.api_key = self.key_pool.keys[0].key
        self.openai_client = self.key_pool.get_client(self.api_key)

    def _acquire_client(self) -> tuple[str, Any]:
        """Acquires the sticky current key from the pool and returns (key, client).

        Raises RateLimitError if every key is currently cooling/disabled.
        """
        pooled = self.key_pool.acquire()
        if pooled is None:
            wait = self.key_pool.next_available_utc()
            counts = self.key_pool.state_counts()
            wait_display = max(1, math.ceil(wait)) if wait > 0 else 0
            details = (
                f"(hazır: {counts['ready']}, soğumada: {counts['cooling']}, devre dışı: {counts['disabled']})"
            )
            raise RateLimitError(
                (
                    f"Tüm API anahtarları şu an kullanılamıyor {details}. "
                    f"En erken {wait_display} sn sonra tekrar deneyin."
                ),
                retry_after=wait or None,
            )
        self.api_key = pooled.key
        self.openai_client = self.key_pool.get_client(pooled.key)
        return pooled.key, self.openai_client

    def _headers_for(self, api_key: str) -> dict[str, str]:
        return {
            "X-API-Key": api_key,
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def _get_headers(self) -> dict[str, str]:
        return self._headers_for(self.api_key)

    def list_models(self) -> list[str]:
        """Fetches the list of available model IDs from EVREN API."""
        url = f"{self.base_url}/models"
        try:
            resp = httpx.get(url, headers=self._get_headers(), timeout=20.0, verify=get_ssl_verify())
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict) and "data" in data:
                return [m.get("id") for m in data["data"] if "id" in m]
            elif isinstance(data, list):
                return [m.get("id", str(m)) for m in data]
            return []
        except Exception as e:
            return ["deepseek-v4.1-flash", "glm-5.3", "qwen3.8-flash-next", "auto"]

    @staticmethod
    def _normalize_quota(data: dict[str, Any], headers: httpx.Headers | None = None) -> dict[str, Any]:
        """Normalizes quota JSON + response headers into a stable shape.

        Supports both legacy daily-token fields and rolling-window fields
        (`cap`, `used_tokens`, `reset_at`, `remaining_cr`, …).
        """
        headers = headers or httpx.Headers()
        out = dict(data) if isinstance(data, dict) else {}

        def _first(*keys, header_keys: tuple[str, ...] = ()):
            for k in keys:
                if k in out and out[k] is not None:
                    return out[k]
            for hk in header_keys:
                v = headers.get(hk)
                if v is not None and v != "":
                    return v
            return None

        remaining = _first(
            "remaining_daily_tokens",
            "remaining_tokens",
            "remaining",
            header_keys=("x-evren-daily-remaining-tokens",),
        )
        limit = _first(
            "daily_limit",
            "daily_tokens_limit",
            "limit",
            "cap",
            "quota_limit",
            header_keys=("x-evren-daily-limit-tokens", "x-evren-daily-limit"),
        )
        used = _first("used_tokens", "used", "daily_used_tokens")
        reset = _first(
            "reset_at",
            "resets_at",
            "quota_reset",
            "reset",
            header_keys=("x-evren-quota-reset", "x-ratelimit-reset"),
        )
        remaining_cr = _first("remaining_cr")
        held_cr = _first("held_cr")

        # Derive remaining from cap/used when the API only exposes those.
        if remaining is None and limit is not None and used is not None:
            try:
                remaining = int(limit) - int(used)
            except (TypeError, ValueError):
                pass

        def _as_int(v):
            if v is None:
                return None
            try:
                return int(v)
            except (TypeError, ValueError):
                return v

        remaining_i = _as_int(remaining)
        limit_i = _as_int(limit)
        used_i = _as_int(used)

        if remaining_i is not None:
            out["remaining_daily_tokens"] = remaining_i
            out["remaining"] = remaining_i
        if limit_i is not None:
            out["limit"] = limit_i
        if used_i is not None:
            out["used"] = used_i
        if reset is not None:
            out["reset"] = reset
        if remaining_cr is not None:
            out["remaining_cr"] = _as_int(remaining_cr)
        if held_cr is not None:
            out["held_cr"] = _as_int(held_cr)
        if isinstance(data, dict):
            if "level" in data:
                out["level"] = data["level"]
            if "window_minutes" in data:
                out["window_minutes"] = data["window_minutes"]
        return out

    def get_quota(self, api_key: str | None = None) -> dict[str, Any]:
        """Fetches token quota for one key (defaults to the sticky active key)."""
        key = api_key or self.api_key
        url = f"{self.base_url}/quota"
        try:
            resp = httpx.get(
                url,
                headers=self._headers_for(key),
                timeout=20.0,
                verify=get_ssl_verify(),
            )
            daily_remaining = resp.headers.get("x-evren-daily-remaining-tokens")
            if daily_remaining and key == self.api_key:
                try:
                    self.last_remaining_tokens = int(daily_remaining)
                except ValueError:
                    pass

            resp.raise_for_status()
            raw = resp.json() if resp.content else {}
            if not isinstance(raw, dict):
                raw = {"raw": raw}
            return self._normalize_quota(raw, resp.headers)
        except Exception as e:
            fallback = {
                "error": str(e),
                "remaining_daily_tokens": (
                    self.last_remaining_tokens if key == self.api_key else "Bilinmiyor"
                ),
            }
            return self._normalize_quota(fallback)

    def get_all_quotas(self) -> list[dict[str, Any]]:
        """Fetches quota for every key in the pool (masked key + normalized fields)."""
        from src.api_key_pool import mask_key

        current = self.key_pool.current_key
        results: list[dict[str, Any]] = []
        for pk in self.key_pool.keys:
            q = self.get_quota(api_key=pk.key)
            results.append(
                {
                    "key": pk.key,
                    "masked": mask_key(pk.key),
                    "active": pk.key == current,
                    "state": pk.state.value,
                    **q,
                }
            )
        return results

    def chat_complete(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
        max_retries: int = 3,
    ) -> Any:
        """
        Sends a standard (non-streaming) chat completion request with retry logic.
        """
        chosen_model = model or self.default_model
        tokens = max_tokens or EVREN_DEFAULT_MAX_TOKENS
        temp = temperature if temperature is not None else EVREN_DEFAULT_TEMPERATURE

        # Ensure reasoning models have enough tokens
        if chosen_model in ("deepseek-v4.1-flash", "glm-5.3") and tokens < 2048:
            tokens = 4096

        kwargs: dict[str, Any] = {
            "model": chosen_model,
            "messages": messages,
            "max_tokens": tokens,
            "temperature": temp,
        }
        # Reasoning yoğunluğu: düşük değer düşünmeyi kısıp koda daha çok token
        # bırakır (daha hızlı yanıt). Yalnızca reasoning modellerinde gönderilir.
        effort = getattr(self, "reasoning_effort", None)
        if effort and chosen_model in ("deepseek-v4.1-flash", "glm-5.3"):
            kwargs["reasoning_effort"] = effort
        if tools:
            kwargs["tools"] = tools
            if tool_choice:
                kwargs["tool_choice"] = tool_choice

        last_error: Exception | None = None
        attempt = 0

        while attempt < max_retries:
            # Pick the sticky current key (failover if unavailable). Raises
            # RateLimitError if every key is cooling/disabled.
            try:
                current_key, client = self._acquire_client()
            except RateLimitError as e:
                last_error = e
                if attempt < max_retries - 1 and e.retry_after and e.retry_after > 0:
                    time.sleep(e.retry_after)
                    self.key_pool.clear_all_cooldowns()
                    attempt += 1
                    continue
                raise

            try:
                response = client.chat.completions.create(**kwargs)
                self.key_pool.report_success(current_key)
                return response

            except Exception as e:
                typed = map_sdk_exception(e, failed_key=current_key)
                last_error = typed

                # Server may not support `reasoning_effort` (400 BadRequest).
                # Strip it and retry once so the request still succeeds.
                if "reasoning_effort" in kwargs and "reasoning_effort" in str(e).lower():
                    kwargs.pop("reasoning_effort", None)
                    self.reasoning_effort = None
                    continue

                # Terms not accepted: prompt once and retry the same request.
                if isinstance(typed, TermsNotAcceptedError):
                    ensure_terms_accepted(interactive=True, api_key=current_key)
                    continue

                # Daily quota exhausted -> disable this key and failover
                # (waiting will not help until the quota resets).
                if isinstance(typed, QuotaExhaustedError):
                    self.key_pool.report_disabled(current_key, reason="quota_exhausted")
                    if self.key_pool.has_available():
                        continue
                    raise typed from e

                # Auth/payment/forbidden -> disable this key and failover.
                if typed.failover:
                    self.key_pool.report_disabled(current_key, reason="auth_error")
                    continue

                # Rate limited -> cool this key down (respect Retry-After).
                if isinstance(typed, RateLimitError):
                    self.key_pool.report_rate_limited(current_key, retry_after=typed.retry_after)
                    # If another key is available, failover immediately.
                    if self.key_pool.has_available():
                        continue
                    # Single-key (or all cooling): wait out the backoff, then
                    # clear the cooldown so the retry is not blocked by the gate.
                    if attempt < max_retries - 1:
                        wait_time = typed.retry_after if typed.retry_after is not None else (attempt + 1) * 3
                        time.sleep(wait_time)
                        self.key_pool.clear_cooldown(current_key)
                        attempt += 1
                        continue
                    raise typed from e

                # Other retryable errors (5xx/network) -> backoff, then retry.
                if typed.retryable and attempt < max_retries - 1:
                    wait_time = typed.retry_after if typed.retry_after is not None else (attempt + 1) * 3
                    time.sleep(wait_time)
                    attempt += 1
                    continue

                # Non-retryable (or retries exhausted): surface the typed error.
                raise typed from e

        # All attempts consumed without success.
        if last_error is not None:
            raise last_error
        raise RateLimitError("İstek tamamlanamadı: kullanılabilir API anahtarı yok.")

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> Generator[dict[str, Any], None, None]:
        """
        Streams chat completion tokens, yielding dicts with:
        {'type': 'reasoning', 'content': '...'} or
        {'type': 'content', 'content': '...'} or
        {'type': 'tool_call', 'tool_call': ...}
        
        Handles EVREN SSE keepalive lines (: keep-alive) and token reasoning deltas.
        """
        chosen_model = model or self.default_model
        tokens = max_tokens or EVREN_DEFAULT_MAX_TOKENS
        temp = temperature if temperature is not None else EVREN_DEFAULT_TEMPERATURE

        if chosen_model in ("deepseek-v4.1-flash", "glm-5.3") and tokens < 2048:
            tokens = 4096

        kwargs: dict[str, Any] = {
            "model": chosen_model,
            "messages": messages,
            "max_tokens": tokens,
            "temperature": temp,
            "stream": True,
        }
        effort = getattr(self, "reasoning_effort", None)
        if effort and chosen_model in ("deepseek-v4.1-flash", "glm-5.3"):
            kwargs["reasoning_effort"] = effort
        if tools:
            kwargs["tools"] = tools

        # Acquire a key for this stream. NOTE: once streaming starts we never
        # retry/failover (a partial response must not be duplicated). Before any
        # token is emitted, however, a quota/auth failure may failover safely.
        max_attempts = max(1, len(self.key_pool))
        attempt = 0
        stream = None
        while True:
            attempt += 1
            try:
                current_key, client = self._acquire_client()
            except Exception as e:
                yield {"type": "error", "content": f"API Hatası: {e}"}
                return

            try:
                # Streaming uses a longer read timeout (reasoning models pause between tokens).
                stream = client.chat.completions.create(timeout=STREAM_TIMEOUT, **kwargs)
                break
            except Exception as e:
                typed = map_sdk_exception(e, failed_key=current_key)
                if isinstance(typed, TermsNotAcceptedError):
                    ensure_terms_accepted(interactive=True, api_key=current_key)
                    yield {"type": "error", "content": "Kullanım şartları onaylandı. Lütfen isteğinizi tekrarlayın."}
                    return
                if isinstance(typed, QuotaExhaustedError):
                    self.key_pool.report_disabled(current_key, reason="quota_exhausted")
                    if attempt < max_attempts and self.key_pool.has_available():
                        continue
                    yield {"type": "error", "content": f"API Hatası: {e}"}
                    return
                if typed.failover:
                    self.key_pool.report_disabled(current_key, reason="auth_error")
                    if attempt < max_attempts and self.key_pool.has_available():
                        continue
                    yield {"type": "error", "content": f"API Hatası: {e}"}
                    return
                yield {"type": "error", "content": f"API Hatası: {e}"}
                return

        try:
            for chunk in stream:
                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta

                # Reasoning delta (supported by DeepSeek, GLM, Qwen reasoning models)
                reasoning = (
                    getattr(delta, "reasoning", None)
                    or getattr(delta, "reasoning_content", None)
                )
                if reasoning:
                    yield {"type": "reasoning", "content": reasoning}

                # Regular response content
                if delta.content:
                    yield {"type": "content", "content": delta.content}

                # Tool calls in stream
                if getattr(delta, "tool_calls", None):
                    yield {"type": "tool_calls", "tool_calls": delta.tool_calls}

            # Stream finished cleanly.
            self.key_pool.report_success(current_key)

        except Exception as e:
            typed = map_sdk_exception(e, failed_key=current_key)
            if isinstance(typed, TermsNotAcceptedError):
                ensure_terms_accepted(interactive=True, api_key=current_key)
                yield {"type": "error", "content": "Kullanım şartları onaylandı. Lütfen isteğinizi tekrarlayın."}
            else:
                # NOTE: no retry/failover once streaming has started — a partial
                # response must not be duplicated. The caller decides what to do.
                yield {"type": "error", "content": f"API Hatası: {e}"}

    def chat_stream_assembled(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        tools: list[dict[str, Any]] | None = None,
        on_reasoning: Any = None,
        on_content: Any = None,
    ) -> Any:
        """Streams a completion, invoking live callbacks, and returns an
        assembled response object shaped like a non-streaming OpenAI response.

        This lets the agent loop stream tokens to the terminal (so the UI never
        looks frozen) while still receiving a single `response.choices[0].message`
        with fully-accumulated `content` and `tool_calls`.

        Callbacks (optional):
          - on_reasoning(text): called for each reasoning delta.
          - on_content(text):   called for each content delta.

        Raises the mapped typed error on failure (no partial retry).
        """
        chosen_model = model or self.default_model
        tokens = max_tokens or EVREN_DEFAULT_MAX_TOKENS
        temp = temperature if temperature is not None else EVREN_DEFAULT_TEMPERATURE

        if chosen_model in ("deepseek-v4.1-flash", "glm-5.3") and tokens < 2048:
            tokens = 4096

        kwargs: dict[str, Any] = {
            "model": chosen_model,
            "messages": messages,
            "max_tokens": tokens,
            "temperature": temp,
            "stream": True,
        }
        effort = getattr(self, "reasoning_effort", None)
        if effort and chosen_model in ("deepseek-v4.1-flash", "glm-5.3"):
            kwargs["reasoning_effort"] = effort
        if tools:
            kwargs["tools"] = tools

        # Failover loop: if a key is out of daily quota (or auth-fails) *before*
        # any token has been streamed, switch to the next key and retry. Once
        # streaming has produced output we must NOT retry (no duplicate output).
        max_attempts = max(1, len(self.key_pool))
        attempt = 0
        while True:
            attempt += 1
            current_key, client = self._acquire_client()

            message = _StreamedMessage()
            usage: Any = None
            # Tool calls arrive as deltas keyed by index; accumulate them in order.
            tool_calls_by_index: dict[int, _StreamedToolCall] = {}
            streamed_any = False

            try:
                stream = client.chat.completions.create(timeout=STREAM_TIMEOUT, **kwargs)
                break
            except Exception as e:
                typed = map_sdk_exception(e, failed_key=current_key)
                if isinstance(typed, TermsNotAcceptedError):
                    ensure_terms_accepted(interactive=True, api_key=current_key)
                    raise typed from e
                # Daily quota exhausted -> disable this key and failover.
                if isinstance(typed, QuotaExhaustedError):
                    self.key_pool.report_disabled(current_key, reason="quota_exhausted")
                    if attempt < max_attempts and self.key_pool.has_available():
                        continue
                    raise typed from e
                # Auth/payment/forbidden -> disable this key and failover.
                if typed.failover:
                    self.key_pool.report_disabled(current_key, reason="auth_error")
                    if attempt < max_attempts and self.key_pool.has_available():
                        continue
                    raise typed from e
                raise typed from e

        try:
            for chunk in stream:
                # Usage may arrive on a final chunk with empty choices.
                chunk_usage = getattr(chunk, "usage", None)
                if chunk_usage is not None:
                    usage = chunk_usage

                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta

                reasoning = (
                    getattr(delta, "reasoning", None)
                    or getattr(delta, "reasoning_content", None)
                )
                if reasoning:
                    streamed_any = True
                    message.reasoning += reasoning
                    if on_reasoning:
                        on_reasoning(reasoning)

                if delta.content:
                    streamed_any = True
                    message.content += delta.content
                    if on_content:
                        on_content(delta.content)

                for tc_delta in getattr(delta, "tool_calls", None) or []:
                    idx = getattr(tc_delta, "index", 0) or 0
                    acc = tool_calls_by_index.get(idx)
                    if acc is None:
                        acc = _StreamedToolCall(idx)
                        tool_calls_by_index[idx] = acc
                    if getattr(tc_delta, "id", None):
                        acc.id = tc_delta.id
                    if getattr(tc_delta, "type", None):
                        acc.type = tc_delta.type
                    fn = getattr(tc_delta, "function", None)
                    if fn is not None:
                        if getattr(fn, "name", None):
                            acc.function.name += fn.name
                        if getattr(fn, "arguments", None):
                            acc.function.arguments += fn.arguments

            message.tool_calls = [tool_calls_by_index[i] for i in sorted(tool_calls_by_index)]
            self.key_pool.report_success(current_key)
            return _StreamedResponse(message, usage=usage)

        except Exception as e:
            typed = map_sdk_exception(e, failed_key=current_key)
            if isinstance(typed, TermsNotAcceptedError):
                ensure_terms_accepted(interactive=True, api_key=current_key)
                raise typed from e
            # Mid-stream quota/auth failure: only failover if nothing was
            # streamed yet (otherwise a partial response would be duplicated).
            if not streamed_any and isinstance(typed, QuotaExhaustedError):
                self.key_pool.report_disabled(current_key, reason="quota_exhausted")
                if attempt < max_attempts and self.key_pool.has_available():
                    return self.chat_stream_assembled(
                        messages=messages,
                        model=model,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        tools=tools,
                        on_reasoning=on_reasoning,
                        on_content=on_content,
                    )
            raise typed from e
