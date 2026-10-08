import unittest
from types import SimpleNamespace

from src.prompt_frame import (
    PromptFrame,
    build_user_content,
    collect_prompt_frame,
    compose_system,
    delivery_summary,
    identity_block,
)
from src.agent import AgentSession
from src.cli import load_frame


class _Client:
    default_model = "test-model"


class TestPromptFrame(unittest.TestCase):
    def test_blank_fields_are_dropped(self):
        frame = PromptFrame(rol="  kimlik  ", brief="   ", kisit="ölçek")
        system = compose_system("TABAN", frame)
        user = build_user_content(frame)

        self.assertIn("Rol: kimlik", system)
        self.assertNotIn("Çerçeve:", system)
        self.assertNotIn("Brief", system)
        self.assertNotIn("Kısıt", system)

        self.assertIn("Kısıt: ölçek", user)
        self.assertNotIn("Brief", user)
        self.assertNotIn("Rol", user)
        self.assertNotIn("Çerçeve", user)

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

    def test_identity_only_gets_a_user_nudge(self):
        frame = PromptFrame(cerceve="strateji")
        self.assertEqual(build_user_content(frame), "Verilen rol ve çerçeveye göre ilerle.")

    def test_extra_prompt_appended_when_task_present(self):
        frame = PromptFrame(brief="tek cümle")
        user = build_user_content(frame, extra="ve testi de yaz")
        self.assertIn("Brief: tek cümle", user)
        self.assertIn("Ek istek:\nve testi de yaz", user)

    def test_extra_prompt_is_the_user_message_when_no_task_lines(self):
        frame = PromptFrame(rol="mühendis")
        user = build_user_content(frame, extra="ask: dosyayı oku")
        self.assertEqual(user, "ask: dosyayı oku")

    def test_slash_on_first_line_skips_the_rest(self):
        answers = iter(["/help", "should-not-be-read"])

        def read_line(_label):
            return next(answers)

        self.assertEqual(collect_prompt_frame(read_line), "/help")

    def test_collect_strips_and_skips_blanks(self):
        answers = iter(["", " çerçeve ", "brief", "", "pptx"])

        def read_line(_label):
            return next(answers)

        frame = collect_prompt_frame(read_line)
        self.assertIsInstance(frame, PromptFrame)
        self.assertEqual(frame.rol, "")
        self.assertEqual(frame.cerceve, "çerçeve")
        self.assertEqual(frame.cikti, "pptx")
        self.assertEqual(delivery_summary(frame), "Çerçeve→system, Brief→user, Çıktı→user")

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

    def test_previous_answer_can_be_overridden(self):
        prev = PromptFrame(rol="eski")
        answers = iter(["yeni", "", "", "", ""])

        def read_line(_label):
            return next(answers)

        frame = collect_prompt_frame(read_line, previous=prev)
        self.assertEqual(frame.rol, "yeni")

    def test_field_prompt_shows_previous_value(self):
        from src.prompt_frame import field_prompt

        self.assertIn("önceki: kimlik", field_prompt("01 Rol", "hint", "kimlik"))
        self.assertNotIn("önceki", field_prompt("01 Rol", "hint"))

    def test_load_frame_from_flags_does_not_need_a_positional(self):
        args = SimpleNamespace(prompt=None, rol="kimlik", cerceve="", brief="iş", kisit="", cikti="")
        loaded = load_frame(args)
        self.assertIsNotNone(loaded)
        frame, prompt = loaded
        self.assertEqual(frame.rol, "kimlik")
        self.assertEqual(frame.brief, "iş")
        self.assertEqual(prompt, "")

    def test_load_frame_keeps_classic_prompt(self):
        args = SimpleNamespace(prompt="  merhaba  ", rol="", cerceve=None, brief="", kisit="", cikti="")
        frame, prompt = load_frame(args)
        self.assertTrue(frame.is_empty())
        self.assertEqual(prompt, "merhaba")

    def test_from_namespace(self):
        args = SimpleNamespace(rol="a", cerceve=None, brief="b", kisit="", cikti="c")
        frame = PromptFrame.from_namespace(args)
        self.assertEqual(frame.rol, "a")
        self.assertEqual(frame.cerceve, "")
        self.assertEqual(frame.brief, "b")
        self.assertEqual(frame.cikti, "c")


class TestAgentFrame(unittest.TestCase):
    def test_apply_frame_merges_identity_and_keeps_base(self):
        session = AgentSession(client=_Client(), workspace_root=None, model="test-model")
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
        session = AgentSession(client=_Client(), workspace_root=None, model="test-model")
        session.apply_frame(PromptFrame(rol="kimlik", brief="iş"))
        session.clear_history()
        self.assertEqual(session.messages[0]["content"], session.base_system_prompt)
        self.assertTrue(session.frame.is_empty())


if __name__ == "__main__":
    unittest.main()
