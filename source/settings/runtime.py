"""Settings runtime implementation."""

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

from .store import load_settings, save_settings


def get_concurrency():
    """
    获取 AI 分析的并发请求数。

    并发数控制 ThreadPoolExecutor 同时发起的 API 请求数量，
    默认值为 5。过大会触发 API 速率限制，过小则分析速度慢。

    返回:
        int: 并发请求数
    """
    settings = load_settings()
    return settings.get("concurrency", 5)


def get_per_page():
    """
    获取 Web 界面每页显示的论文数量。

    返回:
        int: 每页论文数，默认 20
    """
    settings = load_settings()
    return settings.get("per_page", 20)


def get_fetch_config():
    """
    获取论文抓取的运行时配置。

    抓取配置控制 arXiv API 的请求行为：
    - request_delay: 两次 API 请求之间的间隔秒数（避免被限流）
    - batch_days: 每个批次抓取的天数范围
    - batch_delay: 两个批次之间的间隔秒数

    如果用户未自定义，使用 config.py 中的默认值。

    返回:
        dict: 包含 request_delay/batch_days/batch_delay 的配置字典
    """
    settings = load_settings()
    return _normalize_fetch_config(settings.get("fetch", {}))


def get_personalization_config():
    """
    获取个性化推荐配置。

    返回:
        dict: {"research_interests": "..."}，空字符串表示关闭个性化推荐。
    """
    settings = load_settings()
    return _normalize_personalization_config(settings.get("personalization", {}))


def get_research_interest_hash(interests=None):
    """
    获取研究兴趣文本的稳定哈希。

    空兴趣返回空字符串；非空兴趣先按保存规则 trim/截断，再计算 SHA-256。
    """
    if interests is None:
        interests = get_personalization_config().get("research_interests", "")
    normalized = _normalize_personalization_config({"research_interests": interests})
    text = normalized.get("research_interests", "")
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def save_personalization_config(personalization_config):
    """
    保存个性化推荐配置。

    仅保存用户研究兴趣文本，不触发历史论文推荐分重算。
    """
    settings = load_settings()
    settings["personalization"] = _normalize_personalization_config(personalization_config)
    return save_settings(settings)


def get_webdav_backup_config(mask_password=False):
    """
    获取 WebDAV 云备份配置。

    参数:
        mask_password: 为 True 时不返回明文 password，仅返回 password_masked。
    """
    settings = load_settings()
    config = _normalize_webdav_backup_config(settings.get("webdav_backup", {}))
    if not mask_password:
        return config
    masked = dict(config)
    password = masked.pop("password", "")
    masked["password_masked"] = "******" if password else ""
    return masked


def save_webdav_backup_config(webdav_config):
    """
    保存 WebDAV 云备份配置。

    前端密码字段为空时保留旧密码，避免每次保存都要求重新输入。
    """
    settings = load_settings()
    current = _normalize_webdav_backup_config(settings.get("webdav_backup", {}))
    webdav_config = dict(webdav_config or {})
    for key in ("last_status", "last_success_at", "last_error", "last_uploaded_file"):
        if key not in webdav_config:
            webdav_config[key] = current.get(key, "")
    settings["webdav_backup"] = _normalize_webdav_backup_config(
        webdav_config,
        existing_password=current.get("password", ""),
    )
    return save_settings(settings)


def update_webdav_backup_status(status, error="", uploaded_file=""):
    """更新最近一次 WebDAV 备份状态。"""
    settings = load_settings()
    config = _normalize_webdav_backup_config(settings.get("webdav_backup", {}))
    config["last_status"] = str(status or "").strip()
    config["last_error"] = str(error or "").strip()
    if uploaded_file:
        config["last_uploaded_file"] = str(uploaded_file).strip()
    if status == "success":
        from datetime import datetime
        config["last_success_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        config["last_error"] = ""
    settings["webdav_backup"] = config
    return save_settings(settings)


def get_email_report_config(mask_password=False):
    """
    获取每日报告邮件配置。

    参数:
        mask_password: 为 True 时不返回明文 password，仅返回 password_masked。
    """
    settings = load_settings()
    config = _normalize_email_report_config(settings.get("email_report", {}))
    if not mask_password:
        return config
    masked = dict(config)
    password = masked.pop("password", "")
    masked["password_masked"] = "******" if password else ""
    return masked


def save_email_report_config(email_config):
    """
    保存每日报告邮件配置。

    前端密码字段为空时保留旧密码，避免每次保存都要求重新输入。
    """
    settings = load_settings()
    current = _normalize_email_report_config(settings.get("email_report", {}))
    email_config = dict(email_config or {})
    for key in ("last_status", "last_success_at", "last_error", "last_sent_report_date"):
        if key not in email_config:
            email_config[key] = current.get(key, "")
    settings["email_report"] = _normalize_email_report_config(
        email_config,
        existing_password=current.get("password", ""),
    )
    return save_settings(settings)


def update_email_report_status(status, error="", report_date=""):
    """更新最近一次报告邮件发送状态；仅成功发送可更新去重日期。"""
    settings = load_settings()
    config = _normalize_email_report_config(settings.get("email_report", {}))
    config["last_status"] = str(status or "").strip()
    config["last_error"] = str(error or "").strip()
    if status == "success":
        from datetime import datetime
        if report_date:
            config["last_sent_report_date"] = str(report_date).strip()
        config["last_success_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        config["last_error"] = ""
    settings["email_report"] = config
    return save_settings(settings)


def get_schedule_config():
    """
    获取每日自动任务配置。

    返回:
        dict: enabled/hour/minute，默认来自 config.py 的 SCHEDULE_HOUR/MINUTE
    """
    settings = load_settings()
    return _normalize_schedule(settings.get("schedule", {}))


def save_schedule_config(schedule_config):
    """
    保存每日自动任务配置到 settings.json。

    参数:
        schedule_config: 包含 enabled/hour/minute 的字典

    返回:
        bool: 保存是否成功
    """
    settings = load_settings()
    settings["schedule"] = _normalize_schedule(schedule_config)
    return save_settings(settings)


def save_fetch_config(fetch_config):
    """
    保存论文抓取配置到 settings.json。

    参数:
        fetch_config: 包含 request_delay/batch_days/batch_delay 的字典

    返回:
        bool: 保存是否成功
    """
    settings = load_settings()
    settings["fetch"] = _normalize_fetch_config(fetch_config)
    return save_settings(settings)


def get_proxy_config():
    """
    获取 HTTP 代理配置。

    代理用于在无法直接访问 arXiv/OpenAI 等服务时进行网络转发。
    配置项包括是否启用、HTTP 代理地址、HTTPS 代理地址。

    返回:
        dict: 包含以下字段的代理配置：
            - enabled: bool, 是否启用代理
            - http: str, HTTP 代理地址（如 "http://127.0.0.1:7890"）
            - https: str, HTTPS 代理地址
    """
    settings = load_settings()
    proxy = settings.get("proxy", {})
    return {
        "enabled": proxy.get("enabled", False),
        "http": proxy.get("http", ""),
        "https": proxy.get("https", ""),
    }


def save_proxy_config(proxy_config):
    """
    保存代理配置到 settings.json。

    参数:
        proxy_config: 包含 enabled/http/https 的代理配置字典

    返回:
        bool: 保存是否成功
    """
    settings = load_settings()
    settings["proxy"] = proxy_config
    return save_settings(settings)


def get_admin_password():
    """
    获取管理员密码的 SHA-256 哈希值。

    密码以哈希形式存储，不保存明文。
    空字符串表示未设置密码（无需验证）。

    返回:
        str: 密码的 SHA-256 哈希值，未设置时返回空字符串
    """
    settings = load_settings()
    return settings.get("admin_password", "")


def set_admin_password(password):
    """
    设置管理员密码。

    密码使用 SHA-256 哈希后存储，传入空字符串则清除密码。

    参数:
        password: 要设置的明文密码，空字符串表示清除密码

    返回:
        bool: 保存是否成功
    """
    import hashlib
    settings = load_settings()
    if password:
        settings["admin_password"] = hashlib.sha256(password.encode()).hexdigest()
    else:
        settings["admin_password"] = ""
    return save_settings(settings)


def verify_admin_password(password):
    """
    验证管理员密码是否正确。

    如果未设置密码（哈希值为空），直接返回 True（无需验证）。
    否则将输入密码哈希后与存储的哈希值比较。

    参数:
        password: 待验证的明文密码

    返回:
        bool: 密码正确或未设置密码时返回 True
    """
    import hashlib
    stored = get_admin_password()
    if not stored:
        return True
    return hashlib.sha256(password.encode()).hexdigest() == stored


def has_admin_password():
    """
    检查是否已设置管理员密码。

    返回:
        bool: 已设置密码返回 True，未设置返回 False
    """
    return bool(get_admin_password())
