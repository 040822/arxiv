import os
import json
import importlib
import sqlite3
import tempfile
import sys
import types
import unittest
import zipfile
from datetime import datetime, timezone
from unittest.mock import patch

from settings import build_chat_completion_kwargs, validate_prompt_template


class FakeArgs(dict):
    def get(self, key, default=None, type=None):
        value = super().get(key, default)
        if value is None:
            return default
        if type is not None:
            try:
                return type(value)
            except (TypeError, ValueError):
                return default
        return value


class ProviderRequestBuilderTests(unittest.TestCase):
    def test_regular_model_omits_disabled_max_tokens(self):
        kwargs = build_chat_completion_kwargs(
            {
                "base_url": "https://api.example.com/v1",
                "model": "gpt-4o",
                "temperature": 0.4,
                "max_tokens": 9999,
                "max_tokens_enabled": False,
            },
            [{"role": "user", "content": "hi"}],
        )

        self.assertEqual(kwargs["temperature"], 0.4)
        self.assertNotIn("max_tokens", kwargs)
        self.assertNotIn("max_completion_tokens", kwargs)

    def test_thinking_model_omits_sampling_parameters(self):
        kwargs = build_chat_completion_kwargs(
            {
                "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                "model": "qwen3-235b-a22b",
                "is_thinking": True,
                "thinking_effort": "high",
                "temperature_enabled": True,
                "top_p_enabled": True,
                "presence_penalty_enabled": True,
                "frequency_penalty_enabled": True,
            },
            [{"role": "user", "content": "hi"}],
        )

        for field in ("temperature", "top_p", "presence_penalty", "frequency_penalty"):
            self.assertNotIn(field, kwargs)
        self.assertEqual(kwargs["extra_body"]["enable_thinking"], True)
        self.assertEqual(kwargs["extra_body"]["thinking_budget"], 8192)

    def test_openai_reasoning_uses_reasoning_effort_and_completion_tokens(self):
        kwargs = build_chat_completion_kwargs(
            {
                "base_url": "https://api.openai.com/v1",
                "model": "o3",
                "is_thinking": True,
                "thinking_effort": "max",
                "max_tokens": 1000,
                "max_tokens_enabled": True,
            },
            [{"role": "user", "content": "hi"}],
        )

        self.assertEqual(kwargs["reasoning_effort"], "high")
        self.assertEqual(kwargs["max_completion_tokens"], 1000)
        self.assertNotIn("max_tokens", kwargs)

    def test_deepseek_v4_uses_thinking_toggle_and_effort(self):
        kwargs = build_chat_completion_kwargs(
            {
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-v4-pro",
                "is_thinking": True,
                "thinking_effort": "max",
            },
            [{"role": "user", "content": "hi"}],
        )

        self.assertEqual(kwargs["reasoning_effort"], "max")
        self.assertEqual(kwargs["extra_body"], {"thinking": {"type": "enabled"}})

    def test_deepseek_legacy_reasoner_does_not_send_enable_thinking(self):
        kwargs = build_chat_completion_kwargs(
            {
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-reasoner",
            },
            [{"role": "user", "content": "hi"}],
        )

        self.assertNotIn("extra_body", kwargs)
        self.assertNotIn("temperature", kwargs)


class AiTaskSettingsTests(unittest.TestCase):
    def test_legacy_settings_get_default_task_routes_and_profiles(self):
        import settings

        original_dir = settings.DB_DIR
        original_path = settings.SETTINGS_PATH
        with tempfile.TemporaryDirectory() as tmp:
            settings.DB_DIR = tmp
            settings.SETTINGS_PATH = os.path.join(tmp, "settings.json")
            with open(settings.SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump({
                    "active_provider": "cheap",
                    "providers": {
                        "cheap": {
                            "name": "Cheap",
                            "api_key": "sk-cheap",
                            "base_url": "https://api.example.com/v1",
                            "model": "cheap-model",
                        }
                    },
                    "prompts": {
                        "system_prompt": "legacy system",
                        "user_prompt": "legacy user {title} {authors} {abstract} {tag_candidates} {rating_criteria}",
                    },
                }, f)

            loaded = settings.load_settings()

        settings.DB_DIR = original_dir
        settings.SETTINGS_PATH = original_path

        self.assertEqual(loaded["ai_tasks"]["basic_analysis"]["provider_key"], "cheap")
        self.assertEqual(loaded["ai_tasks"]["basic_analysis"]["max_tokens"], 1200)
        self.assertFalse(loaded["ai_tasks"]["basic_analysis"]["is_thinking"])
        self.assertEqual(loaded["ai_tasks"]["deep_reading"]["thinking_effort"], "high")
        self.assertTrue(loaded["ai_tasks"]["deep_reading"]["is_thinking"])
        self.assertEqual(loaded["ai_tasks"]["paper_chat"]["provider_key"], "cheap")
        self.assertTrue(loaded["ai_tasks"]["paper_chat"]["is_thinking"])
        self.assertEqual(loaded["ai_tasks"]["paper_quiz"]["provider_key"], "cheap")
        self.assertTrue(loaded["ai_tasks"]["paper_quiz"]["is_thinking"])
        self.assertEqual(loaded["ai_tasks"]["recommendation"]["provider_key"], "cheap")
        self.assertEqual(loaded["ai_tasks"]["recommendation"]["max_tokens"], 500)
        self.assertEqual(loaded["prompt_profiles"]["deep_reading"]["system"], "legacy system")
        self.assertIn('"rating"', loaded["prompt_profiles"]["basic_analysis"]["instruction"])
        self.assertIn("{rating_criteria}", loaded["prompt_profiles"]["basic_analysis"]["instruction"])
        self.assertIn("recommendation_score", loaded["prompt_profiles"]["recommendation"]["instruction"])
        self.assertIn("多轮讨论", loaded["prompt_profiles"]["paper_chat"]["instruction"])
        self.assertIn("主动回忆", loaded["prompt_profiles"]["paper_quiz"]["instruction"])
        self.assertEqual(loaded["personalization"]["research_interests"], "")

    def test_load_settings_preserves_session_secret(self):
        import settings

        original_dir = settings.DB_DIR
        original_path = settings.SETTINGS_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                settings.DB_DIR = tmp
                settings.SETTINGS_PATH = os.path.join(tmp, "settings.json")
                with open(settings.SETTINGS_PATH, "w", encoding="utf-8") as f:
                    json.dump({"session_secret": "stable-secret"}, f)

                loaded = settings.load_settings()

            self.assertEqual(loaded["session_secret"], "stable-secret")
        finally:
            settings.DB_DIR = original_dir
            settings.SETTINGS_PATH = original_path

    def test_legacy_schedule_is_upgraded_to_full_daily_pipeline_defaults(self):
        import settings

        original_dir = settings.DB_DIR
        original_path = settings.SETTINGS_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                settings.DB_DIR = tmp
                settings.SETTINGS_PATH = os.path.join(tmp, "settings.json")
                with open(settings.SETTINGS_PATH, "w", encoding="utf-8") as f:
                    json.dump({
                        "schedule": {"enabled": True, "hour": 8, "minute": 30},
                    }, f)

                loaded = settings.load_settings()["schedule"]

            self.assertEqual(loaded["days_of_week"], ["mon", "tue", "wed", "thu", "fri", "sat", "sun"])
            self.assertEqual(loaded["fetch_days"], 3)
            self.assertEqual(loaded["analyze_limit"], 1000)
            self.assertEqual(loaded["fetch_retry_interval_minutes"], 10)
            self.assertEqual(loaded["fetch_max_retries"], 20)
            self.assertEqual((loaded["hour"], loaded["minute"]), (8, 30))
        finally:
            settings.DB_DIR = original_dir
            settings.SETTINGS_PATH = original_path

    def test_get_session_secret_generates_and_persists_secret(self):
        import settings

        original_dir = settings.DB_DIR
        original_path = settings.SETTINGS_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                settings.DB_DIR = tmp
                settings.SETTINGS_PATH = os.path.join(tmp, "settings.json")

                secret = settings.get_session_secret()
                with open(settings.SETTINGS_PATH, "r", encoding="utf-8") as f:
                    saved = json.load(f)

            self.assertGreaterEqual(len(secret), 32)
            self.assertEqual(saved["session_secret"], secret)
        finally:
            settings.DB_DIR = original_dir
            settings.SETTINGS_PATH = original_path

    def test_personalization_config_is_trimmed_and_preserved(self):
        import settings

        original_dir = settings.DB_DIR
        original_path = settings.SETTINGS_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                settings.DB_DIR = tmp
                settings.SETTINGS_PATH = os.path.join(tmp, "settings.json")
                with open(settings.SETTINGS_PATH, "w", encoding="utf-8") as f:
                    json.dump({
                        "personalization": {"research_interests": "  robot learning  "},
                    }, f)

                loaded = settings.load_settings()
                interest_hash = settings.get_research_interest_hash(loaded["personalization"]["research_interests"])

            self.assertEqual(loaded["personalization"]["research_interests"], "robot learning")
            self.assertEqual(len(interest_hash), 64)
        finally:
            settings.DB_DIR = original_dir
            settings.SETTINGS_PATH = original_path

    def test_webdav_backup_config_is_preserved_and_masked(self):
        import settings

        original_dir = settings.DB_DIR
        original_path = settings.SETTINGS_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                settings.DB_DIR = tmp
                settings.SETTINGS_PATH = os.path.join(tmp, "settings.json")
                with open(settings.SETTINGS_PATH, "w", encoding="utf-8") as f:
                    json.dump({
                        "webdav_backup": {
                            "enabled": True,
                            "url": " https://dav.example.com/root/ ",
                            "username": "alice",
                            "password": "secret",
                            "remote_dir": " backups/arxiv/ ",
                            "history_days": "3",
                        },
                    }, f)

                loaded = settings.load_settings()
                masked = settings.get_webdav_backup_config(mask_password=True)

            self.assertTrue(loaded["webdav_backup"]["enabled"])
            self.assertEqual(loaded["webdav_backup"]["url"], "https://dav.example.com/root/")
            self.assertEqual(loaded["webdav_backup"]["password"], "secret")
            self.assertEqual(loaded["webdav_backup"]["remote_dir"], "backups/arxiv")
            self.assertEqual(loaded["webdav_backup"]["history_days"], 3)
            self.assertNotIn("password", masked)
            self.assertEqual(masked["password_masked"], "******")
        finally:
            settings.DB_DIR = original_dir
            settings.SETTINGS_PATH = original_path

    def test_save_webdav_backup_config_preserves_existing_password_when_blank(self):
        import settings

        original_dir = settings.DB_DIR
        original_path = settings.SETTINGS_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                settings.DB_DIR = tmp
                settings.SETTINGS_PATH = os.path.join(tmp, "settings.json")
                with open(settings.SETTINGS_PATH, "w", encoding="utf-8") as f:
                    json.dump({
                        "webdav_backup": {
                            "enabled": True,
                            "url": "https://dav.example.com",
                            "username": "alice",
                            "password": "old-secret",
                            "remote_dir": "old",
                            "history_days": 3,
                            "last_status": "success",
                            "last_success_at": "2026-06-15 12:00:00",
                            "last_uploaded_file": "arxiv-backup-old.zip",
                        },
                    }, f)

                self.assertTrue(settings.save_webdav_backup_config({
                    "enabled": False,
                    "url": "https://dav.example.com/new",
                    "username": "bob",
                    "password": "",
                    "remote_dir": "new",
                    "history_days": 5,
                }))
                saved = settings.load_settings()["webdav_backup"]

            self.assertFalse(saved["enabled"])
            self.assertEqual(saved["url"], "https://dav.example.com/new")
            self.assertEqual(saved["username"], "bob")
            self.assertEqual(saved["password"], "old-secret")
            self.assertEqual(saved["remote_dir"], "new")
            self.assertEqual(saved["history_days"], 5)
            self.assertEqual(saved["last_status"], "success")
            self.assertEqual(saved["last_success_at"], "2026-06-15 12:00:00")
            self.assertEqual(saved["last_uploaded_file"], "arxiv-backup-old.zip")
        finally:
            settings.DB_DIR = original_dir
            settings.SETTINGS_PATH = original_path

    def test_task_config_merges_provider_credentials_and_task_overrides(self):
        import settings

        original_dir = settings.DB_DIR
        original_path = settings.SETTINGS_PATH
        with tempfile.TemporaryDirectory() as tmp:
            settings.DB_DIR = tmp
            settings.SETTINGS_PATH = os.path.join(tmp, "settings.json")
            with open(settings.SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump({
                    "active_provider": "cheap",
                    "providers": {
                        "cheap": {
                            "name": "Cheap",
                            "api_key": "sk-cheap",
                            "base_url": "https://api.example.com/v1",
                            "model": "cheap-model",
                        },
                        "smart": {
                            "name": "Smart",
                            "api_key": "sk-smart",
                            "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                            "model": "qwen3-smart",
                        },
                    },
                    "ai_tasks": {
                        "deep_reading": {
                            "provider_key": "smart",
                            "model": "qwen3-smart",
                            "is_thinking": True,
                            "thinking_effort": "high",
                            "temperature_enabled": True,
                            "max_tokens_enabled": True,
                            "max_tokens": 6000,
                        }
                    },
                }, f)

            cfg = settings.get_ai_task_config("deep_reading")

        settings.DB_DIR = original_dir
        settings.SETTINGS_PATH = original_path

        self.assertEqual(cfg["api_key"], "sk-smart")
        self.assertEqual(cfg["provider_key"], "smart")
        self.assertEqual(cfg["model"], "qwen3-smart")
        kwargs = build_chat_completion_kwargs(cfg, [{"role": "user", "content": "hi"}])
        self.assertNotIn("temperature", kwargs)
        self.assertEqual(kwargs["extra_body"]["thinking_budget"], 8192)
        self.assertEqual(kwargs["max_tokens"], 6000)

    def test_recommendation_task_config_is_independent_route(self):
        import settings

        original_dir = settings.DB_DIR
        original_path = settings.SETTINGS_PATH
        with tempfile.TemporaryDirectory() as tmp:
            settings.DB_DIR = tmp
            settings.SETTINGS_PATH = os.path.join(tmp, "settings.json")
            with open(settings.SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump({
                    "active_provider": "cheap",
                    "providers": {
                        "cheap": {
                            "name": "Cheap",
                            "api_key": "sk-cheap",
                            "base_url": "https://api.example.com/v1",
                            "model": "cheap-model",
                        },
                        "rec": {
                            "name": "Rec",
                            "api_key": "sk-rec",
                            "base_url": "https://api.rec.example/v1",
                            "model": "rec-default",
                        },
                    },
                    "ai_tasks": {
                        "recommendation": {
                            "provider_key": "rec",
                            "model": "rec-model",
                            "max_tokens_enabled": True,
                            "max_tokens": 321,
                        }
                    },
                }, f)

            cfg = settings.get_ai_task_config("recommendation")

        settings.DB_DIR = original_dir
        settings.SETTINGS_PATH = original_path

        self.assertEqual(cfg["task_key"], "recommendation")
        self.assertEqual(cfg["provider_key"], "rec")
        self.assertEqual(cfg["api_key"], "sk-rec")
        self.assertEqual(cfg["model"], "rec-model")
        self.assertEqual(cfg["max_tokens"], 321)

    def test_legacy_deep_reading_prompt_migrates_to_qa_only(self):
        import settings

        legacy_prompt = """请对论文内容进行深度阅读分析，按 Q&A 格式详细回答每个问题，然后给出标签、评级、中文摘要和价值评价。

请严格返回合法 JSON，不要返回额外解释：

{
  "qa_analysis": "### Q1: 自定义问题？\\n\\n详细回答...",
  "tags": ["标签1", "标签2"],
  "rating": 3,
  "summary_cn": "中文摘要",
  "value_comment": "价值评价"
}

标签选择指南：
{tag_candidates}

{rating_criteria}
"""
        profiles = settings._normalize_prompt_profiles({
            "deep_reading": {"system": "s", "instruction": legacy_prompt}
        })
        instruction = profiles["deep_reading"]["instruction"]

        self.assertIn("自定义问题？", instruction)
        self.assertIn('"qa_analysis"', instruction)
        for text in ('"tags"', '"rating"', '"summary_cn"', '"value_comment"', "{tag_candidates}", "{rating_criteria}"):
            self.assertNotIn(text, instruction)

    def test_custom_deep_reading_prompt_is_preserved(self):
        import settings

        custom_prompt = '请按我自己的结构返回 {"qa_analysis": "详细内容"}，并重点分析机器人实验。'
        profiles = settings._normalize_prompt_profiles({
            "deep_reading": {"system": "s", "instruction": custom_prompt}
        })

        self.assertEqual(profiles["deep_reading"]["instruction"], custom_prompt)

    def test_weak_qa_only_prompt_migrates_to_strict_all_questions_prompt(self):
        import settings

        weak_prompt = """请对论文内容进行深度阅读分析，只生成 Q&A 深度阅读内容。

请严格返回合法 JSON，不要返回额外解释：

{
  "qa_analysis": "### Q1: 问题一？\\n\\n详细回答...\\n\\n### Q2: 问题二？\\n\\n详细回答..."
}

重要要求：
1. qa_analysis 中每个 Q&A 使用 Markdown 标题格式（### Qn: 问题）
"""
        profiles = settings._normalize_prompt_profiles({
            "deep_reading": {"system": "s", "instruction": weak_prompt}
        })
        instruction = profiles["deep_reading"]["instruction"]

        self.assertIn("必须按顺序完整回答以下 2 个问题", instruction)
        self.assertIn("问题一？", instruction)
        self.assertIn("问题二？", instruction)
        self.assertIn("必须输出 Q1 到 Q2 的全部条目", instruction)


class TaskLogDatabaseTests(unittest.TestCase):
    def test_task_logs_include_ordered_pipeline_steps(self):
        import database

        original_dir = database.DB_DIR
        original_path = database.DB_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                database.DB_DIR = tmp
                database.DB_PATH = os.path.join(tmp, "papers.db")
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
            database.DB_DIR = original_dir
            database.DB_PATH = original_path

    def test_startup_reconciliation_interrupts_orphaned_task_runs(self):
        import database

        original_dir = database.DB_DIR
        original_path = database.DB_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                database.DB_DIR = tmp
                database.DB_PATH = os.path.join(tmp, "papers.db")
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
            database.DB_DIR = original_dir
            database.DB_PATH = original_path

    def test_clearing_parent_logs_cascades_pipeline_steps(self):
        import database

        original_dir = database.DB_DIR
        original_path = database.DB_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                database.DB_DIR = tmp
                database.DB_PATH = os.path.join(tmp, "papers.db")
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
            database.DB_DIR = original_dir
            database.DB_PATH = original_path


class DummyOpenAI:
    models_response = types.SimpleNamespace(data=[])
    models_error = None
    chat_response = None
    last_chat_kwargs = None
    last_init_kwargs = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        DummyOpenAI.last_init_kwargs = kwargs
        self.models = types.SimpleNamespace(list=self._list_models)
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create_chat_completion)
        )

    def _list_models(self):
        if DummyOpenAI.models_error:
            raise DummyOpenAI.models_error
        return DummyOpenAI.models_response

    def _create_chat_completion(self, **kwargs):
        DummyOpenAI.last_chat_kwargs = kwargs
        return DummyOpenAI.chat_response


class DummyHttpxClient:
    last_kwargs = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        DummyHttpxClient.last_kwargs = kwargs


def install_import_stubs():
    os.environ.setdefault("FLASK_SECRET_KEY", "test-secret-key")
    flask_mod = types.ModuleType("flask")

    class FakeFlask:
        def __init__(self, *args, **kwargs):
            self.secret_key = None
            self.config = {}

        def route(self, *args, **kwargs):
            def decorator(func):
                return func
            return decorator

        def before_request(self, func):
            return func

        def context_processor(self, func):
            return func

        def run(self, *args, **kwargs):
            pass

    def jsonify(*args, **kwargs):
        if args and kwargs:
            return {"args": args, **kwargs}
        if kwargs:
            return kwargs
        if len(args) == 1:
            return args[0]
        return list(args)

    class FakeSession(dict):
        permanent = False

        def clear(self):
            self.permanent = False
            super().clear()

    flask_mod.Flask = FakeFlask
    flask_mod.render_template = lambda *args, **kwargs: ""
    flask_mod.request = types.SimpleNamespace(args={}, get_json=lambda: {})
    flask_mod.jsonify = jsonify
    flask_mod.Response = lambda *args, **kwargs: types.SimpleNamespace(args=args, kwargs=kwargs)
    flask_mod.session = FakeSession()
    flask_mod.redirect = lambda target: {"redirect": target}
    flask_mod.url_for = lambda endpoint, **kwargs: "/" + endpoint
    sys.modules.setdefault("flask", flask_mod)

    sched_mod = types.ModuleType("apscheduler.schedulers.background")

    class FakeScheduler:
        running = False

        def add_job(self, *args, **kwargs):
            pass

        def remove_job(self, *args, **kwargs):
            pass

        def get_job(self, *args, **kwargs):
            return None

        def start(self):
            self.running = True
            pass

        def get_jobs(self):
            return []

    sched_mod.BackgroundScheduler = FakeScheduler
    sys.modules.setdefault("apscheduler", types.ModuleType("apscheduler"))
    sys.modules.setdefault("apscheduler.schedulers", types.ModuleType("apscheduler.schedulers"))
    sys.modules.setdefault("apscheduler.schedulers.background", sched_mod)

    sys.modules.setdefault("arxiv", types.ModuleType("arxiv"))
    sys.modules.setdefault("fitz", types.ModuleType("fitz"))

    openai_mod = types.ModuleType("openai")
    openai_mod.OpenAI = DummyOpenAI
    openai_mod.DefaultHttpxClient = DummyHttpxClient
    sys.modules["openai"] = openai_mod


class FakeRequest:
    def __init__(self, data=None, endpoint="", method="POST", path="/api/test", args=None):
        self._data = data
        self.endpoint = endpoint
        self.method = method
        self.path = path
        self.full_path = path
        self.query_string = b""
        self.args = FakeArgs(args or {})

    def get_json(self, *args, **kwargs):
        return self._data


class ProviderEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_import_stubs()
        import app as app_module
        cls.app_module = app_module

    def tearDown(self):
        DummyOpenAI.models_error = None
        DummyOpenAI.models_response = types.SimpleNamespace(data=[])
        DummyOpenAI.chat_response = None
        DummyOpenAI.last_chat_kwargs = None
        DummyOpenAI.last_init_kwargs = None
        DummyHttpxClient.last_kwargs = None

    def test_provider_models_endpoint_returns_sorted_models(self):
        app_module = self.app_module
        app_module.request = FakeRequest({
            "provider_key": "demo",
            "api_key": "sk-test",
            "base_url": "https://api.example.com/v1",
        })
        DummyOpenAI.models_response = types.SimpleNamespace(
            data=[types.SimpleNamespace(id="model-b"), {"id": "model-a"}]
        )

        with patch.object(app_module, "update_provider") as update_provider:
            result = app_module.api_provider_models()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["models"], ["model-a", "model-b"])
        update_provider.assert_called_once_with("demo", {"available_models": ["model-a", "model-b"]})

    def test_provider_models_uses_shared_openai_client(self):
        app_module = self.app_module
        app_module.request = FakeRequest({
            "provider_key": "demo",
            "api_key": "sk-test",
            "base_url": "https://api.example.com/v1",
        })
        fake_client = types.SimpleNamespace(
            models=types.SimpleNamespace(list=lambda: types.SimpleNamespace(data=[{"id": "model-a"}]))
        )

        with patch.object(app_module, "get_openai_client", return_value=fake_client) as get_client, \
                patch.object(app_module, "update_provider"):
            result = app_module.api_provider_models()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(get_client.call_args.args[0]["api_key"], "sk-test")
        self.assertEqual(get_client.call_args.args[0]["base_url"], "https://api.example.com/v1")

    def test_provider_models_endpoint_reports_fetch_errors(self):
        app_module = self.app_module
        app_module.request = FakeRequest({
            "api_key": "sk-test",
            "base_url": "https://api.example.com/v1",
        })
        DummyOpenAI.models_error = RuntimeError("boom")

        result, status = app_module.api_provider_models()

        self.assertEqual(status, 500)
        self.assertEqual(result["status"], "error")
        self.assertIn("boom", result["message"])

    def test_detect_thinking_uses_provider_specific_request_and_saves_result(self):
        app_module = self.app_module
        message = types.SimpleNamespace(content="2", reasoning_content="thinking")
        choice = types.SimpleNamespace(message=message)
        DummyOpenAI.chat_response = types.SimpleNamespace(
            choices=[choice],
            usage=types.SimpleNamespace(
                completion_tokens_details=types.SimpleNamespace(reasoning_tokens=12)
            ),
        )
        cfg = {
            "api_key": "sk-test",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-v4-pro",
            "is_thinking": False,
            "thinking_effort": "high",
        }

        with patch.object(app_module, "get_ai_config", return_value=cfg), \
             patch.object(app_module, "load_settings", return_value={"active_provider": "deepseek"}), \
             patch.object(app_module, "update_provider") as update_provider:
            result = app_module.api_detect_thinking()

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["is_thinking"])
        self.assertEqual(result["thinking_protocol"], "deepseek_v4")
        self.assertEqual(DummyOpenAI.last_chat_kwargs["extra_body"], {"thinking": {"type": "enabled"}})
        self.assertNotIn("temperature", DummyOpenAI.last_chat_kwargs)
        update_provider.assert_called_once_with(
            "deepseek",
            {"is_thinking": True, "thinking_effort": "high"},
        )

    def test_openai_client_uses_matching_proxy_and_ignores_env(self):
        import analyzer

        with patch.object(analyzer, "get_proxy_config", return_value={
            "enabled": True,
            "http": "http://proxy.local:8080",
            "https": "http://secure-proxy.local:7890",
        }):
            analyzer.get_openai_client({
                "api_key": "sk-test",
                "base_url": "https://api.example.com/v1",
            })

        self.assertEqual(DummyHttpxClient.last_kwargs["proxy"], "http://secure-proxy.local:7890")
        self.assertFalse(DummyHttpxClient.last_kwargs["trust_env"])
        self.assertIs(DummyOpenAI.last_init_kwargs["http_client"].__class__, DummyHttpxClient)

    def test_openai_client_disables_environment_proxy_when_proxy_is_off(self):
        import analyzer

        with patch.object(analyzer, "get_proxy_config", return_value={
            "enabled": False,
            "http": "http://proxy.local:8080",
            "https": "http://secure-proxy.local:7890",
        }):
            analyzer.get_openai_client({
                "api_key": "sk-test",
                "base_url": "https://api.example.com/v1",
            })

        self.assertNotIn("proxy", DummyHttpxClient.last_kwargs)
        self.assertFalse(DummyHttpxClient.last_kwargs["trust_env"])

    def test_network_llm_test_uses_basic_analysis_route(self):
        app_module = self.app_module
        message = types.SimpleNamespace(content="ok")
        fake_client = types.SimpleNamespace(
            chat=types.SimpleNamespace(
                completions=types.SimpleNamespace(
                    create=lambda **kwargs: types.SimpleNamespace(
                        choices=[types.SimpleNamespace(message=message)]
                    )
                )
            )
        )
        cfg = {
            "provider_key": "deepseek",
            "api_key": "sk-test",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "max_tokens_enabled": True,
            "max_tokens": 100,
        }

        with patch.object(app_module, "get_ai_task_config", return_value=cfg), \
                patch.object(app_module, "get_openai_client", return_value=fake_client) as get_client:
            result = app_module.api_network_test_llm()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["provider_key"], "deepseek")
        self.assertEqual(result["model"], "deepseek-chat")
        self.assertGreaterEqual(result["duration_ms"], 0)
        get_client.assert_called_once_with(cfg)

    def test_provider_list_does_not_return_plain_api_key(self):
        app_module = self.app_module
        with patch.object(app_module, "load_settings", return_value={
            "active_provider": "demo",
            "providers": {
                "demo": {
                    "name": "Demo",
                    "api_key": "sk-secret-value",
                    "base_url": "https://api.example.com/v1",
                    "model": "demo-model",
                }
            },
        }):
            result = app_module.api_list_providers()

        provider = result["providers"]["demo"]
        self.assertNotIn("api_key", provider)
        self.assertEqual(provider["api_key_masked"], "sk-s****alue")

    def test_auth_blocks_protected_api_when_not_logged_in(self):
        app_module = self.app_module
        app_module.session.clear()
        app_module.request = FakeRequest(
            {},
            endpoint="api_save_concurrency",
            method="POST",
            path="/api/settings/concurrency",
        )

        with patch.object(app_module, "has_admin_password", return_value=True):
            result, status = app_module.require_auth_for_protected_routes()

        self.assertEqual(status, 401)
        self.assertTrue(result["auth_required"])

    def test_auth_login_sets_permanent_session_and_token(self):
        app_module = self.app_module
        app_module.session.clear()
        app_module.request = FakeRequest({"password": "secret"})

        with patch.object(app_module, "verify_admin_password", return_value=True), \
             patch.object(app_module, "get_admin_password", return_value="hash-v1"):
            result = app_module.api_auth_login()

        self.assertEqual(result["status"], "ok")
        self.assertTrue(app_module.session.permanent)
        self.assertTrue(app_module.session["admin_authenticated"])
        self.assertEqual(
            app_module.session["admin_auth_token"],
            app_module._admin_auth_token("hash-v1"),
        )

    def test_auth_rejects_legacy_session_without_password_token(self):
        app_module = self.app_module
        app_module.session.clear()
        app_module.session["admin_authenticated"] = True

        with patch.object(app_module, "has_admin_password", return_value=True), \
             patch.object(app_module, "get_admin_password", return_value="hash-v1"):
            self.assertFalse(app_module.is_authenticated())

    def test_auth_rejects_session_after_password_hash_changes(self):
        app_module = self.app_module
        app_module.session.clear()
        app_module.session["admin_authenticated"] = True
        app_module.session["admin_auth_token"] = app_module._admin_auth_token("hash-v1")

        with patch.object(app_module, "has_admin_password", return_value=True), \
             patch.object(app_module, "get_admin_password", return_value="hash-v2"):
            self.assertFalse(app_module.is_authenticated())

    def test_set_admin_password_refreshes_current_session_token(self):
        app_module = self.app_module
        app_module.session.clear()
        app_module.session.permanent = True
        app_module.session["admin_authenticated"] = True
        app_module.session["admin_auth_token"] = app_module._admin_auth_token("old-hash")
        app_module.request = FakeRequest({
            "current_password": "old-secret",
            "new_password": "new-secret",
        })

        with patch.object(app_module, "has_admin_password", return_value=True), \
             patch.object(app_module, "verify_admin_password", return_value=True), \
             patch.object(app_module, "set_admin_password") as set_password, \
             patch.object(app_module, "get_admin_password", return_value="new-hash"):
            result = app_module.api_set_admin_password()

        self.assertEqual(result["status"], "ok")
        set_password.assert_called_once_with("new-secret")
        self.assertTrue(app_module.session.permanent)
        self.assertTrue(app_module.session["admin_authenticated"])
        self.assertEqual(
            app_module.session["admin_auth_token"],
            app_module._admin_auth_token("new-hash"),
        )

    def test_todo_add_remove_are_public_even_when_password_enabled(self):
        app_module = self.app_module
        app_module.session.clear()

        for endpoint, method in (("api_add_todo", "POST"), ("api_remove_todo", "DELETE")):
            with self.subTest(endpoint=endpoint):
                app_module.request = FakeRequest(
                    {},
                    endpoint=endpoint,
                    method=method,
                    path="/api/paper/2601.00001/todo",
                )
                with patch.object(app_module, "has_admin_password", return_value=True):
                    self.assertIsNone(app_module.require_auth_for_protected_routes())

    def test_promo_pages_are_public_even_when_password_enabled(self):
        app_module = self.app_module
        app_module.session.clear()

        for endpoint, path in (("about_page", "/about"), ("vision_page", "/vision")):
            with self.subTest(endpoint=endpoint):
                app_module.request = FakeRequest(
                    endpoint=endpoint,
                    method="GET",
                    path=path,
                )
                with patch.object(app_module, "has_admin_password", return_value=True):
                    self.assertIsNone(app_module.require_auth_for_protected_routes())

    def test_promo_page_routes_render_their_public_templates(self):
        app_module = self.app_module

        for view_name, template_name in (
            ("about_page", "about.html"),
            ("vision_page", "vision.html"),
        ):
            with self.subTest(view_name=view_name), \
                 patch.object(app_module, "render_template", return_value=template_name) as render:
                result = getattr(app_module, view_name)()

                self.assertEqual(result, template_name)
                render.assert_called_once_with(template_name)

    def test_todo_read_status_changes_still_require_login(self):
        app_module = self.app_module
        app_module.session.clear()
        app_module.request = FakeRequest(
            {},
            endpoint="api_mark_read",
            method="POST",
            path="/api/paper/2601.00001/todo/read",
        )

        with patch.object(app_module, "has_admin_password", return_value=True):
            result, status = app_module.require_auth_for_protected_routes()

        self.assertEqual(status, 401)
        self.assertTrue(result["auth_required"])

    def test_learning_write_api_requires_login_when_password_enabled(self):
        app_module = self.app_module
        app_module.session.clear()
        app_module.request = FakeRequest(
            {"message": "请解释这篇论文"},
            endpoint="api_paper_chat_send",
            method="POST",
            path="/api/paper/2601.00001/chat/messages",
        )

        with patch.object(app_module, "has_admin_password", return_value=True):
            result, status = app_module.require_auth_for_protected_routes()

        self.assertEqual(status, 401)
        self.assertTrue(result["auth_required"])

    def test_clear_admin_password_requires_current_password(self):
        app_module = self.app_module
        app_module.request = FakeRequest({"current_password": "wrong"})

        with patch.object(app_module, "has_admin_password", return_value=True), \
             patch.object(app_module, "verify_admin_password", return_value=False), \
             patch.object(app_module, "set_admin_password") as set_password:
            result, status = app_module.api_clear_admin_password()

        self.assertEqual(status, 403)
        self.assertEqual(result["status"], "error")
        set_password.assert_not_called()

    def test_batch_analyze_uses_selected_papers(self):
        app_module = self.app_module
        selected = [
            {"id": 1, "arxiv_id": "2601.00001", "title": "A", "authors": [], "abstract": ""},
            {"id": 2, "arxiv_id": "2601.00002", "title": "B", "authors": [], "abstract": ""},
        ]
        app_module.request = FakeRequest({"arxiv_ids": ["2601.00001", "2601.00002"]})

        with patch("database.get_unanalyzed_papers_by_ids", return_value=selected), \
             patch.object(app_module, "get_concurrency", return_value=3), \
             patch.object(app_module, "analyze_papers", return_value=2) as analyze_papers:
            result = app_module.api_batch_analyze_papers()

        self.assertEqual(result["status"], "ok")
        analyze_papers.assert_called_once_with(selected, concurrency=3)

    def test_schedule_endpoint_saves_and_reconfigures(self):
        app_module = self.app_module
        current = {
            "enabled": True,
            "days_of_week": ["mon", "wed", "fri"],
            "hour": 10,
            "minute": 0,
            "fetch_days": 7,
            "analyze_limit": 250,
        }
        saved = {**current, "hour": 8, "minute": 30}
        app_module.request = FakeRequest({"enabled": True, "hour": 8, "minute": 30})

        with patch.object(app_module, "save_schedule_config", return_value=True) as save_schedule, \
             patch.object(app_module, "get_schedule_config", side_effect=[current, saved]), \
             patch.object(app_module, "configure_daily_job") as configure_daily_job:
            result = app_module.api_save_schedule_config()

        self.assertEqual(result["status"], "ok")
        save_schedule.assert_called_once_with(saved)
        configure_daily_job.assert_called_once_with(saved)

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

        with patch.object(app_module.scheduler, "get_job", return_value=None), \
             patch.object(app_module.scheduler, "add_job") as add_job:
            app_module.configure_daily_job(schedule)

        kwargs = add_job.call_args.kwargs
        self.assertEqual(kwargs["day_of_week"], "mon,wed,fri")
        self.assertEqual((kwargs["hour"], kwargs["minute"]), (8, 30))
        self.assertEqual(kwargs["max_instances"], 1)
        self.assertTrue(kwargs["coalesce"])

    def test_scheduled_tasks_endpoint_includes_timezone_config_and_last_run(self):
        app_module = self.app_module
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

        with patch.object(app_module, "get_schedule_config", return_value=schedule), \
             patch.object(app_module.scheduler, "get_jobs", return_value=[job]), \
             patch.object(app_module, "get_task_logs", return_value=([last_run], 1)):
            result = app_module.api_scheduled_tasks()

        self.assertEqual(result["days_of_week"], ["mon", "tue"])
        self.assertEqual(result["fetch_days"], 5)
        self.assertEqual(result["analyze_limit"], 200)
        self.assertTrue(result["timezone"])
        self.assertEqual(result["last_run"], last_run)

    def test_app_startup_reconciles_orphaned_running_tasks_before_scheduling(self):
        app_module = self.app_module

        with patch.object(app_module, "init_db"), \
             patch.object(app_module, "interrupt_running_task_logs", return_value=2) as interrupt, \
             patch.object(app_module, "configure_daily_job") as configure:
            app_module.create_app()

        interrupt.assert_called_once()
        configure.assert_called_once()

    def test_get_webdav_backup_endpoint_masks_password(self):
        app_module = self.app_module

        with patch.object(app_module, "get_webdav_backup_config", return_value={
            "enabled": True,
            "url": "https://dav.example.com",
            "username": "alice",
            "password_masked": "******",
        }) as get_config:
            result = app_module.api_get_webdav_backup()

        get_config.assert_called_once_with(mask_password=True)
        self.assertEqual(result["password_masked"], "******")
        self.assertNotIn("password", result)

    def test_save_webdav_backup_endpoint_saves_config(self):
        app_module = self.app_module
        app_module.request = FakeRequest({
            "enabled": True,
            "url": "https://dav.example.com",
            "username": "alice",
            "password": "",
            "remote_dir": "arxiv",
            "history_days": "3",
        })

        with patch.object(app_module, "save_webdav_backup_config", return_value=True) as save_config, \
             patch.object(app_module, "get_webdav_backup_config", return_value={"password_masked": "******"}):
            result = app_module.api_save_webdav_backup()

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
        app_module = self.app_module
        app_module.request = FakeRequest({}, endpoint="api_run_webdav_backup", method="POST", path="/api/backup/webdav/run")
        result_payload = {
            "status": "ok",
            "message": "WebDAV 备份完成",
            "uploaded_files": ["arxiv-backup-20260615-120000.zip", "arxiv-backup-latest.zip"],
            "deleted_files": [],
            "archive_size": "1.0 KB",
            "db_size": "2.0 KB",
            "last_uploaded_file": "arxiv-backup-20260615-120000.zip",
        }

        with patch.object(app_module, "start_task_log", return_value=7) as start_log, \
             patch.object(app_module, "finish_task_log") as finish_log, \
             patch.object(app_module, "run_webdav_backup", return_value=result_payload) as backup:
            result = app_module.api_run_webdav_backup()

        self.assertEqual(result["status"], "ok")
        backup.assert_called_once_with(force=True)
        start_log.assert_called_once_with("webdav_backup", "WebDAV 云同步备份")
        self.assertEqual(finish_log.call_args.args[1], "success")

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

        with patch.object(app_module, "start_task_log", return_value=1), \
             patch.object(app_module, "initialize_task_log_steps") as initialize_steps, \
             patch.object(app_module, "set_task_log_step_status") as set_step, \
             patch.object(app_module, "finish_task_log") as finish_log, \
             patch.object(app_module, "get_schedule_config", return_value=schedule), \
             patch.object(app_module, "fetch_latest_papers", return_value=[]) as fetch, \
             patch.object(app_module, "get_concurrency", return_value=2), \
             patch.object(app_module, "analyze_pending_papers", return_value=0) as analyze, \
             patch.object(app_module, "get_all_dates", return_value=[("2026-06-23",)]), \
             patch.object(app_module, "get_personalization_config", return_value={"research_interests": ""}), \
             patch.object(app_module, "recommend_pending_papers") as recommend, \
             patch.object(app_module, "generate_report_content", return_value=("html", 0, 0, 0.0)), \
             patch.object(app_module, "save_report"), \
             patch.object(app_module, "get_email_report_config", return_value={"enabled": False}), \
             patch.object(app_module, "get_webdav_backup_config", return_value={"enabled": False}):
            result = app_module.daily_pipeline()

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

        with patch.object(app_module, "start_task_log", return_value=1), \
             patch.object(app_module, "initialize_task_log_steps"), \
             patch.object(app_module, "set_task_log_step_status") as set_step, \
             patch.object(app_module, "finish_task_log") as finish_log, \
             patch.object(app_module, "get_schedule_config", return_value={
                 "fetch_days": 3,
                 "analyze_limit": 1000,
                 "fetch_retry_interval_minutes": 10,
                 "fetch_max_retries": 20,
             }), \
             patch.object(app_module, "fetch_latest_papers", return_value=[]), \
             patch.object(app_module, "get_concurrency", return_value=2), \
             patch.object(app_module, "analyze_pending_papers", return_value=0), \
             patch.object(app_module, "get_all_dates", return_value=[("2026-06-15",)]), \
             patch.object(app_module, "get_personalization_config", return_value={"research_interests": "robotics"}), \
             patch.object(app_module, "recommend_pending_papers", return_value=0), \
             patch.object(app_module, "generate_report_content", return_value=("html", 1, 1, 0.0)), \
             patch.object(app_module, "save_report"), \
             patch.object(app_module, "get_email_report_config", return_value={"enabled": False}), \
             patch.object(app_module, "get_webdav_backup_config", return_value={"enabled": True}), \
             patch.object(app_module, "_run_webdav_backup_task", side_effect=RuntimeError("dav down")):
            result = app_module.daily_pipeline()

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

        with patch.object(app_module, "start_task_log", return_value=1), \
             patch.object(app_module, "initialize_task_log_steps"), \
             patch.object(app_module, "set_task_log_step_status") as set_step, \
             patch.object(app_module, "finish_task_log") as finish_log, \
             patch.object(app_module, "get_schedule_config", return_value={
                 "fetch_days": 3,
                 "analyze_limit": 1000,
                 "fetch_retry_interval_minutes": 10,
                 "fetch_max_retries": 20,
             }), \
             patch.object(app_module, "fetch_latest_papers", return_value=[]), \
             patch.object(app_module, "get_concurrency", return_value=2), \
             patch.object(app_module, "analyze_pending_papers", return_value=0), \
             patch.object(app_module, "get_all_dates", return_value=[("2026-06-17",)]), \
             patch.object(app_module, "get_personalization_config", return_value={"research_interests": "robotics"}), \
             patch.object(app_module, "recommend_pending_papers", return_value=0), \
             patch.object(app_module, "generate_report_content", return_value=("html", 1, 1, 4.0)), \
             patch.object(app_module, "save_report"), \
             patch.object(app_module, "get_email_report_config", return_value={"enabled": True}), \
             patch.object(app_module, "_run_email_report_task", return_value=skipped) as email_task, \
             patch.object(app_module, "get_webdav_backup_config", return_value={"enabled": False}):
            app_module.daily_pipeline()

        self.assertFalse(email_task.call_args.kwargs["log_task"])
        self.assertEqual(finish_log.call_args.args[1], "success")
        self.assertIn(("email", "skipped"), {(call.args[1], call.args[2]) for call in set_step.call_args_list})

    def test_scheduled_pipeline_skips_when_full_pipeline_is_already_running(self):
        app_module = self.app_module
        self.assertTrue(app_module.pipeline_lock.acquire(blocking=False))
        try:
            with patch.object(app_module, "start_task_log", return_value=12), \
                 patch.object(app_module, "finish_task_log") as finish_log, \
                 patch.object(app_module, "fetch_latest_papers") as fetch:
                result = app_module.daily_pipeline()
        finally:
            app_module.pipeline_lock.release()

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(finish_log.call_args.args[1], "skipped")
        fetch.assert_not_called()

    def test_manual_combined_run_returns_conflict_when_pipeline_is_busy(self):
        app_module = self.app_module
        app_module.request = FakeRequest(args={"task_id": "manual-1"})
        self.assertTrue(app_module.pipeline_lock.acquire(blocking=False))
        try:
            with patch.object(app_module, "start_task_log") as start_log, \
                 patch.object(app_module, "fetch_latest_papers") as fetch:
                result, status = app_module.api_run()
        finally:
            app_module.pipeline_lock.release()

        self.assertEqual(status, 409)
        self.assertEqual(result["status"], "error")
        self.assertIn("正在运行", result["message"])
        start_log.assert_not_called()
        fetch.assert_not_called()

    def test_daily_pipeline_stops_after_core_step_failure(self):
        app_module = self.app_module

        with patch.object(app_module, "start_task_log", return_value=1), \
             patch.object(app_module, "initialize_task_log_steps"), \
             patch.object(app_module, "set_task_log_step_status") as set_step, \
             patch.object(app_module, "finish_task_log") as finish_log, \
             patch.object(app_module, "get_schedule_config", return_value={
                 "fetch_days": 3,
                 "analyze_limit": 1000,
                 "fetch_retry_interval_minutes": 10,
                 "fetch_max_retries": 0,
             }), \
             patch.object(app_module, "fetch_latest_papers", side_effect=RuntimeError("arXiv down")), \
             patch.object(app_module, "analyze_pending_papers") as analyze:
            result = app_module.daily_pipeline()

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

        with patch.object(app_module, "start_task_log", return_value=9), \
             patch.object(app_module, "finish_task_log") as finish_log, \
             patch.object(app_module, "get_email_report_config", return_value={
                 "enabled": True,
                 "last_sent_report_date": "2026-06-16",
             }), \
             patch.object(app_module, "generate_report_ai_summary", return_value=(None, "summary model down")) as summary, \
             patch.object(app_module, "send_report_email", return_value=send_result) as send:
            result = app_module._run_email_report_task(report, force=False)

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

        with patch.object(app_module, "start_task_log", return_value=10), \
             patch.object(app_module, "finish_task_log") as finish_log, \
             patch.object(app_module, "get_email_report_config", return_value={
                 "enabled": True,
                 "last_sent_report_date": "2026-06-17",
             }), \
             patch.object(app_module, "generate_report_ai_summary") as summary, \
             patch.object(app_module, "send_report_email") as send:
            result = app_module._run_email_report_task(report, force=False)

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

        with patch.object(app_module, "start_task_log", return_value=11), \
             patch.object(app_module, "finish_task_log"), \
             patch.object(app_module, "get_email_report_config", return_value={
                 "enabled": True,
                 "last_sent_report_date": "2026-06-17",
             }), \
             patch.object(app_module, "generate_report_ai_summary", return_value=("summary", "")) as summary, \
             patch.object(app_module, "send_report_email", return_value=send_result) as send:
            result = app_module._run_email_report_task(report, force=True)

        self.assertEqual(result["status"], "ok")
        summary.assert_called_once_with("2026-06-17")
        send.assert_called_once()
        self.assertTrue(send.call_args.kwargs["force"])

    def test_save_personalization_saves_interest_without_recommendation_call(self):
        app_module = self.app_module
        app_module.request = FakeRequest({"research_interests": "robotics"})

        with patch.object(app_module, "save_personalization_config", return_value=True) as save_personalization, \
             patch.object(app_module, "get_personalization_config", return_value={"research_interests": "robotics"}), \
             patch.object(app_module, "recommend_pending_papers") as recommend:
            result = app_module.api_save_personalization()

        self.assertEqual(result["status"], "ok")
        save_personalization.assert_called_once_with({"research_interests": "robotics"})
        recommend.assert_not_called()

    def test_recalculate_recommendations_calls_recommendation_task(self):
        app_module = self.app_module
        app_module.request = FakeRequest(
            {"limit": 5},
            endpoint="api_recalculate_recommendations",
            method="POST",
            path="/api/recommendations/recalculate",
            args={"task_id": "rec-task"},
        )

        with patch.object(app_module, "start_task_log", return_value=1), \
             patch.object(app_module, "finish_task_log"), \
             patch.object(app_module, "get_personalization_config", return_value={"research_interests": "robotics"}), \
             patch.object(app_module, "get_concurrency", return_value=2), \
             patch.object(app_module, "recommend_pending_papers", return_value=3) as recommend:
            result = app_module.api_recalculate_recommendations()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["count"], 3)
        self.assertEqual(recommend.call_args.kwargs["limit"], 5)
        self.assertEqual(recommend.call_args.kwargs["concurrency"], 2)
        self.assertIsNone(recommend.call_args.kwargs["date"])

    def test_generate_report_default_does_not_call_ai_summary(self):
        app_module = self.app_module
        app_module.request = FakeRequest({}, endpoint="api_generate", method="POST", path="/api/generate")

        with patch.object(app_module, "start_task_log", return_value=1), \
             patch.object(app_module, "finish_task_log"), \
             patch.object(app_module, "get_all_dates", return_value=[("2026-01-01",)]), \
             patch.object(app_module, "get_concurrency", return_value=2), \
             patch.object(app_module, "recommend_pending_papers", return_value=1) as recommend, \
             patch.object(app_module, "generate_report_ai_summary") as ai_summary, \
             patch.object(app_module, "generate_report_content", return_value=("html", 2, 1, 4.0)) as report_content, \
             patch.object(app_module, "save_report") as save_report:
            result = app_module.api_generate()

        self.assertEqual(result["status"], "ok")
        ai_summary.assert_not_called()
        recommend.assert_called_once_with(limit=1000, date="2026-01-01", concurrency=2)
        report_content.assert_called_once_with("2026-01-01", ai_summary=None)
        save_report.assert_called_once()

    def test_generate_report_recommend_zero_skips_recommendation(self):
        app_module = self.app_module
        app_module.request = FakeRequest(
            {},
            endpoint="api_generate",
            method="POST",
            path="/api/generate",
            args={"date": "2026-01-01", "recommend": "0"},
        )

        with patch.object(app_module, "start_task_log", return_value=1), \
             patch.object(app_module, "finish_task_log"), \
             patch.object(app_module, "recommend_pending_papers") as recommend, \
             patch.object(app_module, "generate_report_content", return_value=("html", 2, 1, 4.0)) as report_content, \
             patch.object(app_module, "save_report"):
            result = app_module.api_generate()

        self.assertEqual(result["status"], "ok")
        recommend.assert_not_called()
        report_content.assert_called_once_with("2026-01-01", ai_summary=None)

    def test_generate_report_with_ai_summary_calls_report_task(self):
        app_module = self.app_module
        app_module.request = FakeRequest(
            {},
            endpoint="api_generate",
            method="POST",
            path="/api/generate",
            args={"date": "2026-01-01", "ai_summary": "1"},
        )

        with patch.object(app_module, "start_task_log", return_value=1), \
             patch.object(app_module, "finish_task_log"), \
             patch.object(app_module, "get_concurrency", return_value=2), \
             patch.object(app_module, "recommend_pending_papers", return_value=0), \
             patch.object(app_module, "generate_report_ai_summary", return_value=("导读", None)) as ai_summary, \
             patch.object(app_module, "generate_report_content", return_value=("html", 2, 1, 4.0)) as report_content, \
             patch.object(app_module, "save_report"):
            result = app_module.api_generate()

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["ai_summary"])
        ai_summary.assert_called_once_with("2026-01-01")
        report_content.assert_called_once_with("2026-01-01", ai_summary="导读")

    def test_reanalyze_updates_only_qa_analysis(self):
        app_module = self.app_module
        paper = {
            "id": 9,
            "arxiv_id": "2601.00009",
            "authors": "[]",
            "categories": "[]",
        }
        deep_result = {
            "qa_analysis": "### Q1: deep",
            "tags": ["should-not-write"],
            "rating": 1,
            "summary_cn": "should-not-write",
            "value_comment": "should-not-write",
        }

        with patch.object(app_module, "get_paper_by_arxiv_id", return_value=paper), \
             patch.object(app_module, "analyze_paper_full", return_value=(paper, deep_result, None)), \
             patch.object(app_module, "update_analysis") as update_analysis:
            result = app_module.api_reanalyze_paper("2601.00009")

        self.assertEqual(result["status"], "ok")
        update_analysis.assert_called_once_with(9, {"qa_analysis": "### Q1: deep"})

    def test_reanalyze_warns_and_preserves_old_qa_when_repair_is_incomplete(self):
        app_module = self.app_module
        paper = {
            "id": 9,
            "arxiv_id": "2601.00009",
            "authors": "[]",
            "categories": "[]",
        }
        deep_result = {
            "qa_analysis": "### Q1: partial",
            "complete": False,
            "continuation_used": True,
            "missing_questions": ["Q2"],
            "finish_reason": "length",
        }

        with patch.object(app_module, "get_paper_by_arxiv_id", return_value=paper), \
             patch.object(app_module, "analyze_paper_full", return_value=(paper, deep_result, None)), \
             patch.object(app_module, "update_analysis") as update_analysis:
            result = app_module.api_reanalyze_paper("2601.00009")

        self.assertEqual(result["status"], "warning")
        self.assertEqual(result["missing_questions"], ["Q2"])
        self.assertTrue(result["continuation_used"])
        update_analysis.assert_not_called()

    def test_add_paper_runs_basic_then_deep_reading(self):
        app_module = self.app_module
        app_module.request = FakeRequest({"input": "2601.00010", "task_id": "t1"})
        paper = {
            "id": 10,
            "arxiv_id": "2601.00010",
            "title": "New Paper",
            "authors": '["Alice"]',
            "categories": '["cs.RO"]',
            "abstract": "Abstract",
            "pdf_url": "https://arxiv.org/pdf/2601.00010",
        }
        basic_result = {"tags": ["VLA"], "summary_cn": "摘要", "summary_en": "", "rating": 4, "value_comment": "有价值"}
        deep_result = {"qa_analysis": "### Q1: deep"}
        events = []

        def fake_basic(paper_data):
            events.append("basic")
            return paper_data, basic_result, None

        def fake_insert(paper_id, result):
            events.append("insert")
            self.assertEqual(paper_id, 10)
            self.assertEqual(result, basic_result)
            return 1

        def fake_deep(paper_data):
            events.append("deep")
            return paper_data, deep_result, None

        def fake_update(paper_id, result):
            events.append("update")
            self.assertEqual(paper_id, 10)
            self.assertEqual(result, {"qa_analysis": "### Q1: deep"})
            return True

        with patch.object(app_module, "parse_arxiv_id", return_value="2601.00010"), \
             patch.object(app_module, "fetch_paper_by_id", return_value=paper), \
             patch.object(app_module, "get_analysis_by_paper_id", return_value=None), \
             patch.object(app_module, "analyze_paper_basic", side_effect=fake_basic), \
             patch.object(app_module, "insert_analysis", side_effect=fake_insert), \
             patch.object(app_module, "analyze_paper_full", side_effect=fake_deep), \
             patch.object(app_module, "update_analysis", side_effect=fake_update):
            result = app_module.api_add_paper()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["rating"], 4)
        self.assertEqual(result["tags"], ["VLA"])
        self.assertEqual(events, ["basic", "insert", "deep", "update"])

    def test_add_paper_preserves_basic_analysis_when_deep_reading_is_incomplete(self):
        app_module = self.app_module
        app_module.request = FakeRequest({"input": "2601.00012", "task_id": "t1"})
        paper = {
            "id": 12,
            "arxiv_id": "2601.00012",
            "title": "Incomplete Deep Reading",
            "authors": '["Alice"]',
            "categories": '["cs.RO"]',
            "abstract": "Abstract",
            "pdf_url": "https://arxiv.org/pdf/2601.00012",
        }
        basic_result = {"tags": ["VLA"], "summary_cn": "摘要", "rating": 4, "value_comment": "有价值"}
        deep_result = {
            "qa_analysis": "### Q1: partial",
            "complete": False,
            "continuation_used": True,
            "missing_questions": ["Q6"],
            "finish_reason": "length",
        }

        with patch.object(app_module, "parse_arxiv_id", return_value="2601.00012"), \
             patch.object(app_module, "fetch_paper_by_id", return_value=paper), \
             patch.object(app_module, "get_analysis_by_paper_id", return_value=None), \
             patch.object(app_module, "analyze_paper_basic", return_value=(paper, basic_result, None)), \
             patch.object(app_module, "insert_analysis", return_value=1), \
             patch.object(app_module, "analyze_paper_full", return_value=(paper, deep_result, None)), \
             patch.object(app_module, "update_analysis") as update_analysis:
            result = app_module.api_add_paper()

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["deep_reading_incomplete"])
        self.assertEqual(result["missing_questions"], ["Q6"])
        update_analysis.assert_not_called()

    def test_add_paper_with_manual_rating_only_still_runs_basic_analysis(self):
        app_module = self.app_module
        app_module.request = FakeRequest({"input": "2601.00011", "task_id": "t1"})
        paper = {
            "id": 11,
            "arxiv_id": "2601.00011",
            "title": "Manual Rating Only",
            "authors": '["Alice"]',
            "categories": '["cs.RO"]',
            "abstract": "Abstract",
            "pdf_url": "https://arxiv.org/pdf/2601.00011",
        }
        basic_result = {"tags": ["VLA"], "summary_cn": "摘要", "summary_en": "", "rating": 4, "value_comment": "有价值"}
        deep_result = {"qa_analysis": "### Q1: deep"}

        with patch.object(app_module, "parse_arxiv_id", return_value="2601.00011"), \
             patch.object(app_module, "fetch_paper_by_id", return_value=paper), \
             patch.object(app_module, "get_analysis_by_paper_id", return_value={"rating": 4, "tags": [], "summary_cn": "", "value_comment": ""}), \
             patch.object(app_module, "analyze_paper_basic", return_value=(paper, basic_result, None)) as basic, \
             patch.object(app_module, "insert_analysis", return_value=None) as insert, \
             patch.object(app_module, "analyze_paper_full", return_value=(paper, deep_result, None)), \
             patch.object(app_module, "update_analysis", return_value=True):
            result = app_module.api_add_paper()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["rating"], 4)
        basic.assert_called_once()
        insert.assert_called_once()


class AiCallRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_import_stubs()
        import analyzer
        cls.analyzer = analyzer

    def test_basic_analysis_uses_short_profile_without_qa(self):
        paper = {
            "id": 1,
            "arxiv_id": "2601.00001",
            "title": "A Robot Paper",
            "authors": ["Alice", "Bob"],
            "abstract": "This is the abstract.",
        }
        fake_result = {"tags": ["VLA"], "rating": 4, "summary_cn": "摘要", "value_comment": "有价值"}

        with patch.object(self.analyzer, "_call_ai", return_value=(fake_result, None)) as call:
            _, result, error = self.analyzer.analyze_paper_basic(paper)

        self.assertIsNone(error)
        self.assertEqual(result["tags"], ["VLA"])
        self.assertEqual(result["rating"], 4)
        messages, _, task_key = call.call_args.args
        self.assertEqual(task_key, "basic_analysis")
        self.assertNotIn("qa_analysis", messages[1]["content"])
        self.assertIn('"rating"', messages[1]["content"])
        self.assertIn("不要把 3 星作为默认安全分", messages[1]["content"])
        self.assertNotIn("{rating_criteria}", messages[1]["content"])
        self.assertIn('"abstract": "This is the abstract."', messages[2]["content"])

    def test_basic_analysis_clamps_out_of_range_rating(self):
        paper = {
            "id": 1,
            "arxiv_id": "2601.00001",
            "title": "A Robot Paper",
            "authors": ["Alice"],
            "abstract": "This is the abstract.",
        }
        fake_result = {"tags": ["VLA"], "rating": 9, "summary_cn": "摘要", "value_comment": "有价值"}

        with patch.object(self.analyzer, "_call_ai", return_value=(fake_result, None)):
            _, result, error = self.analyzer.analyze_paper_basic(paper)

        self.assertIsNone(error)
        self.assertEqual(result["rating"], 5)

    def test_json_cleaner_repairs_invalid_escapes_without_breaking_latex(self):
        content = (
            '{"qa_analysis": "valid latex: \\\\alpha and unicode \\\\u03b1; '
            'invalid markdown \\_ and latex \\uparrow"}'
        )

        cleaned = self.analyzer._clean_json_content(content)
        parsed = json.loads(cleaned)

        self.assertIn(r"\alpha", parsed["qa_analysis"])
        self.assertIn(r"\u03b1", cleaned)
        self.assertIn("invalid markdown _", parsed["qa_analysis"])
        self.assertIn("latex uparrow", parsed["qa_analysis"])

    def test_deep_reading_uses_full_pdf_without_text_limit(self):
        paper = {
            "id": 1,
            "arxiv_id": "2601.00001",
            "title": "A Robot Paper",
            "authors": ["Alice"],
            "abstract": "Abstract",
            "pdf_url": "https://arxiv.org/pdf/2601.00001",
        }
        qa_analysis = "\n\n".join(f"### Q{i}: question {i}\n\nanswer {i}" for i in range(1, 7))
        fake_raw = json.dumps({"qa_analysis": qa_analysis})

        with patch.object(self.analyzer, "get_paper_full_text", return_value="FULL PDF TEXT") as full_text, \
             patch.object(self.analyzer, "_call_ai_raw", return_value=(fake_raw, {"finish_reason": "stop"})) as call:
            _, result, error = self.analyzer.analyze_paper_full(paper)

        self.assertIsNone(error)
        self.assertIn("qa_analysis", result)
        self.assertTrue(result["complete"])
        self.assertFalse(result["continuation_used"])
        self.assertEqual(result["missing_questions"], [])
        full_text.assert_called_once_with("https://arxiv.org/pdf/2601.00001", "2601.00001", max_chars=None)
        messages, _, task_key = call.call_args.args
        self.assertEqual(task_key, "deep_reading")
        for text in ('"tags"', '"rating"', '"summary_cn"', '"value_comment"', "{tag_candidates}", "{rating_criteria}"):
            self.assertNotIn(text, messages[1]["content"])
        self.assertIn('"paper_text": "FULL PDF TEXT"', messages[2]["content"])

    def test_recommendation_uses_recommendation_task_and_interest_payload(self):
        paper = {
            "id": 1,
            "arxiv_id": "2601.00001",
            "title": "A Robot Paper",
            "authors": ["Alice"],
            "abstract": "Abstract",
            "categories": ["cs.RO"],
            "tags": ["VLA"],
            "rating": 4,
            "summary_cn": "摘要",
        }
        fake_result = {
            "recommendation_score": 88,
            "recommendation_reason": "与 VLA 研究兴趣高度相关。",
        }

        with patch.object(self.analyzer, "_call_ai", return_value=(fake_result, None)) as call:
            _, result, error = self.analyzer.analyze_paper_recommendation(
                paper,
                research_interests="VLA and robot learning",
                interest_hash="hash-v1",
            )

        self.assertIsNone(error)
        self.assertEqual(result["recommendation_score"], 88)
        self.assertEqual(result["recommendation_interest_hash"], "hash-v1")
        messages, _, task_key = call.call_args.args
        self.assertEqual(task_key, "recommendation")
        self.assertIn('"research_interests": "VLA and robot learning"', messages[2]["content"])
        self.assertIn('"tags": [', messages[2]["content"])

    def test_learning_text_prefers_cached_pdf_without_download(self):
        paper = {
            "id": 1,
            "arxiv_id": "2601.00001",
            "title": "A Robot Paper",
            "authors": ["Alice"],
            "abstract": "Abstract fallback",
            "pdf_url": "https://arxiv.org/pdf/2601.00001",
        }

        with patch.object(self.analyzer, "get_cached_pdf_path", return_value="/tmp/cached.pdf"), \
             patch.object(self.analyzer, "extract_text_from_pdf", return_value="PDF TEXT") as extract_text, \
             patch.object(self.analyzer, "download_pdf") as download_pdf:
            info = self.analyzer.get_learning_paper_text(paper)

        self.assertEqual(info["paper_text"], "PDF TEXT")
        self.assertTrue(info["used_pdf_cache"])
        self.assertTrue(info["used_pdf_full_text"])
        extract_text.assert_called_once_with("/tmp/cached.pdf")
        download_pdf.assert_not_called()

    def test_learning_text_falls_back_to_abstract_when_pdf_unavailable(self):
        paper = {
            "id": 1,
            "arxiv_id": "2601.00001",
            "title": "A Robot Paper",
            "authors": ["Alice"],
            "abstract": "Abstract fallback",
            "pdf_url": "https://arxiv.org/pdf/2601.00001",
        }

        with patch.object(self.analyzer, "get_cached_pdf_path", return_value=None), \
             patch.object(self.analyzer, "download_pdf", return_value="/tmp/missing.pdf"), \
             patch.object(self.analyzer, "extract_text_from_pdf", side_effect=RuntimeError("bad pdf")):
            info = self.analyzer.get_learning_paper_text(paper)

        self.assertEqual(info["paper_text"], "Abstract fallback")
        self.assertFalse(info["used_pdf_cache"])
        self.assertFalse(info["used_pdf_full_text"])

    def test_learning_messages_keep_volatile_content_after_stable_pdf_prefix(self):
        paper = {
            "id": 1,
            "arxiv_id": "2601.00001",
            "title": "A Robot Paper",
            "authors": ["Alice"],
            "abstract": "Abstract",
        }
        text_info = {"paper_text": "FULL PDF TEXT", "used_pdf_cache": True, "used_pdf_full_text": True}

        messages_a, _ = self.analyzer.build_paper_learning_messages(
            paper,
            "paper_chat",
            "stable instruction",
            [{"role": "user", "content": "first dynamic question"}],
            text_info=text_info,
        )
        messages_b, _ = self.analyzer.build_paper_learning_messages(
            paper,
            "paper_chat",
            "stable instruction",
            [{"role": "user", "content": "second dynamic question"}],
            text_info=text_info,
        )

        self.assertEqual(messages_a[:3], messages_b[:3])
        self.assertIn("FULL PDF TEXT", messages_a[2]["content"])
        self.assertNotIn("first dynamic question", messages_a[2]["content"])
        self.assertEqual(messages_a[3]["content"], "first dynamic question")
        self.assertEqual(messages_b[3]["content"], "second dynamic question")

    def test_generate_paper_quiz_uses_paper_quiz_route_and_parses_questions(self):
        paper = {
            "id": 1,
            "arxiv_id": "2601.00001",
            "title": "A Robot Paper",
            "authors": ["Alice"],
            "abstract": "Abstract",
        }
        response = json.dumps({
            "questions": [
                {"question": "Q1?", "expected_points": ["p1"]},
                {"question": "Q2?", "expected_points": ["p2"]},
                {"question": "Q3?", "expected_points": ["p3"]},
            ]
        }, ensure_ascii=False)

        with patch.object(self.analyzer, "get_learning_paper_text", return_value={
            "paper_text": "FULL PDF TEXT",
            "used_pdf_cache": True,
            "used_pdf_full_text": True,
        }), patch.object(self.analyzer, "_call_ai_raw", return_value=(response, {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "cached_tokens": 80,
            "cache_miss_tokens": 20,
        })) as call:
            questions, error, meta = self.analyzer.generate_paper_quiz(paper, mode="quick3")

        self.assertIsNone(error)
        self.assertEqual(len(questions), 3)
        self.assertEqual(questions[0]["question"], "Q1?")
        messages, _, task_key = call.call_args.args
        self.assertEqual(task_key, "paper_quiz")
        self.assertIn("FULL PDF TEXT", messages[2]["content"])
        self.assertEqual(meta["cached_tokens"], 80)
        self.assertEqual(meta["cache_miss_tokens"], 20)

    def test_extract_usage_reads_deepseek_cache_hit_and_miss_tokens(self):
        usage = types.SimpleNamespace(
            prompt_tokens=120,
            completion_tokens=30,
            total_tokens=150,
            prompt_cache_hit_tokens=90,
            prompt_cache_miss_tokens=30,
        )
        extracted = self.analyzer._extract_usage(types.SimpleNamespace(usage=usage))

        self.assertEqual(extracted["cached_tokens"], 90)
        self.assertEqual(extracted["cache_miss_tokens"], 30)


class PromptAndReportSafetyTests(unittest.TestCase):
    def test_prompt_validation_rejects_missing_required_field(self):
        ok, message = validate_prompt_template("标题: {title}\n摘要: {abstract}")
        self.assertFalse(ok)
        self.assertIn("{authors}", message)

    def test_prompt_validation_rejects_unescaped_json_braces(self):
        ok, message = validate_prompt_template("{title}\n{\"rating\": 3}\n{authors}\n{abstract}\n{tag_candidates}\n{rating_criteria}")
        self.assertFalse(ok)
        self.assertIn("Prompt", message)

    def test_profile_prompt_validation_allows_deep_reading_without_basic_fields(self):
        ok, message = validate_prompt_template('{"qa_analysis": "### Q1: answer"}', profile_key="deep_reading")
        self.assertTrue(ok, message)

        ok, message = validate_prompt_template('{"recommendation_score": 80}', profile_key="recommendation")
        self.assertTrue(ok, message)

        ok, message = validate_prompt_template("只做基础分析", profile_key="basic_analysis")
        self.assertFalse(ok)
        self.assertIn("{tag_candidates}", message)
        self.assertIn("{rating_criteria}", message)

    def test_report_generation_escapes_database_content(self):
        import database

        original_dir = database.DB_DIR
        original_path = database.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            database.DB_DIR = tmp
            database.DB_PATH = os.path.join(tmp, "papers.db")
            database.init_db()
            paper_id = database.insert_paper({
                "arxiv_id": "2601.00001",
                "title": "<script>alert(1)</script>",
                "authors": ["Alice <Admin>"],
                "abstract": "abstract",
                "categories": ["cs.RO"],
                "primary_category": "cs.RO",
                "url": "https://arxiv.org/abs/2601.00001",
                "pdf_url": "https://arxiv.org/pdf/2601.00001",
                "published_date": "2026-01-01",
                "updated_date": "2026-01-01",
            })
            database.insert_analysis(paper_id, {
                "tags": ["<tag>"],
                "summary_cn": "<img src=x onerror=alert(1)>",
                "summary_en": "",
                "rating": 5,
                "value_comment": "<b>bad</b>",
                "qa_analysis": "",
            })
            database.update_recommendation_result(paper_id, 95, "<i>rec</i>", "hash-v1")

            with patch("settings.get_personalization_config", return_value={"research_interests": "机器人基础模型\nVLA"}), \
                 patch("settings.get_research_interest_hash", return_value="hash-v1"):
                content, _, _, _ = database.generate_report_content("2026-01-01")

        database.DB_DIR = original_dir
        database.DB_PATH = original_path
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", content)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", content)
        self.assertIn("&lt;b&gt;bad&lt;/b&gt;", content)
        self.assertIn("&lt;i&gt;rec&lt;/i&gt;", content)
        self.assertNotIn("<script>", content)
        self.assertNotIn("<img", content)
        self.assertNotIn("<i>", content)

    def test_rating_migration_restores_legacy_ai_rating_once(self):
        import sqlite3
        import database

        original_dir = database.DB_DIR
        original_path = database.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            database.DB_DIR = tmp
            database.DB_PATH = os.path.join(tmp, "papers.db")
            conn = sqlite3.connect(database.DB_PATH)
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

            database.init_db()
            conn = sqlite3.connect(database.DB_PATH)
            rows = conn.execute(
                "SELECT id, rating, legacy_ai_rating, rating_restored_from_legacy FROM analysis ORDER BY id"
            ).fetchall()
            self.assertEqual(rows, [(1, 3, 3, 1), (2, 5, 5, 1)])

            conn.execute("UPDATE analysis SET rating = 4 WHERE id = 1")
            conn.commit()
            conn.close()

            database.init_db()
            conn = sqlite3.connect(database.DB_PATH)
            rows = conn.execute(
                "SELECT id, rating, legacy_ai_rating, rating_restored_from_legacy FROM analysis ORDER BY id"
            ).fetchall()
            conn.close()

        database.DB_DIR = original_dir
        database.DB_PATH = original_path
        self.assertEqual(rows, [(1, 4, 3, 1), (2, 5, 5, 1)])

    def test_rating_restore_overwrites_current_rating_when_legacy_exists(self):
        import sqlite3
        import database

        original_dir = database.DB_DIR
        original_path = database.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            database.DB_DIR = tmp
            database.DB_PATH = os.path.join(tmp, "papers.db")
            conn = sqlite3.connect(database.DB_PATH)
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

            database.init_db()
            conn = sqlite3.connect(database.DB_PATH)
            row = conn.execute(
                "SELECT rating, legacy_ai_rating, rating_restored_from_legacy FROM analysis WHERE paper_id = 1"
            ).fetchone()
            conn.execute("UPDATE analysis SET rating = 2 WHERE paper_id = 1")
            conn.commit()
            conn.close()

            database.init_db()
            conn = sqlite3.connect(database.DB_PATH)
            row_after_second_init = conn.execute(
                "SELECT rating, legacy_ai_rating, rating_restored_from_legacy FROM analysis WHERE paper_id = 1"
            ).fetchone()
            conn.close()

        database.DB_DIR = original_dir
        database.DB_PATH = original_path
        self.assertEqual(row, (4, 4, 1))
        self.assertEqual(row_after_second_init, (2, 4, 1))

    def test_new_analysis_defaults_to_zero_manual_rating_without_legacy_backup(self):
        import sqlite3
        import database

        original_dir = database.DB_DIR
        original_path = database.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            database.DB_DIR = tmp
            database.DB_PATH = os.path.join(tmp, "papers.db")
            database.init_db()
            paper_id = database.insert_paper({
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
            database.insert_analysis(paper_id, {
                "tags": ["Robot"],
                "summary_cn": "摘要",
                "summary_en": "",
                "value_comment": "评价",
                "qa_analysis": "",
            })
            conn = sqlite3.connect(database.DB_PATH)
            row = conn.execute("SELECT rating, legacy_ai_rating FROM analysis WHERE paper_id = ?", (paper_id,)).fetchone()
            conn.close()

        database.DB_DIR = original_dir
        database.DB_PATH = original_path
        self.assertEqual(row, (0, None))

    def test_manual_rating_only_record_is_still_pending_basic_analysis(self):
        import database

        original_dir = database.DB_DIR
        original_path = database.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            database.DB_DIR = tmp
            database.DB_PATH = os.path.join(tmp, "papers.db")
            database.init_db()
            paper_id = database.insert_paper({
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
            database.update_analysis(paper_id, {"rating": 4})
            pending = database.get_unanalyzed_papers(limit=10)
            pending_count = database.get_unanalyzed_count()
            analyzed_count = database.get_analyzed_count()

        database.DB_DIR = original_dir
        database.DB_PATH = original_path
        self.assertEqual([p["arxiv_id"] for p in pending], ["2601.00007"])
        self.assertEqual(pending_count, 1)
        self.assertEqual(analyzed_count, 0)

    def test_recommendation_columns_update_and_report_sorting(self):
        import database

        original_dir = database.DB_DIR
        original_path = database.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            database.DB_DIR = tmp
            database.DB_PATH = os.path.join(tmp, "papers.db")
            database.init_db()
            high_rating_id = database.insert_paper({
                "arxiv_id": "2601.00001",
                "title": "High Rating Low Interest",
                "authors": ["Alice"],
                "abstract": "abstract",
                "categories": ["cs.RO"],
                "primary_category": "cs.RO",
                "url": "https://arxiv.org/abs/2601.00001",
                "pdf_url": "https://arxiv.org/pdf/2601.00001",
                "published_date": "2026-01-01",
                "updated_date": "2026-01-01",
            })
            high_interest_id = database.insert_paper({
                "arxiv_id": "2601.00002",
                "title": "Lower Rating High Interest",
                "authors": ["Bob"],
                "abstract": "abstract",
                "categories": ["cs.RO"],
                "primary_category": "cs.RO",
                "url": "https://arxiv.org/abs/2601.00002",
                "pdf_url": "https://arxiv.org/pdf/2601.00002",
                "published_date": "2026-01-01",
                "updated_date": "2026-01-01",
            })
            database.insert_analysis(high_rating_id, {
                "tags": ["Robot"],
                "summary_cn": "摘要",
                "summary_en": "",
                "rating": 5,
                "value_comment": "高分",
                "qa_analysis": "",
            })
            database.insert_analysis(high_interest_id, {
                "tags": ["VLA"],
                "summary_cn": "高兴趣中文摘要",
                "summary_en": "",
                "rating": 3,
                "value_comment": "相关",
                "qa_analysis": "",
            })
            self.assertTrue(database.update_recommendation_result(high_rating_id, 20, "弱相关", "hash-v1"))
            self.assertTrue(database.update_recommendation_result(high_interest_id, 95, "强相关", "hash-v1"))

            with patch("settings.get_personalization_config", return_value={"research_interests": "机器人基础模型\nVLA"}), \
                 patch("settings.get_research_interest_hash", return_value="hash-v1"):
                content, _, _, _ = database.generate_report_content("2026-01-01")

        database.DB_DIR = original_dir
        database.DB_PATH = original_path
        self.assertIn("个性化推荐", content)
        self.assertIn("<strong>研究兴趣:</strong><br>机器人基础模型<br>VLA", content)
        self.assertIn("推荐 95/100", content)
        self.assertIn("★ ★ ★ ☆ ☆", content)
        self.assertNotIn("3★", content)
        self.assertNotIn("5★", content)
        self.assertIn("<strong>中文摘要:</strong> 高兴趣中文摘要", content)
        self.assertIn("<strong>推荐语:</strong> 强相关", content)
        self.assertIn("<strong>评价:</strong> 相关", content)
        self.assertNotIn("高分论文", content)
        self.assertLess(content.index("Lower Rating High Interest"), content.index("High Rating Low Interest"))

    def test_recommendation_candidates_require_existing_analysis(self):
        import database

        original_dir = database.DB_DIR
        original_path = database.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            database.DB_DIR = tmp
            database.DB_PATH = os.path.join(tmp, "papers.db")
            database.init_db()
            unanalyzed_id = database.insert_paper({
                "arxiv_id": "2601.00003",
                "title": "Unanalyzed",
                "authors": ["Alice"],
                "abstract": "abstract",
                "categories": ["cs.RO"],
                "primary_category": "cs.RO",
                "url": "https://arxiv.org/abs/2601.00003",
                "pdf_url": "https://arxiv.org/pdf/2601.00003",
                "published_date": "2026-01-01",
                "updated_date": "2026-01-01",
            })
            analyzed_id = database.insert_paper({
                "arxiv_id": "2601.00004",
                "title": "Analyzed",
                "authors": ["Bob"],
                "abstract": "abstract",
                "categories": ["cs.RO"],
                "primary_category": "cs.RO",
                "url": "https://arxiv.org/abs/2601.00004",
                "pdf_url": "https://arxiv.org/pdf/2601.00004",
                "published_date": "2026-01-01",
                "updated_date": "2026-01-01",
            })
            database.insert_analysis(analyzed_id, {
                "tags": ["VLA"],
                "summary_cn": "摘要",
                "summary_en": "",
                "rating": 3,
                "value_comment": "相关",
                "qa_analysis": "",
            })

            candidates = database.get_papers_for_recommendation(limit=10, date="2026-01-01", interest_hash="hash-v1")

        database.DB_DIR = original_dir
        database.DB_PATH = original_path
        self.assertEqual([p["id"] for p in candidates], [analyzed_id])
        self.assertNotIn(unanalyzed_id, [p["id"] for p in candidates])

    def test_ai_usage_log_records_and_summarizes_tokens(self):
        import database

        original_dir = database.DB_DIR
        original_path = database.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            database.DB_DIR = tmp
            database.DB_PATH = os.path.join(tmp, "papers.db")
            database.init_db()
            database.record_ai_usage({
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
            database.record_ai_usage({
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
            summary = database.get_ai_usage_summary(days=7)
            summary_by_model = database.get_ai_usage_summary(days=7, group_by="model")

        database.DB_DIR = original_dir
        database.DB_PATH = original_path

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
        import database

        original_dir = database.DB_DIR
        original_path = database.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            database.DB_DIR = tmp
            database.DB_PATH = os.path.join(tmp, "papers.db")
            database.init_db()
            paper_id = database.insert_paper({
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

            database.add_paper_chat_message(paper_id, "user", "问题")
            database.add_paper_chat_message(paper_id, "assistant", "回答")
            session_id = database.create_paper_quiz_session(paper_id, "quick3")
            question_ids = database.add_paper_quiz_questions(session_id, [
                {"question": "Q1?", "expected_points": ["A"]},
                {"question": "Q2?", "expected_points": ["B"]},
            ])
            database.add_paper_quiz_attempt(question_ids[0], "我的答案", 4, {"feedback": "不错"})
            database.add_paper_quiz_attempt(question_ids[0], "第二版答案", 5, {"feedback": "更好"})

            messages = database.get_paper_chat_messages(paper_id)
            session = database.get_paper_quiz_session_detail(session_id, paper_id=paper_id)
            latest_sessions = database.get_latest_paper_quiz_sessions(paper_id)

            database.delete_paper("2601.00001")
            conn = database.get_connection()
            counts = {
                "chat": conn.execute("SELECT COUNT(*) FROM paper_chat_messages").fetchone()[0],
                "sessions": conn.execute("SELECT COUNT(*) FROM paper_quiz_sessions").fetchone()[0],
                "questions": conn.execute("SELECT COUNT(*) FROM paper_quiz_questions").fetchone()[0],
                "attempts": conn.execute("SELECT COUNT(*) FROM paper_quiz_attempts").fetchone()[0],
            }
            conn.close()

        database.DB_DIR = original_dir
        database.DB_PATH = original_path

        self.assertEqual([m["role"] for m in messages], ["user", "assistant"])
        self.assertEqual(len(session["questions"]), 2)
        self.assertEqual(session["questions"][0]["feedback"], {"feedback": "更好"})
        self.assertEqual(latest_sessions[0]["question_count"], 2)
        self.assertEqual(latest_sessions[0]["attempt_count"], 2)
        self.assertEqual(counts, {"chat": 0, "sessions": 0, "questions": 0, "attempts": 0})


class FetchBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_import_stubs()
        import fetcher
        cls.fetcher = fetcher

    def test_recent_fetch_does_not_skip_because_old_data_exists(self):
        calls = []
        progress_messages = []

        def fake_fetch_range(categories, start_date, end_date):
            calls.append((categories, start_date, end_date))
            return [{"arxiv_id": "2606.00001"}]

        with patch.object(self.fetcher, "get_fetch_config", return_value={
            "request_delay": 3,
            "batch_days": 30,
            "batch_delay": 0,
        }), patch.object(self.fetcher, "_fetch_date_range", side_effect=fake_fetch_range):
            papers = self.fetcher.fetch_batch(
                categories=["cs.RO"],
                total_days=1,
                batch_days=30,
                batch_delay=0,
                progress_callback=progress_messages.append,
            )

        self.assertEqual(papers, [{"arxiv_id": "2606.00001"}])
        self.assertEqual(len(calls), 1)
        self.assertNotIn("无需重复抓取", " ".join(m.get("message", "") for m in progress_messages))

    def test_fetch_errors_are_reported_instead_of_hidden_as_zero_results(self):
        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def results(self, search):
                raise RuntimeError("HTTP 429 Too Many Requests")

        fake_arxiv = types.SimpleNamespace(
            Client=FakeClient,
            Search=lambda **kwargs: kwargs,
            SortCriterion=types.SimpleNamespace(SubmittedDate="submitted"),
            SortOrder=types.SimpleNamespace(Descending="descending"),
        )

        with patch.object(self.fetcher, "arxiv", fake_arxiv), \
             patch.object(self.fetcher, "get_fetch_config", return_value={"request_delay": 3}), \
             patch.object(self.fetcher.logger, "error"):
            with self.assertRaisesRegex(RuntimeError, "429"):
                self.fetcher._fetch_date_range(
                    ["cs.RO"],
                    datetime(2026, 5, 28, tzinfo=timezone.utc),
                    datetime(2026, 6, 4, tzinfo=timezone.utc),
                )

    def test_date_range_uses_saved_request_delay_when_not_overridden(self):
        client_kwargs = []

        class FakeClient:
            def __init__(self, *args, **kwargs):
                client_kwargs.append(kwargs)

            def results(self, search):
                return []

        fake_arxiv = types.SimpleNamespace(
            Client=FakeClient,
            Search=lambda **kwargs: kwargs,
            SortCriterion=types.SimpleNamespace(SubmittedDate="submitted"),
            SortOrder=types.SimpleNamespace(Descending="descending"),
        )

        with patch.object(self.fetcher, "arxiv", fake_arxiv), \
             patch.object(self.fetcher, "get_fetch_config", return_value={"request_delay": 30}):
            self.fetcher._fetch_date_range(
                ["cs.RO"],
                datetime(2026, 5, 28, tzinfo=timezone.utc),
                datetime(2026, 6, 4, tzinfo=timezone.utc),
            )

        self.assertEqual(client_kwargs[0]["delay_seconds"], 30)

    def test_fetch_by_date_uses_utc_date_boundaries(self):
        with patch.object(self.fetcher, "_fetch_date_range", return_value=[]) as fetch_range:
            self.fetcher.fetch_by_date("2026-06-01", categories=["cs.RO"])

        _, start_date, end_date = fetch_range.call_args.args
        self.assertEqual(start_date, datetime(2026, 6, 1, tzinfo=timezone.utc))
        self.assertEqual(end_date, datetime(2026, 6, 2, tzinfo=timezone.utc))


class RuntimeSettingPropagationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_import_stubs()
        import app
        import analyzer
        import fetcher
        import main
        import pdf_reader
        import settings
        cls.app = app
        cls.analyzer = analyzer
        cls.fetcher = fetcher
        cls.main = main
        cls.pdf_reader = pdf_reader
        cls.settings = settings

    def test_api_papers_uses_saved_per_page_setting(self):
        self.app.request = FakeRequest(args={"page": "2"})
        with patch.object(self.app, "get_per_page", return_value=50), \
             patch.object(self.app, "get_papers_with_analysis", return_value=[]) as get_papers:
            result = self.app.api_papers()

        self.assertEqual(result, [])
        get_papers.assert_called_once()
        self.assertEqual(get_papers.call_args.kwargs["limit"], 50)
        self.assertEqual(get_papers.call_args.kwargs["offset"], 50)

    def test_api_papers_allows_request_per_page_override(self):
        self.app.request = FakeRequest(args={"page": "3", "per_page": "10"})
        with patch.object(self.app, "get_per_page", return_value=50), \
             patch.object(self.app, "get_papers_with_analysis", return_value=[]) as get_papers:
            self.app.api_papers()

        self.assertEqual(get_papers.call_args.kwargs["limit"], 10)
        self.assertEqual(get_papers.call_args.kwargs["offset"], 20)

    def test_cli_analyze_uses_saved_concurrency(self):
        with patch("database.init_db"), \
             patch("settings.get_concurrency", return_value=7), \
             patch("analyzer.analyze_pending_papers", return_value=3) as analyze, \
             patch.object(self.main.logger, "info"):
            self.main.run_analyze_only()

        analyze.assert_called_once_with(limit=100, concurrency=7)

    def test_analyze_papers_fallback_uses_saved_concurrency(self):
        paper = {"id": 1, "arxiv_id": "2601.00001"}
        result = {"tags": [], "summary_cn": "", "rating": 0, "value_comment": ""}

        with patch.object(self.analyzer, "get_concurrency", return_value=6), \
             patch.object(self.analyzer, "analyze_paper_basic", return_value=(paper, result, None)) as analyze_basic, \
             patch.object(self.analyzer, "insert_analysis", return_value=True), \
             patch.object(self.analyzer, "ThreadPoolExecutor", wraps=self.analyzer.ThreadPoolExecutor) as executor, \
             patch.object(self.analyzer.logger, "info"):
            count = self.analyzer.analyze_papers([paper], concurrency=None)

        self.assertEqual(count, 1)
        analyze_basic.assert_called_once_with(paper)
        self.assertEqual(executor.call_args.kwargs["max_workers"], 6)

    def test_analyze_papers_updates_existing_manual_rating_without_overwriting_rating(self):
        paper = {"id": 1, "arxiv_id": "2601.00001"}
        result = {"tags": ["VLA"], "summary_cn": "摘要", "summary_en": "", "rating": 0, "value_comment": "简评"}

        with patch.object(self.analyzer, "analyze_paper_basic", return_value=(paper, result, None)), \
             patch.object(self.analyzer, "insert_analysis", return_value=None), \
             patch.object(self.analyzer, "update_analysis", return_value=True) as update_analysis, \
             patch.object(self.analyzer.logger, "info"):
            count = self.analyzer.analyze_papers([paper], concurrency=1)

        self.assertEqual(count, 1)
        update_analysis.assert_called_once_with(1, {
            "rating": 0,
            "tags": ["VLA"],
            "summary_cn": "摘要",
            "summary_en": "",
            "value_comment": "简评",
        })

    def test_pdf_download_uses_proxy_settings(self):
        class FakeResponse:
            content = b"%PDF-test"

            def raise_for_status(self):
                pass

        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(self.pdf_reader, "PDF_CACHE_DIR", tmp), \
             patch.object(self.pdf_reader._pdf_bucket, "acquire", return_value=0), \
             patch.object(self.pdf_reader, "get_proxy_config", return_value={
                 "enabled": True,
                 "http": "http://proxy.local:7890",
                 "https": "http://proxy.local:7890",
             }), \
             patch.object(self.pdf_reader.requests, "get", return_value=FakeResponse()) as get, \
             patch.object(self.pdf_reader.logger, "info"):
            path = self.pdf_reader.download_pdf("https://arxiv.org/pdf/2601.00001", "2601.00001")

        self.assertTrue(path.endswith("2601.00001.pdf"))
        self.assertEqual(get.call_args.kwargs["proxies"], {
            "http": "http://proxy.local:7890",
            "https": "http://proxy.local:7890",
        })

    def test_fetch_config_is_normalized(self):
        normalized = self.settings._normalize_fetch_config({
            "request_delay": "1",
            "batch_days": "999",
            "batch_delay": "9999",
        })

        self.assertEqual(normalized, {
            "request_delay": 3.0,
            "batch_days": 365,
            "batch_delay": 1800.0,
        })


class BackupServiceTests(unittest.TestCase):
    def test_database_file_sizes_include_wal_and_shm(self):
        import backup

        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "papers.db")
            with open(db_path, "wb") as f:
                f.write(b"a" * 10)
            with open(db_path + "-wal", "wb") as f:
                f.write(b"b" * 20)
            with open(db_path + "-shm", "wb") as f:
                f.write(b"c" * 30)

            sizes = backup.get_database_file_sizes(db_path)

        self.assertEqual(sizes["total_bytes"], 60)
        self.assertEqual([f["name"] for f in sizes["files"]], ["papers.db", "papers.db-wal", "papers.db-shm"])

    def test_backup_archive_contains_database_settings_and_manifest(self):
        import backup

        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "papers.db")
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE demo (id INTEGER PRIMARY KEY, name TEXT)")
            conn.execute("INSERT INTO demo (name) VALUES ('paper')")
            conn.commit()
            conn.close()

            settings_path = os.path.join(tmp, "settings.json")
            with open(settings_path, "w", encoding="utf-8") as f:
                json.dump({"api_key": "secret"}, f)

            archive_path = os.path.join(tmp, "backup.zip")
            manifest = backup.create_backup_archive(
                archive_path,
                db_path=db_path,
                settings_path=settings_path,
            )

            with zipfile.ZipFile(archive_path, "r") as zf:
                names = set(zf.namelist())
                extract_dir = os.path.join(tmp, "extract")
                zf.extract("papers.db", extract_dir)
                manifest_data = json.loads(zf.read("manifest.json").decode("utf-8"))

            copied = sqlite3.connect(os.path.join(extract_dir, "papers.db"))
            row = copied.execute("SELECT name FROM demo").fetchone()
            copied.close()

        self.assertIn("papers.db", names)
        self.assertIn("settings.json", names)
        self.assertIn("manifest.json", names)
        self.assertEqual(names, {"papers.db", "settings.json", "manifest.json"})
        self.assertEqual(row[0], "paper")
        self.assertTrue(manifest["included"]["settings_json"])
        self.assertNotIn("output_dir", manifest_data["included"])
        self.assertNotIn("report_files", manifest_data)

    def test_webdav_backup_uploads_latest_and_cleans_expired_history(self):
        import backup

        class FakeResponse:
            def __init__(self, status_code=200, content=b""):
                self.status_code = status_code
                self.content = content

            def raise_for_status(self):
                raise AssertionError(f"unexpected status {self.status_code}")

        propfind_xml = b"""<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
  <d:response><d:href>/dav/arxiv/arxiv-backup-20260610-120000.zip</d:href></d:response>
  <d:response><d:href>/dav/arxiv/arxiv-backup-20260614-120000.zip</d:href></d:response>
  <d:response><d:href>/dav/arxiv/arxiv-backup-latest.zip</d:href></d:response>
</d:multistatus>"""

        calls = []

        def fake_request(method, url, **kwargs):
            calls.append((method, url, kwargs))
            if method == "PROPFIND":
                return FakeResponse(207, propfind_xml)
            if method == "MKCOL":
                return FakeResponse(201)
            if method == "PUT":
                return FakeResponse(201)
            if method == "DELETE":
                return FakeResponse(204)
            return FakeResponse(200)

        def fake_archive(path, **kwargs):
            with open(path, "wb") as f:
                f.write(b"zip")
            return {
                "archive": {"size_bytes": 3, "size": "3 B"},
                "database": {"size_bytes": 2, "size": "2 B"},
            }

        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(backup, "DB_DIR", tmp), \
             patch.object(backup, "create_backup_archive", side_effect=fake_archive), \
             patch.object(backup.requests, "request", side_effect=fake_request):
            result = backup.run_webdav_backup(
                config={
                    "enabled": False,
                    "url": "https://dav.example.com",
                    "username": "alice",
                    "password": "secret",
                    "remote_dir": "arxiv",
                    "history_days": 3,
                },
                force=True,
                record_status=False,
                now=datetime(2026, 6, 15, 12, 0, 0),
            )

        methods = [call[0] for call in calls]
        self.assertEqual(methods.count("PUT"), 2)
        self.assertIn("MKCOL", methods)
        self.assertIn("PROPFIND", methods)
        self.assertIn("DELETE", methods)
        self.assertEqual(result["uploaded_files"], [
            "arxiv-backup-20260615-120000.zip",
            "arxiv-backup-latest.zip",
        ])
        self.assertEqual(result["deleted_files"], ["arxiv-backup-20260610-120000.zip"])


class EmailReportTests(unittest.TestCase):
    def _sample_email_data(self, config=None):
        return {
            "report_date": "2026-06-17",
            "papers": [],
            "important": [],
            "overview": [],
            "total": 2,
            "analyzed": 1,
            "avg_rating": 3.5,
            "ai_summary": "今日导读",
            "ai_summary_error": "",
            "full_report_url": "",
            "config": config or {"site_url": ""},
        }

    def test_email_report_settings_preserve_password_and_mask_get(self):
        import settings

        original_dir = settings.DB_DIR
        original_path = settings.SETTINGS_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                settings.DB_DIR = tmp
                settings.SETTINGS_PATH = os.path.join(tmp, "settings.json")

                loaded = settings.load_settings()
                self.assertIn("email_report", loaded)
                self.assertFalse(loaded["email_report"]["enabled"])

                settings.save_email_report_config({
                    "enabled": True,
                    "smtp_host": "smtp.example.com",
                    "smtp_port": "587",
                    "security": "starttls",
                    "username": "alice@example.com",
                    "password": "secret",
                    "sender": "",
                    "recipients": "bob@example.com; carol@example.com\nbob@example.com",
                    "subject_template": "Daily {date}",
                    "site_url": "https://papers.example.com/",
                })
                settings.save_email_report_config({
                    "enabled": True,
                    "smtp_host": "smtp2.example.com",
                    "smtp_port": "465",
                    "security": "ssl",
                    "username": "alice@example.com",
                    "password": "",
                    "sender": "",
                    "recipients": ["bob@example.com", "carol@example.com"],
                    "subject_template": "Daily {date}",
                    "site_url": "https://papers.example.com/",
                })

                full = settings.get_email_report_config(mask_password=False)
                masked = settings.get_email_report_config(mask_password=True)

            self.assertEqual(full["password"], "secret")
            self.assertEqual(full["recipients"], ["bob@example.com", "carol@example.com"])
            self.assertEqual(full["site_url"], "https://papers.example.com")
            self.assertNotIn("password", masked)
            self.assertEqual(masked["password_masked"], "******")
        finally:
            settings.DB_DIR = original_dir
            settings.SETTINGS_PATH = original_path

    def test_email_report_status_only_success_updates_sent_date(self):
        import settings

        original_dir = settings.DB_DIR
        original_path = settings.SETTINGS_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                settings.DB_DIR = tmp
                settings.SETTINGS_PATH = os.path.join(tmp, "settings.json")
                settings.load_settings()

                settings.update_email_report_status("success", report_date="2026-06-16")
                settings.update_email_report_status(
                    "error",
                    error="smtp down",
                    report_date="2026-06-17",
                )
                failed = settings.get_email_report_config(mask_password=False)

                settings.update_email_report_status("success")
                manual = settings.get_email_report_config(mask_password=False)

            self.assertEqual(failed["last_status"], "error")
            self.assertEqual(failed["last_error"], "smtp down")
            self.assertEqual(failed["last_sent_report_date"], "2026-06-16")
            self.assertEqual(manual["last_status"], "success")
            self.assertEqual(manual["last_error"], "")
            self.assertEqual(manual["last_sent_report_date"], "2026-06-16")
        finally:
            settings.DB_DIR = original_dir
            settings.SETTINGS_PATH = original_path

    def test_report_email_html_uses_digest_layout_and_site_links(self):
        import email_report

        def paper(arxiv_id, score, rating=3):
            return {
                "arxiv_id": arxiv_id,
                "title": f"Paper {arxiv_id}",
                "authors": ["Alice", "Bob"],
                "abstract": f"abstract {arxiv_id}",
                "categories": ["cs.RO"],
                "tags": ["VLA"],
                "rating": rating,
                "summary_cn": f"summary {arxiv_id}",
                "value_comment": f"comment {arxiv_id}",
                "recommendation_reason": f"reason {arxiv_id}",
                "current_recommendation_score": score,
                "analysis_id": 1,
                "url": f"https://arxiv.org/abs/{arxiv_id}",
                "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
            }

        papers = [paper("2606.00081", 81, 5), paper("2606.00080", 80, 4)]
        papers.extend(paper(f"2606.{i:05d}", 70 - i, 3) for i in range(25))
        report = {
            "report_date": "2026-06-17",
            "content": "<div>legacy web report should not be reused</div>",
            "paper_count": len(papers),
            "analyzed_count": len(papers),
            "avg_rating": 4.5,
        }
        with patch.object(email_report, "_load_report_papers", return_value=papers):
            data = email_report.build_report_email_data(
                report,
                {"site_url": "https://papers.example.com/"},
                ai_summary="今日趋势\n重点方向",
            )
            html = email_report.build_report_email_html(report, email_data=data)

        self.assertEqual([p["arxiv_id"] for p in data["important"]], ["2606.00081"])
        self.assertEqual(len(data["overview"]), 20)
        self.assertIn("今日趋势<br>重点方向", html)
        self.assertIn("重点精读", html)
        self.assertIn("快速速览", html)
        self.assertIn('href="https://papers.example.com/paper/2606.00081"', html)
        self.assertIn('href="https://papers.example.com/reports/2026-06-17"', html)
        self.assertIn("Paper 2606.00080", html)
        self.assertNotIn("legacy web report should not be reused", html)

    def test_report_email_without_recommendations_uses_overview_only(self):
        import email_report

        papers = [
            {
                "arxiv_id": "2606.00001",
                "title": "High rating without recommendation",
                "authors": ["Alice"],
                "abstract": "abstract",
                "categories": ["cs.RO"],
                "tags": ["Robot Learning"],
                "rating": 5,
                "summary_cn": "",
                "value_comment": "valuable",
                "recommendation_reason": "",
                "current_recommendation_score": None,
                "analysis_id": 1,
                "url": "https://arxiv.org/abs/2606.00001",
                "pdf_url": "",
            }
        ]
        report = {"report_date": "2026-06-17", "paper_count": 1, "analyzed_count": 1, "avg_rating": 5}

        with patch.object(email_report, "_load_report_papers", return_value=papers):
            data = email_report.build_report_email_data(report, {"site_url": ""}, ai_summary=None, ai_summary_error="boom")
            html = email_report.build_report_email_html(report, email_data=data)

        self.assertEqual(data["important"], [])
        self.assertEqual([p["arxiv_id"] for p in data["overview"]], ["2606.00001"])
        self.assertIn("AI 导读暂不可用", html)
        self.assertIn("今天没有推荐分高于 80", html)
        self.assertIn("High rating without recommendation", html)

    def test_send_report_email_supports_starttls_ssl_and_plain_smtp(self):
        import email_report

        report = {
            "report_date": "2026-06-17",
            "content": "<div>report</div>",
            "paper_count": 2,
            "analyzed_count": 1,
            "avg_rating": 3.5,
        }

        class FakeSMTP:
            def __init__(self, kind, host, port, **kwargs):
                self.kind = kind
                self.host = host
                self.port = port
                self.kwargs = kwargs
                calls.append(("connect", kind, host, port))

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def starttls(self, context=None):
                calls.append(("starttls", self.kind))

            def login(self, username, password):
                calls.append(("login", username, password))

            def send_message(self, message):
                calls.append(("send", self.kind, message["Subject"], message["To"]))

        def smtp_factory(kind):
            def factory(host, port, **kwargs):
                return FakeSMTP(kind, host, port, **kwargs)
            return factory

        base_config = {
            "enabled": False,
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "username": "alice@example.com",
            "password": "secret",
            "sender": "daily@example.com",
            "recipients": ["bob@example.com"],
            "subject_template": "Daily {date} - {paper_count}",
            "site_url": "",
        }

        for security, expected_kind, should_starttls in (
            ("starttls", "smtp", True),
            ("ssl", "ssl", False),
            ("none", "smtp", False),
        ):
            calls = []
            config = {**base_config, "security": security, "smtp_port": 465 if security == "ssl" else 587}
            with patch.object(email_report, "get_proxy_config", return_value={"enabled": False, "http": "", "https": ""}), \
                 patch.object(email_report, "build_report_email_data", return_value=self._sample_email_data(config)), \
                 patch.object(email_report.smtplib, "SMTP", smtp_factory("smtp")), \
                 patch.object(email_report.smtplib, "SMTP_SSL", smtp_factory("ssl")):
                result = email_report.send_report_email(config=config, report=report, force=True, record_status=False)

            self.assertEqual(result["status"], "ok")
            self.assertIn(("connect", expected_kind, "smtp.example.com", config["smtp_port"]), calls)
            self.assertEqual(any(call[0] == "starttls" for call in calls), should_starttls)
            self.assertIn(("login", "alice@example.com", "secret"), calls)
            self.assertTrue(any(call[0] == "send" and call[1] == expected_kind for call in calls))

    def test_test_send_does_not_update_automatic_deduplication_date(self):
        import email_report

        report = {
            "report_date": "2026-06-17",
            "paper_count": 1,
            "analyzed_count": 1,
            "avg_rating": 4,
        }
        config = {
            "enabled": False,
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "security": "none",
            "username": "",
            "password": "",
            "sender": "daily@example.com",
            "recipients": ["reader@example.com"],
            "subject_template": "Daily {date}",
            "site_url": "",
        }

        with patch.object(email_report, "build_report_email_data", return_value=self._sample_email_data(config)), \
             patch.object(email_report, "_send_message"), \
             patch.object(email_report, "update_email_report_status") as update_status:
            result = email_report.send_report_email(
                report,
                config=config,
                force=True,
                record_status=True,
            )

        self.assertEqual(result["status"], "ok")
        update_status.assert_called_once_with("success", report_date="")

    def test_automatic_send_records_report_date_and_failure_does_not(self):
        import email_report

        report = {
            "report_date": "2026-06-17",
            "paper_count": 1,
            "analyzed_count": 1,
            "avg_rating": 4,
        }
        config = {
            "enabled": True,
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "security": "none",
            "username": "",
            "password": "",
            "sender": "daily@example.com",
            "recipients": ["reader@example.com"],
            "subject_template": "Daily {date}",
            "site_url": "",
        }

        with patch.object(email_report, "build_report_email_data", return_value=self._sample_email_data(config)), \
             patch.object(email_report, "_send_message"), \
             patch.object(email_report, "update_email_report_status") as update_status:
            email_report.send_report_email(report, config=config, force=False, record_status=True)

        update_status.assert_called_once_with("success", report_date="2026-06-17")

        with patch.object(email_report, "build_report_email_data", return_value=self._sample_email_data(config)), \
             patch.object(email_report, "_send_message", side_effect=RuntimeError("smtp down")), \
             patch.object(email_report, "update_email_report_status") as update_status:
            with self.assertRaisesRegex(RuntimeError, "smtp down"):
                email_report.send_report_email(report, config=config, force=False, record_status=True)

        update_status.assert_called_once_with("error", error="smtp down")

    def test_smtp_proxy_url_prefers_https_and_falls_back_to_http(self):
        import email_report

        self.assertEqual(email_report._resolve_smtp_proxy_url({
            "enabled": True,
            "http": "http://http-proxy.local:7890",
            "https": "http://https-proxy.local:7890",
        }), "http://https-proxy.local:7890")
        self.assertEqual(email_report._resolve_smtp_proxy_url({
            "enabled": True,
            "http": "http://http-proxy.local:7890",
            "https": "",
        }), "http://http-proxy.local:7890")
        self.assertEqual(email_report._resolve_smtp_proxy_url({
            "enabled": False,
            "http": "http://http-proxy.local:7890",
            "https": "http://https-proxy.local:7890",
        }), "")

    def test_proxy_tunnel_sends_connect_request_and_basic_auth(self):
        import base64
        import email_report

        class FakeSocket:
            def __init__(self):
                self.sent = b""
                self.closed = False

            def sendall(self, data):
                self.sent += data

            def recv(self, size):
                return b"HTTP/1.1 200 Connection Established\r\nProxy-Agent: fake\r\n\r\n"

            def close(self):
                self.closed = True

        fake_socket = FakeSocket()
        with patch.object(email_report.socket, "create_connection", return_value=fake_socket) as create_connection:
            sock = email_report._create_proxy_tunnel(
                "smtp.example.com",
                587,
                30,
                "http://alice:secret@proxy.example.com:8080",
            )

        connect_request = fake_socket.sent.decode("ascii")
        expected_token = base64.b64encode(b"alice:secret").decode("ascii")
        self.assertIs(sock, fake_socket)
        create_connection.assert_called_once_with(("proxy.example.com", 8080), timeout=30)
        self.assertIn("CONNECT smtp.example.com:587 HTTP/1.1", connect_request)
        self.assertIn("Host: smtp.example.com:587", connect_request)
        self.assertIn(f"Proxy-Authorization: Basic {expected_token}", connect_request)
        self.assertFalse(fake_socket.closed)

    def test_send_report_email_routes_security_modes_through_proxy_classes(self):
        import email_report

        report = {
            "report_date": "2026-06-17",
            "content": "<div>report</div>",
            "paper_count": 2,
            "analyzed_count": 1,
            "avg_rating": 3.5,
        }
        base_config = {
            "enabled": False,
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "username": "alice@example.com",
            "password": "secret",
            "sender": "daily@example.com",
            "recipients": ["bob@example.com"],
            "subject_template": "Daily {date}",
            "site_url": "",
        }

        class FakeProxySMTP:
            def __init__(self, kind, host, port, **kwargs):
                self.kind = kind
                calls.append(("connect", kind, host, port, kwargs.get("proxy_url")))

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def starttls(self, context=None):
                calls.append(("starttls", self.kind))

            def login(self, username, password):
                calls.append(("login", self.kind, username, password))

            def send_message(self, message):
                calls.append(("send", self.kind, message["To"]))

        def smtp_factory(kind):
            def factory(host, port, **kwargs):
                return FakeProxySMTP(kind, host, port, **kwargs)
            return factory

        for security, expected_kind, should_starttls in (
            ("starttls", "smtp", True),
            ("ssl", "ssl", False),
            ("none", "smtp", False),
        ):
            calls = []
            config = {**base_config, "security": security, "smtp_port": 465 if security == "ssl" else 587}
            with patch.object(email_report, "get_proxy_config", return_value={
                "enabled": True,
                "http": "http://http-proxy.local:7890",
                "https": "http://https-proxy.local:7891",
            }), \
                 patch.object(email_report, "build_report_email_data", return_value=self._sample_email_data(config)), \
                 patch.object(email_report, "_ProxySMTP", smtp_factory("smtp")), \
                 patch.object(email_report, "_ProxySMTP_SSL", smtp_factory("ssl")):
                result = email_report.send_report_email(config=config, report=report, force=True, record_status=False)

            self.assertEqual(result["status"], "ok")
            self.assertIn(("connect", expected_kind, "smtp.example.com", config["smtp_port"], "http://https-proxy.local:7891"), calls)
            self.assertEqual(any(call[0] == "starttls" for call in calls), should_starttls)
            self.assertTrue(any(call[0] == "send" and call[1] == expected_kind for call in calls))

    def test_proxy_errors_are_readable_and_record_status(self):
        import email_report

        report = {
            "report_date": "2026-06-17",
            "content": "<div>report</div>",
            "paper_count": 1,
            "analyzed_count": 1,
            "avg_rating": 4,
        }
        config = {
            "enabled": False,
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "security": "none",
            "username": "",
            "password": "",
            "sender": "daily@example.com",
            "recipients": ["bob@example.com"],
            "subject_template": "Daily {date}",
            "site_url": "",
        }

        class RaisingProxySMTP:
            def __init__(self, host, port, **kwargs):
                email_report._create_proxy_tunnel(host, port, kwargs.get("timeout"), kwargs.get("proxy_url"))

        with patch.object(email_report, "get_proxy_config", return_value={
            "enabled": True,
            "http": "socks5://127.0.0.1:1080",
            "https": "",
        }), \
             patch.object(email_report, "build_report_email_data", return_value=self._sample_email_data(config)), \
             patch.object(email_report, "_ProxySMTP", RaisingProxySMTP), \
             patch.object(email_report, "update_email_report_status") as update_status:
            with self.assertRaisesRegex(ValueError, "仅支持 HTTP CONNECT"):
                email_report.send_report_email(config=config, report=report, force=True, record_status=True)

        update_status.assert_called_once()
        self.assertEqual(update_status.call_args.args[0], "error")
        self.assertIn("仅支持 HTTP CONNECT", update_status.call_args.kwargs["error"])

    def test_proxy_tunnel_rejects_non_200_connect_response(self):
        import email_report

        class FakeSocket:
            def __init__(self):
                self.closed = False

            def sendall(self, data):
                pass

            def recv(self, size):
                return b"HTTP/1.1 407 Proxy Authentication Required\r\n\r\n"

            def close(self):
                self.closed = True

        fake_socket = FakeSocket()
        with patch.object(email_report.socket, "create_connection", return_value=fake_socket):
            with self.assertRaisesRegex(ConnectionError, "407 Proxy Authentication Required"):
                email_report._create_proxy_tunnel(
                    "smtp.example.com",
                    587,
                    30,
                    "http://proxy.example.com:8080",
                )
        self.assertTrue(fake_socket.closed)


class TemplateSafetyTests(unittest.TestCase):
    def test_paper_pages_share_sanitized_markdown_and_math_renderer(self):
        with open("templates/paper.html", "r", encoding="utf-8") as f:
            paper = f.read()
        with open("templates/paper_chat.html", "r", encoding="utf-8") as f:
            chat = f.read()
        with open("static/rich_text.js", "r", encoding="utf-8") as f:
            renderer = f.read()

        for template in (paper, chat):
            self.assertIn('/static/vendor/marked.min.js', template)
            self.assertIn('/static/vendor/katex.min.js', template)
            self.assertIn('/static/vendor/auto-render.min.js', template)
            self.assertIn('/static/rich_text.js', template)
        self.assertIn('/static/vendor/katex.min.css', paper)
        self.assertNotIn('function renderMd(', paper)
        self.assertNotIn('function renderRich(', chat)
        self.assertIn('global.RichText =', renderer)
        self.assertIn("script,style,iframe,object,embed,link,meta", renderer)
        self.assertIn("startsWith('on')", renderer)
        self.assertIn('javascript:', renderer)

    def test_paper_detail_handles_flexible_qa_headings_and_visible_warnings(self):
        with open("templates/paper.html", "r", encoding="utf-8") as f:
            paper = f.read()
        with open("static/style.css", "r", encoding="utf-8") as f:
            style = f.read()

        self.assertIn("/^###\\s*Q(\\d+)\\s*:\\s*([\\s\\S]*)/i", paper)
        self.assertIn("container.innerHTML = RichText.render(raw)", paper)
        self.assertIn("RichText.renderMath(container)", paper)
        self.assertIn(".action-status.warning", style)

    def test_paper_processing_page_excludes_schedule_stats_and_logs(self):
        with open("templates/tasks.html", "r", encoding="utf-8") as f:
            template = f.read()

        self.assertIn("论文处理 - AI 论文数据库", template)
        self.assertIn("抓取、分析并生成报告", template)
        self.assertNotIn("schedule-enabled", template)
        self.assertNotIn("task-stats", template)
        self.assertNotIn("/api/tasks/logs", template)
        self.assertNotIn("运行中的任务", template)

    def test_settings_has_independent_schedule_tab_with_email_and_logs(self):
        with open("templates/settings.html", "r", encoding="utf-8") as f:
            template = f.read()

        ai_start = template.index('id="tab-ai"')
        schedule_start = template.index('id="tab-schedule"')
        db_start = template.index('id="tab-db"')
        self.assertLess(ai_start, schedule_start)
        self.assertLess(schedule_start, db_start)
        self.assertNotIn('id="grp-email"', template[ai_start:schedule_start])
        self.assertIn('id="grp-email"', template[schedule_start:db_start])
        self.assertIn('id="schedule-task-logs"', template[schedule_start:db_start])
        self.assertIn("固定执行流程", template[schedule_start:db_start])

    def test_public_promo_page_presents_the_complete_research_workflow(self):
        with open("templates/about.html", "r", encoding="utf-8") as f:
            html = f.read()

        self.assertIn('href="/static/promo.css"', html)
        self.assertIn("从发现论文，", html)
        self.assertIn("到真正读懂。", html)
        for label in ("每日抓取", "AI 筛选", "个性化推荐", "PDF 精读", "主动问答", "报告与备份"):
            with self.subTest(label=label):
                self.assertIn(label, html)
        self.assertIn('href="/reports"', html)
        self.assertIn('href="/browse"', html)
        self.assertNotIn('href="/vision"', html)
        self.assertIn('rel="noopener noreferrer"', html)
        self.assertTrue(os.path.exists("static/promo.css"))

    def test_vision_page_separates_current_capabilities_from_the_roadmap(self):
        with open("templates/vision.html", "r", encoding="utf-8") as f:
            html = f.read()

        self.assertIn('href="/static/promo.css"', html)
        self.assertIn("从个人阅读工具，", html)
        self.assertIn("进化为实验室科研情报基础设施", html)
        for status in ("已实现", "下一步", "长期愿景"):
            with self.subTest(status=status):
                self.assertIn(status, html)
        for phase in ("实验室方向雷达", "协作型科研工作台", "实验室研究记忆"):
            with self.subTest(phase=phase):
                self.assertIn(phase, html)
        for tool in ("Cool Papers", "Elicit", "ResearchRabbit", "OpenClaw"):
            with self.subTest(tool=tool):
                self.assertIn(tool, html)
        self.assertIn('rel="noopener noreferrer"', html)

    def test_home_links_to_public_promo_without_exposing_internal_vision(self):
        with open("templates/index.html", "r", encoding="utf-8") as f:
            index_html = f.read()

        self.assertIn('href="/about"', index_html)
        self.assertIn("项目介绍", index_html)
        for path in (
            "templates/index.html",
            "templates/about.html",
            "templates/browse.html",
            "templates/search.html",
            "templates/reports.html",
            "templates/reading_list.html",
        ):
            with self.subTest(path=path):
                with open(path, "r", encoding="utf-8") as f:
                    self.assertNotIn('href="/vision"', f.read())

    def test_paper_inline_json_handlers_use_single_quoted_attributes(self):
        with open("templates/paper.html", "r", encoding="utf-8") as f:
            html = f.read()

        self.assertIn("onclick='toggleTodo({{ paper.arxiv_id | tojson }})'", html)
        self.assertIn("onclick='removeTag({{ t.strip() | tojson }})'", html)
        self.assertIn("onclick='removeTag({{ t | tojson }})'", html)
        self.assertNotIn('onclick="toggleTodo({{ paper.arxiv_id | tojson }})"', html)
        self.assertNotIn('onclick="removeTag({{ t | tojson }})"', html)

    def test_report_detail_has_regenerate_action_for_current_date(self):
        with open("templates/report_detail.html", "r", encoding="utf-8") as f:
            html = f.read()

        self.assertIn("重新生成该日报告", html)
        self.assertIn("const reportDate = {{ report.report_date | tojson }};", html)
        self.assertIn("/api/generate?date=${encodeURIComponent(reportDate)}", html)
        self.assertIn("window.location.reload()", html)

    def test_list_templates_show_spaced_star_rating_without_numeric_suffix(self):
        for path in ("templates/index.html", "templates/browse.html", "templates/search.html", "templates/reading_list.html"):
            with self.subTest(path=path):
                with open(path, "r", encoding="utf-8") as f:
                    html = f.read()
                self.assertIn("{% if not loop.last %} {% endif %}", html)
                self.assertNotIn("{{ paper.rating }}★", html)
        with open("templates/browse.html", "r", encoding="utf-8") as f:
            browse_html = f.read()
        self.assertNotIn("{{ i }}★", browse_html)
        self.assertNotIn("{{ min_rating }}★", browse_html)
        self.assertNotIn("{{ max_rating }}★", browse_html)

    def test_settings_has_network_proxy_tab_and_diagnostics(self):
        with open("templates/settings.html", "r", encoding="utf-8") as f:
            html = f.read()

        self.assertIn("网络与代理", html)
        self.assertIn("switchTab('network'", html)
        self.assertIn("id=\"tab-network\"", html)
        self.assertIn("testProxy()", html)
        self.assertIn("testLlmConnection()", html)
        self.assertIn("/api/network/test-llm", html)


class ScheduleRetryTests(unittest.TestCase):
    def import_app_with_temp_settings(self, tmp):
        import settings

        settings.DB_DIR = tmp
        settings.SETTINGS_PATH = os.path.join(tmp, "settings.json")
        sys.modules.pop("app", None)
        return importlib.import_module("app")

    def test_schedule_api_saves_and_returns_fetch_retry_config(self):
        import settings

        original_dir = settings.DB_DIR
        original_path = settings.SETTINGS_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                app_module = self.import_app_with_temp_settings(tmp)
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
                with patch.object(app_module, "configure_daily_job"), \
                        patch.object(app_module, "scheduler", types.SimpleNamespace(running=True)), \
                        patch.object(app_module, "request", types.SimpleNamespace(get_json=lambda: payload)):
                    response = app_module.api_save_schedule_config()
                    self.assertEqual(response["status"], "ok")

                    data = app_module.api_get_schedule_config()

            self.assertEqual(data["fetch_retry_interval_minutes"], 12)
            self.assertEqual(data["fetch_max_retries"], 25)
        finally:
            sys.modules.pop("app", None)
            settings.DB_DIR = original_dir
            settings.SETTINGS_PATH = original_path

    def test_daily_fetch_retries_then_succeeds(self):
        import settings

        original_dir = settings.DB_DIR
        original_path = settings.SETTINGS_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                app_module = self.import_app_with_temp_settings(tmp)
                with patch.object(app_module, "fetch_latest_papers", side_effect=[
                    RuntimeError("arXiv 429"),
                    RuntimeError("still limited"),
                    [{"id": 1}],
                ]) as fetch_mock, \
                        patch.object(app_module.time, "sleep") as sleep_mock, \
                        patch.object(app_module, "set_task_log_step_status") as step_mock:
                    papers, retries = app_module._fetch_for_daily_pipeline_with_retries(
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
            settings.DB_DIR = original_dir
            settings.SETTINGS_PATH = original_path

    def test_daily_fetch_raises_after_max_retries(self):
        import settings

        original_dir = settings.DB_DIR
        original_path = settings.SETTINGS_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                app_module = self.import_app_with_temp_settings(tmp)
                with patch.object(app_module, "fetch_latest_papers", side_effect=RuntimeError("offline")) as fetch_mock, \
                        patch.object(app_module.time, "sleep") as sleep_mock, \
                        patch.object(app_module, "set_task_log_step_status"):
                    with self.assertRaisesRegex(RuntimeError, "已重试 2 次仍未成功"):
                        app_module._fetch_for_daily_pipeline_with_retries(
                            log_id=123,
                            fetch_days=3,
                            retry_interval_minutes=10,
                            max_retries=2,
                        )

            self.assertEqual(fetch_mock.call_count, 3)
            self.assertEqual(sleep_mock.call_count, 2)
        finally:
            sys.modules.pop("app", None)
            settings.DB_DIR = original_dir
            settings.SETTINGS_PATH = original_path

    def test_daily_fetch_can_disable_retries(self):
        import settings

        original_dir = settings.DB_DIR
        original_path = settings.SETTINGS_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                app_module = self.import_app_with_temp_settings(tmp)
                with patch.object(app_module, "fetch_latest_papers", side_effect=RuntimeError("offline")) as fetch_mock, \
                        patch.object(app_module.time, "sleep") as sleep_mock, \
                        patch.object(app_module, "set_task_log_step_status"):
                    with self.assertRaisesRegex(RuntimeError, "已重试 0 次仍未成功"):
                        app_module._fetch_for_daily_pipeline_with_retries(
                            log_id=123,
                            fetch_days=3,
                            retry_interval_minutes=10,
                            max_retries=0,
                        )

            self.assertEqual(fetch_mock.call_count, 1)
            sleep_mock.assert_not_called()
        finally:
            sys.modules.pop("app", None)
            settings.DB_DIR = original_dir
            settings.SETTINGS_PATH = original_path


if __name__ == "__main__":
    unittest.main()
