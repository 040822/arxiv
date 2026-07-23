"""Flask pages routes."""

import hashlib
import hmac
import logging
import os
import re
import sqlite3
import time
from datetime import datetime

from flask import (
    Blueprint, Response, current_app, jsonify, redirect, render_template,
    request, session, url_for,
)
from analyzer import (
    analyze_pending_papers, analyze_paper_basic, analyze_paper_full,
    analyze_papers, generate_report_ai_summary, recommend_pending_papers,
    chat_about_paper, generate_paper_quiz, get_openai_client,
    grade_quiz_answer, socratic_reply,
)
from backup import get_database_file_sizes
from fetcher import (
    fetch_batch, fetch_by_date, fetch_latest_papers, fetch_paper_by_id,
    parse_arxiv_id,
)
from source.pipeline import (
    _run_email_report_task, _run_webdav_backup_task, configure_daily_job,
    pipeline_lock, scheduler,
)
from source.reports import generate_report_content
from source.settings import (
    get_per_page,
)
from source.storage import (
    browse_papers,
    get_all_categories,
    get_all_dates,
    get_all_tags,
    get_analysis_by_paper_id,
    get_analyzed_count,
    get_latest_paper_quiz_sessions,
    get_paper_by_arxiv_id,
    get_paper_count,
    get_papers_with_analysis,
    get_reading_list,
    get_reading_list_count,
    get_report_by_date,
    get_reports,
    normalize_search_terms,
    search_papers,
)
from source.storage.row_mapping import parse_paper_row
from .progress import get_progress, update_progress

logger = logging.getLogger(__name__)


bp = Blueprint("pages", __name__)


@bp.route("/about")
def about_page():
    """面向普通用户的项目介绍页。"""
    return render_template("about.html")


@bp.route("/vision")
def vision_page():
    """面向实验室内部汇报的项目愿景页。"""
    return render_template("vision.html")


@bp.route("/")
def index():
    """
    首页路由：论文列表页

    支持的查询参数：
    - page: 页码（默认 1）
    - tag: 按标签筛选
    - date: 按日期筛选（默认最新日期）
    - min_rating: 最低评级筛选（0-5）
    - per_page: 每页数量（5/10/20/50/100，仅影响当前请求）
    """
    page = request.args.get("page", 1, type=int)
    tag = request.args.get("tag", None)
    date = request.args.get("date", None)
    min_rating = request.args.get("min_rating", None, type=int)
    per_page_param = request.args.get("per_page", None, type=int)
    per_page = get_per_page()

    # 如果用户指定了合法的每页数量，则仅用于当前请求；持久化保存走受保护的设置 API
    if per_page_param and per_page_param in (5, 10, 20, 50, 100):
        per_page = per_page_param

    # 评级范围限制在 0-5 之间
    if min_rating is not None:
        min_rating = max(0, min(5, min_rating))

    # 无筛选条件时默认显示最新日期的论文
    if not date and not tag and min_rating is None:
        dates = get_all_dates()
        if dates:
            date = dates[0][0]

    # 获取论文列表（带分析数据）和总数
    papers, total = get_papers_with_analysis(
        date=date, tag=tag, min_rating=min_rating,
        limit=per_page, offset=(page - 1) * per_page,
        count_total=True
    )

    # 计算分页信息和统计数据
    total_pages = (total + per_page - 1) // per_page if total > 0 else 1
    total_papers = get_paper_count()
    analyzed_papers = get_analyzed_count()
    tags_with_counts = get_all_tags()

    return render_template(
        "index.html",
        papers=papers,
        page=page,
        total_pages=total_pages,
        per_page=per_page,
        tag=tag,
        date=date,
        min_rating=min_rating,
        total_papers=total_papers,
        analyzed_papers=analyzed_papers,
        tags=tags_with_counts,
    )


@bp.route("/paper/<arxiv_id>")
def paper_detail(arxiv_id):
    """
    论文详情页路由

    根据 arxiv_id 查询论文信息和分析结果，解析 authors/categories 的 JSON 字符串。
    """
    paper = get_paper_by_arxiv_id(arxiv_id)
    if not paper:
        return "Paper not found", 404

    paper = parse_paper_row(paper)

    # 获取该论文的 AI 分析结果
    analysis = get_analysis_by_paper_id(paper["id"])

    return render_template("paper.html", paper=paper, analysis=analysis)


def _prepare_paper_for_view(paper):
    """解析论文 JSON 字段，返回可直接传给模板/AI 的 dict。"""
    return parse_paper_row(paper)


@bp.route("/paper/<arxiv_id>/chat")
def paper_chat_page(arxiv_id):
    """单篇论文学习页：自由讨论、主动问答和苏格拉底追问。"""
    paper = get_paper_by_arxiv_id(arxiv_id)
    if not paper:
        return "Paper not found", 404
    paper_data = _prepare_paper_for_view(paper)
    analysis = get_analysis_by_paper_id(paper_data["id"])
    quiz_sessions = get_latest_paper_quiz_sessions(paper_data["id"], limit=10)
    return render_template(
        "paper_chat.html",
        paper=paper_data,
        analysis=analysis,
        quiz_sessions=quiz_sessions,
    )


def _search_pattern(terms):
    escaped = [re.escape(term) for term in sorted(terms, key=len, reverse=True) if term]
    return re.compile("|".join(escaped), re.IGNORECASE) if escaped else None


def _highlight_search_text(value, pattern):
    """将文本拆成普通/命中片段，模板逐段转义后再包裹 mark。"""
    text = str(value or "")
    if not pattern:
        return [{"text": text, "match": False}]
    parts = []
    cursor = 0
    for match in pattern.finditer(text):
        if match.start() > cursor:
            parts.append({"text": text[cursor:match.start()], "match": False})
        parts.append({"text": match.group(0), "match": True})
        cursor = match.end()
    if cursor < len(text):
        parts.append({"text": text[cursor:], "match": False})
    return parts or [{"text": text, "match": False}]


def _search_excerpt(value, pattern, max_chars=220):
    text = " ".join(str(value or "").split())
    if len(text) <= max_chars:
        return text
    match = pattern.search(text) if pattern else None
    center = match.start() if match else 0
    start = max(0, center - max_chars // 3)
    end = min(len(text), start + max_chars)
    start = max(0, end - max_chars)
    return ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")


def _prepare_search_result(paper, terms):
    prepared = dict(paper)
    pattern = _search_pattern(terms)
    prepared["title_highlight"] = _highlight_search_text(prepared.get("title"), pattern)
    prepared["tag_highlights"] = [
        _highlight_search_text(tag, pattern) for tag in (prepared.get("tags") or [])
    ]

    field_labels = (
        ("title", "标题"),
        ("tags", "标签"),
        ("summary_cn", "中文摘要"),
        ("abstract", "英文摘要"),
        ("qa_analysis", "深度阅读"),
    )
    matched_fields = []
    for field, label in field_labels:
        value = prepared.get(field)
        text = " ".join(str(item) for item in value) if isinstance(value, list) else str(value or "")
        if pattern and pattern.search(text):
            matched_fields.append(label)
    prepared["matched_fields"] = matched_fields

    preview_label = ""
    preview_text = ""
    for field, label in (("summary_cn", "中文摘要"), ("abstract", "英文摘要"), ("qa_analysis", "深度阅读")):
        value = prepared.get(field)
        if value and pattern and pattern.search(str(value)):
            preview_label = label
            preview_text = _search_excerpt(value, pattern)
            break
    if not preview_text:
        for field, label in (("summary_cn", "中文摘要"), ("abstract", "英文摘要")):
            if prepared.get(field):
                preview_label = label
                preview_text = _search_excerpt(prepared[field], pattern)
                break
    prepared["search_preview"] = {
        "label": preview_label,
        "parts": _highlight_search_text(preview_text, pattern),
    }
    return prepared


@bp.route("/search")
def search():
    """
    搜索页路由

    支持查询参数 q（最多 10 个关键词），执行跨字段 AND 匹配和相关性排序。
    """
    keyword = request.args.get("q", "").strip()
    papers = []
    search_error = ""
    if keyword:
        try:
            terms = normalize_search_terms(keyword)
            papers = [_prepare_search_result(paper, terms) for paper in search_papers(keyword, limit=50)]
        except ValueError as exc:
            search_error = str(exc)
    return render_template("search.html", papers=papers, keyword=keyword, search_error=search_error)


@bp.route("/browse")
def browse():
    """
    分类浏览页路由：支持多条件组合筛选

    支持的查询参数：
    - page: 页码
    - per_page: 每页数量
    - date/tag/category: 按日期、标签、分类筛选
    - min_rating/max_rating: 评级范围筛选
    - has_analysis: 是否已分析（"1"=已分析，"0"=未分析）
    - has_deep_analysis: 是否有深度分析
    - hidden: 是否显示隐藏论文（"1"=仅隐藏，"0"=仅未隐藏）
    """
    page = request.args.get("page", 1, type=int)
    per_page_param = request.args.get("per_page", None, type=int)
    per_page = get_per_page()

    if per_page_param and per_page_param in (5, 10, 20, 50, 100):
        per_page = per_page_param

    date = request.args.get("date", None)
    tag = request.args.get("tag", None)
    category = request.args.get("category", None)
    min_rating = request.args.get("min_rating", None, type=int)
    max_rating = request.args.get("max_rating", None, type=int)
    has_analysis = request.args.get("has_analysis", None)
    has_deep_analysis = request.args.get("has_deep_analysis", None)
    hidden = request.args.get("hidden", None)

    # 评级范围限制在 0-5 之间
    if min_rating is not None:
        min_rating = max(0, min(5, min_rating))
    if max_rating is not None:
        max_rating = max(0, min(5, max_rating))

    # 执行多条件查询
    papers, total = browse_papers(
        date=date, tag=tag, category=category,
        min_rating=min_rating, max_rating=max_rating,
        has_analysis=has_analysis, has_deep_analysis=has_deep_analysis, hidden=hidden,
        limit=per_page, offset=(page - 1) * per_page
    )

    # 获取筛选器所需的统计数据
    total_pages = (total + per_page - 1) // per_page if total > 0 else 1
    tags_with_counts = get_all_tags()
    categories_with_counts = get_all_categories()
    dates_with_counts = get_all_dates()

    return render_template(
        "browse.html",
        papers=papers, page=page, total=total, total_pages=total_pages,
        per_page=per_page,
        date=date, tag=tag, category=category,
        min_rating=min_rating, max_rating=max_rating,
        has_analysis=has_analysis,
        has_deep_analysis=has_deep_analysis,
        hidden=hidden,
        tags=tags_with_counts,
        categories=categories_with_counts,
        dates=dates_with_counts,
    )


@bp.route("/settings")
def settings_page():
    """设置页路由：AI 供应商、Prompt、代理、数据库等配置"""
    return render_template("settings.html")


@bp.route("/tasks")
def tasks_page():
    """论文处理页：提供抓取、分析、生成报告和添加指定论文操作。"""
    return render_template("tasks.html")


@bp.route("/reports")
def reports_page():
    """报告列表页路由：显示所有已生成的报告"""
    reports = get_reports()
    return render_template("reports.html", reports=reports)


@bp.route("/reports/<report_date>")
def report_detail_page(report_date):
    """报告详情页路由：根据日期显示单份报告内容"""
    report = get_report_by_date(report_date)
    if not report:
        return "报告不存在", 404
    return render_template("report_detail.html", report=report)


@bp.route("/reading-list")
def reading_list_page():
    """
    阅读清单页路由

    支持按阅读状态筛选（unread/read），显示各状态的论文数量统计。
    """
    status = request.args.get("status", None)
    papers = get_reading_list(status=status)
    counts = get_reading_list_count()
    return render_template("reading_list.html", papers=papers, counts=counts, current_status=status)
