import tempfile
import unittest
from pathlib import Path

from src.tracking import (
    add_task,
    update_task,
    list_tasks,
    remove_task,
    progress_summary,
    STATUSES,
)


class TestTracking(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_add_task(self):
        item = add_task("impl-001", "Testleri yaz", workspace_root=self.ws)
        self.assertEqual(item["status"], "not-started")
        self.assertEqual(item["progress"], 0)

    def test_add_duplicate_raises(self):
        add_task("impl-001", "A", workspace_root=self.ws)
        with self.assertRaises(ValueError):
            add_task("impl-001", "B", workspace_root=self.ws)




    def test_progress_100_completes(self):
        add_task("t1", "iş", workspace_root=self.ws)
        item = update_task("t1", progress=100, workspace_root=self.ws)
        self.assertEqual(item["status"], "completed")




    def test_list_filter_open(self):
        add_task("a", "x", workspace_root=self.ws)
        add_task("b", "y", workspace_root=self.ws)
        update_task("b", status="completed", workspace_root=self.ws)
        open_items = list_tasks("open", self.ws)
        self.assertEqual([i["id"] for i in open_items], ["a"])






if __name__ == "__main__":
    unittest.main()
