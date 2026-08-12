"""Shared model-call plumbing for benchmark routes (client, usage, kwargs)."""

import logging
import time

from source.analysis.client import get_openai_client
from source.analysis.usage import _extract_usage, get_usage_user_id
from source.storage import record_ai_usage

from .config import build_route_kwargs

logger = logging.getLogger(__name__)


def _first_choice(response):
    choices = []
    try:
        choices = response.choices or []
    except Exception:
        pass
    first = choices[0] if choices else None
    if first is None:
        return "", ""
    try:
        message = first.message or {}
    except Exception:
        message = {}
    content = ""
    try:
        content = (message.content or "").strip()
    except Exception:
        pass
    finish_reason = ""
    try:
        finish_reason = str(first.finish_reason or "") or ""
    except Exception:
        pass
    return content, finish_reason


def call_model(cfg, messages, task_key, paper_ref=None):
    """调用模型并返回 (content, usage)；usage 含 finish_reason 与 latency_ms。"""
    client = get_openai_client(cfg)
    kwargs = build_route_kwargs(cfg, messages)
    started = time.monotonic()
    response = client.chat.completions.create(**kwargs)
    latency_ms = round((time.monotonic() - started) * 1000, 1)
    usage = dict(_extract_usage(response) or {})
    content, finish_reason = _first_choice(response)
    usage["finish_reason"] = finish_reason
    usage["latency_ms"] = latency_ms
    try:
        record_ai_usage({
            "task_key": task_key,
            "provider_key": cfg.get("provider_key", ""),
            "provider_name": cfg.get("provider_name", ""),
            "model": cfg.get("model", ""),
            "paper_id": (paper_ref or {}).get("paper_id"),
            "arxiv_id": (paper_ref or {}).get("arxiv_id", ""),
            "user_id": get_usage_user_id(),
            **usage,
        })
    except Exception as exc:  # 用量记录失败不影响评测主流程
        logger.debug(f"Failed to record benchmark usage: {exc}")
    return content, usage
