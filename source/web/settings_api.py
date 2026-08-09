"""Flask settings_api routes."""

import hashlib
import hmac
import json
import logging
import os
import re
import time
from datetime import datetime

from flask import (
    Blueprint, Response, current_app, jsonify, redirect, render_template,
    request, session, url_for,
)
from source.storage import get_database_file_sizes
from source.pipeline import configure_daily_job, scheduler
from source.settings import (
    get_ai_tasks,
    get_email_report_config,
    get_fetch_config,
    get_personalization_config,
    get_prompt_profiles,
    get_prompts,
    get_proxy_config,
    get_schedule_config,
    get_webdav_backup_config,
    load_settings,
    save_ai_tasks,
    save_email_report_config,
    save_fetch_config,
    save_personalization_config,
    save_prompt_profile,
    save_prompts,
    save_proxy_config,
    save_schedule_config,
    save_settings,
    save_webdav_backup_config,
    validate_prompt_template,
)
from source.storage import (
    get_ai_usage_summary,
    get_all_categories,
    get_all_dates,
    get_all_tags,
    get_analyzed_count,
    get_average_rating,
    get_paper_count,
    get_unanalyzed_count,
)

logger = logging.getLogger(__name__)
from source.value_coercion import as_int


def _request_bool(data, key, default=False):
    """解析前端传来的布尔字段，保留显式 false。"""
    if key not in data:
        return default
    value = data.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


bp = Blueprint("settings_api", __name__)


@bp.route("/api/settings/concurrency", methods=["POST"])
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


@bp.route("/api/settings/per_page", methods=["POST"])
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


@bp.route("/api/settings/ai-tasks", methods=["GET"])
def api_get_ai_tasks():
    """获取所有 AI 功能的任务级模型路由。"""
    return jsonify({
        "tasks": get_ai_tasks(),
    })


@bp.route("/api/settings/ai-tasks", methods=["POST"])
def api_save_ai_tasks():
    """保存任务级模型路由和参数配置。"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "无效的请求数据"}), 400
        tasks = data.get("tasks", data)
        if save_ai_tasks(tasks):
            return jsonify({"status": "ok", "message": "AI 功能模型路由已保存", "tasks": get_ai_tasks()})
        return jsonify({"status": "error", "message": "保存失败"}), 500
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/settings/ai-usage", methods=["GET"])
def api_get_ai_usage():
    """获取近期 LLM token 用量汇总。"""
    days = request.args.get("days", 7, type=int)
    group_by = request.args.get("group_by", "task")
    return jsonify(get_ai_usage_summary(days=days, group_by=group_by))


@bp.route("/api/settings/personalization", methods=["GET"])
def api_get_personalization():
    """获取个性化推荐配置。"""
    return jsonify(get_personalization_config())


@bp.route("/api/settings/personalization", methods=["POST"])
def api_save_personalization():
    """保存用户研究兴趣；不自动触发历史推荐分重算。"""
    try:
        data = request.get_json() or {}
        if save_personalization_config(data):
            return jsonify({
                "status": "ok",
                "message": "研究兴趣已保存",
                "personalization": get_personalization_config(),
            })
        return jsonify({"status": "error", "message": "保存失败"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/settings/proxy", methods=["GET"])
def api_get_proxy():
    """获取当前代理配置"""
    return jsonify(get_proxy_config())


@bp.route("/api/settings/proxy", methods=["POST"])
def api_save_proxy():
    """保存代理配置（启用状态、HTTP/HTTPS 代理地址）"""
    try:
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


@bp.route("/api/test_proxy", methods=["POST"])
def api_test_proxy():
    """测试代理连接：通过代理访问 arXiv API 验证连通性"""
    try:
        import requests as req
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


@bp.route("/api/settings/webdav-backup", methods=["GET"])
def api_get_webdav_backup():
    """获取 WebDAV 云备份配置；不返回明文密码。"""
    return jsonify(get_webdav_backup_config(mask_password=True))


@bp.route("/api/settings/webdav-backup", methods=["POST"])
def api_save_webdav_backup():
    """保存 WebDAV 云备份配置。密码留空时保留旧密码。"""
    try:
        data = request.get_json() or {}
        config = {
            "enabled": _request_bool(data, "enabled", False),
            "url": data.get("url", ""),
            "username": data.get("username", ""),
            "password": data.get("password", ""),
            "remote_dir": data.get("remote_dir", "arxiv-backups"),
            "history_days": as_int(data.get("history_days", 3), 3),
        }
        if save_webdav_backup_config(config):
            return jsonify({
                "status": "ok",
                "message": "WebDAV 云备份配置已保存",
                "webdav_backup": get_webdav_backup_config(mask_password=True),
            })
        return jsonify({"status": "error", "message": "保存失败"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/settings/email-report", methods=["GET"])
def api_get_email_report():
    """获取每日报告邮件配置；不返回明文密码。"""
    return jsonify(get_email_report_config(mask_password=True))


@bp.route("/api/settings/email-report", methods=["POST"])
def api_save_email_report():
    """保存每日报告邮件配置。密码留空时保留旧密码。"""
    try:
        data = request.get_json() or {}
        config = {
            "enabled": _request_bool(data, "enabled", False),
            "smtp_host": data.get("smtp_host", ""),
            "smtp_port": as_int(data.get("smtp_port", 587), 587),
            "security": data.get("security", "starttls"),
            "username": data.get("username", ""),
            "password": data.get("password", ""),
            "sender": data.get("sender", ""),
            "recipients": data.get("recipients", []),
            "subject_template": data.get("subject_template", ""),
            "site_url": data.get("site_url", ""),
            "important_score_threshold": as_int(data.get("important_score_threshold", 80), 80),
            "overview_limit": as_int(data.get("overview_limit", 20), 20),
        }
        if save_email_report_config(config):
            return jsonify({
                "status": "ok",
                "message": "报告邮件配置已保存",
                "email_report": get_email_report_config(mask_password=True),
            })
        return jsonify({"status": "error", "message": "保存失败"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/settings/fetch", methods=["GET"])
def api_get_fetch_config():
    """获取论文抓取配置（请求间隔、批次天数、批次间隔）"""
    return jsonify(get_fetch_config())


@bp.route("/api/settings/fetch", methods=["POST"])
def api_save_fetch_config():
    """保存论文抓取配置"""
    try:
        data = request.get_json() or {}
        fetch_config = dict(get_fetch_config())
        if "request_delay" in data:
            fetch_config["request_delay"] = float(data["request_delay"])
        if "batch_days" in data:
            fetch_config["batch_days"] = int(data["batch_days"])
        if "batch_delay" in data:
            fetch_config["batch_delay"] = float(data["batch_delay"])
        if save_fetch_config(fetch_config):
            return jsonify({"status": "ok", "message": "抓取配置已保存"})
        return jsonify({"status": "error", "message": "保存失败"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/settings/schedule", methods=["GET"])
def api_get_schedule_config():
    """获取每日定时任务配置。"""
    return jsonify(get_schedule_config())


@bp.route("/api/settings/schedule", methods=["POST"])
def api_save_schedule_config():
    """保存每日定时任务配置，并立即重建 APScheduler job。"""
    try:
        data = request.get_json() or {}
        schedule_config = dict(get_schedule_config())
        if "enabled" in data:
            schedule_config["enabled"] = _request_bool(data, "enabled", schedule_config["enabled"])
        for key in (
            "hour",
            "minute",
            "fetch_days",
            "analyze_limit",
            "fetch_retry_interval_minutes",
            "fetch_max_retries",
        ):
            if key in data:
                schedule_config[key] = as_int(data.get(key, schedule_config[key]), schedule_config[key])
        if "days_of_week" in data:
            schedule_config["days_of_week"] = data.get("days_of_week")
        if not save_schedule_config(schedule_config):
            return jsonify({"status": "error", "message": "保存失败"}), 500
        schedule = get_schedule_config()
        configure_daily_job(schedule)
        if not getattr(scheduler, "running", False):
            scheduler.start()
        status = "已启用" if schedule["enabled"] else "已停用"
        return jsonify({
            "status": "ok",
            "message": f"定时任务{status}，时间 {schedule['hour']:02d}:{schedule['minute']:02d}",
            "schedule": schedule,
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/prompts", methods=["GET"])
def api_get_prompts():
    """获取当前的 system_prompt 和 user_prompt 配置"""
    return jsonify(get_prompts())


@bp.route("/api/prompts", methods=["POST"])
def api_save_prompts():
    """保存 system_prompt 和 user_prompt 配置"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "无效的请求数据"}), 400
        if "profile_key" in data:
            profile_key = data.get("profile_key", "")
            profile = {
                "system": data.get("system", ""),
                "instruction": data.get("instruction", ""),
            }
            ok, message = validate_prompt_template(profile["instruction"], profile_key=profile_key)
            if not ok:
                return jsonify({"status": "error", "message": message}), 400
            if save_prompt_profile(profile_key, profile):
                return jsonify({"status": "ok", "message": "Prompt Profile 已保存", "prompt_profiles": get_prompt_profiles()})
            return jsonify({"status": "error", "message": "未知的 Prompt Profile"}), 400
        prompts = {
            "system_prompt": data.get("system_prompt", ""),
            "user_prompt": data.get("user_prompt", ""),
        }
        ok, message = validate_prompt_template(prompts["user_prompt"], profile_key="deep_reading")
        if not ok:
            return jsonify({"status": "error", "message": message}), 400
        if save_prompts(prompts):
            return jsonify({"status": "ok", "message": "Prompt 已保存"})
        return jsonify({"status": "error", "message": "保存失败"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/db/info", methods=["GET"])
def api_db_info():
    """
    获取数据库概览信息

    返回：论文总数、已分析数、标签数、分类数、日期范围、数据库文件大小、平均评级等。
    """
    import os
    from source.config import DB_PATH

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

    # 计算数据库文件占用（包含 WAL/SHM）
    db_file_sizes = get_database_file_sizes(DB_PATH)
    db_size = db_file_sizes["total"]

    # 计算所有已分析论文的平均评级
    avg_rating = None
    if analyzed > 0:
        try:
            value = get_average_rating()
            if value is not None:
                avg_rating = f"{value:.1f}"
        except Exception as exc:
            logger.warning("Failed to calculate average analysis rating: %s", exc)

    return jsonify({
        "total_papers": total,
        "analyzed_papers": analyzed,
        "unanalyzed_papers": unanalyzed,
        "total_tags": len(tags),
        "total_categories": len(categories),
        "date_range": date_range,
        "db_size": db_size,
        "db_size_bytes": db_file_sizes["total_bytes"],
        "db_files": db_file_sizes["files"],
        "avg_rating": avg_rating,
        "db_path": DB_PATH,
    })
