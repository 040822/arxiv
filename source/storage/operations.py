"""SQLite operations implementation."""

import html as html_module
import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

from .connection import get_connection


def start_task_log(task_name, message=""):
    """记录任务开始执行的日志。
    
    在任务开始时调用，返回日志 ID，后续用于 finish_task_log 更新状态。
    
    参数：
        task_name (str): 任务名称，如 "daily_pipeline", "fetch", "analyze"
        message (str): 任务描述信息
        
    返回：
        int: 日志记录 ID
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO task_logs (task_name, status, message, started_at) VALUES (?, 'running', ?, ?)",
            (task_name, message, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        log_id = cursor.lastrowid

    return log_id


def finish_task_log(log_id, status, message="", detail=""):
    """更新任务日志，记录任务完成状态。
    
    在任务结束时调用，自动计算执行时长。
    
    参数：
        log_id (int): 日志记录 ID（由 start_task_log 返回）
        status (str): 最终状态，如 success/warning/error/skipped/interrupted
        message (str): 结果描述信息
        detail (str): 技术细节（如错误堆栈）
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        # 计算执行时长：当前时间 - 开始时间
        cursor.execute("SELECT started_at FROM task_logs WHERE id = ?", (log_id,))
        row = cursor.fetchone()
        duration = 0
        if row and row["started_at"]:
            started = datetime.strptime(row["started_at"], "%Y-%m-%d %H:%M:%S")
            duration = (datetime.now() - started).total_seconds()

        cursor.execute(
            "UPDATE task_logs SET status = ?, message = ?, detail = ?, finished_at = ?, duration_sec = ? WHERE id = ?",
            (status, message, detail, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), round(duration, 1), log_id)
        )



def initialize_task_log_steps(log_id, steps):
    """为一次流水线执行初始化有序步骤。

    ``steps`` 是 ``(step_key, step_name)`` 二元组列表。重复初始化不会覆盖
    已经开始或完成的步骤，便于调用方安全重试日志初始化。
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.executemany(
            """
            INSERT OR IGNORE INTO task_log_steps
                (task_log_id, step_key, step_name, position, status)
            VALUES (?, ?, ?, ?, 'pending')
            """,
            [
                (log_id, step_key, step_name, position)
                for position, (step_key, step_name) in enumerate(steps, start=1)
            ],
        )



def set_task_log_step_status(log_id, step_key, status, message="", detail=""):
    """更新流水线步骤状态，并维护步骤开始/结束时间和耗时。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT started_at FROM task_log_steps WHERE task_log_id = ? AND step_key = ?",
            (log_id, step_key),
        )
        row = cursor.fetchone()
        if not row:

            return False

        if status == "running":
            cursor.execute(
                """
                UPDATE task_log_steps
                SET status = ?, message = ?, detail = ?,
                    started_at = COALESCE(started_at, ?), finished_at = NULL, duration_sec = NULL
                WHERE task_log_id = ? AND step_key = ?
                """,
                (status, message, detail, now, log_id, step_key),
            )
        elif status == "pending":
            cursor.execute(
                """
                UPDATE task_log_steps SET status = ?, message = ?, detail = ?
                WHERE task_log_id = ? AND step_key = ?
                """,
                (status, message, detail, log_id, step_key),
            )
        else:
            started_at = row["started_at"] or now
            started = datetime.strptime(started_at, "%Y-%m-%d %H:%M:%S")
            duration = round((datetime.now() - started).total_seconds(), 1)
            cursor.execute(
                """
                UPDATE task_log_steps
                SET status = ?, message = ?, detail = ?, started_at = ?,
                    finished_at = ?, duration_sec = ?
                WHERE task_log_id = ? AND step_key = ?
                """,
                (status, message, detail, started_at, now, max(0, duration), log_id, step_key),
            )

    return True


def get_task_logs(task_name=None, limit=50, offset=0):
    """获取任务日志列表，支持按任务名筛选和分页。
    
    参数：
        task_name (str, optional): 按任务名筛选
        limit (int): 每页数量，默认 50
        offset (int): 偏移量，默认 0
        
    返回：
        tuple: (日志列表, 总数) 元组
    """
    with get_connection() as conn:
        cursor = conn.cursor()

        # 构建查询：可选按任务名筛选
        query = "SELECT * FROM task_logs WHERE 1=1"
        params = []

        if task_name:
            query += " AND task_name = ?"
            params.append(task_name)

        # 统计总数（用于分页）
        count_query = query.replace("SELECT *", "SELECT COUNT(*) as cnt")
        cursor.execute(count_query, params)
        total = cursor.fetchone()["cnt"]

        # 分页查询：按开始时间降序
        query += " ORDER BY started_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor.execute(query, params)
        rows = cursor.fetchall()
        results = [dict(row) for row in rows]
        if results:
            log_ids = [row["id"] for row in results]
            placeholders = ",".join("?" for _ in log_ids)
            cursor.execute(
                f"""
                SELECT * FROM task_log_steps
                WHERE task_log_id IN ({placeholders})
                ORDER BY task_log_id, position
                """,
                log_ids,
            )
            steps_by_log = {log_id: [] for log_id in log_ids}
            for step in cursor.fetchall():
                step_data = dict(step)
                steps_by_log.setdefault(step_data["task_log_id"], []).append(step_data)
            for row in results:
                row["steps"] = steps_by_log.get(row["id"], [])


    return results, total


def get_task_stats():
    """获取各任务的执行统计信息。
    
    统计每个任务的：总运行次数、成功次数、错误次数、运行中次数、
    最后运行时间、平均执行时长。
    
    返回：
        list: 统计数据字典列表
    """
    with get_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT task_name,
                   COUNT(*) as total_runs,
                   SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as success_runs,
                   SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as error_runs,
                   SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) as running,
                   MAX(started_at) as last_run,
                   AVG(duration_sec) as avg_duration
            FROM task_logs
            GROUP BY task_name
            ORDER BY last_run DESC
        """)
        rows = cursor.fetchall()

    return [dict(row) for row in rows]


def get_running_tasks():
    """获取当前正在运行的任务列表。
    
    用于检测是否有任务卡住或正在执行。
    
    返回：
        list: 状态为 "running" 的日志记录列表
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM task_logs WHERE status = 'running' ORDER BY started_at DESC")
        rows = cursor.fetchall()

    return [dict(row) for row in rows]


def interrupt_running_task_logs(reason="服务重启，任务已中断"):
    """在应用启动时收口上一次进程遗留的运行中任务。"""
    now_dt = datetime.now()
    now = now_dt.strftime("%Y-%m-%d %H:%M:%S")
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, started_at FROM task_logs WHERE status = 'running'")
        running_logs = cursor.fetchall()
        for row in running_logs:
            duration = 0
            if row["started_at"]:
                started = datetime.strptime(row["started_at"], "%Y-%m-%d %H:%M:%S")
                duration = max(0, round((now_dt - started).total_seconds(), 1))
            cursor.execute(
                """
                UPDATE task_logs
                SET status = 'interrupted', message = ?, finished_at = ?, duration_sec = ?
                WHERE id = ?
                """,
                (reason, now, duration, row["id"]),
            )
            cursor.execute(
                """
                UPDATE task_log_steps
                SET status = 'interrupted', message = ?, finished_at = ?,
                    duration_sec = CASE
                        WHEN started_at IS NULL THEN 0
                        ELSE MAX(0, ROUND((julianday(?) - julianday(started_at)) * 86400, 1))
                    END
                WHERE task_log_id = ? AND status = 'running'
                """,
                (reason, now, now, row["id"]),
            )
            cursor.execute(
                """
                UPDATE task_log_steps
                SET status = 'skipped', message = '父任务中断前未执行',
                    started_at = COALESCE(started_at, ?), finished_at = ?, duration_sec = 0
                WHERE task_log_id = ? AND status = 'pending'
                """,
                (now, now, row["id"]),
            )

    return len(running_logs)


def clear_task_logs(keep_days=30):
    """清理指定天数之前的旧任务日志。
    
    用于定期清理，防止日志表无限增长。
    
    参数：
        keep_days (int): 保留最近多少天的日志，默认 30 天
        
    返回：
        int: 实际删除的日志条数
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM task_logs WHERE started_at < datetime('now', ?)",
            (f"-{keep_days} days",)
        )
        deleted = cursor.rowcount

    return deleted


def _safe_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def record_ai_usage(usage):
    """记录一次 LLM API 调用的 token 用量。"""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO ai_usage_logs (
                task_key, provider_key, provider_name, model, paper_id, arxiv_id,
                prompt_tokens, completion_tokens, total_tokens, cached_tokens, cache_miss_tokens
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            usage.get("task_key", ""),
            usage.get("provider_key", ""),
            usage.get("provider_name", ""),
            usage.get("model", ""),
            usage.get("paper_id"),
            usage.get("arxiv_id", ""),
            _safe_int(usage.get("prompt_tokens")),
            _safe_int(usage.get("completion_tokens")),
            _safe_int(usage.get("total_tokens")),
            _safe_int(usage.get("cached_tokens")),
            _safe_int(usage.get("cache_miss_tokens")),
        ))



def _usage_dates(days):
    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=days - 1)
    return [(start_date + timedelta(days=i)).isoformat() for i in range(days)]


def _empty_usage_point(day):
    return {
        "date": day,
        "call_count": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cached_tokens": 0,
        "cache_miss_tokens": 0,
    }


def get_ai_usage_summary(days=7, group_by="task"):
    """按任务/模型汇总最近 N 天 token 用量，并返回按天分桶的时间序列。"""
    days = max(1, min(365, _safe_int(days) or 7))
    group_by = group_by if group_by in {"task", "model"} else "task"
    dates = _usage_dates(days)
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                task_key,
                provider_key,
                provider_name,
                model,
                COUNT(*) AS call_count,
                SUM(prompt_tokens) AS prompt_tokens,
                SUM(completion_tokens) AS completion_tokens,
                SUM(total_tokens) AS total_tokens,
                SUM(cached_tokens) AS cached_tokens,
                SUM(cache_miss_tokens) AS cache_miss_tokens
            FROM ai_usage_logs
            WHERE created_at >= datetime('now', ?)
            GROUP BY task_key, provider_key, provider_name, model
            ORDER BY total_tokens DESC, call_count DESC
        """, (f"-{days} days",))
        rows = cursor.fetchall()

        if group_by == "model":
            cursor.execute("""
                SELECT
                    date(created_at) AS usage_date,
                    COALESCE(NULLIF(model, ''), 'unknown') AS group_key,
                    COALESCE(NULLIF(model, ''), 'unknown') AS label,
                    COUNT(*) AS call_count,
                    SUM(prompt_tokens) AS prompt_tokens,
                    SUM(completion_tokens) AS completion_tokens,
                    SUM(total_tokens) AS total_tokens,
                    SUM(cached_tokens) AS cached_tokens,
                    SUM(cache_miss_tokens) AS cache_miss_tokens
                FROM ai_usage_logs
                WHERE created_at >= datetime('now', ?)
                GROUP BY usage_date, group_key
                ORDER BY usage_date, group_key
            """, (f"-{days} days",))
        else:
            cursor.execute("""
                SELECT
                    date(created_at) AS usage_date,
                    COALESCE(NULLIF(task_key, ''), 'unknown') AS group_key,
                    COALESCE(NULLIF(task_key, ''), 'unknown') AS label,
                    COUNT(*) AS call_count,
                    SUM(prompt_tokens) AS prompt_tokens,
                    SUM(completion_tokens) AS completion_tokens,
                    SUM(total_tokens) AS total_tokens,
                    SUM(cached_tokens) AS cached_tokens,
                    SUM(cache_miss_tokens) AS cache_miss_tokens
                FROM ai_usage_logs
                WHERE created_at >= datetime('now', ?)
                GROUP BY usage_date, group_key
                ORDER BY usage_date, group_key
            """, (f"-{days} days",))
        series_rows = cursor.fetchall()


    groups = {}
    for row in series_rows:
        item = dict(row)
        key = item["group_key"]
        if key not in groups:
            groups[key] = {
                "key": key,
                "label": item["label"],
                "call_count": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cached_tokens": 0,
                "cache_miss_tokens": 0,
                "points": {day: _empty_usage_point(day) for day in dates},
            }
        point = groups[key]["points"].setdefault(item["usage_date"], _empty_usage_point(item["usage_date"]))
        for field in ("call_count", "prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens", "cache_miss_tokens"):
            value = _safe_int(item.get(field))
            point[field] = value
            groups[key][field] += value

    group_items = []
    for group in groups.values():
        group["points"] = [group["points"].get(day, _empty_usage_point(day)) for day in dates]
        group_items.append(group)
    group_items.sort(key=lambda item: (item["total_tokens"], item["call_count"]), reverse=True)

    totals = {
        "call_count": sum(_safe_int(row["call_count"]) for row in rows),
        "prompt_tokens": sum(_safe_int(row["prompt_tokens"]) for row in rows),
        "completion_tokens": sum(_safe_int(row["completion_tokens"]) for row in rows),
        "total_tokens": sum(_safe_int(row["total_tokens"]) for row in rows),
        "cached_tokens": sum(_safe_int(row["cached_tokens"]) for row in rows),
        "cache_miss_tokens": sum(_safe_int(row["cache_miss_tokens"]) for row in rows),
    }
    return {
        "days": days,
        "group_by": group_by,
        "dates": dates,
        "totals": totals,
        "groups": group_items,
        "items": [dict(row) for row in rows],
    }
