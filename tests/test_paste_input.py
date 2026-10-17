"""Pasted text is task text, not a command (6.1); big pastes are compacted (6.2)."""

import builtins
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.agent import AgentSession
from src.commands import (
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
    """Routing of pasted text vs. commands at the token level."""

    def test_path_like_and_non_slash_text_is_task_text(self):
        for text in (
            BUILD_ERROR,
            "/home/a",
            "/usr/bin/x: command not found",
            "/c:/work/x.cs(1,1): error CS1002",
            "//comment style line",
            "/home is full, free some space",
            "/src klasörünü incele",
            "",
            "   ",
            "clear",
            "devam et",
            "ask: /clear",
        ):
            with self.subTest(text=text):
                self.assertFalse(is_command(text))
                self.assertFalse(is_unknown_command(text))
                self.assertFalse(is_command_input(text))

    def test_known_commands_stay_commands(self):
        for text in (
            "/clear", "/ssh list", "/help", "/exit", "/q", "/?", "/CLEAR", "/Model qwen",
            "/run echo hi", "/continue", "/ssh add a u@h 22", "/run echo a\necho b",
        ):
            with self.subTest(text=text):
                self.assertTrue(is_command(text))
                self.assertTrue(is_command_input(text))

    def test_multiline_paste_and_typos(self):
        # Unknown first token in a multiline paste is task text; a typo is a command.
        for text in ("/cleare\nikinci satır", "/foo\n/bar.cs(1,1): error CS1002"):
            with self.subTest(paste=text):
                self.assertFalse(is_command_input(text))
        self.assertTrue(is_command_input("/exti now"))  # close-match typo with args stays a command
        for text in ("/cleare", "/hlep", "/zzzzzz"):
            with self.subTest(typo=text):
                self.assertFalse(is_command(text))
                self.assertTrue(is_unknown_command(text))
                self.assertTrue(is_command_input(text))

    def test_suggestion_and_first_token(self):
        self.assertEqual(suggest_command("/cleare"), "/clear")
        self.assertEqual(suggest_command("/hlep"), "/help")
        self.assertIsNone(suggest_command("/zzzzzz"))
        message = unknown_command_message("/cleare")
        self.assertIn("Bunu mu demek istediniz: /clear?", message)
        self.assertIn("Komut listesi için /help", message)
        self.assertNotIn("Bunu mu", unknown_command_message("/zzzzzz"))
        self.assertEqual(first_token("  /ssh   list "), "/ssh")
        self.assertEqual(first_token(""), "")


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

    def test_pasted_error_becomes_the_brief_and_commands_return_as_text(self):
        paste = f"{BUILD_ERROR}\n/src/Portal.Api/B.cs(3,4): error CS1002: ; expected"
        for text in (BUILD_ERROR, paste):
            with self.subTest(brief=text[:20]):
                frame = self.collect([text])
                self.assertIsInstance(frame, PromptFrame)
                self.assertEqual(frame.brief, text)
        for text in ("/clear", "/ssh list", "/cleare"):
            with self.subTest(command=text):
                self.assertEqual(self.collect([text]), text)
        # A command typed during clarification still aborts the frame.
        with patch.dict(os.environ, {"EVREN_CLARIFY_ROUNDS": "2"}):
            abort = MagicMock(return_value=FrameGuidance(question="Hangi hata?"))
            self.assertEqual(self.collect(["derle", "/clear"], abort), "/clear")

    def test_pasted_clarification_answer_is_an_answer_not_a_command(self):
        guidance = MagicMock(side_effect=[FrameGuidance(question="Hangi hata?"), FrameGuidance()])
        with patch.dict(os.environ, {"EVREN_CLARIFY_ROUNDS": "2"}):
            frame = self.collect(["derle", BUILD_ERROR], guidance)
            self.assertIsInstance(frame, PromptFrame)
            self.assertIn("error CS0246", frame.clarifications)


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

    def test_pasted_error_goes_to_the_agent_and_known_commands_work(self):
        paste = f"{BUILD_ERROR}\n/src/Portal.Api/B.cs(3,4): error CS1002: ; expected"
        for text in (BUILD_ERROR, paste):
            with self.subTest(text=text[:20]):
                result = self.run_repl([text, "/exit"])
                result.run_agent.assert_called_once()
                prompt = result.run_agent.call_args.args[1]
                self.assertIn("error CS", prompt)
                self.assertTrue(result.run_agent.call_args.kwargs["compact"])
                self.assertFalse([c for c in result.warn.call_args_list if "Bilinmeyen" in str(c)])
        result = self.run_repl(["/help", "/ssh list", "/clear", "/exit"])
        result.show_help.assert_called_once()
        result.ssh.assert_called_once()
        self.assertEqual(result.ssh.call_args.args[0], "list")
        result.session.clear_history.assert_called_once()
        result.run_agent.assert_not_called()
        result.warn.assert_not_called()

    def test_mistyped_command_warns_with_or_without_a_suggestion(self):
        result = self.run_repl(["/cleare", "/zzzzzz", "/exit"])
        messages = [str(c) for c in result.warn.call_args_list]
        typed = next(m for m in messages if "'/cleare'" in m)
        self.assertIn("Bilinmeyen komut: '/cleare'", typed)
        self.assertIn("Bunu mu demek istediniz: /clear?", typed)
        plain = next(m for m in messages if "'/zzzzzz'" in m)
        self.assertIn("Bilinmeyen komut: '/zzzzzz'", plain)
        self.assertNotIn("Bunu mu", plain)
        result.run_agent.assert_not_called()
        result.session.clear_history.assert_not_called()

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
    def test_defaults_overrides_and_invalid_values(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EVREN_PASTE_MAX_LINES", None)
            os.environ.pop("EVREN_PASTE_MAX_CHARS", None)
            self.assertEqual(paste_max_lines(), 80)
            self.assertEqual(paste_max_chars(), 6000)
        with patch.dict(os.environ, {"EVREN_PASTE_MAX_LINES": "200", "EVREN_PASTE_MAX_CHARS": "20000"}):
            self.assertEqual(paste_max_lines(), 200)
            self.assertEqual(paste_max_chars(), 20000)
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
        for text in ("kısa görev", "", "\n".join(f"satır {i}" for i in range(80))):
            with self.subTest(text=text[:15]):
                self.assertEqual(compact_paste(text, self.root), (text, None))
        self.assertFalse((self.root / ".evren").exists())
        # Text that would not shrink is also left alone (nothing saved).
        text = "\n".join(f"s{i}" for i in range(60))
        self.assertEqual(compact_paste(text, self.root, max_lines=40), (text, None))
        self.assertEqual(list((self.root / ".evren" / "tmp").glob("paste-*.txt")), [])
        # Unwritable target returns the original text.
        text = make_build_log(lines=300, errors=5)
        with patch("src.paste._save_paste", return_value=None):
            self.assertEqual(compact_paste(text, self.root), (text, None))

    def test_44k_paste_shrinks_keeps_errors_and_saves_the_full_text(self):
        text = make_build_log()
        self.assertGreater(len(text), 44_000)
        compact, path = compact_paste(text, self.root)
        self.assertIsNotNone(path)
        self.assertLessEqual(len(compact), 10_500)
        self.assertLess(len(compact), len(text) // 4)
        for line in [line for line in text.splitlines() if "error CS0246" in line]:
            self.assertIn(line, compact)
        self.assertIn("Build FAILED.", compact)  # tail
        self.assertIn("Build started 12:00:01", compact)  # head
        self.assertNotIn("Compiling Portal.Api.Module700 ", compact)  # middle noise is gone
        # Marker and note name the saved file; the full text is on disk; numbers increment.
        count = len(text.splitlines())
        self.assertTrue(compact.startswith(
            f"[Yapıştırılan metin #1: {count} satır, {len(text)} karakter → .evren/tmp/paste-1.txt]"
        ))
        self.assertIn("view_file", compact)
        self.assertEqual(path, self.root / ".evren" / "tmp" / "paste-1.txt")
        self.assertEqual(path.read_text(encoding="utf-8"), text)
        second = make_build_log(lines=900, errors=10)
        compact2, path2 = compact_paste(second, self.root)
        self.assertEqual(path2.name, "paste-2.txt")
        self.assertIn("#2", compact2)
        self.assertEqual(path2.read_text(encoding="utf-8"), second)
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
        # At most sixty error lines are shown from the middle, with a counter.
        lines = ["üst"] * 15 + [f"item {i}: error E{i:04d} unique failure" for i in range(200)] + ["alt"] * 15
        compact, _ = compact_paste("\n".join(lines), self.root, max_lines=50)
        shown = [line for line in compact.splitlines() if line.startswith("item ")]
        self.assertEqual(len(shown), 60)
        self.assertIn("(60/200)", compact)
        self.assertEqual(shown[0], "item 0: error E0000 unique failure")

    def test_char_budgets_clipping_crlf_and_env_limits(self):
        # Many long error lines stay near the budget.
        lines = ["üst"] * 15 + [f"{i} error " + "z" * 290 for i in range(300)] + ["alt"] * 15
        compact, _ = compact_paste("\n".join(lines), self.root, max_lines=50)
        self.assertLessEqual(len(compact), 11_000)
        # A long single line is clipped but fully saved.
        text = "x" * 20_000
        compact, path = compact_paste(text, self.root)
        self.assertIsNotNone(path)
        self.assertLess(len(compact), 1500)
        self.assertEqual(path.read_text(encoding="utf-8"), text)
        # Few but very long lines trigger compaction on characters.
        text = "\n".join(f"{i} " + "q" * 400 for i in range(40))
        self.assertGreater(len(text), 6000)
        compact, path = compact_paste(text, self.root)
        self.assertIsNotNone(path)
        self.assertLess(len(compact), len(text))
        # CRLF input is normalized in the saved file.
        text = "\r\n".join(f"satır {i} " + "w" * 60 for i in range(120)) + "\r\n"
        compact, path = compact_paste(text, self.root)
        self.assertIsNotNone(path)
        self.assertNotIn(b"\r", path.read_bytes())
        self.assertIn("120 satır", compact)
        # Env limits apply.
        text = "\n".join(f"satır {i} " + "v" * 60 for i in range(120))
        with patch.dict(os.environ, {"EVREN_PASTE_MAX_LINES": "500", "EVREN_PASTE_MAX_CHARS": "50000"}):
            self.assertEqual(compact_paste(text, self.root), (text, None))
        with patch.dict(os.environ, {"EVREN_PASTE_MAX_LINES": "100"}):
            compact, path = compact_paste(text, self.root)
            self.assertIsNotNone(path)

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

    def test_small_text_and_generated_prompts_are_never_compacted(self):
        self.session.run_step("testleri çalıştır", compact=True)
        self.assertEqual(self.session.task_goal, "testleri çalıştır")
        self.assertEqual(self.pasted_files(), [])
        self.assertEqual(self.infos, [])
        self.sent.clear()
        text = make_build_log()
        self.session.run_step(text)  # compact defaults to False (skills, templates, playbooks)
        self.assertEqual(self.pasted_files(), [])
        self.assertIn("Compiling Portal.Api.Module700 ", self.sent[0])

    def test_followup_paste_is_compacted_and_numbers_increment(self):
        self.session.run_step("projeyi derle", compact=True)
        self.session.execute_followup(make_build_log(lines=800, errors=12))
        self.assertEqual(len(self.pasted_files()), 1)
        self.assertIn("[Yapıştırılan metin #1:", self.sent[-1])
        self.assertIn("projeyi derle", self.sent[-1])
        self.assertNotIn("Compiling Portal.Api.Module400 ", self.sent[-1])
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
