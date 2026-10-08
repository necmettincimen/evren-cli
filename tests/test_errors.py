import unittest

from src.errors import (
    EvrenApiError,
    RateLimitError,
    QuotaExhaustedError,
    AuthError,
    ServerError,
    NetworkError,
    TermsNotAcceptedError,
    map_sdk_exception,
)
from tests.helpers import make_status_error, make_connection_error


class TestErrorTaxonomy(unittest.TestCase):
    def test_subclass_flags(self):
        self.assertTrue(RateLimitError().retryable)
        self.assertFalse(RateLimitError().failover)

        self.assertTrue(AuthError().failover)
        self.assertFalse(AuthError().retryable)

        self.assertTrue(ServerError().retryable)
        self.assertTrue(NetworkError().retryable)

    def test_all_are_evren_api_error(self):
        for cls in (RateLimitError, AuthError, ServerError, NetworkError, TermsNotAcceptedError):
            self.assertIsInstance(cls(), EvrenApiError)


class TestSdkMapping(unittest.TestCase):
    def test_map_429(self):
        err = map_sdk_exception(make_status_error(429, "rate limited"))
        self.assertIsInstance(err, RateLimitError)
        self.assertTrue(err.retryable)
        self.assertEqual(err.status_code, 429)

    def test_map_429_retry_after(self):
        err = map_sdk_exception(make_status_error(429, "rate limited", retry_after="7"))
        self.assertEqual(err.retry_after, 7.0)

    def test_map_429_daily_quota_is_quota_exhausted(self):
        err = map_sdk_exception(
            make_status_error(429, "You exceeded your current quota (insufficient_quota)")
        )
        self.assertIsInstance(err, QuotaExhaustedError)
        self.assertIsInstance(err, RateLimitError)
        self.assertTrue(err.failover)
        self.assertFalse(err.retryable)

    def test_map_429_transient_stays_rate_limit(self):
        err = map_sdk_exception(make_status_error(429, "too many requests, slow down"))
        self.assertIsInstance(err, RateLimitError)
        self.assertNotIsInstance(err, QuotaExhaustedError)

    def test_map_429_turkish_quota_marker(self):
        err = map_sdk_exception(make_status_error(429, "Günlük kota tükendi"))
        self.assertIsInstance(err, QuotaExhaustedError)

    def test_map_401_403_402(self):
        for code in (401, 402, 403):
            err = map_sdk_exception(make_status_error(code, "auth"))
            self.assertIsInstance(err, AuthError)
            self.assertTrue(err.failover)

    def test_map_5xx(self):
        err = map_sdk_exception(make_status_error(503, "unavailable"))
        self.assertIsInstance(err, ServerError)
        self.assertTrue(err.retryable)

    def test_map_connection_error(self):
        err = map_sdk_exception(make_connection_error())
        self.assertIsInstance(err, NetworkError)
        self.assertTrue(err.retryable)

    def test_map_terms_not_accepted(self):
        err = map_sdk_exception(Exception("terms_not_accepted: please accept"))
        self.assertIsInstance(err, TermsNotAcceptedError)

    def test_map_unknown_is_typed(self):
        err = map_sdk_exception(Exception("something weird"))
        self.assertIsInstance(err, EvrenApiError)
        self.assertFalse(err.retryable)
        self.assertFalse(err.failover)

    def test_failed_key_attached(self):
        err = map_sdk_exception(make_status_error(429, "x"), failed_key="evren_llm_secret")
        self.assertEqual(err.failed_key, "evren_llm_secret")


if __name__ == "__main__":
    unittest.main()
