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




if __name__ == "__main__":
    unittest.main()
