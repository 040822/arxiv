import json
import os
import tempfile
import unittest
from unittest.mock import patch

from source.settings import load_settings, store


class SettingsMergeTests(unittest.TestCase):
    def test_new_top_level_fields_survive_loading_without_a_whitelist(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "settings.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({
                    "active_provider": "deepseek",
                    "providers": {
                        "deepseek": {
                            "name": "DeepSeek",
                            "api_key": "secret",
                            "base_url": "https://api.deepseek.com",
                            "model": "deepseek-chat",
                        },
                    },
                    "future_feature": {
                        "enabled": True,
                        "strategy": "experimental",
                    },
                }, handle)

            with patch.object(store, "SETTINGS_PATH", path):
                loaded = load_settings()

        self.assertEqual(loaded["future_feature"], {
            "enabled": True,
            "strategy": "experimental",
        })


    def test_legacy_provider_migration_preserves_unrelated_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "settings.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({
                    "provider": "deepseek",
                    "api_key": "legacy-secret",
                    "model": "deepseek-chat",
                    "future_feature": {"enabled": True},
                }, handle)

            with patch.object(store, "SETTINGS_PATH", path):
                loaded = load_settings()

        self.assertEqual(loaded["future_feature"], {"enabled": True})
        self.assertEqual(loaded["providers"]["deepseek"]["api_key"], "legacy-secret")
        self.assertNotIn("api_key", loaded)


if __name__ == "__main__":
    unittest.main()
