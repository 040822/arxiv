import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from source.settings import store as settings_store
from source.storage import connection
from source.storage import (
    add_paper_chat_message,
    add_paper_quiz_question,
    add_to_reading_list,
    create_member,
    create_paper_quiz_session,
    delete_member,
    get_audit_events,
    get_paper_chat_messages,
    get_paper_quiz_session_detail,
    get_reading_list,
    get_user_by_username,
    init_db,
    insert_paper,
    record_audit_event,
    set_member_enabled,
)
from source.web.progress import get_progress, progress_store, update_progress


class InviteOnlyUserSystemTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db_path = connection.DB_PATH
        self.original_settings_path = settings_store.SETTINGS_PATH
        connection.DB_PATH = os.path.join(self.tmp.name, "papers.db")
        settings_store.SETTINGS_PATH = os.path.join(self.tmp.name, "settings.json")
        with open(settings_store.SETTINGS_PATH, "w", encoding="utf-8") as handle:
            json.dump({
                "settings_schema_version": 3,
                "admin_password": "scrypt:32768:8:1$legacy$hash",
                "admin_password_change_recommended": False,
                "session_secret": "kept",
            }, handle)
        init_db()
        self.admin = get_user_by_username("admin")

    def tearDown(self):
        connection.DB_PATH = self.original_db_path
        settings_store.SETTINGS_PATH = self.original_settings_path
        self.tmp.cleanup()

    def _paper(self, key="2608.00001", importer=None):
        return insert_paper({
            "paper_key": key, "arxiv_id": key, "source_type": "arxiv",
            "source_id": key, "ingest_mode": "manual" if importer else "feed",
            "title": "Paper", "authors": [], "abstract": "Abstract", "categories": [],
            "imported_by_user_id": importer,
        })

    def test_settings_credential_is_removed_field_by_field_after_database_commit(self):
        with open(settings_store.SETTINGS_PATH, encoding="utf-8") as handle:
            settings = json.load(handle)
        self.assertEqual(settings["settings_schema_version"], 4)
        self.assertEqual(settings["session_secret"], "kept")
        self.assertNotIn("admin_password", settings)
        self.assertNotIn("admin_password_change_recommended", settings)
        backup_dir = os.path.join(self.tmp.name, "settings-backup")
        self.assertEqual(len(os.listdir(backup_dir)), 1)
        with open(os.path.join(backup_dir, os.listdir(backup_dir)[0]), encoding="utf-8") as handle:
            self.assertIn("admin_password", json.load(handle))

    def test_session_secret_refuses_to_overwrite_malformed_settings(self):
        original = b'{"broken":'
        with open(settings_store.SETTINGS_PATH, "wb") as handle:
            handle.write(original)
        with self.assertRaises(RuntimeError):
            settings_store.get_session_secret()
        with open(settings_store.SETTINGS_PATH, "rb") as handle:
            self.assertEqual(handle.read(), original)


    def test_private_learning_queries_are_scoped_to_the_owner(self):
        alice, _ = create_member("alice")
        bob, _ = create_member("bob")
        paper_id = self._paper()
        add_to_reading_list(alice["id"], paper_id)
        add_paper_chat_message(alice["id"], paper_id, "user", "Alice private message")
        session_id = create_paper_quiz_session(alice["id"], paper_id, "quick3")
        add_paper_quiz_question(session_id, 1, "Private question")

        self.assertEqual(len(get_reading_list(alice["id"])), 1)
        self.assertEqual(get_reading_list(bob["id"]), [])
        self.assertEqual(len(get_paper_chat_messages(alice["id"], paper_id)), 1)
        self.assertEqual(get_paper_chat_messages(bob["id"], paper_id), [])
        self.assertIsNotNone(get_paper_quiz_session_detail(alice["id"], session_id, paper_id))
        self.assertIsNone(get_paper_quiz_session_detail(bob["id"], session_id, paper_id))

    def test_deleting_member_cascades_private_data_nulls_importer_and_preserves_audit_snapshot(self):
        member, _ = create_member("to-delete")
        paper_id = self._paper(importer=member["id"])
        add_to_reading_list(member["id"], paper_id)
        add_paper_chat_message(member["id"], paper_id, "user", "private")
        create_paper_quiz_session(member["id"], paper_id, "quick3")
        record_audit_event(member, "paper.imported", "paper", "2608.00001")

        self.assertTrue(delete_member(member["id"]))

        with connection.get_connection() as conn:
            counts = [
                conn.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id = ?", (member["id"],)).fetchone()[0]
                for table in ("reading_list", "paper_chat_messages", "paper_quiz_sessions")
            ]
            importer = conn.execute("SELECT imported_by_user_id FROM papers WHERE id = ?", (paper_id,)).fetchone()[0]
        events, _ = get_audit_events()
        self.assertEqual(counts, [0, 0, 0])
        self.assertIsNone(importer)
        self.assertEqual(events[0]["actor_username"], "to-delete")
        self.assertIsNone(events[0]["actor_user_id"])

    def test_admin_cannot_be_disabled_or_deleted_and_member_disable_revokes_sessions(self):
        member, _ = create_member("member-1")
        old_version = member["session_version"]
        self.assertFalse(set_member_enabled(self.admin["id"], False))
        self.assertFalse(delete_member(self.admin["id"]))
        self.assertTrue(set_member_enabled(member["id"], False))
        disabled = get_user_by_username("member-1")
        self.assertFalse(disabled["enabled"])
        self.assertGreater(disabled["session_version"], old_version)

    def test_async_progress_uses_a_separate_task_namespace_per_user(self):
        progress_store.clear()
        update_progress("private-task", {"status": "running"}, user_id=41)
        self.assertEqual(get_progress("private-task", user_id=41)["status"], "running")
        self.assertIsNone(get_progress("private-task", user_id=42))
        self.assertTrue(update_progress("private-task", {"status": "queued"}, user_id=42))
        self.assertEqual(get_progress("private-task", user_id=41)["status"], "running")
        self.assertEqual(get_progress("private-task", user_id=42)["status"], "queued")
        progress_store.clear()

    def test_user_can_start_after_another_users_terminal_task_with_the_same_id(self):
        progress_store.clear()
        try:
            for terminal_status in ("completed", "error"):
                with self.subTest(terminal_status=terminal_status):
                    task_id = f"shared-{terminal_status}"
                    self.assertTrue(update_progress(task_id, {"status": terminal_status}, user_id=41))
                    self.assertTrue(update_progress(task_id, {"status": "running"}, user_id=42))

                    self.assertEqual(get_progress(task_id, user_id=41)["status"], terminal_status)
                    self.assertEqual(get_progress(task_id, user_id=42)["status"], "running")
        finally:
            progress_store.clear()

    def test_terminal_progress_expires_after_ten_minutes(self):
        progress_store.clear()
        try:
            with patch("source.web.progress.time.time") as now:
                now.return_value = 1000
                update_progress("finished-task", {"status": "completed"}, user_id=41)

                now.return_value = 1599
                self.assertEqual(get_progress("finished-task", user_id=41)["status"], "completed")

                now.return_value = 1600
                self.assertIsNone(get_progress("finished-task", user_id=41))
        finally:
            progress_store.clear()

    def test_running_progress_does_not_expire_at_the_terminal_ttl(self):
        progress_store.clear()
        try:
            with patch("source.web.progress.time.time") as now:
                now.return_value = 1000
                update_progress("long-task", {"status": "running"}, user_id=41)

                now.return_value = 10000
                self.assertEqual(get_progress("long-task", user_id=41)["status"], "running")
        finally:
            progress_store.clear()

    def test_new_progress_update_sweeps_expired_terminal_entries(self):
        progress_store.clear()
        try:
            with patch("source.web.progress.time.time") as now:
                now.return_value = 1000
                update_progress("old-task", {"status": "error"}, user_id=41)

                now.return_value = 1600
                update_progress("new-task", {"status": "running"}, user_id=42)

                # Move the test clock back so get_progress cannot perform the
                # expiry itself; the earlier write must already have swept it.
                now.return_value = 1001
                self.assertIsNone(get_progress("old-task", user_id=41))
                self.assertEqual(get_progress("new-task", user_id=42)["status"], "running")
        finally:
            progress_store.clear()

    def test_audit_metadata_drops_secrets_and_private_content(self):
        record_audit_event(self.admin, "safe.test", "paper", "p1", {
            "field": "rating", "password": "never", "api_key": "never",
            "chat_content": "never", "answer": "never", "prompt": "never",
            "qa_analysis": "never",
            "safe_list": [{"nested_secret": "never"}, "tag"],
        })
        events, _ = get_audit_events()
        self.assertEqual(events[0]["metadata"], {"field": "rating", "safe_list": ["tag"]})
        serialized = json.dumps(events[0], ensure_ascii=False)
        self.assertNotIn("never", serialized)

    def test_audit_metadata_truncates_long_strings_and_limits_lists(self):
        from source.storage.users import AUDIT_LIST_MAX_ITEMS, AUDIT_STRING_MAX_LENGTH
        long_text = "x" * 1200
        record_audit_event(self.admin, "safe.test", "paper", "p2", {
            "long_field": long_text,
            "long_list": [long_text, "ok", {"nested": long_text}],
            "big_list": list(range(AUDIT_LIST_MAX_ITEMS + 50)),
            "nested_object": {"deep": {"password": "never"}},
        })
        events, _ = get_audit_events()
        metadata = events[0]["metadata"]
        self.assertEqual(len(metadata["long_field"]), AUDIT_STRING_MAX_LENGTH)
        self.assertEqual(len(metadata["long_list"]), 2)
        self.assertEqual(len(metadata["long_list"][0]), AUDIT_STRING_MAX_LENGTH)
        self.assertEqual(metadata["long_list"][1], "ok")
        self.assertEqual(len(metadata["big_list"]), AUDIT_LIST_MAX_ITEMS)
        self.assertNotIn("nested_object", metadata)

    def test_audit_table_never_contains_credentials_or_private_bodies(self):
        markers = {
            "password_hash": "s3cret-password",
            "chat_content": "private-chat-content",
            "answer_text": "quiz-answer-text",
            "prompt": "prompt-template",
            "api_key": "sk-live-api-key",
            "qa_analysis": "full-qa-body",
        }
        record_audit_event(self.admin, "safe.test", "paper", "p3", markers)
        record_audit_event(self.admin, "safe.test", "paper", "p3", {
            "before_qa_sha256": "abc123",
            "before_qa_length": 42,
            "fields": ["rating", "tags"],
            "note": "safe metadata",
        })
        with connection.get_connection() as conn:
            rows = conn.execute(
                "SELECT action, target_type, target_id, metadata_json FROM audit_events"
            ).fetchall()
        blob = json.dumps([dict(row) for row in rows], ensure_ascii=False)
        for marker in markers.values():
            self.assertNotIn(marker, blob)
        self.assertIn("before_qa_sha256", blob)
        self.assertIn("note", blob)

    def test_database_constraints_reject_second_admin_and_ownerless_private_rows(self):
        paper_id = self._paper()
        with self.assertRaises(sqlite3.IntegrityError), connection.get_connection() as conn:
            conn.execute("""
                INSERT INTO users (username, password_hash, role)
                VALUES ('admin2', 'hash', 'admin')
            """)
        with self.assertRaises(sqlite3.IntegrityError), connection.get_connection() as conn:
            conn.execute("INSERT INTO reading_list (paper_id) VALUES (?)", (paper_id,))


if __name__ == "__main__":
    unittest.main()
