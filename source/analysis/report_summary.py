"""AI-generated daily report introduction from lightweight paper context."""

import json

from source.settings import get_research_interest_hash
from source.storage import get_connection

from .messages import _build_task_messages
from .core import _call_ai

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
