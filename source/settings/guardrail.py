"""Detect data/settings.json being rebuilt from the default template.

Read-only helpers: they never write, migrate or normalize settings.
"""

import json
import os

from .defaults import DEFAULT_SETTINGS
from .store import SETTINGS_PATH

EMAIL_REPORT_RUNTIME_FIELDS = (
    "last_status",
    "last_success_at",
    "last_error",
    "last_sent_report_date",
)


def read_settings_file(path=SETTINGS_PATH):
    """Read settings.json as plain JSON without any side effects."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _strip_runtime_fields(section):
    """Drop runtime-only bookkeeping fields from an email_report section."""
    if not isinstance(section, dict):
        return section
    return {
        key: value
        for key, value in section.items()
        if key not in EMAIL_REPORT_RUNTIME_FIELDS
    }


def settings_rebuild_warnings(data):
    """Return warnings when user configuration partitions equal the template.

    Compares only the partitions that hold user secrets or high-frequency
    configuration: providers, ai_tasks and email_report. Runtime-only fields
    (admin_password/session_secret/fetch/...) and low-frequency partitions
    (prompts/personalization/schedule/webdav_backup) are intentionally ignored.
    """
    if not isinstance(data, dict):
        return []
    warnings = []
    defaults = json.loads(json.dumps(DEFAULT_SETTINGS))

    providers = data.get("providers") or {}
    if providers and providers == defaults.get("providers") and not any(
        str(config.get("api_key") or "")
        for config in providers.values()
    ):
        warnings.append(
            "providers 与默认模板完全一致且所有 api_key 为空："
            "settings.json 疑似被默认模板重建，AI 供应商配置可能已丢失"
        )

    if data.get("ai_tasks") and data["ai_tasks"] == defaults.get("ai_tasks"):
        warnings.append(
            "ai_tasks 与默认模板完全一致：AI 功能模型路由可能已被重置为默认值"
        )

    current_email = _strip_runtime_fields(data.get("email_report"))
    default_email = _strip_runtime_fields(defaults.get("email_report"))
    if current_email == default_email:
        warnings.append(
            "email_report 与默认模板完全一致：报告邮件发送设置可能已被重置为默认值"
        )

    return warnings


def warn_on_settings_rebuild(path=SETTINGS_PATH):
    """Return rebuild warnings for the real settings file, or [] when absent."""
    data = read_settings_file(path)
    if data is None:
        return []
    return settings_rebuild_warnings(data)
