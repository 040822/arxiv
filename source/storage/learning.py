"""SQLite learning implementation."""

import html as html_module
import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

from .connection import get_connection
from .row_mapping import parse_paper_row
from source.value_coercion import as_int


def add_to_reading_list(user_id, paper_id):
    """将论文添加到阅读清单。
    
    参数：
        paper_id (int): 论文 ID
        
    返回：
        bool: 添加成功返回 True，已存在或其他错误返回 False
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT INTO reading_list (user_id, paper_id) VALUES (?, ?)", (user_id, paper_id))
            return True
        except Exception:
            return False


def remove_from_reading_list(user_id, paper_id):
    """从阅读清单中移除论文。
    
    参数：
        paper_id (int): 论文 ID
        
    返回：
        bool: 移除成功返回 True，论文不在清单中返回 False
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM reading_list WHERE user_id = ? AND paper_id = ?", (user_id, paper_id))
        affected = cursor.rowcount

    return affected > 0


def is_in_reading_list(user_id, paper_id):
    """检查论文是否在阅读清单中。
    
    参数：
        paper_id (int): 论文 ID
        
    返回：
        bool: 在清单中返回 True，否则返回 False
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM reading_list WHERE user_id = ? AND paper_id = ?", (user_id, paper_id))
        exists = cursor.fetchone() is not None

    return exists


def mark_as_read(user_id, paper_id):
    """将阅读清单中的论文标记为已读。
    
    同时记录完成时间。
    
    参数：
        paper_id (int): 论文 ID
        
    返回：
        bool: 操作成功返回 True，论文不在清单中返回 False
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE reading_list SET status = 'read', completed_at = ? WHERE user_id = ? AND paper_id = ?",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), user_id, paper_id)
        )
        affected = cursor.rowcount

    return affected > 0


def mark_as_unread(user_id, paper_id):
    """将阅读清单中的论文标记为未读。
    
    清除完成时间，重置为未读状态。
    
    参数：
        paper_id (int): 论文 ID
        
    返回：
        bool: 操作成功返回 True，论文不在清单中返回 False
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE reading_list SET status = 'unread', completed_at = NULL WHERE user_id = ? AND paper_id = ?",
            (user_id, paper_id)
        )
        affected = cursor.rowcount

    return affected > 0


def get_reading_list(user_id, status=None):
    """获取阅读清单，支持按状态筛选。
    
    关联 papers 和 analysis 表，返回完整的论文信息。
    默认按状态排序（未读优先），同状态按添加时间降序。
    
    参数：
        status (str, optional): 按状态筛选，"unread" 或 "read"，None 返回全部
        
    返回：
        list: 论文数据字典列表，额外包含 todo_status, added_at, completed_at 字段
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        if status:
            # 按指定状态筛选
            cursor.execute("""
                SELECT p.*, a.tags, a.summary_cn, a.rating, a.value_comment,
                       a.recommendation_score, a.recommendation_reason, a.recommendation_interest_hash,
                       rl.status as todo_status, rl.added_at, rl.completed_at
                FROM reading_list rl
                JOIN papers p ON rl.paper_id = p.id
                LEFT JOIN analysis a ON p.id = a.paper_id
                WHERE rl.user_id = ? AND rl.status = ?
                ORDER BY rl.added_at DESC
            """, (user_id, status))
        else:
            # 返回全部：未读优先，同状态按添加时间降序
            cursor.execute("""
                SELECT p.*, a.tags, a.summary_cn, a.rating, a.value_comment,
                       a.recommendation_score, a.recommendation_reason, a.recommendation_interest_hash,
                       rl.status as todo_status, rl.added_at, rl.completed_at
                FROM reading_list rl
                JOIN papers p ON rl.paper_id = p.id
                LEFT JOIN analysis a ON p.id = a.paper_id
                WHERE rl.user_id = ?
                ORDER BY
                    CASE WHEN rl.status = 'unread' THEN 0 ELSE 1 END,
                    rl.added_at DESC
            """, (user_id,))
        rows = cursor.fetchall()


    return [parse_paper_row(row) for row in rows]


def get_reading_list_count(user_id):
    """获取阅读清单的统计数据。
    
    返回：
        dict: 包含 total（总数）和 unread（未读数）
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status = 'unread' THEN 1 ELSE 0 END) as unread
            FROM reading_list WHERE user_id = ?
        """, (user_id,))
        row = cursor.fetchone()

    return {"total": row["total"] or 0, "unread": row["unread"] or 0}


def add_paper_chat_message(user_id, paper_id, role, content):
    """保存一条论文自由讨论消息。"""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO paper_chat_messages (user_id, paper_id, role, content)
            VALUES (?, ?, ?, ?)
        """, (user_id, paper_id, str(role or ""), str(content or "")))
        message_id = cursor.lastrowid

    return message_id


def get_paper_chat_messages(user_id, paper_id, limit=200):
    """按时间顺序读取论文自由讨论历史。"""
    limit = max(1, min(1000, as_int(limit) or 200))
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM (
                SELECT id, paper_id, role, content, created_at
                FROM paper_chat_messages
                WHERE user_id = ? AND paper_id = ?
                ORDER BY id DESC
                LIMIT ?
            )
            ORDER BY id ASC
        """, (user_id, paper_id, limit))
        rows = cursor.fetchall()

    return [dict(row) for row in rows]


def create_paper_quiz_session(user_id, paper_id, mode):
    """创建一轮论文问答或苏格拉底练习会话。"""
    mode = str(mode or "quick3")
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO paper_quiz_sessions (user_id, paper_id, mode, status, updated_at)
            VALUES (?, ?, ?, 'active', CURRENT_TIMESTAMP)
        """, (user_id, paper_id, mode))
        session_id = cursor.lastrowid

    return session_id


def get_paper_quiz_session(user_id, session_id, paper_id=None):
    """读取单个练习会话元数据。"""
    with get_connection() as conn:
        cursor = conn.cursor()
        if paper_id is None:
            cursor.execute("SELECT * FROM paper_quiz_sessions WHERE id = ? AND user_id = ?", (session_id, user_id))
        else:
            cursor.execute("SELECT * FROM paper_quiz_sessions WHERE id = ? AND user_id = ? AND paper_id = ?", (session_id, user_id, paper_id))
        row = cursor.fetchone()

    return dict(row) if row else None


def add_paper_quiz_questions(session_id, questions):
    """批量保存练习题。questions 元素包含 question/expected_points。"""
    with get_connection() as conn:
        cursor = conn.cursor()
        saved = []
        for idx, question in enumerate(questions or [], start=1):
            if not isinstance(question, dict):
                continue
            text = str(question.get("question") or "").strip()
            if not text:
                continue
            expected = question.get("expected_points", [])
            if not isinstance(expected, str):
                expected = json.dumps(expected or [], ensure_ascii=False)
            cursor.execute("""
                INSERT INTO paper_quiz_questions (session_id, position, question, expected_points)
                VALUES (?, ?, ?, ?)
            """, (session_id, idx, text, expected))
            saved.append(cursor.lastrowid)
        cursor.execute("UPDATE paper_quiz_sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (session_id,))

    return saved


def add_paper_quiz_question(session_id, position, question, expected_points=""):
    """保存单个练习题或苏格拉底追问。"""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO paper_quiz_questions (session_id, position, question, expected_points)
            VALUES (?, ?, ?, ?)
        """, (session_id, int(position or 1), str(question or ""), str(expected_points or "")))
        cursor.execute("UPDATE paper_quiz_sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (session_id,))
        question_id = cursor.lastrowid

    return question_id


def add_paper_quiz_attempt(question_id, answer_text, score, feedback):
    """保存用户答案和模型反馈。"""
    with get_connection() as conn:
        cursor = conn.cursor()
        feedback_json = feedback if isinstance(feedback, str) else json.dumps(feedback or {}, ensure_ascii=False)
        cursor.execute("""
            INSERT INTO paper_quiz_attempts (question_id, answer_text, score, feedback_json)
            VALUES (?, ?, ?, ?)
        """, (question_id, str(answer_text or ""), max(0, min(5, as_int(score))), feedback_json))
        cursor.execute("""
            UPDATE paper_quiz_sessions
            SET updated_at = CURRENT_TIMESTAMP
            WHERE id = (
                SELECT session_id FROM paper_quiz_questions WHERE id = ?
            )
        """, (question_id,))
        attempt_id = cursor.lastrowid

    return attempt_id


def get_paper_quiz_question(user_id, question_id, paper_id=None):
    """读取单个题目，可选校验所属论文。"""
    with get_connection() as conn:
        cursor = conn.cursor()
        query = """
            SELECT q.*, s.paper_id, s.mode, s.status
            FROM paper_quiz_questions q
            JOIN paper_quiz_sessions s ON q.session_id = s.id
            WHERE q.id = ? AND s.user_id = ?
        """
        params = [question_id, user_id]
        if paper_id is not None:
            query += " AND s.paper_id = ?"
            params.append(paper_id)
        cursor.execute(query, params)
        row = cursor.fetchone()

    return dict(row) if row else None


def get_paper_quiz_session_detail(user_id, session_id, paper_id=None):
    """读取练习会话、题目和每题最新一次作答反馈。"""
    session = get_paper_quiz_session(user_id, session_id, paper_id=paper_id)
    if not session:
        return None

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT q.*, a.id AS attempt_id, a.answer_text, a.score, a.feedback_json, a.created_at AS answered_at
            FROM paper_quiz_questions q
            LEFT JOIN paper_quiz_attempts a ON a.id = (
                SELECT id FROM paper_quiz_attempts
                WHERE question_id = q.id
                ORDER BY id DESC
                LIMIT 1
            )
            WHERE q.session_id = ?
            ORDER BY q.position ASC, q.id ASC
        """, (session_id,))
        rows = cursor.fetchall()


    questions = []
    for row in rows:
        item = dict(row)
        expected = item.get("expected_points")
        if expected:
            try:
                item["expected_points"] = json.loads(expected)
            except json.JSONDecodeError:
                item["expected_points"] = expected
        feedback = item.get("feedback_json")
        if feedback:
            try:
                item["feedback"] = json.loads(feedback)
            except json.JSONDecodeError:
                item["feedback"] = {"feedback": feedback}
        else:
            item["feedback"] = None
        questions.append(item)
    session["questions"] = questions
    return session


def get_latest_paper_quiz_sessions(user_id, paper_id, limit=20):
    """读取某篇论文最近的学习会话列表。"""
    limit = max(1, min(100, as_int(limit) or 20))
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT s.*,
                   COUNT(DISTINCT q.id) AS question_count,
                   COUNT(DISTINCT a.id) AS attempt_count
            FROM paper_quiz_sessions s
            LEFT JOIN paper_quiz_questions q ON s.id = q.session_id
            LEFT JOIN paper_quiz_attempts a ON q.id = a.question_id
            WHERE s.user_id = ? AND s.paper_id = ?
            GROUP BY s.id
            ORDER BY s.updated_at DESC, s.id DESC
            LIMIT ?
        """, (user_id, paper_id, limit))
        rows = cursor.fetchall()

    return [dict(row) for row in rows]
