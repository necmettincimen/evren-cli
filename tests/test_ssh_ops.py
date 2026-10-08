import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.ssh_hosts import SshHost, load_hosts, get_host, add_host, remove_host
from src.ssh_ops import build_ssh_command, ssh_run, ssh_read_file, ssh_write_file, ssh_list_hosts
from src.modes import AgentMode
from src.tool_gateway import check_tool_allowed


class TestSshHosts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / ".evren").mkdir(parents=True, exist_ok=True)
        # load_hosts() falls back to ~/.evren/ssh_hosts.json; isolate the test
        # from the real user registry so results never depend on the machine.
        self._home_patch = mock.patch.object(Path, "home", return_value=self.root)
        self._home_patch.start()

    def tearDown(self):
        self._home_patch.stop()
        self.tmp.cleanup()

    def test_load_hosts_missing_file(self):
        hosts = load_hosts(self.root)
        self.assertEqual(hosts, {})

    def test_load_hosts_malformed_json(self):
        (self.root / ".evren" / "ssh_hosts.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(ValueError):
            load_hosts(self.root)

    def test_add_and_get_host(self):
        add_host("prod", "10.0.0.5", user="deploy", port=2222, workspace_root=self.root)
        h = get_host("prod", self.root)
        self.assertIsNotNone(h)
        self.assertEqual(h.host, "10.0.0.5")
        self.assertEqual(h.user, "deploy")
        self.assertEqual(h.port, 2222)
        self.assertFalse(h.allow_write)  # default safe

    def test_remove_host(self):
        add_host("prod", "10.0.0.5", workspace_root=self.root)
        self.assertTrue(remove_host("prod", self.root))
        self.assertFalse(remove_host("prod", self.root))
        self.assertIsNone(get_host("prod", self.root))

    def test_host_target_string(self):
        h = SshHost(alias="a", host="1.2.3.4", user="root")
        self.assertEqual(h.target, "root@1.2.3.4")
        h2 = SshHost(alias="b", host="1.2.3.4")
        self.assertEqual(h2.target, "1.2.3.4")


class TestBuildSshCommand(unittest.TestCase):
    def test_basic_command(self):
        h = SshHost(alias="a", host="1.2.3.4", user="root", port=22)
        argv = build_ssh_command(h, "uptime")
        self.assertEqual(argv[0], "ssh")
        self.assertIn("BatchMode=yes", argv)
        self.assertIn("StrictHostKeyChecking=accept-new", argv)
        self.assertIn("root@1.2.3.4", argv)
        self.assertEqual(argv[-1], "uptime")
        self.assertNotIn("-p", argv)  # default port omitted

    def test_custom_port_and_identity(self):
        h = SshHost(alias="a", host="1.2.3.4", user="deploy", port=2222,
                    identity_file="C:\\keys\\id")
        argv = build_ssh_command(h, "ls")
        self.assertIn("-p", argv)
        self.assertIn("2222", argv)
        self.assertIn("-i", argv)
        self.assertIn("C:\\keys\\id", argv)


class TestSshWriteGuard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / ".evren").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_write_blocked_when_allow_write_false(self):
        add_host("prod", "10.0.0.5", user="deploy", allow_write=False, workspace_root=self.root)
        result = ssh_write_file("prod", "/tmp/x", "data", workspace_root=self.root)
        self.assertTrue(result.startswith("HATA:"))
        self.assertIn("allow_write", result)

    def test_unknown_alias_rejected(self):
        result = ssh_run("nope", "uptime", workspace_root=self.root)
        self.assertTrue(result.startswith("HATA:"))
        self.assertIn("kayıtlı", result.lower())


class TestSshRunMocked(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / ".evren").mkdir(parents=True, exist_ok=True)
        add_host("prod", "10.0.0.5", user="deploy", workspace_root=self.root)

    def tearDown(self):
        self.tmp.cleanup()

    @mock.patch("src.ssh_ops.run_with_tree_kill")
    def test_ssh_run_success(self, mock_run):
        mock_run.return_value = (0, "ok output", "", False)
        result = ssh_run("prod", "uptime", workspace_root=self.root)
        self.assertIn("Çıkış Kodu: 0", result)
        self.assertIn("ok output", result)
        # Verify ssh argv was passed (not shell=True).
        called_argv = mock_run.call_args[0][0]
        self.assertEqual(called_argv[0], "ssh")
        self.assertFalse(mock_run.call_args[1].get("shell", False))

    @mock.patch("src.ssh_ops.run_with_tree_kill")
    def test_ssh_run_timeout(self, mock_run):
        mock_run.return_value = (-1, "", "", True)
        result = ssh_run("prod", "sleep 999", workspace_root=self.root)
        self.assertTrue(result.startswith("HATA:"))
        self.assertIn("zaman aşımı", result)

    @mock.patch("src.ssh_ops.run_with_tree_kill")
    def test_ssh_read_file(self, mock_run):
        mock_run.return_value = (0, "file contents", "", False)
        result = ssh_read_file("prod", "/etc/hosts", workspace_root=self.root)
        self.assertIn("file contents", result)
        self.assertIn("/etc/hosts", result)


class TestSshGateway(unittest.TestCase):
    def test_read_only_ssh_tools_allowed_in_ask(self):
        for tool in ["ssh_list_hosts", "ssh_read_file"]:
            ok, _ = check_tool_allowed(tool, {}, AgentMode.ASK)
            self.assertTrue(ok, f"{tool} should be allowed in ask")

    def test_ssh_write_denied_in_ask(self):
        ok, reason = check_tool_allowed("ssh_write_file", {}, AgentMode.ASK)
        self.assertFalse(ok)
        self.assertIn("ask", reason.lower())

    def test_ssh_write_denied_in_plan(self):
        ok, reason = check_tool_allowed("ssh_write_file", {}, AgentMode.PLAN)
        self.assertFalse(ok)
        self.assertIn("plan", reason.lower())

    def test_ssh_run_read_only_ok_in_ask(self):
        ok, _ = check_tool_allowed("ssh_run", {"command": "uptime"}, AgentMode.ASK)
        self.assertTrue(ok)

    def test_ssh_run_write_denied_in_ask(self):
        ok, _ = check_tool_allowed("ssh_run", {"command": "rm -rf /"}, AgentMode.ASK)
        self.assertFalse(ok)

    def test_ssh_run_allowed_in_normal(self):
        ok, _ = check_tool_allowed("ssh_run", {"command": "rm -rf /"}, AgentMode.NORMAL)
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
