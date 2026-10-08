import unittest
from unittest.mock import patch, MagicMock

from src.api_client import EvrenClient
from src.api_key_pool import ApiKeyPool
from src.errors import RateLimitError, QuotaExhaustedError, AuthError, ServerError
from tests.helpers import FakeClock, make_status_error, FakeCompletion


def _make_client(keys=None):
    """Builds an EvrenClient with a pool but without real SDK/network setup."""
    client = EvrenClient.__new__(EvrenClient)
    client.base_url = "https://example.test/v1"
    client.default_model = "deepseek-v4.1-flash"
    client.last_remaining_tokens = None
    client.key_pool = ApiKeyPool(keys or ["evren_llm_test"])
    client.api_key = client.key_pool.keys[0].key
    return client


def _fake_client_with(side_effect):
    """Returns a MagicMock OpenAI client whose create() uses given side_effect."""
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = side_effect
    return mock_client


class TestChatCompleteRetry(unittest.TestCase):
    def test_success_first_try(self):
        client = _make_client()
        fake = _fake_client_with([FakeCompletion("hello")])
        with patch.object(client.key_pool, "get_client", return_value=fake):
            resp = client.chat_complete(messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(resp.choices[0].message.content, "hello")

    def test_retry_on_429_then_success(self):
        # Single key: 429 must cool down, wait, clear cooldown, then retry.
        client = _make_client()
        clock = FakeClock()
        fake = _fake_client_with([
            make_status_error(429, "rate limited"),
            FakeCompletion("recovered"),
        ])
        with patch.object(client.key_pool, "get_client", return_value=fake):
            with clock.patch():
                resp = client.chat_complete(messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(resp.choices[0].message.content, "recovered")
        self.assertEqual(len(clock.sleeps), 1)  # waited once before retrying

    def test_retry_after_header_respected(self):
        client = _make_client()
        clock = FakeClock()
        fake = _fake_client_with([
            make_status_error(429, "rate limited", retry_after="5"),
            FakeCompletion("ok"),
        ])
        with patch.object(client.key_pool, "get_client", return_value=fake):
            with clock.patch():
                client.chat_complete(messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(clock.sleeps, [5.0])

    def test_retry_exhausted_raises_typed(self):
        client = _make_client()
        clock = FakeClock()
        fake = _fake_client_with(make_status_error(503, "down"))
        with patch.object(client.key_pool, "get_client", return_value=fake):
            with clock.patch():
                with self.assertRaises(ServerError):
                    client.chat_complete(messages=[{"role": "user", "content": "hi"}], max_retries=3)
        self.assertEqual(len(clock.sleeps), 2)  # 3 attempts -> 2 sleeps

    def test_auth_error_disables_key(self):
        client = _make_client(keys=["bad", "good"])
        fake = _fake_client_with([
            make_status_error(401, "bad key"),
            FakeCompletion("ok with good key"),
        ])
        with patch.object(client.key_pool, "get_client", return_value=fake):
            resp = client.chat_complete(messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(resp.choices[0].message.content, "ok with good key")
        # The bad key must be disabled after the 401.
        self.assertEqual(client.key_pool.keys[0].state.value, "disabled")

    def test_rate_limit_failover_to_next_key(self):
        client = _make_client(keys=["k1", "k2"])
        fake = _fake_client_with([
            make_status_error(429, "rate limited"),
            FakeCompletion("served by k2"),
        ])
        with patch.object(client.key_pool, "get_client", return_value=fake):
            resp = client.chat_complete(messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(resp.choices[0].message.content, "served by k2")
        # k1 should now be cooling.
        self.assertEqual(client.key_pool.keys[0].state.value, "cooling")

    def test_failover_does_not_consume_retry_budget(self):
        client = _make_client(keys=["k1", "k2", "k3"])
        fake = _fake_client_with([
            make_status_error(401, "bad key 1"),
            make_status_error(401, "bad key 2"),
            FakeCompletion("served by k3"),
        ])
        with patch.object(client.key_pool, "get_client", return_value=fake):
            resp = client.chat_complete(
                messages=[{"role": "user", "content": "hi"}],
                max_retries=1,
            )
        self.assertEqual(resp.choices[0].message.content, "served by k3")

    def test_daily_quota_disables_key_and_failover(self):
        # A daily-quota 429 must DISABLE the key (not merely cool it) and
        # immediately failover to the next key.
        client = _make_client(keys=["k1", "k2"])
        fake = _fake_client_with([
            make_status_error(429, "insufficient_quota: daily quota exceeded"),
            FakeCompletion("served by k2"),
        ])
        with patch.object(client.key_pool, "get_client", return_value=fake):
            resp = client.chat_complete(messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(resp.choices[0].message.content, "served by k2")
        self.assertEqual(client.key_pool.keys[0].state.value, "disabled")
        self.assertEqual(client.key_pool.keys[0].last_error, "quota_exhausted")

    def test_daily_quota_all_keys_raises_quota_error(self):
        client = _make_client(keys=["k1"])
        fake = _fake_client_with(make_status_error(429, "daily quota exceeded"))
        with patch.object(client.key_pool, "get_client", return_value=fake):
            with self.assertRaises(QuotaExhaustedError):
                client.chat_complete(messages=[{"role": "user", "content": "hi"}])

    def test_all_keys_unavailable_raises(self):
        client = _make_client(keys=["k1"])
        client.key_pool.report_disabled("k1")
        with self.assertRaises(RateLimitError):
            client.chat_complete(messages=[{"role": "user", "content": "hi"}])

    def test_all_keys_cooling_waits_then_retries(self):
        client = _make_client(keys=["k1"])
        clock = FakeClock()
        client.key_pool.report_rate_limited("k1", retry_after=2)
        fake = _fake_client_with([FakeCompletion("ok after wait")])
        with patch.object(client.key_pool, "get_client", return_value=fake):
            with clock.patch():
                resp = client.chat_complete(
                    messages=[{"role": "user", "content": "hi"}],
                    max_retries=2,
                )
        self.assertEqual(resp.choices[0].message.content, "ok after wait")
        self.assertEqual(len(clock.sleeps), 1)
        self.assertAlmostEqual(clock.sleeps[0], 2, places=1)


class TestChatStreamAssembled(unittest.TestCase):
    """Tests for chat_stream_assembled: live callbacks + assembled response."""

    @staticmethod
    def _make_stream(chunks):
        """Builds a fake stream of chunk objects.

        Each chunk is a dict with optional keys:
          content, reasoning, tool_calls (list of dicts with index/id/name/arguments).
        """
        class _Fn:
            def __init__(self, name=None, arguments=None):
                self.name = name
                self.arguments = arguments

        class _ToolCallDelta:
            def __init__(self, index, id=None, name=None, arguments=None):
                self.index = index
                self.id = id
                self.type = "function"
                self.function = _Fn(name, arguments)

        class _Delta:
            def __init__(self, content=None, reasoning=None, tool_calls=None):
                self.content = content
                self.reasoning = reasoning
                self.reasoning_content = reasoning
                self.tool_calls = tool_calls

        class _Choice:
            def __init__(self, delta):
                self.delta = delta

        class _Chunk:
            def __init__(self, delta, usage=None):
                self.choices = [_Choice(delta)] if delta is not None else []
                self.usage = usage

        result = []
        for spec in chunks:
            tcs = None
            if spec.get("tool_calls"):
                tcs = [_ToolCallDelta(**tc) for tc in spec["tool_calls"]]
            delta = _Delta(
                content=spec.get("content"),
                reasoning=spec.get("reasoning"),
                tool_calls=tcs,
            )
            result.append(_Chunk(delta, usage=spec.get("usage")))
        return result

    def test_assembles_content_and_calls_callbacks(self):
        client = _make_client(keys=["k1"])
        stream = self._make_stream([
            {"reasoning": "think "},
            {"reasoning": "more"},
            {"content": "Hel"},
            {"content": "lo"},
        ])
        fake = MagicMock()
        fake.chat.completions.create.return_value = stream

        reasoning_parts, content_parts = [], []
        with patch.object(client.key_pool, "get_client", return_value=fake):
            resp = client.chat_stream_assembled(
                messages=[{"role": "user", "content": "hi"}],
                on_reasoning=reasoning_parts.append,
                on_content=content_parts.append,
            )

        self.assertEqual(resp.choices[0].message.content, "Hello")
        self.assertEqual(resp.choices[0].message.reasoning, "think more")
        self.assertEqual("".join(reasoning_parts), "think more")
        self.assertEqual("".join(content_parts), "Hello")
        self.assertEqual(client.key_pool.keys[0].success_count, 1)

    def test_assembles_tool_calls_across_deltas(self):
        client = _make_client(keys=["k1"])
        stream = self._make_stream([
            {"tool_calls": [{"index": 0, "id": "call_1", "name": "view_", "arguments": '{"pa'}]},
            {"tool_calls": [{"index": 0, "name": "file", "arguments": 'th": "a.py"}'}]},
        ])
        fake = MagicMock()
        fake.chat.completions.create.return_value = stream

        with patch.object(client.key_pool, "get_client", return_value=fake):
            resp = client.chat_stream_assembled(messages=[{"role": "user", "content": "hi"}])

        tcs = resp.choices[0].message.tool_calls
        self.assertEqual(len(tcs), 1)
        self.assertEqual(tcs[0].id, "call_1")
        self.assertEqual(tcs[0].function.name, "view_file")
        self.assertEqual(tcs[0].function.arguments, '{"path": "a.py"}')

    def test_stream_error_raises_typed(self):
        client = _make_client(keys=["k1"])
        fake = _fake_client_with(make_status_error(503, "down"))
        with patch.object(client.key_pool, "get_client", return_value=fake):
            with self.assertRaises(ServerError):
                client.chat_stream_assembled(messages=[{"role": "user", "content": "hi"}])

    def test_stream_quota_failover_before_output(self):
        # Quota 429 before any token -> disable key and failover to next key.
        client = _make_client(keys=["k1", "k2"])
        stream = self._make_stream([{"content": "Hello"}])
        fake = MagicMock()
        fake.chat.completions.create.side_effect = [
            make_status_error(429, "insufficient_quota"),
            stream,
        ]
        with patch.object(client.key_pool, "get_client", return_value=fake):
            resp = client.chat_stream_assembled(messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(resp.choices[0].message.content, "Hello")
        self.assertEqual(client.key_pool.keys[0].state.value, "disabled")
        self.assertEqual(client.key_pool.keys[0].last_error, "quota_exhausted")

    def test_stream_quota_all_keys_raises(self):
        client = _make_client(keys=["k1"])
        fake = _fake_client_with(make_status_error(429, "daily quota exceeded"))
        with patch.object(client.key_pool, "get_client", return_value=fake):
            with self.assertRaises(QuotaExhaustedError):
                client.chat_stream_assembled(messages=[{"role": "user", "content": "hi"}])


class TestChatStreamNoRetry(unittest.TestCase):
    def test_stream_error_yields_no_retry(self):
        # Once streaming starts, an error must NOT trigger retry/failover.
        client = _make_client(keys=["k1", "k2"])
        fake = _fake_client_with(make_status_error(503, "down"))
        with patch.object(client.key_pool, "get_client", return_value=fake):
            chunks = list(client.chat_stream(messages=[{"role": "user", "content": "hi"}]))
        # Exactly one error chunk, no retries (create called once).
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["type"], "error")
        self.assertEqual(fake.chat.completions.create.call_count, 1)

    def test_stream_success_reports_success(self):
        client = _make_client(keys=["k1"])
        # Build a minimal fake stream of chunks.
        class _Delta:
            def __init__(self, content):
                self.content = content
                self.reasoning = None
                self.reasoning_content = None
                self.tool_calls = None

        class _Choice:
            def __init__(self, content):
                self.delta = _Delta(content)

        class _Chunk:
            def __init__(self, content):
                self.choices = [_Choice(content)]

        fake = MagicMock()
        fake.chat.completions.create.return_value = [_Chunk("Hel"), _Chunk("lo")]
        with patch.object(client.key_pool, "get_client", return_value=fake):
            chunks = list(client.chat_stream(messages=[{"role": "user", "content": "hi"}]))
        text = "".join(c["content"] for c in chunks if c["type"] == "content")
        self.assertEqual(text, "Hello")
        self.assertEqual(client.key_pool.keys[0].success_count, 1)


if __name__ == "__main__":
    unittest.main()
