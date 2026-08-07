"""
AI 论文分析模块

本模块负责调用 OpenAI 兼容 API 对 arXiv 论文进行分析与学习辅助。
支持以下任务：
  - 基础分析（basic）：仅使用论文摘要，生成标签、中文翻译和简评，不含 Q&A 深度阅读
  - 深度阅读（full）：下载 PDF 提取全文，只生成 Q&A 深度阅读，不覆盖基础分析字段
  - 论文学习：复用 PDF 缓存，支持自由讨论、主动问答评分和苏格拉底追问

核心流程：
  1. 从数据库获取未分析的论文
  2. 构建 prompt（稳定任务说明 + 动态论文 JSON）
  3. 并发调用 AI API 进行分析
  4. 解析 JSON 响应并写入数据库
"""

import json
import logging
import re
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import DefaultHttpxClient, OpenAI
from config import TAG_CANDIDATES, RATING_CRITERIA
from source.settings import (
    build_chat_completion_kwargs, get_ai_config, get_ai_task_config, get_prompt_profile,
    get_concurrency, get_personalization_config, get_proxy_config, get_research_interest_hash
)
from source.storage import (
    get_connection, insert_analysis, update_analysis, get_unanalyzed_papers, record_ai_usage,
    get_papers_for_recommendation, update_recommendation_result
)
from pdf_reader import (
    download_pdf, extract_text_from_pdf, get_cached_pdf_path,
    get_paper_full_text, get_paper_pdf_path,
)
from source.value_coercion import as_int

# 模块级日志记录器
logger = logging.getLogger(__name__)


# ============================================================
# 客户端初始化
# ============================================================

def _select_proxy_for_base_url(base_url, proxy_config=None):
    """按供应商 base_url 的 scheme 选择全局代理地址。"""
    proxy = proxy_config if proxy_config is not None else get_proxy_config()
    if not proxy.get("enabled"):
        return ""
    scheme = urlparse(str(base_url or "")).scheme.lower()
    if scheme == "http":
        return str(proxy.get("http") or proxy.get("https") or "").strip()
    return str(proxy.get("https") or proxy.get("http") or "").strip()


def _build_openai_http_client(cfg):
    """创建不读取环境变量代理的 OpenAI HTTP 客户端。"""
    kwargs = {"trust_env": False}
    proxy_url = _select_proxy_for_base_url(cfg.get("base_url", ""))
    if proxy_url:
        kwargs["proxy"] = proxy_url
    return DefaultHttpxClient(**kwargs)


def get_openai_client(cfg=None):
    """
    创建并返回 OpenAI 客户端实例。

    从运行时配置（data/settings.json）中读取当前激活供应商的 API 密钥和基础 URL，
    创建一个 OpenAI SDK 客户端。该客户端支持所有 OpenAI 兼容 API
    （如 DeepSeek、通义千问、Moonshot 等）。

    Returns:
        OpenAI: 配置好的 OpenAI 客户端实例
    """
    cfg = cfg or get_ai_config()
    return OpenAI(
        api_key=cfg["api_key"],
        base_url=cfg["base_url"],
        http_client=_build_openai_http_client(cfg),
    )


# ============================================================
# 核心 AI 调用逻辑
# ============================================================

def _clean_json_content(content):
    """清理 AI 返回的 JSON 内容，修复常见的格式问题。

    AI 模型返回的 JSON 可能存在以下问题：
    1. 包裹在 markdown 代码块中：```json ... ```
    2. 包含无效的反斜杠转义：\\_、\\[、\\] 等
    3. 前后包含多余文本（解释说明等）

    Args:
        content (str): AI 返回的原始文本

    Returns:
        str: 清理后的 JSON 字符串
    """
    import re

    # 步骤 1：去除 markdown 代码块标记
    # AI 有时会将 JSON 包裹在 ```json ... ``` 中
    if content.startswith("```"):
        lines = content.split("\n")
        # 去除首尾的 ``` 行
        content = "\n".join(lines[1:])
        if content.endswith("```"):
            content = content[:-3].strip()

    # 步骤 2：提取 JSON 对象
    # 如果响应中包含非 JSON 文本（如解释说明），只提取 { ... } 部分
    json_match = re.search(r'\{[\s\S]*\}', content)
    if json_match:
        content = json_match.group(0)

    # 步骤 3：修复 JSON 字符串内部的无效反斜杠转义。
    # 不能用简单正则全局替换：合法的 "\\alpha" 会被误改成 "\alpha"。
    content = _repair_invalid_json_escapes(content)

    return content


def _repair_invalid_json_escapes(content):
    """只移除 JSON 字符串内无效 escape 的反斜杠，保留合法转义。"""
    result = []
    in_string = False
    escaped = False
    i = 0
    valid_simple_escapes = set('"\\/bfnrt')

    while i < len(content):
        char = content[i]

        if not in_string:
            result.append(char)
            if char == '"':
                in_string = True
            i += 1
            continue

        if escaped:
            if char in valid_simple_escapes:
                result.append(char)
            elif char == "u":
                hex_part = content[i + 1:i + 5]
                if len(hex_part) == 4 and all(c in "0123456789abcdefABCDEF" for c in hex_part):
                    result.append(char)
                    result.append(hex_part)
                    i += 4
                else:
                    result.pop()
                    result.append(char)
            else:
                result.pop()
                result.append(char)
            escaped = False
            i += 1
            continue

        if char == "\\":
            result.append(char)
            escaped = True
            i += 1
            continue

        result.append(char)
        if char == '"':
            in_string = False
        i += 1

    if escaped and result and result[-1] == "\\":
        result.pop()
    return "".join(result)


def _value(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _extract_usage(response):
    """从 OpenAI SDK 响应中提取 token 用量，兼容 dict 和对象响应。"""
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


def _authors_text(paper_data):
    authors = paper_data.get("authors", "")
    if isinstance(authors, list):
        return ", ".join(authors)
    return authors


def _render_instruction(instruction):
    """渲染稳定 prompt 前缀；论文动态数据会放在单独 message 中。"""
    return (instruction or "").replace(
        "{tag_candidates}",
        ", ".join(TAG_CANDIDATES[:30]),
    ).replace(
        "{rating_criteria}",
        RATING_CRITERIA,
    )


def _build_task_messages(task_key, payload):
    """
    构建任务消息列表，包含稳定的 system + instruction 前缀和动态论文 JSON 数据。
    
    2026-08-06
    对于深度阅读和手动导入任务，我们发现deepseek v4 flash会在原有的长文本前缀中“淹没”指令，导致漏答问题。为提升指令遵循率，我们将 instruction 放在动态数据之后。
    其余任务通常无长文本数据（chat任务使用build_paper_learning_messages），所以说明 instruction 放在前面更符合直觉。
    """
    
    profile = get_prompt_profile(task_key)
    system = {"role": "system", "content": profile.get("system", "")}
    instruction = {"role": "user", "content": _render_instruction(profile.get("instruction", ""))}
    data = {
        "role": "user",
        "content": "动态输入数据（JSON，固定字段顺序）：\n" + json.dumps(payload, ensure_ascii=False, indent=2),
    }

    if task_key in {"deep_reading", "paper_import"}:
        return [system, data, instruction]
    return [system, instruction, data]


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


def extract_paper_import_metadata(paper_text):
    """Extract editable bibliographic metadata from an uploaded PDF."""
    text = str(paper_text or "").strip()
    if not text:
        return None, "PDF 未提取到可读文本"
    messages = _build_task_messages("paper_import", {"paper_text": text[:50000]})
    result, error = _call_ai(messages, {"id": None, "arxiv_id": "paper-import"}, "paper_import")
    if error or not isinstance(result, dict):
        return None, error or "模型未返回元数据"
    authors = result.get("authors")
    if isinstance(authors, str):
        authors = [item.strip() for item in authors.split(",") if item.strip()]
    if not isinstance(authors, list):
        authors = []
    return {
        "title": str(result.get("title") or "").strip(),
        "authors": [str(item).strip() for item in authors if str(item).strip()],
        "abstract": str(result.get("abstract") or "").strip(),
        "venue": str(result.get("venue") or "").strip(),
        "published_date": str(result.get("published_date") or "").strip(),
    }, None



# ============================================================
# 分析模式：基础分析与深度阅读
# ============================================================

def analyze_paper_basic(paper_data):
    """
    基础分析模式：仅使用论文摘要进行 AI 分析，不下载 PDF。

    该模式速度较快，适合大批量快速分析。分析结果包含：
      - 标签分类、中文翻译、价值评价
      - 不包含 Q&A 深度阅读

    Args:
        paper_data (dict): 论文数据字典，需包含：
            id, arxiv_id, title, authors, abstract

    Returns:
        tuple: (paper_data, result, error) — 原始论文数据透传
    """
    abstract = str(paper_data.get("abstract") or "").strip()
    paper_text = ""
    if not abstract:
        try:
            pdf_path = get_paper_pdf_path(paper_data, download=True)
            if pdf_path:
                paper_text = (extract_text_from_pdf(pdf_path) or "")[:50000]
        except Exception as exc:
            logger.warning("Basic analysis PDF fallback failed: %s", exc)
    payload = {
        "arxiv_id": paper_data.get("arxiv_id", ""),
        "title": paper_data.get("title", ""),
        "authors": _authors_text(paper_data),
        "abstract": abstract,
        "paper_text": paper_text,
    }
    messages = _build_task_messages("basic_analysis", payload)
    result, error = _call_ai(messages, paper_data, "basic_analysis")
    if result:
        result = _normalise_analysis_result(result)
    return paper_data, result, error


def analyze_paper_full(paper_data):
    """
    深度阅读模式：下载 PDF 并提取全文进行 Q&A 分析。

    该模式会尝试下载论文 PDF 并提取全文内容。如果 PDF 下载或提取失败，
    自动回退到使用摘要进行分析。分析结果只包含 Q&A 深度阅读。

    Args:
        paper_data (dict): 论文数据字典，需包含：
            id, arxiv_id, title, authors, abstract, pdf_url

    Returns:
        tuple: (paper_data, result, error) — 原始论文数据透传
    """
    # 尝试下载 PDF 并提取全文
    full_text = None
    try:
        source_type = str(paper_data.get("source_type") or "")
        is_arxiv = source_type == "arxiv" or (
            not source_type and bool(paper_data.get("arxiv_id"))
        )
        pdf_path = None
        if paper_data.get("pdf_local_path") or not is_arxiv:
            pdf_path = get_paper_pdf_path(paper_data, download=False)
        if pdf_path:
            full_text = extract_text_from_pdf(pdf_path)
        elif paper_data.get("pdf_url"):
            full_text = get_paper_full_text(
                paper_data.get("pdf_url"),
                paper_data.get("paper_key") or paper_data.get("arxiv_id", ""),
                max_chars=None,
            )
    except Exception as exc:
        logger.warning(
            "Failed to resolve full text for %s: %s",
            paper_data.get("paper_key") or paper_data.get("arxiv_id"), exc,
        )

    # 优先使用 PDF 全文，提取失败时回退到摘要
    if full_text:
        abstract_or_text = full_text
    else:
        abstract_or_text = paper_data.get("abstract", "")

    payload = {
        "arxiv_id": paper_data.get("arxiv_id", ""),
        "title": paper_data.get("title", ""),
        "authors": _authors_text(paper_data),
        "abstract": paper_data.get("abstract", ""),
        "paper_text": abstract_or_text,
        "used_pdf_full_text": bool(full_text),
    }
    messages = _build_task_messages("deep_reading", payload)
    profile = get_prompt_profile("deep_reading")
    expected_questions = sorted({
        int(match.group(1))
        for match in re.finditer(r"###\s*Q(\d+)\s*:", profile.get("instruction", ""), re.IGNORECASE)
    })

    def parse_result(raw_content):
        parsed = json.loads(_clean_json_content(raw_content))
        return _normalise_deep_reading_result(parsed)

    def question_sections(qa_text):
        sections = {}
        matches = list(re.finditer(r"(?m)^###\s*Q(\d+)\s*:", qa_text or "", re.IGNORECASE))
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(qa_text)
            sections[int(match.group(1))] = qa_text[match.start():end].strip()
        return sections

    try:
        raw_content, usage = _call_ai_raw(messages, paper_data, "deep_reading")
    except Exception as e:
        logger.error(f"Deep reading error for paper {paper_data.get('arxiv_id', 'unknown')}: {e}")
        return paper_data, None, str(e)

    finish_reason = str((usage or {}).get("finish_reason") or "")
    continuation_used = False
    try:
        result = parse_result(raw_content)
    except json.JSONDecodeError:
        continuation_used = True
        continuation_instruction = (
            "上一个 JSON 输出因长度中断。请只从中断字符之后继续输出，不要重复已有内容，"
            "不要添加 Markdown 代码围栏或解释。"
        )
        try:
            suffix, continuation_usage = _call_ai_raw(
                messages + [
                    {"role": "assistant", "content": raw_content},
                    {"role": "user", "content": continuation_instruction},
                ],
                paper_data,
                "deep_reading",
            )
            finish_reason = str((continuation_usage or {}).get("finish_reason") or finish_reason)
            result = parse_result(raw_content + suffix)
        except Exception as repair_error:
            logger.warning(
                f"Deep reading continuation failed for paper {paper_data.get('arxiv_id', 'unknown')}: "
                f"{repair_error}"
            )
            return paper_data, {
                "qa_analysis": "",
                "complete": False,
                "continuation_used": True,
                "missing_questions": [f"Q{number}" for number in expected_questions],
                "finish_reason": finish_reason,
            }, None

    sections = question_sections(result.get("qa_analysis", ""))
    missing = [number for number in expected_questions if number not in sections]
    if finish_reason in {"length", "max_tokens"} and expected_questions and not missing:
        missing = [expected_questions[-1]]

    if missing and not continuation_used:
        continuation_used = True
        repair_instruction = (
            "深度阅读输出缺少或未完整回答以下问题："
            + ", ".join(f"Q{number}" for number in missing)
            + "。请只返回这些问题的完整 Q&A，严格使用合法 JSON："
            + '{"qa_analysis":"### Qn: 问题\\n\\n完整回答"}'
        )
        try:
            repair_raw, repair_usage = _call_ai_raw(
                messages + [{"role": "user", "content": repair_instruction}],
                paper_data,
                "deep_reading",
            )
            repair_result = parse_result(repair_raw)
            repair_sections = question_sections(repair_result.get("qa_analysis", ""))
            sections.update({number: text for number, text in repair_sections.items() if number in missing})
            missing = [number for number in expected_questions if number not in sections]
            repaired_questions = [number for number in expected_questions if number in repair_sections]
            finish_reason = str((repair_usage or {}).get("finish_reason") or finish_reason)
            if finish_reason in {"length", "max_tokens"} and repaired_questions:
                last_repaired = repaired_questions[-1]
                if last_repaired not in missing:
                    missing.append(last_repaired)
            result["qa_analysis"] = "\n\n".join(
                sections[number] for number in expected_questions if number in sections
            )
        except Exception as e:
            logger.warning(f"Deep reading repair failed for {paper_data.get('arxiv_id', 'unknown')}: {e}")

    result.update({
        "complete": not missing,
        "continuation_used": continuation_used,
        "missing_questions": [f"Q{number}" for number in missing],
        "finish_reason": finish_reason,
    })
    return paper_data, result, None


def analyze_paper_recommendation(paper_data, research_interests=None, interest_hash=None):
    """
    个性化推荐模式：按用户研究兴趣为一篇论文计算推荐分。

    该任务与基础分析/深度阅读/报告导读使用独立模型路由，只依赖论文摘要
    和已有基础分析字段，不下载 PDF。
    """
    research_interests = (research_interests or get_personalization_config().get("research_interests", "")).strip()
    interest_hash = interest_hash or get_research_interest_hash(research_interests)
    if not research_interests or not interest_hash:
        return paper_data, None, "未设置研究兴趣"

    payload = {
        "research_interests": research_interests,
        "paper": {
            "arxiv_id": paper_data.get("arxiv_id", ""),
            "title": paper_data.get("title", ""),
            "authors": _authors_text(paper_data),
            "abstract": paper_data.get("abstract", ""),
            "categories": paper_data.get("categories") or [],
            "tags": paper_data.get("tags") or [],
            "rating": paper_data.get("rating") or 0,
            "summary_cn": paper_data.get("summary_cn") or "",
            "value_comment": paper_data.get("value_comment") or "",
        },
    }
    messages = _build_task_messages("recommendation", payload)
    result, error = _call_ai(messages, paper_data, "recommendation")
    if result:
        result = _normalise_recommendation_result(result)
        result["recommendation_interest_hash"] = interest_hash
    return paper_data, result, error


def _parse_ai_json(content):
    cleaned = _clean_json_content(content)
    return json.loads(cleaned)


def _learning_meta(text_info, usage):
    return {
        "used_pdf_cache": bool(text_info.get("used_pdf_cache")),
        "used_pdf_full_text": bool(text_info.get("used_pdf_full_text")),
        **(usage or {}),
    }


def _quiz_question_count(mode):
    return 6 if mode == "standard6" else 3


def _normalise_quiz_questions(result, mode):
    count = _quiz_question_count(mode)
    questions = result.get("questions", []) if isinstance(result, dict) else []
    normalised = []
    for item in questions:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        if not question:
            continue
        expected = item.get("expected_points", [])
        if isinstance(expected, str):
            expected = [expected]
        elif not isinstance(expected, list):
            expected = []
        normalised.append({
            "question": question,
            "expected_points": [str(point).strip() for point in expected if str(point).strip()],
        })
        if len(normalised) >= count:
            break
    return normalised


def _normalise_feedback_result(result):
    result = result if isinstance(result, dict) else {}
    try:
        score = int(round(float(result.get("score", 0) or 0)))
    except (TypeError, ValueError):
        score = 0

    def list_value(key):
        value = result.get(key, [])
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if value:
            return [str(value).strip()]
        return []

    return {
        "score": max(0, min(5, score)),
        "feedback": str(result.get("feedback") or "").strip(),
        "correct_points": list_value("correct_points"),
        "missing_points": list_value("missing_points"),
        "misconceptions": list_value("misconceptions"),
        "improved_answer": str(result.get("improved_answer") or "").strip(),
    }


def _normalise_socratic_result(result):
    feedback = _normalise_feedback_result(result)
    feedback["next_question"] = str((result or {}).get("next_question") or "").strip()
    if not feedback["next_question"]:
        feedback["next_question"] = "请你先用自己的话概括这篇论文最核心的问题和方法。"
    return feedback


def chat_about_paper(paper_data, user_message, history=None):
    """基于 PDF 全文上下文与用户自由讨论论文。"""
    history = (history or [])[-12:]
    volatile = []
    for item in history:
        role = item.get("role") if isinstance(item, dict) else ""
        if role not in {"user", "assistant"}:
            continue
        volatile.append({"role": role, "content": str(item.get("content") or "")})
    volatile.append({"role": "user", "content": str(user_message or "").strip()})

    profile = get_prompt_profile("paper_chat")
    messages, text_info = build_paper_learning_messages(
        paper_data,
        "paper_chat",
        "论文自由讨论任务说明：\n" + profile.get("instruction", ""),
        volatile,
    )
    try:
        content, usage = _call_ai_raw(messages, paper_data, "paper_chat")
        return content.strip(), None, _learning_meta(text_info, usage)
    except Exception as e:
        logger.error(f"Paper chat error for {paper_data.get('arxiv_id', 'unknown')}: {e}")
        return None, str(e), _learning_meta(text_info, {})


def generate_paper_quiz(paper_data, mode="quick3"):
    """按需生成 3 题或 6 题主动回忆练习。"""
    mode = mode if mode in {"quick3", "standard6"} else "quick3"
    count = _quiz_question_count(mode)
    profile = get_prompt_profile("paper_quiz")
    volatile = [{
        "role": "user",
        "content": f"""本轮任务：生成 {count} 道论文主动回忆练习题。

请严格返回合法 JSON，不要返回额外解释：
{{
  "questions": [
    {{"question": "问题文本", "expected_points": ["参考要点1", "参考要点2"]}}
  ]
}}

要求：
- 题目必须覆盖论文主线、核心方法、关键实验、局限或适用边界
- 问题应促使用户用自己的话解释，不要只问名词定义
- expected_points 用于后续评分，写成简短要点
- 必须恰好返回 {count} 道题
""",
    }]
    messages, text_info = build_paper_learning_messages(
        paper_data,
        "paper_quiz",
        "论文主动问答任务说明：\n" + profile.get("instruction", ""),
        volatile,
    )
    try:
        content, usage = _call_ai_raw(messages, paper_data, "paper_quiz")
        questions = _normalise_quiz_questions(_parse_ai_json(content), mode)
        if len(questions) != count:
            return None, f"模型返回题目数量不正确：{len(questions)}/{count}", _learning_meta(text_info, usage)
        return questions, None, _learning_meta(text_info, usage)
    except Exception as e:
        logger.error(f"Paper quiz generation error for {paper_data.get('arxiv_id', 'unknown')}: {e}")
        return None, str(e), _learning_meta(text_info, {})


def grade_quiz_answer(paper_data, question, answer_text):
    """评价用户对单题的回答，并给出纠错反馈。"""
    profile = get_prompt_profile("paper_quiz")
    volatile = [{
        "role": "user",
        "content": "本轮任务：评价用户答案。\n"
        + json.dumps({
            "question": question.get("question", ""),
            "expected_points": question.get("expected_points", ""),
            "user_answer": answer_text,
        }, ensure_ascii=False, indent=2)
        + """

请严格返回合法 JSON，不要返回额外解释：
{
  "score": 0,
  "feedback": "总体反馈",
  "correct_points": ["用户答对的点"],
  "missing_points": ["用户漏掉的关键点"],
  "misconceptions": ["用户可能存在的误解"],
  "improved_answer": "一段更好的参考答案"
}

score 必须是 0 到 5 的整数。
""",
    }]
    messages, text_info = build_paper_learning_messages(
        paper_data,
        "paper_quiz",
        "论文主动问答任务说明：\n" + profile.get("instruction", ""),
        volatile,
    )
    try:
        content, usage = _call_ai_raw(messages, paper_data, "paper_quiz")
        feedback = _normalise_feedback_result(_parse_ai_json(content))
        return feedback, None, _learning_meta(text_info, usage)
    except Exception as e:
        logger.error(f"Paper quiz grading error for {paper_data.get('arxiv_id', 'unknown')}: {e}")
        return None, str(e), _learning_meta(text_info, {})


def socratic_reply(paper_data, session_history=None, user_answer=None):
    """独立苏格拉底追问模式：根据历史和用户回答生成反馈与下一问。"""
    profile = get_prompt_profile("paper_quiz")
    volatile_payload = {
        "session_history": session_history or [],
        "user_answer": user_answer or "",
        "is_first_turn": not session_history and not user_answer,
    }
    volatile = [{
        "role": "user",
        "content": "本轮任务：进行独立苏格拉底式论文追问。\n"
        + json.dumps(volatile_payload, ensure_ascii=False, indent=2)
        + """

请严格返回合法 JSON，不要返回额外解释：
{
  "score": 0,
  "feedback": "如果这是第一轮，可为空；否则指出用户答案的亮点、缺口或误解",
  "correct_points": [],
  "missing_points": [],
  "misconceptions": [],
  "improved_answer": "如果这是第一轮，可为空；否则给出更好的回答",
  "next_question": "下一轮追问"
}

要求：
- 第一轮只提出一个能打开论文主线的问题
- 后续轮次根据用户回答继续追问，不要直接讲完整答案
- next_question 必须具体、可回答，避免泛泛而谈
""",
    }]
    messages, text_info = build_paper_learning_messages(
        paper_data,
        "paper_quiz",
        "论文主动问答任务说明：\n" + profile.get("instruction", ""),
        volatile,
    )
    try:
        content, usage = _call_ai_raw(messages, paper_data, "paper_quiz")
        feedback = _normalise_socratic_result(_parse_ai_json(content))
        return feedback, None, _learning_meta(text_info, usage)
    except Exception as e:
        logger.error(f"Paper Socratic error for {paper_data.get('arxiv_id', 'unknown')}: {e}")
        return None, str(e), _learning_meta(text_info, {})


def _get_report_summary_context(report_date, limit=30):
    """读取报告导读所需的轻量论文上下文，避免把全文再次送给模型。"""
    with get_connection() as conn:
        cursor = conn.cursor()
        interest_hash = get_research_interest_hash()
        cursor.execute("""
            SELECT p.arxiv_id, p.title, p.authors, p.categories,
                   a.tags, a.rating, a.value_comment, a.summary_cn,
                   a.recommendation_score, a.recommendation_interest_hash
            FROM papers p
            LEFT JOIN analysis a ON p.id = a.paper_id
            WHERE p.published_date = ? AND p.ingest_mode = 'feed'
            ORDER BY
                CASE
                    WHEN ? <> ''
                     AND a.recommendation_interest_hash = ?
                     AND a.recommendation_score IS NOT NULL
                    THEN a.recommendation_score
                    ELSE -1
                END DESC,
                COALESCE(a.rating, 0) DESC,
                p.arxiv_id
            LIMIT ?
        """, (report_date, interest_hash, interest_hash, limit))
        rows = cursor.fetchall()


    papers = []
    for row in rows:
        item = dict(row)
        for field in ("authors", "categories", "tags"):
            if item.get(field) and isinstance(item[field], str):
                try:
                    item[field] = json.loads(item[field])
                except json.JSONDecodeError:
                    item[field] = []
        papers.append({
            "arxiv_id": item.get("arxiv_id", ""),
            "title": item.get("title", ""),
            "authors": item.get("authors") or [],
            "categories": item.get("categories") or [],
            "tags": item.get("tags") or [],
            "rating": item.get("rating") or 0,
            "recommendation_score": item.get("recommendation_score")
            if interest_hash and item.get("recommendation_interest_hash") == interest_hash
            else None,
            "value_comment": item.get("value_comment") or "",
            "summary_cn": item.get("summary_cn") or "",
        })
    return papers


def generate_report_ai_summary(report_date):
    """使用 report_summary 任务模型生成一段可选的日报导读。"""
    papers = _get_report_summary_context(report_date)
    if not papers:
        return None, "无论文数据"

    payload = {
        "report_date": report_date,
        "paper_count": len(papers),
        "papers": papers,
    }
    paper_data = {"id": None, "arxiv_id": f"report:{report_date}"}
    messages = _build_task_messages("report_summary", payload)
    result, error = _call_ai(messages, paper_data, "report_summary")
    if error:
        return None, error
    summary = (result or {}).get("summary", "")
    if not summary:
        return None, "模型未返回 summary 字段"
    return summary.strip(), None


# ============================================================
# 批量分析与并发控制
# ============================================================

def analyze_papers(papers, concurrency=None, progress_callback=None):
    """
    批量分析指定论文列表：使用线程池并发执行基础分析。

    该函数是批量分析的入口，负责：
      1. 使用 ThreadPoolExecutor 并发执行基础分析
      2. 将分析结果写入数据库（含去重检查）
      3. 通过回调函数报告进度（支持 Web 前端实时显示）

    Args:
        papers (list[dict]): 要分析的论文列表
        concurrency (int | None): 并发线程数，None 时使用 config.py 中的默认值
        progress_callback (callable | None): 进度回调函数，接收 dict 参数

    Returns:
        int: 成功新增分析的论文数量
    """
    if not papers:
        logger.info("No unanalyzed papers found.")
        return 0

    # 确定并发数：未指定时使用运行时设置
    if concurrency is None:
        concurrency = get_concurrency()

    # 初始化统计计数器
    total = len(papers)
    success_count = 0   # 成功新增分析数
    skip_count = 0      # 已分析跳过数
    fail_count = 0      # 分析失败数
    completed_count = 0 # 已完成总数

    logger.info(f"Starting parallel basic analysis: {total} papers, concurrency={concurrency}")

    # 发送初始进度通知
    if progress_callback:
        progress_callback({"current": 0, "total": total, "status": "running", "message": "开始基础分析..."})

    # 使用线程池并发执行分析任务
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        # 提交所有任务，建立 future -> paper 的映射
        futures = {executor.submit(analyze_paper_basic, paper): paper for paper in papers}

        # 按完成顺序处理结果（as_completed 保证先完成的先返回）
        for future in as_completed(futures):
            source_paper = futures[future]
            try:
                paper_data, result, error = future.result()
            except Exception as e:
                paper_data = source_paper
                result = None
                error = str(e)
            arxiv_id = paper_data.get("arxiv_id", "unknown")
            completed_count += 1

            if result:
                # 尝试将分析结果写入数据库（insert_analysis 内部有重复检查）
                inserted = insert_analysis(paper_data["id"], result)
                if inserted:
                    success_count += 1
                    logger.info(f"[{completed_count}/{total}] ✅ {arxiv_id} | "
                                f"{result.get('rating', 0)}★ | {', '.join(result['tags'])}")
                else:
                    # 已有 analysis 记录时，按当前基础分析结果刷新标签、摘要、评价和 AI 初评。
                    update_analysis(paper_data["id"], {
                        "rating": result.get("rating", 0),
                        "tags": result.get("tags", []),
                        "summary_cn": result.get("summary_cn", ""),
                        "summary_en": result.get("summary_en", ""),
                        "value_comment": result.get("value_comment", ""),
                    })
                    success_count += 1
                    logger.info(f"[{completed_count}/{total}] ✅ {arxiv_id} updated basic analysis | "
                                f"{result.get('rating', 0)}★ | {', '.join(result['tags'])}")
            else:
                # AI 调用失败或 JSON 解析失败
                fail_count += 1
                logger.warning(f"[{completed_count}/{total}] ❌ {arxiv_id}: {error}")

            # 每完成一篇都发送进度更新（用于 Web 前端实时显示）
            if progress_callback:
                progress_callback({
                    "current": completed_count,
                    "total": total,
                    "status": "running",
                    "success": success_count,
                    "skip": skip_count,
                    "fail": fail_count,
                    "arxiv_id": arxiv_id,
                    "rating": result.get("rating", 0) if result else 0,
                    "tags": result.get("tags", []) if result else [],
                    "message": f"[{completed_count}/{total}] {arxiv_id}"
                })

    # 批量分析完成，记录汇总日志
    logger.info(f"Basic analysis complete: {success_count} new, {skip_count} skipped, {fail_count} failed.")
    # 发送最终完成通知
    if progress_callback:
        progress_callback({
            "current": total,
            "total": total,
            "status": "completed",
            "success": success_count,
            "skip": skip_count,
            "fail": fail_count,
            "message": f"基础分析完成：{success_count} 篇新增，{skip_count} 跳过，{fail_count} 失败"
        })
    return success_count


def recommend_papers(papers, research_interests, interest_hash, concurrency=None, progress_callback=None):
    """批量计算指定论文列表的个性化推荐分。"""
    if not papers or not research_interests or not interest_hash:
        if progress_callback:
            progress_callback({"current": 0, "total": 0, "status": "completed", "message": "无推荐评分任务"})
        return 0
    if concurrency is None:
        concurrency = get_concurrency()

    total = len(papers)
    success_count = 0
    fail_count = 0
    completed_count = 0
    logger.info(f"Starting recommendation scoring: {total} papers, concurrency={concurrency}")

    if progress_callback:
        progress_callback({"current": 0, "total": total, "status": "running", "message": "开始个性化推荐评分..."})

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {
            executor.submit(analyze_paper_recommendation, paper, research_interests, interest_hash): paper
            for paper in papers
        }
        for future in as_completed(futures):
            source_paper = futures[future]
            try:
                paper_data, result, error = future.result()
            except Exception as e:
                paper_data = source_paper
                result = None
                error = str(e)

            completed_count += 1
            arxiv_id = paper_data.get("arxiv_id", "unknown")
            if result and update_recommendation_result(
                paper_data["id"],
                result.get("recommendation_score", 0),
                result.get("recommendation_reason", ""),
                interest_hash,
            ):
                success_count += 1
                logger.info(f"[{completed_count}/{total}] 🎯 {arxiv_id} | 推荐 {result['recommendation_score']}/100")
            else:
                fail_count += 1
                logger.warning(f"[{completed_count}/{total}] ❌ recommendation {arxiv_id}: {error or 'update failed'}")

            if progress_callback:
                progress_callback({
                    "current": completed_count,
                    "total": total,
                    "status": "running",
                    "success": success_count,
                    "fail": fail_count,
                    "arxiv_id": arxiv_id,
                    "recommendation_score": result.get("recommendation_score", 0) if result else 0,
                    "message": f"[{completed_count}/{total}] {arxiv_id}"
                })

    if progress_callback:
        progress_callback({
            "current": total,
            "total": total,
            "status": "completed",
            "success": success_count,
            "fail": fail_count,
            "message": f"推荐评分完成：{success_count} 篇成功，{fail_count} 篇失败"
        })
    return success_count


def recommend_pending_papers(limit=200, date=None, concurrency=None, progress_callback=None, ingest_mode=None):
    """按当前研究兴趣为缺失或过期的论文补齐个性化推荐分。"""
    cfg = get_personalization_config()
    research_interests = cfg.get("research_interests", "")
    interest_hash = get_research_interest_hash(research_interests)
    if not research_interests or not interest_hash:
        if progress_callback:
            progress_callback({"current": 0, "total": 0, "status": "completed", "message": "未设置研究兴趣，跳过推荐评分"})
        return 0
    papers = get_papers_for_recommendation(
        limit=limit,
        date=date,
        interest_hash=interest_hash,
        ingest_mode=ingest_mode,
    )
    return recommend_papers(papers, research_interests, interest_hash, concurrency=concurrency, progress_callback=progress_callback)


def analyze_pending_papers(limit=50, concurrency=None, progress_callback=None, ingest_mode=None):
    """
    批量分析未处理的论文：从数据库获取指定数量后调用 analyze_papers()。

    Args:
        limit (int): 最大处理论文数量，默认 50
        concurrency (int | None): 并发线程数，None 时使用 config.py 中的默认值
        progress_callback (callable | None): 进度回调函数，接收 dict 参数

    Returns:
        int: 成功新增分析的论文数量
    """
    papers = get_unanalyzed_papers(limit=limit, ingest_mode=ingest_mode)
    return analyze_papers(papers, concurrency=concurrency, progress_callback=progress_callback)
