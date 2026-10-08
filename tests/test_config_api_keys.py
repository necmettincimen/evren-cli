import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import config


class TestSaveApiKeys(unittest.TestCase):
    def test_roundtrip_preserves_other_fields(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            path.write_text(
                json.dumps({"ApiKeys": ["old"], "BaseUrl": "https://example.test", "Extra": 1}),
                encoding="utf-8",
            )
            with patch.object(config, "LEGACY_CONFIG_PATH", path):
                written = config.save_api_keys(["k1", "k2", "k1", "  "])
                self.assertEqual(written, path)
                data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["ApiKeys"], ["k1", "k2"])
            self.assertEqual(data["BaseUrl"], "https://example.test")
            self.assertEqual(data["Extra"], 1)


class TestLoadLegacyConfigKeys(unittest.TestCase):
    def test_loads_all_keys_into_env(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            path.write_text(
                json.dumps({"ApiKeys": ["evren_llm_aaa", "evren_llm_bbb"]}),
                encoding="utf-8",
            )
            with patch.object(config, "LEGACY_CONFIG_PATH", path):
                with patch.dict(os.environ, {}, clear=True):
                    config._load_legacy_evren_cli_config()
                    self.assertEqual(
                        os.environ.get("EVREN_API_KEYS"),
                        "evren_llm_aaa,evren_llm_bbb",
                    )
                    self.assertEqual(os.environ.get("EVREN_API_KEY"), "evren_llm_aaa")


if __name__ == "__main__":
    unittest.main()
