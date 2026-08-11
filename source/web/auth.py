"""Flask auth routes."""

import hashlib
import hmac
import json
import logging
import os
import re
import sqlite3
import threading
import time
from datetime import datetime

from flask import (
    Blueprint, Response, current_app, jsonify, redirect, render_template,
    request, session, url_for,
)
from source.settings import (
    ADMIN_PASSWORD_MIN_LENGTH,
    admin_password_change_recommended,
    get_admin_password,
    has_admin_password,
    set_admin_password,
    verify_admin_password,
)


logger = logging.getLogger(__name__)


bp = Blueprint("auth", __name__)

PUBLIC_GET_ENDPOINTS = {
    "index", "about_page", "vision_page", "paper_detail", "search", "browse",
    "reports_page", "report_detail_page", "api_papers", "api_tags", "api_stats",
    "login_page", "api_auth_status",
}
AUTH_ENDPOINTS = {"login_page", "api_auth_login", "api_auth_logout", "api_auth_status"}
LOGIN_FAILURE_LIMIT = 5
LOGIN_FAILURE_WINDOW_SECONDS = 15 * 60
_login_failures = {}
_login_failure_lock = threading.Lock()


def _admin_auth_token(admin_password_hash=None):
    """生成绑定当前管理密码版本的 session token。"""
    password_hash = admin_password_hash if admin_password_hash is not None else get_admin_password()
    if not password_hash:
        return ""
    return hmac.new(
        str(current_app.secret_key).encode("utf-8"),
        password_hash.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _clear_admin_session():
    """清除当前请求里的管理登录状态。"""
    session.permanent = False
    session.pop("admin_authenticated", None)
    session.pop("admin_auth_token", None)


def _mark_admin_authenticated():
    """写入 180 天持久管理登录状态。"""
    session.permanent = True
    session["admin_authenticated"] = True
    session["admin_auth_token"] = _admin_auth_token()


def is_authenticated():
    """检查 session 是否绑定当前管理密码版本；缺少凭据时失败关闭。"""
    if not has_admin_password():
        return False
    if not session.get("admin_authenticated"):
        return False
    expected = _admin_auth_token()
    actual = session.get("admin_auth_token", "")
    return bool(expected and actual and hmac.compare_digest(str(actual), expected))


def _json_auth_error():
    return jsonify({
        "status": "error",
        "message": "需要登录后才能执行该操作",
        "auth_required": True,
    }), 401


def _safe_next_url(next_url):
    """只允许站内相对路径作为登录后的跳转目标。"""
    if not next_url or not next_url.startswith("/") or next_url.startswith("//"):
        return "/settings"
    return next_url


def _login_client_key():
    return str(getattr(request, "remote_addr", None) or "unknown")


def _prune_login_failures(now):
    for key, timestamps in list(_login_failures.items()):
        active = [
            timestamp
            for timestamp in timestamps
            if now - timestamp < LOGIN_FAILURE_WINDOW_SECONDS
        ]
        if active:
            _login_failures[key] = active
        else:
            _login_failures.pop(key, None)


def _login_retry_after(now=None):
    now = time.monotonic() if now is None else now
    key = _login_client_key()
    with _login_failure_lock:
        _prune_login_failures(now)
        failures = _login_failures.get(key, [])
        if len(failures) < LOGIN_FAILURE_LIMIT:
            return 0
        return max(1, int(LOGIN_FAILURE_WINDOW_SECONDS - (now - failures[0]) + 0.999))


def _record_login_failure(now=None):
    now = time.monotonic() if now is None else now
    key = _login_client_key()
    with _login_failure_lock:
        _prune_login_failures(now)
        failures = list(_login_failures.get(key, []))
        failures.append(now)
        _login_failures[key] = failures


def _clear_login_failures():
    with _login_failure_lock:
        _login_failures.pop(_login_client_key(), None)


@bp.before_app_request
def require_auth_for_protected_routes():
    """保护设置页、任务页、所有写接口和敏感配置读取接口。"""
    endpoint = (request.endpoint or "").rsplit(".", 1)[-1] or None
    if endpoint in (None, "static") or endpoint in AUTH_ENDPOINTS:
        return None
    if request.method == "GET" and endpoint in PUBLIC_GET_ENDPOINTS:
        return None
    if is_authenticated():
        return None
    if request.path.startswith("/api/"):
        return _json_auth_error()
    return redirect(url_for("auth.login_page", next=request.full_path if request.query_string else request.path))


@bp.app_context_processor
def auth_processor():
    """向模板注入统一认证状态。"""
    authenticated = is_authenticated()
    return {
        "auth_enabled": has_admin_password(),
        "is_authenticated": authenticated,
        "password_change_recommended": (
            authenticated and admin_password_change_recommended()
        ),
    }


@bp.app_context_processor
def inject_now():
    """向所有模板注入当前时间变量 now，用于页面显示"""
    return {"now": datetime.now().strftime("%Y-%m-%d %H:%M")}


@bp.app_context_processor
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


@bp.route("/login")
def login_page():
    """登录页。已登录时直接返回目标页面。"""
    next_url = _safe_next_url(request.args.get("next") or "/settings")
    if is_authenticated():
        return redirect(next_url)
    return render_template("login.html", next_url=next_url)


@bp.route("/api/auth/status", methods=["GET"])
def api_auth_status():
    """获取当前认证状态。"""
    return jsonify({
        "password_enabled": has_admin_password(),
        "authenticated": is_authenticated(),
        "password_change_recommended": admin_password_change_recommended(),
    })


@bp.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    """使用管理密码登录。"""
    retry_after = _login_retry_after()
    if retry_after:
        return jsonify({
            "status": "error",
            "message": "登录失败次数过多，请稍后再试",
            "retry_after": retry_after,
        }), 429, {"Retry-After": str(retry_after)}
    data = request.get_json() or {}
    password = data.get("password", "")
    if verify_admin_password(password):
        _clear_login_failures()
        _mark_admin_authenticated()
        return jsonify({
            "status": "ok",
            "message": "登录成功",
            "password_change_recommended": admin_password_change_recommended(),
        })
    _record_login_failure()
    return jsonify({"status": "error", "message": "密码错误"}), 403


@bp.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    """退出登录。"""
    _clear_admin_session()
    return jsonify({"status": "ok", "message": "已退出登录"})


@bp.route("/api/admin/password", methods=["POST"])
def api_set_admin_password():
    """
    验证当前密码后修改管理密码。
    """
    try:
        data = request.get_json()
        current = data.get("current_password", "")
        new_pwd = data.get("new_password", "")

        if not verify_admin_password(current):
            return jsonify({"status": "error", "message": "当前密码错误"}), 403
        if len(str(new_pwd or "")) < ADMIN_PASSWORD_MIN_LENGTH:
            return jsonify({
                "status": "error",
                "message": f"新密码至少需要 {ADMIN_PASSWORD_MIN_LENGTH} 个字符",
            }), 400

        set_admin_password(new_pwd)
        _clear_admin_session()
        _mark_admin_authenticated()
        return jsonify({"status": "ok", "message": "管理密码已修改"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
