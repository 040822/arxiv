"""Flask tasks_api routes."""

import hashlib
import hmac
import json
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
from source.analysis import (
    analyze_pending_papers, generate_report_ai_summary, recommend_pending_papers,
)
from source.ingestion import fetch_batch, fetch_by_date, fetch_latest_papers
from source.pipeline import (
    _run_email_report_task, _run_webdav_backup_task, pipeline_lock, scheduler,
)
from source.reports import generate_report_content
from source.settings import (
    get_concurrency,
    get_fetch_config,
    get_personalization_config,
    get_schedule_config,
)
from source.storage import (
    clear_task_logs,
    finish_task_log,
    get_all_dates,
    get_reports,
    get_running_tasks,
    get_task_logs,
    get_task_stats,
    get_unanalyzed_count,
    save_report,
    start_task_log,
)
from .progress import get_progress, update_progress

logger = logging.getLogger(__name__)


bp = Blueprint("tasks_api", __name__)


@bp.route("/api/fetch", methods=["POST"])
def api_fetch():
    """
    论文抓取 API

    支持的查询参数：
    - task_id: 任务追踪 ID（用于进度查询）
    - category: 指定 arXiv 分类（如 cs.RO）
    - days: 抓取最近 N 天的论文（按日期窗口抓全）；缺省为最近 1 天
    - date: 抓取指定日期的论文
    """
    if "max_results" in request.args:
        return jsonify({
            "status": "error",
            "message": "max_results 参数已废弃，请使用 days（默认 1 天）或 date",
        }), 400

    task_id = request.args.get("task_id", "fetch")
    log_id = start_task_log("fetch", "手动抓取论文")
    try:
        category = request.args.get("category", "").strip()
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
            # 默认抓取最近 1 天（按日期窗口抓全）
            new_papers = fetch_latest_papers(categories=categories)
            desc = "默认（最近 1 天）"

        unanalyzed = get_unanalyzed_count()
        cat_desc = f"（分类: {category}）" if category else ""
        msg = f"抓取完成：{len(new_papers)} 篇新论文{cat_desc}（{desc}）" + (f"，{unanalyzed} 篇待分析" if unanalyzed else "")
        finish_task_log(log_id, "success", msg, f"new_papers={len(new_papers)}, unanalyzed={unanalyzed}")
        return jsonify({"status": "ok", "count": len(new_papers), "unanalyzed": unanalyzed, "message": msg})
    except Exception as e:
        finish_task_log(log_id, "error", str(e))
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/analyze", methods=["POST"])
def api_analyze():
    """
    AI 分析 API：对未分析的论文进行低成本基础分析

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


@bp.route("/api/recommendations/recalculate", methods=["POST"])
def api_recalculate_recommendations():
    """手动重算缺失或过期的个性化推荐分。"""
    task_id = request.args.get("task_id", "recommend")
    log_id = start_task_log("recommend", "手动重算个性化推荐评分")
    try:
        cfg = get_personalization_config()
        if not cfg.get("research_interests"):
            finish_task_log(log_id, "error", "未设置研究兴趣")
            return jsonify({"status": "error", "message": "请先在设置页填写研究兴趣"}), 400

        data = request.get_json(silent=True) or {}
        limit = request.args.get("limit", data.get("limit", 200), type=int)
        limit = max(1, min(1000, int(limit or 200)))
        date = (request.args.get("date") or data.get("date") or "").strip() or None
        concurrency = get_concurrency()

        def progress_callback(progress):
            update_progress(task_id, {**progress, "phase": "recommend"})

        count = recommend_pending_papers(
            limit=limit,
            date=date,
            concurrency=concurrency,
            progress_callback=progress_callback,
        )
        scope = f"（日期 {date}）" if date else ""
        msg = f"推荐评分完成{scope}：{count} 篇已更新"
        finish_task_log(log_id, "success", msg, f"recommended={count}, limit={limit}, date={date or ''}, concurrency={concurrency}")
        return jsonify({"status": "ok", "count": count, "message": msg})
    except Exception as e:
        finish_task_log(log_id, "error", str(e))
        update_progress(task_id, {"status": "error", "message": str(e), "phase": "recommend"})
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/run", methods=["POST"])
def api_run():
    """
    抓取、分析并生成报告 API

    依次执行：抓取论文 → AI 分析 → 推荐评分 → 生成报告
    通过进度回调实时更新各阶段状态。
    """
    task_id = request.args.get("task_id", "run")
    if not pipeline_lock.acquire(blocking=False):
        return jsonify({
            "status": "error",
            "message": "已有完整流水线正在运行，请等待完成后再试",
        }), 409
    log_id = None
    try:
        log_id = start_task_log("run", "抓取、分析并生成报告")
        # 阶段1：抓取论文（与定时日报一致的回看天数窗口）
        update_progress(task_id, {"current": 0, "total": 4, "status": "running", "message": "正在抓取论文..."})
        new_papers = fetch_latest_papers(days=get_schedule_config()["fetch_days"])

        # 阶段2：AI 分析
        update_progress(task_id, {"current": 1, "total": 4, "status": "running", "message": f"抓取完成，开始分析 {len(new_papers)} 篇新论文..."})
        concurrency = get_concurrency()

        def analysis_progress_callback(data):
            update_progress(task_id, {**data, "phase": "analyze"})

        analyzed_count = analyze_pending_papers(limit=100, concurrency=concurrency, progress_callback=analysis_progress_callback, ingest_mode="feed")

        dates = get_all_dates(ingest_mode="feed")
        report_date = dates[0][0] if dates else datetime.now().strftime("%Y-%m-%d")

        # 阶段3：个性化推荐评分
        update_progress(task_id, {"current": 2, "total": 4, "status": "running", "message": "正在计算个性化推荐分..."})

        def recommendation_progress_callback(data):
            update_progress(task_id, {**data, "phase": "recommend"})

        recommended_count = recommend_pending_papers(
            limit=1000,
            date=report_date,
            concurrency=concurrency,
            ingest_mode="feed",
            progress_callback=recommendation_progress_callback,
        )

        # 阶段4：生成报告
        update_progress(task_id, {"current": 3, "total": 4, "status": "running", "message": "正在生成报告..."})
        content, paper_count, analyzed_count_r, avg_rating = generate_report_content(report_date)
        if content:
            save_report(report_date, content, paper_count, analyzed_count_r, avg_rating)

        msg = f"完成！抓取 {len(new_papers)} 篇，分析 {analyzed_count} 篇，推荐评分 {recommended_count} 篇，报告已生成"
        finish_task_log(log_id, "success", msg, f"fetched={len(new_papers)}, analyzed={analyzed_count}, recommended={recommended_count}, concurrency={concurrency}")
        update_progress(task_id, {"current": 4, "total": 4, "status": "completed", "message": msg})
        return jsonify({"status": "ok", "fetched": len(new_papers), "analyzed": analyzed_count, "recommended": recommended_count, "concurrency": concurrency, "message": msg})
    except Exception as e:
        if log_id is not None:
            finish_task_log(log_id, "error", str(e))
        update_progress(task_id, {"status": "error", "message": str(e)})
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        pipeline_lock.release()


@bp.route("/api/progress/<task_id>")
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


@bp.route("/api/generate", methods=["POST"])
def api_generate():
    """
    报告生成 API

    支持查询参数 date（指定日期），默认使用最新日期。
    如果指定日期无论文数据，返回 400 错误。
    """
    log_id = start_task_log("generate", "手动生成报告")
    try:
        from datetime import datetime
        data = request.get_json(silent=True) or {}
        date_param = request.args.get("date", "").strip()
        include_ai_summary = str(request.args.get("ai_summary", "")).lower() in {"1", "true", "yes", "on"} \
            or bool(data.get("ai_summary"))
        recommend_arg = str(request.args.get("recommend", "")).lower()
        skip_recommend = recommend_arg in {"0", "false", "no", "off"} or data.get("recommend") is False
        if date_param:
            report_date = date_param
        else:
            dates = get_all_dates(ingest_mode="feed")
            report_date = dates[0][0] if dates else datetime.now().strftime("%Y-%m-%d")

        recommended_count = 0
        if not skip_recommend:
            recommended_count = recommend_pending_papers(limit=1000, date=report_date, concurrency=get_concurrency(), ingest_mode="feed")

        ai_summary = None
        if include_ai_summary:
            ai_summary, summary_error = generate_report_ai_summary(report_date)
            if summary_error:
                finish_task_log(log_id, "error", f"AI 导读生成失败: {summary_error}")
                return jsonify({"status": "error", "message": f"AI 导读生成失败: {summary_error}"}), 500

        content, paper_count, analyzed_count, avg_rating = generate_report_content(report_date, ai_summary=ai_summary)
        if content is None:
            finish_task_log(log_id, "error", f"{report_date} 无论文数据")
            return jsonify({"status": "error", "message": f"{report_date} 无论文数据，请先抓取该日论文"}), 400
        save_report(report_date, content, paper_count, analyzed_count, avg_rating)
        msg = f"报告生成成功（{report_date}）：{paper_count} 篇论文，{analyzed_count} 篇已分析，平均评级 {avg_rating}"
        if recommended_count:
            msg += f"，推荐评分 {recommended_count} 篇"
        if ai_summary:
            msg += "，包含 AI 导读"
        finish_task_log(log_id, "success", msg, f"date={report_date}, papers={paper_count}, analyzed={analyzed_count}, recommended={recommended_count}, ai_summary={bool(ai_summary)}")
        return jsonify({"status": "ok", "date": report_date, "ai_summary": bool(ai_summary), "recommended": recommended_count, "message": msg})
    except Exception as e:
        finish_task_log(log_id, "error", str(e))
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/backup/webdav/run", methods=["POST"])
def api_run_webdav_backup():
    """手动执行一次 WebDAV 云备份。"""
    try:
        result = _run_webdav_backup_task(force=True)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": f"WebDAV 备份失败: {e}"}), 500


@bp.route("/api/email-report/test", methods=["POST"])
def api_test_email_report():
    """手动发送最近一份报告邮件，用于验证 SMTP 配置。"""
    try:
        reports = get_reports(limit=1)
        if not reports:
            return jsonify({"status": "error", "message": "暂无可发送的日报告，请先生成报告"}), 400
        result = _run_email_report_task(reports[0], force=True)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": f"报告邮件发送失败: {e}"}), 500


@bp.route("/api/tasks/stats", methods=["GET"])
def api_task_stats():
    """获取任务统计数据（各任务类型的执行次数、成功率等）和当前运行中的任务"""
    stats = get_task_stats()
    running = get_running_tasks()
    return jsonify({"stats": stats, "running": running})


@bp.route("/api/tasks/logs", methods=["GET"])
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


@bp.route("/api/tasks/clear", methods=["POST"])
def api_clear_logs():
    """清理旧的任务日志（默认保留最近 30 天）"""
    keep_days = request.args.get("keep_days", 30, type=int)
    deleted = clear_task_logs(keep_days=keep_days)
    return jsonify({"status": "ok", "message": f"已清理 {deleted} 条 {keep_days} 天前的日志"})


@bp.route("/api/tasks/scheduled", methods=["GET"])
def api_scheduled_tasks():
    """获取内置日报配置、运行时状态和最近一次执行结果。"""
    schedule = get_schedule_config()
    jobs = []
    for job in scheduler.get_jobs():
        next_run = job.next_run_time
        jobs.append({
            "id": job.id,
            "name": job.name or job.id,
            "next_run": next_run.strftime("%Y-%m-%d %H:%M:%S") if next_run else "未调度",
            "trigger": str(job.trigger),
        })
    recent_runs, _ = get_task_logs(task_name="daily_pipeline", limit=1, offset=0)
    return jsonify({
        **schedule,
        "schedule": schedule,
        "timezone": str(datetime.now().astimezone().tzinfo),
        "jobs": jobs,
        "last_run": recent_runs[0] if recent_runs else None,
    })
