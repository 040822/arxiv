"""
test_task_logs_db.py — 由 tests/test_ai_provider_config.py 拆分迁入（Q20），方法体逐字节一致。
"""

import unittest
import os
import tempfile

from source.storage import connection as db_connection


class TaskLogDatabaseTests(unittest.TestCase):
    def test_task_logs_include_ordered_pipeline_steps(self):
        import database

        original_dir = db_connection.DB_DIR
        original_path = db_connection.DB_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                db_connection.DB_DIR = tmp
                db_connection.DB_PATH = os.path.join(tmp, "papers.db")
                database.init_db()

                log_id = database.start_task_log("daily_pipeline", "每日定时任务启动")
                database.initialize_task_log_steps(log_id, [
                    ("fetch", "抓取论文"),
                    ("analyze", "基础分析"),
                ])
                database.set_task_log_step_status(log_id, "fetch", "running", "正在抓取")
                database.set_task_log_step_status(log_id, "fetch", "success", "抓取 2 篇")
                database.finish_task_log(log_id, "warning", "完成但有警告")

                logs, total = database.get_task_logs(task_name="daily_pipeline")

            self.assertEqual(total, 1)
            self.assertEqual(logs[0]["status"], "warning")
            self.assertEqual([step["step_key"] for step in logs[0]["steps"]], ["fetch", "analyze"])
            self.assertEqual(logs[0]["steps"][0]["status"], "success")
            self.assertEqual(logs[0]["steps"][1]["status"], "pending")
        finally:
            db_connection.DB_DIR = original_dir
            db_connection.DB_PATH = original_path

    def test_startup_reconciliation_interrupts_orphaned_task_runs(self):
        import database

        original_dir = db_connection.DB_DIR
        original_path = db_connection.DB_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                db_connection.DB_DIR = tmp
                db_connection.DB_PATH = os.path.join(tmp, "papers.db")
                database.init_db()

                running_id = database.start_task_log("daily_pipeline", "每日定时任务启动")
                database.initialize_task_log_steps(running_id, [
                    ("fetch", "抓取论文"),
                    ("analyze", "基础分析"),
                ])
                database.set_task_log_step_status(running_id, "fetch", "running", "正在抓取")
                finished_id = database.start_task_log("fetch", "手动抓取")
                database.finish_task_log(finished_id, "success", "已完成")

                interrupted = database.interrupt_running_task_logs("服务重启，任务已中断")
                logs, _ = database.get_task_logs()

            by_id = {log["id"]: log for log in logs}
            self.assertEqual(interrupted, 1)
            self.assertEqual(by_id[running_id]["status"], "interrupted")
            self.assertEqual(by_id[running_id]["steps"][0]["status"], "interrupted")
            self.assertEqual(by_id[running_id]["steps"][1]["status"], "skipped")
            self.assertEqual(by_id[finished_id]["status"], "success")
        finally:
            db_connection.DB_DIR = original_dir
            db_connection.DB_PATH = original_path

    def test_clearing_parent_logs_cascades_pipeline_steps(self):
        import database

        original_dir = db_connection.DB_DIR
        original_path = db_connection.DB_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                db_connection.DB_DIR = tmp
                db_connection.DB_PATH = os.path.join(tmp, "papers.db")
                database.init_db()
                log_id = database.start_task_log("daily_pipeline", "AI 论文日报启动")
                database.initialize_task_log_steps(log_id, [("fetch", "抓取论文")])
                with database.get_connection() as conn:
                    conn.execute("UPDATE task_logs SET started_at = '2020-01-01 00:00:00' WHERE id = ?", (log_id,))
                    conn.commit()

                deleted = database.clear_task_logs(keep_days=30)
                with database.get_connection() as conn:
                    step_count = conn.execute("SELECT COUNT(*) FROM task_log_steps").fetchone()[0]

            self.assertEqual(deleted, 1)
            self.assertEqual(step_count, 0)
        finally:
            db_connection.DB_DIR = original_dir
            db_connection.DB_PATH = original_path


if __name__ == "__main__":
    unittest.main()
