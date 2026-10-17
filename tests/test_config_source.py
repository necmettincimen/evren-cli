"""Tests for the startup config / API key source report (src/config.py)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from src import config
from src.ui import print_banner


class ConfigSourceReportTest(unittest.TestCase):
    def report(self, **overrides):
        patches = {
            "_PREEXISTING_KEY_ENV": False,
            "ENV_FILE_PATH": None,
            "LEGACY_KEYS_USED": 0,
            "EVREN_API_KEY": "",
        }
        patches.update(overrides)
        with ExitStack() as stack:
            for name, value in patches.items():
                stack.enter_context(patch.object(config, name, value))
            stack.enter_context(patch.object(config, "LEGACY_CONFIG_PATH", self.config_path))
            return config.config_source_report(pool_size=2)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.config_path = self.root / "config.json"

    def test_process_environment_wins(self):
        report = self.report(
            _PREEXISTING_KEY_ENV=True,
            ENV_FILE_PATH=self.root / ".env",
            LEGACY_KEYS_USED=3,
        )
        self.assertIn("süreç ortam değişkeni", report["key_source"])
        self.assertNotIn(".env", report["key_source"])

    def test_config_json_is_named_when_it_supplied_the_keys(self):
        self.config_path.write_text(json.dumps({"ApiKeys": ["a", "b"]}), encoding="utf-8")
        report = self.report(LEGACY_KEYS_USED=2)
        self.assertIn(str(self.config_path), report["key_source"])
        self.assertIn("2 anahtar", report["key_source"])
        self.assertTrue(report["config_json_exists"])

    def test_env_file_is_named_when_it_supplied_the_keys(self):
        env = self.root / ".env"
        report = self.report(ENV_FILE_PATH=env)
        self.assertEqual(report["key_source"], f"{env} (EVREN_API_KEY / EVREN_API_KEYS)")
        self.assertEqual(report["env_file"], str(env))

    def test_missing_everything_reports_not_found(self):
        report = self.report()
        self.assertEqual(report["key_source"], "bulunamadı")
        self.assertEqual(report["env_file"], "")
        self.assertFalse(report["config_json_exists"])
        self.assertEqual(report["key_count"], 2)

    def test_report_never_contains_key_values(self):
        secret = "evren_llm_SAKLIDEGER123"  # check_text: ignore
        self.config_path.write_text(json.dumps({"ApiKeys": [secret]}), encoding="utf-8")
        report = self.report(LEGACY_KEYS_USED=1, EVREN_API_KEY=secret)
        dumped = json.dumps(report, ensure_ascii=False)
        self.assertNotIn(secret, dumped)
        # The path is reported (JSON escapes backslashes, so compare the value).
        self.assertEqual(report["config_json"], str(self.config_path))
        self.assertIn(str(self.config_path), report["key_source"])

    def test_candidates_are_listed_for_diagnostics(self):
        report = self.report()
        self.assertEqual(len(report["env_candidates"]), 3)
        # First candidate is always the current working directory's .env.
        self.assertEqual(Path(report["env_candidates"][0]).name, ".env")
        self.assertTrue(all(path.endswith(".env") for path in report["env_candidates"]))


class BannerSourceTest(unittest.TestCase):
    def test_banner_prints_paths_without_key_values(self):
        secret = "evren_llm_GIZLIDEGERSAKLA"  # check_text: ignore
        info = {
            "env_file": r"C:\proje\.env",
            "config_json": r"C:\Users\test\.evren-cli\config.json",
            "config_json_exists": True,
            "key_source": r"C:\proje\.env (EVREN_API_KEY / EVREN_API_KEYS)",
            "key_count": 1,
        }
        captured = []
        with patch("src.ui.HAVE_RICH", False), patch("builtins.print", lambda *a, **k: captured.append(" ".join(str(x) for x in a))):
            print_banner("model-x", r"C:\proje", "1000", info)
        text = "\n".join(captured)
        self.assertIn(".env=.env", text)      # dosya adı biçiminde kısaltıldı
        self.assertIn("config.json", text)
        self.assertIn("anahtar=.env", text)   # aynı yol tekrar yazılmıyor
        self.assertIn("(1 anahtar)", text)
        self.assertNotIn(secret, text)

    def test_banner_without_config_info_still_works(self):
        captured = []
        with patch("src.ui.HAVE_RICH", False), \
                patch("src.config.config_source_report", side_effect=RuntimeError("bozuk")), \
                patch("builtins.print", lambda *a, **k: captured.append(" ".join(str(x) for x in a))):
            print_banner("model-x", r"C:\proje")
        self.assertIn("EVREN CLI", "\n".join(captured))

    def test_banner_config_source_fits_single_line(self):
        info = {
            "env_file": r"C:\proje\.env",
            "config_json": r"C:\Users\test\.evren-cli\config.json",
            "config_json_exists": True,
            "key_source": r"C:\proje\.env (EVREN_API_KEY / EVREN_API_KEYS)",
            "key_count": 2,
        }
        captured = []
        with patch("src.ui.HAVE_RICH", False), \
                patch("src.version.get_changelog_notes", return_value=[]), \
                patch("builtins.print", lambda *a, **k: captured.append(" ".join(str(x) for x in a))):
            print_banner("model-x", r"C:\proje", "1000", info)
        text = "\n".join(captured)
        config_lines = [line for line in captured if line.startswith("Yapılandırma:")]
        self.assertEqual(len(config_lines), 1)
        self.assertIn(".env=.env", config_lines[0])
        self.assertIn("anahtar=.env", config_lines[0])
        self.assertIn("(2 anahtar)", config_lines[0])
        self.assertLessEqual(len(config_lines[0]), 80)  # satır taşmasın
        # Eski 3 satırlık biçim geri gelmemeli.
        self.assertNotIn("API anahtarı:", text)
        self.assertNotIn("config.json:", text)

    def test_release_notes_stripped_of_markdown_and_capped(self):
        from src.ui import _clean_release_note, _shorten_path

        note = _clean_release_note(
            "**Kullanıcı tanımlı beceriler:** `<cli_home>/skills/*.md` ile eklenir ve "
            "birçok uzun kelime ile satırı taşırmaya devam eder böylece kırpma sınırı test edilir"
        )
        self.assertNotIn("**", note)
        self.assertNotIn("`", note)
        self.assertLessEqual(len(note), 72)
        self.assertTrue(note.endswith("…"))
        self.assertEqual(_clean_release_note("**Kısa madde**"), "Kısa madde")

    def test_shorten_path_uses_home_and_clips_head(self):
        from src.ui import _shorten_path

        home = str(Path.home())
        self.assertEqual(_shorten_path(home), "~")
        self.assertEqual(_shorten_path(home + r"\proje\.env"), "~\\proje\\.env")
        long_path = "C:\\" + "a" * 20 + "\\" + "b" * 80
        short = _shorten_path(long_path, limit=40)
        self.assertEqual(len(short), 40)
        self.assertTrue(short.startswith("…"))
        self.assertTrue(long_path.endswith(short[1:]))


class ReplHelperTest(unittest.TestCase):
    def test_config_source_info_uses_pool_size(self):
        from src.repl import _config_source_info

        client = type("C", (), {"key_pool": type("P", (), {"keys": [1, 2, 3]})()})()
        info = _config_source_info(client)
        self.assertEqual(info["key_count"], 3)
        self.assertIn("key_source", info)

    def test_config_source_info_accepts_method_style_keys(self):
        from src.repl import _config_source_info

        class Pool:
            def keys(self):
                return [1, 2]

        client = type("C", (), {"key_pool": Pool()})()
        self.assertEqual(_config_source_info(client)["key_count"], 2)

    def test_config_source_info_without_client_uses_environment(self):
        from src.repl import _config_source_info

        info = _config_source_info(None)
        self.assertIn("key_count", info)

    def test_config_source_info_survives_failure(self):
        from src.repl import _config_source_info

        with patch("src.config.config_source_report", side_effect=RuntimeError("bozuk")):
            self.assertEqual(_config_source_info(None), {})


class RealProcessTest(unittest.TestCase):
    def test_env_file_is_reported_in_a_clean_process(self):
        """End-to-end: a child process with no key env vars reads .env (or config.json)."""
        env = {k: v for k, v in os.environ.items()
               if k not in ("EVREN_API_KEY", "EVREN_API_KEYS")}
        code = (
            "from src.config import config_source_report as r;"
            "x=r();print(x['key_count']);print(x['key_source'])"
        )
        out = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, env=env, check=True,
        ).stdout.strip().splitlines()
        self.assertTrue(int(out[0]) >= 0)
        self.assertTrue(
            ".env" in out[1] or "config.json" in out[1] or "bulunamadı" in out[1],
            out[1],
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
