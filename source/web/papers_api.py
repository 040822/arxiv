"""Flask papers_api routes."""

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
    get_concurrency,
    get_per_page,
)
from source.storage import (
    add_to_reading_list,
    batch_delete_papers,
    batch_hide_papers,
    delete_paper,
    get_all_tags,
    get_analysis_by_paper_id,
    get_analyzed_count,
    get_paper_by_arxiv_id,
    get_paper_count,
    get_papers_with_analysis,
    get_reading_list,
    get_reading_list_count,
    get_unanalyzed_count,
    get_unanalyzed_papers_by_ids,
    hide_paper,
    insert_analysis,
    is_in_reading_list,
    mark_as_read,
    mark_as_unread,
    remove_from_reading_list,
    unhide_paper,
    update_analysis,
)
from source.storage.row_mapping import parse_paper_row
from .progress import get_progress, update_progress

logger = logging.getLogger(__name__)


bp = Blueprint("papers_api", __name__)


def _has_basic_analysis(analysis):
    """检查 tags、中文摘要和简评是否都已由基础分析补齐。"""
    if not analysis:
        return False
    tags = analysis.get("tags")
    return bool(tags and analysis.get("summary_cn") and analysis.get("value_comment"))


@bp.route("/api/papers")
def api_papers():
    """获取论文列表 JSON 接口：支持按日期、标签、评级筛选和分页"""
    page = request.args.get("page", 1, type=int)
    tag = request.args.get("tag", None)
    date = request.args.get("date", None)
    min_rating = request.args.get("min_rating", None, type=int)
    per_page_param = request.args.get("per_page", None, type=int)
    per_page = get_per_page()
    if per_page_param and per_page_param in (5, 10, 20, 50, 100):
        per_page = per_page_param

    papers = get_papers_with_analysis(
        date=date, tag=tag, min_rating=min_rating,
        limit=per_page, offset=(page - 1) * per_page
    )
    return jsonify(papers)


@bp.route("/api/tags")
def api_tags():
    """获取所有标签及其论文数量的 JSON 接口"""
    tags = get_all_tags()
    return jsonify(tags)


@bp.route("/api/stats")
def api_stats():
    """获取系统统计数据的 JSON 接口：论文总数、已分析数、待分析数、并发数等"""
    return jsonify({
        "total_papers": get_paper_count(),
        "analyzed_papers": get_analyzed_count(),
        "unanalyzed_papers": get_unanalyzed_count(),
        "concurrency": get_concurrency(),
        "per_page": get_per_page(),
    })


@bp.route("/api/paper/<arxiv_id>/analysis", methods=["PUT"])
def api_update_paper_analysis(arxiv_id):
    """更新论文分析结果（星级修正、标签、摘要等）"""
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


@bp.route("/api/paper/<arxiv_id>/hide", methods=["POST"])
def api_hide_paper(arxiv_id):
    """隐藏论文（在列表中默认不显示）"""
    try:
        if hide_paper(arxiv_id):
            return jsonify({"status": "ok", "message": "论文已隐藏"})
        return jsonify({"status": "error", "message": "操作失败"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/paper/<arxiv_id>/unhide", methods=["POST"])
def api_unhide_paper(arxiv_id):
    """取消论文隐藏"""
    try:
        if unhide_paper(arxiv_id):
            return jsonify({"status": "ok", "message": "论文已取消隐藏"})
        return jsonify({"status": "error", "message": "操作失败"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/paper/<arxiv_id>", methods=["DELETE"])
def api_delete_paper(arxiv_id):
    """删除论文及其关联的分析数据（CASCADE 删除）"""
    try:
        if delete_paper(arxiv_id):
            return jsonify({"status": "ok", "message": "论文已删除"})
        return jsonify({"status": "error", "message": "删除失败"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/papers/batch-delete", methods=["POST"])
def api_batch_delete_papers():
    """批量删除论文：接收 arxiv_ids 数组，返回实际删除数量"""
    try:
        data = request.get_json()
        arxiv_ids = data.get("arxiv_ids", [])
        if not arxiv_ids:
            return jsonify({"status": "error", "message": "未选择论文"}), 400
        deleted = batch_delete_papers(arxiv_ids)
        return jsonify({"status": "ok", "message": f"已删除 {deleted} 篇论文", "deleted": deleted})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/papers/batch-hide", methods=["POST"])
def api_batch_hide_papers():
    """批量隐藏论文：接收 arxiv_ids 数组，返回实际隐藏数量"""
    try:
        data = request.get_json()
        arxiv_ids = data.get("arxiv_ids", [])
        if not arxiv_ids:
            return jsonify({"status": "error", "message": "未选择论文"}), 400
        hidden = batch_hide_papers(arxiv_ids)
        return jsonify({"status": "ok", "message": f"已隐藏 {hidden} 篇论文", "hidden": hidden})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/papers/batch-analyze", methods=["POST"])
def api_batch_analyze_papers():
    """批量分析论文：对选中的未分析论文执行 AI 分析"""
    try:
        data = request.get_json()
        arxiv_ids = data.get("arxiv_ids", [])
        if not arxiv_ids:
            return jsonify({"status": "error", "message": "未选择论文"}), 400
        papers = get_unanalyzed_papers_by_ids(arxiv_ids)
        if not papers:
            return jsonify({"status": "ok", "message": "所选论文均已分析过", "count": 0})
        concurrency = get_concurrency()
        count = analyze_papers(papers, concurrency=concurrency)
        return jsonify({"status": "ok", "message": f"已分析 {count} 篇论文", "count": count})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/paper/<arxiv_id>/reanalyze", methods=["POST"])
def api_reanalyze_paper(arxiv_id):
    """重新生成单篇论文的深度阅读 Q&A，不覆盖基础分析字段。"""
    try:
        paper = get_paper_by_arxiv_id(arxiv_id)
        if not paper:
            return jsonify({"status": "error", "message": "论文不存在"}), 404

        paper_data = parse_paper_row(paper)

        result_data, result, error = analyze_paper_full(paper_data)
        if result:
            if not result.get("complete", True):
                missing = result.get("missing_questions") or []
                missing_text = "、".join(missing) if missing else "未知部分"
                return jsonify({
                    "status": "warning",
                    "message": f"深度阅读输出仍不完整（缺少 {missing_text}），已保留原有内容",
                    "missing_questions": missing,
                    "continuation_used": bool(result.get("continuation_used")),
                    "finish_reason": result.get("finish_reason", ""),
                })
            update_analysis(paper["id"], {"qa_analysis": result.get("qa_analysis", "")})
            message = "深度阅读生成完成"
            if result.get("continuation_used"):
                message += "（已自动续写补全）"
            return jsonify({
                "status": "ok",
                "message": message,
                "continuation_used": bool(result.get("continuation_used")),
                "finish_reason": result.get("finish_reason", ""),
            })
        return jsonify({"status": "error", "message": f"分析失败: {error}"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/paper/add", methods=["POST"])
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
        update_progress(task_id, {"current": 0, "total": 3, "status": "running", "message": f"正在获取论文 {arxiv_id}..."})

        # 从 arXiv 获取论文元数据
        paper_data = fetch_paper_by_id(arxiv_id)
        if not paper_data:
            update_progress(task_id, {"status": "error", "message": "论文获取失败"})
            return jsonify({"status": "error", "message": "论文获取失败，请检查编号是否正确"}), 404

        # 检查论文是否已有完整基础分析；仅有星级但缺少标签/摘要/简评时继续补齐。
        already_analyzed = get_analysis_by_paper_id(paper_data.get("id"))
        if _has_basic_analysis(already_analyzed):
            update_progress(task_id, {"current": 3, "total": 3, "status": "completed", "message": "论文已存在且已分析"})
            return jsonify({
                "status": "ok",
                "message": f"论文已存在且已分析: {paper_data['title'][:50]}...",
                "arxiv_id": paper_data.get("arxiv_id"),
                "already_exists": True
            })

        update_progress(task_id, {"current": 1, "total": 3, "status": "running", "message": f"正在基础分析论文 {arxiv_id}..."})

        paper_data = parse_paper_row(paper_data)

        # 先用廉价基础分析补齐标签、摘要、价值评价和 AI 初评，再用深度阅读补充 Q&A。
        result_data, basic_result, basic_error = analyze_paper_basic(paper_data)
        if not basic_result:
            update_progress(task_id, {"status": "error", "message": f"基础分析失败: {basic_error}"})
            return jsonify({"status": "ok", "message": f"论文已添加但基础分析失败: {basic_error}", "arxiv_id": paper_data.get("arxiv_id")})

        inserted = insert_analysis(paper_data["id"], basic_result)
        if not inserted:
            update_analysis(paper_data["id"], {
                "rating": basic_result.get("rating", 0),
                "tags": basic_result.get("tags", []),
                "summary_cn": basic_result.get("summary_cn", ""),
                "summary_en": basic_result.get("summary_en", ""),
                "value_comment": basic_result.get("value_comment", ""),
            })
        update_progress(task_id, {"current": 2, "total": 3, "status": "running", "message": f"正在生成深度阅读 {arxiv_id}..."})

        result_data, deep_result, deep_error = analyze_paper_full(paper_data)
        if deep_result:
            if not deep_result.get("complete", True):
                missing = deep_result.get("missing_questions") or []
                missing_text = "、".join(missing) if missing else "未知部分"
                message = f"基础分析完成；深度阅读仍不完整（缺少 {missing_text}），未保存不完整内容"
                update_progress(task_id, {
                    "current": 3,
                    "total": 3,
                    "status": "completed",
                    "message": message,
                })
                return jsonify({
                    "status": "ok",
                    "message": message,
                    "arxiv_id": paper_data.get("arxiv_id"),
                    "rating": basic_result.get("rating", 0),
                    "tags": basic_result.get("tags", []),
                    "deep_reading_incomplete": True,
                    "missing_questions": missing,
                    "continuation_used": bool(deep_result.get("continuation_used")),
                    "finish_reason": deep_result.get("finish_reason", ""),
                })
            update_analysis(paper_data["id"], {"qa_analysis": deep_result.get("qa_analysis", "")})
            update_progress(task_id, {"current": 3, "total": 3, "status": "completed", "message": "添加、基础分析和深度阅读完成"})
            return jsonify({
                "status": "ok",
                "message": f"添加成功: {paper_data['title'][:50]}...",
                "arxiv_id": paper_data.get("arxiv_id"),
                "rating": basic_result.get("rating", 0),
                "tags": basic_result.get("tags", [])
            })

        update_progress(task_id, {"current": 3, "total": 3, "status": "completed", "message": f"基础分析完成，深度阅读失败: {deep_error}"})
        return jsonify({
            "status": "ok",
            "message": f"论文已添加并完成基础分析，但深度阅读失败: {deep_error}",
            "arxiv_id": paper_data.get("arxiv_id"),
            "rating": basic_result.get("rating", 0),
            "tags": basic_result.get("tags", []),
            "deep_reading_error": True,
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/paper/<arxiv_id>/todo", methods=["POST"])
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


@bp.route("/api/paper/<arxiv_id>/todo/status", methods=["GET"])
def api_todo_status(arxiv_id):
    """只读检查论文是否已在阅读清单中。"""
    paper = get_paper_by_arxiv_id(arxiv_id)
    if not paper:
        return jsonify({"status": "error", "message": "论文不存在"}), 404
    return jsonify({
        "status": "ok",
        "in_reading_list": is_in_reading_list(paper["id"]),
    })


@bp.route("/api/paper/<arxiv_id>/todo", methods=["DELETE"])
def api_remove_todo(arxiv_id):
    """将论文从阅读清单中移除"""
    paper = get_paper_by_arxiv_id(arxiv_id)
    if not paper:
        return jsonify({"status": "error", "message": "论文不存在"}), 404
    if remove_from_reading_list(paper["id"]):
        return jsonify({"status": "ok", "message": "已从阅读清单移除"})
    return jsonify({"status": "error", "message": "移除失败"}), 500


@bp.route("/api/paper/<arxiv_id>/todo/read", methods=["POST"])
def api_mark_read(arxiv_id):
    """将论文标记为已读"""
    paper = get_paper_by_arxiv_id(arxiv_id)
    if not paper:
        return jsonify({"status": "error", "message": "论文不存在"}), 404
    if mark_as_read(paper["id"]):
        return jsonify({"status": "ok", "message": "已标记为已读"})
    return jsonify({"status": "error", "message": "操作失败"}), 500


@bp.route("/api/paper/<arxiv_id>/todo/unread", methods=["POST"])
def api_mark_unread(arxiv_id):
    """将论文标记为未读"""
    paper = get_paper_by_arxiv_id(arxiv_id)
    if not paper:
        return jsonify({"status": "error", "message": "论文不存在"}), 404
    if mark_as_unread(paper["id"]):
        return jsonify({"status": "ok", "message": "已标记为未读"})
    return jsonify({"status": "error", "message": "操作失败"}), 500


@bp.route("/api/reading-list", methods=["GET"])
def api_reading_list():
    """获取阅读清单 JSON 接口：支持按状态筛选，返回论文列表和各状态数量"""
    status = request.args.get("status", None)
    papers = get_reading_list(status=status)
    counts = get_reading_list_count()
    return jsonify({"papers": papers, "counts": counts})
