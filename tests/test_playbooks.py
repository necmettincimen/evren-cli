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



    def test_get_playbook_exact(self):
        pb = get_playbook("release")
        self.assertIsNotNone(pb)
        self.assertEqual(pb.name, "release")



    def test_step_prompt_out_of_range(self):
        pb = get_playbook("release")
        with self.assertRaises(IndexError):
            pb.step_prompt(999)

    def test_save_and_load_checkpoint(self):
        save_checkpoint("release", 2, self.ws)
        cp = load_checkpoint(self.ws)
        self.assertEqual(cp["playbook"], "release")
        self.assertEqual(cp["next_step"], 2)






if __name__ == "__main__":
    unittest.main()
