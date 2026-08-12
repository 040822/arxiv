"""Real-storage tests for atomic paper deletion with private-data protection."""

import json
import os
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from source.settings import store as settings_store
from source.storage import connection
from source.storage import papers as papers_storage
from source.storage import (
    add_paper_chat_message,
    add_to_reading_list,
    batch_delete_papers_protected,
    create_paper_quiz_session,
    delete_paper_protected,
    get_paper_by_key,
    get_paper_private_impact,
    init_db,
    insert_paper,
)


class DeletePaperProtectedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db_path = connection.DB_PATH
        self.original_settings_path = settings_store.SETTINGS_PATH
        connection.DB_PATH = os.path.join(self.tmp.name, "papers.db")
        settings_store.SETTINGS_PATH = os.path.join(self.tmp.name, "settings.json")
        with open(settings_store.SETTINGS_PATH, "w", encoding="utf-8") as handle:
            json.dump({"settings_schema_version": 4}, handle)
        init_db()
        with connection.get_connection() as conn:
            self.admin_id = conn.execute(
                "SELECT id FROM users WHERE username = 'admin'"
            ).fetchone()[0]

    def tearDown(self):
        connection.DB_PATH = self.original_db_path
        settings_store.SETTINGS_PATH = self.original_settings_path
        self.tmp.cleanup()

    def _paper(self, key="2608.00001"):
        return insert_paper({
            "paper_key": key, "arxiv_id": key, "source_type": "arxiv",
            "source_id": key, "ingest_mode": "feed",
            "title": "Paper", "authors": [], "abstract": "Abstract", "categories": [],
        })

    def _private_counts(self, paper_id):
        with connection.get_connection() as conn:
            return {
                "reading_list": conn.execute(
                    "SELECT COUNT(*) FROM reading_list WHERE paper_id = ?", (paper_id,)
                ).fetchone()[0],
                "chat_messages": conn.execute(
                    "SELECT COUNT(*) FROM paper_chat_messages WHERE paper_id = ?", (paper_id,)
                ).fetchone()[0],
                "quiz_sessions": conn.execute(
                    "SELECT COUNT(*) FROM paper_quiz_sessions WHERE paper_id = ?", (paper_id,)
                ).fetchone()[0],
            }

    def test_safe_paper_is_deleted_without_force(self):
        paper_id = self._paper()
        result = delete_paper_protected("2608.00001")
        self.assertTrue(result["deleted"])
        self.assertFalse(result["conflict"])
        self.assertEqual(result["impact"]["total"], 0)
        self.assertIsNone(get_paper_by_key("2608.00001"))

    def test_protected_paper_is_not_deleted_without_force(self):
        paper_id = self._paper()
        add_to_reading_list(self.admin_id, paper_id)
        add_paper_chat_message(self.admin_id, paper_id, "user", "private")
        create_paper_quiz_session(self.admin_id, paper_id, "quick3")

        result = delete_paper_protected("2608.00001")
        self.assertFalse(result["deleted"])
        self.assertTrue(result["conflict"])
        self.assertEqual(result["impact"]["total"], 3)
        self.assertIsNotNone(get_paper_by_key("2608.00001"))
        self.assertEqual(self._private_counts(paper_id), {
            "reading_list": 1, "chat_messages": 1, "quiz_sessions": 1,
        })

    def test_force_cascades_private_records(self):
        paper_id = self._paper()
        add_to_reading_list(self.admin_id, paper_id)
        add_paper_chat_message(self.admin_id, paper_id, "user", "private")
        session_id = create_paper_quiz_session(self.admin_id, paper_id, "quick3")

        result = delete_paper_protected("2608.00001", force=True)
        self.assertTrue(result["deleted"])
        self.assertFalse(result["conflict"])
        self.assertEqual(result["impact"]["total"], 3)
        self.assertIsNone(get_paper_by_key("2608.00001"))
        with connection.get_connection() as conn:
            orphan = conn.execute(
                "SELECT COUNT(*) FROM paper_chat_messages WHERE paper_id = ?", (paper_id,)
            ).fetchone()[0]
            self.assertEqual(orphan, 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM reading_list").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM paper_quiz_sessions").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM paper_quiz_questions").fetchone()[0], 0)
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_missing_paper_returns_untouched_result(self):
        result = delete_paper_protected("nope-1")
        self.assertEqual(result, {
            "deleted": False, "paper": None, "impact": None, "conflict": False,
        })

    def test_batch_deletes_safe_papers_and_skips_protected_with_dedup(self):
        safe_id = self._paper("2608.00010")
        protected_id = self._paper("2608.00020")
        add_to_reading_list(self.admin_id, protected_id)

        result = batch_delete_papers_protected(
            ["2608.00010", "2608.00020", "2608.00010", "2608.00010", "missing-key"]
        )
        self.assertEqual(result["deleted"], 1)
        self.assertEqual(result["skipped"], ["2608.00020"])
        self.assertEqual([p["paper_key"] for p in result["papers"]], ["2608.00010"])
        self.assertIsNone(get_paper_by_key("2608.00010"))
        self.assertIsNotNone(get_paper_by_key("2608.00020"))

    def test_batch_delete_protected_after_force_removes_paper(self):
        protected_id = self._paper("2608.00030")
        add_to_reading_list(self.admin_id, protected_id)
        delete_paper_protected("2608.00030", force=True)
        result = batch_delete_papers_protected(["2608.00030"])
        self.assertEqual(result["deleted"], 0)
        self.assertEqual(result["skipped"], [])


class DeletePaperRaceTests(unittest.TestCase):
    """Prove the impact check and delete stay atomic under concurrent private
    writes: in WAL mode BEGIN IMMEDIATE holds the exclusive WAL write lock from
    the BEGIN statement, so either the concurrent insert commits first (the
    check must then see it -> conflict) or the delete wins (the insert is then
    rejected by the write lock / FK). Orphan rows or silent data loss are
    impossible regardless of transaction variant.
    """

    ROUNDS = 8

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db_path = connection.DB_PATH
        self.original_settings_path = settings_store.SETTINGS_PATH
        connection.DB_PATH = os.path.join(self.tmp.name, "papers.db")
        settings_store.SETTINGS_PATH = os.path.join(self.tmp.name, "settings.json")
        with open(settings_store.SETTINGS_PATH, "w", encoding="utf-8") as handle:
            json.dump({"settings_schema_version": 4}, handle)
        init_db()
        with connection.get_connection() as conn:
            self.admin_id = conn.execute(
                "SELECT id FROM users WHERE username = 'admin'"
            ).fetchone()[0]

    def tearDown(self):
        connection.DB_PATH = self.original_db_path
        settings_store.SETTINGS_PATH = self.original_settings_path
        self.tmp.cleanup()

    def test_delete_and_private_insert_never_produce_orphan_or_silent_data_loss(self):
        for round_index in range(self.ROUNDS):
            key = f"2608.{round_index + 1:05d}"
            paper_id = insert_paper({
                "paper_key": key, "arxiv_id": key, "source_type": "arxiv",
                "source_id": key, "ingest_mode": "feed",
                "title": "Paper", "authors": [], "abstract": "Abstract", "categories": [],
            })
            barrier = threading.Barrier(2)
            outcomes = []
            insert_error = []

            def deleter():
                barrier.wait(timeout=5)
                try:
                    outcomes.append(delete_paper_protected(key))
                except Exception as exc:  # pragma: no cover - unexpected
                    insert_error.append(f"delete raised: {exc}")

            def inserter():
                barrier.wait(timeout=5)
                try:
                    outcomes.append(("insert", add_to_reading_list(self.admin_id, paper_id)))
                except Exception as exc:  # pragma: no cover - unexpected
                    insert_error.append(f"insert raised: {exc}")

            threads = [threading.Thread(target=deleter), threading.Thread(target=inserter)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
            self.assertEqual(insert_error, [])

            delete_result = next(
                (item for item in outcomes if isinstance(item, dict)), None
            )
            insert_result = next(
                (value for item in outcomes
                 if isinstance(item, tuple) for key, value in [item] if key == "insert"),
                None,
            )
            with connection.get_connection() as conn:
                paper_exists = conn.execute(
                    "SELECT 1 FROM papers WHERE id = ?", (paper_id,)
                ).fetchone() is not None
                reading_exists = conn.execute(
                    "SELECT 1 FROM reading_list WHERE paper_id = ?", (paper_id,)
                ).fetchone() is not None

            if delete_result and delete_result.get("conflict"):
                self.assertTrue(paper_exists, f"round {round_index}: conflict but paper deleted")
                self.assertTrue(reading_exists, f"round {round_index}: conflict but reading row missing")
            elif delete_result and delete_result.get("deleted"):
                self.assertFalse(paper_exists, f"round {round_index}: deleted but paper survives")
                self.assertFalse(reading_exists, f"round {round_index}: orphan reading row after delete")
                self.assertIs(insert_result, False,
                              f"round {round_index}: delete won but concurrent insert succeeded")
            else:
                self.fail(f"round {round_index}: unexpected outcome {outcomes}")

    def test_delete_paper_protected_blocks_writer_inside_critical_section(self):
        """Deterministic interleaving through the PRODUCTION function: the impact
        query is wrapped so the deleter pauses inside delete_paper_protected()
        after the real impact check, while still holding BEGIN IMMEDIATE. A
        no-wait writer must then be blocked by the write lock, and the delete
        must complete atomically once released. This pins the production
        check-then-delete transaction boundary itself."""
        key = "2608.00999"
        paper_id = insert_paper({
            "paper_key": key, "arxiv_id": key, "source_type": "arxiv",
            "source_id": key, "ingest_mode": "feed",
            "title": "Paper", "authors": [], "abstract": "Abstract", "categories": [],
        })
        impact_done = threading.Event()
        release = threading.Event()
        real_impact = papers_storage._paper_private_impact_in_conn
        outcome = []

        def paused_impact(conn, paper_key):
            impact = real_impact(conn, paper_key)
            impact_done.set()
            assert release.wait(timeout=5)
            return impact

        def deleter():
            outcome.append(delete_paper_protected(key))

        with patch.object(
            papers_storage, "_paper_private_impact_in_conn", side_effect=paused_impact
        ):
            thread = threading.Thread(target=deleter)
            thread.start()
            self.assertTrue(
                impact_done.wait(timeout=5),
                "production impact check did not run within timeout",
            )
            no_wait = sqlite3.connect(connection.DB_PATH, timeout=0)
            no_wait.execute("PRAGMA foreign_keys=ON")
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    no_wait.execute(
                        "INSERT INTO reading_list (user_id, paper_id) VALUES (?, ?)",
                        (self.admin_id, paper_id),
                    )
            finally:
                no_wait.close()
            release.set()
            thread.join(timeout=10)

        self.assertFalse(thread.is_alive())
        self.assertEqual(len(outcome), 1)
        result = outcome[0]
        self.assertTrue(result["deleted"])
        self.assertFalse(result["conflict"])
        self.assertEqual(result["impact"]["total"], 0)
        self.assertIsNone(get_paper_by_key(key))
        with connection.get_connection() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM reading_list WHERE paper_id = ?",
                    (paper_id,),
                ).fetchone()[0],
                0,
            )


if __name__ == "__main__":
    unittest.main()
