import unittest
from unittest.mock import MagicMock, patch

import httpx

from src.api_client import EvrenClient
from src.api_key_pool import ApiKeyPool


class TestNormalizeQuota(unittest.TestCase):
    def test_rolling_window_fields(self):
        raw = {
            "used_tokens": 100,
            "cap": 1000,
            "window_minutes": 60,
            "reset_at": "2026-10-07T12:00:00Z",
            "level": "ok",
            "remaining_cr": 50,
        }
        out = EvrenClient._normalize_quota(raw)
        # Token remaining is derived from cap - used; remaining_cr is credit.
        self.assertEqual(out["remaining"], 900)
        self.assertEqual(out["remaining_cr"], 50)
        self.assertEqual(out["limit"], 1000)
        self.assertEqual(out["used"], 100)
        self.assertEqual(out["reset"], "2026-10-07T12:00:00Z")
        self.assertEqual(out["level"], "ok")

    def test_derive_remaining_from_cap_used(self):
        out = EvrenClient._normalize_quota({"cap": 500, "used_tokens": 120})
        self.assertEqual(out["remaining"], 380)
        self.assertEqual(out["limit"], 500)

    def test_header_fallback(self):
        headers = httpx.Headers({"x-evren-daily-remaining-tokens": "42"})
        out = EvrenClient._normalize_quota({}, headers)
        self.assertEqual(out["remaining"], 42)


class TestGetAllQuotas(unittest.TestCase):
    def test_queries_each_key(self):
        client = EvrenClient.__new__(EvrenClient)
        client.base_url = "https://example.test/v1"
        client.api_key = "a"
        client.last_remaining_tokens = None
        client.key_pool = ApiKeyPool(["a", "b"])

        def fake_get(api_key=None):
            return {"remaining": 10 if api_key == "a" else 99, "limit": 100, "reset": "soon"}

        with patch.object(client, "get_quota", side_effect=fake_get):
            rows = client.get_all_quotas()
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0]["active"])
        self.assertFalse(rows[1]["active"])
        self.assertEqual(rows[0]["remaining"], 10)
        self.assertEqual(rows[1]["remaining"], 99)


if __name__ == "__main__":
    unittest.main()
