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

    def test_truncated_response_recovers_and_keeps_system_prompt_stable(self):
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
        # The system message must stay identical between steps (prompt cache).
        self.assertEqual(requests[1][0]["content"], requests[0][0]["content"])
        self.assertTrue(all(message["role"] != "system" for message in requests[1][1:]))
        self.assertIn("unfinished", str(requests[1]))
        self.assertIn("kaldığın yerden", str(requests[1]).casefold())

    def test_unproductive_loops_stop_explicitly(self):
        cases = (
            (response("", "length"), "HATA:"),
            (NetworkError("Connection error."), "Hata:"),
        )
        for index, (reply, prefix) in enumerate(cases):
            with self.subTest(case=index):
                # A fresh session per case: recovery counters are per task.
                client = MagicMock(default_model="test-model")
                session = AgentSession(client, workspace_root=Path(self.tmp.name))
                if isinstance(reply, Exception):
                    client.chat_stream_assembled.side_effect = reply
                else:
                    client.chat_stream_assembled.return_value = reply
                result = session.run_step("uygula")
                self.assertTrue(result.startswith(prefix))
                self.assertEqual(client.chat_stream_assembled.call_count, 4)
                if prefix == "Hata:":
                    self.assertIn("Connection error", result)

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

    def test_transient_response_states_recover(self):
        cases = (
            ([NetworkError("Connection error."), response("done")],),
            ([response(), response("done")],),
            ([response("", "length"), response("done")],),
        )
        for index, (replies,) in enumerate(cases):
            with self.subTest(case=index):
                client = MagicMock(default_model="test-model")
                session = AgentSession(client, workspace_root=Path(self.tmp.name))
                budgets = []

                def reply(**kwargs):
                    budgets.append(kwargs["max_tokens"])
                    # side_effect list semantics: exceptions must be raised.
                    item = replies[len(budgets) - 1]
                    if isinstance(item, Exception):
                        raise item
                    return item

                client.chat_stream_assembled.side_effect = reply
                self.assertEqual(session.run_step("incele"), "done")
                if index == 2:
                    self.assertGreater(budgets[1], budgets[0])

    def test_tool_call_truncation_and_malformed_arguments_are_not_executed(self):
        cases = {
            "truncated": ("length", '{"command": "echo'),
            "malformed_open": ("tool_calls", '{"command":'),
            "malformed_list": ("tool_calls", "[]"),
        }
        for name, (finish, arguments) in cases.items():
            with self.subTest(case=name):
                client = MagicMock(default_model="test-model")
                session = AgentSession(client, workspace_root=Path(self.tmp.name))
                client.chat_stream_assembled.side_effect = [
                    response("", finish, [tool_call(arguments)]),
                    response("done"),
                ]
                with patch("src.agent.execute_tool_call") as execute:
                    self.assertEqual(session.run_step("uygula"), "done")
                execute.assert_not_called()
                if finish == "tool_calls":
                    errors = [m["content"] for m in session.messages if m["role"] == "tool"]
                    self.assertTrue(errors[0].startswith("HATA:"))
                else:
                    self.assertFalse(any(m.get("tool_calls") for m in session.messages))

    def test_tool_round_trip_local_and_remote(self):
        cases = {
            "local": (tool_call(json.dumps({"command": "echo local"})), "run_command"),
            "remote": (tool_call('{"alias": "prod-web", "command": "hostname"}', "ssh_run"),
                       "ssh_run"),
        }
        for name, (call, expected) in cases.items():
            with self.subTest(case=name):
                client = MagicMock(default_model="test-model")
                session = AgentSession(client, workspace_root=Path(self.tmp.name))
                client.chat_stream_assembled.side_effect = [
                    response("", "tool_calls", [call]),
                    response("done"),
                ]
                with patch("src.agent.execute_tool_call", return_value="Çıkış Kodu: 0") as execute:
                    self.assertEqual(session.run_step("uygula"), "done")
                execute.assert_called_once()
                self.assertEqual(execute.call_args.kwargs["name"], expected)

    def test_read_only_modes_block_writes(self):
        # Ask mode rejects the write tool call before execution.
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

        # A read-only turn mode survives followup/continue and pauses again.
        for resume in ("execute_followup", "continue_task"):
            with self.subTest(resume=resume):
                client = MagicMock(default_model="test-model")
                session = AgentSession(client, workspace_root=Path(self.tmp.name))
                client.chat_stream_assembled.return_value = response(
                    "", "tool_calls", [tool_call('{"command": "hostname"}')],
                )
                with patch("src.agent.execute_tool_call", return_value="local"):
                    session.run_step("ask: incele", max_steps=1)
                client.chat_stream_assembled.return_value = response(
                    "", "tool_calls", [tool_call('{"path":"a.py","content":"x"}', "write_file")],
                )
                approval = session.auto_approve
                with patch("src.agent.execute_tool_call") as execute:
                    getattr(session, resume)(
                        "hepsine onay veriyorum", max_steps=1,
                    ) if resume == "execute_followup" else session.continue_task(max_steps=1)
                execute.assert_not_called()
                self.assertEqual(session.pending_mode, AgentMode.ASK)
                self.assertEqual(session.auto_approve, approval)
                self.assertEqual(session.mode, AgentMode.NORMAL)

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

    def test_step_limit_rules(self):
        with self.subTest(case="invalid_limit"):
            with self.assertRaises(ValueError):
                self.session.run_step("incele", max_steps=0)
            self.client.chat_stream_assembled.assert_not_called()

        with self.subTest(case="limit_reached"):
            self.client.chat_stream_assembled.return_value = response(
                "", "tool_calls", [tool_call('{"command": "echo local"}')],
            )
            with patch("src.agent.execute_tool_call", return_value="Çıkış Kodu: 0"):
                result = self.session.run_step("uygula", max_steps=1)
            self.assertTrue(result.startswith("HATA:"))

        with self.subTest(case="final_answer_on_last_step"):
            client = MagicMock(default_model="test-model")
            session = AgentSession(client, workspace_root=Path(self.tmp.name))
            client.chat_stream_assembled.return_value = response("done")
            with patch("src.agent.print_warning") as warning:
                self.assertEqual(session.run_step("incele", max_steps=1), "done")
            warning.assert_not_called()

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

    def test_resume_without_pending_task_never_calls_model(self):
        for name in ("continue_task", "execute_followup"):
            with self.subTest(method=name):
                session = AgentSession(self.client, workspace_root=Path(self.tmp.name))
                result = (session.execute_followup("yap artık") if name == "execute_followup"
                          else session.continue_task())
                self.assertTrue(result.startswith("HATA:"))
        self.client.chat_stream_assembled.assert_not_called()

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
        # No mandatory clarify step and no one-step-per-turn rule: both made the
        # model deliberate and spend a full request per tool call.
        self.assertNotIn("ZORUNLU NETLEŞTİRME", SYSTEM_PROMPT)
        self.assertNotIn("Her model turunda yalnızca sıradaki küçük alt adımı", SYSTEM_PROMPT)
        self.assertIn("aynı turda birlikte çağır", SYSTEM_PROMPT)
        # Domain notes left the system prompt and travel with matching tasks only.
        self.assertNotIn("resource-tree", SYSTEM_PROMPT)

    def test_domain_notes_attach_only_to_matching_tasks(self):
        from src.agent import HTTP_ARGO_NOTES, K8S_NOTES, domain_hints

        self.assertIn("/api/v1/applications/{ad}/resource-tree", HTTP_ARGO_NOTES)
        self.assertIn("/pods", HTTP_ARGO_NOTES)
        self.assertIn("HTTP:%{http_code}", HTTP_ARGO_NOTES)
        # Multi-endpoint probing must be one loop in one run_command call, and a
        # 404 must not be retried (r20/curl404 spent 4 steps / 86 s on 50 curls).
        self.assertIn("for i in $(seq 1 50); do", HTTP_ARGO_NOTES)
        self.assertIn("break", HTTP_ARGO_NOTES)
        self.assertIn("tekrar denemez", HTTP_ARGO_NOTES)
        # 50 sequential curls took ~60 s; stop after 3 identical 404s.
        self.assertIn('"$i" -ge 3', HTTP_ARGO_NOTES)
        self.assertEqual(domain_hints("README yazım hatasını düzelt"), "")
        self.assertIn(HTTP_ARGO_NOTES, domain_hints("Argo CD pod durumunu getir"))
        hints = domain_hints("koyu3-k8s üzerindeki podların resource ve limit değerlerini listele")
        self.assertIn(K8S_NOTES, hints)
        self.assertIn('preset="k8s_resources"', K8S_NOTES)
        # kubectl work alone does not drag in the HTTP/Argo notes.
        self.assertNotIn(HTTP_ARGO_NOTES, domain_hints("kubectl ile podları listele"))

    def test_context_describes_actual_shell_and_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            for system, os_name, shell in (
                ("Windows", "nt", r"C:\Windows\System32\cmd.exe"),
                ("Linux", "posix", "/bin/sh"),
            ):
                with self.subTest(system=system):
                    with patch("src.config.platform.system", return_value=system), \
                         patch("src.config.windows_shell", return_value="cmd"), \
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
                 patch.dict("os.environ", {"EVREN_SHELL": "cmd"}), \
                 patch("src.tools.print_warning"), patch("src.tools.print_command_output"):
                result = tool_run_command("echo local", workspace_root=root, auto_approve=True)
            self.assertIn("Çıkış Kodu: 0", result)
            execute.assert_called_once_with("echo local", cwd=str(root), timeout=180, shell=True)


if __name__ == "__main__":
    unittest.main()
