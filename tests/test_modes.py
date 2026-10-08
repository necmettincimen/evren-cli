import unittest

from src.modes import AgentMode, parse_mode_prefix, mode_label


class TestModes(unittest.TestCase):
    def test_parse_ask_prefix(self):
        mode, text = parse_mode_prefix("ask: dosyayı incele")
        self.assertEqual(mode, AgentMode.ASK)
        self.assertEqual(text, "dosyayı incele")

    def test_parse_plan_prefix(self):
        mode, text = parse_mode_prefix("plan: refactor yap")
        self.assertEqual(mode, AgentMode.PLAN)
        self.assertEqual(text, "refactor yap")

    def test_parse_case_insensitive(self):
        mode, _ = parse_mode_prefix("ASK: test")
        self.assertEqual(mode, AgentMode.ASK)

    def test_no_prefix(self):
        mode, text = parse_mode_prefix("normal istek")
        self.assertIsNone(mode)
        self.assertEqual(text, "normal istek")

    def test_mode_label(self):
        self.assertIn("salt-okuma", mode_label(AgentMode.ASK))
        self.assertIn("plan", mode_label(AgentMode.PLAN))


if __name__ == "__main__":
    unittest.main()
