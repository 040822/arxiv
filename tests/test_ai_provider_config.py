import os
import json
import tempfile
import sys
import types
import unittest
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
        self.assertEqual(loaded["prompt_profiles"]["deep_reading"]["system"], "legacy system")

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


class DummyOpenAI:
    models_response = types.SimpleNamespace(data=[])
    models_error = None
    chat_response = None
    last_chat_kwargs = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
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


def install_import_stubs():
    flask_mod = types.ModuleType("flask")

    class FakeFlask:
        def __init__(self, *args, **kwargs):
            self.secret_key = None

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

    flask_mod.Flask = FakeFlask
    flask_mod.render_template = lambda *args, **kwargs: ""
    flask_mod.request = types.SimpleNamespace(args={}, get_json=lambda: {})
    flask_mod.jsonify = jsonify
    flask_mod.Response = lambda *args, **kwargs: types.SimpleNamespace(args=args, kwargs=kwargs)
    flask_mod.session = {}
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
        schedule = {"enabled": True, "hour": 8, "minute": 30}
        app_module.request = FakeRequest(schedule)

        with patch.object(app_module, "save_schedule_config", return_value=True), \
             patch.object(app_module, "get_schedule_config", return_value=schedule), \
             patch.object(app_module, "configure_daily_job") as configure_daily_job:
            result = app_module.api_save_schedule_config()

        self.assertEqual(result["status"], "ok")
        configure_daily_job.assert_called_once_with(schedule)

    def test_generate_report_default_does_not_call_ai_summary(self):
        app_module = self.app_module
        app_module.request = FakeRequest({}, endpoint="api_generate", method="POST", path="/api/generate")

        with patch.object(app_module, "start_task_log", return_value=1), \
             patch.object(app_module, "finish_task_log"), \
             patch.object(app_module, "get_all_dates", return_value=[("2026-01-01",)]), \
             patch.object(app_module, "generate_report_ai_summary") as ai_summary, \
             patch.object(app_module, "generate_report_content", return_value=("html", 2, 1, 4.0)) as report_content, \
             patch.object(app_module, "save_report") as save_report:
            result = app_module.api_generate()

        self.assertEqual(result["status"], "ok")
        ai_summary.assert_not_called()
        report_content.assert_called_once_with("2026-01-01", ai_summary=None)
        save_report.assert_called_once()

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
        messages, _, task_key = call.call_args.args
        self.assertEqual(task_key, "basic_analysis")
        self.assertNotIn("qa_analysis", messages[1]["content"])
        self.assertIn('"abstract": "This is the abstract."', messages[2]["content"])

    def test_deep_reading_uses_full_pdf_without_text_limit(self):
        paper = {
            "id": 1,
            "arxiv_id": "2601.00001",
            "title": "A Robot Paper",
            "authors": ["Alice"],
            "abstract": "Abstract",
            "pdf_url": "https://arxiv.org/pdf/2601.00001",
        }
        fake_result = {
            "qa_analysis": "### Q1: ...",
            "tags": ["Robot"],
            "rating": 5,
            "summary_cn": "摘要",
            "value_comment": "很强",
        }

        with patch.object(self.analyzer, "get_paper_full_text", return_value="FULL PDF TEXT") as full_text, \
             patch.object(self.analyzer, "_call_ai", return_value=(fake_result, None)) as call:
            _, result, error = self.analyzer.analyze_paper_full(paper)

        self.assertIsNone(error)
        self.assertIn("qa_analysis", result)
        self.assertEqual(set(result.keys()), {"qa_analysis"})
        full_text.assert_called_once_with("https://arxiv.org/pdf/2601.00001", "2601.00001", max_chars=None)
        messages, _, task_key = call.call_args.args
        self.assertEqual(task_key, "deep_reading")
        for text in ('"tags"', '"rating"', '"summary_cn"', '"value_comment"', "{tag_candidates}", "{rating_criteria}"):
            self.assertNotIn(text, messages[1]["content"])
        self.assertIn('"paper_text": "FULL PDF TEXT"', messages[2]["content"])


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

        ok, message = validate_prompt_template("只做基础分析", profile_key="basic_analysis")
        self.assertFalse(ok)
        self.assertIn("{tag_candidates}", message)

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

            content, _, _, _ = database.generate_report_content("2026-01-01")

        database.DB_DIR = original_dir
        database.DB_PATH = original_path
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", content)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", content)
        self.assertIn("&lt;b&gt;bad&lt;/b&gt;", content)
        self.assertNotIn("<script>", content)
        self.assertNotIn("<img", content)

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
            })
            summary = database.get_ai_usage_summary(days=7)
            summary_by_model = database.get_ai_usage_summary(days=7, group_by="model")

        database.DB_DIR = original_dir
        database.DB_PATH = original_path

        self.assertEqual(summary["items"][0]["task_key"], "basic_analysis")
        self.assertEqual(summary["items"][0]["total_tokens"], 120)
        self.assertEqual(summary["items"][0]["cached_tokens"], 50)
        self.assertEqual(summary["group_by"], "task")
        self.assertEqual(len(summary["dates"]), 7)
        self.assertEqual(summary["totals"]["total_tokens"], 200)
        self.assertEqual(summary["groups"][0]["key"], "basic_analysis")
        self.assertEqual(len(summary["groups"][0]["points"]), 7)
        self.assertTrue(any(point["total_tokens"] == 120 for point in summary["groups"][0]["points"]))
        self.assertEqual(summary_by_model["group_by"], "model")
        self.assertEqual(summary_by_model["groups"][0]["key"], "cheap-model")
        self.assertEqual(len(summary_by_model["groups"]), 2)
        self.assertEqual(sum(group["total_tokens"] for group in summary_by_model["groups"]), 200)
        self.assertTrue(any(group["key"] == "smart-model" and group["cached_tokens"] == 80 for group in summary_by_model["groups"]))


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


class TemplateSafetyTests(unittest.TestCase):
    def test_paper_inline_json_handlers_use_single_quoted_attributes(self):
        with open("templates/paper.html", "r", encoding="utf-8") as f:
            html = f.read()

        self.assertIn("onclick='toggleTodo({{ paper.arxiv_id | tojson }})'", html)
        self.assertIn("onclick='removeTag({{ t.strip() | tojson }})'", html)
        self.assertIn("onclick='removeTag({{ t | tojson }})'", html)
        self.assertNotIn('onclick="toggleTodo({{ paper.arxiv_id | tojson }})"', html)
        self.assertNotIn('onclick="removeTag({{ t | tojson }})"', html)


if __name__ == "__main__":
    unittest.main()
