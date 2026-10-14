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

    def test_new_task_with_execution_words_is_not_followup(self):
        from src.prompt_frame import is_execution_followup

        for text in ("postgres cluster yap", "yap artık postgres migration", "yeni uygulama başla"):
            self.assertFalse(is_execution_followup(text))

    def test_execution_reply_during_clarification_preserves_brief_and_answers(self):
        for answer in ("yap artık", "soru sorma", "devam", "hepsine onay veriyorum"):
            with self.subTest(answer=answer):
                guidance = MagicMock(side_effect=[
                    FrameGuidance(question="Tek yazarlı mı çok yazarlı mı?"),
                    FrameGuidance(question="Hangi sürüm?"),
                ])
                frame = self.collect(["iki düğümü bağla", "çok yazarlı", answer], guidance)
                self.assertEqual(guidance.call_count, 2)
                self.assertEqual(frame.brief, "iki düğümü bağla")
                self.assertIn("Yanıt: çok yazarlı", frame.clarifications)
                self.assertIn("Çözülemeyen soru: Hangi sürüm?", frame.clarifications)
                self.assertIn("salt-okuma", frame.clarifications)
                self.assertEqual(frame.rol, "")

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
        guidance = MagicMock(side_effect=[
            FrameGuidance(question=f"Karar {index}?") for index in range(4)
        ] + [FrameGuidance(suggestions=self.suggestions)])
        frame = self.collect(["iş", "bir", "iki", "üç", "devam", "", "", "", ""], guidance)
        self.assertEqual(guidance.call_count, 5)
        self.assertIn("Yanıt: üç", frame.clarifications)
        self.assertIn("Çözülemeyen soru: Karar 3?", frame.clarifications)
        self.assertIn("salt-okuma", build_user_content(frame))

    def test_repeated_question_offers_intent_correction_and_preserves_answers(self):
        guidance = MagicMock(side_effect=[
            FrameGuidance(question="Hedef?"),
            FrameGuidance(question=" HEDEF? "),
            FrameGuidance(suggestions=self.suggestions),
        ])
        frame = self.collect([
            "iki sunucudaki uygulamaları postgres ile bağla",
            "yerel ve bursa3-k8s",
            "uygulamalar değil, iki sunucuda PostgreSQL aktif-aktif olsun",
            "", "", "", "",
        ], guidance)
        content = build_user_content(frame)
        self.assertIn("yerel ve bursa3-k8s", content)
        self.assertIn("PostgreSQL aktif-aktif", content)
        self.assertIn("Çözülemeyen soru", content)
        self.assertEqual(guidance.call_count, 3)

    def test_limit_decision_can_cancel_or_run_slash_command(self):
        for decision in ("iptal", "", "/clear"):
            with self.subTest(decision=decision):
                guidance = MagicMock(return_value=FrameGuidance(question="Hedef?"))
                if decision.startswith("/"):
                    self.assertEqual(self.collect(["iş", "yanıt", decision], guidance), decision)
                else:
                    with self.assertRaisesRegex(ValueError, "iptal"):
                        self.collect(["iş", "yanıt", decision], guidance)
                self.assertEqual(guidance.call_count, 2)

    def test_three_server_questions_keep_both_endpoints_and_corrected_intent(self):
        guidance = MagicMock(side_effect=[
            FrameGuidance(question="Hangi uygulamalar ve hangi ortam?"),
            FrameGuidance(question="Uygulamaların adları?"),
            FrameGuidance(question="Uygulama ve bağlantı adresleri?"),
            FrameGuidance(question="Hangi uygulamalar?"),
            FrameGuidance(suggestions=self.suggestions),
        ])
        frame = self.collect([
            "bu host ve bursa3-k8s uygulamaları postgres ile bağla",
            "ilki bu hostta diğeri bursa3-k8s",
            "bu sunucuda ve bursa3-k8s üzerinde",
            "postgresql://test-user:test-secret@192.0.2.30:30432/postgres\n"
            "postgresql://test-user:test-secret@198.51.100.30:30432/postgres",
            "aslında iki sunucu arasında aktif aktif PostgreSQL istiyorum",
            "", "", "", "",
        ], guidance)
        self.assertIn("192.0.2.30", frame.clarifications)
        self.assertIn("198.51.100.30", frame.clarifications)
        self.assertIn("aktif aktif PostgreSQL", frame.clarifications)
        self.assertIn("izinler netleşmeden", frame.clarifications)

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
        self.assertEqual(kwargs["model"], "test-model")
        tool = kwargs["tools"][0]["function"]
        self.assertEqual(tool["name"], "submit_frame_guidance")
        self.assertEqual(set(tool["parameters"]["properties"]), {"question"})
        self.assertEqual(kwargs["tool_choice"]["function"]["name"], "submit_frame_guidance")
        self.assertNotIn("view_file", str(kwargs["tools"]))
        instruction = kwargs["messages"][0]["content"]
        self.assertIn("ASIL HEDEF", instruction)
        self.assertIn("tek karar", instruction)
        self.assertEqual(kwargs["messages"][0]["role"], "system")
        self.assertTrue(all(message["role"] != "system" for message in kwargs["messages"][1:]))

    def test_guidance_accepts_tool_schema_arguments(self):
        client = MagicMock()
        client.chat_complete.return_value = SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="tool_calls",
            message=SimpleNamespace(
                content="",
                tool_calls=[SimpleNamespace(function=SimpleNamespace(
                    name="submit_frame_guidance",
                    arguments='{"question": "Hangi namespace?"}',
                ))],
            ),
        )])
        result = request_frame_guidance(client, "test-model", PromptFrame(brief="iş"), True)
        self.assertEqual(result.question, "Hangi namespace?")
        sent = client.chat_complete.call_args.kwargs
        self.assertEqual(sent["messages"][0]["role"], "system")
        schema = sent["tools"][0]["function"]["parameters"]
        self.assertEqual(schema["required"], ["question"])
        self.assertIn("output_schema", sent["messages"][1]["content"])

    def test_suggestion_schema_lists_only_empty_fields(self):
        client = MagicMock()
        client.chat_complete.return_value = SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="tool_calls",
            message=SimpleNamespace(
                content=None,
                tool_calls=[SimpleNamespace(function=SimpleNamespace(
                    name="submit_frame_guidance",
                    arguments='{"cerceve":"adımlar","kisit":"sınır","cikti":"test"}',
                ))],
            ),
        )])
        result = request_frame_guidance(
            client, "test-model", PromptFrame(brief="iş", rol="koru"), False,
        )
        self.assertEqual(set(result.suggestions), {"cerceve", "kisit", "cikti"})
        properties = client.chat_complete.call_args.kwargs["tools"][0]["function"]["parameters"]["properties"]
        self.assertEqual(set(properties), {"cerceve", "kisit", "cikti"})

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

        from src.project_docs import ensure_project_docs

        # Own workspace with a generated agent.md: agent.md is git-ignored, so
        # relying on Path.cwd() failed on every fresh clone.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        ensure_project_docs(root, goal="x", status="tamam", read_only=False)
        session = SimpleNamespace(client=MagicMock(), model="active-model", workspace_root=root)
        with patch("src.repl._read_line", side_effect=["iş", "", "", "", ""]), \
             patch("src.repl.request_frame_guidance", side_effect=[
                 FrameGuidance(), FrameGuidance(suggestions=self.suggestions),
             ]) as request:
            frame = _collect_frame(session, None)
        self.assertEqual(frame.rol, "mühendis")
        self.assertEqual(request.call_args.args[1], "active-model")
        # Plan token-consumption-fixes F4: the guidance call is a separate API
        # request, so it now receives the compact project digest instead of the
        # full execution-context block. It must still know which project it is
        # preparing for.
        context = request.call_args.args[4]
        self.assertIn("PROJE ÖZETİ", context)
        self.assertIn("Çalışma dizini", context)
        self.assertNotIn("YEREL ÇALIŞTIRMA BAĞLAMI", context)


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

    def test_previous_identity_is_kept_without_asking(self):
        prev = PromptFrame(brief="eski", rol="mühendis", cerceve="küçük adımlar")
        frame = self.collect([""], MagicMock(), prev)
        self.assertEqual(frame.rol, "mühendis")
        self.assertEqual(frame.cerceve, "küçük adımlar")

    def test_opt_in_clarify_is_bounded(self):
        guidance = MagicMock(side_effect=[
            FrameGuidance(question="Hangi dosya?"), FrameGuidance(question="Hangi test?"),
        ])
        with patch.dict(os.environ, {"EVREN_CLARIFY_ROUNDS": "2"}):
            frame = self.collect(["iş", "a.py", "devam"], guidance)
        self.assertEqual(guidance.call_count, 2)
        self.assertIn("Yanıt: a.py", frame.clarifications)
        self.assertIn("Çözülemeyen soru: Hangi test?", frame.clarifications)

    def test_opt_in_fields_are_asked(self):
        with patch.dict(os.environ, {"EVREN_FORM_FIELDS": "true"}):
            frame = self.collect(["iş", "rol", "", "", ""], MagicMock())
        self.assertEqual(frame.rol, "rol")


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
        with patch("src.repl.console", narrow_console), patch("src.repl.HAVE_RICH", True), \
             patch.dict("os.environ", {"EVREN_UI_THEME": "dark"}):
            _read_line(terminal, text + "\nEnter=kabul > ")
        rendered = capture.getvalue()
        self.assertIn("[bold]", rendered)
        self.assertIn("SON", rendered)
        self.assertGreater(len(rendered.splitlines()), 3)
        content = "".join(rendered.split())
        for word in ("literal", "SON", "önerisi"):
            self.assertIn(word, content)
        terminal.prompt.assert_called_once_with([("bold fg:#8fdf8f", "Enter=kabul > ")])

    def test_colored_panels_and_matching_prompt_labels(self):
        from rich.console import Console
        from rich.panel import Panel
        from rich.text import Text
        from src.repl import _read_line

        for label, title, color in (
            ("Yanıt > ", "Netleştirme", "#66d9ef"),
            ("Karar > ", "Netleştirme kararı", "#ffd166"),
            ("Enter=kabul > ", "Alan önerisi", "#8fdf8f"),
        ):
            with self.subTest(label=label):
                terminal = MagicMock()
                terminal.prompt.return_value = "yanıt"
                capture = StringIO()
                colored_console = Console(file=capture, width=50, force_terminal=True)
                with patch("src.repl.console", colored_console), patch("src.repl.HAVE_RICH", True), \
                     patch.dict("os.environ", {"EVREN_UI_THEME": "dark"}), \
                     patch.object(colored_console, "print", wraps=colored_console.print) as render:
                    self.assertEqual(_read_line(terminal, "[bold]soru[/bold]\n" + label), "yanıt")
                panel = render.call_args.args[0]
                self.assertIsInstance(panel, Panel)
                self.assertEqual(panel.title, title)
                self.assertEqual(panel.border_style, color)
                self.assertIsInstance(panel.renderable, Text)
                self.assertEqual(panel.renderable.plain, "[bold]soru[/bold]")
                self.assertIn("\x1b[", capture.getvalue())
                terminal.prompt.assert_called_once_with([(f"bold fg:{color}", label)])

    def test_light_palette_is_shared_by_panel_and_input(self):
        from rich.console import Console
        from src.repl import _read_line
        from src.ui import FORM_PALETTES

        for label, kind in (("Yanıt > ", "question"), ("Karar > ", "decision"),
                            ("Enter=kabul > ", "suggestion")):
            with self.subTest(label=label):
                terminal = MagicMock()
                terminal.prompt.return_value = ""
                output = MagicMock(spec=Console)
                with patch("src.repl.console", output), patch("src.repl.HAVE_RICH", True), \
                     patch.dict("os.environ", {"EVREN_UI_THEME": "light"}):
                    _read_line(terminal, "metin\n" + label)
                color = FORM_PALETTES["light"][kind]
                self.assertEqual(output.print.call_args.args[0].border_style, color)
                terminal.prompt.assert_called_once_with([(f"bold fg:{color}", label)])

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

    def test_theme_validation_and_default(self):
        from src.config import get_ui_theme

        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(get_ui_theme(), "dark")
        with patch.dict("os.environ", {"EVREN_UI_THEME": " LIGHT "}):
            self.assertEqual(get_ui_theme(), "light")
        with patch.dict("os.environ", {"EVREN_UI_THEME": "automatic"}):
            with self.assertRaisesRegex(ValueError, "EVREN_UI_THEME"):
                get_ui_theme()

    def test_rich_without_prompt_toolkit_colors_label_and_reads_answer(self):
        from rich.console import Console
        from src.repl import _read_line

        capture = StringIO()
        colored_console = Console(file=capture, width=50, force_terminal=True)
        with patch("src.repl.console", colored_console), patch("src.repl.HAVE_RICH", True), \
             patch("builtins.input", return_value="yanıt") as read:
            self.assertEqual(_read_line(None, "Netleştirme: soru?\nYanıt > "), "yanıt")
        self.assertIn("Yanıt >", capture.getvalue())
        self.assertIn("\x1b[", capture.getvalue())
        read.assert_called_once_with()

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
