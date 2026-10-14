"""Startup key choice: most daily quota wins, near-empty keys are parked."""

import unittest
from unittest.mock import patch

from src.api_client import EvrenClient
from src.api_key_pool import ApiKeyPool, KeyState


def _client(keys):
    client = EvrenClient.__new__(EvrenClient)
    client.base_url = "https://example.test/v1"
    client.default_model = "m"
    client.last_remaining_tokens = None
    client.key_pool = ApiKeyPool(keys)
    client.api_key = client.key_pool.keys[0].key
    return client


class SelectBestKeyTest(unittest.TestCase):
    def test_fullest_key_is_pinned_and_empty_ones_parked(self):
        client = _client(["evren_llm_a", "evren_llm_b", "evren_llm_c"])
        remaining = {"evren_llm_a": 1086, "evren_llm_b": 15_000_000, "evren_llm_c": 3_000_000}
        with patch.object(
            client, "get_quota",
            side_effect=lambda api_key: {"remaining_daily_tokens": remaining[api_key]},
        ):
            row = client.select_best_key(min_remaining=50_000)
        self.assertEqual(row["key"], "evren_llm_b")
        self.assertEqual(client.key_pool.acquire().key, "evren_llm_b")
        states = {pk.key: pk.state for pk in client.key_pool.keys}
        self.assertEqual(states["evren_llm_a"], KeyState.DISABLED)
        self.assertEqual(states["evren_llm_c"], KeyState.READY)
        self.assertEqual(client.last_remaining_tokens, 15_000_000)

    def test_all_low_still_uses_the_fullest(self):
        client = _client(["evren_llm_a", "evren_llm_b"])
        remaining = {"evren_llm_a": 10, "evren_llm_b": 900}
        with patch.object(
            client, "get_quota",
            side_effect=lambda api_key: {"remaining_daily_tokens": remaining[api_key]},
        ):
            row = client.select_best_key(min_remaining=50_000)
        self.assertEqual(row["key"], "evren_llm_b")
        self.assertEqual(client.key_pool.acquire().key, "evren_llm_b")

    def test_unknown_quotas_leave_the_pool_alone(self):
        client = _client(["evren_llm_a", "evren_llm_b"])
        with patch.object(client, "get_quota", return_value={"remaining_daily_tokens": "Bilinmiyor"}):
            self.assertIsNone(client.select_best_key())
        self.assertEqual(client.key_pool.acquire().key, "evren_llm_a")


class DailyQuotaMappingTest(unittest.TestCase):
    def test_daily_limit_wins_over_window_cap(self):
        # Live /v1/quota shape: 5-minute window fields next to the daily ones.
        out = EvrenClient._normalize_quota({
            "used_tokens": 0, "cap": 2_500_000, "window_minutes": 5,
            "daily_used_tokens": 14_998_914, "daily_limit_tokens": 15_000_000,
            "daily_remaining_tokens": 1086, "remaining_daily_tokens": 1086,
        })
        self.assertEqual(out["remaining"], 1086)
        self.assertEqual(out["limit"], 15_000_000)
        self.assertEqual(out["used"], 14_998_914)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
