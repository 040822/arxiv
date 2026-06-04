import sys
import types
import unittest
from unittest.mock import patch

from settings import build_chat_completion_kwargs


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
            pass

        def route(self, *args, **kwargs):
            def decorator(func):
                return func
            return decorator

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
    sys.modules.setdefault("flask", flask_mod)

    sched_mod = types.ModuleType("apscheduler.schedulers.background")

    class FakeScheduler:
        def add_job(self, *args, **kwargs):
            pass

        def start(self):
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
    def __init__(self, data):
        self._data = data

    def get_json(self):
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


if __name__ == "__main__":
    unittest.main()
