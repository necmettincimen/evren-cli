import unittest

from src.skills import SKILLS, list_skills, get_skill, categories


class TestSkills(unittest.TestCase):
    def test_catalog_not_empty(self):
        self.assertGreater(len(SKILLS), 0)

    def test_list_all(self):
        items = list_skills()
        self.assertEqual(len(items), len(SKILLS))

    def test_list_by_category(self):
        items = list_skills("debugging")
        self.assertTrue(items)
        self.assertTrue(all(s.category == "debugging" for s in items))

    def test_list_unknown_category(self):
        self.assertEqual(list_skills("nope"), [])

    def test_get_skill_exact(self):
        skill = get_skill("root-cause-debug")
        self.assertIsNotNone(skill)
        self.assertEqual(skill.name, "root-cause-debug")

    def test_get_skill_case_insensitive(self):
        self.assertIsNotNone(get_skill("ROOT-CAUSE-DEBUG"))

    def test_get_skill_missing(self):
        self.assertIsNone(get_skill("does-not-exist"))

    def test_get_skill_empty(self):
        self.assertIsNone(get_skill(""))

    def test_prompt_block_contains_steps(self):
        skill = get_skill("test-first")
        block = skill.prompt_block("bağlam")
        self.assertIn("UYGULANAN BECERİ", block)
        self.assertIn("bağlam", block)
        self.assertIn("1.", block)

    def test_categories_sorted_unique(self):
        cats = categories()
        self.assertEqual(cats, sorted(set(cats)))


if __name__ == "__main__":
    unittest.main()
