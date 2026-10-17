"""Pasted text is task text, not a command (6.1); big pastes are compacted (6.2)."""

import builtins
import os
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.agent import AgentSession
from src.commands import (
    KNOWN_COMMANDS,
    first_token,
    is_command,
    is_command_input,
    is_unknown_command,
    suggest_command,
    unknown_command_message,
)
from src.config import paste_max_chars, paste_max_lines
from src.paste import compact_paste, paste_info
from src.prompt_frame import (
    FrameGuidance,
    PromptFrame,
    build_user_content,
    collect_guided_prompt_frame,
    collect_prompt_frame,
)

BUILD_ERROR = (
    "/src/Portal.Api/Portal.Api.csproj : error CS0246: The type or namespace name "
    "'Foo' could not be found (are you missing a using directive?)"
)
TOKEN = "fake" + "TokenForTests0123456789abcd"  # fake value; split so scanners ignore it


def make_build_log(lines=1327, errors=40):
    """A synthetic build log of about 1,300 lines / 44K characters."""
    step = max(1, lines // errors)
    out = ["Build started 12:00:01", "Restore complete (0.4s)"]
    for index in range(lines):
        if index % step == 0 and index // step < errors:
            out.append(
                f"/src/Portal.Api/Module{index}/Handler{index}.cs({index},12): error CS0246: "
                f"The type or namespace name 'Foo{index}' could not be found [/src/Portal.Api/Portal.Api.csproj]"
            )
        else:
            out.append(f"  Compiling Portal.Api.Module{index} -> bin/Debug/net8.0/Module{index}.dll (ok)")
    out += ["Build FAILED.", f"    {errors} Error(s)", "Time Elapsed 00:00:42"]
    return "\n".join(out)


class CommandInputTest(unittest.TestCase):
    def test_path_like_first_token_is_task_text(self):
        for text in (
            BUILD_ERROR,
            "/home/a",
            "/usr/bin/x: command not found",
            "/c:/work/x.cs(1,1): error CS1002",
            "/etc/hosts değişmiş, bakar mısın",
            "//comment style line",
            "/clear.txt dosyasını sil",
        ):
            with self.subTest(text=text):
                self.assertFalse(is_command(text))
                self.assertFalse(is_unknown_command(text))
                self.assertFalse(is_command_input(text))

    def test_known_commands_and_aliases_stay_commands(self):
        for text in (
            "/clear", "/ssh list", "/help", "/exit", "/q", "/?", "/CLEAR", "/Model qwen",
            "/check-in update", "/run echo hi", "/apply root-cause-debug", "/continue",
            "/execute yap artık", "/ssh add a u@h 22",
        ):
            with self.subTest(text=text):
                self.assertTrue(is_command(text))
                self.assertTrue(is_command_input(text))

    def test_non_slash_text_is_never_a_command(self):
        for text in ("", "   ", "clear", "devam et", "ask: /clear"):
            with self.subTest(text=text):
                self.assertFalse(is_command_input(text))

    def test_multiline_paste_with_unknown_first_token_is_task_text(self):
        self.assertFalse(is_command_input("/cleare\nikinci satır"))
        self.assertFalse(is_command_input("/foo\n/bar.cs(1,1): error CS1002"))
        paste = f"{BUILD_ERROR}\n{BUILD_ERROR}\n  Build FAILED."
        self.assertFalse(is_command_input(paste))

    def test_multiline_input_with_known_first_token_stays_a_command(self):
        self.assertTrue(is_command_input("/run echo a\necho b"))

    def test_mistyped_command_is_still_routed_to_the_command_handler(self):
        for text in ("/cleare", "/hlep", "/exti", "/zzzzzz", "/cleare all"):
            with self.subTest(text=text):
                self.assertFalse(is_command(text))
                self.assertTrue(is_unknown_command(text))
                self.assertTrue(is_command_input(text))

    def test_slash_word_with_arguments_but_no_close_command_is_task_text(self):
        for text in ("/home is full, free some space", "/etc is broken", "/src klasörünü incele"):
            with self.subTest(text=text):
                self.assertFalse(is_command_input(text))
        self.assertTrue(is_command_input("/exti now"))

    def test_suggestion_for_typo(self):
        self.assertEqual(suggest_command("/cleare"), "/clear")
        self.assertEqual(suggest_command("/hlep"), "/help")
        self.assertEqual(suggest_command("/models"), "/models")
        self.assertIsNone(suggest_command("/zzzzzz"))
        self.assertIn("Bunu mu demek istediniz: /clear?", unknown_command_message("/cleare"))
        self.assertIn("Komut listesi için /help", unknown_command_message("/cleare"))
        self.assertNotIn("Bunu mu", unknown_command_message("/zzzzzz"))

    def test_first_token(self):
        self.assertEqual(first_token("  /ssh   list "), "/ssh")
        self.assertEqual(first_token(""), "")

    def test_registry_matches_the_repl_dispatch(self):
        """Every command the REPL dispatches is registered, and the other way round."""
        from src import repl

        source = Path(repl.__file__).read_text(encoding="utf-8")
        handled = set()
        for line in source.splitlines():
            if re.search(r"\bcmd (?:==|in|not in)\s", line):
                handled.update(re.findall(r'"(/[A-Za-z?][A-Za-z?-]*)"', line))
        handled.add("/continue")  # handled before the dispatch chain
        self.assertEqual(KNOWN_COMMANDS, handled)


class PromptFrameRoutingTest(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("EVREN_CLARIFY_ROUNDS", "EVREN_FRAME_SUGGEST", "EVREN_FORM_FIELDS"):
            os.environ.pop(name, None)

    def collect(self, answers, guidance=None):
        inputs = iter(answers)
        return collect_guided_prompt_frame(lambda _: next(inputs), guidance or MagicMock())

    def test_pasted_build_error_becomes_the_brief(self):
        frame = self.collect([BUILD_ERROR])
        self.assertIsInstance(frame, PromptFrame)
        self.assertEqual(frame.brief, BUILD_ERROR)

    def test_multiline_paste_starting_with_slash_becomes_the_brief(self):
        paste = f"{BUILD_ERROR}\n/src/Portal.Api/B.cs(3,4): error CS1002: ; expected"
        frame = self.collect([paste])
        self.assertIsInstance(frame, PromptFrame)
        self.assertEqual(frame.brief, paste)

    def test_commands_still_return_as_text(self):
        for text in ("/clear", "/ssh list", "/help", "/cleare"):
            with self.subTest(text=text):
                self.assertEqual(self.collect([text]), text)

    def test_pasted_clarification_answer_is_an_answer_not_a_command(self):
        guidance = MagicMock(side_effect=[FrameGuidance(question="Hangi hata?"), FrameGuidance()])
        with patch.dict(os.environ, {"EVREN_CLARIFY_ROUNDS": "2"}):
            frame = self.collect(["derle", BUILD_ERROR], guidance)
        self.assertIsInstance(frame, PromptFrame)
        self.assertIn("error CS0246", frame.clarifications)

    def test_command_during_clarification_still_aborts(self):
        guidance = MagicMock(return_value=FrameGuidance(question="Hangi hata?"))
        with patch.dict(os.environ, {"EVREN_CLARIFY_ROUNDS": "2"}):
            self.assertEqual(self.collect(["derle", "/clear"], guidance), "/clear")

    def test_legacy_five_line_form(self):
        answers = iter([BUILD_ERROR, "", "", "", ""])
        frame = collect_prompt_frame(lambda _: next(answers))
        self.assertIsInstance(frame, PromptFrame)
        self.assertEqual(frame.rol, BUILD_ERROR)
        self.assertEqual(collect_prompt_frame(lambda _: "/help"), "/help")


class ReplRoutingTest(unittest.TestCase):
    """Scripted REPL sessions: what reaches the agent and what reaches the handler."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        env = patch.dict(os.environ, {"EVREN_AUTO_DOCS": "false"}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        for name in ("EVREN_CLARIFY_ROUNDS", "EVREN_FRAME_SUGGEST", "EVREN_FORM_FIELDS"):
            os.environ.pop(name, None)

    def run_repl(self, inputs):
        from src import repl

        client = MagicMock()
        client.default_model = "test-model"
        client.api_key = "k"
        client.key_pool = []
        client.get_quota.return_value = {"remaining": 10, "limit": 100}
        session = MagicMock()
        session.mode.value = "normal"
        session.model = "test-model"
        session.interrupted = False
        session.task_goal = ""
        session.pending_mode = None
        session.workspace_root = Path(self._tmp.name)
        session.apply_frame.side_effect = lambda frame, **kwargs: build_user_content(frame)
        feed = iter(inputs)
        with patch.object(repl, "EvrenClient", return_value=client), \
                patch.object(repl, "AgentSession", return_value=session), \
                patch.object(repl, "ensure_terms_accepted", return_value=True), \
                patch.object(repl, "HAVE_PT", False), \
                patch.object(repl, "_run_agent") as run_agent, \
                patch.object(repl, "_handle_ssh_command") as ssh, \
                patch.object(repl, "show_help") as show_help, \
                patch.object(repl, "print_warning") as warn, \
                patch.object(builtins, "input", lambda *a, **k: next(feed)):
            repl.start_repl(workspace_path=self._tmp.name)
        return SimpleNamespace(
            run_agent=run_agent, ssh=ssh, show_help=show_help, warn=warn, session=session,
        )

    def test_pasted_build_error_goes_to_the_agent_not_the_command_handler(self):
        result = self.run_repl([BUILD_ERROR, "/exit"])
        result.run_agent.assert_called_once()
        prompt = result.run_agent.call_args.args[1]
        self.assertIn("error CS0246", prompt)
        self.assertTrue(result.run_agent.call_args.kwargs["compact"])
        self.assertFalse([c for c in result.warn.call_args_list if "Bilinmeyen" in str(c)])

    def test_multiline_paste_at_the_request_prompt_goes_to_the_agent(self):
        paste = f"{BUILD_ERROR}\n/src/Portal.Api/B.cs(3,4): error CS1002: ; expected"
        result = self.run_repl([paste, "/exit"])
        result.run_agent.assert_called_once()
        self.assertIn("error CS1002", result.run_agent.call_args.args[1])
        result.warn.assert_not_called()

    def test_known_commands_keep_working(self):
        result = self.run_repl(["/help", "/ssh list", "/clear", "/exit"])
        result.show_help.assert_called_once()
        result.ssh.assert_called_once()
        self.assertEqual(result.ssh.call_args.args[0], "list")
        result.session.clear_history.assert_called_once()
        result.run_agent.assert_not_called()
        result.warn.assert_not_called()

    def test_mistyped_command_warns_with_a_suggestion(self):
        result = self.run_repl(["/cleare", "/exit"])
        result.run_agent.assert_not_called()
        result.session.clear_history.assert_not_called()
        message = str(result.warn.call_args_list[0])
        self.assertIn("Bilinmeyen komut: '/cleare'", message)
        self.assertIn("Bunu mu demek istediniz: /clear?", message)

    def test_unknown_command_without_a_close_match_keeps_the_plain_warning(self):
        result = self.run_repl(["/zzzzzz", "/exit"])
        message = str(result.warn.call_args_list[0])
        self.assertIn("Bilinmeyen komut: '/zzzzzz'", message)
        self.assertNotIn("Bunu mu", message)

    def test_interrupt_followup_with_a_pasted_error_is_not_a_command(self):
        from src.repl import _take_interrupt_followup

        session = SimpleNamespace(interrupted=True, task_goal="derle")
        with patch("src.repl.print_info"):
            kind, text = _take_interrupt_followup(session, lambda _: BUILD_ERROR)
        self.assertEqual((kind, text), ("followup", BUILD_ERROR))
        session.interrupted = True
        with patch("src.repl.print_info"):
            self.assertEqual(_take_interrupt_followup(session, lambda _: "/clear"), ("command", "/clear"))


class PasteConfigTest(unittest.TestCase):
    def test_defaults_and_overrides(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EVREN_PASTE_MAX_LINES", None)
            os.environ.pop("EVREN_PASTE_MAX_CHARS", None)
            self.assertEqual(paste_max_lines(), 80)
            self.assertEqual(paste_max_chars(), 6000)
        with patch.dict(os.environ, {"EVREN_PASTE_MAX_LINES": "200", "EVREN_PASTE_MAX_CHARS": "20000"}):
            self.assertEqual(paste_max_lines(), 200)
            self.assertEqual(paste_max_chars(), 20000)

    def test_invalid_values_fall_back_to_defaults(self):
        for value in ("abc", "0", "-5", ""):
            with self.subTest(value=value), patch.dict(
                os.environ, {"EVREN_PASTE_MAX_LINES": value, "EVREN_PASTE_MAX_CHARS": value},
            ):
                self.assertEqual(paste_max_lines(), 80)
                self.assertEqual(paste_max_chars(), 6000)


class CompactPasteTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        env = patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("EVREN_PASTE_MAX_LINES", None)
        os.environ.pop("EVREN_PASTE_MAX_CHARS", None)

    def test_text_within_the_limits_passes_through_untouched(self):
        text = "\n".join(f"satır {i}" for i in range(80))
        self.assertEqual(compact_paste(text, self.root), (text, None))
        self.assertEqual(compact_paste("kısa görev", self.root), ("kısa görev", None))
        self.assertEqual(compact_paste("", self.root), ("", None))
        self.assertFalse((self.root / ".evren").exists())

    def test_44k_paste_shrinks_to_about_10k_and_keeps_the_error_lines(self):
        text = make_build_log()
        self.assertGreater(len(text), 44_000)
        self.assertGreater(text.count("\n"), 1300)
        compact, path = compact_paste(text, self.root)
        self.assertIsNotNone(path)
        self.assertLessEqual(len(compact), 10_500)
        self.assertLess(len(compact), len(text) // 4)
        error_lines = [line for line in text.splitlines() if "error CS0246" in line]
        self.assertEqual(len(error_lines), 40)
        for line in error_lines:
            self.assertIn(line, compact)
        self.assertIn("Build FAILED.", compact)  # tail
        self.assertIn("Build started 12:00:01", compact)  # head
        self.assertNotIn("Compiling Portal.Api.Module700 ", compact)  # noise from the middle is gone

    def test_marker_and_note_name_the_saved_file(self):
        text = make_build_log()
        compact, path = compact_paste(text, self.root)
        count = len(text.splitlines())
        self.assertTrue(compact.startswith(
            f"[Yapıştırılan metin #1: {count} satır, {len(text)} karakter → .evren/tmp/paste-1.txt]"
        ))
        self.assertIn("view_file", compact)
        self.assertIn(".evren/tmp/paste-1.txt dosyasında", compact)
        self.assertEqual(path, self.root / ".evren" / "tmp" / "paste-1.txt")

    def test_full_text_is_saved_and_numbers_increment(self):
        first = make_build_log()
        second = make_build_log(lines=900, errors=10)
        _, path1 = compact_paste(first, self.root)
        compact2, path2 = compact_paste(second, self.root)
        self.assertEqual(path1.read_text(encoding="utf-8"), first)
        self.assertEqual(path2.read_text(encoding="utf-8"), second)
        self.assertEqual(path2.name, "paste-2.txt")
        self.assertIn("#2", compact2)
        gitignore = (self.root / ".evren" / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("tmp/", gitignore.splitlines())

    def test_error_like_lines_are_kept_deduplicated_and_truncated(self):
        middle = (
            ["Traceback (most recent call last):", "Traceback (most recent call last):"]
            + ["CVE-2024-1234 high severity in lodash"] * 3
            + ["Program.cs(12,5): warning CS8618: Non-nullable property"]
            + ["PANIC: out of memory", "FATAL: disk full", "Test FAILED: test_login"]
            + ["E" + "x" * 500 + " error " + "y" * 500]
        )
        filler = [f"  ok step {i} passed quietly" for i in range(300)]
        lines = ["başlık"] * 15 + middle + filler + ["son"] * 15
        compact, _ = compact_paste("\n".join(lines), self.root)
        for needle in (
            "CVE-2024-1234 high severity in lodash", "warning CS8618", "PANIC: out of memory",
            "FATAL: disk full", "Test FAILED: test_login",
        ):
            self.assertEqual(compact.count(needle), 1, needle)
        self.assertEqual(compact.count("Traceback (most recent call last):"), 1)
        long_line = next(line for line in compact.splitlines() if line.startswith("Ex"))
        self.assertLessEqual(len(long_line), 300 + len("... [+1007 karakter]"))
        self.assertIn("karakter]", long_line)
        self.assertNotIn("ok step 120 passed", compact)

    def test_at_most_sixty_error_lines_from_the_middle(self):
        lines = ["üst"] * 15
        lines += [f"item {i}: error E{i:04d} unique failure" for i in range(200)]
        lines += ["alt"] * 15
        compact, _ = compact_paste("\n".join(lines), self.root, max_lines=50)
        shown = [line for line in compact.splitlines() if line.startswith("item ")]
        self.assertEqual(len(shown), 60)
        self.assertIn("(60/200)", compact)
        self.assertEqual(shown[0], "item 0: error E0000 unique failure")

    def test_many_long_error_lines_stay_near_the_budget(self):
        lines = ["üst"] * 15
        lines += [f"{i} error " + "z" * 290 for i in range(300)]
        lines += ["alt"] * 15
        compact, _ = compact_paste("\n".join(lines), self.root, max_lines=50)
        self.assertLessEqual(len(compact), 11_000)

    def test_long_single_line_paste_is_clipped(self):
        text = "x" * 20_000
        compact, path = compact_paste(text, self.root)
        self.assertIsNotNone(path)
        self.assertLess(len(compact), 1500)
        self.assertEqual(path.read_text(encoding="utf-8"), text)

    def test_few_but_very_long_lines_trigger_on_chars(self):
        text = "\n".join(f"{i} " + "q" * 400 for i in range(40))
        self.assertGreater(len(text), 6000)
        compact, path = compact_paste(text, self.root)
        self.assertIsNotNone(path)
        self.assertLess(len(compact), len(text))

    def test_crlf_input_is_normalized_in_the_saved_file(self):
        text = "\r\n".join(f"satır {i} " + "w" * 60 for i in range(120)) + "\r\n"
        compact, path = compact_paste(text, self.root)
        self.assertIsNotNone(path)
        raw = path.read_bytes()
        self.assertNotIn(b"\r", raw)
        self.assertIn("120 satır", compact)

    def test_env_limits_apply(self):
        text = "\n".join(f"satır {i} " + "v" * 60 for i in range(120))
        with patch.dict(os.environ, {"EVREN_PASTE_MAX_LINES": "500", "EVREN_PASTE_MAX_CHARS": "50000"}):
            self.assertEqual(compact_paste(text, self.root), (text, None))
        with patch.dict(os.environ, {"EVREN_PASTE_MAX_LINES": "100"}):
            compact, path = compact_paste(text, self.root)
            self.assertIsNotNone(path)

    def test_text_that_would_not_shrink_is_left_alone(self):
        text = "\n".join(f"s{i}" for i in range(60))
        self.assertEqual(compact_paste(text, self.root, max_lines=40), (text, None))
        self.assertEqual(list((self.root / ".evren" / "tmp").glob("paste-*.txt")), [])

    def test_unwritable_target_returns_the_original_text(self):
        text = make_build_log(lines=300, errors=5)
        with patch("src.paste._save_paste", return_value=None):
            self.assertEqual(compact_paste(text, self.root), (text, None))

    def test_paste_info_is_a_one_line_turkish_notice(self):
        text = make_build_log(lines=300, errors=5)
        _, path = compact_paste(text, self.root)
        info = paste_info(text, path)
        self.assertNotIn("\n", info)
        self.assertIn(".evren/tmp/paste-1.txt", info)
        self.assertIn("satır", info)
        self.assertIn("view_file", info)


def _reply(content="tamam"):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            finish_reason="stop", message=SimpleNamespace(content=content, tool_calls=[]),
        )],
        usage=None,
    )


class AgentPasteTest(unittest.TestCase):
    """Compaction at the point where user text enters the agent."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        env = patch.dict(
            os.environ, {"EVREN_AUTO_DOCS": "false", "EVREN_AUTO_ROUTE": "false"}, clear=False,
        )
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("EVREN_PASTE_MAX_LINES", None)
        os.environ.pop("EVREN_PASTE_MAX_CHARS", None)
        self.client = MagicMock(default_model="m")
        self.session = AgentSession(self.client, workspace_root=self.root)
        self.infos = []
        for name in ("print_warning", "print_error", "print_markdown", "print_tool_call",
                     "print_tool_result", "print_reasoning"):
            patcher = patch(f"src.agent.{name}")
            patcher.start()
            self.addCleanup(patcher.stop)
        info = patch("src.agent.print_info", side_effect=self.infos.append)
        info.start()
        self.addCleanup(info.stop)
        self.sent = []

        def reply(**kwargs):
            self.sent.append(str(kwargs["messages"]))
            return _reply()

        self.client.chat_stream_assembled.side_effect = reply

    def pasted_files(self):
        return sorted((self.root / ".evren" / "tmp").glob("paste-*.txt"))

    def test_big_user_paste_is_saved_and_only_the_compact_form_is_sent(self):
        text = make_build_log()
        self.session.run_step(text, compact=True)
        self.assertEqual(len(self.pasted_files()), 1)
        self.assertEqual(self.pasted_files()[0].read_text(encoding="utf-8"), text)
        self.assertIn("[Yapıştırılan metin #1:", self.sent[0])
        self.assertIn("error CS0246", self.sent[0])
        self.assertNotIn("Compiling Portal.Api.Module700 ", self.sent[0])
        self.assertLess(len(self.sent[0]), len(text) // 3)
        # The compact form is the task goal, so /continue does not resend the paste.
        self.assertTrue(self.session.task_goal.startswith("[Yapıştırılan metin #1:"))
        user_messages = [m for m in self.session.messages if m["role"] == "user"]
        self.assertLess(len(user_messages[0]["content"]), 12_000)
        self.assertTrue(any("paste-1.txt" in info for info in self.infos))

    def test_secrets_are_masked_before_the_paste_is_written_to_disk(self):
        lines = [f"  Compiling Module{i} -> ok" for i in range(400)]
        lines[200] = f'curl failed: error 401 with -H "Authorization: Bearer {TOKEN}"'
        text = "\n".join(lines)
        self.session.run_step(text, compact=True)
        saved = self.pasted_files()[0].read_text(encoding="utf-8")
        self.assertNotIn(TOKEN, saved)
        self.assertIn("Bearer {{EVREN_SECRET_1}}", saved)
        self.assertNotIn(TOKEN, self.sent[0])
        self.assertIn("{{EVREN_SECRET_1}}", self.sent[0])
        self.assertNotIn(TOKEN, self.session.task_goal)

    def test_small_text_is_unchanged_even_with_compact_on(self):
        self.session.run_step("testleri çalıştır", compact=True)
        self.assertEqual(self.session.task_goal, "testleri çalıştır")
        self.assertEqual(self.pasted_files(), [])
        self.assertEqual(self.infos, [])

    def test_generated_prompts_are_never_compacted(self):
        text = make_build_log()
        self.session.run_step(text)  # compact defaults to False (skills, templates, playbooks)
        self.assertEqual(self.pasted_files(), [])
        self.assertIn("Compiling Portal.Api.Module700 ", self.sent[0])

    def test_big_followup_paste_is_compacted_too(self):
        self.session.run_step("projeyi derle", compact=True)
        text = make_build_log(lines=800, errors=12)
        self.session.execute_followup(text)
        self.assertEqual(len(self.pasted_files()), 1)
        self.assertIn("[Yapıştırılan metin #1:", self.sent[-1])
        self.assertIn("projeyi derle", self.sent[-1])
        self.assertNotIn("Compiling Portal.Api.Module400 ", self.sent[-1])

    def test_second_paste_gets_the_next_number(self):
        self.session.run_step(make_build_log(), compact=True)
        self.session.run_step(make_build_log(lines=900, errors=9), compact=True)
        self.assertEqual([p.name for p in self.pasted_files()], ["paste-1.txt", "paste-2.txt"])
        self.assertIn("[Yapıştırılan metin #2:", self.sent[-1])


class CliCompactTest(unittest.TestCase):
    def test_cli_agent_marks_user_text_for_compaction(self):
        from src import cli

        session = MagicMock()
        args = SimpleNamespace(
            workspace=None, model=None, max_steps=None, prompt="derle", rol=None,
            cerceve=None, brief=None, kisit=None, cikti=None,
        )
        with patch("src.terms.ensure_terms_accepted", return_value=True), \
                patch("src.api_client.EvrenClient"), \
                patch("src.agent.AgentSession", return_value=session), \
                patch.object(cli, "_pin_best_key"), \
                patch.object(cli, "get_workspace_dir", return_value=Path(tempfile.gettempdir())), \
                patch("src.project_docs.ensure_project_docs", side_effect=RuntimeError("skip")), \
                patch("src.cli_home.ensure_cli_docs"), \
                patch.object(cli, "print_info"), patch.object(cli, "print_warning"):
            cli.cmd_agent(args)
        session.run_step.assert_called_once()
        self.assertTrue(session.run_step.call_args.kwargs["compact"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
