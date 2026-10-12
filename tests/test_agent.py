import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.agent import AgentSession, SYSTEM_PROMPT
from src.errors import NetworkError
from src.config import execution_context_block
from src.modes import AgentMode
from src.prompt_frame import PromptFrame
from src.tools import TOOLS_SCHEMA, tool_run_command


def response(content="", finish_reason="stop", tools=()):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            finish_reason=finish_reason,
            message=SimpleNamespace(content=content, tool_calls=list(tools)),
        )],
        usage=None,
    )


def tool_call(arguments, name="run_command"):
    return SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


class TestAgentLoop(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.client = MagicMock(default_model="test-model")
        self.session = AgentSession(self.client, workspace_root=Path(self.tmp.name))
        for name in ("print_info", "print_warning", "print_error", "print_markdown",
                     "print_tool_call", "print_tool_result"):
            patcher = patch(f"src.agent.{name}")
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_truncated_response_recovers_with_small_step_instruction(self):
        requests = []

        def reply(**kwargs):
            requests.append(deepcopy(kwargs["messages"]))
            if len(requests) == 1:
                return response("unfinished", "length")
            return response("verified")

        self.client.chat_stream_assembled.side_effect = reply
        self.assertEqual(self.session.run_step("bu hostta testleri çalıştır"), "verified")
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[1][0]["role"], "system")
        self.assertIn("küçük", requests[1][0]["content"])
        self.assertTrue(all(message["role"] != "system" for message in requests[1][1:]))
        self.assertIn("unfinished", str(requests[1]))
        self.assertIn("kaldığın yerden", str(requests[1]).casefold())

    def test_repeated_reasoning_only_responses_stop_explicitly(self):
        self.client.chat_stream_assembled.return_value = response("", "length")
        result = self.session.run_step("uygula")
        self.assertTrue(result.startswith("HATA:"))
        self.assertEqual(self.client.chat_stream_assembled.call_count, 4)

    def test_ctrl_c_keeps_goal_and_closes_open_tool_call(self):
        self.client.chat_stream_assembled.return_value = response(
            "", "tool_calls", [tool_call(json.dumps({"command": "echo local"}))],
        )
        with patch("src.agent.execute_tool_call", side_effect=KeyboardInterrupt):
            result = self.session.run_step("deploy the service")
        self.assertIn("Geçmiş", result)
        self.assertEqual(self.session.task_goal, "deploy the service")
        self.assertTrue(self.session.interrupted)
        self.assertEqual(self.session.pending_mode, AgentMode.NORMAL)
        self.assertIn("deploy the service", self.session.messages[1]["content"])
        cancelled = [m for m in self.session.messages if m["role"] == "tool"]
        self.assertTrue(cancelled)
        self.assertIn("Ctrl+C", cancelled[-1]["content"])

    def test_correction_after_ctrl_c_keeps_original_goal(self):
        self.client.chat_stream_assembled.side_effect = KeyboardInterrupt
        self.session.run_step("deploy the service")
        history = deepcopy(self.session.messages)
        self.client.chat_stream_assembled.side_effect = None
        self.client.chat_stream_assembled.return_value = response("done")
        self.assertEqual(self.session.execute_followup("portu 8080 yap"), "done")
        self.assertEqual(self.session.task_goal, "deploy the service")
        self.assertFalse(self.session.interrupted)
        self.assertEqual(self.session.messages[:len(history)], history)
        request = self.client.chat_stream_assembled.call_args.kwargs["messages"]
        self.assertIn("portu 8080 yap", request[-1]["content"])
        self.assertIn("deploy the service", request[-1]["content"])

    def test_connection_error_retries_the_step(self):
        self.client.chat_stream_assembled.side_effect = [
            NetworkError("Connection error."),
            response("done"),
        ]
        self.assertEqual(self.session.run_step("incele"), "done")
        self.assertEqual(self.client.chat_stream_assembled.call_count, 2)

    def test_repeated_connection_errors_stop_explicitly(self):
        self.client.chat_stream_assembled.side_effect = NetworkError("Connection error.")
        result = self.session.run_step("incele")
        self.assertTrue(result.startswith("Hata:"))
        self.assertIn("Connection error", result)
        self.assertEqual(self.client.chat_stream_assembled.call_count, 4)

    def test_empty_stall_retries_with_a_larger_budget(self):
        budgets = []

        def reply(**kwargs):
            budgets.append(kwargs["max_tokens"])
            if len(budgets) == 1:
                return response("", "length")
            return response("done")

        self.client.chat_stream_assembled.side_effect = reply
        self.assertEqual(self.session.run_step("incele"), "done")
        self.assertGreater(budgets[1], budgets[0])

    def test_complete_tool_call_survives_length_limit(self):
        self.client.chat_stream_assembled.side_effect = [
            response("", "length", [tool_call(json.dumps({"command": "echo local"}))]),
            response("done"),
        ]
        with patch("src.agent.execute_tool_call", return_value="Çıkış Kodu: 0") as execute:
            self.assertEqual(self.session.run_step("uygula"), "done")
        execute.assert_called_once()

    def test_empty_response_gets_one_recovery_attempt(self):
        self.client.chat_stream_assembled.side_effect = [response(), response("done")]
        self.assertEqual(self.session.run_step("incele"), "done")
        self.assertEqual(self.client.chat_stream_assembled.call_count, 2)

    def test_truncated_tool_call_is_never_executed(self):
        self.client.chat_stream_assembled.side_effect = [
            response("", "length", [tool_call('{"command": "echo')]),
            response("done"),
        ]
        with patch("src.agent.execute_tool_call") as execute:
            self.assertEqual(self.session.run_step("uygula"), "done")
        execute.assert_not_called()
        self.assertFalse(any(m.get("tool_calls") for m in self.session.messages))

    def test_malformed_arguments_are_reported_not_executed(self):
        for arguments in ('{"command":', '[]'):
            with self.subTest(arguments=arguments):
                self.session.clear_history()
                self.client.chat_stream_assembled.side_effect = [
                    response("", "tool_calls", [tool_call(arguments)]),
                    response("done"),
                ]
                with patch("src.agent.execute_tool_call") as execute:
                    self.session.run_step("uygula")
                execute.assert_not_called()
                errors = [m["content"] for m in self.session.messages if m["role"] == "tool"]
                self.assertTrue(errors[0].startswith("HATA:"))

    def test_normal_tool_then_final_answer_preserves_behavior(self):
        self.client.chat_stream_assembled.side_effect = [
            response("", "tool_calls", [tool_call(json.dumps({"command": "echo local"}))]),
            response("done"),
        ]
        with patch("src.agent.execute_tool_call", return_value="Çıkış Kodu: 0") as execute:
            self.assertEqual(self.session.run_step("bu hosts üzerinde kontrol et"), "done")
        execute.assert_called_once_with(
            name="run_command", arguments={"command": "echo local"},
            workspace_root=self.session.workspace_root, auto_approve=self.session.auto_approve,
        )

    def test_explicit_remote_tool_is_still_available(self):
        self.client.chat_stream_assembled.side_effect = [
            response("", "tool_calls", [
                tool_call('{"alias": "prod-web", "command": "hostname"}', "ssh_run"),
            ]),
            response("done"),
        ]
        with patch("src.agent.execute_tool_call", return_value="remote-host") as execute:
            self.assertEqual(self.session.run_step("prod-web üzerinde SSH ile kontrol et"), "done")
        self.assertEqual(execute.call_args.kwargs["name"], "ssh_run")
        self.assertEqual(execute.call_args.kwargs["arguments"]["alias"], "prod-web")

    def test_ask_mode_still_blocks_writes(self):
        self.session.set_mode(AgentMode.ASK)
        self.client.chat_stream_assembled.side_effect = [
            response("", "tool_calls", [
                tool_call('{"path": "a.py", "content": "x"}', "write_file"),
            ]),
            response("done"),
        ]
        with patch("src.agent.execute_tool_call") as execute:
            self.session.run_step("incele")
        execute.assert_not_called()
        errors = [m["content"] for m in self.session.messages if m["role"] == "tool"]
        self.assertTrue(errors[0].startswith("REDDEDİLDİ:"))

    def test_recovery_counter_resets_after_tool_progress(self):
        self.client.chat_stream_assembled.side_effect = [
            response("", "length"),
            response("", "tool_calls", [tool_call('{"command": "echo local"}')]),
            response("", "length"),
            response("done"),
        ]
        with patch("src.agent.execute_tool_call", return_value="Çıkış Kodu: 0"):
            self.assertEqual(self.session.run_step("uygula"), "done")
        self.assertEqual(self.client.chat_stream_assembled.call_count, 4)

    def test_request_uses_resolved_output_budget(self):
        self.client.chat_stream_assembled.return_value = response("done")
        with patch.object(self.session.token_manager, "resolve_output_budget", return_value=8192):
            self.session.run_step("incele")
        self.assertEqual(self.client.chat_stream_assembled.call_args.kwargs["max_tokens"], 8192)

    def test_invalid_step_limit_does_not_call_model(self):
        with self.assertRaises(ValueError):
            self.session.run_step("incele", max_steps=0)
        self.client.chat_stream_assembled.assert_not_called()

    def test_step_limit_returns_incomplete_status(self):
        self.client.chat_stream_assembled.return_value = response(
            "", "tool_calls", [tool_call('{"command": "echo local"}')],
        )
        with patch("src.agent.execute_tool_call", return_value="Çıkış Kodu: 0"):
            result = self.session.run_step("uygula", max_steps=1)
        self.assertTrue(result.startswith("HATA:"))

    def test_continue_preserves_tool_history_and_finishes(self):
        self.client.chat_stream_assembled.return_value = response(
            "", "tool_calls", [tool_call('{"command": "echo local"}')],
        )
        with patch("src.agent.execute_tool_call", return_value="Çıkış Kodu: 0"):
            self.session.run_step("uygula", max_steps=1)
        history = deepcopy(self.session.messages)
        self.client.chat_stream_assembled.return_value = response("done")
        self.assertEqual(self.session.continue_task(max_steps=1), "done")
        self.assertEqual(self.session.messages[:len(history)], history)
        self.assertIsNone(self.session.pending_mode)

    def test_continue_without_pending_task_does_not_call_model(self):
        self.assertTrue(self.session.continue_task().startswith("HATA:"))
        self.client.chat_stream_assembled.assert_not_called()

    def test_execute_followup_uses_completed_turn_history_and_original_goal(self):
        self.client.chat_stream_assembled.return_value = response("Discovery done; approval required")
        self.session.run_step("configure databases")
        history = deepcopy(self.session.messages)
        self.client.chat_stream_assembled.return_value = response("done")
        self.assertEqual(self.session.execute_followup("hepsine onay veriyorum"), "done")
        self.assertEqual(self.session.messages[:len(history)], history)
        self.assertEqual(self.session.task_goal, "configure databases")
        request = self.client.chat_stream_assembled.call_args.kwargs["messages"]
        self.assertEqual(request[0]["role"], "system")
        self.assertTrue(all(message["role"] != "system" for message in request[1:]))
        self.assertEqual(request[-1]["role"], "user")
        self.assertIn("hepsine onay veriyorum", request[-1]["content"])
        self.assertIn("configure databases", request[-1]["content"])

    def test_execute_without_prior_task_never_calls_model(self):
        self.assertTrue(self.session.execute_followup("yap artık").startswith("HATA:"))
        self.client.chat_stream_assembled.assert_not_called()

    def test_execute_followup_does_not_override_read_only_mode(self):
        self.client.chat_stream_assembled.return_value = response(
            "", "tool_calls", [tool_call('{"command":"hostname"}')],
        )
        with patch("src.agent.execute_tool_call", return_value="local"):
            self.session.run_step("ask: inspect", max_steps=1)
        self.client.chat_stream_assembled.return_value = response(
            "", "tool_calls", [tool_call('{"path":"a.py","content":"x"}', "write_file")],
        )
        approval = self.session.auto_approve
        with patch("src.agent.execute_tool_call") as execute:
            self.session.execute_followup("hepsine onay veriyorum", max_steps=1)
        execute.assert_not_called()
        self.assertEqual(self.session.pending_mode, AgentMode.ASK)
        self.assertEqual(self.session.auto_approve, approval)
        self.assertEqual(self.session.mode, AgentMode.NORMAL)

    def test_continue_retains_read_only_turn_mode_and_can_pause_again(self):
        self.client.chat_stream_assembled.return_value = response(
            "", "tool_calls", [tool_call('{"command": "hostname"}')],
        )
        with patch("src.agent.execute_tool_call", return_value="local"):
            self.session.run_step("ask: incele", max_steps=1)
        self.client.chat_stream_assembled.return_value = response(
            "", "tool_calls", [tool_call('{"path":"a.py","content":"x"}', "write_file")],
        )
        with patch("src.agent.execute_tool_call") as execute:
            self.session.continue_task(max_steps=1)
        execute.assert_not_called()
        self.assertEqual(self.session.pending_mode, AgentMode.ASK)
        self.assertEqual(self.session.mode, AgentMode.NORMAL)
        self.session.clear_history()
        self.assertIsNone(self.session.pending_mode)

    def test_final_answer_on_last_step_is_not_reported_as_limit(self):
        self.client.chat_stream_assembled.return_value = response("done")
        with patch("src.agent.print_warning") as warning:
            self.assertEqual(self.session.run_step("incele", max_steps=1), "done")
        warning.assert_not_called()

    def test_content_filter_is_not_retried_or_executed(self):
        self.client.chat_stream_assembled.return_value = response(
            "", "content_filter", [tool_call('{"command": "echo local"}')],
        )
        with patch("src.agent.execute_tool_call") as execute:
            result = self.session.run_step("uygula")
        self.assertTrue(result.startswith("HATA:"))
        self.assertEqual(self.client.chat_stream_assembled.call_count, 1)
        execute.assert_not_called()


class TestExecutionContext(unittest.TestCase):
    def test_default_prompt_defines_local_aliases_and_small_steps(self):
        for phrase in ("bu host", "bu hosts", "bu makine", "localhost", "127.0.0.1", "::1"):
            with self.subTest(phrase=phrase):
                self.assertIn(f'"{phrase}"', SYSTEM_PROMPT)
        self.assertIn("3-7", SYSTEM_PROMPT)
        self.assertIn("Her model turunda yalnızca sıradaki küçük alt adımı", SYSTEM_PROMPT)
        self.assertNotIn("Her turda mümkün olduğunca ÇOK iş bitir", SYSTEM_PROMPT)

    def test_context_describes_actual_shell_and_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            for system, os_name, shell in (
                ("Windows", "nt", r"C:\Windows\System32\cmd.exe"),
                ("Linux", "posix", "/bin/sh"),
            ):
                with self.subTest(system=system):
                    with patch("src.config.platform.system", return_value=system), \
                         patch("src.config.os", SimpleNamespace(
                             name=os_name, environ={"COMSPEC": r"C:\Windows\System32\cmd.exe"},
                         )):
                        context = execution_context_block(Path(directory))
                    self.assertIn(str(Path(directory).resolve()), context)
                    self.assertIn(shell, context)
                    self.assertIn(f"İşletim sistemi: {system}", context)
                    self.assertIn("SSH bağlantısı kurmaz", context)

    def test_context_survives_frame_refresh_and_clear(self):
        with tempfile.TemporaryDirectory() as directory:
            session = AgentSession(MagicMock(default_model="test-model"), Path(directory))
            session.apply_frame(PromptFrame(rol="senior fullstack developer"))
            session.refresh_context()
            session.clear_history()
            context = session.messages[0]["content"]
            self.assertEqual(context.count("YEREL ÇALIŞTIRMA BAĞLAMI\n"), 1)
            self.assertIn(str(Path(directory).resolve()), context)

    def test_tool_descriptions_distinguish_local_and_remote(self):
        descriptions = {
            entry["function"]["name"]: entry["function"]["description"]
            for entry in TOOLS_SCHEMA
        }
        self.assertIn("YEREL", descriptions["run_command"])
        for name in ("ssh_list_hosts", "ssh_run", "ssh_read_file", "ssh_write_file"):
            with self.subTest(name=name):
                self.assertIn("UZAK", descriptions[name])

    def test_local_command_executor_uses_workspace_cwd(self):
        def stream(*_args, **_kwargs):
            yield "stdout", "local"
            return 0, "local", "", False

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("src.tools.stream_with_tree_kill", side_effect=stream) as execute, \
                 patch("src.tools.print_warning"), patch("src.tools.print_command_output"):
                result = tool_run_command("echo local", workspace_root=root, auto_approve=True)
            self.assertIn("Çıkış Kodu: 0", result)
            execute.assert_called_once_with("echo local", cwd=str(root), timeout=180, shell=True)


if __name__ == "__main__":
    unittest.main()
