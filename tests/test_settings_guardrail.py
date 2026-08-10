"""Guardrail tests: detect settings.json rebuilt from the default template.

这些测试只验证 guardrail 判定逻辑本身（构造数据），不读取真实
data/settings.json——真实文件的检查由 scripts/check_settings_guardrail.py
与 create_app() 启动告警承担。
"""

import json
import unittest

from source.settings.defaults import DEFAULT_SETTINGS
from source.settings.guardrail import settings_rebuild_warnings


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


if __name__ == "__main__":
    unittest.main()
