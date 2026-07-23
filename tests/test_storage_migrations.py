import os
import sqlite3
import tempfile
import unittest

from source.storage import connection
from source.storage.snapshot import copy_sqlite_snapshot

import database


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


class SchemaMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db_path = connection.DB_PATH
        connection.DB_PATH = os.path.join(self.tmp.name, "papers.db")

    def tearDown(self):
        connection.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    def test_empty_database_migrates_to_version_one_without_snapshot(self):
        database.init_db()

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

        self.assertEqual(versions, [(1, "baseline")])
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

        database.init_db()

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
        database.init_db()
        self.assertEqual(os.listdir(backup_dir), backups)
        snapshot = sqlite3.connect(os.path.join(backup_dir, backups[0]))
        try:
            self.assertEqual(snapshot.execute("SELECT title FROM papers").fetchone()[0], "title")
            with self.assertRaises(sqlite3.OperationalError):
                snapshot.execute("SELECT * FROM schema_migrations")
        finally:
            snapshot.close()


if __name__ == "__main__":
    unittest.main()
