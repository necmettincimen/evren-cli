"""Tests for the marker-based managed-section merge core (src/docs_merge.py)."""

from __future__ import annotations

import unittest

from src.docs_merge import (
    MALFORMED,
    MergeError,
    UNTOUCHABLE,
    end_marker,
    extract_sections,
    has_managed_sections,
    parse_sections,
    render_sections,
    section_hash,
    start_marker,
    strip_managed,
    wrap_section,
)


def doc(*lines: str) -> str:
    return "\n".join(lines) + "\n"


class MarkerHelpersTest(unittest.TestCase):
    def test_markers_round_trip(self):
        block = wrap_section("runtime_env", "- os: Windows\n- shell: cmd")
        self.assertIn(start_marker("runtime_env"), block)
        self.assertIn(end_marker("runtime_env"), block)
        sections, errors = parse_sections(block + "\n")
        self.assertEqual(errors, [])
        self.assertEqual(sections[0].body_text, "- os: Windows\n- shell: cmd")




class ParseSectionsTest(unittest.TestCase):

    def test_nested_start_is_malformed(self):
        text = doc(
            start_marker("a"),
            "iç",
            start_marker("b"),
            end_marker("b"),
            end_marker("a"),
        )
        sections, errors = parse_sections(text)
        self.assertEqual(sections, [])
        self.assertTrue(errors)






class RenderSectionsTest(unittest.TestCase):

    def test_malformed_document_is_untouchable(self):
        text = doc(start_marker("a"), "iç")
        new, changed, errors = render_sections(text, {"a": "x"})
        self.assertEqual(new, text)
        self.assertEqual(changed, [])
        self.assertEqual(errors, [MALFORMED])

    def test_only_managed_body_changes(self):
        text = doc(
            "# agent.md",
            "",
            "## Elle bölüm",
            "korunacak metin",
            "",
            start_marker("runtime_env"),
            "- eski: 1",
            end_marker("runtime_env"),
            "",
            "## Notlar (elle)",
            "alt not",
        )
        new, changed, errors = render_sections(text, {"runtime_env": "- yeni: 2"})
        self.assertEqual(errors, [])
        self.assertEqual(changed, ["runtime_env"])
        self.assertIn("korunacak metin", new)
        self.assertIn("alt not", new)
        self.assertIn("- yeni: 2", new)
        self.assertNotIn("- eski: 1", new)



    def test_crlf_and_bom_preserved(self):
        plain = doc("# başlık", start_marker("a"), "eski", end_marker("a"))
        text = "\ufeff" + plain.replace("\n", "\r\n")
        new, changed, errors = render_sections(text, {"a": "yeni"})
        self.assertEqual(changed, ["a"])
        self.assertEqual(errors, [])
        self.assertTrue(new.startswith("\ufeff"))
        self.assertIn("\r\nyeni\r\n", new)
        # No bare LF was introduced into an otherwise CRLF document.
        self.assertEqual(new.replace("\r\n", "").count("\n"), 0)




class StripManagedTest(unittest.TestCase):
    def test_strip_keeps_human_text(self):
        text = doc(
            "# agent.md",
            "",
            "## Notlar",
            "elle metin",
            "",
            start_marker("a"),
            "makine metni",
            end_marker("a"),
        )
        new, removed, errors = strip_managed(text)
        self.assertEqual(errors, [])
        self.assertEqual(removed, ["a"])
        self.assertIn("elle metin", new)
        self.assertNotIn("makine metni", new)
        self.assertNotIn("evren:managed", new)




if __name__ == "__main__":  # pragma: no cover
    unittest.main()
