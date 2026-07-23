"""SQLite analysis implementation."""

import html as html_module
import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

from .connection import get_connection

from .papers import _basic_analysis_missing_condition


def insert_analysis(paper_id, analysis_data):
    """插入 AI 分析结果到数据库。
    
    功能：
    1. 检查该论文是否已有分析结果（防重复）
    2. 将分析数据插入 analysis 表
    3. tags 字段自动从 Python 列表转为 JSON 字符串
    
    参数：
        paper_id (int): 关联的论文 ID
        analysis_data (dict): 分析数据字典，包含：
            - tags: 标签列表（如 ["VLA", "World Model"]）
            - summary_cn: 中文摘要
            - summary_en: 英文摘要
            - rating: AI 初评（0-5），用户可在详情页手动修正
            - value_comment: 价值评价
            - qa_analysis: Q&A 深度阅读（可选，默认为空字符串）
            - recommendation_score/reason/interest_hash: 个性化推荐字段（可选）
            
    返回：
        int or None: 成功返回分析 ID，已存在则返回 None
    """
    with get_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            INSERT OR IGNORE INTO analysis (
                paper_id, tags, summary_cn, summary_en, rating, value_comment, qa_analysis,
                recommendation_score, recommendation_reason, recommendation_interest_hash, recommendation_analyzed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            paper_id,
            json.dumps(analysis_data["tags"], ensure_ascii=False),
            analysis_data["summary_cn"],
            analysis_data["summary_en"],
            int(analysis_data.get("rating", 0)),
            analysis_data["value_comment"],
            analysis_data.get("qa_analysis", ""),
            analysis_data.get("recommendation_score"),
            analysis_data.get("recommendation_reason", ""),
            analysis_data.get("recommendation_interest_hash", ""),
            analysis_data.get("recommendation_analyzed_at"),
        ))
        if cursor.rowcount == 0:
            logger.debug(f"Analysis already exists for paper_id={paper_id}, skipping.")
            return None
        analysis_id = cursor.lastrowid

    return analysis_id


def get_analysis_by_paper_id(paper_id):
    """根据论文 ID 获取分析结果。
    
    参数：
        paper_id (int): 论文 ID
        
    返回：
        dict or None: 分析数据字典（tags 已解析为列表），不存在返回 None
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM analysis WHERE paper_id = ?", (paper_id,))
        row = cursor.fetchone()

    if row:
        r = dict(row)
        if r.get("tags") and isinstance(r["tags"], str):
            r["tags"] = json.loads(r["tags"])
        return r
    return None


def update_analysis(paper_id, data):
    """更新或插入论文的分析结果。
    
    逻辑：
    1. 如果该论文已有分析记录，则更新指定字段（部分更新）
    2. 如果没有记录，则插入新记录
    
    参数：
        paper_id (int): 论文 ID
        data (dict): 要更新的数据，可包含以下字段：
            - rating: AI 初评或用户手动修正（0-5）
            - tags: 标签列表
            - summary_cn: 中文摘要
            - value_comment: 价值评价
            - qa_analysis: Q&A 深度阅读
            - recommendation_score: 个性化推荐分（0-100）
            - recommendation_reason: 推荐理由
            - recommendation_interest_hash: 对应研究兴趣哈希
            - recommendation_analyzed_at: 推荐评分时间
            
    返回：
        bool: 始终返回 True
    """
    with get_connection() as conn:
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT OR IGNORE INTO analysis (
                paper_id, tags, summary_cn, summary_en, rating, value_comment, qa_analysis,
                recommendation_score, recommendation_reason, recommendation_interest_hash,
                recommendation_analyzed_at
            )
            VALUES (?, '[]', '', '', 0, '', '', NULL, '', '', NULL)
            """,
            (paper_id,),
        )

        sets = []
        params = []
        field_values = {
            "rating": lambda value: int(value),
            "tags": lambda value: json.dumps(value, ensure_ascii=False),
            "summary_cn": lambda value: value,
            "summary_en": lambda value: value,
            "value_comment": lambda value: value,
            "qa_analysis": lambda value: value,
            "recommendation_score": lambda value: None if value is None else int(value),
            "recommendation_reason": lambda value: value,
            "recommendation_interest_hash": lambda value: value,
            "recommendation_analyzed_at": lambda value: value,
        }
        for field, convert in field_values.items():
            if field in data:
                sets.append(f"{field} = ?")
                params.append(convert(data[field]))
        if sets:
            params.append(paper_id)
            cursor.execute(
                f"UPDATE analysis SET {', '.join(sets)} WHERE paper_id = ?",
                params,
            )


    return True


def _parse_paper_analysis_row(row):
    """解析 papers + analysis 查询结果中的 JSON 字段。"""
    r = dict(row)
    for field in ("authors", "categories", "tags"):
        if r.get(field) and isinstance(r[field], str):
            try:
                r[field] = json.loads(r[field])
            except json.JSONDecodeError:
                r[field] = []
    return r


def get_papers_for_recommendation(limit=200, date=None, interest_hash=""):
    """
    获取需要计算个性化推荐分的论文。

    只返回已有基础分析结果的非隐藏论文，避免推荐任务创建空 analysis 记录后
    影响 get_unanalyzed_papers() 对“待基础分析”论文的判断。
    """
    if not interest_hash:
        return []
    limit = max(1, min(1000, int(limit or 200)))
    with get_connection() as conn:
        cursor = conn.cursor()
        params = [interest_hash]
        where = """
            WHERE (p.hidden IS NULL OR p.hidden = 0)
            AND a.id IS NOT NULL
            AND (
                a.recommendation_score IS NULL
                OR a.recommendation_interest_hash IS NULL
                OR a.recommendation_interest_hash != ?
            )
        """
        if date:
            where += " AND p.published_date = ?"
            params.append(date)
        params.append(limit)
        cursor.execute("""
            SELECT p.*, a.tags, a.summary_cn, a.summary_en, a.rating, a.value_comment, a.qa_analysis,
                   a.recommendation_score, a.recommendation_reason, a.recommendation_interest_hash,
                   a.recommendation_analyzed_at
            FROM papers p
            JOIN analysis a ON p.id = a.paper_id
        """ + where + """
            ORDER BY p.published_date DESC, COALESCE(a.rating, 0) DESC, p.arxiv_id
            LIMIT ?
        """, params)
        rows = cursor.fetchall()

    return [_parse_paper_analysis_row(row) for row in rows]


def update_recommendation_result(paper_id, score, reason, interest_hash):
    """更新已有分析记录的个性化推荐结果。"""
    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            score = max(0, min(100, int(score or 0)))
        except (TypeError, ValueError):
            score = 0
        cursor.execute("""
            UPDATE analysis
            SET recommendation_score = ?,
                recommendation_reason = ?,
                recommendation_interest_hash = ?,
                recommendation_analyzed_at = ?
            WHERE paper_id = ?
        """, (
            score,
            str(reason or ""),
            str(interest_hash or ""),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            paper_id,
        ))
        updated = cursor.rowcount > 0

    return updated
