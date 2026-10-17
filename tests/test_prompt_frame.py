import os
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.prompt_frame import (
    PromptFrame,
    build_user_content,
    collect_prompt_frame,
    compose_system,
    delivery_summary,
    identity_block,
    FrameGuidance,
    collect_guided_prompt_frame,
    request_frame_guidance,
)
from src.agent import AgentSession
from src.cli import load_frame


class _Client:
    default_model = "test-model"


class TestPromptFrame(unittest.TestCase):

    def test_identity_stays_on_system_task_on_user(self):
        frame = PromptFrame(
            rol="tasarımcı",
            cerceve="tek ekran",
            brief="kapak sayfası",
            kisit="design system",
            cikti="HTML",
        )
        system = identity_block(frame)
        user = build_user_content(frame)

        self.assertIn("Rol: tasarımcı", system)
        self.assertIn("Çerçeve: tek ekran", system)
        self.assertNotIn("Brief", system)
        self.assertNotIn("Kısıt", system)
        self.assertNotIn("Çıktı", system)

        self.assertIn("Brief: kapak sayfası", user)
        self.assertIn("Kısıt: design system", user)
        self.assertIn("Çıktı: HTML", user)
        self.assertNotIn("Rol:", user)
        self.assertNotIn("Çerçeve:", user)

    def test_empty_frame_leaves_system_untouched(self):
        frame = PromptFrame(rol="  ", cerceve="")
        self.assertTrue(frame.is_empty())
        self.assertEqual(compose_system("TABAN", frame), "TABAN")
        self.assertEqual(identity_block(frame), "")


    def test_extra_prompt_appended_when_task_present(self):
        frame = PromptFrame(brief="tek cümle")
        user = build_user_content(frame, extra="ve testi de yaz")
        self.assertIn("Brief: tek cümle", user)
        self.assertIn("Ek istek:\nve testi de yaz", user)


    def test_slash_on_first_line_skips_the_rest(self):
        answers = iter(["/help", "should-not-be-read"])

        def read_line(_label):
            return next(answers)

        self.assertEqual(collect_prompt_frame(read_line), "/help")


    def test_previous_answer_is_the_default(self):
        prev = PromptFrame(rol="kimlik", cerceve="kalıp", brief="ilk iş")
        # Blank answers keep the previous turn's values; only Çıktı is new.
        answers = iter(["", "", "", "", "pptx"])

        def read_line(_label):
            return next(answers)

        frame = collect_prompt_frame(read_line, previous=prev)
        self.assertEqual(frame.rol, "kimlik")
        self.assertEqual(frame.cerceve, "kalıp")
        self.assertEqual(frame.brief, "ilk iş")
        self.assertEqual(frame.kisit, "")
        self.assertEqual(frame.cikti, "pptx")



    def test_load_frame_from_flags_does_not_need_a_positional(self):
        args = SimpleNamespace(prompt=None, rol="kimlik", cerceve="", brief="iş", kisit="", cikti="")
        loaded = load_frame(args)
        self.assertIsNotNone(loaded)
        frame, prompt = loaded
        self.assertEqual(frame.rol, "kimlik")
        self.assertEqual(frame.brief, "iş")
        self.assertEqual(prompt, "")




class TestAgentFrame(unittest.TestCase):
    def setUp(self):
        # `workspace_root=None` resolves to the real repository, whose agent.md
        # legitimately carries a "Son görev" line. Since the docs budget now
        # prioritises `current_focus`, that text reaches the system prompt and
        # would make these frame-composition assertions workspace-dependent.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.workspace = Path(tmp.name).resolve()
        patcher = patch.dict(os.environ, {"EVREN_AUTO_DOCS": "false"}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_apply_frame_merges_identity_and_keeps_base(self):
        session = AgentSession(client=_Client(), workspace_root=self.workspace, model="test-model")
        base = session.base_system_prompt

        session.apply_frame(PromptFrame(rol="kimlik", brief="ilk"))
        self.assertIn(base, session.messages[0]["content"])
        self.assertIn("Rol: kimlik", session.messages[0]["content"])
        self.assertNotIn("Brief", session.messages[0]["content"])

        user = session.apply_frame(PromptFrame(cerceve="kalıp", kisit="ölçek"))
        system = session.messages[0]["content"]
        self.assertIn("Rol: kimlik", system)
        self.assertIn("Çerçeve: kalıp", system)
        self.assertEqual(system.count("GÖREV KALIBI"), 1)
        self.assertIn("Kısıt: ölçek", user)
        self.assertNotIn("Brief", user)

    def test_clear_history_drops_the_frame(self):
        session = AgentSession(client=_Client(), workspace_root=self.workspace, model="test-model")
        session.apply_frame(PromptFrame(rol="kimlik", brief="iş"))
        session.clear_history()
        self.assertIn(session.base_system_prompt, session.messages[0]["content"])
        self.assertIn("YEREL ÇALIŞTIRMA BAĞLAMI", session.messages[0]["content"])
        self.assertNotIn("GÖREV KALIBI", session.messages[0]["content"])
        self.assertTrue(session.frame.is_empty())



class TestGuidedFrame(unittest.TestCase):
    suggestions = {"rol": "mühendis", "cerceve": "küçük adımlar", "kisit": "cerrahi yama", "cikti": "test sonucu"}

    def setUp(self):
        # These tests cover the full form (4 clarify rounds + field suggestions);
        # the lean defaults are covered by TestGuidedFrameDefaults.
        patcher = patch.dict(os.environ, {
            "EVREN_CLARIFY_ROUNDS": "4", "EVREN_FRAME_SUGGEST": "true", "EVREN_FORM_FIELDS": "true",
        })
        patcher.start()
        self.addCleanup(patcher.stop)

    def collect(self, answers, guidance, previous=None):
        inputs = iter(answers)
        return collect_guided_prompt_frame(lambda _: next(inputs), guidance, previous)

    def test_clarify_before_suggestions_and_individual_accept_edit_reject(self):
        phases = []

        def guidance(frame, clarify):
            phases.append(clarify)
            if len(phases) == 1:
                return FrameGuidance(question="Hangi dosya?")
            if clarify:
                self.assertIn("src/config.py", frame.clarifications)
                return FrameGuidance()
            return FrameGuidance(suggestions=self.suggestions)

        frame = self.collect(["timeout düzelt", "src/config.py", "", "hedefli test", "-", ""], guidance)
        self.assertEqual(phases, [True, True, False])
        self.assertEqual(frame.brief, "timeout düzelt")
        self.assertEqual(frame.rol, "mühendis")
        self.assertEqual(frame.cerceve, "hedefli test")
        self.assertEqual(frame.kisit, "")
        self.assertEqual(frame.cikti, "test sonucu")
        self.assertIn("Hangi dosya?", build_user_content(frame))
        self.assertIn("src/config.py", build_user_content(frame))
        self.assertNotIn("src/config.py", identity_block(frame))



    def test_blank_brief_skips_api_and_uses_manual_fields(self):
        guidance = MagicMock()
        frame = self.collect(["", "rol", "", "", ""], guidance)
        guidance.assert_not_called()
        self.assertEqual(frame.rol, "rol")

    def test_slash_command_skips_api(self):
        guidance = MagicMock()
        self.assertEqual(self.collect(["/help"], guidance), "/help")
        guidance.assert_not_called()

    def test_continue_skips_form_and_guidance(self):
        for text in ("devam", " DEVAM ", "/continue"):
            with self.subTest(text=text):
                guidance = MagicMock()
                self.assertEqual(self.collect([text], guidance), "/continue")
                guidance.assert_not_called()

    def test_execution_approval_skips_entire_preparation(self):
        for text in (
            "yap artık", "uygula", "hepsi için onay veriyorum kabul ediyorum",
            "hepsine onay veriyorum kabul ediyorum",
            "tüm görevleri yap hepsine onay veriyorum",
            "soru sorma, doğrudan yap!", "onay veriyorum", "kabul ediyorum",
        ):
            with self.subTest(text=text):
                guidance = MagicMock()
                self.assertEqual(self.collect([text], guidance), "/execute " + text)
                guidance.assert_not_called()



    def test_slash_during_clarify_aborts_preparation(self):
        guidance = MagicMock(return_value=FrameGuidance(question="Hedef?"))
        self.assertEqual(self.collect(["iş", "/clear"], guidance), "/clear")
        self.assertEqual(guidance.call_count, 1)



    def test_clarification_loop_is_bounded(self):
        guidance = MagicMock(side_effect=[
            FrameGuidance(question=f"Karar {index}?") for index in range(4)
        ] + [FrameGuidance(suggestions=self.suggestions)])
        frame = self.collect(["iş", "bir", "iki", "üç", "devam", "", "", "", ""], guidance)
        self.assertEqual(guidance.call_count, 5)
        self.assertIn("Yanıt: üç", frame.clarifications)
        self.assertIn("Çözülemeyen soru: Karar 3?", frame.clarifications)
        self.assertIn("salt-okuma", build_user_content(frame))




    def test_keyboard_interrupt_propagates(self):
        def read(_):
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            collect_guided_prompt_frame(read, MagicMock())









class TestGuidedFrameDefaults(unittest.TestCase):
    """Fast defaults: brief only, no pre-task API call."""

    def setUp(self):
        patcher = patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("EVREN_CLARIFY_ROUNDS", "EVREN_FRAME_SUGGEST", "EVREN_FORM_FIELDS"):
            os.environ.pop(name, None)

    def collect(self, answers, guidance, previous=None):
        inputs = iter(answers)
        return collect_guided_prompt_frame(lambda _: next(inputs), guidance, previous)

    def test_brief_goes_straight_to_the_agent(self):
        guidance = MagicMock()
        frame = self.collect(["timeout düzelt"], guidance)
        guidance.assert_not_called()
        self.assertEqual(frame.brief, "timeout düzelt")
        self.assertEqual(frame.rol, "")





class TestFormPromptDisplay(unittest.TestCase):




    def test_palette_contrast_is_at_least_four_point_five(self):
        from src.ui import FORM_PALETTES

        def luminance(color):
            channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
            linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
                      for c in channels]
            return sum(c * weight for c, weight in zip(linear, (0.2126, 0.7152, 0.0722)))

        for theme, background in (("light", "#ffffff"), ("dark", "#1e1e1e")):
            for kind, color in FORM_PALETTES[theme].items():
                with self.subTest(theme=theme, kind=kind):
                    values = sorted((luminance(color), luminance(background)))
                    contrast = (values[1] + 0.05) / (values[0] + 0.05)
                    self.assertGreaterEqual(contrast, 4.5)






if __name__ == "__main__":
    unittest.main()
