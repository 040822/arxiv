import os
import sqlite3
import tempfile
import unittest

from source.storage import connection


class ManagedConnectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db_path = connection.DB_PATH
        connection.DB_PATH = os.path.join(self.tmp.name, "papers.db")

    def tearDown(self):
        connection.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    def test_context_commits_and_closes_connection(self):
        with connection.get_connection() as conn:
            conn.execute("CREATE TABLE sample (value TEXT)")
            conn.execute("INSERT INTO sample (value) VALUES ('saved')")
            managed = conn

        with self.assertRaises(sqlite3.ProgrammingError):
            managed.execute("SELECT 1")

        reader = sqlite3.connect(connection.DB_PATH)
        try:
            self.assertEqual(reader.execute("SELECT value FROM sample").fetchone()[0], "saved")
        finally:
            reader.close()


    def test_context_rolls_back_and_closes_on_exception(self):
        with connection.get_connection() as conn:
            conn.execute("CREATE TABLE sample (value TEXT)")

        managed = None
        with self.assertRaisesRegex(RuntimeError, "boom"):
            with connection.get_connection() as conn:
                managed = conn
                conn.execute("INSERT INTO sample (value) VALUES ('discarded')")
                raise RuntimeError("boom")

        with self.assertRaises(sqlite3.ProgrammingError):
            managed.execute("SELECT 1")

        reader = sqlite3.connect(connection.DB_PATH)
        try:
            self.assertEqual(reader.execute("SELECT COUNT(*) FROM sample").fetchone()[0], 0)
        finally:
            reader.close()


    def test_connection_configures_concurrency_pragmas(self):
        with connection.get_connection() as conn:
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 5000)
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")


if __name__ == "__main__":
    unittest.main()
