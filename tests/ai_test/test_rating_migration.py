"""
test_rating_migration.py — 由 tests/test_ai_provider_config.py 拆分迁入（Q20），方法体逐字节一致。
"""

import unittest
import os
import sqlite3
import tempfile

from source.storage import connection as db_connection
from source.storage import (
    get_analyzed_count,
    get_unanalyzed_count,
    get_unanalyzed_papers,
    init_db,
    insert_analysis,
    insert_paper,
    update_analysis,
)


class RatingMigrationTests(unittest.TestCase):
    def test_rating_migration_restores_legacy_ai_rating_once(self):
        import sqlite3

        original_dir = db_connection.DB_DIR
        original_path = db_connection.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            db_connection.DB_DIR = tmp
            db_connection.DB_PATH = os.path.join(tmp, "papers.db")
            conn = sqlite3.connect(db_connection.DB_PATH)
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
            conn.execute("INSERT INTO analysis (paper_id, rating) VALUES (1, 3)")
            conn.execute("INSERT INTO analysis (paper_id, rating) VALUES (2, 5)")
            conn.commit()
            conn.close()

            init_db()
            conn = sqlite3.connect(db_connection.DB_PATH)
            rows = conn.execute(
                "SELECT id, rating, legacy_ai_rating, rating_restored_from_legacy FROM analysis ORDER BY id"
            ).fetchall()
            self.assertEqual(rows, [(1, 3, 3, 1), (2, 5, 5, 1)])

            conn.execute("UPDATE analysis SET rating = 4 WHERE id = 1")
            conn.commit()
            conn.close()

            init_db()
            conn = sqlite3.connect(db_connection.DB_PATH)
            rows = conn.execute(
                "SELECT id, rating, legacy_ai_rating, rating_restored_from_legacy FROM analysis ORDER BY id"
            ).fetchall()
            conn.close()

        db_connection.DB_DIR = original_dir
        db_connection.DB_PATH = original_path
        self.assertEqual(rows, [(1, 4, 3, 1), (2, 5, 5, 1)])

    def test_rating_restore_overwrites_current_rating_when_legacy_exists(self):
        import sqlite3

        original_dir = db_connection.DB_DIR
        original_path = db_connection.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            db_connection.DB_DIR = tmp
            db_connection.DB_PATH = os.path.join(tmp, "papers.db")
            conn = sqlite3.connect(db_connection.DB_PATH)
            conn.execute("""
                CREATE TABLE analysis (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    paper_id INTEGER NOT NULL,
                    tags TEXT,
                    summary_cn TEXT,
                    summary_en TEXT,
                    rating INTEGER DEFAULT 0,
                    legacy_ai_rating INTEGER,
                    value_comment TEXT,
                    analyzed_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("INSERT INTO analysis (paper_id, rating, legacy_ai_rating) VALUES (1, 1, 4)")
            conn.commit()
            conn.close()

            init_db()
            conn = sqlite3.connect(db_connection.DB_PATH)
            row = conn.execute(
                "SELECT rating, legacy_ai_rating, rating_restored_from_legacy FROM analysis WHERE paper_id = 1"
            ).fetchone()
            conn.execute("UPDATE analysis SET rating = 2 WHERE paper_id = 1")
            conn.commit()
            conn.close()

            init_db()
            conn = sqlite3.connect(db_connection.DB_PATH)
            row_after_second_init = conn.execute(
                "SELECT rating, legacy_ai_rating, rating_restored_from_legacy FROM analysis WHERE paper_id = 1"
            ).fetchone()
            conn.close()

        db_connection.DB_DIR = original_dir
        db_connection.DB_PATH = original_path
        self.assertEqual(row, (4, 4, 1))
        self.assertEqual(row_after_second_init, (2, 4, 1))

    def test_new_analysis_defaults_to_zero_manual_rating_without_legacy_backup(self):
        import sqlite3

        original_dir = db_connection.DB_DIR
        original_path = db_connection.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            db_connection.DB_DIR = tmp
            db_connection.DB_PATH = os.path.join(tmp, "papers.db")
            init_db()
            paper_id = insert_paper({
                "arxiv_id": "2601.00000",
                "title": "Manual Rating Paper",
                "authors": ["Alice"],
                "abstract": "abstract",
                "categories": ["cs.RO"],
                "primary_category": "cs.RO",
                "url": "https://arxiv.org/abs/2601.00000",
                "pdf_url": "https://arxiv.org/pdf/2601.00000",
                "published_date": "2026-01-01",
                "updated_date": "2026-01-01",
            })
            insert_analysis(paper_id, {
                "tags": ["Robot"],
                "summary_cn": "摘要",
                "summary_en": "",
                "value_comment": "评价",
                "qa_analysis": "",
            })
            conn = sqlite3.connect(db_connection.DB_PATH)
            row = conn.execute("SELECT rating, legacy_ai_rating FROM analysis WHERE paper_id = ?", (paper_id,)).fetchone()
            conn.close()

        db_connection.DB_DIR = original_dir
        db_connection.DB_PATH = original_path
        self.assertEqual(row, (0, None))

    def test_manual_rating_only_record_is_still_pending_basic_analysis(self):

        original_dir = db_connection.DB_DIR
        original_path = db_connection.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            db_connection.DB_DIR = tmp
            db_connection.DB_PATH = os.path.join(tmp, "papers.db")
            init_db()
            paper_id = insert_paper({
                "arxiv_id": "2601.00007",
                "title": "Manual Rated Pending",
                "authors": ["Alice"],
                "abstract": "abstract",
                "categories": ["cs.RO"],
                "primary_category": "cs.RO",
                "url": "https://arxiv.org/abs/2601.00007",
                "pdf_url": "https://arxiv.org/pdf/2601.00007",
                "published_date": "2026-01-01",
                "updated_date": "2026-01-01",
            })
            update_analysis(paper_id, {"rating": 4})
            pending = get_unanalyzed_papers(limit=10)
            pending_count = get_unanalyzed_count()
            analyzed_count = get_analyzed_count()

        db_connection.DB_DIR = original_dir
        db_connection.DB_PATH = original_path
        self.assertEqual([p["arxiv_id"] for p in pending], ["2601.00007"])
        self.assertEqual(pending_count, 1)
        self.assertEqual(analyzed_count, 0)


if __name__ == "__main__":
    unittest.main()
