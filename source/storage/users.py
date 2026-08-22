"""Account, session-revocation, and audit persistence."""

import hashlib
import hmac
import json
import re
import secrets
import sqlite3

from werkzeug.security import check_password_hash, generate_password_hash

from .connection import get_connection


USERNAME_RE = re.compile(r"[a-z0-9._-]{3,32}")
PASSWORD_MIN_LENGTH = 8
_LEGACY_SHA256_RE = re.compile(r"[0-9a-fA-F]{64}")
AUDIT_STRING_MAX_LENGTH = 500
AUDIT_LIST_MAX_ITEMS = 50


def normalize_username(username):
    return str(username or "").strip().lower()


def validate_username(username, *, allow_admin=False):
    normalized = normalize_username(username)
    if not USERNAME_RE.fullmatch(normalized):
        raise ValueError("用户名须为 3–32 位字母、数字、点、下划线或连字符")
    if normalized == "admin" and not allow_admin:
        raise ValueError("admin 是保留用户名")
    return normalized


def validate_password(password):
    value = str(password or "")
    if len(value) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"密码至少需要 {PASSWORD_MIN_LENGTH} 个字符")
    return value


def _user(row):
    if not row:
        return None
    result = dict(row)
    result.pop("password_hash", None)
    return result


def get_user_by_id(user_id, *, include_password=False):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row and include_password else _user(row)


def get_user_by_username(username, *, include_password=False):
    normalized = normalize_username(username)
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (normalized,)
        ).fetchone()
    return dict(row) if row and include_password else _user(row)


def _password_matches(stored, password):
    if _LEGACY_SHA256_RE.fullmatch(str(stored or "")):
        candidate = hashlib.sha256(str(password).encode("utf-8")).hexdigest()
        return hmac.compare_digest(candidate.lower(), stored.lower())
    try:
        return check_password_hash(stored, str(password or ""))
    except (TypeError, ValueError):
        return False


def authenticate_user(username, password):
    """Authenticate without revealing whether the normalized username exists."""
    normalized = normalize_username(username)
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (normalized,)
        ).fetchone()
        stored = row["password_hash"] if row else generate_password_hash("invalid-password", method="scrypt")
        valid = _password_matches(stored, password)
        if not row or not valid or not row["enabled"]:
            return None
        if _LEGACY_SHA256_RE.fullmatch(str(row["password_hash"] or "")):
            conn.execute(
                "UPDATE users SET password_hash = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (generate_password_hash(str(password), method="scrypt"), row["id"]),
            )
        conn.execute(
            "UPDATE users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?", (row["id"],)
        )
    return get_user_by_id(row["id"])


def create_member(username, display_name=""):
    normalized = validate_username(username)
    temporary_password = secrets.token_urlsafe(24)
    with get_connection() as conn:
        try:
            user_id = conn.execute(
                """
                INSERT INTO users (
                    username, display_name, password_hash, role, enabled,
                    must_change_password, session_version
                ) VALUES (?, ?, ?, 'member', 1, 1, 1)
                """,
                (
                    normalized,
                    str(display_name or "").strip() or None,
                    generate_password_hash(temporary_password, method="scrypt"),
                ),
            ).lastrowid
        except sqlite3.IntegrityError as exc:
            raise ValueError("用户名已存在") from exc
    return get_user_by_id(user_id), temporary_password


def change_user_password(user_id, current_password, new_password):
    new_password = validate_password(new_password)
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row or not _password_matches(row["password_hash"], current_password):
            return False
        conn.execute(
            """
            UPDATE users SET password_hash = ?, must_change_password = 0,
                session_version = session_version + 1, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (generate_password_hash(new_password, method="scrypt"), user_id),
        )
    return True


def reset_member_password(user_id):
    temporary_password = secrets.token_urlsafe(24)
    with get_connection() as conn:
        row = conn.execute("SELECT role FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row or row["role"] != "member":
            return None
        conn.execute(
            """
            UPDATE users SET password_hash = ?, must_change_password = 1,
                session_version = session_version + 1, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (generate_password_hash(temporary_password, method="scrypt"), user_id),
        )
    return temporary_password


def reset_admin_password_local():
    """Generate a one-time admin password for the local recovery script."""
    temporary_password = secrets.token_urlsafe(24)
    with get_connection() as conn:
        row = conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()
        if not row:
            raise RuntimeError("admin 账号不存在，请先完成数据库迁移")
        conn.execute(
            """
            UPDATE users SET password_hash = ?, must_change_password = 1,
                session_version = session_version + 1, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (generate_password_hash(temporary_password, method="scrypt"), row["id"]),
        )
    return temporary_password


def set_member_enabled(user_id, enabled):
    with get_connection() as conn:
        row = conn.execute("SELECT role FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row or row["role"] != "member":
            return False
        conn.execute(
            """
            UPDATE users SET enabled = ?, session_version = session_version + 1,
                updated_at = CURRENT_TIMESTAMP WHERE id = ?
            """,
            (int(bool(enabled)), user_id),
        )
    return True


def delete_member(user_id):
    with get_connection() as conn:
        row = conn.execute("SELECT role FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row or row["role"] != "member":
            return False
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return True


def list_users_with_summaries():
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT u.id, u.username, u.display_name, u.role, u.enabled,
                   u.must_change_password, u.session_version, u.last_login_at,
                   u.created_at, u.updated_at,
                   (SELECT COUNT(*) FROM reading_list r WHERE r.user_id = u.id) AS reading_count,
                   (SELECT COUNT(*) FROM paper_chat_messages c WHERE c.user_id = u.id) AS chat_count,
                   (SELECT COUNT(*) FROM paper_quiz_sessions q WHERE q.user_id = u.id) AS quiz_count,
                   (SELECT COALESCE(SUM(total_tokens), 0) FROM ai_usage_logs a WHERE a.user_id = u.id) AS total_tokens
            FROM users u ORDER BY CASE WHEN u.role = 'admin' THEN 0 ELSE 1 END, u.username
        """).fetchall()
    return [dict(row) for row in rows]


def _truncate_audit_scalar(value):
    """Return a JSON-safe scalar; long strings are truncated to a fixed length."""
    if isinstance(value, str):
        return value[:AUDIT_STRING_MAX_LENGTH]
    return value


def record_audit_event(actor, action, target_type="", target_id="", metadata=None):
    """Persist only caller-supplied safe metadata; never accept secret-shaped keys."""
    safe = {}
    for key, value in dict(metadata or {}).items():
        lowered = str(key).lower()
        if any(term in lowered for term in ("password", "secret", "api_key", "chat", "answer", "prompt", "qa_analysis")):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[str(key)] = _truncate_audit_scalar(value)
        elif isinstance(value, list):
            safe[str(key)] = [
                _truncate_audit_scalar(item)
                for item in value
                if isinstance(item, (str, int, float, bool)) or item is None
            ][:AUDIT_LIST_MAX_ITEMS]
    actor = dict(actor or {})
    with get_connection() as conn:
        return conn.execute(
            """
            INSERT INTO audit_events (
                actor_user_id, actor_username, action, target_type, target_id, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                actor.get("id"), actor.get("username") or "system", str(action),
                str(target_type or ""), str(target_id or ""),
                json.dumps(safe, ensure_ascii=False),
            ),
        ).lastrowid


def get_audit_events(limit=50, offset=0):
    limit = max(1, min(200, int(limit or 50)))
    offset = max(0, int(offset or 0))
    with get_connection() as conn:
        total = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM audit_events ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
        except ValueError:
            item["metadata"] = {}
        result.append(item)
    return result, total
