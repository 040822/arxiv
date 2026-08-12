"""
test_ai_usage_and_learning.py — 由 tests/test_ai_provider_config.py 拆分迁入（Q20），方法体逐字节一致。
"""

import unittest
import os
import tempfile

from source.storage import connection as db_connection
from source.storage import (
    add_paper_chat_message,
    add_paper_quiz_attempt,
    add_paper_quiz_questions,
    create_paper_quiz_session,
    delete_paper,
    get_ai_usage_summary,
    get_connection,
    get_latest_paper_quiz_sessions,
    get_paper_chat_messages,
    get_paper_quiz_session_detail,
    init_db,
    insert_paper,
    record_ai_usage,
)


class AiUsageAndLearningTests(unittest.TestCase):
    def test_ai_usage_log_records_and_summarizes_tokens(self):

        original_dir = db_connection.DB_DIR
        original_path = db_connection.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            db_connection.DB_DIR = tmp
            db_connection.DB_PATH = os.path.join(tmp, "papers.db")
            init_db()
            record_ai_usage({
                "task_key": "basic_analysis",
                "provider_key": "cheap",
                "provider_name": "Cheap",
                "model": "cheap-model",
                "arxiv_id": "2601.00001",
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "cached_tokens": 50,
                "cache_miss_tokens": 50,
            })
            record_ai_usage({
                "task_key": "deep_reading",
                "provider_key": "smart",
                "provider_name": "Smart",
                "model": "smart-model",
                "arxiv_id": "2601.00002",
                "prompt_tokens": 50,
                "completion_tokens": 30,
                "total_tokens": 80,
                "cached_tokens": 80,
                "cache_miss_tokens": 0,
            })
            summary = get_ai_usage_summary(days=7)
            summary_by_model = get_ai_usage_summary(days=7, group_by="model")

        db_connection.DB_DIR = original_dir
        db_connection.DB_PATH = original_path

        self.assertEqual(summary["items"][0]["task_key"], "basic_analysis")
        self.assertEqual(summary["items"][0]["total_tokens"], 120)
        self.assertEqual(summary["items"][0]["cached_tokens"], 50)
        self.assertEqual(summary["items"][0]["cache_miss_tokens"], 50)
        self.assertEqual(summary["group_by"], "task")
        self.assertEqual(len(summary["dates"]), 7)
        self.assertEqual(summary["totals"]["total_tokens"], 200)
        self.assertEqual(summary["totals"]["cache_miss_tokens"], 50)
        self.assertEqual(summary["groups"][0]["key"], "basic_analysis")
        self.assertEqual(len(summary["groups"][0]["points"]), 7)
        self.assertTrue(any(point["total_tokens"] == 120 for point in summary["groups"][0]["points"]))
        self.assertEqual(summary_by_model["group_by"], "model")
        self.assertEqual(summary_by_model["groups"][0]["key"], "cheap-model")
        self.assertEqual(len(summary_by_model["groups"]), 2)
        self.assertEqual(sum(group["total_tokens"] for group in summary_by_model["groups"]), 200)
        self.assertTrue(any(group["key"] == "smart-model" and group["cached_tokens"] == 80 for group in summary_by_model["groups"]))

    def test_paper_learning_tables_store_history_and_cascade_delete(self):

        original_dir = db_connection.DB_DIR
        original_path = db_connection.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            db_connection.DB_DIR = tmp
            db_connection.DB_PATH = os.path.join(tmp, "papers.db")
            init_db()
            paper_id = insert_paper({
                "arxiv_id": "2601.00001",
                "title": "Learning Paper",
                "authors": ["Alice"],
                "abstract": "abstract",
                "categories": ["cs.RO"],
                "primary_category": "cs.RO",
                "url": "https://arxiv.org/abs/2601.00001",
                "pdf_url": "https://arxiv.org/pdf/2601.00001",
                "published_date": "2026-01-01",
                "updated_date": "2026-01-01",
            })

            with get_connection() as conn:
                admin_id = conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]

            add_paper_chat_message(admin_id, paper_id, "user", "问题")
            add_paper_chat_message(admin_id, paper_id, "assistant", "回答")
            session_id = create_paper_quiz_session(admin_id, paper_id, "quick3")
            question_ids = add_paper_quiz_questions(session_id, [
                {"question": "Q1?", "expected_points": ["A"]},
                {"question": "Q2?", "expected_points": ["B"]},
            ])
            add_paper_quiz_attempt(question_ids[0], "我的答案", 4, {"feedback": "不错"})
            add_paper_quiz_attempt(question_ids[0], "第二版答案", 5, {"feedback": "更好"})

            messages = get_paper_chat_messages(admin_id, paper_id)
            session = get_paper_quiz_session_detail(admin_id, session_id, paper_id=paper_id)
            latest_sessions = get_latest_paper_quiz_sessions(admin_id, paper_id)

            delete_paper("2601.00001")
            conn = get_connection()
            counts = {
                "chat": conn.execute("SELECT COUNT(*) FROM paper_chat_messages").fetchone()[0],
                "sessions": conn.execute("SELECT COUNT(*) FROM paper_quiz_sessions").fetchone()[0],
                "questions": conn.execute("SELECT COUNT(*) FROM paper_quiz_questions").fetchone()[0],
                "attempts": conn.execute("SELECT COUNT(*) FROM paper_quiz_attempts").fetchone()[0],
            }
            conn.close()

        db_connection.DB_DIR = original_dir
        db_connection.DB_PATH = original_path

        self.assertEqual([m["role"] for m in messages], ["user", "assistant"])
        self.assertEqual(len(session["questions"]), 2)
        self.assertEqual(session["questions"][0]["feedback"], {"feedback": "更好"})
        self.assertEqual(latest_sessions[0]["question_count"], 2)
        self.assertEqual(latest_sessions[0]["attempt_count"], 2)
        self.assertEqual(counts, {"chat": 0, "sessions": 0, "questions": 0, "attempts": 0})


if __name__ == "__main__":
    unittest.main()
