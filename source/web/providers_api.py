"""Flask providers_api routes."""

import hashlib
import hmac
import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime

from flask import (
    Blueprint, Response, current_app, jsonify, redirect, render_template,
    request, session, url_for,
)
from analyzer import (
    analyze_pending_papers, analyze_paper_basic, analyze_paper_full,
    analyze_papers, generate_report_ai_summary, recommend_pending_papers,
    chat_about_paper, generate_paper_quiz, get_openai_client,
    grade_quiz_answer, socratic_reply,
)
from backup import get_database_file_sizes
from fetcher import (
    fetch_batch, fetch_by_date, fetch_latest_papers, fetch_paper_by_id,
    parse_arxiv_id,
)
from source.pipeline import (
    _run_email_report_task, _run_webdav_backup_task, configure_daily_job,
    pipeline_lock, scheduler,
)
from source.reports import generate_report_content
from source.settings import (
    add_provider,
    build_chat_completion_kwargs,
    get_ai_config,
    get_ai_task_config,
    get_all_providers,
    get_provider_presets,
    get_thinking_protocol,
    load_settings,
    normalize_provider_config,
    remove_provider,
    switch_provider,
    update_provider,
)

from .progress import get_progress, update_progress

logger = logging.getLogger(__name__)


bp = Blueprint("providers_api", __name__)


@bp.route("/api/network/test-llm", methods=["POST"])
def api_network_test_llm():
    """测试基础分析任务路由的 LLM chat/completions 连接。"""
    cfg = {}
    started = time.time()
    try:
        cfg = get_ai_task_config("basic_analysis")
        if not cfg.get("api_key"):
            return jsonify({
                "status": "error",
                "message": "基础分析模型路由缺少 API Key",
                "provider_key": cfg.get("provider_key", ""),
                "model": cfg.get("model", ""),
                "duration_ms": 0,
            }), 400

        client = get_openai_client(cfg)
        kwargs = build_chat_completion_kwargs(
            cfg,
            [{"role": "user", "content": "Hello, reply with 'ok' only."}],
            token_limit_override=10,
        )
        response = client.chat.completions.create(**kwargs)
        reply = (response.choices[0].message.content or "").strip()
        duration_ms = int((time.time() - started) * 1000)
        return jsonify({
            "status": "ok",
            "message": f"基础分析 LLM 连接成功：{reply or 'ok'}",
            "provider_key": cfg.get("provider_key", ""),
            "model": cfg.get("model", ""),
            "duration_ms": duration_ms,
        })
    except Exception as e:
        duration_ms = int((time.time() - started) * 1000)
        return jsonify({
            "status": "error",
            "message": f"基础分析 LLM 连接失败: {str(e)}",
            "provider_key": cfg.get("provider_key", ""),
            "model": cfg.get("model", ""),
            "duration_ms": duration_ms,
        }), 500


def _request_bool(data, key, default=False):
    """解析前端传来的布尔字段，保留显式 false。"""
    if key not in data:
        return default
    value = data.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _request_float(data, key, default):
    try:
        value = data.get(key, default)
        if value == "" or value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _request_int(data, key, default):
    try:
        value = data.get(key, default)
        if value == "" or value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _provider_config_from_request(data, key="", partial=False):
    """从请求 JSON 中提取供应商配置字段。"""
    data = data or {}
    config = {}

    string_fields = ("name", "base_url", "model", "thinking_effort")
    for field in string_fields:
        if field in data or not partial:
            config[field] = data.get(field, key if field == "name" else "")

    if "api_key" in data:
        if data.get("api_key") or not partial:
            config["api_key"] = data.get("api_key", "")
    elif not partial:
        config["api_key"] = ""

    float_fields = {
        "temperature": 0.3,
        "top_p": 1.0,
        "presence_penalty": 0.0,
        "frequency_penalty": 0.0,
    }
    for field, default in float_fields.items():
        if field in data or not partial:
            config[field] = _request_float(data, field, default)

    if "max_tokens" in data or not partial:
        config["max_tokens"] = _request_int(data, "max_tokens", 8192)

    bool_fields = (
        "temperature_enabled",
        "top_p_enabled",
        "presence_penalty_enabled",
        "frequency_penalty_enabled",
        "max_tokens_enabled",
        "is_thinking",
    )
    defaults = {
        "temperature_enabled": True,
        "top_p_enabled": False,
        "presence_penalty_enabled": False,
        "frequency_penalty_enabled": False,
        "max_tokens_enabled": False,
        "is_thinking": False,
    }
    for field in bool_fields:
        if field in data or not partial:
            config[field] = _request_bool(data, field, defaults[field])

    if "available_models" in data:
        models = data.get("available_models") or []
        config["available_models"] = [str(m).strip() for m in models if str(m).strip()]
    elif not partial:
        config["available_models"] = []

    return normalize_provider_config(config, key) if not partial else config


def _extract_model_ids(model_page):
    """从 OpenAI SDK models.list() 响应中提取模型 ID。"""
    data = getattr(model_page, "data", model_page)
    models = []
    for item in data or []:
        model_id = getattr(item, "id", None)
        if model_id is None and isinstance(item, dict):
            model_id = item.get("id")
        if model_id:
            models.append(str(model_id))
    return sorted(set(models), key=str.lower)


def _get_nested_value(obj, *path):
    """同时兼容 OpenAI SDK 对象和测试中的 dict 响应。"""
    current = obj
    for part in path:
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(part)
        else:
            current = getattr(current, part, None)
    return current


def _has_reasoning_content(message):
    return bool(_get_nested_value(message, "reasoning_content"))


def _get_reasoning_tokens(response):
    value = _get_nested_value(response, "usage", "completion_tokens_details", "reasoning_tokens")
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


@bp.route("/api/providers/presets", methods=["GET"])
def api_provider_presets():
    """获取预设的 AI 供应商列表（如 DeepSeek、OpenAI 等）"""
    return jsonify(get_provider_presets())


@bp.route("/api/providers/models", methods=["POST"])
def api_provider_models():
    """从供应商的 OpenAI 兼容 /models 接口拉取可用模型列表。"""
    try:
        data = request.get_json() or {}
        provider_key = data.get("provider_key", "")
        saved_provider = {}
        if provider_key:
            saved_provider = get_all_providers().get(provider_key, {})

        api_key = data.get("api_key") or saved_provider.get("api_key", "")
        base_url = data.get("base_url") or saved_provider.get("base_url", "")
        if not api_key:
            return jsonify({"status": "error", "message": "请先配置 API Key"}), 400
        if not base_url:
            return jsonify({"status": "error", "message": "Base URL 不能为空"}), 400

        client = get_openai_client({"api_key": api_key, "base_url": base_url})
        models = _extract_model_ids(client.models.list())
        if provider_key and models:
            update_provider(provider_key, {"available_models": models})
        return jsonify({"status": "ok", "models": models, "count": len(models)})
    except Exception as e:
        return jsonify({"status": "error", "message": f"获取模型列表失败: {str(e)}"}), 500


@bp.route("/api/providers", methods=["GET"])
def api_list_providers():
    """
    获取所有已配置的 AI 供应商列表

    返回每个供应商的配置信息，其中 API Key 脱敏显示（仅保留首尾各 4 字符）。
    """
    settings = load_settings()
    providers = settings.get("providers", {})
    active = settings.get("active_provider", "")
    result = {}
    for k, v in providers.items():
        item = v.copy()
        # API Key 脱敏处理
        if item.get("api_key"):
            key = item["api_key"]
            item["api_key_masked"] = key[:4] + "****" + key[-4:] if len(key) > 8 else "****"
        else:
            item["api_key_masked"] = ""
        item.pop("api_key", None)
        item["is_active"] = (k == active)
        result[k] = item
    return jsonify({"active_provider": active, "providers": result})


@bp.route("/api/providers", methods=["POST"])
def api_add_provider():
    """添加新的 AI 供应商配置"""
    try:
        data = request.get_json() or {}
        key = data.get("key", "").strip()
        if not key:
            return jsonify({"status": "error", "message": "供应商ID不能为空"}), 400
        config = _provider_config_from_request(data, key, partial=False)
        if add_provider(key, config):
            return jsonify({"status": "ok", "message": f"供应商 {config['name']} 已添加"})
        return jsonify({"status": "error", "message": "保存失败"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/providers/<key>", methods=["PUT"])
def api_update_provider(key):
    """更新指定供应商的配置（仅更新传入的字段）"""
    try:
        data = request.get_json() or {}
        config = _provider_config_from_request(data, key, partial=True)
        if update_provider(key, config):
            return jsonify({"status": "ok", "message": "已更新"})
        return jsonify({"status": "error", "message": "更新失败，供应商不存在"}), 404
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/providers/<key>", methods=["DELETE"])
def api_delete_provider(key):
    """删除指定的 AI 供应商配置"""
    if remove_provider(key):
        return jsonify({"status": "ok", "message": "已删除"})
    return jsonify({"status": "error", "message": "删除失败"}), 400


@bp.route("/api/providers/<key>/activate", methods=["POST"])
def api_activate_provider(key):
    """切换当前激活的 AI 供应商"""
    if switch_provider(key):
        return jsonify({"status": "ok", "message": f"已切换到 {key}"})
    return jsonify({"status": "error", "message": "切换失败，供应商不存在"}), 404


@bp.route("/api/test_connection", methods=["POST"])
def api_test_connection():
    """测试当前激活的 AI 供应商连接：发送简单请求验证 API 可用性"""
    try:
        cfg = get_ai_config()
        if not cfg["api_key"]:
            return jsonify({"status": "error", "message": "请先配置 API Key"}), 400
        client = get_openai_client(cfg)
        kwargs = build_chat_completion_kwargs(
            cfg,
            [{"role": "user", "content": "Hello, reply with 'ok' only."}],
            token_limit_override=10,
        )
        response = client.chat.completions.create(**kwargs)
        reply = (response.choices[0].message.content or "").strip()
        return jsonify({"status": "ok", "message": f"连接成功！模型回复: {reply}"})
    except Exception as e:
        return jsonify({"status": "error", "message": f"连接失败: {str(e)}"}), 500


@bp.route("/api/detect_thinking", methods=["POST"])
def api_detect_thinking():
    """
    检测当前模型是否支持思考模式（reasoning）

    使用当前供应商对应的思考协议发送请求，并结合响应字段、usage 和模型名启发式判断。
    """
    try:
        cfg = get_ai_config()
        if not cfg["api_key"]:
            return jsonify({"status": "error", "message": "请先配置 API Key"}), 400
        client = get_openai_client(cfg)
        kwargs = build_chat_completion_kwargs(
            cfg,
            [{"role": "user", "content": "What is 1+1? Reply with just the number."}],
            token_limit_override=50,
            force_thinking=True,
        )
        response = client.chat.completions.create(**kwargs)
        msg = response.choices[0].message
        has_reasoning = _has_reasoning_content(msg)
        reasoning_tokens = _get_reasoning_tokens(response)
        protocol = get_thinking_protocol({**cfg, "is_thinking": True})
        model_heuristic = bool(cfg.get("effective_is_thinking")) or protocol in {
            "openai_reasoning",
            "deepseek_v4",
            "deepseek_legacy",
            "qwen_compatible",
        }
        is_thinking = has_reasoning or reasoning_tokens > 0 or model_heuristic
        confidence = "high" if (has_reasoning or reasoning_tokens > 0) else ("medium" if model_heuristic else "low")

        settings = load_settings()
        active = settings.get("active_provider", "")
        if active:
            update_provider(active, {
                "is_thinking": is_thinking,
                "thinking_effort": cfg.get("thinking_effort", "medium") or "medium",
            })

        if is_thinking:
            message = f"该模型支持思考模式（{protocol}，置信度 {confidence}），已保存检测结果"
        else:
            message = "该模型未返回思考内容，也未命中已知思考模型规则，已保存检测结果"
        return jsonify({
            "status": "ok",
            "is_thinking": is_thinking,
            "confidence": confidence,
            "thinking_protocol": protocol,
            "reasoning_tokens": reasoning_tokens,
            "message": message,
        })
    except Exception as e:
        return jsonify({"status": "error", "message": f"检测失败: {str(e)}"}), 500
