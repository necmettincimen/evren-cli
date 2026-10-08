import tempfile
import unittest
from pathlib import Path

from src.playbooks import (
    PLAYBOOKS,
    list_playbooks,
    get_playbook,
    save_checkpoint,
    load_checkpoint,
    clear_checkpoint,
)


class TestPlaybooks(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_catalog_not_empty(self):
        self.assertGreater(len(PLAYBOOKS), 0)

    def test_list_playbooks(self):
        self.assertEqual(len(list_playbooks()), len(PLAYBOOKS))

    def test_get_playbook_exact(self):
        pb = get_playbook("release")
        self.assertIsNotNone(pb)
        self.assertEqual(pb.name, "release")

    def test_get_playbook_missing(self):
        self.assertIsNone(get_playbook("nope"))

    def test_step_prompt(self):
        pb = get_playbook("release")
        block = pb.step_prompt(0)
        self.assertIn("Adım 1/", block)

    def test_step_prompt_out_of_range(self):
        pb = get_playbook("release")
        with self.assertRaises(IndexError):
            pb.step_prompt(999)

    def test_save_and_load_checkpoint(self):
        save_checkpoint("release", 2, self.ws)
        cp = load_checkpoint(self.ws)
        self.assertEqual(cp["playbook"], "release")
        self.assertEqual(cp["next_step"], 2)

    def test_save_checkpoint_unknown_playbook(self):
        with self.assertRaises(ValueError):
            save_checkpoint("nope", 0, self.ws)

    def test_save_checkpoint_out_of_range(self):
        with self.assertRaises(ValueError):
            save_checkpoint("release", 999, self.ws)

    def test_load_no_checkpoint(self):
        self.assertIsNone(load_checkpoint(self.ws))

    def test_clear_checkpoint(self):
        save_checkpoint("release", 1, self.ws)
        self.assertTrue(clear_checkpoint(self.ws))
        self.assertFalse(clear_checkpoint(self.ws))


if __name__ == "__main__":
    unittest.main()
