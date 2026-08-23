import json
import os
import sqlite3
import subprocess
import sys
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
from source.storage.benchmark_migrations import migrate_benchmark_tables
from source.settings import store as settings_store
from source.storage.user_migration import take_generated_admin_password

LATEST_VERSION = migrations.MIGRATIONS[-1][0]


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
        self.original_settings_path = settings_store.SETTINGS_PATH
        connection.DB_PATH = os.path.join(self.tmp.name, "papers.db")
        settings_store.SETTINGS_PATH = os.path.join(self.tmp.name, "settings.json")

    def tearDown(self):
        connection.DB_PATH = self.original_db_path
        settings_store.SETTINGS_PATH = self.original_settings_path
        self.tmp.cleanup()

    def test_version_four_migrates_legacy_admin_credential_into_user_account(self):
        password_hash = "scrypt:32768:8:1$legacy$safe-hash"
        with open(settings_store.SETTINGS_PATH, "w", encoding="utf-8") as handle:
            json.dump({
                "settings_schema_version": 3,
                "admin_password": password_hash,
                "admin_password_change_recommended": True,
            }, handle)

        init_db()

        conn = sqlite3.connect(connection.DB_PATH)
        try:
            admin = conn.execute(
                "SELECT username, password_hash, role, enabled, must_change_password "
                "FROM users WHERE username = 'admin'"
            ).fetchone()
            version = conn.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0]
        finally:
            conn.close()

        self.assertEqual(version, LATEST_VERSION)
        self.assertEqual(admin, ("admin", password_hash, "admin", 1, 1))

    def test_v6_backfills_candidate_calls_from_v5_response_continuations(self):
        """A v5 benchmark fixture is upgraded with exact historical usage."""
        conn = sqlite3.connect(":memory:")
        try:
            migrate_benchmark_tables(conn)
            suite_id = conn.execute(
                "INSERT INTO benchmark_suites (subset_name, version_label) VALUES ('pilot', 'v5')"
            ).lastrowid
            paper_ref_id = conn.execute(
                "INSERT INTO benchmark_suite_papers (suite_id, paper_key) VALUES (?, 'p')",
                (suite_id,),
            ).lastrowid
            run_id = conn.execute(
                "INSERT INTO benchmark_runs (suite_id, status, max_calls) VALUES (?, 'interrupted', 10)",
                (suite_id,),
            ).lastrowid
            candidate_id = conn.execute(
                "INSERT INTO benchmark_candidates (run_id, position, label) VALUES (?, 1, 'c')",
                (run_id,),
            ).lastrowid
            for continuation_count in (0, 2):
                conn.execute(
                    """
                    INSERT INTO benchmark_responses (
                        run_id, candidate_id, paper_ref_id, track, repeat_index,
                        prompt_snapshot, raw_output, parsed, continuation_count
                    ) VALUES (?, ?, ?, 'chat', ?, '[]', 'raw', '{}', ?)
                    """,
                    (run_id, candidate_id, paper_ref_id, continuation_count, continuation_count),
                )
            conn.commit()

            # Invoke the registered v6 migration, not a test-only ALTER TABLE.
            migration = migrations.MIGRATIONS[-1]
            self.assertEqual(migration[0], 6)
            migration[2](conn)
            column = next(
                row for row in conn.execute("PRAGMA table_info(benchmark_runs)").fetchall()
                if row[1] == "candidate_calls_made"
            )
            calls = conn.execute(
                "SELECT candidate_calls_made FROM benchmark_runs WHERE id = ?", (run_id,)
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual((column[1], column[2], column[3], column[4]),
                         ("candidate_calls_made", "INTEGER", 1, "0"))
        self.assertEqual(calls, 4)

    def test_v4_preserves_all_legacy_private_records_under_admin(self):
        with open(settings_store.SETTINGS_PATH, "w", encoding="utf-8") as handle:
            json.dump({"settings_schema_version": 3, "admin_password": "legacy-hash"}, handle)
        with connection.get_connection() as conn:
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("PRAGMA legacy_alter_table=ON")
            conn.execute("""CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")
            for version, name, migration in migrations.MIGRATIONS[:3]:
                migration(conn)
                conn.execute("INSERT INTO schema_migrations (version, name) VALUES (?, ?)", (version, name))
            paper_id = conn.execute("""
                INSERT INTO papers (paper_key, arxiv_id, source_type, source_id, ingest_mode,
                    title, authors, abstract, categories)
                VALUES ('legacy-manual', NULL, 'upload', 'legacy', 'manual',
                    'Legacy', '[]', 'Abstract', '[]')
            """).lastrowid
            conn.execute("INSERT INTO reading_list (paper_id) VALUES (?)", (paper_id,))
            conn.execute(
                "INSERT INTO paper_chat_messages (paper_id, role, content) VALUES (?, 'user', 'private')",
                (paper_id,),
            )
            session_id = conn.execute(
                "INSERT INTO paper_quiz_sessions (paper_id, mode) VALUES (?, 'quick3')",
                (paper_id,),
            ).lastrowid
            question_id = conn.execute(
                "INSERT INTO paper_quiz_questions (session_id, position, question) VALUES (?, 1, 'Q')",
                (session_id,),
            ).lastrowid
            conn.execute(
                "INSERT INTO paper_quiz_attempts (question_id, answer_text) VALUES (?, 'A')",
                (question_id,),
            )

        init_db()
        with connection.get_connection() as conn:
            admin_id = conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
            self.assertEqual(conn.execute("SELECT user_id FROM reading_list").fetchone()[0], admin_id)
            self.assertEqual(conn.execute("SELECT user_id FROM paper_chat_messages").fetchone()[0], admin_id)
            self.assertEqual(conn.execute("SELECT user_id FROM paper_quiz_sessions").fetchone()[0], admin_id)
            self.assertEqual(conn.execute("SELECT imported_by_user_id FROM papers WHERE id = ?", (paper_id,)).fetchone()[0], admin_id)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM paper_quiz_questions").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM paper_quiz_attempts").fetchone()[0], 1)
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])


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
            (version, name)
            for version, name, _migration in migrations.MIGRATIONS
        ])
        self.assertTrue({"papers", "analysis", "task_logs", "schema_migrations"} <= tables)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "migration_backups")))

    def test_corrupt_settings_aborts_v4_without_overwriting_config(self):
        original = b'{"broken":'
        with open(settings_store.SETTINGS_PATH, "wb") as handle:
            handle.write(original)
        with self.assertRaises(migrations.MigrationError):
            init_db()
        with open(settings_store.SETTINGS_PATH, "rb") as handle:
            self.assertEqual(handle.read(), original)
        conn = sqlite3.connect(connection.DB_PATH)
        try:
            users_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'users'"
            ).fetchone()
            versions = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        finally:
            conn.close()
        self.assertIsNone(users_table)
        self.assertEqual(versions, 3)

    def test_concurrent_bootstrap_is_idempotent_and_password_is_returned_once(self):
        take_generated_admin_password()
        errors = []
        barrier = threading.Barrier(4)

        def migrate():
            try:
                barrier.wait(timeout=5)
                init_db()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=migrate) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(errors, [])
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertIsNotNone(take_generated_admin_password())
        self.assertIsNone(take_generated_admin_password())
        init_db()
        with connection.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[0], 1)

    def test_multiprocess_bootstrap_migrates_once_and_yields_password_once(self):
        """Independent processes sharing one DB/settings must serialize via flock."""
        take_generated_admin_password()
        with open(settings_store.SETTINGS_PATH, "w", encoding="utf-8") as handle:
            json.dump({"settings_schema_version": 3, "session_secret": "kept"}, handle)

        script = (
            "import sys;"
            "from source.storage import connection;"
            "from source.settings import store as settings_store;"
            "connection.DB_PATH = sys.argv[1];"
            "settings_store.SETTINGS_PATH = sys.argv[2];"
            "from source.storage import init_db;"
            "from source.storage.user_migration import take_generated_admin_password;"
            "init_db();"
            "print('PASSWORD' if take_generated_admin_password() else 'NONE')"
        )
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        procs = [
            subprocess.Popen(
                [sys.executable, "-c", script, connection.DB_PATH, settings_store.SETTINGS_PATH],
                cwd=repo_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(4)
        ]
        results = []
        for proc in procs:
            stdout, stderr = proc.communicate(timeout=60)
            results.append((proc.returncode, stdout.strip(), stderr))

        self.assertTrue(os.path.exists(os.path.join(self.tmp.name, ".database-migration.lock")))
        for returncode, stdout, stderr in results:
            self.assertEqual(returncode, 0, f"child failed: {stderr}")
            self.assertIn(stdout, {"PASSWORD", "NONE"}, f"unexpected child output: {stderr}")
        self.assertEqual(sum(1 for _, stdout, _ in results if stdout == "PASSWORD"), 1)

        conn = sqlite3.connect(connection.DB_PATH)
        try:
            version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
            admins = conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(version, LATEST_VERSION)
        self.assertEqual(admins, 1)
        with open(settings_store.SETTINGS_PATH, encoding="utf-8") as handle:
            settings = json.load(handle)
        self.assertEqual(settings["settings_schema_version"], 4)
        self.assertNotIn("admin_password", settings)

    def test_settings_finalization_failure_keeps_committed_v4_and_yields_password_once(self):
        take_generated_admin_password()
        with open(settings_store.SETTINGS_PATH, "w", encoding="utf-8") as handle:
            json.dump({"settings_schema_version": 3, "session_secret": "kept"}, handle)
        original_settings = open(settings_store.SETTINGS_PATH, "rb").read()

        with patch(
            "source.storage.migrations.finalize_legacy_admin_settings",
            side_effect=RuntimeError("injected finalization failure"),
        ):
            with self.assertRaises(RuntimeError):
                init_db()

        conn = sqlite3.connect(connection.DB_PATH)
        try:
            version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
            admin = conn.execute(
                "SELECT username, role FROM users WHERE username = 'admin'"
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(version, LATEST_VERSION)
        self.assertEqual(admin, ("admin", "admin"))

        first = take_generated_admin_password()
        second = take_generated_admin_password()
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        with open(settings_store.SETTINGS_PATH, "rb") as handle:
            self.assertEqual(handle.read(), original_settings)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "settings-backup")))

    def test_rolled_back_v4_never_yields_uncommitted_password(self):
        take_generated_admin_password()
        with open(settings_store.SETTINGS_PATH, "w", encoding="utf-8") as handle:
            json.dump({"settings_schema_version": 3}, handle)

        original = migrations.MIGRATIONS

        def fail_after_write(conn):
            for version, name, migration in original:
                if version == 4:
                    migration(conn)
            raise RuntimeError("injected failure after v4 work")

        try:
            migrations.MIGRATIONS = original[:3] + (
                (4, "invite_only_users", fail_after_write),
            )
            with self.assertRaises(migrations.MigrationError):
                init_db()
        finally:
            migrations.MIGRATIONS = original

        conn = sqlite3.connect(connection.DB_PATH)
        try:
            version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
            users_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'users'"
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(version, 3)
        self.assertIsNone(users_table)
        self.assertIsNone(take_generated_admin_password())



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

        self.assertEqual(versions, [(v,) for v, _n, _m in migrations.MIGRATIONS])
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
        with connection.get_connection() as db:
            admin_id = db.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
        add_paper_chat_message(admin_id, paper_id, "user", "hello")

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
            next_version = original_migrations[-1][0] + 1
            migrations.MIGRATIONS = original_migrations + (
                (next_version, "failing_migration", fail_after_write),
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
        self.assertEqual(versions, [(v,) for v, _n, _m in migrations.MIGRATIONS])
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
                "INSERT INTO schema_migrations (version, name) VALUES (?, 'future')",
                (LATEST_VERSION + 1,),
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
