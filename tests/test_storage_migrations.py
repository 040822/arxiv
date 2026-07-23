import os
import sqlite3
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
