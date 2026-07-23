"""Settings store implementation."""

import hashlib
import json
import logging
import os
import re
import secrets
from string import Formatter

from config import (
    DB_DIR,
    FETCH_BATCH_DAYS,
    FETCH_BATCH_DELAY,
    FETCH_REQUEST_DELAY,
    SCHEDULE_HOUR,
    SCHEDULE_MINUTE,
)

logger = logging.getLogger(__name__)

from .defaults import (
    PROVIDER_PRESETS,
    THINKING_EFFORTS,
    THINKING_BUDGETS,
    OPENAI_REASONING_PREFIXES,
    REQUIRED_PROMPT_FIELDS,
    AI_TASK_KEYS,
    AI_TASK_LABELS,
    DEFAULT_PROVIDER_OPTIONS,
    DEFAULT_AI_TASK_OPTIONS,
    DEFAULT_SYSTEM_PROMPT,
    DEFAULT_BASIC_ANALYSIS_INSTRUCTION,
    DEFAULT_DEEP_READING_QUESTIONS,
    _build_deep_reading_instruction,
    DEFAULT_DEEP_READING_INSTRUCTION,
    DEFAULT_REPORT_SUMMARY_INSTRUCTION,
    DEFAULT_RECOMMENDATION_INSTRUCTION,
    DEFAULT_PAPER_CHAT_SYSTEM_PROMPT,
    DEFAULT_PAPER_CHAT_INSTRUCTION,
    DEFAULT_PAPER_QUIZ_SYSTEM_PROMPT,
    DEFAULT_PAPER_QUIZ_INSTRUCTION,
    DEFAULT_PROMPT_PROFILES,
    DEFAULT_SETTINGS,
)
from .normalize import (
    _as_bool,
    _as_float,
    _as_int,
    _normalize_schedule,
    _normalize_fetch_config,
    _normalize_personalization_config,
    _normalize_webdav_backup_config,
    _normalize_email_recipients,
    _normalize_email_report_config,
    _select_provider_key,
    _normalize_ai_task_config,
    _normalize_ai_tasks,
    _extract_deep_reading_questions,
    _is_legacy_deep_reading_instruction,
    _is_weak_qa_only_deep_reading_instruction,
    _migrate_deep_reading_instruction,
    _is_legacy_basic_analysis_instruction,
    _migrate_basic_analysis_instruction,
    _normalize_prompt_profiles,
    _model_leaf,
    is_openai_reasoning_model,
    _is_deepseek_v4_model,
    _is_deepseek_reasoning_model,
    _is_qwen_like_model,
    _is_openai_base_url,
    _is_deepseek_base_url,
    _is_qwen_like_base_url,
    get_thinking_protocol,
    is_known_thinking_model,
    normalize_provider_config,
    _normalize_effort,
    _map_openai_effort,
    _map_deepseek_effort,
    _map_thinking_budget,
    _should_use_max_completion_tokens,
    build_chat_completion_kwargs,
)


SETTINGS_PATH = os.path.join(DB_DIR, "settings.json")


def _ensure_dir():
    """确保数据目录存在，如果不存在则递归创建。"""
    os.makedirs(DB_DIR, exist_ok=True)


def _deep_merge(defaults, overrides):
    """Return a deep copy of defaults recursively overlaid by user values."""
    if not isinstance(defaults, dict) or not isinstance(overrides, dict):
        return json.loads(json.dumps(overrides))

    merged = json.loads(json.dumps(defaults))
    for key, value in overrides.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = json.loads(json.dumps(value))
    return merged


def _migrate_old_settings(data):
    """Migrate legacy flat provider fields while preserving unrelated fields."""
    if "providers" in data:
        return data

    migrated = dict(data)
    provider_key = data.get("provider", "deepseek")
    preset = PROVIDER_PRESETS.get(provider_key, {})
    migrated["active_provider"] = provider_key
    migrated["providers"] = {
        provider_key: {
            "name": preset.get("name", provider_key),
            "api_key": data.get("api_key", ""),
            "base_url": data.get("base_url", preset.get("base_url", "")),
            "model": data.get("model", ""),
            "temperature": data.get("temperature", 0.3),
            "max_tokens": data.get("max_tokens", 1000),
            "max_tokens_enabled": data.get("max_tokens_enabled", False),
            "is_thinking": data.get("is_thinking", False),
            "thinking_effort": data.get("thinking_effort", "medium"),
        }
    }
    for key in (
        "provider", "api_key", "base_url", "model", "temperature",
        "max_tokens", "max_tokens_enabled", "is_thinking", "thinking_effort",
    ):
        migrated.pop(key, None)
    return migrated


def load_settings():
    """
    加载运行时配置。

    加载流程：
    1. 确保数据目录存在
    2. 如果配置文件不存在，写入默认配置并返回
    3. 读取配置文件，执行旧版格式迁移
    4. 以 DEFAULT_SETTINGS 为基础，递归合并文件中的值
    5. 补齐新增的供应商参数开关，兼容旧配置

    普通配置字段由递归合并自动保留；需要迁移、归一化或密码保留语义的
    字段仍应在对应逻辑中显式处理。

    返回:
        合并后的完整配置字典
    """
    _ensure_dir()
    if not os.path.exists(SETTINGS_PATH):
        default_settings = json.loads(json.dumps(DEFAULT_SETTINGS))
        default_settings["ai_tasks"] = _normalize_ai_tasks(
            default_settings.get("ai_tasks", {}),
            default_settings.get("active_provider", ""),
            default_settings.get("providers", {}),
        )
        default_settings["prompt_profiles"] = _normalize_prompt_profiles(default_settings.get("prompt_profiles", {}))
        save_settings(default_settings)
        return default_settings
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
        # 执行旧版格式迁移
        migrated = _migrate_old_settings(saved)
        merged = _deep_merge(DEFAULT_SETTINGS, migrated)
        merged["personalization"] = _normalize_personalization_config(merged.get("personalization", {}))
        merged["webdav_backup"] = _normalize_webdav_backup_config(merged.get("webdav_backup", {}))
        merged["email_report"] = _normalize_email_report_config(merged.get("email_report", {}))
        merged["schedule"] = _normalize_schedule(merged.get("schedule", {}))
        merged["fetch"] = _normalize_fetch_config(merged.get("fetch", {}))
        merged["providers"] = {
            key: normalize_provider_config(config, key)
            for key, config in merged.get("providers", {}).items()
        }
        merged["ai_tasks"] = _normalize_ai_tasks(
            migrated.get("ai_tasks", {}),
            merged.get("active_provider", ""),
            merged.get("providers", {}),
        )
        merged["prompt_profiles"] = _normalize_prompt_profiles(
            migrated.get("prompt_profiles", {}),
            migrated.get("prompts", {}),
        )
        return merged
    except Exception as e:
        logger.error(f"Failed to load settings: {e}")
        return json.loads(json.dumps(DEFAULT_SETTINGS))


def save_settings(settings):
    """
    将配置字典保存到 settings.json 文件。

    使用 ensure_ascii=False 保留中文字符，indent=2 格式化输出便于手动编辑。

    参数:
        settings: 要保存的配置字典

    返回:
        bool: 保存成功返回 True，失败返回 False
    """
    _ensure_dir()
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Failed to save settings: {e}")
        return False


def get_session_secret():
    """
    获取 Flask session 签名密钥。

    优先复用 settings.json 中已保存的密钥；如果旧配置没有该字段，则生成
    一个随机密钥并写回配置文件，保证服务重启后已有登录 cookie 仍可验证。
    """
    settings = load_settings()
    secret = settings.get("session_secret", "")
    if not secret:
        secret = secrets.token_hex(32)
        settings["session_secret"] = secret
        save_settings(settings)
    return secret
