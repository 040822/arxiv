"""Shared model-call plumbing for benchmark routes (client, usage, kwargs)."""

import logging
import re
import time

from source.analysis.client import get_openai_client
from source.analysis.usage import _extract_usage, get_usage_user_id
from source.storage import record_ai_usage

from .config import build_route_kwargs

logger = logging.getLogger(__name__)


class RetryFailed(RuntimeError):
    """A transient model failure remained after the bounded retry policy."""

    def __init__(self, message, *, cause=None, retry_count=0):
        super().__init__(message)
        self.cause = cause
        self.retry_count = int(retry_count or 0)


class ContextWindowError(RuntimeError):
    """A provider rejected the candidate input for exceeding its context."""

    def __init__(self, message, *, cause=None):
        super().__init__(message)
        self.cause = cause
        self.error_json = sanitized_error(cause or self, category="context_window")


def _status_code(exc):
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(exc, "http_status", None)
    if status is None:
        status = getattr(exc, "code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def is_context_window_error(exc):
    """Recognize only explicit HTTP-400 context/token-window failures."""
    if _status_code(exc) != 400:
        return False
    message = str(exc).lower()
    markers = (
        "context_length_exceeded", "context length", "maximum context length",
        "max context", "context window", "too many tokens", "token limit",
        "token_limit", "token-limit", "maximum_context_length",
        "input too long", "prompt too long",
    )
    return any(marker in message for marker in markers)


def sanitized_error(exc, category="provider"):
    """Return a small, prompt/credential-safe provider error snapshot."""
    if exc is None:
        return {"category": category, "type": "UnknownError"}
    message = str(exc)
    message = re.sub(r"sk-[A-Za-z0-9_-]+", "[redacted]", message)
    message = re.sub(r"(?i)(api[_ -]?key|authorization)\s*[:=]\s*\S+", r"\1=[redacted]", message)
    return {
        "category": category,
        "type": type(exc).__name__,
        "status_code": _status_code(exc),
        "message": message[:300],
    }


def is_retryable_error(exc):
    """Return whether an exception is a temporary provider/network failure."""
    status = _status_code(exc)
    if status == 429 or (status is not None and 500 <= status <= 599):
        return True
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    if any(token in name for token in ("timeout", "connection", "network", "temporar")):
        return True
    return bool(re.search(r"\b(?:429|500|502|503|504)\b", message)) or any(
        token in message for token in ("timed out", "connection reset", "temporarily unavailable")
    )


def call_model_with_retries(cfg, messages, task_key, paper_ref=None,
                            max_retries=2, sleep_fn=None, before_attempt=None):
    """Call a model with at most two additional attempts for transient errors.

    Returns the same ``(content, usage)`` pair as :func:`call_model` and
    attaches ``retry_count`` to usage.  Non-transient errors are raised
    immediately.  Keeping this policy here makes author, candidate and judge
    logical calls consistent while allowing tests to inject ``sleep_fn``.
    """
    sleep_fn = sleep_fn or time.sleep
    retries = max(0, int(max_retries or 0))
    for retry_count in range(retries + 1):
        try:
            if before_attempt is not None:
                before_attempt()
            content, usage = call_model(cfg, messages, task_key, paper_ref=paper_ref)
            usage = dict(usage or {})
            usage["retry_count"] = retry_count
            return content, usage
        except Exception as exc:
            if is_context_window_error(exc):
                raise ContextWindowError(
                    "候选输入超过供应商上下文窗口", cause=exc,
                ) from exc
            if not is_retryable_error(exc):
                raise
            if retry_count >= retries:
                raise RetryFailed(
                    f"模型临时调用失败（已重试 {retry_count} 次）: {exc}",
                    cause=exc, retry_count=retry_count,
                ) from exc
            sleep_fn(2 ** retry_count)


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
