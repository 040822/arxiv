"""Ordered, transactional SQLite schema migrations."""

import logging
import os
import sqlite3
import fcntl
import threading
from contextlib import contextmanager
from datetime import datetime

from . import connection
from .analysis_migrations import migrate_analysis_unique
from .benchmark_migrations import (
    migrate_benchmark_candidate_call_budget, migrate_benchmark_tables, migrate_benchmark_v7,
)
from .paper_identity_migration import migrate_generic_paper_identity
from .schema import apply_baseline_schema
from .snapshot import copy_sqlite_snapshot
from .user_migration import (
    finalize_legacy_admin_settings, mark_generated_admin_committed, migrate_invite_only_users,
)

logger = logging.getLogger(__name__)
_IN_PROCESS_MIGRATION_LOCK = threading.Lock()

MIGRATION_BACKUP_KEEP = 3
MIGRATIONS = (
    (1, "baseline", apply_baseline_schema),
    (2, "analysis_unique", migrate_analysis_unique),
    (3, "generic_paper_identity", migrate_generic_paper_identity),
    (4, "invite_only_users", migrate_invite_only_users),
    (5, "benchmark_tables", migrate_benchmark_tables),
    (6, "benchmark_candidate_call_budget", migrate_benchmark_candidate_call_budget),
    (7, "benchmark_v7_run_reuse_review_audit", migrate_benchmark_v7),
)


class MigrationError(RuntimeError):
    """Raised when database schema migration cannot complete safely."""


@contextmanager
def _migration_lock(db_path):
    directory = os.path.dirname(db_path)
    os.makedirs(directory, exist_ok=True)
    lock_path = os.path.join(directory, ".database-migration.lock")
    with _IN_PROCESS_MIGRATION_LOCK:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


def _read_applied_versions(db_path):
    if not os.path.exists(db_path) or os.path.getsize(db_path) == 0:
        return []
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        exists = conn.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = 'schema_migrations'
            """
        ).fetchone()
        if not exists:
            return []
        return [
            int(row[0])
            for row in conn.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        ]
    finally:
        conn.close()


def _validate_versions(versions):
    target = MIGRATIONS[-1][0]
    if versions and versions[-1] > target:
        raise MigrationError(
            f"数据库 schema 版本 {versions[-1]} 高于程序支持的版本 {target}"
        )
    expected = list(range(1, (versions[-1] if versions else 0) + 1))
    if versions != expected:
        raise MigrationError(f"数据库 migration 版本不连续: {versions}")


def _migration_backup_path(db_path, current_version, target_version, now=None):
    now = now or datetime.now()
    backup_dir = os.path.join(os.path.dirname(db_path), "migration_backups")
    basename = os.path.splitext(os.path.basename(db_path))[0]
    stamp = now.strftime("%Y%m%d-%H%M%S-%f")
    filename = f"{basename}-v{current_version}-to-v{target_version}-{stamp}.db"
    return os.path.join(backup_dir, filename)


def _prune_migration_backups(backup_dir):
    backups = sorted(
        (
            os.path.join(backup_dir, name)
            for name in os.listdir(backup_dir)
            if name.endswith(".db")
        ),
        key=lambda path: (os.path.getmtime(path), path),
        reverse=True,
    )
    for path in backups[MIGRATION_BACKUP_KEEP:]:
        os.remove(path)


def _create_migration_backup(db_path, current_version, target_version):
    backup_path = _migration_backup_path(
        db_path,
        current_version,
        target_version,
    )
    copy_sqlite_snapshot(db_path, backup_path)
    _prune_migration_backups(os.path.dirname(backup_path))
    return backup_path


def _ensure_migration_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _run_migrations_locked():
    """Apply pending migrations in order and return the current schema version."""
    db_path = connection.DB_PATH
    target_version = MIGRATIONS[-1][0]
    try:
        applied_versions = _read_applied_versions(db_path)
        _validate_versions(applied_versions)
    except MigrationError:
        raise
    except Exception as exc:
        raise MigrationError(f"无法读取数据库 migration 状态: {exc}") from exc

    pending = [
        migration
        for migration in MIGRATIONS
        if migration[0] not in applied_versions
    ]
    if not pending:
        if 4 in applied_versions:
            finalize_legacy_admin_settings()
        return applied_versions[-1] if applied_versions else 0

    current_version = applied_versions[-1] if applied_versions else 0
    backup_path = None
    if os.path.exists(db_path) and os.path.getsize(db_path) > 0:
        try:
            backup_path = _create_migration_backup(
                db_path,
                current_version,
                target_version,
            )
        except Exception as exc:
            raise MigrationError(f"迁移前数据库快照失败: {exc}") from exc

    for version, name, migration in pending:
        try:
            with connection.get_connection() as conn:
                conn.execute("PRAGMA foreign_keys=OFF")
                conn.execute("PRAGMA legacy_alter_table=ON")
                conn.execute("BEGIN IMMEDIATE")
                _ensure_migration_table(conn)
                live_versions = [
                    int(row[0])
                    for row in conn.execute(
                        "SELECT version FROM schema_migrations ORDER BY version"
                    ).fetchall()
                ]
                _validate_versions(live_versions)
                if version in live_versions:
                    continue
                expected_version = (live_versions[-1] if live_versions else 0) + 1
                if version != expected_version:
                    raise MigrationError(
                        f"migration 顺序错误: 期望 v{expected_version}，实际 v{version}"
                    )
                migration(conn)
                violations = conn.execute("PRAGMA foreign_key_check").fetchall()
                if violations:
                    raise MigrationError(
                        f"migration v{version} foreign key violations: {violations[:5]}"
                    )
                conn.execute(
                    "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                    (version, name),
                )
        except Exception as exc:
            location = f"，快照: {backup_path}" if backup_path else ""
            raise MigrationError(
                f"数据库 migration v{version} ({name}) 失败{location}: {exc}"
            ) from exc
        logger.info("Applied database migration v%s (%s)", version, name)
        if version == 4:
            mark_generated_admin_committed()

    finalize_legacy_admin_settings()
    return target_version


def run_migrations():
    """Serialize migrations and settings finalization across processes."""
    with _migration_lock(connection.DB_PATH):
        return _run_migrations_locked()
