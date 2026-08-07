"""
test_web_providers_api.py — 由 tests/test_ai_provider_config.py 拆分迁入（Q20），方法体逐字节一致。
"""

import unittest
import importlib
import types
from unittest.mock import patch

from .common import (
    DummyHttpxClient,
    DummyOpenAI,
    FakeRequest,
    install_import_stubs,
    setup_web_test_base,
    teardown_web_test_base,
)


web_providers_api = None

class ProviderApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_import_stubs()
        global web_providers_api
        web_providers_api = importlib.import_module("source.web.providers_api")
        cls.web_providers_api = web_providers_api
        setup_web_test_base()

    @classmethod
    def tearDownClass(cls):
        teardown_web_test_base()

    def tearDown(self):
        DummyOpenAI.models_error = None
        DummyOpenAI.models_response = types.SimpleNamespace(data=[])
        DummyOpenAI.chat_response = None
        DummyOpenAI.last_chat_kwargs = None
        DummyOpenAI.last_init_kwargs = None
        DummyHttpxClient.last_kwargs = None

    def test_provider_create_persists_connection_fields_only(self):
        web_providers_api = self.web_providers_api
        web_providers_api.request = FakeRequest({
            "key": "demo",
            "name": "Demo",
            "api_key": "sk-test",
            "base_url": "https://api.example.com/v1",
            "available_models": ["model-b", "model-a", "model-a"],
            "model": "legacy-default",
            "temperature": 0.9,
            "is_thinking": True,
        })

        with patch.object(web_providers_api, "add_provider", return_value=True) as add_provider:
            result = web_providers_api.api_add_provider()

        self.assertEqual(result["status"], "ok")
        add_provider.assert_called_once_with("demo", {
            "name": "Demo",
            "api_key": "sk-test",
            "base_url": "https://api.example.com/v1",
            "available_models": ["model-b", "model-a"],
        })

    def test_provider_delete_is_blocked_when_task_routes_reference_it(self):
        web_providers_api = self.web_providers_api
        with patch.object(web_providers_api, "remove_provider", return_value={
            "removed": False,
            "references": ["basic_analysis", "paper_import"],
        }):
            result, status = web_providers_api.api_delete_provider("demo")

        self.assertEqual(status, 409)
        self.assertEqual(result["references"], ["基础分析", "PDF 元数据提取"])
        self.assertIn("先修改功能模型路由", result["message"])

    def test_ai_task_route_test_uses_unsaved_draft_and_reports_thinking(self):
        web_providers_api = self.web_providers_api
        draft = {
            "provider_key": "demo",
            "model": "reasoning-model-v2",
            "is_thinking": True,
            "thinking_effort": "high",
            "temperature_enabled": False,
            "top_p_enabled": False,
            "presence_penalty_enabled": False,
            "frequency_penalty_enabled": False,
            "max_tokens_enabled": True,
            "max_tokens": 4000,
        }
        cfg = {
            **draft,
            "task_key": "paper_chat",
            "provider_key": "demo",
            "provider_name": "Demo",
            "api_key": "sk-demo",
            "base_url": "https://api.example.com/v1",
        }
        web_providers_api.request = FakeRequest({"config": draft})
        response = types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(
                content="ok", reasoning_content="reasoning",
            ))],
            usage=types.SimpleNamespace(
                completion_tokens_details=types.SimpleNamespace(reasoning_tokens=12),
            ),
        )
        fake_client = types.SimpleNamespace(chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=lambda **kwargs: response),
        ))

        with patch.object(web_providers_api, "resolve_ai_task_config", return_value=cfg) as resolve, \
                patch.object(web_providers_api, "get_openai_client", return_value=fake_client):
            result = web_providers_api.api_test_ai_task_route("paper_chat")

        resolve.assert_called_once_with("paper_chat", draft)
        self.assertEqual(result["task_name"], "论文对话")
        self.assertEqual(result["provider_key"], "demo")
        self.assertEqual(result["model"], "reasoning-model-v2")
        self.assertTrue(result["thinking_detection"]["detected"])
        self.assertEqual(result["thinking_detection"]["confidence"], "high")
        self.assertGreaterEqual(result["duration_ms"], 0)

    def test_ai_task_route_test_does_not_infer_thinking_from_base_url_alone(self):
        web_providers_api = self.web_providers_api
        draft = {
            "provider_key": "deepseek",
            "model": "deepseek-chat",
            "is_thinking": False,
            "temperature_enabled": True,
            "temperature": 0.2,
        }
        cfg = {
            **draft,
            "task_key": "basic_analysis",
            "provider_name": "DeepSeek",
            "api_key": "sk-demo",
            "base_url": "https://api.deepseek.com",
        }
        web_providers_api.request = FakeRequest({"config": draft})
        response = types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="ok"))],
            usage=types.SimpleNamespace(completion_tokens_details=None),
        )
        fake_client = types.SimpleNamespace(chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=lambda **kwargs: response),
        ))

        with patch.object(web_providers_api, "resolve_ai_task_config", return_value=cfg), \
                patch.object(web_providers_api, "get_openai_client", return_value=fake_client):
            result = web_providers_api.api_test_ai_task_route("basic_analysis")

        self.assertFalse(result["thinking_detection"]["detected"])
        self.assertEqual(result["thinking_detection"]["confidence"], "low")

    def test_provider_models_endpoint_returns_sorted_models(self):
        web_providers_api = self.web_providers_api
        web_providers_api.request = FakeRequest({
            "provider_key": "demo",
            "api_key": "sk-test",
            "base_url": "https://api.example.com/v1",
        })
        DummyOpenAI.models_response = types.SimpleNamespace(
            data=[types.SimpleNamespace(id="model-b"), {"id": "model-a"}]
        )

        with patch.object(web_providers_api, "update_provider") as update_provider:
            result = web_providers_api.api_provider_models()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["models"], ["model-a", "model-b"])
        update_provider.assert_called_once_with("demo", {"available_models": ["model-a", "model-b"]})

    def test_provider_models_uses_shared_openai_client(self):
        web_providers_api = self.web_providers_api
        web_providers_api.request = FakeRequest({
            "provider_key": "demo",
            "api_key": "sk-test",
            "base_url": "https://api.example.com/v1",
        })
        fake_client = types.SimpleNamespace(
            models=types.SimpleNamespace(list=lambda: types.SimpleNamespace(data=[{"id": "model-a"}]))
        )

        with patch.object(web_providers_api, "get_openai_client", return_value=fake_client) as get_client, \
                patch.object(web_providers_api, "update_provider"):
            result = web_providers_api.api_provider_models()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(get_client.call_args.args[0]["api_key"], "sk-test")
        self.assertEqual(get_client.call_args.args[0]["base_url"], "https://api.example.com/v1")

    def test_provider_models_endpoint_reports_fetch_errors(self):
        web_providers_api = self.web_providers_api
        web_providers_api.request = FakeRequest({
            "api_key": "sk-test",
            "base_url": "https://api.example.com/v1",
        })
        DummyOpenAI.models_error = RuntimeError("boom")

        result, status = web_providers_api.api_provider_models()

        self.assertEqual(status, 500)
        self.assertEqual(result["status"], "error")
        self.assertIn("boom", result["message"])

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

    def test_provider_list_does_not_return_plain_api_key(self):
        web_providers_api = self.web_providers_api
        with patch.object(web_providers_api, "load_settings", return_value={
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
            result = web_providers_api.api_list_providers()

        provider = result["providers"]["demo"]
        self.assertNotIn("api_key", provider)
        self.assertEqual(provider["api_key_masked"], "sk-s****alue")


if __name__ == "__main__":
    unittest.main()
