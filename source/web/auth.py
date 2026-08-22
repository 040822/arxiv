"""Invite-only authentication, authorization, and CSRF policy."""

import secrets
import threading
import time
from datetime import datetime

from flask import Blueprint, g, jsonify, redirect, render_template, request, session, url_for

from source.storage import (
    PASSWORD_MIN_LENGTH,
    authenticate_user,
    change_user_password,
    create_member,
    delete_member,
    get_audit_events,
    get_user_by_id,
    list_users_with_summaries,
    normalize_username,
    record_audit_event,
    reset_member_password,
    set_member_enabled,
)
from source.analysis.usage import set_usage_user_id


bp = Blueprint("auth", __name__)

PUBLIC_GET_ENDPOINTS = {
    "index", "about_page", "vision_page", "paper_detail", "search", "browse",
    "reports_page", "report_detail_page", "api_papers", "api_tags", "api_stats",
    "login_page", "api_auth_status",
}
PUBLIC_ENDPOINTS = {"login_page", "api_auth_status", "api_auth_login"}
MEMBER_ENDPOINTS = {
    "account_password_page", "api_account_password", "api_auth_logout",
    "paper_chat_page", "reading_list_page", "api_update_paper_analysis",
    "api_reanalyze_paper", "api_add_todo", "api_todo_status",
    "api_remove_todo", "api_mark_read", "api_mark_unread", "api_reading_list",
    "api_paper_chat_messages", "api_paper_chat_send", "api_create_quiz_session",
    "api_get_quiz_session", "api_answer_quiz_question", "api_create_socratic_session",
    "api_reply_socratic_session", "api_preview_paper_import", "api_confirm_paper_import",
    "api_progress", "tasks_page",
}
ADMIN_ENDPOINTS = {
    "api_add_paper", "api_add_provider", "api_analyze", "api_attach_paper_pdf", "api_audit_events",
    "api_batch_analyze_papers", "api_batch_delete_papers", "api_batch_hide_papers",
    "api_clear_logs", "api_db_info", "api_delete_paper", "api_delete_provider",
    "api_fetch", "api_generate", "api_get_ai_tasks", "api_get_ai_usage",
    "api_get_email_report", "api_get_fetch_config", "api_get_personalization",
    "api_get_prompts", "api_get_proxy", "api_get_schedule_config", "api_get_webdav_backup",
    "api_hide_paper", "api_list_providers", "api_provider_models", "api_provider_presets",
    "api_recalculate_recommendations", "api_run", "api_run_webdav_backup",
    "api_save_ai_tasks", "api_save_concurrency", "api_save_email_report",
    "api_save_fetch_config", "api_save_per_page", "api_save_personalization",
    "api_save_prompts", "api_save_proxy", "api_save_schedule_config", "api_save_webdav_backup",
    "api_scheduled_tasks", "api_set_admin_password", "api_task_logs", "api_task_stats",
    "api_test_ai_task_route", "api_test_email_report", "api_test_proxy", "api_unhide_paper",
    "api_update_provider", "api_user_delete", "api_user_enabled", "api_user_reset_password",
    "api_users_create", "api_users_list", "settings_page",
}
PASSWORD_CHANGE_ENDPOINTS = {
    "account_password_page", "api_account_password", "api_auth_logout", "api_auth_status",
}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
LOGIN_FAILURE_LIMIT = 5
LOGIN_FAILURE_WINDOW_SECONDS = 15 * 60
_login_failures = {}
_login_failure_lock = threading.Lock()


def _endpoint_name():
    return (request.endpoint or "").rsplit(".", 1)[-1] or None


def route_policy(endpoint=None, method=None):
    """Return the fail-closed policy for every non-static route."""
    endpoint = endpoint or _endpoint_name()
    method = method or request.method
    if endpoint in (None, "static"):
        return "public"
    if endpoint in PUBLIC_ENDPOINTS or (method == "GET" and endpoint in PUBLIC_GET_ENDPOINTS):
        return "public"
    if endpoint in MEMBER_ENDPOINTS:
        return "member"
    return "admin"


def _clear_session():
    session.clear()
    session.permanent = False


def _csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def _submitted_csrf_token():
    header = request.headers.get("X-CSRF-Token", "") if hasattr(request, "headers") else ""
    if header:
        return header
    data = request.get_json(silent=True) if getattr(request, "is_json", False) else None
    if isinstance(data, dict) and data.get("csrf_token"):
        return str(data["csrf_token"])
    return str(request.form.get("csrf_token", "")) if hasattr(request, "form") else ""


def _csrf_valid():
    expected = session.get("csrf_token", "")
    actual = _submitted_csrf_token()
    return bool(expected and actual and secrets.compare_digest(str(expected), str(actual)))


def current_user():
    cached = getattr(g, "current_user", None)
    if cached is not None:
        return cached
    user_id = session.get("user_id")
    version = session.get("session_version")
    user = get_user_by_id(user_id) if user_id else None
    if not user or not user.get("enabled") or int(user.get("session_version", 0)) != int(version or -1):
        if user_id:
            _clear_session()
        g.current_user = None
        return None
    g.current_user = user
    return user


def is_authenticated():
    return current_user() is not None


def is_admin():
    user = current_user()
    return bool(user and user.get("role") == "admin")


def _json_auth_error(status=401, message="需要登录后才能执行该操作"):
    return jsonify({
        "status": "error", "message": message, "auth_required": status == 401,
    }), status


def _safe_next_url(next_url):
    if not next_url or not next_url.startswith("/") or next_url.startswith("//"):
        return "/"
    return next_url


def _client_ip():
    return str(getattr(request, "remote_addr", None) or "unknown")


def _failure_keys(username):
    return (("ip", _client_ip()), ("username", normalize_username(username)))


def _prune_login_failures(now):
    for key, timestamps in list(_login_failures.items()):
        active = [stamp for stamp in timestamps if now - stamp < LOGIN_FAILURE_WINDOW_SECONDS]
        if active:
            _login_failures[key] = active
        else:
            _login_failures.pop(key, None)


def _login_retry_after(username, now=None):
    now = time.monotonic() if now is None else now
    with _login_failure_lock:
        _prune_login_failures(now)
        waits = []
        for key in _failure_keys(username):
            failures = _login_failures.get(key, [])
            if len(failures) >= LOGIN_FAILURE_LIMIT:
                waits.append(LOGIN_FAILURE_WINDOW_SECONDS - (now - failures[0]))
        return max(1, int(max(waits) + 0.999)) if waits else 0


def _record_login_failure(username, now=None):
    now = time.monotonic() if now is None else now
    with _login_failure_lock:
        _prune_login_failures(now)
        for key in _failure_keys(username):
            _login_failures.setdefault(key, []).append(now)


def _clear_login_failures(username):
    with _login_failure_lock:
        for key in _failure_keys(username):
            _login_failures.pop(key, None)


def _sign_in(user):
    csrf = session.get("csrf_token") or secrets.token_urlsafe(32)
    session.clear()
    session.permanent = True
    session["user_id"] = user["id"]
    session["session_version"] = user["session_version"]
    session["csrf_token"] = csrf


@bp.before_app_request
def enforce_request_policy():
    endpoint = _endpoint_name()
    if endpoint in (None, "static"):
        return None
    user = current_user()
    set_usage_user_id(user.get("id") if user else None)
    policy = route_policy(endpoint, request.method)
    if policy != "public" and not user:
        if request.path.startswith("/api/"):
            return _json_auth_error()
        return redirect(url_for("auth.login_page", next=request.full_path if request.query_string else request.path))
    if policy == "admin" and user.get("role") != "admin":
        if request.path.startswith("/api/"):
            return _json_auth_error(403, "权限不足")
        return jsonify({"status": "error", "message": "权限不足"}), 403
    if user and user.get("must_change_password") and endpoint not in PASSWORD_CHANGE_ENDPOINTS:
        if request.path.startswith("/api/"):
            return jsonify({
                "status": "error", "message": "首次登录必须先修改密码",
                "password_change_required": True,
            }), 403
        return redirect(url_for("auth.account_password_page"))
    if request.method not in SAFE_METHODS and not _csrf_valid():
        return jsonify({"status": "error", "message": "CSRF token 无效"}), 403
    return None


@bp.app_context_processor
def auth_processor():
    user = current_user()
    return {
        "auth_enabled": True,
        "is_authenticated": bool(user),
        "is_admin": bool(user and user.get("role") == "admin"),
        "current_user": user,
        "password_change_recommended": bool(user and user.get("must_change_password")),
        "password_min_length": PASSWORD_MIN_LENGTH,
        "csrf_token": _csrf_token(),
    }


@bp.app_context_processor
def inject_now():
    return {"now": datetime.now().strftime("%Y-%m-%d %H:%M")}


@bp.app_context_processor
def utility_processor():
    def _remove_param(key):
        args = request.args.copy()
        args.pop(key, None)
        args.pop("page", None)
        return "&".join(f"{k}={v}" for k, v in args.items() if v)

    def _build_query():
        args = request.args.copy()
        args.pop("page", None)
        return "&".join(f"{k}={v}" for k, v in args.items() if v)

    return {"_remove_param": _remove_param, "_build_query": _build_query}


@bp.route("/login")
def login_page():
    next_url = _safe_next_url(request.args.get("next") or "/")
    if is_authenticated():
        return redirect(next_url)
    return render_template("login.html", next_url=next_url, csrf_token=_csrf_token())


@bp.route("/account/password")
def account_password_page():
    return render_template("account_password.html")


@bp.route("/api/auth/status", methods=["GET"])
def api_auth_status():
    user = current_user()
    return jsonify({
        "authenticated": bool(user), "user": user, "role": user.get("role") if user else None,
        "password_change_required": bool(user and user.get("must_change_password")),
        "csrf_token": _csrf_token(),
    })


@bp.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    data = request.get_json() or {}
    username = normalize_username(data.get("username"))
    retry_after = _login_retry_after(username)
    if retry_after:
        return jsonify({
            "status": "error", "message": "登录失败次数过多，请稍后再试",
            "retry_after": retry_after,
        }), 429, {"Retry-After": str(retry_after)}
    user = authenticate_user(username, data.get("password", ""))
    if not user:
        _record_login_failure(username)
        return jsonify({"status": "error", "message": "用户名或密码错误"}), 403
    _clear_login_failures(username)
    _sign_in(user)
    return jsonify({
        "status": "ok", "message": "登录成功", "user": user,
        "password_change_required": bool(user.get("must_change_password")),
        "csrf_token": session["csrf_token"],
    })


@bp.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    _clear_session()
    return jsonify({"status": "ok", "message": "已退出登录"})


@bp.route("/api/account/password", methods=["POST"])
def api_account_password():
    user = current_user()
    data = request.get_json() or {}
    try:
        changed = change_user_password(
            user["id"], data.get("current_password", ""), data.get("new_password", "")
        )
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    if not changed:
        return jsonify({"status": "error", "message": "当前密码错误"}), 403
    refreshed = get_user_by_id(user["id"])
    _sign_in(refreshed)
    record_audit_event(refreshed, "account.password_changed", "user", refreshed["id"])
    return jsonify({"status": "ok", "message": "密码已修改", "csrf_token": session["csrf_token"]})


@bp.route("/api/admin/password", methods=["POST"])
def api_set_admin_password():
    return api_account_password()


@bp.route("/api/users", methods=["GET"])
def api_users_list():
    return jsonify({"users": list_users_with_summaries()})


@bp.route("/api/users", methods=["POST"])
def api_users_create():
    data = request.get_json() or {}
    try:
        user, temporary_password = create_member(data.get("username"), data.get("display_name", ""))
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    record_audit_event(current_user(), "user.created", "user", user["id"], {"username": user["username"]})
    return jsonify({"status": "ok", "user": user, "temporary_password": temporary_password}), 201


@bp.route("/api/users/<int:user_id>/enabled", methods=["POST"])
def api_user_enabled(user_id):
    data = request.get_json() or {}
    if not set_member_enabled(user_id, bool(data.get("enabled"))):
        return jsonify({"status": "error", "message": "成员不存在"}), 404
    record_audit_event(current_user(), "user.enabled_changed", "user", user_id, {"enabled": bool(data.get("enabled"))})
    return jsonify({"status": "ok"})


@bp.route("/api/users/<int:user_id>/reset-password", methods=["POST"])
def api_user_reset_password(user_id):
    temporary_password = reset_member_password(user_id)
    if not temporary_password:
        return jsonify({"status": "error", "message": "成员不存在"}), 404
    record_audit_event(current_user(), "user.password_reset", "user", user_id)
    return jsonify({"status": "ok", "temporary_password": temporary_password})


@bp.route("/api/users/<int:user_id>", methods=["DELETE"])
def api_user_delete(user_id):
    target = get_user_by_id(user_id)
    if not target or not delete_member(user_id):
        return jsonify({"status": "error", "message": "成员不存在"}), 404
    record_audit_event(current_user(), "user.deleted", "user", user_id, {"username": target["username"]})
    return jsonify({"status": "ok"})


@bp.route("/api/audit-events", methods=["GET"])
def api_audit_events():
    page = max(1, request.args.get("page", 1, type=int))
    per_page = max(1, min(100, request.args.get("per_page", 50, type=int)))
    events, total = get_audit_events(per_page, (page - 1) * per_page)
    return jsonify({"events": events, "total": total, "page": page, "per_page": per_page})
