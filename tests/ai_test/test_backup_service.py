"""
test_backup_service.py — 由 tests/test_ai_provider_config.py 拆分迁入（Q20），方法体逐字节一致。
"""

import unittest
import os
import json
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from unittest.mock import patch


class BackupServiceTests(unittest.TestCase):
    def test_database_file_sizes_include_wal_and_shm(self):
        import source.backups as backup
        from source.storage import get_database_file_sizes

        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "papers.db")
            with open(db_path, "wb") as f:
                f.write(b"a" * 10)
            with open(db_path + "-wal", "wb") as f:
                f.write(b"b" * 20)
            with open(db_path + "-shm", "wb") as f:
                f.write(b"c" * 30)

            sizes = get_database_file_sizes(db_path)

        self.assertEqual(sizes["total_bytes"], 60)
        self.assertEqual([f["name"] for f in sizes["files"]], ["papers.db", "papers.db-wal", "papers.db-shm"])

    def test_backup_archive_contains_database_settings_and_manifest(self):
        import source.backups as backup

        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "papers.db")
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE demo (id INTEGER PRIMARY KEY, name TEXT)")
            conn.execute("INSERT INTO demo (name) VALUES ('paper')")
            conn.commit()
            conn.close()

            settings_path = os.path.join(tmp, "settings.json")
            with open(settings_path, "w", encoding="utf-8") as f:
                json.dump({"api_key": "secret"}, f)

            archive_path = os.path.join(tmp, "backup.zip")
            manifest = backup.create_backup_archive(
                archive_path,
                db_path=db_path,
                settings_path=settings_path,
            )

            with zipfile.ZipFile(archive_path, "r") as zf:
                names = set(zf.namelist())
                extract_dir = os.path.join(tmp, "extract")
                zf.extract("papers.db", extract_dir)
                manifest_data = json.loads(zf.read("manifest.json").decode("utf-8"))

            copied = sqlite3.connect(os.path.join(extract_dir, "papers.db"))
            row = copied.execute("SELECT name FROM demo").fetchone()
            copied.close()

        self.assertIn("papers.db", names)
        self.assertIn("settings.json", names)
        self.assertIn("manifest.json", names)
        self.assertEqual(names, {"papers.db", "settings.json", "manifest.json"})
        self.assertEqual(row[0], "paper")
        self.assertTrue(manifest["included"]["settings_json"])
        self.assertNotIn("output_dir", manifest_data["included"])
        self.assertTrue(manifest_data["privacy"]["contains_accounts"])
        self.assertTrue(manifest_data["privacy"]["contains_private_learning_data"])
        self.assertTrue(manifest_data["privacy"]["trusted_storage_required"])
        self.assertFalse(manifest_data["privacy"]["client_side_encryption"])
        self.assertNotIn("report_files", manifest_data)

    def test_backup_restores_v4_users_ownership_and_private_records(self):
        import source.backups as backup
        from source.settings import store as settings_store
        from source.storage import connection as db_connection
        from source.storage import (
            add_paper_chat_message,
            add_paper_quiz_attempt,
            add_paper_quiz_questions,
            add_to_reading_list,
            create_member,
            create_paper_quiz_session,
            init_db,
            insert_paper,
            record_ai_usage,
        )

        original_db_path = db_connection.DB_PATH
        original_settings_path = settings_store.SETTINGS_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                db_path = os.path.join(tmp, "papers.db")
                settings_path = os.path.join(tmp, "settings.json")
                db_connection.DB_PATH = db_path
                settings_store.SETTINGS_PATH = settings_path
                with open(settings_path, "w", encoding="utf-8") as handle:
                    json.dump({
                        "settings_schema_version": 4,
                        "session_secret": "backup-secret",
                        "concurrency": 7,
                    }, handle)
                init_db()
                member, _ = create_member("alice")
                paper_id = insert_paper({
                    "paper_key": "2608.00001", "arxiv_id": "2608.00001",
                    "source_type": "arxiv", "source_id": "2608.00001",
                    "ingest_mode": "manual", "imported_by_user_id": member["id"],
                    "title": "Paper", "authors": ["Alice"], "abstract": "Abstract",
                    "categories": ["cs.RO"],
                })
                add_to_reading_list(member["id"], paper_id)
                add_paper_chat_message(member["id"], paper_id, "user", "private chat")
                session_id = create_paper_quiz_session(member["id"], paper_id, "quick3")
                question_ids = add_paper_quiz_questions(session_id, [
                    {"question": "Q1?", "expected_points": ["A"]},
                ])
                add_paper_quiz_attempt(question_ids[0], "my answer", 4, {"feedback": "ok"})
                record_ai_usage({
                    "task_key": "basic_analysis",
                    "provider_key": "deepseek",
                    "provider_name": "DeepSeek",
                    "model": "deepseek-chat",
                    "arxiv_id": "2608.00001",
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                    "cached_tokens": 0,
                    "cache_miss_tokens": 15,
                    "user_id": member["id"],
                })

                archive_path = os.path.join(tmp, "backup.zip")
                backup.create_backup_archive(
                    archive_path, db_path=db_path, settings_path=settings_path,
                )

                extract_dir = os.path.join(tmp, "extract")
                with zipfile.ZipFile(archive_path, "r") as zf:
                    zf.extract("papers.db", extract_dir)
                    archived_settings = zf.read("settings.json")
                with open(settings_path, "rb") as handle:
                    original_settings = handle.read()

                copied = sqlite3.connect(os.path.join(extract_dir, "papers.db"))
                try:
                    users = copied.execute(
                        "SELECT username, role FROM users ORDER BY username"
                    ).fetchall()
                    reading = copied.execute(
                        "SELECT user_id, paper_id FROM reading_list"
                    ).fetchall()
                    chat = copied.execute(
                        "SELECT user_id, paper_id, content FROM paper_chat_messages"
                    ).fetchall()
                    sessions = copied.execute(
                        "SELECT user_id, paper_id, mode FROM paper_quiz_sessions"
                    ).fetchall()
                    attempts = copied.execute(
                        "SELECT COUNT(*) FROM paper_quiz_attempts"
                    ).fetchone()[0]
                    importer = copied.execute(
                        "SELECT imported_by_user_id FROM papers WHERE id = ?", (paper_id,)
                    ).fetchone()[0]
                    usage = copied.execute(
                        "SELECT user_id, total_tokens FROM ai_usage_logs"
                    ).fetchall()
                finally:
                    copied.close()

                self.assertEqual(users, [("admin", "admin"), ("alice", "member")])
                self.assertEqual(reading, [(member["id"], paper_id)])
                self.assertEqual(chat, [(member["id"], paper_id, "private chat")])
                self.assertEqual(sessions, [(member["id"], paper_id, "quick3")])
                self.assertEqual(attempts, 1)
                self.assertEqual(importer, member["id"])
                self.assertEqual(usage, [(member["id"], 15)])
                self.assertEqual(archived_settings, original_settings)
                self.assertIn(b'"concurrency": 7', archived_settings)
        finally:
            db_connection.DB_PATH = original_db_path
            settings_store.SETTINGS_PATH = original_settings_path

    def test_webdav_backup_uploads_latest_and_cleans_expired_history(self):
        import source.backups as backup

        class FakeResponse:
            def __init__(self, status_code=200, content=b""):
                self.status_code = status_code
                self.content = content

            def raise_for_status(self):
                raise AssertionError(f"unexpected status {self.status_code}")

        propfind_xml = b"""<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
  <d:response><d:href>/dav/arxiv/arxiv-backup-20260610-120000.zip</d:href></d:response>
  <d:response><d:href>/dav/arxiv/arxiv-backup-20260614-120000.zip</d:href></d:response>
  <d:response><d:href>/dav/arxiv/arxiv-backup-latest.zip</d:href></d:response>
</d:multistatus>"""

        calls = []

        def fake_request(method, url, **kwargs):
            calls.append((method, url, kwargs))
            if method == "PROPFIND":
                return FakeResponse(207, propfind_xml)
            if method == "MKCOL":
                return FakeResponse(201)
            if method == "PUT":
                return FakeResponse(201)
            if method == "DELETE":
                return FakeResponse(204)
            return FakeResponse(200)

        def fake_archive(path, **kwargs):
            with open(path, "wb") as f:
                f.write(b"zip")
            return {
                "archive": {"size_bytes": 3, "size": "3 B"},
                "database": {"size_bytes": 2, "size": "2 B"},
            }

        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(backup, "DB_DIR", tmp), \
             patch.object(backup, "create_backup_archive", side_effect=fake_archive), \
             patch.object(backup.requests, "request", side_effect=fake_request):
            result = backup.run_webdav_backup(
                config={
                    "enabled": False,
                    "url": "https://dav.example.com",
                    "username": "alice",
                    "password": "secret",
                    "remote_dir": "arxiv",
                    "history_days": 3,
                },
                force=True,
                record_status=False,
                now=datetime(2026, 6, 15, 12, 0, 0),
            )

        methods = [call[0] for call in calls]
        self.assertEqual(methods.count("PUT"), 2)
        self.assertIn("MKCOL", methods)
        self.assertIn("PROPFIND", methods)
        self.assertIn("DELETE", methods)
        self.assertEqual(result["uploaded_files"], [
            "arxiv-backup-20260615-120000.zip",
            "arxiv-backup-latest.zip",
        ])
        self.assertEqual(result["deleted_files"], ["arxiv-backup-20260610-120000.zip"])


if __name__ == "__main__":
    unittest.main()
