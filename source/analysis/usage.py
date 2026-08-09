"""Normalize token usage across OpenAI-compatible response shapes."""

from source.value_coercion import as_int


def _value(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _extract_usage(response):
    usage = _value(response, "usage", {}) or {}
    prompt_tokens = as_int(_value(usage, "prompt_tokens", 0))
    completion_tokens = as_int(_value(usage, "completion_tokens", 0))
    total_tokens = as_int(_value(usage, "total_tokens", 0))
    details = _value(usage, "prompt_tokens_details", None) or _value(usage, "input_tokens_details", None) or {}
    cached_tokens = (
        as_int(_value(usage, "prompt_cache_hit_tokens", 0))
        or as_int(_value(details, "cached_tokens", 0))
        or as_int(_value(details, "cache_read_input_tokens", 0))
    )
    cache_miss_tokens = as_int(_value(usage, "prompt_cache_miss_tokens", 0))
    if not cache_miss_tokens and prompt_tokens:
        cache_miss_tokens = max(prompt_tokens - cached_tokens, 0)
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cached_tokens": cached_tokens,
        "cache_miss_tokens": cache_miss_tokens,
    }
