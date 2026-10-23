import unittest

from src.modes import AgentMode
from src.tool_gateway import check_tool_allowed, is_read_only_command


class TestReadOnlyCommand(unittest.TestCase):

    def test_write_commands_rejected(self):
        for cmd in ["rm -rf /", "del file.txt", "mkdir newdir", "pip install x", "npm install"]:
            ok, _ = is_read_only_command(cmd)
            self.assertFalse(ok, f"{cmd} should be rejected")

    def test_shell_metacharacters_rejected(self):
        for cmd in ["ls > out.txt", "echo x && rm y", "ls; rm z", "cat a | xargs rm",
                    "git log; git push", 'echo "$(rm -rf x)"', "cat `rm x`", "git log | tee out.txt",
                    "git log 2>/dev/null; ls", "git branch -D main", "git branch yeni", "git tag v1",
                    "git tag -d v1", "git remote add o url", "git -C repo branch -D x",
                    "git diff --output=x.patch", "git reflog expire --all"]:
            ok, _ = is_read_only_command(cmd)
            self.assertFalse(ok, f"{cmd} should be rejected")

    def test_read_only_chains_allowed(self):
        # Planners lost most of their 4 steps to refused read-only chains (2026-10-23).
        for cmd in ["git log --oneline --grep z8; echo ---; git log --oneline -3 abc",
                    "cat a | grep b", "git grep -n Foo -- '*.cs' | head -20",
                    "cd src && git status", 'git grep -n -E "a|b;c" -- "*.cs"',
                    "git -C portal-backend grep -n Foo", "git branch -a", "git branch --list 'evren/*'",
                    "git tag -l", "git remote -v", "git --no-pager log -3"]:
            ok, reason = is_read_only_command(cmd)
            self.assertTrue(ok, f"{cmd} should be allowed: {reason}")




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
