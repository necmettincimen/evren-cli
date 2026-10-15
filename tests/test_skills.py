import unittest
from pathlib import Path

from src.skills import (
    SKILLS,
    USER_SKILLS,
    list_skills,
    get_skill,
    categories,
    load_user_skills,
    reload_user_skills,
    _parse_skill_file,
)

VALID_SKILL = """---
name: deploy-checklist
title: Dağıtım Kontrol Listesi
category: deployment
description: Üretim dağıtımını güvenli adımlarla tamamlar.
when_to_use: Yeni sürüm üretime alınırken.
---
- Sürüm notlarını oku
- Migration'ları kuru çalıştır
- Canary dağıt ve metrikleri izle
"""


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


class TestUserSkills(unittest.TestCase):
    def setUp(self):
        # Ensure a clean cache for every test.
        reload_user_skills(directory=Path("__nonexistent_skills_dir__"))

    def tearDown(self):
        reload_user_skills(directory=Path("__nonexistent_skills_dir__"))

    def test_parse_valid_front_matter(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "deploy-checklist.md"
            path.write_text(VALID_SKILL, encoding="utf-8")
            skill, warning = _parse_skill_file(path)
            self.assertEqual(warning, "")
            self.assertIsNotNone(skill)
            self.assertEqual(skill.name, "deploy-checklist")
            self.assertEqual(skill.title, "Dağıtım Kontrol Listesi")
            self.assertEqual(skill.category, "deployment")
            self.assertEqual(skill.source, "user")
            self.assertEqual(len(skill.steps), 3)

    def test_missing_name_falls_back_to_filename(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "My_Odd Skill.md"
            path.write_text("---\ntitle: X\n---\n- adım bir\n", encoding="utf-8")
            skill, warning = _parse_skill_file(path)
            self.assertIsNotNone(skill)
            self.assertEqual(skill.name, "my-odd-skill")
            self.assertEqual(skill.category, "user")

    def test_broken_file_skipped_with_warning(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "ok.md").write_text(VALID_SKILL, encoding="utf-8")
            (Path(d) / "---.md").write_text("", encoding="utf-8")
            skills, warnings = load_user_skills(Path(d))
            self.assertEqual(len(skills), 1)
            self.assertEqual(len(warnings), 1)

    def test_missing_dir_returns_empty(self):
        skills, warnings = load_user_skills(Path("__nonexistent_skills_dir__"))
        self.assertEqual(skills, [])
        self.assertEqual(warnings, [])

    def test_user_skill_shadows_builtin(self):
        import tempfile

        builtin_name = "test-first"
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / f"{builtin_name}.md").write_text(
                f"---\nname: {builtin_name}\ntitle: Benim TDD\ncategory: testing\n---\n- kendi adımım\n",
                encoding="utf-8",
            )
            loaded = reload_user_skills(directory=Path(d))
            self.assertEqual(len(loaded), 1)
            skill = get_skill(builtin_name)
            self.assertEqual(skill.source, "user")
            self.assertEqual(skill.title, "Benim TDD")
            # list_skills still exposes every builtin at least once.
            self.assertGreaterEqual(len(list_skills()), len(SKILLS))

    def test_list_and_get_include_user_skills(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "deploy-checklist.md").write_text(VALID_SKILL, encoding="utf-8")
            reload_user_skills(directory=Path(d))
            self.assertIsNotNone(get_skill("deploy-checklist"))
            self.assertIn("deployment", categories())
            items = list_skills("deployment")
            self.assertEqual([s.name for s in items], ["deploy-checklist"])
            self.assertIn("deploy-checklist", USER_SKILLS)

    def test_prompt_block_works_for_user_skill(self):
        skill = get_skill("root-cause-debug")
        block = skill.prompt_block("")
        self.assertIn("UYGULANAN BECERİ", block)


if __name__ == "__main__":
    unittest.main()


class UserSkillPolishTest(unittest.TestCase):
    def test_file_without_steps_or_description_is_skipped(self):
        import tempfile
        from src.skills import load_user_skills

        with tempfile.TemporaryDirectory() as directory:
            d = Path(directory)
            (d / "bos.md").write_text("---\nname: bos\n---\n", encoding="utf-8")
            (d / "iyi.md").write_text("---\nname: iyi\n---\n- tek adim\n", encoding="utf-8")
            skills, warnings = load_user_skills(d)
        self.assertEqual([s.name for s in skills], ["iyi"])
        self.assertTrue(any("bos.md" in w for w in warnings))

    def test_user_skills_load_lazily_on_first_lookup(self):
        import tempfile
        from unittest import mock
        import src.skills as skills_mod

        with tempfile.TemporaryDirectory() as directory:
            d = Path(directory)
            (d / "lazy.md").write_text("---\nname: lazy-one\n---\n- adim\n", encoding="utf-8")
            with mock.patch.object(skills_mod, "_USER_SKILLS_LOADED", False), \
                    mock.patch.object(skills_mod, "user_skills_dir", return_value=d):
                self.assertIsNotNone(skills_mod.get_skill("lazy-one"))
        skills_mod.reload_user_skills(Path(directory))  # leave the cache empty again
