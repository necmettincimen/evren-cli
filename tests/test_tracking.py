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

    def test_add_empty_id_raises(self):
        with self.assertRaises(ValueError):
            add_task("", "A", workspace_root=self.ws)

    def test_add_empty_description_raises(self):
        with self.assertRaises(ValueError):
            add_task("x", "  ", workspace_root=self.ws)

    def test_update_progress(self):
        add_task("t1", "iş", workspace_root=self.ws)
        item = update_task("t1", progress=40, workspace_root=self.ws)
        self.assertEqual(item["progress"], 40)
        self.assertEqual(item["status"], "in-progress")

    def test_progress_100_completes(self):
        add_task("t1", "iş", workspace_root=self.ws)
        item = update_task("t1", progress=100, workspace_root=self.ws)
        self.assertEqual(item["status"], "completed")

    def test_update_status(self):
        add_task("t1", "iş", workspace_root=self.ws)
        item = update_task("t1", status="blocked", workspace_root=self.ws)
        self.assertEqual(item["status"], "blocked")

    def test_update_invalid_status_raises(self):
        add_task("t1", "iş", workspace_root=self.ws)
        with self.assertRaises(ValueError):
            update_task("t1", status="bogus", workspace_root=self.ws)

    def test_update_missing_raises(self):
        with self.assertRaises(ValueError):
            update_task("nope", progress=10, workspace_root=self.ws)

    def test_list_filter_open(self):
        add_task("a", "x", workspace_root=self.ws)
        add_task("b", "y", workspace_root=self.ws)
        update_task("b", status="completed", workspace_root=self.ws)
        open_items = list_tasks("open", self.ws)
        self.assertEqual([i["id"] for i in open_items], ["a"])

    def test_list_filter_status(self):
        add_task("a", "x", workspace_root=self.ws)
        update_task("a", status="deferred", workspace_root=self.ws)
        self.assertEqual(len(list_tasks("deferred", self.ws)), 1)

    def test_remove_task(self):
        add_task("a", "x", workspace_root=self.ws)
        self.assertTrue(remove_task("a", self.ws))
        self.assertFalse(remove_task("a", self.ws))

    def test_progress_summary(self):
        add_task("a", "x", workspace_root=self.ws)
        add_task("b", "y", workspace_root=self.ws)
        update_task("b", status="completed", workspace_root=self.ws)
        summary = progress_summary(self.ws)
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["percent"], 50)

    def test_all_statuses_valid(self):
        add_task("a", "x", workspace_root=self.ws)
        for status in STATUSES:
            item = update_task("a", status=status, workspace_root=self.ws)
            self.assertEqual(item["status"], status)


if __name__ == "__main__":
    unittest.main()
