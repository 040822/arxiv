"""Self-managed benchmark task routes (author/judge/judge_review).

v1 决策：不进入共享 ai_tasks 设置，路由配置存 benchmark_routes 表，
供应商凭据仍从 settings.json 的 providers 解析，复用归一化与请求参数构建。
"""

import logging

from source.settings import (
    build_chat_completion_kwargs,
    load_settings,
    normalize_provider_config,
    normalize_provider_connection,
)
from source.value_coercion import as_bool, as_float, as_int

logger = logging.getLogger(__name__)

BENCHMARK_ROUTE_LABELS = {
    "benchmark_author": "Benchmark 出题",
    "benchmark_judge": "Benchmark 主裁判",
    "benchmark_judge_review": "Benchmark 复核裁判",
}

DEFAULT_BENCHMARK_ROUTES = {
    "benchmark_author": {
        "provider_key": "",
        "model": "",
        "is_thinking": True,
        "thinking_effort": "high",
        "temperature_enabled": False,
        "temperature": 0.1,
        "max_tokens_enabled": True,
        "max_tokens": 8000,
    },
    "benchmark_judge": {
        "provider_key": "",
        "model": "",
        "is_thinking": False,
        "thinking_effort": "medium",
        "temperature_enabled": True,
        "temperature": 0.1,
        "max_tokens_enabled": True,
        "max_tokens": 4000,
    },
    "benchmark_judge_review": {
        "provider_key": "",
        "model": "",
        "is_thinking": False,
        "thinking_effort": "medium",
        "temperature_enabled": True,
        "temperature": 0.1,
        "max_tokens_enabled": True,
        "max_tokens": 4000,
    },
}

from .prompts import (  # noqa: E402  (constants used by save_route default)
    BENCHMARK_AUTHOR_SYSTEM,
    BENCHMARK_AUTHOR_INSTRUCTION,
    BENCHMARK_JUDGE_SYSTEM,
    BENCHMARK_JUDGE_INSTRUCTION,
    BENCHMARK_JUDGE_REVIEW_INSTRUCTION,
)

DEFAULT_BENCHMARK_PROMPTS = {
    "benchmark_author": {
        "system": BENCHMARK_AUTHOR_SYSTEM,
        "instruction": BENCHMARK_AUTHOR_INSTRUCTION,
    },
    "benchmark_judge": {
        "system": BENCHMARK_JUDGE_SYSTEM,
        "instruction": BENCHMARK_JUDGE_INSTRUCTION,
    },
    "benchmark_judge_review": {
        "system": BENCHMARK_JUDGE_SYSTEM,
        "instruction": BENCHMARK_JUDGE_REVIEW_INSTRUCTION,
    },
}


def _clamp_route(config):
    """约束并补齐单个 benchmark 任务路由字段（与共享任务路由同语义）。"""
    config = dict(config or {})
    effort = str(config.get("thinking_effort") or "medium").lower()
    if effort not in {"low", "medium", "high"}:
        effort = "medium"
    return {
        "provider_key": str(config.get("provider_key") or "").strip(),
        "model": str(config.get("model") or "").strip(),
        "is_thinking": as_bool(config.get("is_thinking")),
        "thinking_effort": effort,
        "temperature_enabled": as_bool(config.get("temperature_enabled")),
        "temperature": max(0.0, min(2.0, as_float(config.get("temperature"), 0.1))),
        "max_tokens_enabled": as_bool(config.get("max_tokens_enabled", True)),
        "max_tokens": max(1, min(200000, as_int(config.get("max_tokens"), 4000))),
    }


def get_route_configs():
    """合并默认值与已保存路由，返回 {task_key: 配置}。"""
    from source.storage.benchmark import get_benchmark_routes

    stored = get_benchmark_routes()
    merged = {}
    for task_key, defaults in DEFAULT_BENCHMARK_ROUTES.items():
        merged[task_key] = _clamp_route({**defaults, **(stored.get(task_key) or {})})
    return merged


def get_route_config(task_key):
    """读取单个路由配置；返回 None 表示未知 task_key。"""
    configs = get_route_configs()
    return configs.get(task_key)


def save_route_config(task_key, config):
    """保存单个路由配置（会校验 task_key）。返回保存后的配置。"""
    from source.storage.benchmark import save_benchmark_route

    if task_key not in DEFAULT_BENCHMARK_ROUTES:
        raise ValueError(f"未知 benchmark 任务路由: {task_key or '未设置'}")
    clamped = _clamp_route({**DEFAULT_BENCHMARK_ROUTES[task_key], **dict(config or {})})
    save_benchmark_route(task_key, clamped)
    return clamped


def get_route_prompts():
    """返回 {task_key: {system, instruction}}；v1 为代码常量，后续可版本化。"""
    return {key: dict(profile) for key, profile in DEFAULT_BENCHMARK_PROMPTS.items()}


def _pick_provider_key(provider_key, providers):
    """优先使用指定供应商；未指定时回退到第一个可用供应商；指定但不存在时抛错。"""
    providers = providers or {}
    provider_key = str(provider_key or "").strip()
    if provider_key:
        if provider_key not in providers:
            raise ValueError(f"benchmark 模型路由引用的供应商不存在: {provider_key}")
        return provider_key
    if not providers:
        return ""
    return next(iter(providers.keys()))


def resolve_model_config(config):
    """把路由/候选配置与供应商凭据合并为可直接调用的 cfg（含 provider_key/model/api_key）。"""
    config = _clamp_route(config)
    settings = load_settings()
    providers = settings.get("providers", {})
    provider_key = _pick_provider_key(config.get("provider_key"), providers)
    if not provider_key:
        raise ValueError("尚未配置任何 AI 供应商，无法解析 benchmark 模型路由")
    if not config.get("model"):
        raise ValueError("benchmark 模型路由未设置模型")
    provider = normalize_provider_connection(providers[provider_key], provider_key)
    merged = {**provider, **config}
    merged["provider_key"] = provider_key
    merged["provider_name"] = provider.get("name", provider_key)
    return normalize_provider_config(merged, provider_key) | {
        "provider_key": provider_key,
        "provider_name": provider.get("name", provider_key),
    }


def build_route_kwargs(cfg, messages):
    """统一构建 Chat Completions 参数（复用共享逻辑，保持可选参数省略语义）。"""
    return build_chat_completion_kwargs(cfg, messages)


def build_actual_request_params(cfg):
    """Build the exact non-prompt request payload used for a candidate.

    A harmless sentinel message is supplied only because the shared request
    builder requires the ``messages`` argument; it is removed before the
    snapshot is returned.  This keeps ``extra_body``, ``reasoning_effort`` and
    ``max_completion_tokens`` visible while guaranteeing credentials/prompts
    never enter the persisted candidate record.
    """
    kwargs = build_route_kwargs(
        cfg,
        [{"role": "user", "content": "__benchmark_request_snapshot__"}],
    )

    def sanitize(value):
        if isinstance(value, dict):
            return {
                str(key): sanitize(item)
                for key, item in value.items()
                if str(key).lower() not in {"messages", "api_key", "apikey", "authorization"}
            }
        if isinstance(value, (list, tuple)):
            return [sanitize(item) for item in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)

    return sanitize({key: value for key, value in kwargs.items() if key != "messages"})
