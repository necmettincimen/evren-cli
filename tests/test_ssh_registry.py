"""Machine-wide + project SSH registries, alias hints and long remote output."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.ssh_hosts import add_host, get_host, load_hosts, remove_host, suggest_aliases
from src.ssh_ops import spill_long_output, ssh_run


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


class RegistryMergeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self.ws = Path(self.tmp.name) / "ws"
        self.ws.mkdir()
        patcher = mock.patch.object(Path, "home", return_value=self.home)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_user_hosts_are_visible_next_to_project_hosts(self):
        _write(self.home / ".evren" / "ssh_hosts.json", {"koyu3-k8s": {"host": "10.0.0.30", "user": "oboe"}})
        _write(self.ws / ".evren" / "ssh_hosts.json", {"koyu3": {"host": "10.0.0.40", "user": "alma"}})
        hosts = load_hosts(self.ws)
        self.assertEqual(set(hosts), {"koyu3-k8s", "koyu3"})

    def test_project_entry_wins_on_conflict(self):
        _write(self.home / ".evren" / "ssh_hosts.json", {"x": {"host": "1.1.1.1"}})
        _write(self.ws / ".evren" / "ssh_hosts.json", {"x": {"host": "2.2.2.2"}})
        self.assertEqual(get_host("x", self.ws).host, "2.2.2.2")

    def test_add_goes_machine_wide_and_remove_finds_it(self):
        add_host("prod", "10.0.0.5", workspace_root=self.ws)
        self.assertTrue((self.home / ".evren" / "ssh_hosts.json").exists())
        self.assertFalse((self.ws / ".evren" / "ssh_hosts.json").exists())
        other_ws = Path(self.tmp.name) / "other"
        other_ws.mkdir()
        self.assertIsNotNone(get_host("prod", other_ws))
        self.assertTrue(remove_host("prod", self.ws))
        self.assertIsNone(get_host("prod", other_ws))

    def test_workspace_scope_stays_local(self):
        add_host("lab", "10.0.0.9", workspace_root=self.ws, scope="workspace")
        self.assertIn("lab", json.loads((self.ws / ".evren" / "ssh_hosts.json").read_text()))
        self.assertFalse((self.home / ".evren" / "ssh_hosts.json").exists())

    def test_unknown_alias_lists_close_matches_with_description(self):
        _write(self.ws / ".evren" / "ssh_hosts.json", {
            "koyu3": {"host": "10.0.0.40", "user": "alma", "description": "test cluster"},
            "bursa1": {"host": "10.0.0.50"},
        })
        self.assertEqual(suggest_aliases("koyu3-k8s", load_hosts(self.ws)), ["koyu3"])
        result = ssh_run("koyu3-k8s", "kubectl get pods", workspace_root=self.ws)
        self.assertTrue(result.startswith("HATA:"))
        self.assertIn("Benzer: koyu3 = alma@10.0.0.40 (test cluster)", result)
        self.assertIn("ask_user", result)


class ConnectionErrorHintTest(unittest.TestCase):
    def test_timeout_names_the_target_and_says_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            ws = Path(directory)
            _write(ws / ".evren" / "ssh_hosts.json", {"k": {"host": "10.9.9.9", "user": "oboe"}})
            with mock.patch.object(Path, "home", return_value=ws),                     mock.patch("src.ssh_ops.run_with_tree_kill", return_value=(
                        255, "", "ssh: connect to host 10.9.9.9 port 22: Connection timed out", False,
                    )):
                result = ssh_run("k", "kubectl get pods", workspace_root=ws)
        self.assertIn("oboe@10.9.9.9:22", result)
        self.assertIn("tekrarlama", result)


class SpillLongOutputTest(unittest.TestCase):
    def test_short_output_is_returned_as_is(self):
        self.assertEqual(spill_long_output("kısa", None, "x"), "kısa")

    def test_long_output_is_saved_shown_and_flagged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            text = "\n".join(f"ns{i} pod-{i} app 100m 200m 128Mi 256Mi" for i in range(400))
            with mock.patch("src.ui.print_command_output") as shown:
                result = spill_long_output(text, root, "ssh-koyu3")
            self.assertEqual(shown.call_count, 400)
            saved = list((root / ".evren" / "tmp").glob("ssh-koyu3-*.txt"))
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0].read_text(encoding="utf-8"), text)
            self.assertLess(len(result), len(text))
            self.assertIn("400 satır", result)
            self.assertIn("yeniden yazma", result)

    def test_live_output_is_not_printed_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch("src.ui.print_command_output") as shown:
                spill_long_output("x" * 9000, Path(directory), "cmd", shown_live=True)
            shown.assert_not_called()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
