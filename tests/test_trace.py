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
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from rich.console import Console

from src.agent import AgentSession
from src.config import trace_enabled
from src.secret_vault import SecretVault, set_active_vault
from src.trace import (
    ARCHIVE_FILE_NAME,
    MAX_BYTES,
    TaskTrace,
    print_last_task,
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

        self.assertEqual([req1["step"], req2["step"], req3["step"]], [1, 2, 3])
        self.assertEqual(
            (req1["prompt_tokens"], req1["cached_tokens"], req1["completion_tokens"]), (1000, 800, 50),
        )
        self.assertEqual((req1["finish_reason"], req3["finish_reason"]), ("tool_calls", "stop"))

        self.assertEqual((tool1["tool"], tool1["step"]), ("run_command", 1))
        self.assertIn("git -C portal-backend grep", tool1["args"])
        self.assertLessEqual(len(tool1["args"]), 160)
        self.assertFalse(tool1["blocked"])
        self.assertTrue(tool1["ok"])
        self.assertEqual(tool1["result_chars"], len(GREP_OUTPUT))

        self.assertTrue(tool2["blocked"])            # the repeat guard refused the second call
        self.assertFalse(tool2["ok"])
        self.assertEqual(tool2["sig"], tool1["sig"])  # same call, so it counts as a repeat

        self.assertEqual((end["steps"], end["requests"], end["outcome"]), (3, 3, "ok"))

    def test_secrets_never_reach_the_trace_file(self):
        cases = {
            "agent task and tool args": (
                f'curl -H "Authorization: Bearer {SECRET}" https://api.example.test/x ile durumu oku',
                f'curl -H "Authorization: Bearer {SECRET}" https://api.example.test/x',
            ),
            "regex redactor (unknown token)": (
                f"password={SECRET} ile giriş yap",
                f"curl -H 'Authorization: Bearer {SECRET}' https://x.test",
            ),
            "active vault values": (
                "echo hunter2plainvalue",
                "echo hunter2plainvalue",
            ),
        }
        for label, (task, command) in cases.items():
            with self.subTest(case=label):
                if label.startswith("active vault"):
                    vault = SecretVault()
                    vault.capture("password=hunter2plainvalue")
                    set_active_vault(vault)
                    trace = TaskTrace(self.root)
                    trace.start(model="m", mode="normal", text=task)
                    trace.tool("run_command", {"command": command, "note": "hunter2plainvalue"})
                else:
                    replies = [reply(tools=[call("run_command", {"command": command}, "a")]), reply("bitti")]
                    self._run(replies, task=task)
                raw = trace_path(self.root).read_text(encoding="utf-8")
                self.assertNotIn(SECRET, raw)
                self.assertNotIn("hunter2plainvalue", raw)
                # The vault/regex redactor swapped the secret for a placeholder before it reached the trace.
                self.assertTrue("[REDACTED]" in raw or "{{EVREN_SECRET" in raw, raw)

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

    def test_renders_the_last_task_as_a_table(self):
        _write_trace(self.root, SYNTHETIC)
        buffer = io.StringIO()
        self.assertTrue(print_last_task(self.root, 1, console=wide_console(buffer)))
        out = buffer.getvalue()
        self.assertIn("Görev son00002", out)
        self.assertIn("run_command", out)
        self.assertIn("git grep Worker", out)
        self.assertIn("ENGELLENDİ", out)
        self.assertIn("Bearer [REDACTED]", out)             # rich markup must not eat the brackets
        self.assertIn("4,096", out)
        self.assertIn("toplam 41.2 sn", out)
        self.assertIn("sonuç: tamam", out)
        self.assertNotIn("eski0001", out)                   # broken line skipped, only last task rendered

    def test_missing_trace_file_prints_a_turkish_hint(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            self.assertFalse(print_last_task(self.root))
        self.assertIn("Henüz görev izi yok", " ".join(buffer.getvalue().split()))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
