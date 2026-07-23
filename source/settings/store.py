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
    logger,
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


def _migrate_old_settings(data):
    """
    迁移旧版配置格式到新版多供应商格式。

    旧版配置将 api_key/base_url/model 等字段平铺在顶层，
    新版将其收纳到 providers 字典中，支持多供应商切换。

    如果数据已是新版格式（包含 "providers" 字段），直接返回。
    否则将旧版平铺字段重组为 providers[active_provider] 结构。

    参数:
        data: 从 settings.json 读取的原始字典

    返回:
        迁移后的配置字典（新版格式）
    """
    if "providers" in data:
        return data
    migrated = {
        "active_provider": data.get("provider", "deepseek"),
        "providers": {}
    }
    prov_key = data.get("provider", "deepseek")
    preset = PROVIDER_PRESETS.get(prov_key, {})
    migrated["providers"][prov_key] = {
        "name": preset.get("name", prov_key),
        "api_key": data.get("api_key", ""),
        "base_url": data.get("base_url", preset.get("base_url", "")),
        "model": data.get("model", ""),
        "temperature": data.get("temperature", 0.3),
        "max_tokens": data.get("max_tokens", 1000),
        "max_tokens_enabled": data.get("max_tokens_enabled", False),
        "is_thinking": data.get("is_thinking", False),
        "thinking_effort": data.get("thinking_effort", "medium"),
    }
    for key in (
        "concurrency",
        "per_page",
        "schedule",
        "proxy",
        "fetch",
        "admin_password",
        "session_secret",
        "personalization",
        "webdav_backup",
        "email_report",
        "prompts",
        "prompt_profiles",
        "ai_tasks",
    ):
        if key in data:
            migrated[key] = data[key]
    return migrated


def load_settings():
    """
    加载运行时配置。

    加载流程：
    1. 确保数据目录存在
    2. 如果配置文件不存在，写入默认配置并返回
    3. 读取配置文件，执行旧版格式迁移
    4. 以 DEFAULT_SETTINGS 为基础，用文件中的值覆盖（合并逻辑）
    5. 补齐新增的供应商参数开关，兼容旧配置

    注意：添加新配置字段时，必须在此函数的合并逻辑中显式添加对应的
    if "key" in migrated 判断，否则新字段在读取时会被 DEFAULT_SETTINGS
    的默认值覆盖而丢失！这是已踩过的坑。

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
        # 深拷贝默认配置作为合并基础
        merged = json.loads(json.dumps(DEFAULT_SETTINGS))
        # 逐字段合并：文件中有的字段覆盖默认值
        merged["active_provider"] = migrated.get("active_provider", "deepseek")
        if "concurrency" in migrated:
            merged["concurrency"] = migrated["concurrency"]
        if "per_page" in migrated:
            merged["per_page"] = migrated["per_page"]
        if "session_secret" in migrated:
            merged["session_secret"] = migrated["session_secret"]
        if "personalization" in migrated:
            merged["personalization"] = _normalize_personalization_config(migrated["personalization"])
        if "webdav_backup" in migrated:
            merged["webdav_backup"] = _normalize_webdav_backup_config(migrated["webdav_backup"])
        if "email_report" in migrated:
            merged["email_report"] = _normalize_email_report_config(migrated["email_report"])
        if "schedule" in migrated:
            merged["schedule"] = _normalize_schedule(migrated["schedule"])
        if "proxy" in migrated:
            merged["proxy"] = migrated["proxy"]
        if "fetch" in migrated:
            merged["fetch"] = _normalize_fetch_config(migrated["fetch"])
        if "admin_password" in migrated:
            merged["admin_password"] = migrated["admin_password"]
        if "prompts" in migrated:
            merged["prompts"] = migrated["prompts"]
        # 合并供应商配置，同时补齐新增参数开关
        for k, v in migrated.get("providers", {}).items():
            merged["providers"][k] = normalize_provider_config(v, k)
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
