"""Settings prompts implementation."""

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


def get_prompt_profiles():
    """获取所有 AI 功能的 prompt profile。"""
    settings = load_settings()
    return _normalize_prompt_profiles(
        settings.get("prompt_profiles", {}),
        settings.get("prompts", {}),
    )


def get_prompt_profile(task_key):
    """获取单个任务的 prompt profile。"""
    profiles = get_prompt_profiles()
    if task_key not in profiles:
        task_key = "basic_analysis"
    return profiles[task_key]


def save_prompt_profiles(profiles):
    """保存全部 prompt profiles。"""
    settings = load_settings()
    settings["prompt_profiles"] = _normalize_prompt_profiles(profiles, settings.get("prompts", {}))
    deep = settings["prompt_profiles"]["deep_reading"]
    settings["prompts"] = {
        "system_prompt": deep.get("system", ""),
        "user_prompt": deep.get("instruction", ""),
    }
    return save_settings(settings)


def save_prompt_profile(task_key, profile):
    """保存单个任务的 prompt profile。"""
    if task_key not in AI_TASK_KEYS:
        return False
    settings = load_settings()
    profiles = _normalize_prompt_profiles(settings.get("prompt_profiles", {}), settings.get("prompts", {}))
    profile = dict(profile or {})
    if "system" in profile:
        profiles[task_key]["system"] = str(profile.get("system") or "")
    if "instruction" in profile:
        profiles[task_key]["instruction"] = str(profile.get("instruction") or "")
    settings["prompt_profiles"] = profiles
    deep = profiles["deep_reading"]
    settings["prompts"] = {
        "system_prompt": deep.get("system", ""),
        "user_prompt": deep.get("instruction", ""),
    }
    return save_settings(settings)


def get_prompts():
    """
    获取 AI 分析使用的 Prompt 模板。

    以默认 prompt 为基础，用用户自定义的 prompt 覆盖。
    这样用户只需修改想改的部分，其余保持默认。

    返回:
        dict: 包含 system_prompt 和 user_prompt 的旧版兼容字典，并附带 prompt_profiles
            - system_prompt/user_prompt: 映射到 deep_reading profile
            - prompt_profiles: 新版按任务拆分的稳定 Prompt 前缀
    """
    profiles = get_prompt_profiles()
    deep = profiles.get("deep_reading", DEFAULT_PROMPT_PROFILES["deep_reading"])
    return {
        "system_prompt": deep.get("system", ""),
        "user_prompt": deep.get("instruction", ""),
        "prompt_profiles": profiles,
    }


def save_prompts(prompts):
    """
    保存用户自定义的 Prompt 模板。

    参数:
        prompts: 包含 system_prompt 和/或 user_prompt 的字典

    返回:
        bool: 保存是否成功
    """
    settings = load_settings()
    settings["prompts"] = prompts
    profiles = _normalize_prompt_profiles(settings.get("prompt_profiles", {}), prompts)
    profiles["deep_reading"] = {
        "system": prompts.get("system_prompt", profiles["deep_reading"]["system"]),
        "instruction": prompts.get("user_prompt", profiles["deep_reading"]["instruction"]),
    }
    settings["prompt_profiles"] = profiles
    return save_settings(settings)


PROFILE_REQUIRED_PROMPT_FIELDS = {
    "basic_analysis": {"tag_candidates", "rating_criteria"},
    "deep_reading": set(),
    "report_summary": set(),
    "recommendation": set(),
    "paper_chat": set(),
    "paper_quiz": set(),
}


def validate_prompt_template(user_prompt, profile_key=None):
    """
    校验用户 Prompt 模板是否能被 str.format 正常渲染。

    返回:
        tuple(bool, str): 是否有效，以及错误消息
    """
    if profile_key in AI_TASK_KEYS:
        fields = set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", user_prompt or ""))
        required = PROFILE_REQUIRED_PROMPT_FIELDS.get(profile_key, set())
        missing = sorted(required - fields)
        if missing:
            return False, "Prompt 缺少建议变量：" + ", ".join("{" + name + "}" for name in missing)
        return True, ""

    try:
        fields = set()
        for _, field_name, _, _ in Formatter().parse(user_prompt or ""):
            if field_name:
                fields.add(field_name.split(".")[0].split("[")[0])
    except ValueError as e:
        return False, f"Prompt 大括号格式错误：{e}。普通 JSON 大括号需要写成 {{ 和 }}。"

    missing = sorted(REQUIRED_PROMPT_FIELDS - fields)
    if missing:
        return False, "Prompt 缺少必需变量：" + ", ".join("{" + name + "}" for name in missing)

    try:
        (user_prompt or "").format(
            title="测试标题",
            authors="测试作者",
            abstract="测试摘要",
            tag_candidates="标签A, 标签B",
            rating_criteria="评级标准",
        )
    except (KeyError, IndexError, ValueError) as e:
        return False, f"Prompt 渲染失败：{e}"

    return True, ""
