"""Built-in remote presets: exact commands, namespace scoping, gateway, dispatch."""

import unittest
from pathlib import Path
from unittest import mock

from src.modes import AgentMode
from src.remote_presets import build_preset, preset_names
from src.tool_gateway import check_tool_allowed
from src.tools import TOOLS_SCHEMA, execute_tool_call


class BuildPresetTest(unittest.TestCase):
    def test_health_matches_the_live_tested_command(self):
        cmd = build_preset("k8s_health")
        self.assertIn("kubectl get pods -A --no-headers | awk '{print $4}'", cmd)
        self.assertIn("awk '$5+0>3'", cmd)
        # The go-template newline must stay an escape, never a raw line break
        # (a raw one broke the template: "unterminated quoted string").
        self.assertIn('x{{"\\n"}}{{end}}', cmd)
        self.assertNotIn("\n", cmd)

    def test_resources_table(self):
        cmd = build_preset("k8s_resources")
        self.assertTrue(cmd.startswith("kubectl get --raw /readyz"))  # API guard first
        self.assertIn("kubectl get pods -A -o go-template='{{range .items}}", cmd)
        self.assertIn("| column -t", cmd)
        self.assertNotIn("\n", cmd)

    def test_namespace_scopes_and_shifts_awk_fields(self):
        cmd = build_preset("k8s_health", "ays-data")
        self.assertNotIn(" -A ", cmd)
        self.assertIn("kubectl get pods -n ays-data --no-headers | awk '{print $3}'", cmd)
        self.assertIn("awk '$4+0>3'", cmd)

    def test_invalid_input_is_rejected(self):
        with self.assertRaises(ValueError):
            build_preset("rm_everything")
        with self.assertRaises(ValueError):
            build_preset("k8s_health", "x; reboot")

    def test_schema_enum_matches_presets(self):
        ssh = next(t for t in TOOLS_SCHEMA if t["function"]["name"] == "ssh_run")
        props = ssh["function"]["parameters"]["properties"]
        self.assertEqual(sorted(props["preset"]["enum"]), preset_names())
        self.assertEqual(ssh["function"]["parameters"]["required"], ["alias"])


class PresetGatewayTest(unittest.TestCase):
    def test_presets_are_allowed_in_read_only_modes(self):
        for mode in (AgentMode.ASK, AgentMode.PLAN):
            ok, reason = check_tool_allowed("ssh_run", {"alias": "k", "preset": "k8s_health"}, mode)
            self.assertTrue(ok, reason)

    def test_unknown_preset_falls_back_to_command_check(self):
        ok, _ = check_tool_allowed(
            "ssh_run", {"alias": "k", "preset": "nope", "command": "systemctl stop k3s"}, AgentMode.ASK,
        )
        self.assertFalse(ok)


class PresetDispatchTest(unittest.TestCase):
    def test_preset_builds_the_command_for_ssh_run(self):
        # Pin the approval path: a developer .env with EVREN_AUTO_APPROVE=true
        # used to skip it and fail this test.
        with mock.patch("src.ssh_ops.ssh_run", return_value="[k] Çıkış Kodu: 0") as run, \
                mock.patch("src.tools.print_warning") as warn, \
                mock.patch("src.tools.EVREN_AUTO_APPROVE", False), \
                mock.patch("src.tools.prompt_confirm", return_value=True):
            out = execute_tool_call(
                "ssh_run", {"alias": "k", "preset": "k8s_resources"}, workspace_root=Path("."), auto_approve=False,
            )
        self.assertIn("Çıkış Kodu: 0", out)
        self.assertEqual(run.call_args.kwargs["command"], build_preset("k8s_resources"))
        self.assertIn("preset=k8s_resources", str(warn.call_args))

    def test_auto_approve_prints_no_warning_line(self):
        # With auto-approve on, the single tool-call line is the whole
        # announcement; no "Ajan ... çalıştırmak istiyor" warning is printed.
        with mock.patch("src.ssh_ops.ssh_run", return_value="[k] Çıkış Kodu: 0") as run, \
                mock.patch("src.tools.print_warning") as warn:
            out = execute_tool_call(
                "ssh_run", {"alias": "k", "preset": "k8s_resources"}, workspace_root=Path("."), auto_approve=True,
            )
        self.assertIn("Çıkış Kodu: 0", out)
        self.assertEqual(run.call_args.kwargs["command"], build_preset("k8s_resources"))
        warn.assert_not_called()

    def test_missing_command_and_preset_is_an_error(self):
        out = execute_tool_call("ssh_run", {"alias": "k"}, workspace_root=Path("."), auto_approve=True)
        self.assertTrue(out.startswith("HATA:"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
