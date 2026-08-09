"""Shared model calls and stable paper-learning context construction."""

import json
import logging

from source.documents import download_pdf, extract_text_from_pdf, get_cached_pdf_path, get_paper_pdf_path
from source.settings import build_chat_completion_kwargs, get_ai_task_config, get_prompt_profile
from source.storage import record_ai_usage

from .client import get_openai_client
from .json_support import _clean_json_content
from .messages import _authors_text
from .usage import _extract_usage, _value

logger = logging.getLogger(__name__)

def _record_usage(task_key, cfg, paper_data, response):
    """记录一次 AI 调用的 token 用量；失败不影响主流程。"""
    try:
        usage = _extract_usage(response)
        record_ai_usage({
            "task_key": task_key,
            "provider_key": cfg.get("provider_key", ""),
            "provider_name": cfg.get("provider_name", ""),
            "model": cfg.get("model", ""),
            "paper_id": paper_data.get("id"),
            "arxiv_id": paper_data.get("arxiv_id", ""),
            **usage,
        })
    except Exception as e:
        logger.debug(f"Failed to record AI usage: {e}")



def _paper_context_payload(paper_data, text_info):
    return {
        "arxiv_id": paper_data.get("arxiv_id", ""),
        "title": paper_data.get("title", ""),
        "authors": _authors_text(paper_data),
        "abstract": paper_data.get("abstract", ""),
        "used_pdf_cache": bool(text_info.get("used_pdf_cache")),
        "used_pdf_full_text": bool(text_info.get("used_pdf_full_text")),
        "paper_text": text_info.get("paper_text", ""),
    }


def get_learning_paper_text(paper_data):
    """获取论文学习上下文：优先复用本地 PDF 缓存，失败时回退摘要。"""
    paper_key = paper_data.get("paper_key") or paper_data.get("arxiv_id", "")
    arxiv_id = paper_data.get("arxiv_id", "")
    try:
        if paper_data.get("pdf_local_path") or not arxiv_id:
            pdf_path = get_paper_pdf_path(paper_data, download=True)
            used_pdf_cache = bool(pdf_path and not paper_data.get("pdf_local_path"))
        else:
            pdf_path = get_cached_pdf_path(arxiv_id)
            used_pdf_cache = bool(pdf_path)
            if not pdf_path and paper_data.get("pdf_url"):
                pdf_path = download_pdf(paper_data.get("pdf_url"), arxiv_id)
    except Exception as e:
        logger.warning(f"Failed to resolve learning PDF for {paper_key}: {e}")
        pdf_path = None
        used_pdf_cache = False

    paper_text = ""
    if pdf_path:
        try:
            paper_text = extract_text_from_pdf(pdf_path) or ""
        except Exception as e:
            logger.warning(f"Failed to extract learning PDF text for {paper_key}: {e}")
            paper_text = ""

    used_pdf_full_text = bool(paper_text)
    if not paper_text:
        paper_text = paper_data.get("abstract", "")

    return {
        "paper_text": paper_text,
        "used_pdf_cache": used_pdf_cache,
        "used_pdf_full_text": used_pdf_full_text,
    }


def build_paper_learning_messages(paper_data, task_key, task_instruction, volatile_messages, text_info=None):
    """
    构造缓存友好的论文学习消息。

    稳定消息固定在前：system -> 任务说明 -> 论文上下文。
    动态历史、题目、用户答案只追加在后面，避免破坏长 PDF 前缀缓存。
    """
    profile = get_prompt_profile(task_key)
    text_info = text_info or get_learning_paper_text(paper_data)
    payload = _paper_context_payload(paper_data, text_info)
    messages = [
        {"role": "system", "content": profile.get("system", "")},
        {"role": "user", "content": (task_instruction or profile.get("instruction", "") or "").strip()},
        {
            "role": "user",
            "content": "稳定论文上下文（JSON，固定字段顺序；后续消息不得改变此前缀）：\n"
            + json.dumps(payload, ensure_ascii=False, indent=2),
        },
    ]
    messages.extend(volatile_messages or [])
    return messages, text_info


def _normalise_analysis_result(result):
    """校验并补齐基础分析结果字段。"""
    if "tags" not in result or not isinstance(result["tags"], list):
        result["tags"] = ["Unknown"]
    if "summary_cn" not in result:
        result["summary_cn"] = ""
    if "summary_en" not in result:
        result["summary_en"] = ""
    if "value_comment" not in result:
        result["value_comment"] = ""
    try:
        result["rating"] = max(0, min(5, int(result.get("rating", 0))))
    except (TypeError, ValueError):
        result["rating"] = 0
    result.pop("qa_analysis", None)
    return result


def _normalise_deep_reading_result(result):
    """深度阅读只保留 Q&A，避免覆盖基础分析字段。"""
    qa_analysis = ""
    if isinstance(result, dict):
        qa_analysis = result.get("qa_analysis", "")
    if not isinstance(qa_analysis, str):
        qa_analysis = json.dumps(qa_analysis, ensure_ascii=False)
    return {"qa_analysis": qa_analysis.strip()}


def _normalise_recommendation_result(result):
    """校验并补齐个性化推荐结果字段。"""
    if not isinstance(result, dict):
        result = {}
    try:
        score = int(round(float(result.get("recommendation_score", 0) or 0)))
    except (TypeError, ValueError):
        score = 0
    reason = result.get("recommendation_reason", "")
    if not isinstance(reason, str):
        reason = json.dumps(reason, ensure_ascii=False)
    return {
        "recommendation_score": max(0, min(100, score)),
        "recommendation_reason": reason.strip(),
    }


def _call_ai(messages, paper_data, task_key):
    """
    核心 AI 调用函数：按任务配置发送 messages 到 AI API 并解析 JSON。

    该函数是所有分析模式的底层实现，负责：
      1. 构建 API 请求参数（模型、消息、温度、最大 token 数）
      2. 发送请求并提取响应内容
      3. 清理响应格式（去除 markdown 代码块标记）
      4. 解析 JSON 并校验/补全必要字段
      5. 根据分析模式决定是否保留 Q&A 内容

    Args:
        messages (list): OpenAI Chat Completions 消息列表
        paper_data (dict): 论文数据字典，至少包含 'arxiv_id' 和 'id' 字段
        task_key (str): basic_analysis/deep_reading/report_summary

    Returns:
        tuple: (result, error)
            - result (dict | None): 成功时返回解析后的 JSON 字典
            - error (str | None): 失败时返回错误信息字符串
    """
    cfg = get_ai_task_config(task_key)
    client = get_openai_client(cfg)

    try:
        content, usage = _call_ai_raw(messages, paper_data, task_key, cfg=cfg, client=client)

        # 清理 AI 返回的 JSON 内容
        content = _clean_json_content(content)

        # 解析 JSON 响应
        result = json.loads(content)
        return result, None

    # JSON 解析失败：AI 返回的内容不是有效 JSON
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error for paper {paper_data.get('arxiv_id', 'unknown')}: {e}")
        return None, str(e)
    # 其他异常：网络错误、API 限流、认证失败等
    except Exception as e:
        logger.error(f"API error for paper {paper_data.get('arxiv_id', 'unknown')}: {e}")
        return None, str(e)


def _call_ai_raw(messages, paper_data, task_key, cfg=None, client=None):
    """调用模型并返回原始文本和 usage；同时记录用量。"""
    cfg = cfg or get_ai_task_config(task_key)
    client = client or get_openai_client(cfg)
    kwargs = build_chat_completion_kwargs(cfg, messages)
    response = client.chat.completions.create(**kwargs)
    usage = _extract_usage(response)
    _record_usage(task_key, cfg, paper_data, response)

    choices = _value(response, "choices", []) or []
    first_choice = choices[0] if choices else {}
    usage = dict(usage or {})
    usage["finish_reason"] = str(_value(first_choice, "finish_reason", "") or "")
    message = _value(first_choice, "message", {}) or {}
    content = (_value(message, "content", "") or "").strip()
    return content, usage


