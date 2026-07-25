"""Manual combined pipeline orchestration."""

from datetime import datetime

from analyzer import analyze_pending_papers, recommend_pending_papers
from fetcher import fetch_latest_papers
from source.reports import generate_report_content
from source.settings import get_concurrency
from source.storage import (
    finish_task_log,
    get_all_dates,
    save_report,
    start_task_log,
)
from .orchestrator import pipeline_lock


class PipelineBusyError(RuntimeError):
    """Raised when another combined pipeline owns the process lock."""


def run_manual_pipeline(progress_callback):
    """Run fetch, analysis, recommendation and report generation under one lock."""
    if not pipeline_lock.acquire(blocking=False):
        raise PipelineBusyError("已有完整流水线正在运行，请等待完成后再试")

    log_id = None
    try:
        log_id = start_task_log("run", "抓取、分析并生成报告")
        progress_callback({"current": 0, "total": 4, "status": "running", "message": "正在抓取论文..."})
        new_papers = fetch_latest_papers()

        progress_callback({
            "current": 1,
            "total": 4,
            "status": "running",
            "message": f"抓取完成，开始分析 {len(new_papers)} 篇新论文...",
        })
        concurrency = get_concurrency()

        def analysis_progress_callback(data):
            progress_callback({**data, "phase": "analyze"})

        analyzed_count = analyze_pending_papers(
            limit=100,
            concurrency=concurrency,
            progress_callback=analysis_progress_callback,
            ingest_mode="feed",
        )
        dates = get_all_dates(ingest_mode="feed")
        report_date = dates[0][0] if dates else datetime.now().strftime("%Y-%m-%d")

        progress_callback({
            "current": 2,
            "total": 4,
            "status": "running",
            "message": "正在计算个性化推荐分...",
        })

        def recommendation_progress_callback(data):
            progress_callback({**data, "phase": "recommend"})

        recommended_count = recommend_pending_papers(
            limit=1000,
            date=report_date,
            concurrency=concurrency,
            ingest_mode="feed",
            progress_callback=recommendation_progress_callback,
        )

        progress_callback({
            "current": 3,
            "total": 4,
            "status": "running",
            "message": "正在生成报告...",
        })
        content, paper_count, analyzed_count_r, avg_rating = generate_report_content(report_date)
        if content:
            save_report(report_date, content, paper_count, analyzed_count_r, avg_rating)

        message = (
            f"完成！抓取 {len(new_papers)} 篇，分析 {analyzed_count} 篇，"
            f"推荐评分 {recommended_count} 篇，报告已生成"
        )
        finish_task_log(
            log_id,
            "success",
            message,
            (
                f"fetched={len(new_papers)}, analyzed={analyzed_count}, "
                f"recommended={recommended_count}, concurrency={concurrency}"
            ),
        )
        result = {
            "status": "ok",
            "fetched": len(new_papers),
            "analyzed": analyzed_count,
            "recommended": recommended_count,
            "concurrency": concurrency,
            "message": message,
        }
        progress_callback({"current": 4, "total": 4, "status": "completed", "message": message})
        return result
    except Exception as exc:
        if log_id is not None:
            finish_task_log(log_id, "error", str(exc))
        raise
    finally:
        pipeline_lock.release()
