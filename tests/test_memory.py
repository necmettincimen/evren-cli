import tempfile
import unittest
from pathlib import Path

from src.memory import (
    load_memory,
    save_memory,
    set_profile,
    add_note,
    clear_memory,
    memory_context_block,
)


class TestMemory(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_load_empty(self):
        state = load_memory(self.ws)
        self.assertEqual(state["profile"], {})
        self.assertEqual(state["notes"], [])

    def test_set_profile_persists(self):
        set_profile("stack", "FastAPI", self.ws)
        state = load_memory(self.ws)
        self.assertEqual(state["profile"]["stack"], "FastAPI")

    def test_set_profile_empty_key_raises(self):
        with self.assertRaises(ValueError):
            set_profile("", "x", self.ws)

    def test_add_note(self):
        add_note("ilk not", self.ws)
        state = load_memory(self.ws)
        self.assertEqual(len(state["notes"]), 1)
        self.assertEqual(state["notes"][0]["text"], "ilk not")

    def test_add_note_empty_raises(self):
        with self.assertRaises(ValueError):
            add_note("   ", self.ws)

    def test_clear_memory(self):
        set_profile("goal", "ship", self.ws)
        clear_memory(self.ws)
        state = load_memory(self.ws)
        self.assertEqual(state["profile"], {})

    def test_context_block_empty(self):
        self.assertEqual(memory_context_block(self.ws), "")

    def test_context_block_with_data(self):
        set_profile("stack", "Django", self.ws)
        add_note("dikkat: migration", self.ws)
        block = memory_context_block(self.ws)
        self.assertIn("PROJE HAFIZASI", block)
        self.assertIn("Django", block)
        self.assertIn("migration", block)

    def test_corrupt_file_returns_empty(self):
        path = self.ws / ".evren" / "memory.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ not json", encoding="utf-8")
        state = load_memory(self.ws)
        self.assertEqual(state["profile"], {})


if __name__ == "__main__":
    unittest.main()
