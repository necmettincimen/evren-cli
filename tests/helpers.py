"""
Shared test helpers for EVREN CLI.

Kept framework-agnostic so it works with both `unittest` (current suite) and
`pytest` (if adopted later). Provides:

- FakeClock: a deterministic replacement for `time.sleep` so retry/backoff
  tests run instantly and assert exact wait durations.
- make_status_error / make_connection_error: build OpenAI SDK exceptions
  without hitting the network, for error-mapping tests.
"""

from __future__ import annotations

from typing import Any


class FakeClock:
    """Records requested sleeps instead of actually sleeping.

    Usage:
        clock = FakeClock()
        with clock.patch():
            ...  # code under test calls time.sleep(...)
        assert clock.sleeps == [3, 6]
    """

    def __init__(self):
        self.sleeps: list[float] = []
        self._original = None

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def patch(self):
        """Context manager that swaps `time.sleep` in the api_client module."""
        import src.api_client as api_client

        clock = self

        class _Ctx:
            def __enter__(self_inner):
                self_inner._orig = api_client.time.sleep
                api_client.time.sleep = clock.sleep
                return clock

            def __exit__(self_inner, *exc):
                api_client.time.sleep = self_inner._orig
                return False

        return _Ctx()

    @property
    def total(self) -> float:
        return sum(self.sleeps)


def make_status_error(status_code: int, message: str = "error", retry_after: str | None = None):
    """Builds an `openai.APIStatusError` with a fake response (no network)."""
    import httpx
    from openai import APIStatusError

    headers = {}
    if retry_after is not None:
        headers["retry-after"] = retry_after

    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    response = httpx.Response(status_code, headers=headers, request=request)
    return APIStatusError(message, response=response, body=None)


def make_connection_error(message: str = "connection failed"):
    """Builds an `openai.APIConnectionError` (no network)."""
    import httpx
    from openai import APIConnectionError

    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    return APIConnectionError(request=request)


class FakeCompletion:
    """Minimal stand-in for an OpenAI chat completion response object."""

    def __init__(self, content: str = "ok", reasoning: str | None = None, tool_calls: Any = None):
        message = _FakeMessage(content=content, reasoning=reasoning, tool_calls=tool_calls)
        self.choices = [_FakeChoice(message)]


class _FakeMessage:
    def __init__(self, content: str, reasoning: str | None, tool_calls: Any):
        self.content = content
        self.reasoning = reasoning
        self.reasoning_content = reasoning
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, message):
        self.message = message
