import os
import tempfile
import sys
import types
import unittest
from unittest.mock import patch

from settings import build_chat_completion_kwargs, validate_prompt_template


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
    def __init__(self, data=None, endpoint="", method="POST", path="/api/test"):
        self._data = data
        self.endpoint = endpoint
        self.method = method
        self.path = path
        self.full_path = path
        self.query_string = b""
        self.args = {}

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


class PromptAndReportSafetyTests(unittest.TestCase):
    def test_prompt_validation_rejects_missing_required_field(self):
        ok, message = validate_prompt_template("标题: {title}\n摘要: {abstract}")
        self.assertFalse(ok)
        self.assertIn("{authors}", message)

    def test_prompt_validation_rejects_unescaped_json_braces(self):
        ok, message = validate_prompt_template("{title}\n{\"rating\": 3}\n{authors}\n{abstract}\n{tag_candidates}\n{rating_criteria}")
        self.assertFalse(ok)
        self.assertIn("Prompt", message)

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


if __name__ == "__main__":
    unittest.main()
