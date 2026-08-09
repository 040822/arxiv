"""
test_schedule_retry.py — 由旧测试拆分迁入，并包含调度重试与生命周期回归测试。
"""

import unittest
import os
import json
import importlib
import tempfile
import sys
import types
from unittest.mock import patch

from source.settings import store as settings_store
from .common import (
    setup_web_test_base,
    teardown_web_test_base,
)


pipeline_orchestrator = None
pipeline_scheduler = None
web_application = None
web_auth = None
web_learning_api = None
web_pages = None
web_papers_api = None
web_providers_api = None
web_settings_api = None
web_tasks_api = None

class ScheduleRetryTests(unittest.TestCase):
    def tearDown(self):
        teardown_web_test_base()

    def import_app_with_temp_settings(self, tmp):

        settings_store.SETTINGS_PATH = os.path.join(tmp, "settings.json")
        sys.modules.pop("app", None)
        app_module = importlib.import_module("app")
        global pipeline_orchestrator, pipeline_scheduler
        pipeline_orchestrator = importlib.import_module("source.pipeline.orchestrator")
        pipeline_scheduler = importlib.import_module("source.pipeline.scheduler")
        global web_application, web_auth, web_learning_api, web_pages
        global web_papers_api, web_providers_api, web_settings_api, web_tasks_api
        web_application = importlib.import_module("source.web.application")
        web_auth = importlib.import_module("source.web.auth")
        web_learning_api = importlib.import_module("source.web.learning_api")
        web_pages = importlib.import_module("source.web.pages")
        web_papers_api = importlib.import_module("source.web.papers_api")
        web_providers_api = importlib.import_module("source.web.providers_api")
        web_settings_api = importlib.import_module("source.web.settings_api")
        web_tasks_api = importlib.import_module("source.web.tasks_api")
        setup_web_test_base()
        return app_module

    def test_schedule_api_saves_and_returns_fetch_retry_config(self):

        original_path = settings_store.SETTINGS_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                self.import_app_with_temp_settings(tmp)
                payload = {
                    "enabled": True,
                    "days_of_week": ["mon", "fri"],
                    "hour": 8,
                    "minute": 15,
                    "fetch_days": 5,
                    "analyze_limit": 500,
                    "fetch_retry_interval_minutes": 12,
                    "fetch_max_retries": 25,
                }
                with patch.object(web_settings_api, "configure_daily_job"), \
                        patch.object(web_settings_api, "scheduler", types.SimpleNamespace(running=True)), \
                        patch.object(web_settings_api, "request", types.SimpleNamespace(get_json=lambda: payload)):
                    response = web_settings_api.api_save_schedule_config()
                    self.assertEqual(response["status"], "ok")

                    data = web_settings_api.api_get_schedule_config()

            self.assertEqual(data["fetch_retry_interval_minutes"], 12)
            self.assertEqual(data["fetch_max_retries"], 25)
        finally:
            sys.modules.pop("app", None)
            settings_store.SETTINGS_PATH = original_path

    def test_daily_fetch_retries_then_succeeds(self):

        original_path = settings_store.SETTINGS_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                app_module = self.import_app_with_temp_settings(tmp)
                with patch.object(pipeline_orchestrator, "fetch_latest_papers", side_effect=[
                    RuntimeError("arXiv 429"),
                    RuntimeError("still limited"),
                    [{"id": 1}],
                ]) as fetch_mock, \
                        patch.object(pipeline_orchestrator.time, "sleep") as sleep_mock, \
                        patch.object(pipeline_orchestrator, "set_task_log_step_status") as step_mock:
                    papers, retries = pipeline_orchestrator._fetch_for_daily_pipeline_with_retries(
                        log_id=123,
                        fetch_days=3,
                        retry_interval_minutes=10,
                        max_retries=2,
                    )

            self.assertEqual(papers, [{"id": 1}])
            self.assertEqual(retries, 2)
            self.assertEqual(fetch_mock.call_count, 3)
            sleep_mock.assert_any_call(600)
            self.assertEqual(sleep_mock.call_count, 2)
            self.assertGreaterEqual(step_mock.call_count, 2)
        finally:
            sys.modules.pop("app", None)
            settings_store.SETTINGS_PATH = original_path

    def test_daily_fetch_raises_after_max_retries(self):

        original_path = settings_store.SETTINGS_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                app_module = self.import_app_with_temp_settings(tmp)
                with patch.object(pipeline_orchestrator, "fetch_latest_papers", side_effect=RuntimeError("offline")) as fetch_mock, \
                        patch.object(pipeline_orchestrator.time, "sleep") as sleep_mock, \
                        patch.object(pipeline_orchestrator, "set_task_log_step_status"):
                    with self.assertRaisesRegex(RuntimeError, "已重试 2 次仍未成功"):
                        pipeline_orchestrator._fetch_for_daily_pipeline_with_retries(
                            log_id=123,
                            fetch_days=3,
                            retry_interval_minutes=10,
                            max_retries=2,
                        )

            self.assertEqual(fetch_mock.call_count, 3)
            self.assertEqual(sleep_mock.call_count, 2)
        finally:
            sys.modules.pop("app", None)
            settings_store.SETTINGS_PATH = original_path

    def test_daily_fetch_can_disable_retries(self):

        original_path = settings_store.SETTINGS_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                app_module = self.import_app_with_temp_settings(tmp)
                with patch.object(pipeline_orchestrator, "fetch_latest_papers", side_effect=RuntimeError("offline")) as fetch_mock, \
                        patch.object(pipeline_orchestrator.time, "sleep") as sleep_mock, \
                        patch.object(pipeline_orchestrator, "set_task_log_step_status"):
                    with self.assertRaisesRegex(RuntimeError, "已重试 0 次仍未成功"):
                        pipeline_orchestrator._fetch_for_daily_pipeline_with_retries(
                            log_id=123,
                            fetch_days=3,
                            retry_interval_minutes=10,
                            max_retries=0,
                        )

            self.assertEqual(fetch_mock.call_count, 1)
            sleep_mock.assert_not_called()
        finally:
            sys.modules.pop("app", None)
            settings_store.SETTINGS_PATH = original_path


if __name__ == "__main__":
    unittest.main()
