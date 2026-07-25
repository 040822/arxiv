"""HTML report rendering."""

import html as html_module
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

from source.settings import get_personalization_config, get_research_interest_hash
from source.storage.connection import get_connection
from source.storage.reports import get_report_trends
from source.storage.row_mapping import parse_paper_row


def generate_report_content(date, ai_summary=None):
    """生成指定日期的 HTML 报告内容。
    
    报告包含：
    1. 统计摘要（论文总数、已分析数、平均评级）
    2. 分类分布
    3. 热门标签（Top 15）
    4. 个性化推荐（如已启用）
    5. 全部论文列表
    
    参数：
        date (str): 报告日期（YYYY-MM-DD）
        ai_summary (str | None): 可选 AI 导读，默认不生成也不展示
        
    返回：
        tuple: (html_content, total, analyzed, avg_rating)
            - html_content: HTML 字符串，无数据返回 None
            - total: 论文总数
            - analyzed: 已分析数
            - avg_rating: 平均评级
    """
    with get_connection() as conn:
        cursor = conn.cursor()

        # 查询指定日期的所有论文及其分析结果
        cursor.execute("""
            SELECT p.*, a.id AS analysis_id, a.tags, a.summary_cn, a.rating, a.value_comment,
                   a.recommendation_score, a.recommendation_reason, a.recommendation_interest_hash,
                   a.recommendation_analyzed_at
            FROM papers p
            LEFT JOIN analysis a ON p.id = a.paper_id
            WHERE p.published_date = ? AND p.ingest_mode = 'feed'
            ORDER BY a.rating DESC, p.arxiv_id
        """, (date,))
        rows = cursor.fetchall()


    if not rows:
        return None, 0, 0, 0

    current_research_interests = get_personalization_config().get("research_interests", "")
    current_interest_hash = get_research_interest_hash(current_research_interests)
    trends = get_report_trends(date, interest_hash=current_interest_hash, days=7, top_tags=5)

    def esc(value):
        return html_module.escape(str(value or ""), quote=True)

    def format_stars(value):
        rating = max(0, min(5, int(value or 0)))
        return " ".join("★" if i < rating else "☆" for i in range(5))

    def current_recommendation_score(paper):
        if not current_interest_hash or paper.get("recommendation_interest_hash") != current_interest_hash:
            return None
        try:
            score = int(paper.get("recommendation_score"))
            return max(0, min(100, score))
        except (TypeError, ValueError):
            return None

    # 解析 JSON 字段
    papers = []
    for row in rows:
        paper = parse_paper_row(row)
        paper["current_recommendation_score"] = current_recommendation_score(paper)
        papers.append(paper)

    if current_interest_hash:
        papers.sort(key=lambda p: (
            -(p.get("current_recommendation_score") if p.get("current_recommendation_score") is not None else -1),
            -int(p.get("rating") or 0),
            str(p.get("arxiv_id") or ""),
        ))

    # 计算统计数据
    total = len(papers)
    analyzed = sum(1 for p in papers if p.get("analysis_id") is not None)
    ratings = [int(p["rating"]) for p in papers if p.get("rating") is not None]
    avg_rating = round(sum(ratings) / len(ratings), 1) if ratings else 0
    recommended_count = sum(1 for p in papers if (p.get("current_recommendation_score") or 0) >= 80)

    # 统计标签和分类分布
    tag_counts = {}
    category_counts = {}
    for p in papers:
        if p.get("tags") and isinstance(p["tags"], list):
            for t in p["tags"]:
                tag_counts[t] = tag_counts.get(t, 0) + 1
        if p.get("categories") and isinstance(p["categories"], list):
            for c in p["categories"]:
                category_counts[c] = category_counts.get(c, 0) + 1

    # 获取 Top 15 标签和 Top 10 分类
    top_tags = sorted(tag_counts.items(), key=lambda x: -x[1])[:15]
    top_categories = sorted(category_counts.items(), key=lambda x: -x[1])[:10]
    recommended = [p for p in papers if (p.get("current_recommendation_score") or 0) >= 60]
    recommended.sort(key=lambda x: (-(x.get("current_recommendation_score") or 0), -int(x.get("rating") or 0)))

    # ==================== 生成 HTML 报告 ====================
    
    # 统计摘要卡片
    html = f'<div class="report-summary">'
    html += f'<div class="report-stats">'
    html += f'<div class="report-stat"><span class="report-stat-val">{total}</span><span class="report-stat-label">论文总数</span></div>'
    html += f'<div class="report-stat"><span class="report-stat-val">{analyzed}</span><span class="report-stat-label">已分析</span></div>'
    html += f'<div class="report-stat"><span class="report-stat-val">{avg_rating}</span><span class="report-stat-label">平均评级</span></div>'
    if current_interest_hash:
        html += f'<div class="report-stat"><span class="report-stat-val">{recommended_count}</span><span class="report-stat-label">强推荐</span></div>'
    html += f'</div></div>'

    if ai_summary:
        summary_html = esc(ai_summary).replace("\n", "<br>")
        html += f'<div class="report-section"><h3>🤖 AI 导读</h3><div class="report-paper-summary">{summary_html}</div></div>'

    if trends.get("dates"):
        html += '<div class="report-section report-trends"><h3>📈 近 7 个有数据日趋势</h3>'
        html += '<div class="report-trend-block"><h4>标签走势</h4>'
        if trends.get("tag_series"):
            html += '<div class="report-trend-table-wrap"><table class="report-trend-table"><thead><tr><th>标签</th>'
            for trend_date in trends["dates"]:
                html += f'<th title="{esc(trend_date)}">{esc(trend_date[5:])}</th>'
            html += '<th>合计</th></tr></thead><tbody>'
            for series in trends["tag_series"]:
                html += f'<tr><th>{esc(series["tag"])}</th>'
                for count in series["counts"]:
                    html += f'<td>{int(count)}</td>'
                html += f'<td><strong>{int(series["total"])}</strong></td></tr>'
            html += '</tbody></table></div>'
        else:
            html += '<div class="report-trend-empty">窗口内暂无标签数据</div>'
        html += '</div>'

        html += '<div class="report-trend-grid"><div class="report-trend-block"><h4>新标签</h4>'
        if not trends.get("has_tag_history"):
            html += '<div class="report-trend-empty">仅有 1 个数据日，暂无历史可比较</div>'
        elif trends.get("new_tags"):
            html += '<div class="report-tags">'
            for item in trends["new_tags"]:
                html += f'<span class="tag-badge">{esc(item["tag"])} <span class="tag-count">{int(item["count"])}</span></span>'
            html += '</div>'
        else:
            html += '<div class="report-trend-empty">报告日没有相对前序数据日的新标签</div>'
        html += '</div>'

        recommendation = trends["recommendation"]
        html += '<div class="report-trend-block"><h4>推荐分分布</h4>'
        if not recommendation.get("enabled"):
            html += '<div class="report-trend-empty">未配置研究兴趣，暂无当前推荐分分布</div>'
        else:
            scored = int(recommendation.get("scored") or 0)
            for bucket in recommendation.get("buckets", []):
                count = int(bucket.get("count") or 0)
                percent = round(count * 100 / scored) if scored else 0
                html += f'''<div class="report-score-row">
                    <span class="report-score-label">{esc(bucket.get("label"))}</span>
                    <span class="report-score-track"><span class="report-score-fill report-score-{esc(bucket.get("key"))}" style="width:{percent}%"></span></span>
                    <strong>{count}</strong>
                </div>'''
            html += f'<div class="report-score-unscored">未评分或兴趣已过期：{int(recommendation.get("unscored") or 0)} 篇</div>'
        html += '</div></div></div>'

    if recommended:
        interest_html = ""
        if current_research_interests:
            interest_text = esc(current_research_interests).replace("\n", "<br>")
            interest_html = f'<div class="report-paper-summary"><strong>研究兴趣:</strong><br>{interest_text}</div>'
        html += f'<div class="report-section"><h3>🎯 个性化推荐</h3>{interest_html}<div class="report-papers">'
        for p in recommended[:20]:
            stars = format_stars(p.get('rating'))
            score = p.get("current_recommendation_score") or 0
            authors = ', '.join(esc(a) for a in p['authors'][:3]) if isinstance(p.get('authors'), list) else esc(p.get('authors', ''))
            cats = ' '.join(f'<span class="category-tag">{esc(c)}</span>' for c in (p.get('categories') or [])[:3])
            arxiv_id = esc(p.get('arxiv_id', ''))
            summary = f'<div class="report-paper-summary"><strong>中文摘要:</strong> {esc(p.get("summary_cn", ""))}</div>' if p.get("summary_cn") else ''
            reason = f'<div class="report-paper-comment"><strong>推荐语:</strong> {esc(p.get("recommendation_reason", ""))}</div>' if p.get("recommendation_reason") else ''
            comment = f'<div class="report-paper-comment"><strong>评价:</strong> {esc(p.get("value_comment", ""))}</div>' if p.get("value_comment") else ''
            html += f'''<div class="report-paper">
                <div class="report-paper-title"><a href="/paper/{arxiv_id}">{esc(p.get('title', ''))}</a></div>
                <div class="report-paper-meta"><span class="rating">推荐 {score}/100</span> <span class="rating">{stars}</span> {cats}</div>
                <div class="report-paper-authors">{authors}</div>
                {summary}
                {reason}
                {comment}
            </div>'''
        html += '</div></div>'

    # 分类分布区块
    if top_categories:
        html += '<div class="report-section"><h3>📂 分类分布</h3><div class="report-tags">'
        for cat, cnt in top_categories:
            html += f'<span class="tag-badge">{esc(cat)} <span class="tag-count">{cnt}</span></span>'
        html += '</div></div>'

    # 热门标签区块
    if top_tags:
        html += '<div class="report-section"><h3>🏷️ 热门标签</h3><div class="report-tags">'
        for tag, cnt in top_tags:
            html += f'<span class="tag-badge">{esc(tag)} <span class="tag-count">{cnt}</span></span>'
        html += '</div></div>'

    # 全部论文列表区块
    html += '<div class="report-section"><h3>📋 全部论文</h3><div class="report-papers">'
    for p in papers:
        stars = ''
        if p.get('rating') is not None:
            stars = f'<span class="rating">{format_stars(p.get("rating"))}</span>'
        rec = ''
        if p.get("current_recommendation_score") is not None:
            rec = f'<span class="rating">推荐 {int(p["current_recommendation_score"])}/100</span>'
        cats = ' '.join(f'<span class="category-tag">{esc(c)}</span>' for c in (p.get('categories') or [])[:3])
        authors = ', '.join(esc(a) for a in p['authors'][:3]) if isinstance(p.get('authors'), list) else esc(p.get('authors', ''))
        tags_html = ''
        if p.get('tags') and isinstance(p['tags'], list):
            tags_html = ' '.join(f'<span class="tag-small">{esc(t)}</span>' for t in p['tags'][:5])
        summary = f'<div class="report-paper-summary">{esc(p["summary_cn"])}</div>' if p.get('summary_cn') else ''
        arxiv_id = esc(p.get('arxiv_id', ''))
        html += f'''<div class="report-paper">
            <div class="report-paper-title"><a href="/paper/{arxiv_id}">{esc(p.get('title', ''))}</a> {rec} {stars}</div>
            <div class="report-paper-meta">{cats}</div>
            <div class="report-paper-authors">{authors}</div>
            {'<div class="report-paper-tags">' + tags_html + '</div>' if tags_html else ''}
            {summary}
        </div>'''
    html += '</div></div>'

    return html, total, analyzed, avg_rating
