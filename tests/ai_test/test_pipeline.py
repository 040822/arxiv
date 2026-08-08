"""
test_pipeline.py — 由 tests/test_ai_provider_config.py 拆分迁入（Q20），方法体逐字节一致。
"""

import unittest
import importlib
from unittest.mock import patch

from .common import (
    FakeRequest,
    install_import_stubs,
    setup_web_test_base,
    teardown_web_test_base,
)


pipeline_orchestrator = None
pipeline_scheduler = None
web_application = None
web_tasks_api = None

class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_import_stubs()
        import app as app_module
        global pipeline_orchestrator, pipeline_scheduler, web_application, web_tasks_api
        pipeline_orchestrator = importlib.import_module("source.pipeline.orchestrator")
        pipeline_scheduler = importlib.import_module("source.pipeline.scheduler")
        web_application = importlib.import_module("source.web.application")
        web_tasks_api = importlib.import_module("source.web.tasks_api")
        cls.web_application = web_application
        cls.web_tasks_api = web_tasks_api
        cls.app_module = app_module
        setup_web_test_base()

    @classmethod
    def tearDownClass(cls):
        teardown_web_test_base()

    def test_scheduler_uses_selected_days_and_prevents_overlapping_instances(self):
        app_module = self.app_module
        schedule = {
            "enabled": True,
            "days_of_week": ["mon", "wed", "fri"],
            "hour": 8,
            "minute": 30,
            "fetch_days": 3,
            "analyze_limit": 1000,
        }

        with patch.object(pipeline_scheduler.scheduler, "get_job", return_value=None), \
             patch.object(pipeline_scheduler.scheduler, "add_job") as add_job:
            pipeline_scheduler.configure_daily_job(schedule)

        kwargs = add_job.call_args.kwargs
        self.assertEqual(kwargs["day_of_week"], "mon,wed,fri")
        self.assertEqual((kwargs["hour"], kwargs["minute"]), (8, 30))
        self.assertEqual(kwargs["max_instances"], 1)
        self.assertTrue(kwargs["coalesce"])

    def test_app_startup_reconciles_orphaned_running_tasks_before_scheduling(self):
        web_application = self.web_application

        with patch.object(web_application, "init_db"), \
             patch.object(web_application, "interrupt_running_task_logs", return_value=2) as interrupt, \
             patch.object(web_application, "configure_daily_job") as configure:
            web_application.create_app()

        interrupt.assert_called_once()
        configure.assert_called_once()

    def test_daily_pipeline_uses_saved_limits_and_records_six_steps(self):
        app_module = self.app_module
        schedule = {
            "enabled": True,
            "days_of_week": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            "hour": 10,
            "minute": 0,
            "fetch_days": 5,
            "analyze_limit": 200,
        }

        with patch.object(pipeline_orchestrator, "start_task_log", return_value=1), \
             patch.object(pipeline_orchestrator, "initialize_task_log_steps") as initialize_steps, \
             patch.object(pipeline_orchestrator, "set_task_log_step_status") as set_step, \
             patch.object(pipeline_orchestrator, "finish_task_log") as finish_log, \
             patch.object(pipeline_orchestrator, "get_schedule_config", return_value=schedule), \
             patch.object(pipeline_orchestrator, "fetch_latest_papers", return_value=[]) as fetch, \
             patch.object(pipeline_orchestrator, "get_concurrency", return_value=2), \
             patch.object(pipeline_orchestrator, "analyze_pending_papers", return_value=0) as analyze, \
             patch.object(pipeline_orchestrator, "get_all_dates", return_value=[("2026-06-23",)]), \
             patch.object(pipeline_orchestrator, "get_personalization_config", return_value={"research_interests": ""}), \
             patch.object(pipeline_orchestrator, "recommend_pending_papers") as recommend, \
             patch.object(pipeline_orchestrator, "generate_report_content", return_value=("html", 0, 0, 0.0)), \
             patch.object(pipeline_orchestrator, "save_report"), \
             patch.object(pipeline_orchestrator, "get_email_report_config", return_value={"enabled": False}), \
             patch.object(pipeline_orchestrator, "get_webdav_backup_config", return_value={"enabled": False}):
            result = pipeline_orchestrator.daily_pipeline()

        self.assertEqual(result["status"], "success")
        fetch.assert_called_once_with(days=5)
        self.assertEqual(analyze.call_args.kwargs["limit"], 200)
        self.assertEqual(len(initialize_steps.call_args.args[1]), 6)
        recommend.assert_not_called()
        step_statuses = {(call.args[1], call.args[2]) for call in set_step.call_args_list}
        self.assertIn(("recommend", "skipped"), step_statuses)
        self.assertIn(("email", "skipped"), step_statuses)
        self.assertIn(("backup", "skipped"), step_statuses)
        self.assertEqual(finish_log.call_args.args[1], "success")

    def test_daily_pipeline_backup_failure_does_not_fail_pipeline(self):
        app_module = self.app_module

        with patch.object(pipeline_orchestrator, "start_task_log", return_value=1), \
             patch.object(pipeline_orchestrator, "initialize_task_log_steps"), \
             patch.object(pipeline_orchestrator, "set_task_log_step_status") as set_step, \
             patch.object(pipeline_orchestrator, "finish_task_log") as finish_log, \
             patch.object(pipeline_orchestrator, "get_schedule_config", return_value={
                 "fetch_days": 3,
                 "analyze_limit": 1000,
                 "fetch_retry_interval_minutes": 10,
                 "fetch_max_retries": 20,
             }), \
             patch.object(pipeline_orchestrator, "fetch_latest_papers", return_value=[]), \
             patch.object(pipeline_orchestrator, "get_concurrency", return_value=2), \
             patch.object(pipeline_orchestrator, "analyze_pending_papers", return_value=0), \
             patch.object(pipeline_orchestrator, "get_all_dates", return_value=[("2026-06-15",)]), \
             patch.object(pipeline_orchestrator, "get_personalization_config", return_value={"research_interests": "robotics"}), \
             patch.object(pipeline_orchestrator, "recommend_pending_papers", return_value=0), \
             patch.object(pipeline_orchestrator, "generate_report_content", return_value=("html", 1, 1, 0.0)), \
             patch.object(pipeline_orchestrator, "save_report"), \
             patch.object(pipeline_orchestrator, "get_email_report_config", return_value={"enabled": False}), \
             patch.object(pipeline_orchestrator, "get_webdav_backup_config", return_value={"enabled": True}), \
             patch.object(pipeline_orchestrator, "_run_webdav_backup_task", side_effect=RuntimeError("dav down")):
            result = pipeline_orchestrator.daily_pipeline()

        self.assertEqual(result["status"], "warning")
        self.assertEqual(finish_log.call_args.args[1], "warning")
        self.assertIn("WebDAV 备份失败", finish_log.call_args.args[2])
        self.assertIn(("backup", "warning"), {(call.args[1], call.args[2]) for call in set_step.call_args_list})

    def test_daily_pipeline_duplicate_email_skip_does_not_fail_pipeline(self):
        app_module = self.app_module
        skipped = {
            "status": "skipped",
            "reason": "already_sent",
            "message": "日报 2026-06-17 已发送，跳过重复发送",
            "report_date": "2026-06-17",
        }

        with patch.object(pipeline_orchestrator, "start_task_log", return_value=1), \
             patch.object(pipeline_orchestrator, "initialize_task_log_steps"), \
             patch.object(pipeline_orchestrator, "set_task_log_step_status") as set_step, \
             patch.object(pipeline_orchestrator, "finish_task_log") as finish_log, \
             patch.object(pipeline_orchestrator, "get_schedule_config", return_value={
                 "fetch_days": 3,
                 "analyze_limit": 1000,
                 "fetch_retry_interval_minutes": 10,
                 "fetch_max_retries": 20,
             }), \
             patch.object(pipeline_orchestrator, "fetch_latest_papers", return_value=[]), \
             patch.object(pipeline_orchestrator, "get_concurrency", return_value=2), \
             patch.object(pipeline_orchestrator, "analyze_pending_papers", return_value=0), \
             patch.object(pipeline_orchestrator, "get_all_dates", return_value=[("2026-06-17",)]), \
             patch.object(pipeline_orchestrator, "get_personalization_config", return_value={"research_interests": "robotics"}), \
             patch.object(pipeline_orchestrator, "recommend_pending_papers", return_value=0), \
             patch.object(pipeline_orchestrator, "generate_report_content", return_value=("html", 1, 1, 4.0)), \
             patch.object(pipeline_orchestrator, "save_report"), \
             patch.object(pipeline_orchestrator, "get_email_report_config", return_value={"enabled": True}), \
             patch.object(pipeline_orchestrator, "_run_email_report_task", return_value=skipped) as email_task, \
             patch.object(pipeline_orchestrator, "get_webdav_backup_config", return_value={"enabled": False}):
            pipeline_orchestrator.daily_pipeline()

        self.assertFalse(email_task.call_args.kwargs["log_task"])
        self.assertEqual(finish_log.call_args.args[1], "success")
        self.assertIn(("email", "skipped"), {(call.args[1], call.args[2]) for call in set_step.call_args_list})

    def test_scheduled_pipeline_skips_when_full_pipeline_is_already_running(self):
        app_module = self.app_module
        self.assertTrue(pipeline_orchestrator.pipeline_lock.acquire(blocking=False))
        try:
            with patch.object(pipeline_orchestrator, "start_task_log", return_value=12), \
                 patch.object(pipeline_orchestrator, "finish_task_log") as finish_log, \
                 patch.object(pipeline_orchestrator, "fetch_latest_papers") as fetch:
                result = pipeline_orchestrator.daily_pipeline()
        finally:
            pipeline_orchestrator.pipeline_lock.release()

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(finish_log.call_args.args[1], "skipped")
        fetch.assert_not_called()

    def test_manual_combined_run_returns_conflict_when_pipeline_is_busy(self):
        web_tasks_api = self.web_tasks_api
        web_tasks_api.request = FakeRequest(args={"task_id": "manual-1"})
        self.assertTrue(web_tasks_api.pipeline_lock.acquire(blocking=False))
        try:
            with patch.object(web_tasks_api, "start_task_log") as start_log, \
                 patch.object(web_tasks_api, "fetch_latest_papers") as fetch:
                result, status = web_tasks_api.api_run()
        finally:
            web_tasks_api.pipeline_lock.release()

        self.assertEqual(status, 409)
        self.assertEqual(result["status"], "error")
        self.assertIn("正在运行", result["message"])
        start_log.assert_not_called()
        fetch.assert_not_called()

    def test_manual_combined_run_fetches_with_schedule_fetch_days(self):
        web_tasks_api = self.web_tasks_api
        web_tasks_api.request = FakeRequest(args={"task_id": "manual-2"})
        captured = {}

        def fake_fetch(categories=None, days=1):
            captured["days"] = days
            raise RuntimeError("abort-after-fetch")

        with patch.object(web_tasks_api, "start_task_log", return_value=1), \
             patch.object(web_tasks_api, "update_progress"), \
             patch.object(web_tasks_api, "get_schedule_config", return_value={"fetch_days": 5}), \
             patch.object(web_tasks_api, "fetch_latest_papers", side_effect=fake_fetch), \
             patch.object(web_tasks_api, "finish_task_log") as finish_log:
            result, status = web_tasks_api.api_run()

        self.assertEqual(status, 500)
        self.assertEqual(result["status"], "error")
        self.assertEqual(captured["days"], 5)
        finish_log.assert_called_once()

    def test_daily_pipeline_stops_after_core_step_failure(self):
        app_module = self.app_module

        with patch.object(pipeline_orchestrator, "start_task_log", return_value=1), \
             patch.object(pipeline_orchestrator, "initialize_task_log_steps"), \
             patch.object(pipeline_orchestrator, "set_task_log_step_status") as set_step, \
             patch.object(pipeline_orchestrator, "finish_task_log") as finish_log, \
             patch.object(pipeline_orchestrator, "get_schedule_config", return_value={
                 "fetch_days": 3,
                 "analyze_limit": 1000,
                 "fetch_retry_interval_minutes": 10,
                 "fetch_max_retries": 0,
             }), \
             patch.object(pipeline_orchestrator, "fetch_latest_papers", side_effect=RuntimeError("arXiv down")), \
             patch.object(pipeline_orchestrator, "analyze_pending_papers") as analyze:
            result = pipeline_orchestrator.daily_pipeline()

        self.assertEqual(result["status"], "error")
        analyze.assert_not_called()
        statuses = [(call.args[1], call.args[2]) for call in set_step.call_args_list]
        self.assertIn(("fetch", "error"), statuses)
        for step_key in ("analyze", "recommend", "report", "email", "backup"):
            self.assertIn((step_key, "skipped"), statuses)
        self.assertEqual(finish_log.call_args.args[1], "error")

    def test_email_report_task_sends_when_ai_summary_fails(self):
        app_module = self.app_module
        report = {"report_date": "2026-06-17", "paper_count": 1, "analyzed_count": 1, "avg_rating": 4}
        send_result = {
            "status": "ok",
            "message": "报告邮件已发送",
            "report_date": "2026-06-17",
            "recipients": ["reader@example.com"],
            "subject": "Daily",
            "important_count": 0,
            "overview_count": 1,
        }

        with patch.object(pipeline_orchestrator, "start_task_log", return_value=9), \
             patch.object(pipeline_orchestrator, "finish_task_log") as finish_log, \
             patch.object(pipeline_orchestrator, "get_email_report_config", return_value={
                 "enabled": True,
                 "last_sent_report_date": "2026-06-16",
             }), \
             patch.object(pipeline_orchestrator, "generate_report_ai_summary", return_value=(None, "summary model down")) as summary, \
             patch.object(pipeline_orchestrator, "send_report_email", return_value=send_result) as send:
            result = pipeline_orchestrator._run_email_report_task(report, force=False)

        self.assertEqual(result["status"], "ok")
        summary.assert_called_once_with("2026-06-17")
        send.assert_called_once()
        self.assertEqual(send.call_args.kwargs["ai_summary"], None)
        self.assertEqual(send.call_args.kwargs["ai_summary_error"], "summary model down")
        self.assertEqual(finish_log.call_args.args[1], "success")
        self.assertIn("ai_summary_error=summary model down", finish_log.call_args.args[3])

    def test_email_report_task_skips_report_already_sent(self):
        app_module = self.app_module
        report = {"report_date": "2026-06-17", "paper_count": 1, "analyzed_count": 1, "avg_rating": 4}

        with patch.object(pipeline_orchestrator, "start_task_log", return_value=10), \
             patch.object(pipeline_orchestrator, "finish_task_log") as finish_log, \
             patch.object(pipeline_orchestrator, "get_email_report_config", return_value={
                 "enabled": True,
                 "last_sent_report_date": "2026-06-17",
             }), \
             patch.object(pipeline_orchestrator, "generate_report_ai_summary") as summary, \
             patch.object(pipeline_orchestrator, "send_report_email") as send:
            result = pipeline_orchestrator._run_email_report_task(report, force=False)

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "already_sent")
        self.assertEqual(result["report_date"], "2026-06-17")
        summary.assert_not_called()
        send.assert_not_called()
        finish_log.assert_called_once_with(
            10,
            "success",
            "日报 2026-06-17 已发送，跳过重复发送",
            "report_date=2026-06-17, reason=already_sent",
        )

    def test_email_report_task_force_bypasses_already_sent_check(self):
        app_module = self.app_module
        report = {"report_date": "2026-06-17", "paper_count": 1, "analyzed_count": 1, "avg_rating": 4}
        send_result = {
            "status": "ok",
            "message": "测试邮件已发送",
            "report_date": "2026-06-17",
            "recipients": ["reader@example.com"],
            "subject": "Daily",
            "important_count": 0,
            "overview_count": 1,
        }

        with patch.object(pipeline_orchestrator, "start_task_log", return_value=11), \
             patch.object(pipeline_orchestrator, "finish_task_log"), \
             patch.object(pipeline_orchestrator, "get_email_report_config", return_value={
                 "enabled": True,
                 "last_sent_report_date": "2026-06-17",
             }), \
             patch.object(pipeline_orchestrator, "generate_report_ai_summary", return_value=("summary", "")) as summary, \
             patch.object(pipeline_orchestrator, "send_report_email", return_value=send_result) as send:
            result = pipeline_orchestrator._run_email_report_task(report, force=True)

        self.assertEqual(result["status"], "ok")
        summary.assert_called_once_with("2026-06-17")
        send.assert_called_once()
        self.assertTrue(send.call_args.kwargs["force"])


if __name__ == "__main__":
    unittest.main()
