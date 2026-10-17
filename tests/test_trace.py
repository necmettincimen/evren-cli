"""Task trace (.evren/logs/trace.jsonl) and `evren debug last` (plan cli-stability-speed, 0.3).

Loops and wasted steps used to be found by hand-parsing logs. Every agent task now
leaves one `task_start`, a `request` per model call, a `tool` per tool call and a
`task_end`; `debug last` renders the last task as a step table.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from rich.console import Console

from src import trace as trace_mod
from src.agent import AgentSession
from src.config import trace_enabled
from src.secret_vault import SecretVault, set_active_vault
from src.trace import (
    ARCHIVE_FILE_NAME,
    MAX_BYTES,
    TaskTrace,
    classify_outcome,
    print_last_task,
    summarize_task,
    table_rows,
    trace_path,
)

SECRET = "abc123xyzSECRET987"
GREP = 'git -C portal-backend grep -rn -E "TransferWorker|PreparationWorker"'
GREP_OUTPUT = "Presentation/Program.cs:1: using X;\n" * 20


def call(name, args, call_id="c"):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(args)))


def usage(prompt, cached, completion):
    return SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
        prompt_tokens_details=SimpleNamespace(cached_tokens=cached),
    )


def reply(content="", tools=(), usage=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            finish_reason="tool_calls" if tools else "stop",
            message=SimpleNamespace(content=content, tool_calls=list(tools)),
        )],
        usage=usage,
    )


def wide_console(buffer: io.StringIO) -> Console:
    return Console(file=buffer, width=170, force_terminal=False, color_system=None)


class TraceTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        env = patch.dict(os.environ, {"EVREN_AUTO_DOCS": "false", "EVREN_AUTO_ROUTE": "false"})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("EVREN_TRACE", None)
        # run_step installs its vault as the active one; leave a clean one behind.
        self.addCleanup(set_active_vault, SecretVault())

    def records(self) -> list[dict]:
        path = trace_path(self.root)
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class AgentTraceTest(TraceTestBase):
    def setUp(self):
        super().setUp()
        self.client = MagicMock(default_model="m")
        self.session = AgentSession(self.client, workspace_root=self.root)
        for name in ("print_info", "print_warning", "print_error", "print_markdown",
                     "print_tool_call", "print_tool_result", "print_reasoning"):
            patcher = patch(f"src.agent.{name}")
            patcher.start()
            self.addCleanup(patcher.stop)

    def _run(self, replies, task="kalan işi yap", executor_output=GREP_OUTPUT, **kwargs):
        self.client.chat_stream_assembled.side_effect = lambda **_kw: replies.pop(0)
        with patch("src.agent.execute_tool_call", return_value=executor_output) as execute:
            result = self.session.run_step(task, **kwargs)
        return result, execute

    def test_run_writes_start_requests_tools_blocked_call_and_end(self):
        replies = [
            reply(tools=[call("run_command", {"command": GREP}, "a")], usage=usage(1000, 800, 50)),
            reply(tools=[call("run_command", {"command": GREP}, "b")], usage=usage(1200, 1000, 40)),
            reply("Bulgular: Program.cs:1.", usage=usage(1300, 1100, 30)),
        ]
        self._run(replies)
        records = self.records()

        self.assertEqual(
            [r["type"] for r in records],
            ["task_start", "request", "tool", "request", "tool", "request", "task_end"],
        )
        self.assertEqual(len({r["task_id"] for r in records}), 1)

        start, req1, tool1, req2, tool2, req3, end = records
        self.assertEqual(start["model"], "m")
        self.assertEqual(start["mode"], "normal")
        self.assertEqual(start["text_preview"], "kalan işi yap")
        self.assertTrue(start["ts"].endswith("Z"))

        self.assertEqual([req1["step"], req2["step"], req3["step"]], [1, 2, 3])
        self.assertEqual(
            (req1["prompt_tokens"], req1["cached_tokens"], req1["completion_tokens"]), (1000, 800, 50),
        )
        self.assertEqual(req3["finish_reason"], "stop")
        self.assertEqual(req1["finish_reason"], "tool_calls")
        self.assertIsInstance(req1["ms"], int)

        self.assertEqual((tool1["tool"], tool1["step"]), ("run_command", 1))
        self.assertIn("git -C portal-backend grep", tool1["args"])
        self.assertLessEqual(len(tool1["args"]), 160)
        self.assertFalse(tool1["blocked"])
        self.assertTrue(tool1["ok"])
        self.assertEqual(tool1["result_chars"], len(GREP_OUTPUT))

        self.assertTrue(tool2["blocked"])            # the repeat guard refused the second call
        self.assertFalse(tool2["ok"])
        self.assertEqual(tool2["step"], 2)
        self.assertEqual(tool2["sig"], tool1["sig"])  # same call, so it counts as a repeat

        self.assertEqual((end["steps"], end["requests"], end["outcome"]), (3, 3, "ok"))
        self.assertIsInstance(end["ms"], int)

    def test_missing_usage_is_recorded_as_nulls(self):
        self._run([reply("tamam")])
        request = next(r for r in self.records() if r["type"] == "request")
        self.assertIsNone(request["prompt_tokens"])
        self.assertIsNone(request["cached_tokens"])
        self.assertIsNone(request["completion_tokens"])

    def test_failed_result_marks_the_tool_not_ok(self):
        replies = [reply(tools=[call("run_command", {"command": "x"}, "a")]), reply("bitti")]
        self._run(replies, executor_output="HATA: komut bulunamadı")
        tool = next(r for r in self.records() if r["type"] == "tool")
        self.assertFalse(tool["ok"])
        self.assertFalse(tool["blocked"])

    def test_invalid_arguments_and_denied_calls_are_traced(self):
        broken = SimpleNamespace(id="a", function=SimpleNamespace(name="run_command", arguments="{not json"))
        edit = call("edit_file", {"path": "a.py", "target_snippet": "x", "replacement_snippet": "y"}, "b")
        replies = [reply(tools=[broken]), reply(tools=[edit]), reply("bitti")]
        _result, execute = self._run(replies, task="ask: dosyayı incele")
        tools = [r for r in self.records() if r["type"] == "tool"]
        self.assertEqual([t["ok"] for t in tools], [False, False])
        self.assertEqual(tools[1]["tool"], "edit_file")   # REDDEDİLDİ: ask mode forbids writes
        self.assertEqual(execute.call_count, 0)

    def test_step_limit_outcome(self):
        replies = [reply(tools=[call("run_command", {"command": GREP}, "a")])]
        result, _execute = self._run(replies, max_steps=1)
        self.assertTrue(result.startswith("HATA: Ajan maksimum adım"))
        end = self.records()[-1]
        self.assertEqual((end["type"], end["outcome"]), ("task_end", "max_steps"))

    def test_failed_request_is_traced_with_error_finish_reason(self):
        self.client.chat_stream_assembled.side_effect = RuntimeError("bağlantı koptu")
        result = self.session.run_step("görev")
        self.assertTrue(result.startswith("Hata:"))
        records = self.records()
        request = next(r for r in records if r["type"] == "request")
        self.assertEqual(request["finish_reason"], "error")
        self.assertEqual(records[-1]["outcome"], "error")
        self.assertEqual((records[-1]["steps"], records[-1]["requests"]), (0, 1))

    def test_each_task_gets_its_own_id(self):
        self._run([reply("bir")])
        self._run([reply("iki")])
        starts = [r for r in self.records() if r["type"] == "task_start"]
        self.assertEqual(len(starts), 2)
        self.assertNotEqual(starts[0]["task_id"], starts[1]["task_id"])

    def test_secrets_never_reach_the_trace_file(self):
        task = f'curl -H "Authorization: Bearer {SECRET}" https://api.example.test/x ile durumu oku'
        command = f'curl -H "Authorization: Bearer {SECRET}" https://api.example.test/x'
        replies = [reply(tools=[call("run_command", {"command": command}, "a")]), reply("bitti")]
        self._run(replies, task=task)
        raw = trace_path(self.root).read_text(encoding="utf-8")
        self.assertNotIn(SECRET, raw)
        preview = self.records()[0]["text_preview"]
        # The vault swapped the token for a placeholder before the task text reached the trace.
        self.assertTrue("[REDACTED]" in preview or "EVREN_SECRET" in preview, preview)

    def test_trace_off_writes_nothing(self):
        for value in ("false", "0", "off"):
            with self.subTest(value=value), patch.dict(os.environ, {"EVREN_TRACE": value}):
                self._run([reply("tamam")])
                self.assertFalse(trace_path(self.root).exists())
                self.assertFalse(trace_path(self.root).parent.exists())

    def test_unwritable_log_directory_never_breaks_the_task(self):
        (self.root / ".evren").write_text("bu bir dosya, klasör değil", encoding="utf-8")
        replies = [reply(tools=[call("run_command", {"command": GREP}, "a")]), reply("bitti")]
        result, execute = self._run(replies)
        self.assertEqual(result, "bitti")
        self.assertEqual(execute.call_count, 1)


class TaskTraceUnitTest(TraceTestBase):
    def test_regex_redactor_masks_unknown_bearer_tokens_and_passwords(self):
        trace = TaskTrace(self.root)
        trace.start(model="m", mode="normal", text=f"password={SECRET} ile giriş yap")
        trace.tool("run_command", {"command": f"curl -H 'Authorization: Bearer {SECRET}' https://x.test"})
        raw = trace_path(self.root).read_text(encoding="utf-8")
        self.assertNotIn(SECRET, raw)
        start, tool = self.records()
        self.assertIn("[REDACTED]", start["text_preview"])
        self.assertIn("Bearer [REDACTED]", tool["args"])

    def test_active_vault_values_are_masked_everywhere(self):
        vault = SecretVault()
        vault.capture("password=hunter2plainvalue")
        set_active_vault(vault)
        trace = TaskTrace(self.root)
        trace.start(model="m", mode="normal", text="echo hunter2plainvalue")
        trace.tool("run_command", {"command": "echo hunter2plainvalue", "note": "hunter2plainvalue"})
        raw = trace_path(self.root).read_text(encoding="utf-8")
        self.assertNotIn("hunter2plainvalue", raw)
        self.assertIn("{{EVREN_SECRET_1}}", raw)

    def test_text_fields_are_capped_and_single_line(self):
        trace = TaskTrace(self.root)
        trace.start(model="m", mode="normal", text="satır\n" * 100)
        trace.tool("write_file", {"path": "a.txt", "content": "x" * 5000, "edits": [1, 2, 3]})
        start, tool = self.records()
        self.assertLessEqual(len(start["text_preview"]), 120)
        self.assertNotIn("\n", start["text_preview"])
        self.assertLessEqual(len(tool["args"]), 160)
        self.assertTrue(tool["args"].startswith("path=a.txt"))
        self.assertEqual(tool["result_chars"], 0)

    def test_result_text_is_never_stored(self):
        trace = TaskTrace(self.root)
        trace.tool("view_file", {"path": "a.py"}, result="GIZLI-ICERIK " * 10)
        raw = trace_path(self.root).read_text(encoding="utf-8")
        self.assertNotIn("GIZLI-ICERIK", raw)
        self.assertEqual(self.records()[0]["result_chars"], len("GIZLI-ICERIK " * 10))

    def test_oversized_log_is_rotated_to_dot_one_when_a_task_starts(self):
        path = trace_path(self.root)
        path.parent.mkdir(parents=True)
        path.write_bytes(b"x" * (MAX_BYTES + 1))
        archive = path.with_name(ARCHIVE_FILE_NAME)
        archive.write_text("eski arşiv", encoding="utf-8")

        trace = TaskTrace(self.root)
        trace.start(model="m", mode="normal", text="yeni görev")
        trace.end("tamam")

        self.assertEqual(archive.stat().st_size, MAX_BYTES + 1)   # the old archive was replaced
        self.assertEqual([r["type"] for r in self.records()], ["task_start", "task_end"])

    def test_small_log_is_appended_not_rotated(self):
        for _ in range(2):
            trace = TaskTrace(self.root)
            trace.start(model="m", mode="normal", text="görev")
            trace.end("tamam")
        self.assertEqual(len(self.records()), 4)
        self.assertFalse(trace_path(self.root).with_name(ARCHIVE_FILE_NAME).exists())

    def test_end_is_written_once(self):
        trace = TaskTrace(self.root)
        trace.start(model="m", mode="normal", text="görev")
        trace.end("tamam")
        trace.end("tamam")
        self.assertEqual([r["type"] for r in self.records()].count("task_end"), 1)

    def test_write_errors_are_swallowed(self):
        trace = TaskTrace(self.root, enabled=True)
        with patch.object(Path, "open", side_effect=PermissionError("kilitli")):
            trace.start(model="m", mode="normal", text="görev")
            trace.request(None, ms=1)
            trace.tool("run_command", {"command": "x"}, result="ok")
            trace.end("tamam")

    def test_outcome_classification(self):
        self.assertEqual(classify_outcome(None), "exception")
        self.assertEqual(classify_outcome("İPTAL: Kullanıcı Ctrl+C ile durdurdu."), "interrupted")
        self.assertEqual(classify_outcome("HATA: Ajan maksimum adım sınırına (30) ulaştı"), "max_steps")
        self.assertEqual(classify_outcome("HATA: Model art arda boş yanıt verdi"), "error")
        self.assertEqual(classify_outcome("Hata: bağlantı"), "error")
        self.assertEqual(classify_outcome("Tüm testler geçti."), "ok")


class TraceSwitchTest(unittest.TestCase):
    def test_default_is_on_and_only_explicit_off_values_disable(self):
        with patch.dict(os.environ):
            os.environ.pop("EVREN_TRACE", None)
            self.assertTrue(trace_enabled())
            for value in ("false", "FALSE", "0", "off", "Off", "no"):
                os.environ["EVREN_TRACE"] = value
                self.assertFalse(trace_enabled(), value)
            for value in ("true", "1", "on", "evet", ""):
                os.environ["EVREN_TRACE"] = value
                self.assertTrue(trace_enabled(), value)


def _write_trace(root: Path, lines: list[dict | str]) -> None:
    path = trace_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = [line if isinstance(line, str) else json.dumps(line, ensure_ascii=False) for line in lines]
    path.write_text("\n".join(body) + "\n", encoding="utf-8")


def _tool(task, step, args, ms, chars, *, blocked=False, ok=True, name="run_command", sig="s1"):
    return {"type": "tool", "task_id": task, "step": step, "tool": name, "args": args, "sig": sig,
            "ms": ms, "result_chars": chars, "blocked": blocked, "ok": ok}


def _request(task, step, prompt, cached, completion, ms, finish="tool_calls"):
    return {"type": "request", "task_id": task, "step": step, "model": "qwen3.8-flash-next",
            "prompt_tokens": prompt, "cached_tokens": cached, "completion_tokens": completion,
            "ms": ms, "finish_reason": finish}


SYNTHETIC = [
    {"type": "task_start", "task_id": "eski0001", "ts": "2026-10-16T09:00:00Z", "model": "m",
     "mode": "normal", "text_preview": "eski görev"},
    _request("eski0001", 1, 500, 0, 20, 900, "stop"),
    {"type": "task_end", "task_id": "eski0001", "steps": 1, "requests": 1, "ms": 1000, "outcome": "ok"},
    "{ bozuk satır",
    {"type": "task_start", "task_id": "son00002", "ts": "2026-10-17T10:00:00Z", "model": "qwen3.8-flash-next",
     "mode": "normal", "text_preview": "Authorization: Bearer [REDACTED] ile sürümü yaz"},
    _request("son00002", 1, 1000, 0, 50, 1500),
    _tool("son00002", 1, "git grep Worker", 120, 4096),
    _request("son00002", 2, 2000, 1500, 40, 1800),
    _tool("son00002", 2, "git grep Worker", 110, 4096),
    _request("son00002", 3, 3000, 2400, 30, 1700),
    _tool("son00002", 3, "git grep Worker", 0, 300, blocked=True, ok=False),
    _request("son00002", 4, None, None, None, 2000, "stop"),
    {"type": "task_end", "task_id": "son00002", "steps": 4, "requests": 4, "ms": 41200, "outcome": "ok"},
]


class DebugLastTest(TraceTestBase):
    def test_summary_numbers(self):
        _write_trace(self.root, SYNTHETIC)
        summary = summarize_task(next(t for t in trace_mod.read_tasks(self.root) if t[0]["task_id"] == "son00002"))
        self.assertEqual(summary["requests"], 4)
        self.assertEqual(summary["prompt_tokens"], 6000)
        self.assertEqual(summary["cache_pct"], 65)         # 3900 cached of 6000 (reporting requests)
        self.assertEqual(summary["repeated"], 2)           # 2nd and 3rd identical call
        self.assertEqual(summary["blocked"], 1)
        self.assertEqual(summary["total_ms"], 41200)
        self.assertTrue(summary["finished"])

    def test_renders_the_last_task_as_a_table(self):
        _write_trace(self.root, SYNTHETIC)
        buffer = io.StringIO()
        self.assertTrue(print_last_task(self.root, 1, console=wide_console(buffer)))
        out = buffer.getvalue()
        self.assertIn("Görev son00002", out)
        self.assertIn("run_command", out)
        self.assertIn("git grep Worker", out)
        self.assertIn("ENGELLENDİ", out)
        self.assertIn("istek", out)
        self.assertIn("Bearer [REDACTED]", out)             # rich markup must not eat the brackets
        self.assertIn("4,096", out)
        self.assertIn(
            "Özet: 4 istek • prompt 6,000 token • önbellek %65 • 2 tekrarlanan çağrı • "
            "1 engellenen çağrı • toplam 41.2 sn • sonuç: tamam",
            out,
        )
        self.assertNotIn("eski0001", out)

    def test_task_option_selects_an_earlier_task(self):
        _write_trace(self.root, SYNTHETIC)
        buffer = io.StringIO()
        self.assertTrue(print_last_task(self.root, 2, console=wide_console(buffer)))
        out = buffer.getvalue()
        self.assertIn("Görev eski0001", out)
        self.assertNotIn("son00002", out)

    def test_out_of_range_and_invalid_task_numbers_explain_themselves(self):
        _write_trace(self.root, SYNTHETIC)
        for index, expected in ((3, "Yalnızca 2 görev izi var"), (0, "1 veya daha büyük")):
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                self.assertFalse(print_last_task(self.root, index))
            self.assertIn(expected, " ".join(buffer.getvalue().split()))

    def test_missing_trace_file_prints_a_turkish_hint(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            self.assertFalse(print_last_task(self.root))
        self.assertIn("Henüz görev izi yok", " ".join(buffer.getvalue().split()))

    def test_unfinished_task_is_reported_as_such(self):
        _write_trace(self.root, SYNTHETIC[:7])              # no task_end for the last task
        buffer = io.StringIO()
        self.assertTrue(print_last_task(self.root, 1, console=wide_console(buffer)))
        self.assertIn("tamamlanmadı", " ".join(buffer.getvalue().split()))

    def test_rows_follow_trace_order(self):
        _write_trace(self.root, SYNTHETIC)
        last = trace_mod.read_tasks(self.root)[-1]
        kinds = [row[1] for row in table_rows(last)]
        self.assertEqual(kinds, ["istek", "run_command", "istek", "run_command", "istek", "run_command", "istek"])

    def test_request_rows_name_the_model_only_when_it_differs_from_the_task_model(self):
        _write_trace(self.root, SYNTHETIC)
        older, last = trace_mod.read_tasks(self.root)
        self.assertNotIn("qwen3.8-flash-next", table_rows(last)[0][2])
        self.assertIn("qwen3.8-flash-next", table_rows(older)[0][2])   # task model is "m"

    def test_archive_is_read_when_the_active_file_has_too_few_tasks(self):
        _write_trace(self.root, SYNTHETIC[3:])
        archive = trace_path(self.root).with_name(ARCHIVE_FILE_NAME)
        archive.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in SYNTHETIC[:3]) + "\n", encoding="utf-8",
        )
        buffer = io.StringIO()
        self.assertTrue(print_last_task(self.root, 2, console=wide_console(buffer)))
        self.assertIn("Görev eski0001", buffer.getvalue())

    def test_real_run_can_be_rendered(self):
        client = MagicMock(default_model="m")
        session = AgentSession(client, workspace_root=self.root)
        replies = [
            reply(tools=[call("run_command", {"command": GREP}, "a")], usage=usage(900, 0, 30)),
            reply(tools=[call("run_command", {"command": GREP}, "b")], usage=usage(1000, 700, 30)),
            reply("bitti", usage=usage(1100, 900, 20)),
        ]
        client.chat_stream_assembled.side_effect = lambda **_kw: replies.pop(0)
        with patch("src.agent.execute_tool_call", return_value=GREP_OUTPUT), \
                contextlib.redirect_stdout(io.StringIO()):
            session.run_step("kalan işi yap")
        buffer = io.StringIO()
        self.assertTrue(print_last_task(self.root, 1, console=wide_console(buffer)))
        out = " ".join(buffer.getvalue().split())
        self.assertIn("ENGELLENDİ", out)
        self.assertIn("Özet: 3 istek • prompt 3,000 token", out)
        self.assertIn("1 engellenen çağrı", out)


class DebugCommandWiringTest(TraceTestBase):
    def test_cli_debug_last_prints_the_table(self):
        _write_trace(self.root, SYNTHETIC)
        buffer = io.StringIO()
        from src import cli

        with patch.object(sys, "argv", ["evren", "--workspace", str(self.root), "debug", "last"]), \
                patch("src.ui.console", wide_console(buffer)):
            cli.main()
        self.assertIn("Görev son00002", buffer.getvalue())

    def test_cli_debug_last_task_option_reaches_the_command(self):
        from src import cli

        with patch.object(sys, "argv", ["evren", "--workspace", str(self.root), "debug", "last", "--task", "3"]), \
                patch("src.cli.cmd_debug") as command:
            cli.main()
        args = command.call_args.args[0]
        self.assertEqual((args.task, args.workspace), (3, str(self.root)))

    def test_cli_debug_without_action_defaults_to_last(self):
        from src import cli

        with patch.object(sys, "argv", ["evren", "debug"]), patch("src.cli.cmd_debug") as command:
            cli.main()
        args = command.call_args.args[0]
        self.assertEqual((args.debug_action, args.task), ("last", 1))

    def test_repl_debug_command_argument_parsing(self):
        from src.repl import _handle_debug_command

        for arg, expected in (("", 1), ("last", 1), ("3", 3), ("last 2", 2)):
            with self.subTest(arg=arg), patch("src.trace.print_last_task") as show:
                _handle_debug_command(arg, self.root)
            show.assert_called_once_with(self.root, expected)

        with patch("src.trace.print_last_task") as show, patch("src.repl.print_warning") as warn:
            _handle_debug_command("abc", self.root)
        show.assert_not_called()
        warn.assert_called_once()

    def test_repl_help_lists_the_command(self):
        from src.repl import HELP_TEXT

        self.assertIn("/debug", HELP_TEXT)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
