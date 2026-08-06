"""Flask learning_api routes."""

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

from source.storage import (
    add_paper_chat_message,
    add_paper_quiz_attempt,
    add_paper_quiz_question,
    add_paper_quiz_questions,
    create_paper_quiz_session,
    get_paper_by_key,
    get_paper_chat_messages,
    get_paper_quiz_question,
    get_paper_quiz_session_detail,
)
from .pages import _prepare_paper_for_view
from .progress import get_progress, update_progress

logger = logging.getLogger(__name__)


bp = Blueprint("learning_api", __name__)


def _get_learning_paper_or_response(arxiv_id):
    paper = get_paper_by_key(arxiv_id)
    if not paper:
        return None, (jsonify({"status": "error", "message": "论文不存在"}), 404)
    return _prepare_paper_for_view(paper), None


@bp.route("/api/paper/<arxiv_id>/chat/messages", methods=["GET"])
def api_paper_chat_messages(arxiv_id):
    """读取单篇论文自由讨论历史。"""
    paper, error_response = _get_learning_paper_or_response(arxiv_id)
    if error_response:
        return error_response
    return jsonify({
        "status": "ok",
        "messages": get_paper_chat_messages(paper["id"]),
    })


@bp.route("/api/paper/<arxiv_id>/chat/messages", methods=["POST"])
def api_paper_chat_send(arxiv_id):
    """发送一条论文讨论消息，并保存模型回复。"""
    paper, error_response = _get_learning_paper_or_response(arxiv_id)
    if error_response:
        return error_response
    data = request.get_json() or {}
    message = str(data.get("message") or "").strip()
    if not message:
        return jsonify({"status": "error", "message": "消息不能为空"}), 400

    history = get_paper_chat_messages(paper["id"], limit=12)
    reply, error, meta = chat_about_paper(paper, message, history=history)
    if error:
        return jsonify({"status": "error", "message": f"模型对话失败: {error}", "meta": meta}), 500

    add_paper_chat_message(paper["id"], "user", message)
    add_paper_chat_message(paper["id"], "assistant", reply)
    return jsonify({
        "status": "ok",
        "reply": reply,
        "messages": get_paper_chat_messages(paper["id"]),
        "meta": meta,
    })


@bp.route("/api/paper/<arxiv_id>/quiz/sessions", methods=["POST"])
def api_create_quiz_session(arxiv_id):
    """创建 3 题或 6 题主动问答练习。"""
    paper, error_response = _get_learning_paper_or_response(arxiv_id)
    if error_response:
        return error_response
    data = request.get_json() or {}
    mode = data.get("mode", "quick3")
    if mode not in {"quick3", "standard6"}:
        return jsonify({"status": "error", "message": "无效的练习模式"}), 400

    questions, error, meta = generate_paper_quiz(paper, mode=mode)
    if error:
        return jsonify({"status": "error", "message": f"生成题目失败: {error}", "meta": meta}), 500

    session_id = create_paper_quiz_session(paper["id"], mode)
    add_paper_quiz_questions(session_id, questions)
    return jsonify({
        "status": "ok",
        "session": get_paper_quiz_session_detail(session_id, paper_id=paper["id"]),
        "meta": meta,
    })


@bp.route("/api/paper/<arxiv_id>/quiz/sessions/<int:session_id>", methods=["GET"])
def api_get_quiz_session(arxiv_id, session_id):
    """读取练习会话详情。"""
    paper, error_response = _get_learning_paper_or_response(arxiv_id)
    if error_response:
        return error_response
    session_detail = get_paper_quiz_session_detail(session_id, paper_id=paper["id"])
    if not session_detail:
        return jsonify({"status": "error", "message": "练习会话不存在"}), 404
    return jsonify({"status": "ok", "session": session_detail})


@bp.route("/api/paper/<arxiv_id>/quiz/questions/<int:question_id>/answer", methods=["POST"])
def api_answer_quiz_question(arxiv_id, question_id):
    """提交单题答案并返回评分反馈。"""
    paper, error_response = _get_learning_paper_or_response(arxiv_id)
    if error_response:
        return error_response
    question = get_paper_quiz_question(question_id, paper_id=paper["id"])
    if not question:
        return jsonify({"status": "error", "message": "题目不存在"}), 404
    data = request.get_json() or {}
    answer = str(data.get("answer") or "").strip()
    if not answer:
        return jsonify({"status": "error", "message": "答案不能为空"}), 400

    feedback, error, meta = grade_quiz_answer(paper, question, answer)
    if error:
        return jsonify({"status": "error", "message": f"评分失败: {error}", "meta": meta}), 500
    add_paper_quiz_attempt(question_id, answer, feedback.get("score", 0), feedback)
    return jsonify({
        "status": "ok",
        "feedback": feedback,
        "session": get_paper_quiz_session_detail(question["session_id"], paper_id=paper["id"]),
        "meta": meta,
    })


def _socratic_history_from_session(session_detail):
    history = []
    for q in (session_detail or {}).get("questions", []):
        item = {"question": q.get("question", "")}
        if q.get("answer_text"):
            item["answer"] = q.get("answer_text", "")
        if q.get("feedback"):
            item["feedback"] = q.get("feedback")
        history.append(item)
    return history


@bp.route("/api/paper/<arxiv_id>/socratic/sessions", methods=["POST"])
def api_create_socratic_session(arxiv_id):
    """创建独立苏格拉底追问会话，并返回第一问。"""
    paper, error_response = _get_learning_paper_or_response(arxiv_id)
    if error_response:
        return error_response

    result, error, meta = socratic_reply(paper, session_history=[], user_answer=None)
    if error:
        return jsonify({"status": "error", "message": f"创建追问失败: {error}", "meta": meta}), 500

    session_id = create_paper_quiz_session(paper["id"], "socratic")
    add_paper_quiz_question(session_id, 1, result.get("next_question", ""), "socratic")
    return jsonify({
        "status": "ok",
        "session": get_paper_quiz_session_detail(session_id, paper_id=paper["id"]),
        "feedback": result,
        "meta": meta,
    })


@bp.route("/api/paper/<arxiv_id>/socratic/sessions/<int:session_id>/reply", methods=["POST"])
def api_reply_socratic_session(arxiv_id, session_id):
    """提交苏格拉底会话回答，并返回反馈和下一问。"""
    paper, error_response = _get_learning_paper_or_response(arxiv_id)
    if error_response:
        return error_response
    session_detail = get_paper_quiz_session_detail(session_id, paper_id=paper["id"])
    if not session_detail or session_detail.get("mode") != "socratic":
        return jsonify({"status": "error", "message": "苏格拉底会话不存在"}), 404
    questions = session_detail.get("questions", [])
    if not questions:
        return jsonify({"status": "error", "message": "会话缺少追问"}), 400
    data = request.get_json() or {}
    answer = str(data.get("answer") or "").strip()
    if not answer:
        return jsonify({"status": "error", "message": "答案不能为空"}), 400

    current_question = questions[-1]
    history = _socratic_history_from_session(session_detail)
    result, error, meta = socratic_reply(paper, session_history=history, user_answer=answer)
    if error:
        return jsonify({"status": "error", "message": f"追问失败: {error}", "meta": meta}), 500

    add_paper_quiz_attempt(current_question["id"], answer, result.get("score", 0), result)
    add_paper_quiz_question(
        session_id,
        len(questions) + 1,
        result.get("next_question", ""),
        "socratic",
    )
    return jsonify({
        "status": "ok",
        "feedback": result,
        "session": get_paper_quiz_session_detail(session_id, paper_id=paper["id"]),
        "meta": meta,
    })
