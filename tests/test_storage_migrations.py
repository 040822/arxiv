import os
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from source.storage import connection, migrations, schema
from source.storage import (
    add_paper_chat_message, get_analysis_by_paper_id, get_average_rating,
    init_db, insert_analysis, insert_paper, update_analysis,
)
from source.storage.snapshot import copy_sqlite_snapshot


class SQLiteSnapshotTests(unittest.TestCase):
    def test_copy_sqlite_snapshot_creates_readable_consistent_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_path = os.path.join(tmp, "source.db")
            snapshot_path = os.path.join(tmp, "snapshot.db")
            source = sqlite3.connect(source_path)
            try:
                source.execute("CREATE TABLE sample (value TEXT)")
                source.execute("INSERT INTO sample (value) VALUES ('saved')")
                source.commit()
            finally:
                source.close()

            copy_sqlite_snapshot(source_path, snapshot_path)

            snapshot = sqlite3.connect(f"file:{snapshot_path}?mode=ro", uri=True)
            try:
                self.assertEqual(snapshot.execute("SELECT value FROM sample").fetchone()[0], "saved")
            finally:
                snapshot.close()

    def test_snapshot_destination_open_failure_closes_source_and_removes_partial_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_path = os.path.join(tmp, "source.db")
            snapshot_path = os.path.join(tmp, "snapshot.db")
            open(source_path, "wb").close()
            open(snapshot_path, "wb").close()
            source = Mock()

            with patch(
                "source.storage.snapshot.sqlite3.connect",
                side_effect=[source, OSError("destination unavailable")],
            ):
                with self.assertRaisesRegex(OSError, "destination unavailable"):
                    copy_sqlite_snapshot(source_path, snapshot_path)

            source.close.assert_called_once_with()
            self.assertFalse(os.path.exists(snapshot_path))


class SchemaMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db_path = connection.DB_PATH
        connection.DB_PATH = os.path.join(self.tmp.name, "papers.db")

    def tearDown(self):
        connection.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    def test_empty_database_migrates_to_latest_version_without_snapshot(self):
        init_db()

        conn = sqlite3.connect(connection.DB_PATH)
        try:
            versions = conn.execute(
                "SELECT version, name FROM schema_migrations ORDER BY version"
            ).fetchall()
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        finally:
            conn.close()

        self.assertEqual(versions, [
            (1, "baseline"),
            (2, "analysis_unique"),
            (3, "generic_paper_identity"),
        ])
        self.assertTrue({"papers", "analysis", "task_logs", "schema_migrations"} <= tables)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "migration_backups")))


    def test_existing_version_zero_database_is_backed_up_and_preserved(self):
        conn = sqlite3.connect(connection.DB_PATH)
        try:
            conn.execute("""
                CREATE TABLE papers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    arxiv_id TEXT UNIQUE NOT NULL,
                    title TEXT NOT NULL,
                    authors TEXT NOT NULL,
                    abstract TEXT NOT NULL,
                    categories TEXT NOT NULL,
                    primary_category TEXT,
                    url TEXT,
                    pdf_url TEXT,
                    published_date TEXT,
                    updated_date TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE analysis (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    paper_id INTEGER NOT NULL,
                    tags TEXT,
                    summary_cn TEXT,
                    summary_en TEXT,
                    rating INTEGER DEFAULT 0,
                    value_comment TEXT,
                    analyzed_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute(
                "INSERT INTO papers (arxiv_id, title, authors, abstract, categories) "
                "VALUES ('2607.00001', 'title', '[]', 'abstract', '[]')"
            )
            conn.commit()
        finally:
            conn.close()

        init_db()

        conn = sqlite3.connect(connection.DB_PATH)
        try:
            self.assertEqual(conn.execute("SELECT title FROM papers").fetchone()[0], "title")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(analysis)")}
            self.assertIn("recommendation_score", columns)
        finally:
            conn.close()

        backup_dir = os.path.join(self.tmp.name, "migration_backups")
        backups = os.listdir(backup_dir)
        self.assertEqual(len(backups), 1)
        init_db()
        self.assertEqual(os.listdir(backup_dir), backups)
        snapshot = sqlite3.connect(os.path.join(backup_dir, backups[0]))
        try:
            self.assertEqual(snapshot.execute("SELECT title FROM papers").fetchone()[0], "title")
            with self.assertRaises(sqlite3.OperationalError):
                snapshot.execute("SELECT * FROM schema_migrations")
        finally:
            snapshot.close()


    def test_version_two_merges_duplicate_analysis_and_enforces_uniqueness(self):
        with connection.get_connection() as conn:
            schema.apply_baseline_schema(conn)
            conn.execute("""
                CREATE TABLE schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("INSERT INTO schema_migrations (version, name) VALUES (1, 'baseline')")
        conn = sqlite3.connect(connection.DB_PATH)
        try:
            paper_id = conn.execute(
                """
                INSERT INTO papers (arxiv_id, title, authors, abstract, categories)
                VALUES ('2607.00002', 'title', '[]', 'abstract', '[]')
                """
            ).lastrowid
            canonical_id = conn.execute(
                """
                INSERT INTO analysis (paper_id, tags, summary_cn, rating, value_comment, qa_analysis)
                VALUES (?, '[]', 'first summary', 4, '', '')
                """,
                (paper_id,),
            ).lastrowid
            conn.execute(
                """
                INSERT INTO analysis (paper_id, tags, summary_cn, rating, value_comment, qa_analysis)
                VALUES (?, '["VLA"]', 'conflicting summary', 5, 'filled comment', 'deep read')
                """,
                (paper_id,),
            )
            conn.commit()
        finally:
            conn.close()

        init_db()

        conn = sqlite3.connect(connection.DB_PATH)
        try:
            rows = conn.execute(
                "SELECT id, tags, summary_cn, rating, value_comment, qa_analysis "
                "FROM analysis WHERE paper_id = ?",
                (paper_id,),
            ).fetchall()
            versions = conn.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO analysis (paper_id) VALUES (?)", (paper_id,))
        finally:
            conn.close()

        self.assertEqual(versions, [(1,), (2,), (3,)])
        self.assertEqual(rows, [
            (canonical_id, '["VLA"]', "first summary", 4, "filled comment", "deep read")
        ])

    def test_version_three_generalizes_paper_identity_without_breaking_foreign_keys(self):
        init_db()
        paper_id = insert_paper({
            "arxiv_id": "2607.54321",
            "title": "Legacy paper",
            "authors": ["Alice"],
            "abstract": "Abstract",
            "categories": ["cs.RO"],
            "primary_category": "cs.RO",
            "url": "https://arxiv.org/abs/2607.54321",
            "pdf_url": "https://arxiv.org/pdf/2607.54321",
            "published_date": "2026-07-24",
            "updated_date": "2026-07-24",
        })
        add_paper_chat_message(paper_id, "user", "hello")

        conn = sqlite3.connect(connection.DB_PATH)
        try:
            row = conn.execute(
                "SELECT paper_key, arxiv_id, source_type, ingest_mode FROM papers WHERE id = ?",
                (paper_id,),
            ).fetchone()
            conn.execute("""
                INSERT INTO papers (
                    paper_key, arxiv_id, source_type, ingest_mode, title,
                    authors, abstract, categories
                ) VALUES (?, NULL, 'upload', 'manual', ?, '[]', '', '[]')
            """, ("p_manual", "Uploaded paper"))
            conn.commit()
            foreign_key_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            chat_count = conn.execute(
                "SELECT COUNT(*) FROM paper_chat_messages WHERE paper_id = ?",
                (paper_id,),
            ).fetchone()[0]
        finally:
            conn.close()

        self.assertEqual(row, ("2607.54321", "2607.54321", "arxiv", "feed"))
        self.assertEqual(chat_count, 1)
        self.assertEqual(foreign_key_violations, [])


    def test_average_rating_query_is_exposed_by_storage(self):
        init_db()
        ratings = (2, 4)
        for index, rating in enumerate(ratings):
            paper_id = insert_paper({
                "arxiv_id": f"2607.1000{index}",
                "title": "title",
                "authors": [],
                "abstract": "abstract",
                "categories": [],
                "primary_category": "cs.AI",
                "url": "",
                "pdf_url": "",
                "published_date": "2026-07-24",
                "updated_date": "2026-07-24",
            })
            insert_analysis(paper_id, {
                "tags": [],
                "summary_cn": "",
                "summary_en": "",
                "rating": rating,
                "value_comment": "",
            })

        self.assertEqual(get_average_rating(), 3.0)


    def test_concurrent_analysis_insert_creates_one_record_without_errors(self):
        init_db()
        paper_id = insert_paper({
            "arxiv_id": "2607.00003",
            "title": "title",
            "authors": [],
            "abstract": "abstract",
            "categories": [],
            "primary_category": "cs.AI",
            "url": "",
            "pdf_url": "",
            "published_date": "2026-07-24",
            "updated_date": "2026-07-24",
        })
        barrier = threading.Barrier(8)
        results = []
        errors = []
        lock = threading.Lock()

        def insert():
            barrier.wait()
            try:
                result = insert_analysis(paper_id, {
                    "tags": ["VLA"],
                    "summary_cn": "summary",
                    "summary_en": "",
                    "rating": 4,
                    "value_comment": "comment",
                })
                with lock:
                    results.append(result)
            except Exception as exc:
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=insert) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertEqual(errors, [])
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(sum(result is None for result in results), 7)


    def test_concurrent_partial_update_creates_one_analysis_without_errors(self):
        init_db()
        paper_id = insert_paper({
            "arxiv_id": "2607.00004",
            "title": "title",
            "authors": [],
            "abstract": "abstract",
            "categories": [],
            "primary_category": "cs.AI",
            "url": "",
            "pdf_url": "",
            "published_date": "2026-07-24",
            "updated_date": "2026-07-24",
        })
        barrier = threading.Barrier(8)
        results = []
        errors = []
        lock = threading.Lock()

        def update():
            barrier.wait()
            try:
                result = update_analysis(paper_id, {"rating": 3})
                with lock:
                    results.append(result)
            except Exception as exc:
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=update) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertEqual(errors, [])
        self.assertEqual(results, [True] * 8)
        self.assertEqual(get_analysis_by_paper_id(paper_id)["rating"], 3)


    def test_failed_migration_rolls_back_and_keeps_only_three_snapshots(self):
        init_db()
        original_migrations = migrations.MIGRATIONS

        def fail_after_write(conn):
            conn.execute("CREATE TABLE should_rollback (id INTEGER)")
            raise RuntimeError("injected failure")

        messages = []
        try:
            migrations.MIGRATIONS = original_migrations + (
                (4, "failing_migration", fail_after_write),
            )
            for _ in range(5):
                with self.assertRaises(RuntimeError) as raised:
                    init_db()
                messages.append(str(raised.exception))
        finally:
            migrations.MIGRATIONS = original_migrations

        conn = sqlite3.connect(connection.DB_PATH)
        try:
            versions = conn.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
            rolled_back = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'should_rollback'"
            ).fetchone()
        finally:
            conn.close()

        backups = os.listdir(os.path.join(self.tmp.name, "migration_backups"))
        self.assertEqual(versions, [(1,), (2,), (3,)])
        self.assertIsNone(rolled_back)
        self.assertEqual(len(backups), 3)
        self.assertTrue(all("快照:" in message for message in messages))

    def test_writer_waits_for_short_lock_and_then_succeeds(self):
        init_db()
        locker = sqlite3.connect(connection.DB_PATH, timeout=0)
        locker.execute("BEGIN IMMEDIATE")
        started = threading.Event()
        result = []
        errors = []

        def write():
            started.set()
            try:
                result.append(insert_paper({
                    "arxiv_id": "2607.00005",
                    "title": "title",
                    "authors": [],
                    "abstract": "abstract",
                    "categories": [],
                    "primary_category": "cs.AI",
                    "url": "",
                    "pdf_url": "",
                    "published_date": "2026-07-24",
                    "updated_date": "2026-07-24",
                }))
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=write)
        thread.start()
        started.wait(timeout=2)
        time.sleep(0.1)
        locker.commit()
        locker.close()
        thread.join(timeout=10)

        self.assertEqual(errors, [])
        self.assertEqual(len(result), 1)
        self.assertIsNotNone(result[0])


    def test_newer_database_version_aborts_startup(self):
        init_db()
        conn = sqlite3.connect(connection.DB_PATH)
        try:
            conn.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (4, 'future')"
            )
            conn.commit()
        finally:
            conn.close()

        with self.assertRaisesRegex(RuntimeError, "高于程序支持的版本"):
            init_db()

    def test_migration_version_gap_aborts_startup(self):
        init_db()
        conn = sqlite3.connect(connection.DB_PATH)
        try:
            conn.execute("DELETE FROM schema_migrations WHERE version = 1")
            conn.commit()
        finally:
            conn.close()

        with self.assertRaisesRegex(RuntimeError, "版本不连续"):
            init_db()

    def test_snapshot_failure_aborts_before_schema_changes(self):
        conn = sqlite3.connect(connection.DB_PATH)
        try:
            conn.execute("CREATE TABLE legacy_data (value TEXT)")
            conn.execute("INSERT INTO legacy_data VALUES ('safe')")
            conn.commit()
        finally:
            conn.close()

        with patch.object(migrations, "copy_sqlite_snapshot", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(RuntimeError, "快照失败"):
                init_db()

        conn = sqlite3.connect(connection.DB_PATH)
        try:
            self.assertEqual(conn.execute("SELECT value FROM legacy_data").fetchone()[0], "safe")
            migration_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNone(migration_table)


if __name__ == "__main__":
    unittest.main()
