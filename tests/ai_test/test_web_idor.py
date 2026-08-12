"""
test_web_idor.py — endpoint-level IDOR: members can only read/write their own
private learning data through the web APIs, regardless of resource ids.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from . import common
from .common import install_import_stubs, setup_web_test_base, teardown_web_test_base
from source.settings import store as settings_store
from source.storage import connection
from source.storage import (
    add_paper_chat_message,
    add_paper_quiz_attempt,
    add_paper_quiz_question,
    add_paper_quiz_questions,
    add_to_reading_list,
    create_member,
    create_paper_quiz_session,
    get_reading_list,
    init_db,
    insert_paper,
)


class WebIdorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_import_stubs()
        import app as app_module
        cls.app_module = app_module
        setup_web_test_base()
        cls.app = common._WEB_APP
        cls.web_learning_api = common.web_learning_api
        cls.web_papers_api = common.web_papers_api

    @classmethod
    def tearDownClass(cls):
        teardown_web_test_base()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db_path = connection.DB_PATH
        self.original_settings_path = settings_store.SETTINGS_PATH
        connection.DB_PATH = os.path.join(self.tmp.name, "papers.db")
        settings_store.SETTINGS_PATH = os.path.join(self.tmp.name, "settings.json")
        with open(settings_store.SETTINGS_PATH, "w", encoding="utf-8") as handle:
            json.dump({"settings_schema_version": 4}, handle)
        init_db()
        self.alice, _ = create_member("alice")
        self.bob, _ = create_member("bob")
        self.paper_a = insert_paper({
            "paper_key": "2608.00101", "arxiv_id": "2608.00101", "source_type": "arxiv",
            "source_id": "2608.00101", "ingest_mode": "feed",
            "title": "Alice Paper", "authors": [], "abstract": "Abstract", "categories": [],
        })
        self.paper_b = insert_paper({
            "paper_key": "2608.00102", "arxiv_id": "2608.00102", "source_type": "arxiv",
            "source_id": "2608.00102", "ingest_mode": "feed",
            "title": "Bob Paper", "authors": [], "abstract": "Abstract", "categories": [],
        })
        add_to_reading_list(self.alice["id"], self.paper_a)
        add_to_reading_list(self.bob["id"], self.paper_b)
        add_paper_chat_message(self.alice["id"], self.paper_a, "user", "alice private chat")
        add_paper_chat_message(self.bob["id"], self.paper_b, "user", "bob private chat")
        self.quiz_a = create_paper_quiz_session(self.alice["id"], self.paper_a, "quick3")
        self.question_a = add_paper_quiz_questions(self.quiz_a, [
            {"question": "alice private question?", "expected_points": ["A"]},
        ])[0]
        add_paper_quiz_attempt(self.question_a, "alice answer", 4, {"feedback": "ok"})
        self.socratic_a = create_paper_quiz_session(self.alice["id"], self.paper_a, "socratic")
        add_paper_quiz_question(self.socratic_a, 1, "alice socratic question?", "socratic")
        self.quiz_b = create_paper_quiz_session(self.bob["id"], self.paper_b, "quick3")
        add_paper_quiz_questions(self.quiz_b, [{"question": "bob question?", "expected_points": ["B"]}])

    def tearDown(self):
        connection.DB_PATH = self.original_db_path
        settings_store.SETTINGS_PATH = self.original_settings_path
        self.tmp.cleanup()

    def _ai_stubs(self):
        return patch.multiple(
            self.web_learning_api,
            chat_about_paper=lambda paper, message, history=None: (
                "model reply", None, {"tokens": 0}),
            generate_paper_quiz=lambda paper, mode="quick3": (
                [{"question": "generated?"}], None, {"tokens": 0}),
            grade_quiz_answer=lambda paper, question, answer: (
                {"score": 4, "feedback": "good"}, None, {"tokens": 0}),
            socratic_reply=lambda paper, session_history=None, user_answer=None: (
                {"next_question": "next?", "score": 0}, None, {"tokens": 0}),
        )

    def test_bob_cannot_read_alice_chat_or_quiz_resources(self):
        with self._ai_stubs(), self.app.test_request_context("/"):
            with patch.object(self.web_learning_api, "current_user", return_value=self.bob):
                response = self.web_learning_api.api_paper_chat_messages("2608.00101")
                response_quiz, status = self.web_learning_api.api_get_quiz_session(
                    "2608.00101", self.quiz_a
                )
            self.assertEqual(response["messages"], [])
            self.assertEqual(status, 404)

    def test_bob_cannot_answer_alice_question_or_reply_to_her_socratic_session(self):
        with self._ai_stubs(), self.app.test_request_context("/"):
            with patch.object(self.web_learning_api, "current_user", return_value=self.bob):
                response, status = self.web_learning_api.api_answer_quiz_question(
                    "2608.00101", self.question_a
                )
                self.assertEqual(status, 404)
                response, status = self.web_learning_api.api_reply_socratic_session(
                    "2608.00101", self.socratic_a
                )
                self.assertEqual(status, 404)

        with connection.get_connection() as conn:
            attempts = conn.execute(
                "SELECT COUNT(*) FROM paper_quiz_attempts WHERE question_id = ?",
                (self.question_a,),
            ).fetchone()[0]
        self.assertEqual(attempts, 1, "alice's attempt count must be unchanged")

    def test_bob_chat_send_never_touches_alice_history(self):
        with self._ai_stubs(), self.app.test_request_context(
            "/api/paper/2608.00101/chat/messages", method="POST", json={"message": "hi"}
        ):
            with patch.object(self.web_learning_api, "current_user", return_value=self.bob):
                response = self.web_learning_api.api_paper_chat_send("2608.00101")
        self.assertEqual(response["status"], "ok")
        with connection.get_connection() as conn:
            alice_msgs = conn.execute(
                "SELECT COUNT(*) FROM paper_chat_messages WHERE paper_id = ? AND user_id = ?",
                (self.paper_a, self.alice["id"]),
            ).fetchone()[0]
            bob_msgs = conn.execute(
                "SELECT COUNT(*) FROM paper_chat_messages WHERE paper_id = ? AND user_id = ?",
                (self.paper_a, self.bob["id"]),
            ).fetchone()[0]
        self.assertEqual(alice_msgs, 1)
        self.assertEqual(bob_msgs, 2)

    def test_reading_list_endpoints_are_scoped_to_the_principal(self):
        with self.app.test_request_context("/"):
            with patch.object(self.web_papers_api, "current_user", return_value=self.bob):
                response = self.web_papers_api.api_todo_status("2608.00101")
            self.assertFalse(response["in_reading_list"])
            with patch.object(self.web_papers_api, "current_user", return_value=self.bob):
                papers = self.web_papers_api.api_reading_list()
        paper_keys = [item["paper_key"] for item in papers["papers"]]
        self.assertNotIn("2608.00101", paper_keys)
        self.assertIn("2608.00102", paper_keys)
        self.assertEqual(len(get_reading_list(self.bob["id"])), 1)

    def test_owner_controls_still_work_unchanged(self):
        with self._ai_stubs(), self.app.test_request_context("/"):
            with patch.object(self.web_learning_api, "current_user", return_value=self.alice):
                messages = self.web_learning_api.api_paper_chat_messages("2608.00101")
                session = self.web_learning_api.api_get_quiz_session("2608.00101", self.quiz_a)
            self.assertEqual(len(messages["messages"]), 1)
            self.assertEqual(session["status"], "ok")
            self.assertEqual(session["session"]["mode"], "quick3")
            with patch.object(self.web_papers_api, "current_user", return_value=self.alice):
                todo = self.web_papers_api.api_todo_status("2608.00101")
            self.assertTrue(todo["in_reading_list"])

    def test_progress_endpoint_injects_principal_and_never_other_users_entries(self):
        from source.web.progress import get_progress, progress_store, update_progress
        web_tasks_api = common.web_tasks_api
        progress_store.clear()
        try:
            update_progress("shared-task", {"status": "running"}, user_id=self.alice["id"])
            terminal = {"status": "completed", "message": "done"}
            with self.app.test_request_context("/api/progress/shared-task"):
                with patch.object(web_tasks_api, "current_user", return_value=self.bob), \
                     patch.object(web_tasks_api, "get_progress", return_value=terminal) as getter:
                    response = web_tasks_api.api_progress("shared-task")
                    events = list(response.response)
                self.assertEqual(response.mimetype, "text/event-stream")
                getter.assert_called_once_with("shared-task", user_id=self.bob["id"])
            self.assertIn("completed", events[0])
            self.assertIsNone(get_progress("shared-task", user_id=self.bob["id"]))
        finally:
            progress_store.clear()


if __name__ == "__main__":
    unittest.main()
