"""v4 migration for invite-only accounts and private-data ownership."""

import json
import os
import secrets

from werkzeug.security import generate_password_hash

from source.settings import store as settings_store


_generated_admin_password = None

_generated_admin_committed = False

def _legacy_admin_credential():
    """Read legacy settings strictly; generate a one-time bootstrap if absent."""
    global _generated_admin_password, _generated_admin_committed
    path = settings_store.SETTINGS_PATH
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                settings = json.load(handle)
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"无法安全读取 {path}: {exc}") from exc
        if not isinstance(settings, dict):
            raise RuntimeError(f"无法安全读取 {path}: 根节点必须是 JSON 对象")
    else:
        settings = {}

    password_hash = str(settings.get("admin_password") or "").strip()
    must_change = bool(settings.get("admin_password_change_recommended", False))
    if not password_hash:
        _generated_admin_password = secrets.token_urlsafe(24)
        password_hash = generate_password_hash(_generated_admin_password, method="scrypt")
        must_change = True
        _generated_admin_committed = False
    return password_hash, int(must_change)


def mark_generated_admin_committed():
    """Mark the generated credential usable only after the admin row commits."""
    global _generated_admin_committed
    if _generated_admin_password:
        _generated_admin_committed = True


def take_generated_admin_password():
    """Return a committed bootstrap password at most once in this process."""
    global _generated_admin_password, _generated_admin_committed
    if not _generated_admin_committed:
        return None
    value = _generated_admin_password
    _generated_admin_password = None
    _generated_admin_committed = False
    return value


def finalize_legacy_admin_settings():
    """Remove legacy credential fields only after the database commit succeeds."""
    path = settings_store.SETTINGS_PATH
    if not os.path.exists(path):
        return False
    from source.settings.strict_io import read_settings_strict, write_settings_atomic

    settings, _ = read_settings_strict()
    changed = False
    for key in ("admin_password", "admin_password_change_recommended"):
        if key in settings:
            settings.pop(key)
            changed = True
    if settings.get("settings_schema_version") != 4:
        settings["settings_schema_version"] = 4
        changed = True
    if changed:
        write_settings_atomic(settings, backup_existing=True)
    return changed


def _table_columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def migrate_invite_only_users(conn):
    """Create accounts and attach all legacy private records to ``admin``."""
    password_hash, must_change = _legacy_admin_credential()
    statements = (
        """CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL COLLATE NOCASE UNIQUE,
            display_name TEXT,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('member', 'admin')),
            enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
            must_change_password INTEGER NOT NULL DEFAULT 1 CHECK (must_change_password IN (0, 1)),
            session_version INTEGER NOT NULL DEFAULT 1,
            last_login_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK (username = lower(username)),
            CHECK (role != 'admin' OR username = 'admin')
        )""",
        "CREATE UNIQUE INDEX uq_users_single_admin ON users(role) WHERE role = 'admin'",
        """CREATE TRIGGER users_protect_admin_delete
        BEFORE DELETE ON users WHEN OLD.role = 'admin'
        BEGIN SELECT RAISE(ABORT, 'admin account cannot be deleted'); END""",
        """CREATE TRIGGER users_protect_admin_update
        BEFORE UPDATE ON users WHEN OLD.role = 'admin' AND (
            NEW.username != 'admin' OR NEW.role != 'admin' OR NEW.enabled != 1
        )
        BEGIN SELECT RAISE(ABORT, 'admin account is immutable'); END""",
        """CREATE TABLE audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_user_id INTEGER,
            actor_username TEXT NOT NULL,
            action TEXT NOT NULL,
            target_type TEXT,
            target_id TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (actor_user_id) REFERENCES users(id) ON DELETE SET NULL
        )""",
        "CREATE INDEX idx_audit_events_created ON audit_events(created_at DESC)",
        "CREATE INDEX idx_audit_events_actor ON audit_events(actor_user_id)",
    )
    for statement in statements:
        conn.execute(statement)
    admin_id = conn.execute(
        """
        INSERT INTO users (
            username, display_name, password_hash, role, enabled,
            must_change_password, session_version
        ) VALUES ('admin', '管理员', ?, 'admin', 1, ?, 1)
        """,
        (password_hash, must_change),
    ).lastrowid

    conn.execute("ALTER TABLE reading_list RENAME TO reading_list_v3")
    conn.execute("""CREATE TABLE reading_list (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            paper_id INTEGER NOT NULL,
            status TEXT DEFAULT 'unread',
            added_at TEXT DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT,
            UNIQUE(user_id, paper_id),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
        )""")
    conn.execute(
        """
        INSERT INTO reading_list (id, user_id, paper_id, status, added_at, completed_at)
        SELECT id, ?, paper_id, status, added_at, completed_at FROM reading_list_v3
        """,
        (admin_id,),
    )
    conn.execute("DROP TABLE reading_list_v3")
    conn.execute("CREATE INDEX idx_reading_list_user_status ON reading_list(user_id, status)")
    conn.execute("CREATE INDEX idx_reading_list_paper ON reading_list(paper_id)")

    conn.execute("ALTER TABLE paper_chat_messages RENAME TO paper_chat_messages_v3")
    conn.execute("""CREATE TABLE paper_chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        paper_id INTEGER NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
    )""")
    conn.execute("""
        INSERT INTO paper_chat_messages (id, user_id, paper_id, role, content, created_at)
        SELECT id, ?, paper_id, role, content, created_at FROM paper_chat_messages_v3
    """, (admin_id,))
    conn.execute("DROP TABLE paper_chat_messages_v3")
    conn.execute("CREATE INDEX idx_paper_chat_user_paper ON paper_chat_messages(user_id, paper_id, id)")

    conn.execute("ALTER TABLE paper_quiz_sessions RENAME TO paper_quiz_sessions_v3")
    conn.execute("""CREATE TABLE paper_quiz_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        paper_id INTEGER NOT NULL,
        mode TEXT NOT NULL,
        status TEXT DEFAULT 'active',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
    )""")
    conn.execute("""
        INSERT INTO paper_quiz_sessions (id, user_id, paper_id, mode, status, created_at, updated_at)
        SELECT id, ?, paper_id, mode, status, created_at, updated_at FROM paper_quiz_sessions_v3
    """, (admin_id,))
    conn.execute("DROP TABLE paper_quiz_sessions_v3")
    conn.execute("CREATE INDEX idx_paper_quiz_user_paper ON paper_quiz_sessions(user_id, paper_id, updated_at)")

    if "imported_by_user_id" not in _table_columns(conn, "papers"):
        conn.execute(
            "ALTER TABLE papers ADD COLUMN imported_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL"
        )
    conn.execute(
        "UPDATE papers SET imported_by_user_id = ? WHERE ingest_mode = 'manual' AND imported_by_user_id IS NULL",
        (admin_id,),
    )
    if "user_id" not in _table_columns(conn, "ai_usage_logs"):
        conn.execute(
            "ALTER TABLE ai_usage_logs ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE SET NULL"
        )
    conn.execute("CREATE INDEX idx_ai_usage_user ON ai_usage_logs(user_id, created_at)")
