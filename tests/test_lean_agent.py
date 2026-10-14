"""Token/loop fixes: bounded view_file, PowerShell argv, thinking switch."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.api_client import EvrenClient
from src.api_key_pool import ApiKeyPool
from src.config import thinking_off_supported
from src.tools import (
    VIEW_DEFAULT_LINES,
    VIEW_MAX_CHARS,
    VIEW_MAX_LINES,
    _command_argv,
    tool_view_file,
)
from tests.helpers import FakeCompletion, make_status_error


class ViewFileWindowTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "big.py").write_text(
            "\n".join(f"line {i}" for i in range(1, 1001)), encoding="utf-8"
        )

    def test_no_range_returns_default_window_with_hint(self):
        text = tool_view_file("big.py", workspace_root=self.root)
        self.assertIn(f"Satırlar 1-{VIEW_DEFAULT_LINES} / Toplam 1000", text)
        self.assertIn(f"line {VIEW_DEFAULT_LINES}", text)
        self.assertNotIn(f"line {VIEW_DEFAULT_LINES + 1}\n", text)
        self.assertIn("kırpıldı", text)
        self.assertIn("start_line", text)

    def test_explicit_range_is_capped(self):
        text = tool_view_file("big.py", start_line=100, end_line=1000, workspace_root=self.root)
        self.assertIn(f"Satırlar 100-{100 + VIEW_MAX_LINES - 1}", text)
        self.assertIn("kırpıldı", text)

    def test_small_range_is_untouched(self):
        text = tool_view_file("big.py", start_line=10, end_line=12, workspace_root=self.root)
        self.assertIn("Satırlar 10-12", text)
        self.assertNotIn("kırpıldı", text)

    def test_long_lines_hit_the_char_ceiling(self):
        (self.root / "wide.txt").write_text("\n".join("x" * 500 for _ in range(200)), encoding="utf-8")
        text = tool_view_file("wide.txt", workspace_root=self.root)
        self.assertLess(len(text), VIEW_MAX_CHARS + 500)
        self.assertIn("kırpıldı", text)

    def test_small_file_is_returned_whole(self):
        (self.root / "s.py").write_text("a\nb\n", encoding="utf-8")
        text = tool_view_file("s.py", workspace_root=self.root)
        self.assertIn("Satırlar 1-2 / Toplam 2", text)
        self.assertNotIn("kırpıldı", text)


class PowerShellArgvTest(unittest.TestCase):
    def test_default_uses_system_shell(self):
        self.assertEqual(_command_argv("dir", False), ("dir", True))

    def test_powershell_avoids_nested_quoting(self):
        argv, use_shell = _command_argv('Get-ChildItem "a b" | Select-Object Name', True)
        self.assertFalse(use_shell)
        self.assertIn("-NoProfile", argv)
        self.assertEqual(argv[-2], "-Command")
        self.assertTrue(argv[-1].endswith('Get-ChildItem "a b" | Select-Object Name'))

    def test_schema_exposes_flag_not_aliased_name(self):
        from src.tools import TOOLS_SCHEMA

        run = next(t for t in TOOLS_SCHEMA if t["function"]["name"] == "run_command")
        props = run["function"]["parameters"]["properties"]
        self.assertEqual(props["use_powershell"]["type"], "boolean")
        # `shell` is a legacy alias of `command` in tool_args; never reuse it.
        self.assertNotIn("shell", props)


class MultilineCommandTest(unittest.TestCase):
    def test_use_python_runs_code_without_shell(self):
        import sys

        argv, use_shell = _command_argv("import os\nprint(os.name)", False, True)
        self.assertEqual(argv, [sys.executable, "-c", "import os\nprint(os.name)"])
        self.assertFalse(use_shell)

    @unittest.skipUnless(os.name == "nt", "cmd.exe specific")
    def test_multiline_python_c_is_rewritten_on_windows(self):
        argv, use_shell = _command_argv('python -c "import json\nprint(json.dumps({\\"a\\": 1}))"', False)
        self.assertFalse(use_shell)
        self.assertEqual(argv[1:], ["-c", 'import json\nprint(json.dumps({"a": 1}))'])

    @unittest.skipUnless(os.name == "nt", "cmd.exe specific")
    def test_other_multiline_cmd_is_rejected_clearly(self):
        from src.tools import MultilineShellCommand

        with self.assertRaises(MultilineShellCommand):
            _command_argv("echo a\necho b", False)

    def test_single_line_stays_on_the_shell(self):
        self.assertEqual(_command_argv('python -c "print(1)"', False), ('python -c "print(1)"', True))


def _make_client(model="qwen3.8-flash-next"):
    client = EvrenClient.__new__(EvrenClient)
    client.base_url = "https://example.test/v1"
    client.default_model = model
    client.last_remaining_tokens = None
    client.reasoning_effort = "low"
    client.thinking_control = True
    client.key_pool = ApiKeyPool(["evren_llm_test"])
    client.api_key = client.key_pool.keys[0].key
    return client


class ThinkingSwitchTest(unittest.TestCase):
    def _send(self, model="qwen3.8-flash-next", side_effect=None, **kwargs):
        client = _make_client(model)
        fake = MagicMock()
        fake.chat.completions.create.side_effect = side_effect or [FakeCompletion("ok")]
        with patch.object(client.key_pool, "get_client", return_value=fake):
            client.chat_complete(messages=[{"role": "user", "content": "x"}], **kwargs)
        return client, fake

    def test_glm_never_gets_thinking_off(self):
        # Measured: glm-5.3 leaks its reasoning into content with thinking:false.
        self.assertFalse(thinking_off_supported("glm-5.3"))
        self.assertTrue(thinking_off_supported("qwen3.8-flash-next"))
        _client, fake = self._send("glm-5.3", thinking=False)
        self.assertNotIn("extra_body", fake.chat.completions.create.call_args.kwargs)

    def test_thinking_false_is_sent_as_extra_body(self):
        _client, fake = self._send(thinking=False)
        kwargs = fake.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["extra_body"], {"thinking": False})

    def test_default_sends_no_thinking_field(self):
        _client, fake = self._send()
        self.assertNotIn("extra_body", fake.chat.completions.create.call_args.kwargs)

    def test_effort_still_goes_to_deepseek_by_default(self):
        _client, fake = self._send("deepseek-v4.1-flash")
        self.assertEqual(fake.chat.completions.create.call_args.kwargs["reasoning_effort"], "low")

    def test_rejected_field_is_dropped_and_retried(self):
        client, fake = self._send(
            side_effect=[make_status_error(400, "unknown field: thinking"), FakeCompletion("ok")],
            thinking=False,
        )
        self.assertEqual(fake.chat.completions.create.call_count, 2)
        self.assertNotIn("extra_body", fake.chat.completions.create.call_args.kwargs)
        self.assertFalse(client.thinking_control)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
