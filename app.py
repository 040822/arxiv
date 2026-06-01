"""
Flask Web 应用主模块

本模块是 arXiv 论文数据库项目的核心 Web 服务，提供以下功能：
- 论文浏览、搜索、分类筛选等页面路由
- 论文抓取、AI 分析、报告生成等任务 API
- 论文 CRUD、批量操作、阅读清单等论文管理 API
- AI 供应商、Prompt、代理等设置 API
- 任务日志与定时任务管理 API
- 基于 APScheduler 的每日定时任务调度

技术栈: Flask + APScheduler + SQLite
"""

import logging
import os
import json
import threading
import time
from datetime import datetime
from flask import Flask, render_template, request, jsonify, Response
from apscheduler.schedulers.background import BackgroundScheduler
from database import (
    init_db, get_papers_with_analysis, get_all_tags,
    get_paper_count, get_analyzed_count, get_unanalyzed_count,
    search_papers, get_daily_stats,
    browse_papers, get_all_categories, get_all_dates,
    get_paper_by_arxiv_id, get_analysis_by_paper_id,
    update_analysis, hide_paper, unhide_paper, delete_paper,
    start_task_log, finish_task_log, get_task_logs, get_task_stats,
    get_running_tasks, clear_task_logs,
    save_report, get_reports, get_report_by_date, get_report_dates,
    generate_report_content,
    add_to_reading_list, remove_from_reading_list, is_in_reading_list,
    mark_as_read, mark_as_unread, get_reading_list, get_reading_list_count
)
from fetcher import fetch_latest_papers, fetch_paper_by_id, parse_arxiv_id
from analyzer import analyze_pending_papers, analyze_paper_full
from markdown_gen import generate_all_markdown
from settings import (
    load_settings, save_settings, get_provider_presets, get_all_providers,
    add_provider, remove_provider, switch_provider, update_provider,
    get_prompts, save_prompts, get_concurrency, get_per_page,
    get_admin_password, set_admin_password, verify_admin_password, has_admin_password
)
from config import WEB_HOST, WEB_PORT, SCHEDULE_HOUR, SCHEDULE_MINUTE

# ==================== 日志配置 ====================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ==================== Flask 应用初始化 ====================

app = Flask(__name__)

# 进度存储：用于跟踪长时间运行任务（抓取、分析）的实时进度
# key 为 task_id，value 为进度数据字典（含 current、total、status、message 等字段）
progress_store = {}
# 线程锁：保证多线程环境下进度数据的读写安全
progress_lock = threading.Lock()


def update_progress(task_id, data):
    """更新指定任务的进度数据，自动附加时间戳"""
    with progress_lock:
        progress_store[task_id] = {**data, "timestamp": time.time()}


def get_progress(task_id):
    """获取指定任务的进度数据，不存在则返回 None"""
    with progress_lock:
        return progress_store.get(task_id)


# ==================== 模板上下文处理器 ====================

@app.context_processor
def inject_now():
    """向所有模板注入当前时间变量 now，用于页面显示"""
    return {"now": datetime.now().strftime("%Y-%m-%d %H:%M")}


@app.context_processor
def utility_processor():
    """向所有模板注入分页辅助函数，用于 URL 参数处理"""

    def _remove_param(key):
        """从当前 URL 查询参数中移除指定 key（同时移除 page），返回剩余参数字符串"""
        args = request.args.copy()
        args.pop(key, None)
        args.pop("page", None)
        return "&".join(f"{k}={v}" for k, v in args.items() if v)

    def _build_query():
        """构建当前 URL 查询参数字符串（移除 page），用于分页链接"""
        args = request.args.copy()
        args.pop("page", None)
        return "&".join(f"{k}={v}" for k, v in args.items() if v)

    return {"_remove_param": _remove_param, "_build_query": _build_query}


# ==================== 定时任务调度器 ====================

scheduler = BackgroundScheduler()


def daily_pipeline():
    """
    每日定时任务主流程

    执行顺序：
    1. 抓取近 3 天的新论文
    2. 对未分析的论文进行 AI 分析
    3. 生成当日报告
    """
    log_id = start_task_log("daily_pipeline", "每日定时任务启动")
    try:
        # 步骤1：抓取近 3 天的论文
        new_papers = fetch_latest_papers(days=3)
        logger.info(f"Fetched {len(new_papers)} new papers.")

        # 步骤2：AI 分析未分析的论文
        concurrency = get_concurrency()
        analyzed_count = analyze_pending_papers(limit=1000, concurrency=concurrency)
        logger.info(f"Analyzed {analyzed_count} papers.")

        # 步骤3：生成报告（使用最新的论文日期）
        from datetime import datetime
        dates = get_all_dates()
        report_date = dates[0][0] if dates else datetime.now().strftime("%Y-%m-%d")
        content, paper_count, analyzed_count_r, avg_rating = generate_report_content(report_date)
        if content:
            save_report(report_date, content, paper_count, analyzed_count_r, avg_rating)
            logger.info(f"Report generated for {report_date}: {paper_count} papers, avg rating {avg_rating}")

        # 记录任务完成日志
        finish_task_log(log_id, "success",
            f"完成：抓取 {len(new_papers)} 篇（近3日），分析 {analyzed_count} 篇，报告已生成",
            f"new_papers={len(new_papers)}, analyzed={analyzed_count}, concurrency={concurrency}")
    except Exception as e:
        logger.error(f"Daily pipeline error: {e}")
        finish_task_log(log_id, "error", f"失败：{e}")


# ====================================================================
#                         页面路由（Page Routes）
# ====================================================================

@app.route("/")
def index():
    """
    首页路由：论文列表页

    支持的查询参数：
    - page: 页码（默认 1）
    - tag: 按标签筛选
    - date: 按日期筛选（默认最新日期）
    - min_rating: 最低评级筛选（0-5）
    - per_page: 每页数量（5/10/20/50/100，修改后持久化保存）
    """
    page = request.args.get("page", 1, type=int)
    tag = request.args.get("tag", None)
    date = request.args.get("date", None)
    min_rating = request.args.get("min_rating", None, type=int)
    per_page_param = request.args.get("per_page", None, type=int)
    per_page = get_per_page()

    # 如果用户指定了合法的每页数量，则持久化保存到配置
    if per_page_param and per_page_param in (5, 10, 20, 50, 100):
        per_page = per_page_param
        settings = load_settings()
        settings["per_page"] = per_page
        save_settings(settings)

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


@app.route("/paper/<arxiv_id>")
def paper_detail(arxiv_id):
    """
    论文详情页路由

    根据 arxiv_id 查询论文信息和分析结果，解析 authors/categories 的 JSON 字符串。
    """
    paper = get_paper_by_arxiv_id(arxiv_id)
    if not paper:
        return "Paper not found", 404

    # 将 authors 和 categories 从 JSON 字符串解析为 Python 对象
    import json
    if paper.get("authors") and isinstance(paper["authors"], str):
        paper["authors"] = json.loads(paper["authors"])
    if paper.get("categories") and isinstance(paper["categories"], str):
        paper["categories"] = json.loads(paper["categories"])

    # 获取该论文的 AI 分析结果
    analysis = get_analysis_by_paper_id(paper["id"])

    return render_template("paper.html", paper=paper, analysis=analysis)


@app.route("/search")
def search():
    """
    搜索页路由

    支持查询参数 q（关键词），对论文标题和摘要进行全文搜索。
    """
    keyword = request.args.get("q", "").strip()
    papers = []
    if keyword:
        papers = search_papers(keyword, limit=50)
    return render_template("search.html", papers=papers, keyword=keyword)


@app.route("/browse")
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


@app.route("/settings")
def settings_page():
    """设置页路由：AI 供应商、Prompt、代理、数据库等配置"""
    return render_template("settings.html")


@app.route("/tasks")
def tasks_page():
    """
    任务管理页路由

    显示定时任务状态、任务统计和日志。传入定时任务的执行时间配置。
    """
    return render_template("tasks.html",
        schedule_hour=SCHEDULE_HOUR,
        schedule_minute=SCHEDULE_MINUTE
    )


@app.route("/reports")
def reports_page():
    """报告列表页路由：显示所有已生成的报告"""
    reports = get_reports()
    return render_template("reports.html", reports=reports)


@app.route("/reports/<report_date>")
def report_detail_page(report_date):
    """报告详情页路由：根据日期显示单份报告内容"""
    report = get_report_by_date(report_date)
    if not report:
        return "报告不存在", 404
    return render_template("report_detail.html", report=report)


@app.route("/reading-list")
def reading_list_page():
    """
    阅读清单页路由

    支持按阅读状态筛选（unread/read），显示各状态的论文数量统计。
    """
    status = request.args.get("status", None)
    papers = get_reading_list(status=status)
    counts = get_reading_list_count()
    return render_template("reading_list.html", papers=papers, counts=counts, current_status=status)


# ====================================================================
#                      论文数据 API（Paper Data API）
# ====================================================================

@app.route("/api/papers")
def api_papers():
    """获取论文列表 JSON 接口：支持按日期、标签、评级筛选和分页"""
    page = request.args.get("page", 1, type=int)
    tag = request.args.get("tag", None)
    date = request.args.get("date", None)
    min_rating = request.args.get("min_rating", None, type=int)
    per_page = 20

    papers = get_papers_with_analysis(
        date=date, tag=tag, min_rating=min_rating,
        limit=per_page, offset=(page - 1) * per_page
    )
    return jsonify(papers)


@app.route("/api/tags")
def api_tags():
    """获取所有标签及其论文数量的 JSON 接口"""
    tags = get_all_tags()
    return jsonify(tags)


@app.route("/api/stats")
def api_stats():
    """获取系统统计数据的 JSON 接口：论文总数、已分析数、待分析数、并发数等"""
    return jsonify({
        "total_papers": get_paper_count(),
        "analyzed_papers": get_analyzed_count(),
        "unanalyzed_papers": get_unanalyzed_count(),
        "concurrency": get_concurrency(),
        "per_page": get_per_page(),
    })


# ====================================================================
#                   任务 API（Task Execution API）
# ====================================================================

@app.route("/api/fetch", methods=["POST"])
def api_fetch():
    """
    论文抓取 API

    支持的查询参数：
    - task_id: 任务追踪 ID（用于进度查询）
    - category: 指定 arXiv 分类（如 cs.RO）
    - max_results: 最大结果数
    - days: 抓取最近 N 天的论文（使用分批抓取）
    - date: 抓取指定日期的论文
    """
    task_id = request.args.get("task_id", "fetch")
    log_id = start_task_log("fetch", "手动抓取论文")
    try:
        from fetcher import fetch_latest_papers, fetch_batch, fetch_by_date
        from settings import get_fetch_config
        category = request.args.get("category", "").strip()
        max_results = request.args.get("max_results", type=int)
        days = request.args.get("days", type=int)
        date = request.args.get("date", "").strip()

        categories = [category] if category else None
        fetch_cfg = get_fetch_config()

        # 进度回调函数，供 fetch_batch 更新实时进度
        def progress_callback(data):
            update_progress(task_id, data)

        # 根据参数选择不同的抓取策略
        if date:
            # 按指定日期抓取
            new_papers = fetch_by_date(date, categories=categories)
            desc = f"日期: {date}"
        elif days:
            # 分批抓取最近 N 天，避免单次请求过大
            new_papers = fetch_batch(categories=categories, total_days=days,
                                     batch_days=fetch_cfg["batch_days"],
                                     batch_delay=fetch_cfg["batch_delay"],
                                     progress_callback=progress_callback)
            desc = f"最近 {days} 天"
        else:
            # 默认抓取（使用配置中的默认参数）
            new_papers = fetch_latest_papers(categories=categories, max_results=max_results)
            desc = "默认"

        unanalyzed = get_unanalyzed_count()
        cat_desc = f"（分类: {category}）" if category else ""
        msg = f"抓取完成：{len(new_papers)} 篇新论文{cat_desc}（{desc}）" + (f"，{unanalyzed} 篇待分析" if unanalyzed else "")
        finish_task_log(log_id, "success", msg, f"new_papers={len(new_papers)}, unanalyzed={unanalyzed}")
        return jsonify({"status": "ok", "count": len(new_papers), "unanalyzed": unanalyzed, "message": msg})
    except Exception as e:
        finish_task_log(log_id, "error", str(e))
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    """
    AI 分析 API：对未分析的论文进行 AI 深度阅读分析

    支持的查询参数：
    - task_id: 任务追踪 ID
    - limit: 最大分析数量（默认 50）
    """
    task_id = request.args.get("task_id", "analyze")
    log_id = start_task_log("analyze", "手动AI分析")
    try:
        limit = request.args.get("limit", 50, type=int)
        concurrency = get_concurrency()

        # 进度回调函数，供分析器更新实时进度
        def progress_callback(data):
            update_progress(task_id, data)

        count = analyze_pending_papers(limit=limit, concurrency=concurrency, progress_callback=progress_callback)
        remaining = get_unanalyzed_count()
        msg = f"分析完成：{count} 篇已分析（并发数 {concurrency}）" + (f"，{remaining} 篇剩余" if remaining else "，全部完成！")
        finish_task_log(log_id, "success", msg, f"analyzed={count}, remaining={remaining}, concurrency={concurrency}")
        return jsonify({"status": "ok", "count": count, "remaining": remaining, "concurrency": concurrency, "message": msg})
    except Exception as e:
        finish_task_log(log_id, "error", str(e))
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/run", methods=["POST"])
def api_run():
    """
    一键执行全部流程 API

    依次执行：抓取论文 → AI 分析 → 生成报告
    通过进度回调实时更新各阶段状态。
    """
    task_id = request.args.get("task_id", "run")
    log_id = start_task_log("run", "一键执行全部")
    try:
        # 阶段1：抓取论文
        update_progress(task_id, {"current": 0, "total": 3, "status": "running", "message": "正在抓取论文..."})
        new_papers = fetch_latest_papers()

        # 阶段2：AI 分析
        update_progress(task_id, {"current": 1, "total": 3, "status": "running", "message": f"抓取完成，开始分析 {len(new_papers)} 篇新论文..."})
        concurrency = get_concurrency()

        def progress_callback(data):
            update_progress(task_id, {**data, "phase": "analyze"})

        analyzed_count = analyze_pending_papers(limit=100, concurrency=concurrency, progress_callback=progress_callback)

        # 阶段3：生成报告
        update_progress(task_id, {"current": 2, "total": 3, "status": "running", "message": "正在生成报告..."})
        dates = get_all_dates()
        report_date = dates[0][0] if dates else datetime.now().strftime("%Y-%m-%d")
        content, paper_count, analyzed_count_r, avg_rating = generate_report_content(report_date)
        if content:
            save_report(report_date, content, paper_count, analyzed_count_r, avg_rating)

        msg = f"完成！抓取 {len(new_papers)} 篇，分析 {analyzed_count} 篇，报告已生成"
        finish_task_log(log_id, "success", msg, f"fetched={len(new_papers)}, analyzed={analyzed_count}, concurrency={concurrency}")
        update_progress(task_id, {"current": 3, "total": 3, "status": "completed", "message": msg})
        return jsonify({"status": "ok", "fetched": len(new_papers), "analyzed": analyzed_count, "concurrency": concurrency, "message": msg})
    except Exception as e:
        finish_task_log(log_id, "error", str(e))
        update_progress(task_id, {"status": "error", "message": str(e)})
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/progress/<task_id>")
def api_progress(task_id):
    """
    任务进度 SSE（Server-Sent Events）接口

    前端可通过 EventSource 订阅此接口，实时获取任务进度。
    当任务状态为 completed 或 error 时自动关闭连接。
    """
    def generate():
        last_data = None
        while True:
            data = get_progress(task_id)
            # 仅在数据变化时发送，避免重复推送
            if data and data != last_data:
                yield f"data: {json.dumps(data)}\n\n"
                last_data = data
                # 任务结束时关闭 SSE 连接
                if data.get("status") in ("completed", "error"):
                    break
            time.sleep(0.5)

    return Response(generate(), mimetype="text/event-stream")


@app.route("/api/generate", methods=["POST"])
def api_generate():
    """
    报告生成 API

    支持查询参数 date（指定日期），默认使用最新日期。
    如果指定日期无论文数据，返回 400 错误。
    """
    log_id = start_task_log("generate", "手动生成报告")
    try:
        from datetime import datetime
        date_param = request.args.get("date", "").strip()
        if date_param:
            report_date = date_param
        else:
            dates = get_all_dates()
            report_date = dates[0][0] if dates else datetime.now().strftime("%Y-%m-%d")
        content, paper_count, analyzed_count, avg_rating = generate_report_content(report_date)
        if content is None:
            finish_task_log(log_id, "error", f"{report_date} 无论文数据")
            return jsonify({"status": "error", "message": f"{report_date} 无论文数据，请先抓取该日论文"}), 400
        save_report(report_date, content, paper_count, analyzed_count, avg_rating)
        msg = f"报告生成成功（{report_date}）：{paper_count} 篇论文，{analyzed_count} 篇已分析，平均评级 {avg_rating}"
        finish_task_log(log_id, "success", msg)
        return jsonify({"status": "ok", "date": report_date, "message": msg})
    except Exception as e:
        finish_task_log(log_id, "error", str(e))
        return jsonify({"status": "error", "message": str(e)}), 500


# ====================================================================
#                  论文管理 API（Paper CRUD API）
# ====================================================================

@app.route("/api/paper/<arxiv_id>/analysis", methods=["PUT"])
def api_update_paper_analysis(arxiv_id):
    """更新论文的 AI 分析结果（评级、标签、摘要等）"""
    try:
        paper = get_paper_by_arxiv_id(arxiv_id)
        if not paper:
            return jsonify({"status": "error", "message": "论文不存在"}), 404

        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "无效的请求数据"}), 400

        update_analysis(paper["id"], data)
        return jsonify({"status": "ok", "message": "已更新"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/paper/<arxiv_id>/hide", methods=["POST"])
def api_hide_paper(arxiv_id):
    """隐藏论文（在列表中默认不显示）"""
    try:
        if hide_paper(arxiv_id):
            return jsonify({"status": "ok", "message": "论文已隐藏"})
        return jsonify({"status": "error", "message": "操作失败"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/paper/<arxiv_id>/unhide", methods=["POST"])
def api_unhide_paper(arxiv_id):
    """取消论文隐藏"""
    try:
        if unhide_paper(arxiv_id):
            return jsonify({"status": "ok", "message": "论文已取消隐藏"})
        return jsonify({"status": "error", "message": "操作失败"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/paper/<arxiv_id>", methods=["DELETE"])
def api_delete_paper(arxiv_id):
    """删除论文及其关联的分析数据（CASCADE 删除）"""
    try:
        if delete_paper(arxiv_id):
            return jsonify({"status": "ok", "message": "论文已删除"})
        return jsonify({"status": "error", "message": "删除失败"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/papers/batch-delete", methods=["POST"])
def api_batch_delete_papers():
    """批量删除论文：接收 arxiv_ids 数组，返回实际删除数量"""
    try:
        from database import batch_delete_papers
        data = request.get_json()
        arxiv_ids = data.get("arxiv_ids", [])
        if not arxiv_ids:
            return jsonify({"status": "error", "message": "未选择论文"}), 400
        deleted = batch_delete_papers(arxiv_ids)
        return jsonify({"status": "ok", "message": f"已删除 {deleted} 篇论文", "deleted": deleted})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/papers/batch-hide", methods=["POST"])
def api_batch_hide_papers():
    """批量隐藏论文：接收 arxiv_ids 数组，返回实际隐藏数量"""
    try:
        from database import batch_hide_papers
        data = request.get_json()
        arxiv_ids = data.get("arxiv_ids", [])
        if not arxiv_ids:
            return jsonify({"status": "error", "message": "未选择论文"}), 400
        hidden = batch_hide_papers(arxiv_ids)
        return jsonify({"status": "ok", "message": f"已隐藏 {hidden} 篇论文", "hidden": hidden})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/papers/batch-analyze", methods=["POST"])
def api_batch_analyze_papers():
    """批量分析论文：对选中的未分析论文执行 AI 分析"""
    try:
        from database import get_unanalyzed_papers_by_ids
        data = request.get_json()
        arxiv_ids = data.get("arxiv_ids", [])
        if not arxiv_ids:
            return jsonify({"status": "error", "message": "未选择论文"}), 400
        papers = get_unanalyzed_papers_by_ids(arxiv_ids)
        if not papers:
            return jsonify({"status": "ok", "message": "所选论文均已分析过", "count": 0})
        concurrency = get_concurrency()
        count = analyze_pending_papers(limit=len(papers), concurrency=concurrency)
        return jsonify({"status": "ok", "message": f"已分析 {count} 篇论文", "count": count})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/paper/<arxiv_id>/reanalyze", methods=["POST"])
def api_reanalyze_paper(arxiv_id):
    """重新分析单篇论文：覆盖已有的分析结果"""
    try:
        from analyzer import analyze_paper_full
        from database import get_paper_by_arxiv_id, update_analysis
        paper = get_paper_by_arxiv_id(arxiv_id)
        if not paper:
            return jsonify({"status": "error", "message": "论文不存在"}), 404

        # 将 paper 转为可修改的字典，并解析 JSON 字段
        paper_data = dict(paper)
        import json
        if paper_data.get("authors") and isinstance(paper_data["authors"], str):
            paper_data["authors"] = json.loads(paper_data["authors"])
        if paper_data.get("categories") and isinstance(paper_data["categories"], str):
            paper_data["categories"] = json.loads(paper_data["categories"])

        result_data, result, error = analyze_paper_full(paper_data)
        if result:
            update_analysis(paper["id"], result)
            return jsonify({"status": "ok", "message": "报告生成完成", "rating": result.get("rating")})
        return jsonify({"status": "error", "message": f"分析失败: {error}"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/paper/add", methods=["POST"])
def api_add_paper():
    """
    手动添加论文 API

    接收 arXiv 论文编号或链接，自动抓取论文信息并进行 AI 分析。
    如果论文已存在且已分析，返回已有结果。
    通过 SSE 进度接口实时更新添加进度。
    """
    try:
        data = request.get_json()
        input_str = data.get("input", "").strip()
        if not input_str:
            return jsonify({"status": "error", "message": "请输入论文编号或链接"}), 400

        # 解析输入的 arXiv ID（支持纯编号和完整链接）
        arxiv_id = parse_arxiv_id(input_str)
        if not arxiv_id:
            return jsonify({"status": "error", "message": "无法识别的格式，请输入 arXiv 论文编号（如 2603.18336）或链接"}), 400

        task_id = data.get("task_id", "add_paper")
        update_progress(task_id, {"current": 0, "total": 2, "status": "running", "message": f"正在获取论文 {arxiv_id}..."})

        # 从 arXiv 获取论文元数据
        paper_data = fetch_paper_by_id(arxiv_id)
        if not paper_data:
            update_progress(task_id, {"status": "error", "message": "论文获取失败"})
            return jsonify({"status": "error", "message": "论文获取失败，请检查编号是否正确"}), 404

        # 检查论文是否已有分析结果
        already_analyzed = get_analysis_by_paper_id(paper_data.get("id"))
        if already_analyzed:
            update_progress(task_id, {"current": 2, "total": 2, "status": "completed", "message": "论文已存在且已分析"})
            return jsonify({
                "status": "ok",
                "message": f"论文已存在且已分析: {paper_data['title'][:50]}...",
                "arxiv_id": paper_data.get("arxiv_id"),
                "already_exists": True
            })

        update_progress(task_id, {"current": 1, "total": 2, "status": "running", "message": f"正在分析论文 {arxiv_id}..."})

        # 解析 JSON 字段
        if isinstance(paper_data.get("authors"), str):
            import json as _json
            paper_data["authors"] = _json.loads(paper_data["authors"])
        if isinstance(paper_data.get("categories"), str):
            import json as _json
            paper_data["categories"] = _json.loads(paper_data["categories"])

        # 执行 AI 分析并保存结果
        result_data, result, error = analyze_paper_full(paper_data)
        if result:
            from database import insert_analysis
            insert_analysis(paper_data["id"], result)
            update_progress(task_id, {"current": 2, "total": 2, "status": "completed", "message": "添加并分析完成"})
            return jsonify({
                "status": "ok",
                "message": f"添加成功: {paper_data['title'][:50]}...",
                "arxiv_id": paper_data.get("arxiv_id"),
                "rating": result.get("rating", 0),
                "tags": result.get("tags", [])
            })
        else:
            # 论文已入库但分析失败（不影响论文存在性）
            update_progress(task_id, {"status": "error", "message": f"分析失败: {error}"})
            return jsonify({"status": "ok", "message": f"论文已添加但分析失败: {error}", "arxiv_id": paper_data.get("arxiv_id")})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ====================================================================
#                   阅读清单 API（Reading List API）
# ====================================================================

@app.route("/api/paper/<arxiv_id>/todo", methods=["POST"])
def api_add_todo(arxiv_id):
    """将论文加入阅读清单（如已在清单中则返回提示）"""
    paper = get_paper_by_arxiv_id(arxiv_id)
    if not paper:
        return jsonify({"status": "error", "message": "论文不存在"}), 404
    if is_in_reading_list(paper["id"]):
        return jsonify({"status": "ok", "message": "已在阅读清单中"})
    if add_to_reading_list(paper["id"]):
        return jsonify({"status": "ok", "message": "已加入阅读清单"})
    return jsonify({"status": "error", "message": "添加失败"}), 500


@app.route("/api/paper/<arxiv_id>/todo", methods=["DELETE"])
def api_remove_todo(arxiv_id):
    """将论文从阅读清单中移除"""
    paper = get_paper_by_arxiv_id(arxiv_id)
    if not paper:
        return jsonify({"status": "error", "message": "论文不存在"}), 404
    if remove_from_reading_list(paper["id"]):
        return jsonify({"status": "ok", "message": "已从阅读清单移除"})
    return jsonify({"status": "error", "message": "移除失败"}), 500


@app.route("/api/paper/<arxiv_id>/todo/read", methods=["POST"])
def api_mark_read(arxiv_id):
    """将论文标记为已读"""
    paper = get_paper_by_arxiv_id(arxiv_id)
    if not paper:
        return jsonify({"status": "error", "message": "论文不存在"}), 404
    if mark_as_read(paper["id"]):
        return jsonify({"status": "ok", "message": "已标记为已读"})
    return jsonify({"status": "error", "message": "操作失败"}), 500


@app.route("/api/paper/<arxiv_id>/todo/unread", methods=["POST"])
def api_mark_unread(arxiv_id):
    """将论文标记为未读"""
    paper = get_paper_by_arxiv_id(arxiv_id)
    if not paper:
        return jsonify({"status": "error", "message": "论文不存在"}), 404
    if mark_as_unread(paper["id"]):
        return jsonify({"status": "ok", "message": "已标记为未读"})
    return jsonify({"status": "error", "message": "操作失败"}), 500


@app.route("/api/reading-list", methods=["GET"])
def api_reading_list():
    """获取阅读清单 JSON 接口：支持按状态筛选，返回论文列表和各状态数量"""
    status = request.args.get("status", None)
    papers = get_reading_list(status=status)
    counts = get_reading_list_count()
    return jsonify({"papers": papers, "counts": counts})


# ====================================================================
#                 设置 API — 并发与分页（Settings API）
# ====================================================================

@app.route("/api/settings/concurrency", methods=["POST"])
def api_save_concurrency():
    """保存 AI 分析并发数设置（范围 1-20）"""
    try:
        data = request.get_json()
        val = int(data.get("concurrency", 5))
        val = max(1, min(20, val))
        settings = load_settings()
        settings["concurrency"] = val
        if save_settings(settings):
            return jsonify({"status": "ok", "message": f"并发数已设置为 {val}"})
        return jsonify({"status": "error", "message": "保存失败"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/settings/per_page", methods=["POST"])
def api_save_per_page():
    """保存每页显示论文数设置（范围 5-100）"""
    try:
        data = request.get_json()
        val = int(data.get("per_page", 20))
        val = max(5, min(100, val))
        settings = load_settings()
        settings["per_page"] = val
        if save_settings(settings):
            return jsonify({"status": "ok", "message": f"每页显示数已设置为 {val}"})
        return jsonify({"status": "error", "message": "保存失败"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ====================================================================
#                设置 API — 代理配置（Proxy Settings）
# ====================================================================

@app.route("/api/settings/proxy", methods=["GET"])
def api_get_proxy():
    """获取当前代理配置"""
    from settings import get_proxy_config
    return jsonify(get_proxy_config())


@app.route("/api/settings/proxy", methods=["POST"])
def api_save_proxy():
    """保存代理配置（启用状态、HTTP/HTTPS 代理地址）"""
    try:
        from settings import save_proxy_config
        data = request.get_json()
        proxy_config = {
            "enabled": bool(data.get("enabled", False)),
            "http": data.get("http", "").strip(),
            "https": data.get("https", "").strip(),
        }
        if save_proxy_config(proxy_config):
            return jsonify({"status": "ok", "message": "代理配置已保存"})
        return jsonify({"status": "error", "message": "保存失败"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/test_proxy", methods=["POST"])
def api_test_proxy():
    """测试代理连接：通过代理访问 arXiv API 验证连通性"""
    try:
        import requests as req
        from settings import get_proxy_config
        proxy = get_proxy_config()
        proxies = None
        # 如果代理已启用，构建代理字典
        if proxy.get("enabled"):
            proxies = {}
            if proxy.get("http"):
                proxies["http"] = proxy["http"]
            if proxy.get("https"):
                proxies["https"] = proxy["https"]

        # 向 arXiv API 发送测试请求
        resp = req.get("https://export.arxiv.org/api/query?search_query=cat:cs.RO&max_results=1",
                       proxies=proxies, timeout=15)
        if resp.status_code == 200:
            return jsonify({"status": "ok", "message": f"arXiv 连接成功（状态码: {resp.status_code}）"})
        else:
            return jsonify({"status": "error", "message": f"arXiv 返回异常状态码: {resp.status_code}"})
    except Exception as e:
        return jsonify({"status": "error", "message": f"连接失败: {str(e)}"})


# ====================================================================
#              设置 API — 抓取配置（Fetch Settings）
# ====================================================================

@app.route("/api/settings/fetch", methods=["GET"])
def api_get_fetch_config():
    """获取论文抓取配置（请求间隔、批次天数、批次间隔）"""
    from settings import get_fetch_config
    return jsonify(get_fetch_config())


@app.route("/api/settings/fetch", methods=["POST"])
def api_save_fetch_config():
    """保存论文抓取配置"""
    try:
        from settings import save_fetch_config
        data = request.get_json()
        fetch_config = {
            "request_delay": float(data.get("request_delay", 3.0)),   # 单次请求间隔（秒）
            "batch_days": int(data.get("batch_days", 30)),             # 每批抓取的天数跨度
            "batch_delay": float(data.get("batch_delay", 5.0)),       # 批次间等待时间（秒）
        }
        if save_fetch_config(fetch_config):
            return jsonify({"status": "ok", "message": "抓取配置已保存"})
        return jsonify({"status": "error", "message": "保存失败"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ====================================================================
#            设置 API — AI 供应商管理（Provider Management）
# ====================================================================

@app.route("/api/providers/presets", methods=["GET"])
def api_provider_presets():
    """获取预设的 AI 供应商列表（如 DeepSeek、OpenAI 等）"""
    return jsonify(get_provider_presets())


@app.route("/api/providers", methods=["GET"])
def api_list_providers():
    """
    获取所有已配置的 AI 供应商列表

    返回每个供应商的配置信息，其中 API Key 脱敏显示（仅保留首尾各 4 字符）。
    """
    settings = load_settings()
    providers = settings.get("providers", {})
    active = settings.get("active_provider", "")
    result = {}
    for k, v in providers.items():
        item = v.copy()
        # API Key 脱敏处理
        if item.get("api_key"):
            key = item["api_key"]
            item["api_key_masked"] = key[:4] + "****" + key[-4:] if len(key) > 8 else "****"
        else:
            item["api_key_masked"] = ""
        item["is_active"] = (k == active)
        result[k] = item
    return jsonify({"active_provider": active, "providers": result})


@app.route("/api/providers", methods=["POST"])
def api_add_provider():
    """添加新的 AI 供应商配置"""
    try:
        data = request.get_json()
        key = data.get("key", "").strip()
        if not key:
            return jsonify({"status": "error", "message": "供应商ID不能为空"}), 400
        config = {
            "name": data.get("name", key),
            "api_key": data.get("api_key", ""),
            "base_url": data.get("base_url", ""),
            "model": data.get("model", ""),
            "temperature": float(data.get("temperature", 0.3)),
            "max_tokens": int(data.get("max_tokens", 1000)),
            "is_thinking": bool(data.get("is_thinking", False)),  # 是否为思考型模型
        }
        if add_provider(key, config):
            return jsonify({"status": "ok", "message": f"供应商 {config['name']} 已添加"})
        return jsonify({"status": "error", "message": "保存失败"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/providers/<key>", methods=["PUT"])
def api_update_provider(key):
    """更新指定供应商的配置（仅更新传入的字段）"""
    try:
        data = request.get_json()
        config = {}
        # 仅收集请求中包含的字段，实现部分更新
        if "name" in data:
            config["name"] = data["name"]
        if "api_key" in data and data["api_key"]:
            config["api_key"] = data["api_key"]
        if "base_url" in data:
            config["base_url"] = data["base_url"]
        if "model" in data:
            config["model"] = data["model"]
        if "temperature" in data:
            config["temperature"] = float(data["temperature"])
        if "max_tokens" in data:
            config["max_tokens"] = int(data["max_tokens"])
        if "is_thinking" in data:
            config["is_thinking"] = bool(data["is_thinking"])
        if update_provider(key, config):
            return jsonify({"status": "ok", "message": "已更新"})
        return jsonify({"status": "error", "message": "更新失败，供应商不存在"}), 404
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/providers/<key>", methods=["DELETE"])
def api_delete_provider(key):
    """删除指定的 AI 供应商配置"""
    if remove_provider(key):
        return jsonify({"status": "ok", "message": "已删除"})
    return jsonify({"status": "error", "message": "删除失败"}), 400


@app.route("/api/providers/<key>/activate", methods=["POST"])
def api_activate_provider(key):
    """切换当前激活的 AI 供应商"""
    if switch_provider(key):
        return jsonify({"status": "ok", "message": f"已切换到 {key}"})
    return jsonify({"status": "error", "message": "切换失败，供应商不存在"}), 404


@app.route("/api/test_connection", methods=["POST"])
def api_test_connection():
    """测试当前激活的 AI 供应商连接：发送简单请求验证 API 可用性"""
    try:
        from openai import OpenAI
        from settings import get_ai_config
        cfg = get_ai_config()
        if not cfg["api_key"]:
            return jsonify({"status": "error", "message": "请先配置 API Key"}), 400
        client = OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])
        response = client.chat.completions.create(
            model=cfg["model"],
            messages=[{"role": "user", "content": "Hello, reply with 'ok' only."}],
            max_tokens=10,
        )
        reply = response.choices[0].message.content.strip()
        return jsonify({"status": "ok", "message": f"连接成功！模型回复: {reply}"})
    except Exception as e:
        return jsonify({"status": "error", "message": f"连接失败: {str(e)}"}), 500


@app.route("/api/detect_thinking", methods=["POST"])
def api_detect_thinking():
    """
    检测当前模型是否支持思考模式（reasoning）

    通过发送带有 enable_thinking 参数的请求，检查响应中是否包含 reasoning_content。
    """
    try:
        from openai import OpenAI
        cfg = get_ai_config()
        if not cfg["api_key"]:
            return jsonify({"status": "error", "message": "请先配置 API Key"}), 400
        client = OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])
        response = client.chat.completions.create(
            model=cfg["model"],
            messages=[{"role": "user", "content": "What is 1+1? Reply with just the number."}],
            max_tokens=50,
            extra_body={"enable_thinking": True},
        )
        msg = response.choices[0].message
        # 检查响应消息是否包含思考内容字段
        has_thinking = hasattr(msg, "reasoning_content") and bool(msg.reasoning_content)
        if has_thinking:
            return jsonify({"status": "ok", "is_thinking": True, "message": "✅ 该模型支持思考模式，已自动开启"})
        else:
            return jsonify({"status": "ok", "is_thinking": False, "message": "ℹ️ 该模型未返回思考内容，可能不支持思考模式"})
    except Exception as e:
        return jsonify({"status": "error", "message": f"检测失败: {str(e)}"}), 500


# ====================================================================
#                设置 API — Prompt 与数据库（Prompt & DB）
# ====================================================================

@app.route("/api/prompts", methods=["GET"])
def api_get_prompts():
    """获取当前的 system_prompt 和 user_prompt 配置"""
    return jsonify(get_prompts())


@app.route("/api/prompts", methods=["POST"])
def api_save_prompts():
    """保存 system_prompt 和 user_prompt 配置"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "无效的请求数据"}), 400
        prompts = {
            "system_prompt": data.get("system_prompt", ""),
            "user_prompt": data.get("user_prompt", ""),
        }
        if save_prompts(prompts):
            return jsonify({"status": "ok", "message": "Prompt 已保存"})
        return jsonify({"status": "error", "message": "保存失败"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/db/info", methods=["GET"])
def api_db_info():
    """
    获取数据库概览信息

    返回：论文总数、已分析数、标签数、分类数、日期范围、数据库文件大小、平均评级等。
    """
    import os
    from config import DB_PATH, OUTPUT_DIR, DAILY_DIR
    from database import get_all_tags, get_all_categories

    total = get_paper_count()
    analyzed = get_analyzed_count()
    unanalyzed = get_unanalyzed_count()
    tags = get_all_tags()
    categories = get_all_categories()

    # 计算论文日期范围
    dates = get_all_dates()
    date_range = ""
    if dates:
        date_range = f"{dates[-1][0]} ~ {dates[0][0]}"

    # 计算数据库文件大小（自动选择 KB/MB 单位）
    db_size = "N/A"
    if os.path.exists(DB_PATH):
        size_bytes = os.path.getsize(DB_PATH)
        if size_bytes > 1024 * 1024:
            db_size = f"{size_bytes / 1024 / 1024:.1f} MB"
        else:
            db_size = f"{size_bytes / 1024:.1f} KB"

    # 计算所有已分析论文的平均评级
    avg_rating = None
    if analyzed > 0:
        conn = None
        try:
            import sqlite3
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT AVG(rating) FROM analysis")
            row = cursor.fetchone()
            if row and row[0]:
                avg_rating = f"{row[0]:.1f}"
        except:
            pass
        finally:
            if conn:
                conn.close()

    return jsonify({
        "total_papers": total,
        "analyzed_papers": analyzed,
        "unanalyzed_papers": unanalyzed,
        "total_tags": len(tags),
        "total_categories": len(categories),
        "date_range": date_range,
        "db_size": db_size,
        "avg_rating": avg_rating,
        "db_path": DB_PATH,
        "output_dir": OUTPUT_DIR,
        "daily_dir": DAILY_DIR,
    })


# ====================================================================
#              设置 API — 管理密码（Admin Password）
# ====================================================================

@app.route("/api/admin/password", methods=["POST"])
def api_set_admin_password():
    """
    设置或修改管理密码

    如果已设置密码，需要提供当前密码进行验证。
    密码以 SHA256 哈希存储。
    """
    try:
        data = request.get_json()
        current = data.get("current_password", "")
        new_pwd = data.get("new_password", "")

        # 如果已有密码，验证当前密码
        if has_admin_password():
            if not verify_admin_password(current):
                return jsonify({"status": "error", "message": "当前密码错误"}), 403

        if not new_pwd:
            return jsonify({"status": "error", "message": "新密码不能为空"}), 400

        set_admin_password(new_pwd)
        return jsonify({"status": "ok", "message": "管理密码已设置"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/admin/password", methods=["DELETE"])
def api_clear_admin_password():
    """清除管理密码（设置为空字符串）"""
    try:
        set_admin_password("")
        return jsonify({"status": "ok", "message": "管理密码已清除"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ====================================================================
#                任务日志 API（Task Logs API）
# ====================================================================

@app.route("/api/tasks/stats", methods=["GET"])
def api_task_stats():
    """获取任务统计数据（各任务类型的执行次数、成功率等）和当前运行中的任务"""
    stats = get_task_stats()
    running = get_running_tasks()
    return jsonify({"stats": stats, "running": running})


@app.route("/api/tasks/logs", methods=["GET"])
def api_task_logs():
    """
    获取任务日志列表

    支持查询参数：
    - task: 按任务类型筛选（如 daily_pipeline、fetch、analyze）
    - page: 页码（每页固定 30 条）
    """
    task_name = request.args.get("task", None)
    page = request.args.get("page", 1, type=int)
    per_page = 30
    logs, total = get_task_logs(task_name=task_name, limit=per_page, offset=(page - 1) * per_page)
    return jsonify({"logs": logs, "total": total, "page": page, "per_page": per_page})


@app.route("/api/tasks/clear", methods=["POST"])
def api_clear_logs():
    """清理旧的任务日志（默认保留最近 30 天）"""
    keep_days = request.args.get("keep_days", 30, type=int)
    deleted = clear_task_logs(keep_days=keep_days)
    return jsonify({"status": "ok", "message": f"已清理 {deleted} 条 {keep_days} 天前的日志"})


@app.route("/api/tasks/scheduled", methods=["GET"])
def api_scheduled_tasks():
    """获取 APScheduler 中所有已注册的定时任务信息（ID、名称、下次执行时间、触发器类型）"""
    jobs = []
    for job in scheduler.get_jobs():
        next_run = job.next_run_time
        jobs.append({
            "id": job.id,
            "name": job.name or job.id,
            "next_run": next_run.strftime("%Y-%m-%d %H:%M:%S") if next_run else "未调度",
            "trigger": str(job.trigger),
        })
    return jsonify(jobs)


# ====================================================================
#                      应用初始化与启动
# ====================================================================

def create_app():
    """
    应用工厂函数

    负责：
    1. 初始化数据库（含表结构迁移）
    2. 注册每日定时任务到 APScheduler
    3. 启动调度器
    """
    init_db()

    # 注册每日定时任务（cron 表达式由 config.py 中的 SCHEDULE_HOUR/MINUTE 控制）
    scheduler.add_job(
        daily_pipeline,
        "cron",
        hour=SCHEDULE_HOUR,
        minute=SCHEDULE_MINUTE,
        id="daily_pipeline",
        name="每日定时任务",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(f"Scheduler started: daily at {SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d}")

    return app


if __name__ == "__main__":
    app = create_app()
    logger.info(f"Starting web server at http://{WEB_HOST}:{WEB_PORT}")
    app.run(host=WEB_HOST, port=WEB_PORT, debug=False)
