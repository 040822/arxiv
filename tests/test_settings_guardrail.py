"""Guardrail tests: detect settings.json rebuilt from the default template."""

import json
import pathlib
import unittest

from source.settings.defaults import DEFAULT_SETTINGS
from source.settings.guardrail import (
    read_settings_file,
    settings_rebuild_warnings,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SETTINGS_FILE = REPO_ROOT / "data" / "settings.json"


def _healthy_settings():
    """A settings dict shaped like a real user-configured file."""
    settings = json.loads(json.dumps(DEFAULT_SETTINGS))
    settings["providers"]["deepseek"]["api_key"] = "sk-" + "a" * 32
    settings["providers"]["deepseek"]["available_models"] = [
        "deepseek-v4-flash", "deepseek-v4-pro",
    ]
    for task in settings["ai_tasks"].values():
        task["model"] = "deepseek-v4-flash"
    settings["email_report"].update({
        "enabled": True,
        "smtp_host": "smtp.example.com",
        "smtp_port": 465,
        "security": "ssl",
        "username": "sender@example.com",
        "password": "secret",
        "sender": "sender@example.com",
        "recipients": ["reader@example.com"],
        "last_status": "success",
        "last_success_at": "2026-08-10 10:00:00",
    })
    return settings


class SettingsGuardrailTests(unittest.TestCase):
    def test_template_copy_raises_all_rebuild_warnings(self):
        settings = json.loads(json.dumps(DEFAULT_SETTINGS))
        warnings = settings_rebuild_warnings(settings)
        self.assertEqual(len(warnings), 3)

    def test_healthy_settings_raise_no_warnings(self):
        self.assertEqual(settings_rebuild_warnings(_healthy_settings()), [])

    def test_email_reset_with_runtime_fields_still_warns(self):
        settings = _healthy_settings()
        settings["email_report"] = {
            **json.loads(json.dumps(DEFAULT_SETTINGS["email_report"])),
            "last_status": "success",
            "last_success_at": "2026-08-10 10:00:00",
        }
        warnings = settings_rebuild_warnings(settings)
        self.assertTrue(any("email_report" in warning for warning in warnings))

    def test_rebuilt_providers_without_key_warns_with_custom_rest(self):
        settings = _healthy_settings()
        settings["providers"] = json.loads(json.dumps(DEFAULT_SETTINGS["providers"]))
        warnings = settings_rebuild_warnings(settings)
        self.assertTrue(any("providers" in warning for warning in warnings))

    def test_real_settings_file_passes_guardrail(self):
        if not SETTINGS_FILE.exists():
            self.skipTest("真实 data/settings.json 不存在（CI/新环境）")
        data = read_settings_file(str(SETTINGS_FILE))
        warnings = settings_rebuild_warnings(data)
        self.assertEqual(
            warnings,
            [],
            "data/settings.json 疑似被默认模板重建。如为主动清空配置请忽略；"
            "否则请从备份恢复。" + " ".join(warnings),
        )


if __name__ == "__main__":
    unittest.main()
