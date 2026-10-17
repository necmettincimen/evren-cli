import unittest

from src.modes import AgentMode
from src.tool_gateway import check_tool_allowed, is_read_only_command


class TestReadOnlyCommand(unittest.TestCase):

    def test_write_commands_rejected(self):
        for cmd in ["rm -rf /", "del file.txt", "mkdir newdir", "pip install x", "npm install"]:
            ok, _ = is_read_only_command(cmd)
            self.assertFalse(ok, f"{cmd} should be rejected")

    def test_shell_metacharacters_rejected(self):
        for cmd in ["ls > out.txt", "cat a | grep b", "echo x && rm y", "ls; rm z"]:
            ok, _ = is_read_only_command(cmd)
            self.assertFalse(ok, f"{cmd} should be rejected")




class TestToolGateway(unittest.TestCase):


    def test_ask_denies_write_tools(self):
        for tool in ["write_file", "edit_file"]:
            ok, reason = check_tool_allowed(tool, {}, AgentMode.ASK)
            self.assertFalse(ok)
            self.assertIn("ask", reason.lower())



    def test_plan_denies_source_writes(self):
        for tool in ["write_file", "edit_file"]:
            ok, reason = check_tool_allowed(tool, {}, AgentMode.PLAN)
            self.assertFalse(ok)
            self.assertIn("plan", reason.lower())




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
