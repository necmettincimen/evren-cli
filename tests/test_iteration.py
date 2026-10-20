"""Iteration mode: plan parsing, check detection, round loop with a fake session."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.iteration import CheckResult, IterationRunner, IterationState, detect_checks, slugify


class FakeSession:
    def __init__(self, answers):
        self.answers = list(answers)
        self.mode = "normal"

    def clear_history(self):
        pass

    def set_mode(self, mode):
        self.mode = mode

    def run_step(self, prompt, max_steps=None, route=True):
        return self.answers.pop(0) if self.answers else '{"done": true, "items": []}'


class IterationTest(unittest.TestCase):
    def test_plan_parsing_json_and_fallback(self):
        done, items = IterationRunner._parse_plan('Plan:\n{"done": false, "items": [{"title": "A", "detail": "d"}]}')
        self.assertFalse(done)
        self.assertEqual([i.title for i in items], ["A"])
        _, items = IterationRunner._parse_plan("1. Birinci\n2) İkinci")
        self.assertEqual(len(items), 2)

    def test_plan_prompt_has_item_size_rules(self):
        from src.iteration import PLAN_PROMPT

        self.assertIn("tek komut", PLAN_PROMPT.lower())
        self.assertIn("en fazla 1", PLAN_PROMPT.lower())

    def test_detect_flutter_and_slug(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "pubspec.yaml").write_text("name: x", encoding="utf-8")
            self.assertIn("flutter test", detect_checks(Path(d)))
        self.assertEqual(slugify("Brief akışı: ajans & marka"), "brief-akisi-ajans-marka")

    def test_round_records_handoff_and_resumes(self):
        answers = ['{"done": false, "items": [{"title": "Düzelt", "detail": "x"}]}',
                   "yaptım\nSONUÇ: tamam — düzeltildi",
                   '{"done": true, "items": []}']
        ok = CheckResult("python -V", 0, 0, "", 0.1)
        with tempfile.TemporaryDirectory() as d, patch("src.iteration.run_check", return_value=ok), \
                patch("src.ui.print_info"), patch("src.ui.print_success"), patch("src.ui.print_warning"):
            runner = IterationRunner(FakeSession(answers), Path(d), "hedef", checks=["python -V"], max_rounds=5)
            state = runner.run()
            self.assertTrue(state.done)
            self.assertEqual(state.items[0].status, "tamam")
            handoff = runner.handoff_path.read_text(encoding="utf-8")
            self.assertIn("[tamam] Düzelt", handoff)
            resumed = IterationRunner(FakeSession([]), Path(d), resume=True)
            self.assertIsInstance(resumed.state, IterationState)
            self.assertEqual(resumed.state.round, 2)

    def test_keyboard_interrupt_writes_partial_round_summary(self):
        class InterruptSession(FakeSession):
            def run_step(self, prompt, max_steps=None, route=True):
                if "SONUÇ:" in prompt:  # item prompt → simulate Ctrl+C mid-item
                    raise KeyboardInterrupt
                return super().run_step(prompt, max_steps=max_steps, route=route)

        answers = ['{"done": false, "items": [{"title": "Düzelt", "detail": "x"}]}']
        ok = CheckResult("python -V", 0, 0, "", 0.1)
        with tempfile.TemporaryDirectory() as d, patch("src.iteration.run_check", return_value=ok), \
                patch("src.ui.print_info"), patch("src.ui.print_success"), patch("src.ui.print_warning"):
            runner = IterationRunner(InterruptSession(answers), Path(d), "hedef", checks=["python -V"], max_rounds=5)
            state = runner.run()  # must not raise
            self.assertEqual(state.stopped, "kesildi")
            self.assertFalse(state.done)
            handoff = runner.handoff_path.read_text(encoding="utf-8")
            self.assertIn("KESİLDİ", handoff)
            self.assertIn("Düzelt", handoff)
            saved = (runner.dir / "state.json").read_text(encoding="utf-8")
            self.assertIn("kesildi", saved)

    def test_resume_writes_heading_for_interrupted_round(self):
        class InterruptSession(FakeSession):
            def run_step(self, prompt, max_steps=None, route=True):
                if "SONUÇ:" in prompt:
                    raise KeyboardInterrupt
                return super().run_step(prompt, max_steps=max_steps, route=route)

        answers = ['{"done": false, "items": [{"title": "Düzelt", "detail": "x"}]}']
        ok = CheckResult("python -V", 0, 0, "", 0.1)
        with tempfile.TemporaryDirectory() as d, patch("src.iteration.run_check", return_value=ok), \
                patch("src.ui.print_info"), patch("src.ui.print_success"), patch("src.ui.print_warning"):
            runner = IterationRunner(InterruptSession(answers), Path(d), "hedef", checks=["python -V"], max_rounds=5)
            state = runner.run()
            self.assertEqual(state.stopped, "kesildi")
            resumed = IterationRunner(FakeSession([]), Path(d), resume=True)
            self.assertEqual(resumed.state.stopped, "")
            handoff = resumed.handoff_path.read_text(encoding="utf-8")
            for number in range(1, resumed.state.round + 1):
                self.assertGreaterEqual(handoff.count(f"## Tur {number}"), 1, f"tur {number} başlığı yok")
            self.assertIn("KESİLDİ (devam ediliyor)", handoff)
            self.assertIn("[bekliyor] Düzelt", handoff)
            # a second resume must not duplicate the heading
            again = IterationRunner(FakeSession([]), Path(d), resume=True)
            self.assertEqual(again.handoff_path.read_text(encoding="utf-8").count("KESİLDİ (devam ediliyor)"), 1)

    def test_missing_check_tool_reports_clear_error_and_stops(self):
        with tempfile.TemporaryDirectory() as d, \
                patch("src.ui.print_info"), patch("src.ui.print_success"), patch("src.ui.print_warning"):
            runner = IterationRunner(FakeSession([]), Path(d), "hedef",
                                     checks=["nonexistent_tool --v"], max_rounds=1)
            state = runner.run()
            self.assertEqual(state.stopped, "kontrol komutu bulunamadı")
            self.assertFalse(state.done)
            handoff = runner.handoff_path.read_text(encoding="utf-8")
            self.assertIn("check komutu bulunamadı", handoff)
            self.assertIn("nonexistent_tool", handoff)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
