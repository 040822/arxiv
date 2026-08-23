"""Candidate runner: execute deep-reading and chat tracks against frozen suites."""

import json
import logging
import re

from ._ai import ContextWindowError, RetryFailed, call_model_with_retries
from .config import resolve_model_config
from .evidence import parse_deep_reading_questions, split_qa_sections

logger = logging.getLogger(__name__)

CONTINUATION_INSTRUCTION = (
    "上一个回答因长度限制中断或缺少问题。请只从中断处继续回答缺失的问题，"
    "不要重复已有内容，不要解释，不要改变已有格式。"
)


class BudgetExceeded(RuntimeError):
    """评测运行超出调用数硬预算。"""


def _paper_payload(paper):
    authors = paper.get("authors") or []
    if isinstance(authors, list):
        authors = ", ".join(authors)
    return {
        "arxiv_id": paper.get("arxiv_id", ""),
        "title": paper.get("title", ""),
        "authors": authors,
        "abstract": paper.get("abstract", ""),
        "paper_text": paper.get("full_text", ""),
    }


def _frozen_snapshot(suite, track):
    return {
        "deep_reading": suite.get("deep_reading_prompt") or {},
        "chat": suite.get("paper_chat_prompt") or {},
    }[track]


def _deep_reading_messages(suite, paper):
    frozen = _frozen_snapshot(suite, "deep_reading")
    payload = _paper_payload(paper)
    return [
        {"role": "system", "content": frozen.get("system", "")},
        {"role": "user", "content": "冻结论文上下文（JSON，固定字段顺序）：\n" + json.dumps(payload, ensure_ascii=False, indent=2)},
        {"role": "user", "content": frozen.get("instruction", "")},
    ]


def _chat_messages(suite, paper, questions, history):
    """system → 稳定任务说明 → 稳定论文上下文 → 动态历史与当前问题。"""
    frozen = _frozen_snapshot(suite, "chat")
    payload = _paper_payload(paper)
    messages = [
        {"role": "system", "content": frozen.get("system", "")},
        {"role": "user", "content": frozen.get("instruction", "")},
        {"role": "user", "content": "冻结论文上下文（JSON，固定字段顺序；后续消息不得改变此前缀）：\n" + json.dumps(payload, ensure_ascii=False, indent=2)},
    ]
    for index, answer in enumerate(history):
        if index >= len(questions):
            break
        messages.append({"role": "user", "content": questions[index].get("question", "")})
        messages.append({"role": "assistant", "content": answer})
    if len(history) < len(questions):
        messages.append({"role": "user", "content": questions[len(history)].get("question", "")})
    return messages


def _call(cfg, messages, task_key, paper_ref, charge_callback=None):
    # Charge immediately before *each* API attempt.  In particular, a deep
    # reading continuation is a second candidate call and must survive a
    # process restart even if the response cannot be persisted afterwards.
    content, usage = call_model_with_retries(
        cfg, messages, task_key, paper_ref=paper_ref,
        before_attempt=charge_callback,
    )
    return content, usage


def _retry_failure(exc):
    cause = getattr(exc, "cause", None) or exc
    return {
        "type": type(cause).__name__,
        "message": str(cause),
        "retry_count": int(getattr(exc, "retry_count", 0) or 0),
    }


def _context_failure(exc, messages, *, raw_output="", continuation_count=0,
                     usage_json=None, retry_count=0):
    return {
        "prompt_snapshot": messages,
        "raw_output": raw_output,
        "parsed": {},
        "status": "context_error",
        "finish_reason": "error",
        "usage_json": usage_json or {},
        "latency_ms": (usage_json or {}).get("latency_ms", 0),
        "continuation_count": continuation_count,
        "retry_count": retry_count,
        "error_json": dict(getattr(exc, "error_json", {}) or {}),
    }


def _qa_sections_from_content(content):
    """从深度阅读输出中提取 Q 章节（文本级解析，不依赖 JSON 完整性）。

    模型输出可能是：合法 JSON（转义换行）、原始换行文本、嵌套/重复 JSON 包络。
    统一把转义换行展开、把 `### Qn:` 前的引号换成换行后，
    按 `### Qn:` 标题解析章节，天然兼容以上各种形态。
    """
    text = str(content or "").replace("\\n", "\n").replace('\\"', '"')
    text = re.sub(r'"\s*(?=###\s*Q\d+\s*:)', "\n", text)
    return split_qa_sections(text)


def run_deep_reading(suite, paper, cfg, paper_ref, charge_callback=None):
    """深度阅读轨：一次冻结 Prompt 调用 + 最多一次续写；返回 (response, 调用次数)。"""
    messages = _deep_reading_messages(suite, paper)
    expected_numbers = {
        "q{}".format(question["number"])
        for question in parse_deep_reading_questions(
            (_frozen_snapshot(suite, "deep_reading") or {}).get("instruction", "")
        )
    }
    try:
        content, usage = _call(
            cfg, messages, "benchmark_runner_deep_reading", paper_ref,
            charge_callback=charge_callback,
        )
    except ContextWindowError as exc:
        return _context_failure(exc, messages), 1
    except RetryFailed as exc:
        return {
            "prompt_snapshot": messages,
            "raw_output": "",
            "parsed": {},
            "status": "retry_failed",
            "finish_reason": "error",
            "usage_json": {"retry_count": exc.retry_count},
            "latency_ms": 0,
            "continuation_count": 0,
            "retry_count": exc.retry_count,
            "error_json": _retry_failure(exc),
        }, 1 + exc.retry_count
    continuation_count = 0
    finish_reason = usage.get("finish_reason", "")
    retry_count_total = int(usage.get("retry_count", 0) or 0)

    def _parse_qa(content):
        sections = _qa_sections_from_content(content)
        if sections:
            return {"q{}".format(number): text for number, text in sections.items()}, "ok"
        return {}, "format_error"

    parsed, status = _parse_qa(content)
    if (
        finish_reason in {"length", "max_tokens"}
        or not parsed
        or (expected_numbers and not expected_numbers.issubset(parsed))
    ):
        # Mark the attempt before entering the call so an API failure still
        # contributes to the persisted 1 + continuation_count accounting.
        continuation_count = 1
        try:
            continuation, continuation_usage = _call(
                cfg,
                messages + [
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": CONTINUATION_INSTRUCTION},
                ],
                "benchmark_runner_deep_reading",
                paper_ref,
                charge_callback=charge_callback,
            )
            content = (content + "\n\n" + continuation).strip()
            finish_reason = continuation_usage.get("finish_reason", finish_reason)
            retry_count_total += int(continuation_usage.get("retry_count", 0) or 0)
            usage["completion_tokens"] = int(usage.get("completion_tokens", 0) or 0) + int(
                continuation_usage.get("completion_tokens", 0) or 0
            )
            usage["total_tokens"] = int(usage.get("total_tokens", 0) or 0) + int(
                continuation_usage.get("total_tokens", 0) or 0
            )
            parsed, status = _parse_qa(content)
        except BudgetExceeded:
            raise
        except ContextWindowError as exc:
            return _context_failure(
                exc, messages, raw_output=content,
                continuation_count=continuation_count, usage_json=usage,
                retry_count=retry_count_total,
            ), 1 + continuation_count
        except RetryFailed as exc:
            return {
                "prompt_snapshot": messages,
                "raw_output": content,
                "parsed": parsed,
                "status": "retry_failed",
                "finish_reason": finish_reason or "error",
                "usage_json": usage,
                "latency_ms": usage.get("latency_ms", 0),
                "continuation_count": continuation_count,
                "retry_count": retry_count_total + exc.retry_count,
                "error_json": _retry_failure(exc),
            }, 1 + continuation_count + exc.retry_count
        except Exception as exc:
            logger.warning(f"benchmark deep reading continuation failed: {exc}")

    if status == "ok" and not parsed:
        status = "empty"
    if expected_numbers and not expected_numbers.issubset(parsed):
        # Keep partial output for diagnosis/judging, but make the response an
        # explicit format anomaly so the review sampler cannot cap it away.
        status = "format_error"
    return {
        "prompt_snapshot": messages,
        "raw_output": content,
        "parsed": parsed,
        "status": status,
        "finish_reason": finish_reason,
        "usage_json": usage,
        "latency_ms": usage.get("latency_ms", 0),
        "continuation_count": continuation_count,
        "retry_count": retry_count_total,
        "error_json": {},
    }, 1 + continuation_count


def run_chat_round(suite, paper, cfg, paper_ref, questions, history, charge_callback=None):
    """交流轨单轮：用候选自身历史（可来自已保存响应）构造消息并调用一次。"""
    messages = _chat_messages(suite, paper, questions, history)
    try:
        content, usage = _call(
            cfg, messages, "benchmark_runner_chat", paper_ref,
            charge_callback=charge_callback,
        )
    except ContextWindowError as exc:
        return _context_failure(exc, messages), 1
    except RetryFailed as exc:
        return {
            "prompt_snapshot": messages,
            "raw_output": "",
            "parsed": {},
            "status": "retry_failed",
            "finish_reason": "error",
            "usage_json": {"retry_count": exc.retry_count},
            "latency_ms": 0,
            "continuation_count": 0,
            "retry_count": exc.retry_count,
            "error_json": _retry_failure(exc),
        }, 1 + exc.retry_count
    return {
        "prompt_snapshot": messages,
        "raw_output": content,
        "parsed": {"answer": content},
        "status": "ok" if content.strip() else "empty",
        "finish_reason": usage.get("finish_reason", ""),
        "usage_json": usage,
        "latency_ms": usage.get("latency_ms", 0),
        "continuation_count": 0,
        "retry_count": int(usage.get("retry_count", 0) or 0),
        "error_json": {},
    }, 1 + int(usage.get("retry_count", 0) or 0)
