"""Settings normalize implementation."""

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
from .coercion import _as_bool, _as_float, _as_int
from .thinking import normalize_provider_config


def _normalize_schedule(schedule):
    """补齐并约束内置日报任务配置。"""
    schedule = dict(schedule or {})
    valid_days = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
    raw_days = schedule.get("days_of_week", valid_days)
    if not isinstance(raw_days, (list, tuple)):
        raw_days = valid_days
    days_of_week = []
    for day in valid_days:
        if day in raw_days:
            days_of_week.append(day)
    if not days_of_week:
        days_of_week = list(valid_days)
    hour = _as_int(schedule.get("hour"), SCHEDULE_HOUR)
    minute = _as_int(schedule.get("minute"), SCHEDULE_MINUTE)
    fetch_days = _as_int(schedule.get("fetch_days"), 3)
    analyze_limit = _as_int(schedule.get("analyze_limit"), 1000)
    fetch_retry_interval_minutes = _as_int(schedule.get("fetch_retry_interval_minutes"), 10)
    fetch_max_retries = _as_int(schedule.get("fetch_max_retries"), 20)
    return {
        "enabled": _as_bool(schedule.get("enabled"), True),
        "days_of_week": days_of_week,
        "hour": max(0, min(23, hour)),
        "minute": max(0, min(59, minute)),
        "fetch_days": max(1, min(3650, fetch_days)),
        "analyze_limit": max(1, min(10000, analyze_limit)),
        "fetch_retry_interval_minutes": max(1, min(1440, fetch_retry_interval_minutes)),
        "fetch_max_retries": max(0, min(100, fetch_max_retries)),
    }


def _normalize_fetch_config(fetch):
    """补齐并约束 arXiv 抓取配置。"""
    fetch = dict(fetch or {})
    request_delay = _as_float(fetch.get("request_delay"), FETCH_REQUEST_DELAY)
    batch_days = _as_int(fetch.get("batch_days"), FETCH_BATCH_DAYS)
    batch_delay = _as_float(fetch.get("batch_delay"), FETCH_BATCH_DELAY)
    return {
        "request_delay": max(3.0, min(300.0, request_delay)),
        "batch_days": max(1, min(365, batch_days)),
        "batch_delay": max(1.0, min(1800.0, batch_delay)),
    }


def _normalize_personalization_config(config):
    """补齐并约束个性化推荐配置。"""
    config = dict(config or {})
    interests = str(config.get("research_interests") or "").strip()
    return {
        "research_interests": interests[:4000],
    }


def _normalize_webdav_backup_config(config, existing_password=None):
    """补齐并约束 WebDAV 云备份配置。"""
    config = dict(config or {})
    password = config.get("password")
    if password in (None, "") and existing_password is not None:
        password = existing_password
    history_days = _as_int(config.get("history_days"), 3)
    return {
        "enabled": _as_bool(config.get("enabled"), False),
        "url": str(config.get("url") or "").strip(),
        "username": str(config.get("username") or "").strip(),
        "password": str(password or ""),
        "remote_dir": str(config.get("remote_dir") or "arxiv-backups").strip().strip("/") or "arxiv-backups",
        "history_days": max(1, min(3650, history_days)),
        "last_status": str(config.get("last_status") or "").strip(),
        "last_success_at": str(config.get("last_success_at") or "").strip(),
        "last_error": str(config.get("last_error") or "").strip(),
        "last_uploaded_file": str(config.get("last_uploaded_file") or "").strip(),
    }


def _normalize_email_recipients(value):
    """将逗号/分号/换行分隔的收件人规范化为列表。"""
    if isinstance(value, str):
        parts = re.split(r"[,;\n\r]+", value)
    elif isinstance(value, (list, tuple)):
        parts = value
    else:
        parts = []
    recipients = []
    seen = set()
    for part in parts:
        email = str(part or "").strip()
        if not email or email in seen:
            continue
        recipients.append(email)
        seen.add(email)
    return recipients


def _normalize_email_report_config(config, existing_password=None):
    """补齐并约束每日报告邮件配置。"""
    config = dict(config or {})
    password = config.get("password")
    if password in (None, "") and existing_password is not None:
        password = existing_password
    security = str(config.get("security") or "starttls").strip().lower()
    if security not in {"starttls", "ssl", "none"}:
        security = "starttls"
    default_port = 465 if security == "ssl" else 587
    smtp_port = _as_int(config.get("smtp_port"), default_port)
    subject_template = str(config.get("subject_template") or "").strip()
    if not subject_template:
        subject_template = DEFAULT_SETTINGS["email_report"]["subject_template"]
    site_url = str(config.get("site_url") or "").strip().rstrip("/")
    important_score_threshold = max(
        0, min(100, _as_int(config.get("important_score_threshold"), 80))
    )
    overview_limit = max(0, min(50, _as_int(config.get("overview_limit"), 20)))
    return {
        "enabled": _as_bool(config.get("enabled"), False),
        "smtp_host": str(config.get("smtp_host") or "").strip(),
        "smtp_port": max(1, min(65535, smtp_port)),
        "security": security,
        "username": str(config.get("username") or "").strip(),
        "password": str(password or ""),
        "sender": str(config.get("sender") or "").strip(),
        "recipients": _normalize_email_recipients(config.get("recipients")),
        "subject_template": subject_template[:300],
        "site_url": site_url,
        "important_score_threshold": important_score_threshold,
        "overview_limit": overview_limit,
        "last_status": str(config.get("last_status") or "").strip(),
        "last_success_at": str(config.get("last_success_at") or "").strip(),
        "last_error": str(config.get("last_error") or "").strip(),
        "last_sent_report_date": str(config.get("last_sent_report_date") or "").strip(),
    }


def _select_provider_key(provider_key, active_provider, providers):
    """选择一个存在的供应商 key，用于任务级路由。"""
    providers = providers or {}
    provider_key = str(provider_key or "").strip()
    if provider_key in providers:
        return provider_key
    if active_provider in providers:
        return active_provider
    return next(iter(providers.keys()), provider_key)


def _normalize_ai_task_config(task, task_key, active_provider, providers):
    """补齐并约束单个 AI 任务的模型和参数配置。"""
    task = dict(task or {})
    defaults = DEFAULT_AI_TASK_OPTIONS.get(task_key, DEFAULT_AI_TASK_OPTIONS["basic_analysis"])
    provider_key = _select_provider_key(
        task.get("provider_key") or defaults.get("provider_key"),
        active_provider,
        providers,
    )
    provider = normalize_provider_config((providers or {}).get(provider_key, {}), provider_key)

    model = str(task.get("model") or defaults.get("model") or provider.get("model", "")).strip()
    thinking_effort = str(task.get("thinking_effort", defaults.get("thinking_effort", "medium"))).lower()
    if thinking_effort not in THINKING_EFFORTS:
        thinking_effort = defaults.get("thinking_effort", "medium")

    return {
        "provider_key": provider_key,
        "model": model,
        "temperature": max(0.0, min(2.0, _as_float(task.get("temperature"), defaults["temperature"]))),
        "temperature_enabled": _as_bool(task.get("temperature_enabled"), defaults["temperature_enabled"]),
        "top_p": max(0.0, min(1.0, _as_float(task.get("top_p"), defaults["top_p"]))),
        "top_p_enabled": _as_bool(task.get("top_p_enabled"), defaults["top_p_enabled"]),
        "presence_penalty": max(-2.0, min(2.0, _as_float(task.get("presence_penalty"), defaults["presence_penalty"]))),
        "presence_penalty_enabled": _as_bool(
            task.get("presence_penalty_enabled"),
            defaults["presence_penalty_enabled"],
        ),
        "frequency_penalty": max(-2.0, min(2.0, _as_float(task.get("frequency_penalty"), defaults["frequency_penalty"]))),
        "frequency_penalty_enabled": _as_bool(
            task.get("frequency_penalty_enabled"),
            defaults["frequency_penalty_enabled"],
        ),
        "max_tokens": max(1, min(200000, _as_int(task.get("max_tokens"), defaults["max_tokens"]))),
        "max_tokens_enabled": _as_bool(task.get("max_tokens_enabled"), defaults["max_tokens_enabled"]),
        "is_thinking": _as_bool(task.get("is_thinking"), defaults["is_thinking"]),
        "thinking_effort": thinking_effort,
    }


def _normalize_ai_tasks(ai_tasks, active_provider, providers):
    """补齐全部 AI 任务路由配置。"""
    ai_tasks = dict(ai_tasks or {})
    return {
        task_key: _normalize_ai_task_config(ai_tasks.get(task_key), task_key, active_provider, providers)
        for task_key in AI_TASK_KEYS
    }


def _extract_deep_reading_questions(instruction):
    """从旧版深度阅读 prompt 中提取 Q&A 问题，提取失败时使用默认问题。"""
    questions = []
    text = (instruction or "").replace("\\n", "\n")
    for match in re.finditer(r"### Q\d+:\s*([^\n]+)", text):
        question = match.group(1).strip()
        if question:
            questions.append(question)
    return questions or DEFAULT_DEEP_READING_QUESTIONS


def _is_legacy_deep_reading_instruction(instruction):
    """判断是否为旧默认/旧问题编辑器生成的深度阅读 prompt。"""
    text = instruction or ""
    has_base_fields = all(
        marker in text
        for marker in ('"tags"', '"rating"', '"summary_cn"', '"value_comment"')
    )
    has_old_template_shape = any(
        marker in text
        for marker in (
            "然后给出标签、评级、中文摘要和价值评价",
            "然后给出标签和评级",
            "论文标题: {title}",
            "标签精度要求（重要）",
        )
    )
    has_old_guidance = ("标签选择指南" in text or "{tag_candidates}" in text) and (
        "评级标准" in text or "{rating_criteria}" in text
    )
    return "qa_analysis" in text and has_base_fields and has_old_template_shape and has_old_guidance


def _is_weak_qa_only_deep_reading_instruction(instruction):
    """判断是否为上一版 Q&A-only 但约束过弱的自动生成 prompt。"""
    text = instruction or ""
    return (
        "请对论文内容进行深度阅读分析，只生成 Q&A 深度阅读内容" in text
        and "qa_analysis" in text
        and "详细回答..." in text
        and "必须按顺序完整回答以下" not in text
    )


def _migrate_deep_reading_instruction(instruction):
    """仅迁移旧默认/旧问题模板；明显自定义的 prompt 原样保留。"""
    if _is_legacy_deep_reading_instruction(instruction) or _is_weak_qa_only_deep_reading_instruction(instruction):
        return _build_deep_reading_instruction(_extract_deep_reading_questions(instruction))
    return instruction


def _is_legacy_basic_analysis_instruction(instruction):
    """判断是否为旧默认/上一版默认基础分析 prompt，需要迁移到校准评级版。"""
    text = instruction or ""
    return (
        "请完成低成本基础论文分析" in text
        and '"tags"' in text
        and '"summary_cn"' in text
        and '"value_comment"' in text
        and "标签精度要求" in text
        and (
            '"rating"' not in text
            or "不要把 3 星作为默认安全分" not in text
            or "{rating_criteria}" not in text
        )
    )


def _migrate_basic_analysis_instruction(instruction):
    """旧默认/上一版默认基础分析 prompt 迁移到校准 AI 评级版；自定义 prompt 原样保留。"""
    if _is_legacy_basic_analysis_instruction(instruction):
        return DEFAULT_BASIC_ANALYSIS_INSTRUCTION
    return instruction


def _normalize_prompt_profiles(profiles, legacy_prompts=None):
    """补齐所有 AI 功能的 prompt profile。"""
    result = json.loads(json.dumps(DEFAULT_PROMPT_PROFILES))
    profiles = profiles if isinstance(profiles, dict) else {}

    if not profiles and isinstance(legacy_prompts, dict):
        system = legacy_prompts.get("system_prompt")
        instruction = legacy_prompts.get("user_prompt")
        if system or instruction:
            result["deep_reading"] = {
                "system": system or result["deep_reading"]["system"],
                "instruction": _migrate_deep_reading_instruction(
                    instruction or result["deep_reading"]["instruction"]
                ),
            }

    for task_key in AI_TASK_KEYS:
        profile = profiles.get(task_key)
        if not isinstance(profile, dict):
            continue
        system = profile.get("system")
        instruction = profile.get("instruction")
        if system is not None:
            result[task_key]["system"] = str(system)
        if instruction is not None:
            instruction = str(instruction)
            if task_key == "basic_analysis":
                instruction = _migrate_basic_analysis_instruction(instruction)
            elif task_key == "deep_reading":
                instruction = _migrate_deep_reading_instruction(instruction)
            result[task_key]["instruction"] = instruction
    return result
