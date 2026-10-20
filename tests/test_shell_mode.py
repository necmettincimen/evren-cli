"""`!komut` shell mode and the Windows Terminal profile builder."""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.commands import is_shell_input as commands_is_shell_input
from src.shell_mode import is_shell_input, run_user_shell
from src.terminal_setup import PROFILE_NAME, build_profile


class ShellModeTest(unittest.TestCase):
    def test_detection(self):
        for text, want in (("!git status", True), ("  !ls", True), ("!", False), ("merhaba !", False)):
            with self.subTest(text=text):
                self.assertEqual(is_shell_input(text), want)

    def test_classifier_is_reexported_from_commands(self):
        """One implementation: shell_mode re-exports the commands.py classifier."""
        self.assertIs(is_shell_input, commands_is_shell_input)

    def test_commands_does_not_import_shell_mode(self):
        """commands.py is the low-level module; importing it must not pull shell_mode."""
        import subprocess
        import sys

        code = (
            "import sys, src.commands; "
            "print('src.shell_mode' in sys.modules)"
        )
        out = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(Path(__file__).resolve().parents[1]),
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(out.stdout.strip(), "False")

    def test_output_is_shown_and_remembered_for_the_agent(self):
        with tempfile.TemporaryDirectory() as d:
            session = SimpleNamespace(workspace_root=Path(d), messages=[], vault=None)
            with patch("src.ui.print_info"):
                code = run_user_shell("!python -c \"print('evren-ok')\"", session)
        self.assertEqual(code, 0)
        self.assertEqual(session.messages[-1]["role"], "user")
        self.assertIn("evren-ok", session.messages[-1]["content"])
        self.assertIn("Çıkış kodu: 0", session.messages[-1]["content"])


class TerminalProfileTest(unittest.TestCase):
    def test_profile_runs_evren_in_a_login_bash_and_keeps_the_tab(self):
        p = build_profile(r"C:\Git\bin\bash.exe", "%USERPROFILE%")
        self.assertEqual(p["name"], PROFILE_NAME)
        self.assertIn("--login -i -c", p["commandline"])
        self.assertIn("evren; exec bash -i", p["commandline"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
