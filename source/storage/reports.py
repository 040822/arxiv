"""SQLite reports implementation."""

import html as html_module
import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

from .connection import get_connection


def get_daily_stats(date):
    """获取指定日期的论文统计信息。
    
    参数：
        date (str): 日期（YYYY-MM-DD）
        
    返回：
        dict or None: 包含 total（总数）、analyzed（已分析数）、avg_rating（平均评级），
                      无数据返回 None
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN NOT {_basic_analysis_missing_condition("a")} THEN 1 ELSE 0 END) as analyzed,
                   AVG(a.rating) as avg_rating
            FROM papers p
            LEFT JOIN analysis a ON p.id = a.paper_id
            WHERE p.published_date = ?
        """, (date,))
        row = cursor.fetchone()

        return dict(row) if row else None


def get_report_trends(report_date, interest_hash="", days=7, top_tags=5):
    """汇总截至报告日最近若干个有论文日期的标签和推荐分趋势。"""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT published_date
            FROM papers
            WHERE published_date IS NOT NULL AND published_date <= ?
            ORDER BY published_date DESC
            LIMIT ?
        """, (report_date, max(1, int(days))))
        dates = [row["published_date"] for row in cursor.fetchall()][::-1]

        if not dates:

            return {
                "dates": [],
                "tag_series": [],
                "new_tags": [],
                "has_tag_history": False,
                "recommendation": {
                    "enabled": bool(interest_hash),
                    "buckets": [],
                    "scored": 0,
                    "unscored": 0,
                    "total": 0,
                },
            }

        placeholders = ",".join("?" for _ in dates)
        cursor.execute(f"""
            SELECT p.published_date, a.tags, a.recommendation_score, a.recommendation_interest_hash
            FROM papers p
            LEFT JOIN analysis a ON p.id = a.paper_id
            WHERE p.published_date IN ({placeholders})
        """, dates)
        rows = [dict(row) for row in cursor.fetchall()]


        date_tag_counts = {date: {} for date in dates}
        for row in rows:
            try:
                tags = json.loads(row.get("tags") or "[]")
            except (TypeError, json.JSONDecodeError):
                tags = []
            for tag in tags if isinstance(tags, list) else []:
                tag = str(tag).strip()
                if tag:
                    counts = date_tag_counts[row["published_date"]]
                    counts[tag] = counts.get(tag, 0) + 1

        totals = {}
        for counts in date_tag_counts.values():
            for tag, count in counts.items():
                totals[tag] = totals.get(tag, 0) + count
        ranked_tags = sorted(totals, key=lambda tag: (-totals[tag], tag.casefold()))[:max(1, int(top_tags))]
        tag_series = [
            {
                "tag": tag,
                "total": totals[tag],
                "counts": [date_tag_counts[date].get(tag, 0) for date in dates],
            }
            for tag in ranked_tags
        ]

        has_tag_history = len(dates) > 1
        new_tags = []
        if has_tag_history:
            previous_tags = set()
            for date in dates[:-1]:
                previous_tags.update(date_tag_counts[date])
            new_tags = [
                {"tag": tag, "count": count}
                for tag, count in sorted(
                    date_tag_counts[dates[-1]].items(),
                    key=lambda item: (-item[1], item[0].casefold()),
                )
                if tag not in previous_tags
            ]

        buckets = [
            {"key": "low", "label": "0–59", "count": 0},
            {"key": "recommended", "label": "60–79", "count": 0},
            {"key": "strong", "label": "80–100", "count": 0},
        ]
        scored = 0
        unscored = 0
        for row in rows:
            score = row.get("recommendation_score")
            if not interest_hash or row.get("recommendation_interest_hash") != interest_hash:
                unscored += 1
                continue
            try:
                score = int(score)
            except (TypeError, ValueError):
                unscored += 1
                continue
            if not 0 <= score <= 100:
                unscored += 1
                continue
            scored += 1
            if score < 60:
                buckets[0]["count"] += 1
            elif score < 80:
                buckets[1]["count"] += 1
            else:
                buckets[2]["count"] += 1

        return {
            "dates": dates,
            "tag_series": tag_series,
            "new_tags": new_tags,
            "has_tag_history": has_tag_history,
            "recommendation": {
                "enabled": bool(interest_hash),
                "buckets": buckets,
                "scored": scored,
                "unscored": unscored,
                "total": len(rows),
            },
        }


def save_report(report_date, content, paper_count, analyzed_count, avg_rating):
    """保存或更新每日报告。
    
    使用 UPSERT 逻辑：如果该日期已有报告则更新，否则插入新记录。
    
    参数：
        report_date (str): 报告日期（YYYY-MM-DD）
        content (str): HTML 格式的报告内容
        paper_count (int): 论文总数
        analyzed_count (int): 已分析数
        avg_rating (float): 平均评级
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO reports (report_date, content, paper_count, analyzed_count, avg_rating)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(report_date) DO UPDATE SET
                content=excluded.content,
                paper_count=excluded.paper_count,
                analyzed_count=excluded.analyzed_count,
                avg_rating=excluded.avg_rating,
                created_at=CURRENT_TIMESTAMP
        """, (report_date, content, paper_count, analyzed_count, avg_rating))
        conn.commit()



def get_reports(limit=50):
    """获取报告列表，按日期降序。
    
    参数：
        limit (int): 最大返回数量，默认 50
        
    返回：
        list: 报告数据字典列表
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM reports ORDER BY report_date DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()

        return [dict(row) for row in rows]


def get_report_by_date(report_date):
    """根据日期获取单份报告。
    
    参数：
        report_date (str): 报告日期（YYYY-MM-DD）
        
    返回：
        dict or None: 报告数据字典，不存在返回 None
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM reports WHERE report_date = ?", (report_date,))
        row = cursor.fetchone()

        return dict(row) if row else None


def get_report_dates():
    """获取所有报告日期及统计信息。
    
    用于报告列表页面显示日期和概览数据。
    
    返回：
        list: 包含 report_date, paper_count, analyzed_count, avg_rating 的字典列表
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT report_date, paper_count, analyzed_count, avg_rating FROM reports ORDER BY report_date DESC")
        rows = cursor.fetchall()

        return [dict(row) for row in rows]
