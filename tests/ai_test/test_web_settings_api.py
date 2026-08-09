"""
test_web_settings_api.py — 由旧测试拆分迁入，并包含设置与任务接口回归测试。
"""

import unittest
import importlib
import types
from datetime import datetime, timezone
from unittest.mock import patch

from .common import (
    FakeRequest,
    install_import_stubs,
    setup_web_test_base,
    teardown_web_test_base,
)


web_settings_api = None
web_tasks_api = None

class SettingsTasksApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_import_stubs()
        global web_settings_api, web_tasks_api
        web_settings_api = importlib.import_module("source.web.settings_api")
        web_tasks_api = importlib.import_module("source.web.tasks_api")
        cls.web_settings_api = web_settings_api
        cls.web_tasks_api = web_tasks_api
        setup_web_test_base()

    @classmethod
    def tearDownClass(cls):
        teardown_web_test_base()

    def test_schedule_endpoint_saves_and_reconfigures(self):
        web_settings_api = self.web_settings_api
        current = {
            "enabled": True,
            "days_of_week": ["mon", "wed", "fri"],
            "hour": 10,
            "minute": 0,
            "fetch_days": 7,
            "analyze_limit": 250,
        }
        saved = {**current, "hour": 8, "minute": 30}
        web_settings_api.request = FakeRequest({"enabled": True, "hour": 8, "minute": 30})

        with patch.object(web_settings_api, "save_schedule_config", return_value=True) as save_schedule, \
             patch.object(web_settings_api, "get_schedule_config", side_effect=[current, saved]), \
             patch.object(web_settings_api, "configure_daily_job") as configure_daily_job:
            result = web_settings_api.api_save_schedule_config()

        self.assertEqual(result["status"], "ok")
        save_schedule.assert_called_once_with(saved)
        configure_daily_job.assert_called_once_with(saved)

    def test_fetch_rejects_deprecated_max_results_before_side_effects(self):
        web_tasks_api = self.web_tasks_api
        cases = (
            {"max_results": "1"},
            {"max_results": ""},
            {"max_results": "1", "days": "3"},
            {"max_results": "1", "date": "2026-08-01"},
        )
        for args in cases:
            with self.subTest(args=args):
                web_tasks_api.request = FakeRequest(
                    args=args,
                    endpoint="api_fetch",
                    path="/api/fetch",
                )

                with patch.object(web_tasks_api, "start_task_log") as start_log, \
                     patch.object(web_tasks_api, "fetch_latest_papers") as fetch_latest, \
                     patch.object(web_tasks_api, "fetch_batch") as fetch_batch, \
                     patch.object(web_tasks_api, "fetch_by_date") as fetch_by_date:
                    result, status = web_tasks_api.api_fetch()

                self.assertEqual(status, 400)
                self.assertEqual(result, {
                    "status": "error",
                    "message": "max_results 参数已废弃，请使用 days（默认 1 天）或 date",
                })
                start_log.assert_not_called()
                fetch_latest.assert_not_called()
                fetch_batch.assert_not_called()
                fetch_by_date.assert_not_called()

    def test_fetch_defaults_to_latest_one_day_route(self):
        web_tasks_api = self.web_tasks_api
        web_tasks_api.request = FakeRequest(args={})

        with patch.object(web_tasks_api, "start_task_log", return_value=1) as start_log, \
             patch.object(web_tasks_api, "finish_task_log") as finish_log, \
             patch.object(web_tasks_api, "get_unanalyzed_count", return_value=0), \
             patch.object(web_tasks_api, "get_fetch_config", return_value={"batch_days": 30, "batch_delay": 10}), \
             patch.object(web_tasks_api, "fetch_latest_papers", return_value=[]) as fetch_latest, \
             patch.object(web_tasks_api, "fetch_batch") as fetch_batch, \
             patch.object(web_tasks_api, "fetch_by_date") as fetch_by_date:
            result = web_tasks_api.api_fetch()

        self.assertEqual(result["status"], "ok")
        fetch_latest.assert_called_once_with(categories=None)
        fetch_batch.assert_not_called()
        fetch_by_date.assert_not_called()
        start_log.assert_called_once()
        finish_log.assert_called_once()

    def test_fetch_uses_days_batch_route(self):
        web_tasks_api = self.web_tasks_api
        web_tasks_api.request = FakeRequest(
            args={"days": "4"},
        )

        with patch.object(web_tasks_api, "start_task_log", return_value=1), \
             patch.object(web_tasks_api, "finish_task_log"), \
             patch.object(web_tasks_api, "get_unanalyzed_count", return_value=0), \
             patch.object(web_tasks_api, "get_fetch_config", return_value={"batch_days": 30, "batch_delay": 10}), \
             patch.object(web_tasks_api, "fetch_latest_papers") as fetch_latest, \
             patch.object(web_tasks_api, "fetch_batch", return_value=[]) as fetch_batch, \
             patch.object(web_tasks_api, "fetch_by_date") as fetch_by_date:
            result = web_tasks_api.api_fetch()

        self.assertEqual(result["status"], "ok")
        fetch_latest.assert_not_called()
        fetch_by_date.assert_not_called()
        self.assertIsNone(fetch_batch.call_args.kwargs["categories"])
        self.assertEqual(fetch_batch.call_args.kwargs["total_days"], 4)
        self.assertEqual(fetch_batch.call_args.kwargs["batch_days"], 30)
        self.assertEqual(fetch_batch.call_args.kwargs["batch_delay"], 10)

    def test_fetch_date_takes_priority_over_days(self):
        web_tasks_api = self.web_tasks_api
        web_tasks_api.request = FakeRequest(
            args={"days": "4", "date": "2026-08-01"},
        )

        with patch.object(web_tasks_api, "start_task_log", return_value=1), \
             patch.object(web_tasks_api, "finish_task_log"), \
             patch.object(web_tasks_api, "get_unanalyzed_count", return_value=0), \
             patch.object(web_tasks_api, "get_fetch_config", return_value={"batch_days": 30, "batch_delay": 10}), \
             patch.object(web_tasks_api, "fetch_latest_papers") as fetch_latest, \
             patch.object(web_tasks_api, "fetch_batch") as fetch_batch, \
             patch.object(web_tasks_api, "fetch_by_date", return_value=[]) as fetch_by_date:
            result = web_tasks_api.api_fetch()

        self.assertEqual(result["status"], "ok")
        fetch_latest.assert_not_called()
        fetch_batch.assert_not_called()
        fetch_by_date.assert_called_once_with(
            "2026-08-01", categories=None,
        )

    def test_save_fetch_config_preserves_omitted_current_values(self):
        web_settings_api = self.web_settings_api
        current = {
            "request_delay": 9.0,
            "batch_days": 30,
            "batch_delay": 17.0,
        }
        web_settings_api.request = FakeRequest({"batch_days": 45})

        with patch.object(web_settings_api, "get_fetch_config", return_value=current), \
             patch.object(web_settings_api, "save_fetch_config", return_value=True) as save_fetch:
            result = web_settings_api.api_save_fetch_config()

        self.assertEqual(result["status"], "ok")
        save_fetch.assert_called_once_with({
            "request_delay": 9.0,
            "batch_days": 45,
            "batch_delay": 17.0,
        })

    def test_scheduled_tasks_endpoint_includes_timezone_config_and_last_run(self):
        web_tasks_api = self.web_tasks_api
        schedule = {
            "enabled": True,
            "days_of_week": ["mon", "tue"],
            "hour": 8,
            "minute": 30,
            "fetch_days": 5,
            "analyze_limit": 200,
        }
        job = types.SimpleNamespace(
            id="daily_pipeline",
            name="AI 论文日报",
            next_run_time=datetime(2026, 6, 24, 8, 30),
            trigger="cron[day_of_week='mon,tue', hour='8', minute='30']",
        )
        last_run = {"id": 9, "status": "warning", "steps": [{"step_key": "backup"}]}

        with patch.object(web_tasks_api, "get_schedule_config", return_value=schedule), \
             patch.object(web_tasks_api.scheduler, "get_jobs", return_value=[job]), \
             patch.object(web_tasks_api, "get_task_logs", return_value=([last_run], 1)):
            result = web_tasks_api.api_scheduled_tasks()

        self.assertEqual(result["days_of_week"], ["mon", "tue"])
        self.assertEqual(result["fetch_days"], 5)
        self.assertEqual(result["analyze_limit"], 200)
        self.assertTrue(result["timezone"])
        self.assertEqual(result["last_run"], last_run)

    def test_get_webdav_backup_endpoint_masks_password(self):
        web_settings_api = self.web_settings_api

        with patch.object(web_settings_api, "get_webdav_backup_config", return_value={
            "enabled": True,
            "url": "https://dav.example.com",
            "username": "alice",
            "password_masked": "******",
        }) as get_config:
            result = web_settings_api.api_get_webdav_backup()

        get_config.assert_called_once_with(mask_password=True)
        self.assertEqual(result["password_masked"], "******")
        self.assertNotIn("password", result)

    def test_save_webdav_backup_endpoint_saves_config(self):
        web_settings_api = self.web_settings_api
        web_settings_api.request = FakeRequest({
            "enabled": True,
            "url": "https://dav.example.com",
            "username": "alice",
            "password": "",
            "remote_dir": "arxiv",
            "history_days": "3",
        })

        with patch.object(web_settings_api, "save_webdav_backup_config", return_value=True) as save_config, \
             patch.object(web_settings_api, "get_webdav_backup_config", return_value={"password_masked": "******"}):
            result = web_settings_api.api_save_webdav_backup()

        self.assertEqual(result["status"], "ok")
        save_config.assert_called_once_with({
            "enabled": True,
            "url": "https://dav.example.com",
            "username": "alice",
            "password": "",
            "remote_dir": "arxiv",
            "history_days": 3,
        })

    def test_manual_webdav_backup_endpoint_logs_task(self):
        web_tasks_api = self.web_tasks_api
        web_tasks_api.request = FakeRequest({}, endpoint="api_run_webdav_backup", method="POST", path="/api/backup/webdav/run")
        result_payload = {
            "status": "ok",
            "message": "WebDAV 备份完成",
            "uploaded_files": ["arxiv-backup-20260615-120000.zip", "arxiv-backup-latest.zip"],
            "deleted_files": [],
            "archive_size": "1.0 KB",
            "db_size": "2.0 KB",
            "last_uploaded_file": "arxiv-backup-20260615-120000.zip",
        }

        with patch.object(
            web_tasks_api,
            "_run_webdav_backup_task",
            return_value=result_payload,
        ) as backup:
            result = web_tasks_api.api_run_webdav_backup()

        self.assertEqual(result["status"], "ok")
        backup.assert_called_once_with(force=True)

    def test_save_personalization_saves_interest_without_recommendation_call(self):
        web_settings_api = self.web_settings_api
        web_settings_api.request = FakeRequest({"research_interests": "robotics"})

        with patch.object(web_settings_api, "save_personalization_config", return_value=True) as save_personalization, \
             patch.object(web_settings_api, "get_personalization_config", return_value={"research_interests": "robotics"}):
            result = web_settings_api.api_save_personalization()

        self.assertEqual(result["status"], "ok")
        save_personalization.assert_called_once_with({"research_interests": "robotics"})

    def test_recalculate_recommendations_calls_recommendation_task(self):
        web_tasks_api = self.web_tasks_api
        web_tasks_api.request = FakeRequest(
            {"limit": 5},
            endpoint="api_recalculate_recommendations",
            method="POST",
            path="/api/recommendations/recalculate",
            args={"task_id": "rec-task"},
        )

        with patch.object(web_tasks_api, "start_task_log", return_value=1), \
             patch.object(web_tasks_api, "finish_task_log"), \
             patch.object(web_tasks_api, "get_personalization_config", return_value={"research_interests": "robotics"}), \
             patch.object(web_tasks_api, "get_concurrency", return_value=2), \
             patch.object(web_tasks_api, "recommend_pending_papers", return_value=3) as recommend:
            result = web_tasks_api.api_recalculate_recommendations()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["count"], 3)
        self.assertEqual(recommend.call_args.kwargs["limit"], 5)
        self.assertEqual(recommend.call_args.kwargs["concurrency"], 2)
        self.assertIsNone(recommend.call_args.kwargs["date"])

    def test_generate_report_default_does_not_call_ai_summary(self):
        web_tasks_api = self.web_tasks_api
        web_tasks_api.request = FakeRequest({}, endpoint="api_generate", method="POST", path="/api/generate")

        with patch.object(web_tasks_api, "start_task_log", return_value=1), \
             patch.object(web_tasks_api, "finish_task_log"), \
             patch.object(web_tasks_api, "get_all_dates", return_value=[("2026-01-01",)]) as get_dates, \
             patch.object(web_tasks_api, "get_concurrency", return_value=2), \
             patch.object(web_tasks_api, "recommend_pending_papers", return_value=1) as recommend, \
             patch.object(web_tasks_api, "generate_report_ai_summary") as ai_summary, \
             patch.object(web_tasks_api, "generate_report_content", return_value=("html", 2, 1, 4.0)) as report_content, \
             patch.object(web_tasks_api, "save_report") as save_report:
            result = web_tasks_api.api_generate()

        self.assertEqual(result["status"], "ok")
        ai_summary.assert_not_called()
        get_dates.assert_called_once_with(ingest_mode="feed")
        recommend.assert_called_once_with(limit=1000, date="2026-01-01", concurrency=2, ingest_mode="feed")
        report_content.assert_called_once_with("2026-01-01", ai_summary=None)
        save_report.assert_called_once()

    def test_generate_report_recommend_zero_skips_recommendation(self):
        web_tasks_api = self.web_tasks_api
        web_tasks_api.request = FakeRequest(
            {},
            endpoint="api_generate",
            method="POST",
            path="/api/generate",
            args={"date": "2026-01-01", "recommend": "0"},
        )

        with patch.object(web_tasks_api, "start_task_log", return_value=1), \
             patch.object(web_tasks_api, "finish_task_log"), \
             patch.object(web_tasks_api, "recommend_pending_papers") as recommend, \
             patch.object(web_tasks_api, "generate_report_content", return_value=("html", 2, 1, 4.0)) as report_content, \
             patch.object(web_tasks_api, "save_report"):
            result = web_tasks_api.api_generate()

        self.assertEqual(result["status"], "ok")
        recommend.assert_not_called()
        report_content.assert_called_once_with("2026-01-01", ai_summary=None)

    def test_generate_report_with_ai_summary_calls_report_task(self):
        web_tasks_api = self.web_tasks_api
        web_tasks_api.request = FakeRequest(
            {},
            endpoint="api_generate",
            method="POST",
            path="/api/generate",
            args={"date": "2026-01-01", "ai_summary": "1"},
        )

        with patch.object(web_tasks_api, "start_task_log", return_value=1), \
             patch.object(web_tasks_api, "finish_task_log"), \
             patch.object(web_tasks_api, "get_concurrency", return_value=2), \
             patch.object(web_tasks_api, "recommend_pending_papers", return_value=0), \
             patch.object(web_tasks_api, "generate_report_ai_summary", return_value=("导读", None)) as ai_summary, \
             patch.object(web_tasks_api, "generate_report_content", return_value=("html", 2, 1, 4.0)) as report_content, \
             patch.object(web_tasks_api, "save_report"):
            result = web_tasks_api.api_generate()

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["ai_summary"])
        ai_summary.assert_called_once_with("2026-01-01")
        report_content.assert_called_once_with("2026-01-01", ai_summary="导读")


if __name__ == "__main__":
    unittest.main()
