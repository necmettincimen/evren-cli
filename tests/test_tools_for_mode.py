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
    def test_normal_is_the_full_schema(self):
        self.assertIs(tools_for_mode(AgentMode.NORMAL), TOOLS_SCHEMA)
        self.assertEqual(names(tools_for_mode(AgentMode.NORMAL)), names(TOOLS_SCHEMA))

    def test_ask_drops_write_tools(self):
        got = names(tools_for_mode(AgentMode.ASK))
        self.assertNotIn("edit_file", got)
        self.assertNotIn("write_file", got)
        self.assertNotIn("ssh_write_file", got)
        self.assertIn("view_file", got)
        self.assertIn("run_command", got)
        self.assertIn("ask_user", got)

    def test_plan_keeps_create_plan_but_drops_writes(self):
        got = names(tools_for_mode(AgentMode.PLAN))
        self.assertIn("create_plan", got)
        self.assertNotIn("edit_file", got)
        self.assertNotIn("write_file", got)

    def test_unknown_mode_is_conservative(self):
        # Anything that is not ask/plan must keep the full surface (fail open to
        # the gateway, never hide a tool the gateway would allow).
        self.assertEqual(names(tools_for_mode(None)), names(TOOLS_SCHEMA))

    def test_schemas_are_returned_by_reference_not_copied(self):
        # The derived TOOL_ARGUMENT_* maps are built once from TOOLS_SCHEMA;
        # filtering must not mutate it.
        before = len(TOOLS_SCHEMA)
        tools_for_mode(AgentMode.ASK)
        tools_for_mode(AgentMode.PLAN)
        self.assertEqual(len(TOOLS_SCHEMA), before)

    def test_filtered_schema_is_smaller(self):
        tm = TokenManager()
        full = tm.estimate_text(json.dumps(TOOLS_SCHEMA, ensure_ascii=False))
        ask = tm.estimate_text(json.dumps(tools_for_mode(AgentMode.ASK), ensure_ascii=False))
        self.assertLess(ask, full)
        self.assertGreaterEqual(full - ask, 300)

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

    def test_plan_mode_still_advertises_create_plan(self):
        allowed, reason = check_tool_allowed(
            "create_plan", {"name": "x", "content": "y"}, AgentMode.PLAN
        )
        self.assertTrue(allowed, reason)
        self.assertIn("create_plan", names(tools_for_mode(AgentMode.PLAN)))

    def test_ask_user_schema_is_unchanged_by_the_filter(self):
        # The filter must not silently remove the tool the system prompt relies on.
        for mode in (AgentMode.ASK, AgentMode.PLAN, AgentMode.NORMAL):
            self.assertIn("ask_user", names(tools_for_mode(mode)))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
