import unittest
from unittest.mock import patch, MagicMock

from src.api_client import EvrenClient, _TRANSIENT_RETRIES, normalize_chat_messages
from src.api_key_pool import ApiKeyPool
from src.errors import RateLimitError, QuotaExhaustedError, ServerError
from tests.helpers import FakeClock, make_connection_error, make_status_error, FakeCompletion


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


def _make_stream(chunks):
    """Builds a fake stream of chunk objects.

    Each chunk is a dict with optional keys:
      content, reasoning, tool_calls (list of dicts with index/id/name/arguments),
      usage, finish_reason.
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
        def __init__(self, delta, finish_reason=None):
            self.delta = delta
            self.finish_reason = finish_reason

    class _Chunk:
        def __init__(self, delta, usage=None, finish_reason=None):
            self.choices = [_Choice(delta, finish_reason)] if delta is not None else []
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
        result.append(_Chunk(
            delta, usage=spec.get("usage"), finish_reason=spec.get("finish_reason"),
        ))
    return result


class TestNormalizeChatMessages(unittest.TestCase):
    def test_later_system_messages_merge_into_the_first(self):
        sent = normalize_chat_messages([
            {"role": "system", "content": "kimlik"},
            {"role": "user", "content": "iş"},
            {"role": "system", "content": "adım"},
        ])
        self.assertEqual([message["role"] for message in sent], ["system", "user"])
        self.assertIn("kimlik", sent[0]["content"])
        self.assertIn("adım", sent[0]["content"])
        self.assertLess(sent[0]["content"].find("kimlik"), sent[0]["content"].find("adım"))
        self.assertEqual(sent[1]["content"], "iş")

    def test_user_only_history_stays_unchanged(self):
        sent = normalize_chat_messages([{"role": "user", "content": "hi"}])
        self.assertEqual(sent, [{"role": "user", "content": "hi"}])

    def test_chat_complete_sends_normalized_messages(self):
        # chat_complete applies the same normalization before sending.
        client = _make_client()
        fake = _fake_client_with([FakeCompletion("ok")])
        with patch.object(client.key_pool, "get_client", return_value=fake):
            client.chat_complete(messages=[
                {"role": "system", "content": "kimlik"},
                {"role": "user", "content": "iş"},
                {"role": "system", "content": "adım"},
            ])
        sent = fake.chat.completions.create.call_args.kwargs["messages"]
        self.assertEqual([message["role"] for message in sent], ["system", "user"])
        self.assertIn("adım", sent[0]["content"])


class TestChatCompleteRetry(unittest.TestCase):
    def test_success_and_retry_after_header(self):
        # First-try success returns the model text; a 429 Retry-After header
        # is honored as the sleep duration before the retry.
        client = _make_client()
        fake = _fake_client_with([FakeCompletion("hello")])
        with patch.object(client.key_pool, "get_client", return_value=fake):
            resp = client.chat_complete(messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(resp.choices[0].message.content, "hello")

        client = _make_client()
        clock = FakeClock()
        fake = _fake_client_with([
            make_status_error(429, "rate limited", retry_after="5"),
            FakeCompletion("ok"),
        ])
        with patch.object(client.key_pool, "get_client", return_value=fake):
            with clock.patch():
                resp = client.chat_complete(messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(resp.choices[0].message.content, "ok")
        self.assertEqual(clock.sleeps, [5.0])

    def test_retry_exhausted_raises_typed(self):
        # Exhausted retries raise typed errors: ServerError for 503,
        # QuotaExhaustedError when every key hits daily quota, RateLimitError
        # when every key is already disabled.
        client = _make_client()
        clock = FakeClock()
        fake = _fake_client_with(make_status_error(503, "down"))
        with patch.object(client.key_pool, "get_client", return_value=fake):
            with clock.patch():
                with self.assertRaises(ServerError):
                    client.chat_complete(messages=[{"role": "user", "content": "hi"}], max_retries=3)
        self.assertEqual(len(clock.sleeps), 2)  # 3 attempts -> 2 sleeps

        client = _make_client(keys=["k1"])
        fake = _fake_client_with(make_status_error(429, "daily quota exceeded"))
        with patch.object(client.key_pool, "get_client", return_value=fake):
            with self.assertRaises(QuotaExhaustedError):
                client.chat_complete(messages=[{"role": "user", "content": "hi"}])

        client = _make_client(keys=["k1"])
        client.key_pool.report_disabled("k1")
        with self.assertRaises(RateLimitError):
            client.chat_complete(messages=[{"role": "user", "content": "hi"}])

    def test_key_state_transitions_on_errors(self):
        # 401 disables the key, plain 429 cools it, quota 429 disables it.
        cases = [
            (["bad", "good"], make_status_error(401, "bad key"), "disabled", None),
            (["k1", "k2"], make_status_error(429, "rate limited"), "cooling", None),
            (["k1", "k2"], make_status_error(429, "insufficient_quota: daily quota exceeded"),
             "disabled", "quota_exhausted"),
        ]
        for keys, error, expected_state, expected_last_error in cases:
            with self.subTest(status=error):
                client = _make_client(keys=keys)
                fake = _fake_client_with([error, FakeCompletion("ok")])
                with patch.object(client.key_pool, "get_client", return_value=fake):
                    resp = client.chat_complete(messages=[{"role": "user", "content": "hi"}])
                self.assertEqual(resp.choices[0].message.content, "ok")
                self.assertEqual(client.key_pool.keys[0].state.value, expected_state)
                if expected_last_error:
                    self.assertEqual(client.key_pool.keys[0].last_error, expected_last_error)

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

    def test_all_keys_cooling_waits_then_retries(self):
        # All keys cooling -> wait for the cooldown, then retry successfully.
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

    def test_stream_success_and_finish_reason(self):
        # chat_stream yields content chunks and records key success; assembled
        # response keeps the finish reason.
        client = _make_client(keys=["k1"])
        fake = MagicMock()
        fake.chat.completions.create.return_value = _make_stream([
            {"content": "Hel"}, {"content": "lo"},
        ])
        with patch.object(client.key_pool, "get_client", return_value=fake):
            chunks = list(client.chat_stream(messages=[{"role": "user", "content": "hi"}]))
        text = "".join(c["content"] for c in chunks if c["type"] == "content")
        self.assertEqual(text, "Hello")
        self.assertEqual(client.key_pool.keys[0].success_count, 1)

    def test_preserves_finish_reason(self):
        for reason in ("stop", "length", "tool_calls", "content_filter"):
            with self.subTest(reason=reason):
                client = _make_client(keys=["k1"])
                fake = MagicMock()
                fake.chat.completions.create.return_value = _make_stream([
                    {"reasoning": "thinking"},
                    {"finish_reason": reason},
                    {"usage": MagicMock()},
                ])
                with patch.object(client.key_pool, "get_client", return_value=fake):
                    response = client.chat_stream_assembled(
                        messages=[{"role": "user", "content": "hi"}],
                    )
                self.assertEqual(response.choices[0].finish_reason, reason)

    def test_assembles_content_reasoning_and_tool_calls(self):
        # Assembled response keeps finish_reason, content, reasoning, callbacks
        # and tool-call deltas merged across chunks.
        client = _make_client(keys=["k1"])
        stream = _make_stream([
            {"reasoning": "think "},
            {"reasoning": "more"},
            {"content": "Hel"},
            {"content": "lo"},
            {"tool_calls": [{"index": 0, "id": "call_1", "name": "view_", "arguments": '{"pa'}]},
            {"tool_calls": [{"index": 0, "name": "file", "arguments": 'th": "a.py"}'}]},
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
        tcs = resp.choices[0].message.tool_calls
        self.assertEqual(len(tcs), 1)
        self.assertEqual(tcs[0].id, "call_1")
        self.assertEqual(tcs[0].function.name, "view_file")
        self.assertEqual(tcs[0].function.arguments, '{"path": "a.py"}')

    def test_stream_transient_errors_retry_then_succeed_or_raise(self):
        # Exhausted stream retries raise typed (ServerError / QuotaExhaustedError);
        # a connection error retries once and succeeds.
        client = _make_client(keys=["k1"])
        clock = FakeClock()
        fake = _fake_client_with(make_status_error(503, "down"))
        with patch.object(client.key_pool, "get_client", return_value=fake):
            with clock.patch():
                with self.assertRaises(ServerError):
                    client.chat_stream_assembled(messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(fake.chat.completions.create.call_count, 1 + _TRANSIENT_RETRIES)
        self.assertEqual(len(clock.sleeps), _TRANSIENT_RETRIES)

        client = _make_client(keys=["k1"])
        fake = _fake_client_with(make_status_error(429, "daily quota exceeded"))
        with patch.object(client.key_pool, "get_client", return_value=fake):
            with self.assertRaises(QuotaExhaustedError):
                client.chat_stream_assembled(messages=[{"role": "user", "content": "hi"}])

        client = _make_client(keys=["k1"])
        clock = FakeClock()
        stream = _make_stream([{"content": "Hello"}])
        fake = MagicMock()
        fake.chat.completions.create.side_effect = [make_connection_error(), stream]
        with patch.object(client.key_pool, "get_client", return_value=fake):
            with clock.patch():
                resp = client.chat_stream_assembled(messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(resp.choices[0].message.content, "Hello")
        self.assertEqual(len(clock.sleeps), 1)
        self.assertEqual(fake.chat.completions.create.call_count, 2)

    def test_stream_quota_failover_and_exhaustion(self):
        # Quota 429 before any token -> disable key and failover to next key.
        client = _make_client(keys=["k1", "k2"])
        stream = _make_stream([{"content": "Hello"}])
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


class TestChatStreamNoRetry(unittest.TestCase):
    def test_stream_error_retries_before_tokens_then_yields_once(self):
        # A 503 before any token is retried, then reported once. A started
        # stream is still not replayed.
        client = _make_client(keys=["k1", "k2"])
        clock = FakeClock()
        fake = _fake_client_with(make_status_error(503, "down"))
        with patch.object(client.key_pool, "get_client", return_value=fake):
            with clock.patch():
                chunks = list(client.chat_stream(messages=[{"role": "user", "content": "hi"}]))
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["type"], "error")
        self.assertEqual(fake.chat.completions.create.call_count, 1 + _TRANSIENT_RETRIES)
        self.assertEqual(len(clock.sleeps), _TRANSIENT_RETRIES)

        # Success path: content chunks are yielded and key success is recorded.
        client = _make_client(keys=["k1"])
        fake = MagicMock()
        fake.chat.completions.create.return_value = _make_stream([
            {"content": "Hel"}, {"content": "lo"},
        ])
        with patch.object(client.key_pool, "get_client", return_value=fake):
            chunks = list(client.chat_stream(messages=[{"role": "user", "content": "hi"}]))
        text = "".join(c["content"] for c in chunks if c["type"] == "content")
        self.assertEqual(text, "Hello")
        self.assertEqual(client.key_pool.keys[0].success_count, 1)


if __name__ == "__main__":
    unittest.main()
