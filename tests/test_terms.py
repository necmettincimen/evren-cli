import unittest
from unittest.mock import MagicMock, patch

from src import terms


class TestTermsHelpers(unittest.TestCase):
    def test_as_bool(self):
        self.assertTrue(terms._as_bool(True))
        self.assertTrue(terms._as_bool("yes"))
        self.assertTrue(terms._as_bool("1"))
        self.assertFalse(terms._as_bool(False))
        self.assertFalse(terms._as_bool("no"))

    def test_ensure_already_accepted_prints(self):
        with patch.object(terms, "get_terms_status", return_value={"accepted": True, "current_version": 2}):
            with patch.object(terms, "print_success") as ok:
                result = terms.ensure_terms_accepted(interactive=True, api_key="k1")
        self.assertTrue(result)
        ok.assert_called_once()
        self.assertIn("kabul edilmiş", ok.call_args[0][0])

    def test_ensure_already_accepted_silent_ok(self):
        with patch.object(terms, "get_terms_status", return_value={"accepted": True, "current_version": 2}):
            with patch.object(terms, "print_success") as ok:
                result = terms.ensure_terms_accepted(interactive=True, api_key="k1", silent_ok=True)
        self.assertTrue(result)
        ok.assert_not_called()

    def test_ensure_accepts_when_confirmed(self):
        with patch.object(terms, "get_terms_status", return_value={"accepted": False, "current_version": 3}):
            with patch.object(terms, "get_terms_text", return_value={"content": "Şartlar metni"}):
                with patch.object(terms, "prompt_confirm", return_value=True):
                    with patch.object(terms, "accept_terms", return_value={}) as accept:
                        with patch.object(terms, "print_success"):
                            result = terms.ensure_terms_accepted(interactive=True, api_key="k1")
        self.assertTrue(result)
        accept.assert_called_once_with(3, api_key="k1")

    def test_ensure_uses_passed_api_key_in_headers(self):
        captured = {}

        def fake_get(url, headers=None, **kwargs):
            captured["headers"] = headers
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {"accepted": True, "current_version": 1}
            return resp

        with patch("src.terms.httpx.get", side_effect=fake_get):
            with patch.object(terms, "print_success"):
                terms.ensure_terms_accepted(api_key="evren_llm_special", silent_ok=True)
        self.assertEqual(captured["headers"]["X-API-Key"], "evren_llm_special")


if __name__ == "__main__":
    unittest.main()
