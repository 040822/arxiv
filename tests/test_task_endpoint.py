import os
import tempfile
import unittest

from flask import Flask

from source.storage import get_task_logs, init_db
from source.storage import connection as db_connection
from source.web.task_endpoint import TaskEndpointResult, task_endpoint


class TaskEndpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db_dir = db_connection.DB_DIR
        self.original_db_path = db_connection.DB_PATH
        db_connection.DB_DIR = self.tmp.name
        db_connection.DB_PATH = os.path.join(self.tmp.name, "papers.db")
        init_db()
        self.app = Flask(__name__)

    def tearDown(self):
        db_connection.DB_DIR = self.original_db_dir
        db_connection.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    def test_success_response_finishes_one_task_log(self):
        @self.app.post("/success")
        @task_endpoint("sample", "starting")
        def success():
            return TaskEndpointResult.ok(
                "finished",
                payload={"value": 7},
                detail="value=7",
            )

        response = self.app.test_client().post("/success")
        logs, total = get_task_logs(task_name="sample")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {
            "status": "ok",
            "message": "finished",
            "value": 7,
        })
        self.assertEqual(total, 1)
        self.assertEqual(logs[0]["status"], "success")
        self.assertEqual(logs[0]["message"], "finished")
        self.assertEqual(logs[0]["detail"], "value=7")


    def test_expected_error_preserves_http_response_and_log_message(self):
        @self.app.post("/expected-error")
        @task_endpoint("sample", "starting")
        def expected_error():
            return TaskEndpointResult.error(
                "bad request",
                http_status=400,
                log_message="invalid input",
            )

        response = self.app.test_client().post("/expected-error")
        logs, total = get_task_logs(task_name="sample")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json(), {
            "status": "error",
            "message": "bad request",
        })
        self.assertEqual(total, 1)
        self.assertEqual(logs[0]["status"], "error")
        self.assertEqual(logs[0]["message"], "invalid input")


    def test_unhandled_exception_returns_500_and_closes_task_log(self):
        @self.app.post("/exception")
        @task_endpoint("sample", "starting")
        def exception():
            raise RuntimeError("boom")

        response = self.app.test_client().post("/exception")
        logs, total = get_task_logs(task_name="sample")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json(), {
            "status": "error",
            "message": "boom",
        })
        self.assertEqual(total, 1)
        self.assertEqual(logs[0]["status"], "error")
        self.assertEqual(logs[0]["message"], "boom")
        self.assertIsNotNone(logs[0]["finished_at"])

    def test_invalid_handler_return_is_logged_as_error(self):
        @self.app.post("/invalid-result")
        @task_endpoint("sample", "starting")
        def invalid_result():
            return {"status": "ok"}

        response = self.app.test_client().post("/invalid-result")
        logs, total = get_task_logs(task_name="sample")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(total, 1)
        self.assertEqual(logs[0]["status"], "error")
        self.assertIn("must return TaskEndpointResult", logs[0]["message"])


if __name__ == "__main__":
    unittest.main()
