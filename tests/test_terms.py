import unittest
from unittest.mock import MagicMock, patch

from src import terms


class TestTermsHelpers(unittest.TestCase):



    def test_ensure_accepts_when_confirmed(self):
        with patch.object(terms, "get_terms_status", return_value={"accepted": False, "current_version": 3}):
            with patch.object(terms, "get_terms_text", return_value={"content": "Şartlar metni"}):
                with patch.object(terms, "prompt_confirm", return_value=True):
                    with patch.object(terms, "accept_terms", return_value={}) as accept:
                        with patch.object(terms, "print_success"):
                            result = terms.ensure_terms_accepted(interactive=True, api_key="k1")
        self.assertTrue(result)
        accept.assert_called_once_with(3, api_key="k1")



if __name__ == "__main__":
    unittest.main()
