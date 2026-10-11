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
        self.assertIn(session.base_system_prompt, session.messages[0]["content"])
        self.assertIn("YEREL ÇALIŞTIRMA BAĞLAMI", session.messages[0]["content"])
        self.assertNotIn("GÖREV KALIBI", session.messages[0]["content"])
        self.assertTrue(session.frame.is_empty())

    def test_guided_frame_replaces_identity_instead_of_restoring_rejected_role(self):
        session = AgentSession(client=_Client())
        session.apply_frame(PromptFrame(rol="eski rol", cerceve="eski kalıp"))
        session.apply_frame(PromptFrame(brief="yeni iş", cerceve="yeni kalıp"), replace_identity=True)
        self.assertNotIn("eski rol", session.messages[0]["content"])
        self.assertNotIn("eski kalıp", session.messages[0]["content"])
        self.assertIn("yeni kalıp", session.messages[0]["content"])
        self.assertEqual(session.frame.rol, "")


class TestGuidedFrame(unittest.TestCase):
    suggestions = {"rol": "mühendis", "cerceve": "küçük adımlar", "kisit": "cerrahi yama", "cikti": "test sonucu"}

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

    def test_new_brief_discards_previous_fields_and_clarifications(self):
        prev = PromptFrame(brief="eski", rol="eski rol", clarifications="eski yanıt")

        def guidance(frame, clarify):
            self.assertEqual(frame.rol, "")
            self.assertEqual(frame.clarifications, "")
            return FrameGuidance() if clarify else FrameGuidance(suggestions=self.suggestions)

        frame = self.collect(["yeni", "", "", "", ""], guidance, prev)
        self.assertEqual(frame.rol, "mühendis")
        self.assertEqual(prev.rol, "eski rol")

    def test_enter_retains_previous_frame_without_overwriting_populated_fields(self):
        prev = PromptFrame(brief="iş", rol="benim rolüm", cerceve="kalıp", kisit="sınır", cikti="kod")
        guidance = MagicMock(return_value=FrameGuidance())
        frame = self.collect(["", "", "", "", ""], guidance, prev)
        self.assertEqual(frame, prev)
        guidance.assert_called_once()
        self.assertTrue(guidance.call_args.args[1])

    def test_blank_brief_skips_api_and_uses_manual_fields(self):
        guidance = MagicMock()
        frame = self.collect(["", "rol", "", "", ""], guidance)
        guidance.assert_not_called()
        self.assertEqual(frame.rol, "rol")

    def test_slash_command_skips_api(self):
        guidance = MagicMock()
        self.assertEqual(self.collect(["/help"], guidance), "/help")
        guidance.assert_not_called()

    def test_slash_during_clarify_aborts_preparation(self):
        guidance = MagicMock(return_value=FrameGuidance(question="Hedef?"))
        self.assertEqual(self.collect(["iş", "/clear"], guidance), "/clear")
        self.assertEqual(guidance.call_count, 1)

    def test_slash_during_suggestions_aborts_preparation(self):
        guidance = MagicMock(side_effect=[FrameGuidance(), FrameGuidance(suggestions=self.suggestions)])
        self.assertEqual(self.collect(["iş", "/exit"], guidance), "/exit")

    def test_empty_clarification_answer_blocks_task(self):
        with self.assertRaisesRegex(ValueError, "görev başlatılmadı"):
            self.collect(["iş", ""], lambda *_: FrameGuidance(question="Hedef?"))

    def test_clarification_loop_is_bounded(self):
        guidance = MagicMock(return_value=FrameGuidance(question="Hedef?"))
        with self.assertRaisesRegex(ValueError, "Üç netleştirme"):
            self.collect(["iş", "bir", "iki", "üç"], guidance)
        self.assertEqual(guidance.call_count, 4)

    def test_keyboard_interrupt_propagates(self):
        def read(_):
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            collect_guided_prompt_frame(read, MagicMock())

    def test_api_request_is_tool_free_and_validates_shape(self):
        client = MagicMock()
        client.chat_complete.return_value = SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="stop", message=SimpleNamespace(content='{"question": "Hedef?"}'),
        )])
        result = request_frame_guidance(client, "test-model", PromptFrame(brief="iş"), True)
        self.assertEqual(result.question, "Hedef?")
        kwargs = client.chat_complete.call_args.kwargs
        self.assertNotIn("tools", kwargs)
        self.assertEqual(kwargs["model"], "test-model")

    def test_invalid_api_payloads_fail_explicitly(self):
        for payload in ('not json', '[]', '{"question": 2}', '{"question": "", "rol": "x"}'):
            with self.subTest(payload=payload):
                client = MagicMock()
                client.chat_complete.return_value = SimpleNamespace(choices=[SimpleNamespace(
                    finish_reason="stop", message=SimpleNamespace(content=payload),
                )])
                with self.assertRaises(ValueError):
                    request_frame_guidance(client, "test-model", PromptFrame(brief="iş"), True)

    def test_suggestions_only_cover_empty_fields(self):
        client = MagicMock()
        client.chat_complete.return_value = SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="stop",
            message=SimpleNamespace(content='{"cerceve":"adımlar","kisit":"sınır","cikti":"test"}'),
        )])
        frame = PromptFrame(brief="iş", rol="koru")
        result = request_frame_guidance(client, "test-model", frame, False)
        self.assertNotIn("rol", result.suggestions)
        self.assertEqual(frame.rol, "koru")

    def test_missing_suggestions_or_truncated_response_block_task(self):
        for content, reason in (('{"rol":"x"}', "stop"), ('{}', "length"), ('{}', "content_filter")):
            with self.subTest(reason=reason):
                client = MagicMock()
                client.chat_complete.return_value = SimpleNamespace(choices=[SimpleNamespace(
                    finish_reason=reason, message=SimpleNamespace(content=content),
                )])
                with self.assertRaises(ValueError):
                    request_frame_guidance(client, "test-model", PromptFrame(brief="iş"), False)

    def test_repl_wrapper_passes_current_model_and_local_context(self):
        from src.repl import _collect_frame

        session = SimpleNamespace(client=MagicMock(), model="active-model", workspace_root=Path.cwd())
        with patch("src.repl._read_line", side_effect=["iş", "", "", "", ""]), \
             patch("src.repl.request_frame_guidance", side_effect=[
                 FrameGuidance(), FrameGuidance(suggestions=self.suggestions),
             ]) as request:
            frame = _collect_frame(session, None)
        self.assertEqual(frame.rol, "mühendis")
        self.assertEqual(request.call_args.args[1], "active-model")
        self.assertIn("YEREL ÇALIŞTIRMA BAĞLAMI", request.call_args.args[4])


class TestFormPromptDisplay(unittest.TestCase):
    def test_long_question_prints_outside_input_prompt(self):
        from src.repl import _read_line

        question = "Netleştirme: " + "uzun soru " * 30 + "[hedef]\nİkinci satır?"
        terminal = MagicMock()
        terminal.prompt.return_value = " yanıt "
        with patch("src.repl.HAVE_RICH", False), patch("builtins.print") as output:
            answer = _read_line(terminal, question + "\nYanıt > ")
        output.assert_called_once_with(question)
        terminal.prompt.assert_called_once_with("Yanıt > ")
        self.assertEqual(answer, "yanıt")

    def test_suggestion_is_printed_without_rich_markup_or_truncation(self):
        from rich.console import Console
        from src.repl import _read_line

        text = "02 Rol önerisi: [bold]literal[/bold] " + "uzun öneri " * 20 + "SON"
        capture = StringIO()
        narrow_console = Console(file=capture, width=24, color_system=None)
        terminal = MagicMock()
        terminal.prompt.return_value = ""
        with patch("src.repl.console", narrow_console), patch("src.repl.HAVE_RICH", True):
            _read_line(terminal, text + "\nEnter=kabul > ")
        rendered = capture.getvalue()
        self.assertIn("[bold]", rendered)
        self.assertIn("SON", rendered)
        self.assertGreater(len(rendered.splitlines()), 3)
        self.assertEqual("".join(rendered.split()), "".join(text.split()))
        terminal.prompt.assert_called_once_with("Enter=kabul > ")

    def test_plain_input_also_separates_question(self):
        from src.repl import _read_line

        with patch("src.repl.HAVE_RICH", False), \
             patch("builtins.print") as output, patch("builtins.input", return_value="evet") as read:
            self.assertEqual(_read_line(None, "Netleştirme: hedef?\nYanıt > "), "evet")
        output.assert_called_once_with("Netleştirme: hedef?")
        read.assert_called_once_with("Yanıt > ")

    def test_single_line_prompt_is_unchanged(self):
        from src.repl import _read_line

        terminal = MagicMock()
        terminal.prompt.return_value = "brief"
        with patch("builtins.print") as output:
            self.assertEqual(_read_line(terminal, "01 Brief > "), "brief")
        output.assert_not_called()
        terminal.prompt.assert_called_once_with("01 Brief > ")


if __name__ == "__main__":
    unittest.main()
