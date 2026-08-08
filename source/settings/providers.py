"""Settings providers implementation."""

import hashlib
import json
import logging
import os
import re
import secrets
from string import Formatter


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
)
from .thinking import (
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
    normalize_provider_connection,
    normalize_provider_config,
    _normalize_effort,
    _map_openai_effort,
    _map_deepseek_effort,
    _map_thinking_budget,
    _should_use_max_completion_tokens,
    build_chat_completion_kwargs,
)

from .store import load_settings, save_settings


def get_ai_config():
    """
    获取 AI API 调用所需的精简配置。

    这是 analyzer.py 调用 OpenAI API 时使用的核心函数，提取供应商配置中
    与 API 调用直接相关的字段。

    返回:
        dict: 包含以下字段的配置字典：
            - api_key: API 密钥
            - base_url: API 端点地址
            - model: 模型名称
            - temperature: 非思考模型可选的采样参数
            - max_tokens/max_tokens_enabled: 可选最大生成 token 数
            - is_thinking/thinking_effort: 思考模式配置
    """
    return get_ai_task_config("basic_analysis")


def get_ai_tasks():
    """获取所有 AI 功能的任务级模型与参数配置。"""
    settings = load_settings()
    return _normalize_ai_tasks(
        settings.get("ai_tasks", {}),
        "",
        settings.get("providers", {}),
    )


def save_ai_tasks(ai_tasks):
    """保存任务级模型与参数配置。"""
    settings = load_settings()
    normalized = _normalize_ai_tasks(
        ai_tasks,
        "",
        settings.get("providers", {}),
    )
    providers = settings.get("providers", {})
    errors = []
    for task_key in AI_TASK_KEYS:
        task = normalized[task_key]
        provider_key = str(task.get("provider_key") or "").strip()
        if provider_key not in providers:
            errors.append(f"{AI_TASK_LABELS[task_key]}引用的供应商不存在: {provider_key or '未设置'}")
        if not str(task.get("model") or "").strip():
            errors.append(f"{AI_TASK_LABELS[task_key]}未设置模型")
    if errors:
        raise ValueError("；".join(errors))
    settings["ai_tasks"] = normalized
    return save_settings(settings)


def get_ai_task_config(task_key):
    """
    获取某个 AI 功能的实际调用配置。

    任务配置只保存 provider_key/model/参数开关；这里会与供应商 API Key/Base URL 合并，
    返回值可直接传给 OpenAI 客户端和 build_chat_completion_kwargs()。
    """
    return resolve_ai_task_config(task_key)


def resolve_ai_task_config(task_key, task_config=None):
    """Resolve a saved or draft task route against stored provider credentials."""
    settings = load_settings()
    if task_key not in AI_TASK_KEYS:
        raise ValueError(f"未知功能模型路由: {task_key}")
    raw_task = (
        settings.get("ai_tasks", {}).get(task_key, {})
        if task_config is None else task_config
    )
    task = _normalize_ai_task_config(
        raw_task,
        task_key,
        "",
        settings.get("providers", {}),
    )
    provider_key = str(task.get("provider_key") or "").strip()
    providers = settings.get("providers", {})
    if provider_key not in providers:
        raise ValueError(f"{AI_TASK_LABELS[task_key]}引用的供应商不存在: {provider_key or '未设置'}")
    if not str(task.get("model") or "").strip():
        raise ValueError(f"{AI_TASK_LABELS[task_key]}未设置模型")
    provider = normalize_provider_connection(providers[provider_key], provider_key)
    merged = {**provider, **task}
    merged["provider_key"] = provider_key
    merged["provider_name"] = provider.get("name", provider_key)
    merged["task_key"] = task_key
    return normalize_provider_config(merged, provider_key) | {
        "provider_key": provider_key,
        "provider_name": provider.get("name", provider_key),
        "task_key": task_key,
    }


def get_all_providers():
    """
    获取所有已配置的供应商列表。

    返回:
        dict: 以供应商 key 为键、配置字典为值的字典
    """
    return load_settings().get("providers", {})


def get_provider_presets():
    """
    获取所有预设供应商模板。

    预设模板用于 Web 设置页的"添加供应商"下拉菜单，
    提供常见 AI API 的 base_url 和 models 预填值。

    返回:
        dict: 预设供应商配置字典（PROVIDER_PRESETS）
    """
    return PROVIDER_PRESETS


def add_provider(key, config):
    """
    添加新供应商到配置。

    参数:
        key: 供应商唯一标识（如 "openai"、"my_api"）
        config: 供应商配置字典，包含 name/api_key/base_url/model 等

    返回:
        bool: 保存是否成功
    """
    settings = load_settings()
    settings["providers"][key] = normalize_provider_connection(config, key)
    return save_settings(settings)


def remove_provider(key):
    """删除未被功能路由引用的供应商，并返回结构化结果。"""
    settings = load_settings()
    if key not in settings["providers"]:
        return {"removed": False, "references": [], "not_found": True}
    references = [
        task_key for task_key, task in settings.get("ai_tasks", {}).items()
        if task.get("provider_key") == key
    ]
    if references:
        return {"removed": False, "references": references, "not_found": False}
    del settings["providers"][key]
    return {
        "removed": bool(save_settings(settings)),
        "references": [],
        "not_found": False,
    }


def update_provider(key, config):
    """
    更新已有供应商的配置字段。

    使用 dict.update() 合并新配置，只更新传入的字段，
    未传入的字段保持不变。

    参数:
        key: 要更新的供应商标识
        config: 要更新的字段字典（如 {"api_key": "sk-xxx"}）

    返回:
        bool: 更新是否成功（供应商不存在时返回 False）
    """
    settings = load_settings()
    if key in settings["providers"]:
        settings["providers"][key].update(config)
        settings["providers"][key] = normalize_provider_connection(settings["providers"][key], key)
        return save_settings(settings)
    return False
