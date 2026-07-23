"""Daily pipeline orchestration."""

import json
import logging
import threading
import time
from datetime import datetime

from analyzer import analyze_pending_papers, generate_report_ai_summary, recommend_pending_papers
from backup import run_webdav_backup
from email_report import send_report_email
from fetcher import fetch_latest_papers
from source.reports import generate_report_content
from source.settings import (
    get_concurrency,
    get_email_report_config,
    get_personalization_config,
    get_schedule_config,
    get_webdav_backup_config,
)
from source.storage import (
    finish_task_log,
    get_all_dates,
    initialize_task_log_steps,
    save_report,
    set_task_log_step_status,
    start_task_log,
)

logger = logging.getLogger(__name__)

pipeline_lock = threading.Lock()


def _run_webdav_backup_task(force=False, log_task=True):
    """执行 WebDAV 云备份；独立调用时写顶级日志。"""
    log_id = start_task_log("webdav_backup", "WebDAV 云同步备份") if log_task else None
    try:
        result = run_webdav_backup(force=force)
        if result.get("status") == "skipped":
            if log_id:
                finish_task_log(log_id, "success", result.get("message", "WebDAV 云备份已跳过"))
            return result
        detail = (
            f"uploaded={','.join(result.get('uploaded_files', []))}, "
            f"deleted={','.join(result.get('deleted_files', []))}, "
            f"archive_size={result.get('archive_size', '')}, db_size={result.get('db_size', '')}"
        )
        if log_id:
            finish_task_log(log_id, "success", result.get("message", "WebDAV 备份完成"), detail)
        return result
    except Exception as e:
        logger.error(f"WebDAV backup error: {e}")
        if log_id:
            finish_task_log(log_id, "error", f"WebDAV 备份失败：{e}")
        raise


def _run_email_report_task(report, force=False, log_task=True):
    """发送每日报告邮件；独立调用时写顶级日志。"""
    log_id = start_task_log("email_report", "发送每日报告邮件") if log_task else None
    try:
        email_config = get_email_report_config(mask_password=False)
        report_date = str(report.get("report_date") or "").strip()
        last_sent_report_date = str(email_config.get("last_sent_report_date") or "").strip()
        if not force and report_date and report_date == last_sent_report_date:
            message = f"日报 {report_date} 已发送，跳过重复发送"
            result = {
                "status": "skipped",
                "reason": "already_sent",
                "message": message,
                "report_date": report_date,
            }
            if log_id:
                finish_task_log(
                    log_id,
                    "success",
                    message,
                    f"report_date={report_date}, reason=already_sent",
                )
            return result

        ai_summary = None
        ai_summary_error = ""
        if email_config.get("enabled") or force:
            try:
                ai_summary, ai_summary_error = generate_report_ai_summary(report_date)
            except Exception as summary_exc:
                ai_summary_error = str(summary_exc)
                ai_summary = None
        result = send_report_email(
            report,
            config=email_config,
            force=force,
            ai_summary=ai_summary,
            ai_summary_error=ai_summary_error,
        )
        if result.get("status") == "skipped":
            if log_id:
                finish_task_log(log_id, "success", result.get("message", "报告邮件发送已跳过"))
            return result
        detail = (
            f"report_date={result.get('report_date', '')}, "
            f"recipients={','.join(result.get('recipients', []))}, "
            f"subject={result.get('subject', '')}, "
            f"important={result.get('important_count', 0)}, overview={result.get('overview_count', 0)}"
        )
        if ai_summary_error:
            detail += f", ai_summary_error={ai_summary_error}"
        if log_id:
            finish_task_log(log_id, "success", result.get("message", "报告邮件发送完成"), detail)
        return result
    except Exception as e:
        logger.error(f"Email report error: {e}")
        if log_id:
            finish_task_log(log_id, "error", f"报告邮件发送失败：{e}")
        raise


DAILY_PIPELINE_STEPS = (
    ("fetch", "抓取论文"),
    ("analyze", "基础分析"),
    ("recommend", "推荐评分"),
    ("report", "生成报告"),
    ("email", "发送邮件"),
    ("backup", "WebDAV 备份"),
)


def _skip_pending_pipeline_steps(log_id, current_step, reason):
    """将核心步骤失败后尚未执行的步骤收口为 skipped。"""
    keys = [key for key, _ in DAILY_PIPELINE_STEPS]
    start = keys.index(current_step) + 1 if current_step in keys else 0
    for step_key in keys[start:]:
        set_task_log_step_status(log_id, step_key, "skipped", reason)


def _daily_fetch_retry_policy_text(retry_interval_minutes, max_retries):
    """生成定时日报抓取失败重试策略的短描述。"""
    if max_retries <= 0:
        return "失败后不重试"
    return f"失败后每 {retry_interval_minutes} 分钟重试，最多 {max_retries} 次"


def _fetch_for_daily_pipeline_with_retries(log_id, fetch_days, retry_interval_minutes, max_retries):
    """为定时日报抓取阶段执行失败等待重试。"""
    retry_interval_minutes = max(1, int(retry_interval_minutes or 10))
    max_retries = max(0, int(max_retries or 0))
    retry_count = 0

    while True:
        if retry_count > 0 and log_id is not None:
            set_task_log_step_status(
                log_id,
                "fetch",
                "running",
                f"第 {retry_count}/{max_retries} 次重试抓取最近 {fetch_days} 天论文",
            )
        try:
            return fetch_latest_papers(days=fetch_days), retry_count
        except Exception as fetch_error:
            if retry_count >= max_retries:
                raise RuntimeError(f"抓取失败，已重试 {retry_count} 次仍未成功：{fetch_error}") from fetch_error

            next_retry = retry_count + 1
            wait_seconds = retry_interval_minutes * 60
            message = (
                f"抓取失败：{fetch_error}；"
                f"{retry_interval_minutes} 分钟后第 {next_retry}/{max_retries} 次重试"
            )
            logger.warning(message)
            if log_id is not None:
                set_task_log_step_status(log_id, "fetch", "running", message)
            time.sleep(wait_seconds)
            retry_count = next_retry


def daily_pipeline():
    """执行唯一的内置 AI 论文日报流水线，并记录六步结构化日志。"""
    if not pipeline_lock.acquire(blocking=False):
        log_id = start_task_log("daily_pipeline", "AI 论文日报触发")
        message = "已有完整流水线正在运行，本次定时触发已跳过"
        finish_task_log(log_id, "skipped", message)
        return {"status": "skipped", "message": message}

    log_id = None
    warnings = []
    current_step = "fetch"
    try:
        log_id = start_task_log("daily_pipeline", "AI 论文日报启动")
        initialize_task_log_steps(log_id, DAILY_PIPELINE_STEPS)
        schedule = get_schedule_config()
        fetch_days = schedule.get("fetch_days", 3)
        analyze_limit = schedule.get("analyze_limit", 1000)
        fetch_retry_interval_minutes = schedule.get("fetch_retry_interval_minutes", 10)
        fetch_max_retries = schedule.get("fetch_max_retries", 20)

        retry_policy = _daily_fetch_retry_policy_text(fetch_retry_interval_minutes, fetch_max_retries)
        set_task_log_step_status(log_id, "fetch", "running", f"抓取最近 {fetch_days} 天论文；{retry_policy}")
        new_papers, fetch_retries = _fetch_for_daily_pipeline_with_retries(
            log_id,
            fetch_days,
            fetch_retry_interval_minutes,
            fetch_max_retries,
        )
        fetch_message = f"抓取完成：{len(new_papers)} 篇新论文（回看 {fetch_days} 天，重试 {fetch_retries} 次后成功）"
        set_task_log_step_status(log_id, "fetch", "success", fetch_message)
        logger.info(fetch_message)

        current_step = "analyze"
        analysis_progress = {}

        def analysis_progress_callback(data):
            if data.get("status") in ("completed", "error"):
                analysis_progress.update(data)

        concurrency = get_concurrency()
        set_task_log_step_status(log_id, "analyze", "running", f"最多分析 {analyze_limit} 篇论文")
        analyzed_count = analyze_pending_papers(
            limit=analyze_limit,
            concurrency=concurrency,
            progress_callback=analysis_progress_callback,
        )
        analysis_failures = int(analysis_progress.get("fail", 0) or 0)
        analysis_status = "warning" if analysis_failures else "success"
        analysis_message = f"基础分析完成：{analyzed_count} 篇成功"
        if analysis_failures:
            analysis_message += f"，{analysis_failures} 篇失败"
            warnings.append(analysis_message)
        set_task_log_step_status(
            log_id,
            "analyze",
            analysis_status,
            analysis_message,
            json.dumps(analysis_progress, ensure_ascii=False),
        )

        dates = get_all_dates()
        report_date = dates[0][0] if dates else datetime.now().strftime("%Y-%m-%d")

        current_step = "recommend"
        personalization = get_personalization_config()
        recommended_count = 0
        if not personalization.get("research_interests"):
            set_task_log_step_status(log_id, "recommend", "skipped", "未设置研究兴趣，跳过推荐评分")
        else:
            recommendation_progress = {}

            def recommendation_progress_callback(data):
                if data.get("status") in ("completed", "error"):
                    recommendation_progress.update(data)

            set_task_log_step_status(log_id, "recommend", "running", f"计算 {report_date} 推荐评分")
            recommended_count = recommend_pending_papers(
                limit=1000,
                date=report_date,
                concurrency=concurrency,
                progress_callback=recommendation_progress_callback,
            )
            recommendation_failures = int(recommendation_progress.get("fail", 0) or 0)
            recommendation_status = "warning" if recommendation_failures else "success"
            recommendation_message = f"推荐评分完成：{recommended_count} 篇成功"
            if recommendation_failures:
                recommendation_message += f"，{recommendation_failures} 篇失败"
                warnings.append(recommendation_message)
            set_task_log_step_status(
                log_id,
                "recommend",
                recommendation_status,
                recommendation_message,
                json.dumps(recommendation_progress, ensure_ascii=False),
            )

        current_step = "report"
        set_task_log_step_status(log_id, "report", "running", f"生成 {report_date} 日报")
        content, paper_count, analyzed_count_r, avg_rating = generate_report_content(report_date)
        if not content:
            raise RuntimeError(f"{report_date} 无论文数据，无法生成报告")
        save_report(report_date, content, paper_count, analyzed_count_r, avg_rating)
        report_message = f"报告已生成：{report_date}，{paper_count} 篇论文"
        set_task_log_step_status(log_id, "report", "success", report_message)

        current_step = "email"
        try:
            email_config = get_email_report_config(mask_password=False)
            if not email_config.get("enabled"):
                email_message = "报告邮件未启用"
                set_task_log_step_status(log_id, "email", "skipped", email_message)
            else:
                set_task_log_step_status(log_id, "email", "running", "发送每日报告邮件")
                email_result = _run_email_report_task({
                    "report_date": report_date,
                    "content": content,
                    "paper_count": paper_count,
                    "analyzed_count": analyzed_count_r,
                    "avg_rating": avg_rating,
                }, force=False, log_task=False)
                email_message = email_result.get("message", "报告邮件发送完成")
                email_status = "skipped" if email_result.get("status") == "skipped" else "success"
                set_task_log_step_status(log_id, "email", email_status, email_message)
        except Exception as email_error:
            email_message = f"报告邮件发送失败：{email_error}"
            warnings.append(email_message)
            set_task_log_step_status(log_id, "email", "warning", email_message)

        current_step = "backup"
        try:
            backup_config = get_webdav_backup_config(mask_password=False)
            if not backup_config.get("enabled"):
                backup_message = "WebDAV 云备份未启用"
                set_task_log_step_status(log_id, "backup", "skipped", backup_message)
            else:
                set_task_log_step_status(log_id, "backup", "running", "执行 WebDAV 云备份")
                backup_result = _run_webdav_backup_task(force=False, log_task=False)
                backup_message = backup_result.get("message", "WebDAV 备份完成")
                backup_status = "skipped" if backup_result.get("status") == "skipped" else "success"
                set_task_log_step_status(log_id, "backup", backup_status, backup_message)
        except Exception as backup_error:
            backup_message = f"WebDAV 备份失败：{backup_error}"
            warnings.append(backup_message)
            set_task_log_step_status(log_id, "backup", "warning", backup_message)

        final_status = "warning" if warnings else "success"
        message = (
            f"完成：抓取 {len(new_papers)} 篇，分析 {analyzed_count} 篇，"
            f"推荐评分 {recommended_count} 篇，报告 {report_date} 已生成"
        )
        if warnings:
            message += f"；警告：{'；'.join(warnings)}"
        detail = json.dumps({
            "new_papers": len(new_papers),
            "analyzed": analyzed_count,
            "recommended": recommended_count,
            "report_date": report_date,
            "concurrency": concurrency,
        }, ensure_ascii=False)
        finish_task_log(log_id, final_status, message, detail)
        return {"status": final_status, "message": message, "log_id": log_id}
    except Exception as e:
        logger.error(f"Daily pipeline error: {e}")
        if log_id is not None:
            set_task_log_step_status(log_id, current_step, "error", f"执行失败：{e}")
            _skip_pending_pipeline_steps(log_id, current_step, "前置步骤失败，未执行")
            finish_task_log(log_id, "error", f"失败：{e}")
        return {"status": "error", "message": str(e), "log_id": log_id}
    finally:
        pipeline_lock.release()
