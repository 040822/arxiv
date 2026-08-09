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
from source.analysis import get_openai_client
from source.settings import (
    AI_TASK_LABELS,
    add_provider,
    build_chat_completion_kwargs,
    get_all_providers,
    get_provider_presets,
    get_thinking_protocol,
    is_known_thinking_model,
    load_settings,
    normalize_provider_connection,
    remove_provider,
    resolve_ai_task_config,
    update_provider,
)


logger = logging.getLogger(__name__)


bp = Blueprint("providers_api", __name__)


def _provider_config_from_request(data, key="", partial=False):
    """从请求 JSON 中提取连接级供应商配置字段。"""
    data = data or {}
    config = {}

    string_fields = ("name", "base_url")
    for field in string_fields:
        if field in data or not partial:
            config[field] = data.get(field, key if field == "name" else "")

    if "api_key" in data:
        if data.get("api_key") or not partial:
            config["api_key"] = data.get("api_key", "")
    elif not partial:
        config["api_key"] = ""

    if "available_models" in data:
        models = data.get("available_models") or []
        config["available_models"] = list(dict.fromkeys(
            str(model).strip() for model in models if str(model).strip()
        ))
    elif not partial:
        config["available_models"] = []

    return normalize_provider_connection(config, key) if not partial else config


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


@bp.route("/api/settings/ai-tasks/<task_key>/test", methods=["POST"])
def api_test_ai_task_route(task_key):
    """使用当前未保存的功能路由草稿发送短请求，并返回思考能力提示。"""
    started = time.time()
    cfg = {}
    task_name = AI_TASK_LABELS.get(task_key, task_key)
    try:
        data = request.get_json() or {}
        draft = data.get("config")
        if not isinstance(draft, dict):
            return jsonify({"status": "error", "message": "缺少功能路由配置"}), 400
        cfg = resolve_ai_task_config(task_key, draft)
        if not cfg.get("api_key"):
            return jsonify({
                "status": "error",
                "message": "所选供应商缺少 API Key",
                "task_key": task_key,
                "task_name": task_name,
                "provider_key": cfg.get("provider_key", ""),
                "model": cfg.get("model", ""),
                "duration_ms": 0,
            }), 400

        client = get_openai_client(cfg)
        configured_limit = cfg.get("max_tokens") if cfg.get("max_tokens_enabled") else 256
        test_limit = max(1, min(int(configured_limit or 256), 256))
        kwargs = build_chat_completion_kwargs(
            cfg,
            [{"role": "user", "content": "Hello, reply with 'ok' only."}],
            token_limit_override=test_limit,
        )
        response = client.chat.completions.create(**kwargs)
        choices = _get_nested_value(response, "choices") or []
        message = _get_nested_value(choices[0], "message") if choices else None
        reply = str(_get_nested_value(message, "content") or "").strip()
        has_reasoning = _has_reasoning_content(message)
        reasoning_tokens = _get_reasoning_tokens(response)
        protocol = get_thinking_protocol(cfg)
        heuristic = is_known_thinking_model(cfg) or bool(cfg.get("is_thinking"))
        detected = bool(has_reasoning or reasoning_tokens > 0 or heuristic)
        confidence = "high" if (has_reasoning or reasoning_tokens > 0) else ("medium" if heuristic else "low")
        duration_ms = int((time.time() - started) * 1000)
        return jsonify({
            "status": "ok",
            "message": f"{task_name}路由连接成功：{reply or 'ok'}",
            "task_key": task_key,
            "task_name": task_name,
            "provider_key": cfg.get("provider_key", ""),
            "model": cfg.get("model", ""),
            "duration_ms": duration_ms,
            "thinking_detection": {
                "detected": detected,
                "confidence": confidence,
                "protocol": protocol,
                "reasoning_tokens": reasoning_tokens,
            },
        })
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    except Exception as exc:
        duration_ms = int((time.time() - started) * 1000)
        return jsonify({
            "status": "error",
            "message": f"路由连接失败: {exc}",
            "task_key": task_key,
            "task_name": task_name,
            "provider_key": cfg.get("provider_key", ""),
            "model": cfg.get("model", ""),
            "duration_ms": duration_ms,
        }), 500


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
        result[k] = item
    return jsonify({"providers": result})


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
    result = remove_provider(key)
    if result.get("removed"):
        return jsonify({"status": "ok", "message": "已删除"})
    if result.get("references"):
        labels = [AI_TASK_LABELS.get(task_key, task_key) for task_key in result["references"]]
        return jsonify({
            "status": "error",
            "message": "该供应商仍被功能模型路由使用，请先修改功能模型路由",
            "references": labels,
        }), 409
    status = 404 if result.get("not_found") else 500
    return jsonify({"status": "error", "message": "删除失败，供应商不存在"}), status
