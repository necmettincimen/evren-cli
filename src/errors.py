"""
Error taxonomy for EVREN CLI.

Provides a structured exception hierarchy so that retry / failover decisions
are made on typed errors instead of fragile string matching ("429" in str(e)).

Mapping from the OpenAI SDK exceptions happens in `map_sdk_exception`.
"""

from __future__ import annotations

from typing import Any


class EvrenApiError(Exception):
    """Base class for all EVREN API errors.

    Attributes:
        status_code: HTTP status code if available, else None.
        retryable:   Whether retrying the *same* key may succeed (429/5xx/network).
        failover:    Whether switching to another API key may succeed (401/402/403).
        retry_after: Seconds to wait before retrying (from Retry-After header), if any.
        failed_key:  The API key that produced this error (masked by callers).
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
        failover: bool = False,
        retry_after: float | None = None,
        failed_key: str | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
        self.failover = failover
        self.retry_after = retry_after
        self.failed_key = failed_key

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"{self.__class__.__name__}(status_code={self.status_code!r}, "
            f"retryable={self.retryable!r}, failover={self.failover!r}, "
            f"retry_after={self.retry_after!r})"
        )


class RateLimitError(EvrenApiError):
    """HTTP 429 — quota / rate limit exceeded. Retryable (respect Retry-After)."""

    def __init__(self, message: str = "Rate limit aşıldı (429).", **kwargs: Any):
        kwargs.setdefault("status_code", 429)
        kwargs.setdefault("retryable", True)
        super().__init__(message, **kwargs)


class QuotaExhaustedError(RateLimitError):
    """HTTP 429 — the key's *daily* token quota is exhausted.

    Unlike a transient rate limit, waiting a few seconds will NOT help: the
    key is out of its daily allowance until the quota resets. The correct
    response is to failover to another key and disable this one (not merely
    cool it down), so it is not retried in a tight loop.
    """

    def __init__(self, message: str = "Günlük token kotası tükendi.", **kwargs: Any):
        kwargs.setdefault("status_code", 429)
        # Not retryable on the same key; failover to another key instead.
        kwargs.setdefault("retryable", False)
        kwargs.setdefault("failover", True)
        super().__init__(message, **kwargs)


class AuthError(EvrenApiError):
    """HTTP 401/402/403 — invalid key, payment required, or forbidden.

    Failover to another key is the correct response.
    """

    def __init__(self, message: str = "Kimlik doğrulama/yetkilendirme hatası.", **kwargs: Any):
        kwargs.setdefault("failover", True)
        super().__init__(message, **kwargs)


class ServerError(EvrenApiError):
    """HTTP 5xx — upstream server failure. Retryable."""

    def __init__(self, message: str = "Sunucu hatası (5xx).", **kwargs: Any):
        kwargs.setdefault("retryable", True)
        super().__init__(message, **kwargs)


class NetworkError(EvrenApiError):
    """Connection / timeout error. Retryable."""

    def __init__(self, message: str = "Ağ bağlantı hatası.", **kwargs: Any):
        kwargs.setdefault("retryable", True)
        super().__init__(message, **kwargs)


class TermsNotAcceptedError(AuthError):
    """Special 403 case: EVREN terms of service not yet accepted."""

    def __init__(self, message: str = "EVREN kullanım şartları kabul edilmemiş.", **kwargs: Any):
        kwargs.setdefault("status_code", 403)
        kwargs.setdefault("failover", False)
        super().__init__(message, **kwargs)


# Markers that indicate a *daily quota* exhaustion rather than a transient
# rate limit. Matched case-insensitively against the error message/body.
_QUOTA_MARKERS = (
    "insufficient_quota",
    "quota_exceeded",
    "quota exceeded",
    "daily quota",
    "daily_quota",
    "daily limit",
    "daily_limit",
    "exceeded your current quota",
    "out of quota",
    "no remaining tokens",
    "günlük kota",
    "günlük limit",
    "kota tükendi",
    "kota aşıldı",
    "kota doldu",
)


def _looks_like_quota_exhausted(text: str) -> bool:
    """True if the error text signals a daily-quota exhaustion."""
    low = (text or "").lower()
    return any(marker in low for marker in _QUOTA_MARKERS)


def _error_text(exc: Exception) -> str:
    """Collects the message + response body of an SDK error for inspection."""
    parts = [str(exc)]
    body = getattr(exc, "body", None)
    if body:
        parts.append(str(body))
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            parts.append(response.text)
        except Exception:  # pragma: no cover - defensive
            pass
    return " ".join(parts)


def _extract_retry_after(response: Any) -> float | None:
    """Reads the Retry-After header from an SDK response object, if present."""
    try:
        headers = getattr(response, "headers", None)
        if not headers:
            return None
        raw = headers.get("retry-after") or headers.get("Retry-After")
        if raw is None:
            return None
        return float(raw)
    except (TypeError, ValueError):
        return None


def map_sdk_exception(exc: Exception, failed_key: str | None = None) -> EvrenApiError:
    """Maps an OpenAI SDK / httpx exception to a typed EvrenApiError.

    Falls back to a generic EvrenApiError for unknown exceptions so callers
    always receive a typed error.
    """
    # Import lazily so this module stays importable without the SDK installed.
    try:
        import openai
    except ImportError:  # pragma: no cover
        openai = None  # type: ignore

    err_str = str(exc)

    # Terms-not-accepted is signalled either by a 403 or a body marker.
    if "terms_not_accepted" in err_str:
        return TermsNotAcceptedError(failed_key=failed_key)

    if openai is not None:
        # Status errors carry an HTTP response with a status code + headers.
        if isinstance(exc, openai.APIStatusError):
            status = getattr(exc, "status_code", None)
            retry_after = _extract_retry_after(getattr(exc, "response", None))
            if status == 429:
                if _looks_like_quota_exhausted(_error_text(exc)):
                    return QuotaExhaustedError(str(exc), retry_after=retry_after, failed_key=failed_key)
                return RateLimitError(str(exc), retry_after=retry_after, failed_key=failed_key)
            if status in (401, 402, 403):
                return AuthError(str(exc), status_code=status, failed_key=failed_key)
            if status is not None and 500 <= status < 600:
                return ServerError(str(exc), status_code=status, failed_key=failed_key)
            return EvrenApiError(str(exc), status_code=status, failed_key=failed_key)

        if isinstance(exc, openai.APIConnectionError):
            return NetworkError(str(exc), failed_key=failed_key)

        if isinstance(exc, openai.APITimeoutError):
            return NetworkError(str(exc), failed_key=failed_key)

    # httpx fallbacks (used by terms.py and direct httpx calls).
    try:
        import httpx
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            retry_after = _extract_retry_after(exc.response)
            if status == 429:
                if _looks_like_quota_exhausted(_error_text(exc)):
                    return QuotaExhaustedError(str(exc), retry_after=retry_after, failed_key=failed_key)
                return RateLimitError(str(exc), retry_after=retry_after, failed_key=failed_key)
            if status in (401, 402, 403):
                return AuthError(str(exc), status_code=status, failed_key=failed_key)
            if 500 <= status < 600:
                return ServerError(str(exc), status_code=status, failed_key=failed_key)
            return EvrenApiError(str(exc), status_code=status, failed_key=failed_key)
        if isinstance(exc, httpx.RequestError):
            return NetworkError(str(exc), failed_key=failed_key)
    except ImportError:  # pragma: no cover
        pass

    # Last-resort string heuristics (kept minimal; prefer typed mapping above).
    if "429" in err_str:
        if _looks_like_quota_exhausted(err_str):
            return QuotaExhaustedError(err_str, failed_key=failed_key)
        return RateLimitError(err_str, failed_key=failed_key)
    if "503" in err_str or "502" in err_str or "500" in err_str:
        return ServerError(err_str, failed_key=failed_key)

    return EvrenApiError(err_str, failed_key=failed_key)
