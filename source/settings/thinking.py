"""Thinking-model detection, provider normalization, and request mapping."""

from source.value_coercion import as_bool, as_float, as_int
from .defaults import (
    DEFAULT_PROVIDER_OPTIONS,
    OPENAI_REASONING_PREFIXES,
    PROVIDER_PRESETS,
    THINKING_BUDGETS,
    THINKING_EFFORTS,
)


def _model_leaf(model):
    """获取模型名最后一段，用于兼容 vendor/model 形式。"""
    return (model or "").strip().lower().split("/")[-1]


def is_openai_reasoning_model(model):
    """根据 OpenAI 模型名启发式判断是否为 reasoning 模型。"""
    leaf = _model_leaf(model)
    return leaf.startswith(OPENAI_REASONING_PREFIXES)


def _is_deepseek_v4_model(model):
    leaf = _model_leaf(model)
    return leaf.startswith("deepseek-v4") or "deepseek-v4" in leaf


def _is_deepseek_reasoning_model(model):
    leaf = _model_leaf(model)
    return "deepseek-reasoner" in leaf or "deepseek-r1" in leaf or leaf.startswith("r1")


def _is_qwen_like_model(model):
    lower_model = (model or "").lower()
    return any(token in lower_model for token in ("qwen", "qwq", "mimo"))


def _is_openai_base_url(base_url):
    return "api.openai.com" in (base_url or "").lower()


def _is_deepseek_base_url(base_url):
    return "deepseek" in (base_url or "").lower()


def _is_qwen_like_base_url(base_url):
    lower_url = (base_url or "").lower()
    return any(token in lower_url for token in ("dashscope", "aliyuncs", "qwen", "xiaomi", "mimo"))


def get_thinking_protocol(provider):
    """
    推断当前供应商/模型的思考模式协议。

    返回值用于构建 OpenAI 兼容 Chat Completions 参数：
    openai_reasoning/deepseek_v4/deepseek_legacy/qwen_compatible/generic_enable_thinking/none
    """
    model = provider.get("model", "")
    base_url = provider.get("base_url", "")

    if _is_deepseek_v4_model(model):
        return "deepseek_v4"
    if _is_deepseek_reasoning_model(model):
        return "deepseek_legacy"
    if _is_qwen_like_model(model) or _is_qwen_like_base_url(base_url):
        return "qwen_compatible"
    if is_openai_reasoning_model(model) and _is_openai_base_url(base_url):
        return "openai_reasoning"
    if _is_deepseek_base_url(base_url) and provider.get("is_thinking"):
        return "deepseek_v4"
    if provider.get("is_thinking"):
        return "generic_enable_thinking"
    return "none"


def is_known_thinking_model(provider):
    """根据模型名判断无需用户手动勾选也应按思考模型处理的模型。"""
    model = provider.get("model", "")
    base_url = provider.get("base_url", "")
    return (
        is_openai_reasoning_model(model)
        and _is_openai_base_url(base_url)
    ) or _is_deepseek_reasoning_model(model)


def normalize_provider_config(config, key=None):
    """补齐供应商配置字段，兼容旧版 settings.json。"""
    config = dict(config or {})
    preset = PROVIDER_PRESETS.get(key or "", {})

    normalized = {
        "name": config.get("name", preset.get("name", key or "")),
        "api_key": config.get("api_key", ""),
        "base_url": config.get("base_url", preset.get("base_url", "")),
        "model": config.get("model", ""),
    }

    available_models = config.get("available_models")
    if not isinstance(available_models, list):
        available_models = preset.get("models", [])
    normalized["available_models"] = [str(m) for m in available_models if str(m).strip()]

    raw_is_thinking = as_bool(config.get("is_thinking"), DEFAULT_PROVIDER_OPTIONS["is_thinking"])
    normalized["is_thinking"] = raw_is_thinking
    normalized["effective_is_thinking"] = raw_is_thinking or is_known_thinking_model({**config, **normalized})

    normalized["temperature"] = as_float(config.get("temperature"), DEFAULT_PROVIDER_OPTIONS["temperature"])
    normalized["temperature_enabled"] = as_bool(
        config.get("temperature_enabled"),
        not normalized["is_thinking"],
    )
    normalized["top_p"] = as_float(config.get("top_p"), DEFAULT_PROVIDER_OPTIONS["top_p"])
    normalized["top_p_enabled"] = as_bool(config.get("top_p_enabled"), DEFAULT_PROVIDER_OPTIONS["top_p_enabled"])
    normalized["presence_penalty"] = as_float(
        config.get("presence_penalty"),
        DEFAULT_PROVIDER_OPTIONS["presence_penalty"],
    )
    normalized["presence_penalty_enabled"] = as_bool(
        config.get("presence_penalty_enabled"),
        DEFAULT_PROVIDER_OPTIONS["presence_penalty_enabled"],
    )
    normalized["frequency_penalty"] = as_float(
        config.get("frequency_penalty"),
        DEFAULT_PROVIDER_OPTIONS["frequency_penalty"],
    )
    normalized["frequency_penalty_enabled"] = as_bool(
        config.get("frequency_penalty_enabled"),
        DEFAULT_PROVIDER_OPTIONS["frequency_penalty_enabled"],
    )

    max_tokens = as_int(config.get("max_tokens"), DEFAULT_PROVIDER_OPTIONS["max_tokens"])
    normalized["max_tokens"] = max_tokens
    normalized["max_tokens_enabled"] = as_bool(
        config.get("max_tokens_enabled"),
        DEFAULT_PROVIDER_OPTIONS["max_tokens_enabled"],
    )

    thinking_effort = str(config.get("thinking_effort", DEFAULT_PROVIDER_OPTIONS["thinking_effort"])).lower()
    if thinking_effort not in THINKING_EFFORTS:
        thinking_effort = DEFAULT_PROVIDER_OPTIONS["thinking_effort"]
    normalized["thinking_effort"] = thinking_effort

    for field, default in DEFAULT_PROVIDER_OPTIONS.items():
        normalized.setdefault(field, default)
    return normalized


def normalize_provider_connection(config, key=None):
    """Normalize the connection-only fields persisted for one provider."""
    config = dict(config or {})
    preset = PROVIDER_PRESETS.get(key or "", {})
    available_models = config.get("available_models")
    if not isinstance(available_models, list):
        available_models = preset.get("models", [])
    return {
        "name": str(config.get("name") or preset.get("name") or key or "").strip(),
        "api_key": str(config.get("api_key") or ""),
        "base_url": str(config.get("base_url") or preset.get("base_url") or "").strip(),
        "available_models": list(dict.fromkeys(
            str(model).strip() for model in available_models if str(model).strip()
        )),
    }


def _normalize_effort(effort):
    effort = str(effort or DEFAULT_PROVIDER_OPTIONS["thinking_effort"]).lower()
    return effort if effort in THINKING_EFFORTS else DEFAULT_PROVIDER_OPTIONS["thinking_effort"]


def _map_openai_effort(effort):
    effort = _normalize_effort(effort)
    if effort == "auto":
        return None
    if effort == "max":
        return "high"
    return effort if effort in {"low", "medium", "high"} else "medium"


def _map_deepseek_effort(effort):
    effort = _normalize_effort(effort)
    if effort in {"auto", "low", "medium", "high"}:
        return "high"
    return "max"


def _map_thinking_budget(effort):
    effort = _normalize_effort(effort)
    if effort == "auto":
        return None
    return THINKING_BUDGETS.get(effort, THINKING_BUDGETS["medium"])


def _should_use_max_completion_tokens(provider, protocol):
    return protocol == "openai_reasoning" or (
        _is_openai_base_url(provider.get("base_url", ""))
        and is_openai_reasoning_model(provider.get("model", ""))
    )


def build_chat_completion_kwargs(provider, messages, token_limit_override=None, force_thinking=False):
    """
    统一构建 OpenAI 兼容 Chat Completions 请求参数。

    - 普通模型只发送显式启用的采样参数和 token 上限。
    - 思考模型省略 temperature/top_p/presence_penalty/frequency_penalty。
    - 不同供应商的思考参数在这里做协议映射。
    """
    cfg = normalize_provider_config(provider)
    protocol = get_thinking_protocol(cfg)
    if force_thinking and protocol == "none" and not _is_openai_base_url(cfg.get("base_url", "")):
        protocol = "generic_enable_thinking"
    thinking_enabled = bool(force_thinking or cfg.get("is_thinking") or is_known_thinking_model(cfg))

    kwargs = {
        "model": cfg.get("model", ""),
        "messages": messages,
    }
    extra_body = {}

    if thinking_enabled:
        effort = cfg.get("thinking_effort", DEFAULT_PROVIDER_OPTIONS["thinking_effort"])
        if protocol == "openai_reasoning":
            mapped = _map_openai_effort(effort)
            if mapped:
                kwargs["reasoning_effort"] = mapped
        elif protocol == "deepseek_v4":
            kwargs["reasoning_effort"] = _map_deepseek_effort(effort)
            extra_body["thinking"] = {"type": "enabled"}
        elif protocol == "qwen_compatible":
            extra_body["enable_thinking"] = True
            budget = _map_thinking_budget(effort)
            if budget:
                extra_body["thinking_budget"] = budget
        elif protocol == "generic_enable_thinking":
            extra_body["enable_thinking"] = True
    else:
        if protocol == "deepseek_v4":
            extra_body["thinking"] = {"type": "disabled"}
        if cfg.get("temperature_enabled"):
            kwargs["temperature"] = cfg.get("temperature", DEFAULT_PROVIDER_OPTIONS["temperature"])
        if cfg.get("top_p_enabled"):
            kwargs["top_p"] = cfg.get("top_p", DEFAULT_PROVIDER_OPTIONS["top_p"])
        if cfg.get("presence_penalty_enabled"):
            kwargs["presence_penalty"] = cfg.get("presence_penalty", DEFAULT_PROVIDER_OPTIONS["presence_penalty"])
        if cfg.get("frequency_penalty_enabled"):
            kwargs["frequency_penalty"] = cfg.get("frequency_penalty", DEFAULT_PROVIDER_OPTIONS["frequency_penalty"])

    token_limit = token_limit_override
    if token_limit is None and cfg.get("max_tokens_enabled"):
        token_limit = cfg.get("max_tokens")
    if token_limit:
        token_key = "max_completion_tokens" if _should_use_max_completion_tokens(cfg, protocol) else "max_tokens"
        kwargs[token_key] = int(token_limit)

    if extra_body:
        kwargs["extra_body"] = extra_body
    return kwargs
