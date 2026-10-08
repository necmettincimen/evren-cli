import unittest

from src.token_manager import TokenManager, REASONING_MIN_OUTPUT_TOKENS


class TestTokenManager(unittest.TestCase):
    def test_estimate_text_empty(self):
        tm = TokenManager()
        self.assertEqual(tm.estimate_text(""), 0)

    def test_estimate_text_scales_with_length(self):
        tm = TokenManager()
        short = tm.estimate_text("hello")
        long = tm.estimate_text("hello " * 100)
        self.assertGreater(long, short)

    def test_estimate_messages(self):
        tm = TokenManager()
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello there"},
        ]
        total = tm.estimate_messages(msgs)
        self.assertGreater(total, 0)

    def test_estimate_message_with_tool_calls(self):
        tm = TokenManager()
        msg = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "view_file", "arguments": '{"path": "a.py"}'}}
            ],
        }
        self.assertGreater(tm.estimate_message(msg), 4)

    def test_calibration_adjusts_ratio(self):
        tm = TokenManager()
        self.assertEqual(tm.calibration, 1.0)
        # Server says actual is 2x our estimate.
        tm.calibrate(estimated=100, actual=200)
        self.assertGreater(tm.calibration, 1.0)

    def test_calibration_ignores_invalid(self):
        tm = TokenManager()
        tm.calibrate(0, 100)
        tm.calibrate(100, 0)
        self.assertEqual(tm.calibration, 1.0)

    def test_resolve_output_budget_reasoning(self):
        tm = TokenManager()
        # Reasoning model with a tiny request -> bumped to the minimum.
        self.assertEqual(
            tm.resolve_output_budget("deepseek-v4.1-flash", 512),
            REASONING_MIN_OUTPUT_TOKENS,
        )
        # Reasoning model with a large request -> kept.
        self.assertEqual(tm.resolve_output_budget("glm-5.3", 8000), 8000)
        # Non-reasoning model -> unchanged.
        self.assertEqual(tm.resolve_output_budget("qwen3.8-flash-next", 512), 512)

    def test_remaining_context_and_fits(self):
        tm = TokenManager(context_window=1000)
        msgs = [{"role": "user", "content": "x" * 100}]
        self.assertTrue(tm.fits(msgs, output_budget=100))
        self.assertGreater(tm.remaining_context(msgs, 100), 0)

    def test_calibrate_from_response(self):
        tm = TokenManager()

        class _Usage:
            prompt_tokens = 500
            completion_tokens = 100
            total_tokens = 600

        class _Resp:
            usage = _Usage()

        tm.calibrate_from_response(estimated=250, response=_Resp())
        self.assertGreater(tm.calibration, 1.0)


if __name__ == "__main__":
    unittest.main()
