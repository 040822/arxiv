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
        import backup

        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "papers.db")
            with open(db_path, "wb") as f:
                f.write(b"a" * 10)
            with open(db_path + "-wal", "wb") as f:
                f.write(b"b" * 20)
            with open(db_path + "-shm", "wb") as f:
                f.write(b"c" * 30)

            sizes = backup.get_database_file_sizes(db_path)

        self.assertEqual(sizes["total_bytes"], 60)
        self.assertEqual([f["name"] for f in sizes["files"]], ["papers.db", "papers.db-wal", "papers.db-shm"])

    def test_backup_archive_contains_database_settings_and_manifest(self):
        import backup

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
        self.assertNotIn("report_files", manifest_data)

    def test_webdav_backup_uploads_latest_and_cleans_expired_history(self):
        import backup

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
