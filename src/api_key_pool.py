"""
API key pool for EVREN CLI.

Ports the C# `ApiKeyPool.cs` behaviour: multiple API keys with per-key state
(Ready / Cooling / Disabled), sticky active-key acquisition (manual `/keys next`
or automatic failover), and success / rate-limit / disable reporting. Keys are
masked for display.

SDK reality: each key needs its own `OpenAI` client instance. We cache one client
per key so switching keys does not rebuild a client on every request.

Key sources:
- `EVREN_API_KEYS`  : comma-separated list (preferred for multiple keys)
- `EVREN_API_KEY`   : single key (backward compatible)
- `~/.evren-cli/config.json` `ApiKeys` (loaded at startup; `/keys add|remove` persist here)
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class KeyState(str, Enum):
    READY = "ready"
    COOLING = "cooling"
    DISABLED = "disabled"


# Backoff applied when a key hits a rate limit (seconds).
DEFAULT_COOLDOWN_SECONDS = 60.0


def mask_key(key: str) -> str:
    """Masks an API key for safe display, e.g. 'evren_llm_ab...yz'."""
    if not key:
        return "(boş)"
    if len(key) <= 12:
        return key[:4] + "..." + key[-2:]
    return f"{key[:10]}...{key[-4:]}"


def parse_api_keys() -> list[str]:
    """Collects API keys from EVREN_API_KEYS (comma-separated) and EVREN_API_KEY.

    Order: EVREN_API_KEYS entries first, then EVREN_API_KEY if not already present.
    Duplicates are removed while preserving order.
    """
    keys: list[str] = []

    multi = os.getenv("EVREN_API_KEYS", "")
    if multi:
        for raw in multi.split(","):
            k = raw.strip()
            if k:
                keys.append(k)

    single = os.getenv("EVREN_API_KEY", "").strip()
    if single:
        keys.append(single)

    seen: set[str] = set()
    unique: list[str] = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            unique.append(k)
    return unique


@dataclass
class PooledKey:
    """A single API key with its runtime state."""

    key: str
    state: KeyState = KeyState.READY
    cooldown_until: float = 0.0
    failure_count: int = 0
    success_count: int = 0
    last_error: str | None = None

    @property
    def masked(self) -> str:
        return mask_key(self.key)

    def is_available(self, now: float | None = None) -> bool:
        now = now if now is not None else time.monotonic()
        if self.state == KeyState.DISABLED:
            return False
        if self.state == KeyState.COOLING:
            if now >= self.cooldown_until:
                # Cooldown elapsed -> back to ready.
                self.state = KeyState.READY
                self.cooldown_until = 0.0
                return True
            return False
        return True

    def next_available_in(self, now: float | None = None) -> float:
        """Seconds until this key becomes available again (0 if already available)."""
        now = now if now is not None else time.monotonic()
        if self.state == KeyState.COOLING and self.cooldown_until > now:
            return self.cooldown_until - now
        return 0.0


class ApiKeyPool:
    """Round-robin pool of API keys with per-key state and client caching."""

    def __init__(
        self,
        keys: list[str] | None = None,
        *,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
        base_url: str | None = None,
    ):
        raw_keys = keys if keys is not None else parse_api_keys()
        self._keys: list[PooledKey] = [PooledKey(k) for k in raw_keys if k]
        self._index: int = 0
        # The client defaults to keys[0]; treat it as the "current" key so that
        # a manual /keys next skips it and moves to the following one.
        self._last_key: str | None = self._keys[0].key if self._keys else None
        self.cooldown_seconds = cooldown_seconds
        self.base_url = base_url
        self._client_cache: dict[str, Any] = {}

    # ---- introspection -------------------------------------------------

    @property
    def keys(self) -> list[PooledKey]:
        return self._keys

    @property
    def current_key(self) -> str | None:
        """Last selected / last-used key (the one /keys next pins for next acquire)."""
        return self._last_key

    def __len__(self) -> int:
        return len(self._keys)

    def is_empty(self) -> bool:
        return not self._keys

    def has_available(self, now: float | None = None) -> bool:
        return any(pk.is_available(now) for pk in self._keys)

    def next_available_utc(self, now: float | None = None) -> float:
        """Seconds until the soonest key becomes available (0 if any is ready)."""
        now = now if now is not None else time.monotonic()
        waits = [pk.next_available_in(now) for pk in self._keys]
        waits = [w for w in waits if w > 0]
        return min(waits) if waits else 0.0

    def state_counts(self, now: float | None = None) -> dict[str, int]:
        """Returns key counts by current effective state."""
        now = now if now is not None else time.monotonic()
        ready = 0
        cooling = 0
        disabled = 0
        for pk in self._keys:
            if pk.state == KeyState.DISABLED:
                disabled += 1
            elif pk.is_available(now):
                ready += 1
            else:
                cooling += 1
        return {"ready": ready, "cooling": cooling, "disabled": disabled}

    # ---- acquisition ---------------------------------------------------

    def _key_index(self, key: str | None) -> int | None:
        if not key:
            return None
        for i, pk in enumerate(self._keys):
            if pk.key == key:
                return i
        return None

    def _pin(self, idx: int, pk: PooledKey) -> PooledKey:
        """Pins `pk` as the sticky current key."""
        self._index = idx
        self._last_key = pk.key
        return pk

    def _first_available_from(self, start: int, now: float) -> PooledKey | None:
        n = len(self._keys)
        for offset in range(n):
            idx = (start + offset) % n
            pk = self._keys[idx]
            if pk.is_available(now):
                return self._pin(idx, pk)
        return None

    def acquire(self, now: float | None = None) -> PooledKey | None:
        """Returns the sticky current key, or failovers to the next available one.

        The active key stays pinned until `/keys next` (rotate) or until it
        becomes unavailable (cooling/disabled), in which case the next ready
        key is pinned automatically.
        """
        now = now if now is not None else time.monotonic()
        n = len(self._keys)
        if n == 0:
            return None

        cur_idx = self._key_index(self._last_key)
        if cur_idx is not None:
            pk = self._keys[cur_idx]
            if pk.is_available(now):
                return self._pin(cur_idx, pk)
            # Sticky key unavailable -> failover starting after it.
            start = (cur_idx + 1) % n
        else:
            start = self._index

        return self._first_available_from(start, now)

    def rotate(self, now: float | None = None) -> PooledKey | None:
        """Manually switches to the next available key (used by /keys next).

        Pins the selected key so subsequent acquire() calls keep using it
        until the next rotate() or an automatic failover.
        """
        now = now if now is not None else time.monotonic()
        n = len(self._keys)
        if n == 0:
            return None

        cur_idx = self._key_index(self._last_key)
        if cur_idx is None:
            start = self._index
        else:
            start = (cur_idx + 1) % n

        return self._first_available_from(start, now)

    # ---- reporting -----------------------------------------------------

    def report_success(self, key: str) -> None:
        pk = self._find(key)
        if pk:
            pk.success_count += 1
            pk.failure_count = 0
            pk.state = KeyState.READY
            pk.cooldown_until = 0.0
            pk.last_error = None

    def report_rate_limited(self, key: str, retry_after: float | None = None) -> None:
        pk = self._find(key)
        if not pk:
            return
        pk.failure_count += 1
        pk.state = KeyState.COOLING
        wait = retry_after if retry_after is not None else self.cooldown_seconds
        pk.cooldown_until = time.monotonic() + wait
        pk.last_error = "rate_limited"

    def report_disabled(self, key: str, reason: str = "auth_error") -> None:
        pk = self._find(key)
        if not pk:
            return
        pk.failure_count += 1
        pk.state = KeyState.DISABLED
        pk.last_error = reason

    def reset_states(self) -> None:
        """Resets all keys to READY (used by the /keys reset command)."""
        for pk in self._keys:
            pk.state = KeyState.READY
            pk.cooldown_until = 0.0
            pk.failure_count = 0
            pk.last_error = None

    def clear_cooldown(self, key: str) -> None:
        """Clears a key's cooldown so it becomes READY again.

        Used after an explicit backoff wait when no other key is available
        (single-key scenario): the caller has already waited, so the cooldown
        gate must not block the retry.
        """
        pk = self._find(key)
        if pk and pk.state == KeyState.COOLING:
            pk.state = KeyState.READY
            pk.cooldown_until = 0.0

    def clear_all_cooldowns(self) -> None:
        """Clears cooldown on all cooling keys."""
        for pk in self._keys:
            if pk.state == KeyState.COOLING:
                pk.state = KeyState.READY
                pk.cooldown_until = 0.0

    # ---- mutation ------------------------------------------------------

    def key_values(self) -> list[str]:
        """Returns the raw key strings currently in the pool (for persistence)."""
        return [pk.key for pk in self._keys]

    def add_key(self, key: str) -> bool:
        key = key.strip()
        if not key or self._find(key):
            return False
        self._keys.append(PooledKey(key))
        if self._last_key is None:
            self._last_key = key
            self._index = 0
        return True

    def remove_key(self, key: str) -> bool:
        pk = self._find(key)
        if not pk:
            return False
        was_idx = self._key_index(key) or 0
        was_current = self._last_key == key
        self._keys.remove(pk)
        self._client_cache.pop(key, None)
        if not self._keys:
            self._index = 0
            self._last_key = None
            return True
        if was_current:
            # After removal, the next key slides into was_idx (or wraps to 0).
            start = was_idx % len(self._keys)
            pinned = self._first_available_from(start, time.monotonic())
            if pinned is None:
                self._index = 0
                self._last_key = self._keys[0].key
        else:
            cur = self._key_index(self._last_key)
            self._index = cur if cur is not None else 0
        return True

    # ---- SDK client cache ----------------------------------------------

    def get_client(self, key: str) -> Any:
        """Returns a cached OpenAI client for `key`, creating it on first use."""
        if key in self._client_cache:
            return self._client_cache[key]

        from openai import OpenAI
        from src.api_client import REQUEST_TIMEOUT
        from src.config import get_ssl_verify
        import httpx

        # Pass an explicit httpx client so the SSL verification setting
        # (custom CA bundle or disabled verification) is honoured by the SDK.
        http_client = httpx.Client(verify=get_ssl_verify(), timeout=REQUEST_TIMEOUT)

        client = OpenAI(
            base_url=self.base_url,
            api_key=key,
            default_headers={"X-API-Key": key},
            max_retries=0,
            timeout=REQUEST_TIMEOUT,
            http_client=http_client,
        )
        self._client_cache[key] = client
        return client

    # ---- helpers -------------------------------------------------------

    def _find(self, key: str) -> PooledKey | None:
        for pk in self._keys:
            if pk.key == key:
                return pk
        return None
