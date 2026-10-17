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







class MultilineCommandTest(unittest.TestCase):
    def test_use_python_runs_code_without_shell(self):
        import sys

        argv, use_shell = _command_argv("import os\nprint(os.name)", False, True)
        self.assertEqual(argv, [sys.executable, "-c", "import os\nprint(os.name)"])
        self.assertFalse(use_shell)


    @unittest.skipUnless(os.name == "nt", "cmd.exe specific")
    def test_other_multiline_cmd_is_rejected_clearly(self):
        from src.tools import MultilineShellCommand

        with self.assertRaises(MultilineShellCommand):
            _command_argv("echo a\necho b", False)



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






if __name__ == "__main__":  # pragma: no cover
    unittest.main()
