import os
import unittest
from unittest.mock import patch

from src.api_key_pool import (
    ApiKeyPool,
    KeyState,
    mask_key,
    parse_api_keys,
)


class TestMaskKey(unittest.TestCase):
    def test_mask_long_key(self):
        masked = mask_key("evren_llm_abcdefghijklmnop")
        self.assertTrue(masked.startswith("evren_llm_"))
        self.assertIn("...", masked)
        self.assertNotIn("abcdefghijklmnop", masked)

    def test_mask_empty(self):
        self.assertEqual(mask_key(""), "(boş)")


class TestParseApiKeys(unittest.TestCase):
    def test_multi_and_single(self):
        env = {"EVREN_API_KEYS": "k1, k2 ,k3", "EVREN_API_KEY": "k4"}
        with patch.dict(os.environ, env, clear=True):
            keys = parse_api_keys()
        self.assertEqual(keys, ["k1", "k2", "k3", "k4"])

    def test_dedup(self):
        env = {"EVREN_API_KEYS": "k1,k2", "EVREN_API_KEY": "k1"}
        with patch.dict(os.environ, env, clear=True):
            keys = parse_api_keys()
        self.assertEqual(keys, ["k1", "k2"])

    def test_single_only(self):
        with patch.dict(os.environ, {"EVREN_API_KEY": "only"}, clear=True):
            keys = parse_api_keys()
        self.assertEqual(keys, ["only"])


class TestApiKeyPool(unittest.TestCase):
    def test_sticky_acquire(self):
        pool = ApiKeyPool(["a", "b", "c"])
        acquired = [pool.acquire().key for _ in range(4)]
        self.assertEqual(acquired, ["a", "a", "a", "a"])
        self.assertEqual(pool.current_key, "a")

    def test_empty_pool(self):
        pool = ApiKeyPool([])
        self.assertTrue(pool.is_empty())
        self.assertIsNone(pool.acquire())

    def test_rate_limit_cools_key(self):
        pool = ApiKeyPool(["a", "b"], cooldown_seconds=100)
        pool.report_rate_limited("a")
        # Sticky 'a' is cooling -> failover pins 'b'.
        self.assertEqual(pool.acquire().key, "b")
        self.assertEqual(pool.current_key, "b")
        # Subsequent acquires stay on 'b'.
        self.assertEqual(pool.acquire().key, "b")

    def test_disabled_key_skipped(self):
        pool = ApiKeyPool(["a", "b"])
        pool.report_disabled("a")
        self.assertEqual(pool.acquire().key, "b")
        self.assertEqual(pool.acquire().key, "b")

    def test_all_cooling_returns_none(self):
        pool = ApiKeyPool(["a", "b"], cooldown_seconds=100)
        pool.report_rate_limited("a")
        pool.report_rate_limited("b")
        self.assertIsNone(pool.acquire())
        self.assertFalse(pool.has_available())
        self.assertGreater(pool.next_available_utc(), 0)

    def test_success_resets_state(self):
        pool = ApiKeyPool(["a"], cooldown_seconds=100)
        pool.report_rate_limited("a")
        self.assertFalse(pool.has_available())
        pool.report_success("a")
        self.assertTrue(pool.has_available())

    def test_reset_states(self):
        pool = ApiKeyPool(["a", "b"])
        pool.report_disabled("a")
        pool.report_rate_limited("b")
        pool.reset_states()
        self.assertTrue(pool.has_available())
        self.assertEqual(pool.keys[0].state, KeyState.READY)

    def test_add_remove_key(self):
        pool = ApiKeyPool(["a"])
        self.assertTrue(pool.add_key("b"))
        self.assertFalse(pool.add_key("b"))  # duplicate
        self.assertEqual(len(pool), 2)
        self.assertTrue(pool.remove_key("a"))
        self.assertEqual(len(pool), 1)
        self.assertEqual(pool.keys[0].key, "b")
        self.assertEqual(pool.current_key, "b")

    def test_rotate_advances_and_sticks(self):
        pool = ApiKeyPool(["a", "b", "c"])
        self.assertEqual(pool.rotate().key, "b")
        self.assertEqual(pool.acquire().key, "b")
        self.assertEqual(pool.acquire().key, "b")
        self.assertEqual(pool.rotate().key, "c")
        self.assertEqual(pool.acquire().key, "c")
        self.assertEqual(pool.rotate().key, "a")
        self.assertEqual(pool.acquire().key, "a")

    def test_rotate_skips_unavailable(self):
        pool = ApiKeyPool(["a", "b", "c"], cooldown_seconds=100)
        pool.report_rate_limited("b")
        # From sticky 'a', rotate should skip cooling 'b' and land on 'c'.
        self.assertEqual(pool.rotate().key, "c")
        self.assertEqual(pool.acquire().key, "c")

    def test_rotate_empty(self):
        pool = ApiKeyPool([])
        self.assertIsNone(pool.rotate())

    def test_cooldown_expiry(self):
        pool = ApiKeyPool(["a"], cooldown_seconds=0)
        pool.report_rate_limited("a", retry_after=0)
        # With zero cooldown, key is immediately available again.
        self.assertTrue(pool.has_available())


if __name__ == "__main__":
    unittest.main()
