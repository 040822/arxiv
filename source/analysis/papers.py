"""Single-paper import, basic, deep-reading, and recommendation analysis."""

import json
import logging
import re

from source.documents import extract_text_from_pdf, get_paper_full_text, get_paper_pdf_path
from source.settings import get_personalization_config, get_prompt_profile, get_research_interest_hash

from .core import _call_ai, _call_ai_raw, _normalise_analysis_result, _normalise_deep_reading_result, _normalise_recommendation_result
from .json_support import _clean_json_content
from .messages import _authors_text, _build_task_messages

logger = logging.getLogger(__name__)

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


