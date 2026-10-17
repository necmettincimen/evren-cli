import unittest

from src.modes import AgentMode, parse_mode_prefix, mode_label


class TestModes(unittest.TestCase):

    def test_parse_plan_prefix(self):
        mode, text = parse_mode_prefix("plan: refactor yap")
        self.assertEqual(mode, AgentMode.PLAN)
        self.assertEqual(text, "refactor yap")





if __name__ == "__main__":
    unittest.main()
