"""Tests for mode-filtered tool schemas (plan token-consumption-fixes, Faz 4 / F2)."""

from __future__ import annotations

import json
import unittest

from src.modes import AgentMode
from src.token_manager import TokenManager
from src.tool_gateway import check_tool_allowed
from src.tools import TOOLS_SCHEMA, tools_for_mode


def names(schema):
    return {t.get("function", {}).get("name") for t in schema}


class ToolsForModeTest(unittest.TestCase):

    def test_ask_drops_write_tools(self):
        got = names(tools_for_mode(AgentMode.ASK))
        self.assertNotIn("edit_file", got)
        self.assertNotIn("write_file", got)
        self.assertNotIn("ssh_write_file", got)
        self.assertIn("view_file", got)
        self.assertIn("run_command", got)
        self.assertIn("ask_user", got)





    def test_every_hidden_tool_is_denied_by_the_gateway(self):
        # The filter is only a cost optimisation: the gateway must already deny
        # every tool we stop advertising.
        hidden = {"edit_file", "write_file", "ssh_write_file"}
        for mode in (AgentMode.ASK, AgentMode.PLAN):
            for tool in hidden:
                allowed, _reason = check_tool_allowed(tool, {}, mode)
                self.assertFalse(allowed, f"{tool} allowed in {mode}")

    def test_advertised_tools_are_all_allowed_by_the_gateway(self):
        # `ask_user` is advertised in every mode, so the gateway must allow it too.
        probes = {
            "view_file": {"path": "a.py"},
            "list_directory": {},
            "find_files": {"pattern": "*.py"},
            "run_command": {"command": "dir"},
            "ssh_list_hosts": {},
            "ask_user": {"question": "?"},
        }
        for mode in (AgentMode.ASK, AgentMode.PLAN):
            for tool in names(tools_for_mode(mode)) - {"create_plan"}:
                if tool in probes:
                    allowed, reason = check_tool_allowed(tool, probes[tool], mode)
                    self.assertTrue(allowed, f"{tool} in {mode}: {reason}")

    def test_create_plan_schema_asks_for_one_call(self):
        # A plan run spent 6 reads before create_plan (7 steps, 66s). The schema
        # must tell the model to write the plan in a single call and not to
        # re-read the file afterwards for verification.
        spec = next(
            t["function"] for t in TOOLS_SCHEMA if t["function"]["name"] == "create_plan"
        )
        desc = spec["description"]
        self.assertIn("TEK çağrıda", desc)
        self.assertIn("yeniden okuma", desc)
        self.assertIn("hedef dosyalar", spec["parameters"]["properties"]["content"]["description"])




if __name__ == "__main__":  # pragma: no cover
    unittest.main()
