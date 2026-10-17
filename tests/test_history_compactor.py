import unittest

from src.token_manager import TokenManager
from src.history_compactor import HistoryCompactor, TOOL_TRUNCATION_MARKER


def _tm(context_window=100000):
    return TokenManager(context_window=context_window)


class TestHistoryCompactor(unittest.TestCase):

    def test_system_prompt_always_preserved(self):
        comp = HistoryCompactor(_tm(context_window=200))
        msgs = [{"role": "system", "content": "SYS"}]
        for i in range(20):
            msgs.append({"role": "user", "content": f"message {i} " * 20})
            msgs.append({"role": "assistant", "content": f"reply {i} " * 20})
        out = comp.compact(msgs, output_budget=50)
        self.assertEqual(out[0]["role"], "system")
        self.assertEqual(out[0]["content"], "SYS")


    def test_assistant_tool_pair_stays_atomic(self):
        comp = HistoryCompactor(_tm(context_window=300))
        msgs = [{"role": "system", "content": "sys"}]
        for i in range(10):
            msgs.append({"role": "user", "content": f"u{i} " * 30})
            msgs.append({"role": "assistant", "content": "", "tool_calls": [
                {"id": f"t{i}", "function": {"name": "view_file", "arguments": "{}"}}
            ]})
            msgs.append({"role": "tool", "tool_call_id": f"t{i}", "content": f"result {i} " * 30})
        out = comp.compact(msgs, output_budget=50)
        # Every tool message must be preceded (somewhere) by its assistant parent.
        assistant_ids = set()
        for m in out:
            if m.get("role") == "assistant":
                for tc in m.get("tool_calls") or []:
                    assistant_ids.add(tc["id"])
        for m in out:
            if m.get("role") == "tool":
                self.assertIn(m["tool_call_id"], assistant_ids)




if __name__ == "__main__":
    unittest.main()
