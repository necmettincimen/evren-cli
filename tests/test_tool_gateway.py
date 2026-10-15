import unittest

from src.modes import AgentMode
from src.tool_gateway import check_tool_allowed, is_read_only_command


class TestReadOnlyCommand(unittest.TestCase):
    def test_simple_read_only(self):
        for cmd in ["ls", "dir", "cat file.txt", "git status", "git log", "pwd", "find . -name '*.py'"]:
            ok, reason = is_read_only_command(cmd)
            self.assertTrue(ok, f"{cmd} -> {reason}")

    def test_write_commands_rejected(self):
        for cmd in ["rm -rf /", "del file.txt", "mkdir newdir", "pip install x", "npm install"]:
            ok, _ = is_read_only_command(cmd)
            self.assertFalse(ok, f"{cmd} should be rejected")

    def test_shell_metacharacters_rejected(self):
        for cmd in ["ls > out.txt", "cat a | grep b", "echo x && rm y", "ls; rm z"]:
            ok, _ = is_read_only_command(cmd)
            self.assertFalse(ok, f"{cmd} should be rejected")

    def test_git_write_subcommand_rejected(self):
        ok, _ = is_read_only_command("git commit -m x")
        self.assertFalse(ok)
        ok2, _ = is_read_only_command("git push")
        self.assertFalse(ok2)

    def test_empty_command(self):
        ok, _ = is_read_only_command("")
        self.assertFalse(ok)


class TestToolGateway(unittest.TestCase):
    def test_normal_allows_everything(self):
        for tool in ["write_file", "edit_file", "run_command", "view_file", "create_plan"]:
            ok, _ = check_tool_allowed(tool, {"command": "rm -rf /"}, AgentMode.NORMAL)
            self.assertTrue(ok)

    def test_ask_allows_read_only_tools(self):
        for tool in ["view_file", "list_directory", "find_files"]:
            ok, _ = check_tool_allowed(tool, {}, AgentMode.ASK)
            self.assertTrue(ok)

    def test_ask_denies_write_tools(self):
        for tool in ["write_file", "edit_file"]:
            ok, reason = check_tool_allowed(tool, {}, AgentMode.ASK)
            self.assertFalse(ok)
            self.assertIn("ask", reason.lower())

    def test_ask_run_command_read_only_ok(self):
        ok, _ = check_tool_allowed("run_command", {"command": "git status"}, AgentMode.ASK)
        self.assertTrue(ok)

    def test_ask_run_command_write_denied(self):
        ok, _ = check_tool_allowed("run_command", {"command": "rm -rf /"}, AgentMode.ASK)
        self.assertFalse(ok)

    def test_plan_denies_source_writes(self):
        for tool in ["write_file", "edit_file"]:
            ok, reason = check_tool_allowed(tool, {}, AgentMode.PLAN)
            self.assertFalse(ok)
            self.assertIn("plan", reason.lower())

    def test_plan_allows_create_plan(self):
        ok, _ = check_tool_allowed("create_plan", {}, AgentMode.PLAN)
        self.assertTrue(ok)

    def test_ask_denies_create_plan(self):
        ok, _ = check_tool_allowed("create_plan", {}, AgentMode.ASK)
        self.assertFalse(ok)

    def test_web_fetch_allowed_in_ask_and_plan(self):
        # web_fetch is a read-only tool: must be usable in every mode.
        for mode in (AgentMode.ASK, AgentMode.PLAN, AgentMode.NORMAL):
            ok, reason = check_tool_allowed(
                "web_fetch", {"url": "https://example.com"}, mode
            )
            self.assertTrue(ok, f"{mode}: {reason}")

    def test_non_string_command_does_not_crash(self):
        # Models may send command as a non-string; must not raise AttributeError.
        ok, _ = is_read_only_command(None)
        self.assertFalse(ok)
        ok2, _ = is_read_only_command(["ls"])
        self.assertFalse(ok2)
        ok3, reason = check_tool_allowed(
            "run_command", {"command": 42}, AgentMode.ASK
        )
        self.assertFalse(ok3)
        self.assertNotIn("AttributeError", reason)


if __name__ == "__main__":
    unittest.main()
