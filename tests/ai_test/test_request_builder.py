"""
test_request_builder.py — 由 tests/test_ai_provider_config.py 拆分迁入（Q20），方法体逐字节一致。
"""

import unittest

from source.settings import build_chat_completion_kwargs, validate_prompt_template


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

    def test_regular_model_ignores_removed_sampling_fields(self):
        kwargs = build_chat_completion_kwargs(
            {
                "base_url": "https://api.example.com/v1",
                "model": "gpt-4o",
                "temperature": 0.4,
                "temperature_enabled": True,
                "top_p": 0.2,
                "top_p_enabled": True,
                "presence_penalty": 0.4,
                "presence_penalty_enabled": True,
                "frequency_penalty": 0.6,
                "frequency_penalty_enabled": True,
            },
            [{"role": "user", "content": "hi"}],
        )

        self.assertEqual(kwargs["temperature"], 0.4)
        for field in ("top_p", "presence_penalty", "frequency_penalty"):
            self.assertNotIn(field, kwargs)

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


if __name__ == "__main__":
    unittest.main()
