"""
test_ai_task_settings.py — 由 tests/test_ai_provider_config.py 拆分迁入（Q20），方法体逐字节一致。
"""

import unittest
import os
import json
import tempfile

from source.settings import (
    DEFAULT_AI_TASK_OPTIONS,
    build_chat_completion_kwargs,
    get_ai_task_config,
    get_research_interest_hash,
    get_session_secret,
    get_webdav_backup_config,
    load_settings,
    save_ai_tasks,
    save_webdav_backup_config,
    validate_prompt_template,
)
from source.settings.normalize import _normalize_prompt_profiles
from source.settings import store as settings_store


class AiTaskSettingsTests(unittest.TestCase):
    def test_first_load_without_settings_file_returns_and_persists_schema_v3(self):

        original_path = settings_store.SETTINGS_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                settings_store.SETTINGS_PATH = os.path.join(tmp, "settings.json")

                loaded = load_settings()
                with open(settings_store.SETTINGS_PATH, "r", encoding="utf-8") as f:
                    persisted = json.load(f)

            self.assertEqual(loaded["settings_schema_version"], 4)
            self.assertEqual(persisted["settings_schema_version"], 4)
        finally:
            settings_store.SETTINGS_PATH = original_path

    def test_v2_migration_persists_schema_and_prunes_legacy_sampling_fields(self):

        original_path = settings_store.SETTINGS_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                settings_store.SETTINGS_PATH = os.path.join(tmp, "settings.json")
                with open(settings_store.SETTINGS_PATH, "w", encoding="utf-8") as f:
                    json.dump({
                        "settings_schema_version": 2,
                        "providers": {
                            "route-a": {
                                "name": "Route A",
                                "api_key": "sk-a",
                                "base_url": "https://api.example.com/v1",
                                "available_models": ["model-a"],
                            },
                            "route-b": {
                                "name": "Route B",
                                "api_key": "sk-b",
                                "base_url": "https://api.example.com/v1",
                                "available_models": ["model-b"],
                            },
                        },
                        "ai_tasks": {
                            "basic_analysis": {
                                "provider_key": "route-a",
                                "model": "model-a",
                                "temperature": 0.7,
                                "temperature_enabled": True,
                                "max_tokens": 4321,
                                "max_tokens_enabled": True,
                                "is_thinking": True,
                                "thinking_effort": "high",
                                "top_p": 0.2,
                                "top_p_enabled": True,
                                "presence_penalty": 0.4,
                                "presence_penalty_enabled": True,
                                "frequency_penalty": 0.6,
                                "frequency_penalty_enabled": True,
                            },
                        },
                    }, f)

                loaded = load_settings()
                with open(settings_store.SETTINGS_PATH, "r", encoding="utf-8") as f:
                    persisted = json.load(f)

            route = loaded["ai_tasks"]["basic_analysis"]
            persisted_route = persisted["ai_tasks"]["basic_analysis"]
            self.assertEqual(loaded["settings_schema_version"], 4)
            self.assertEqual(persisted["settings_schema_version"], 4)
            for candidate in (route, persisted_route):
                self.assertEqual(candidate["provider_key"], "route-a")
                self.assertEqual(candidate["model"], "model-a")
                self.assertEqual(candidate["temperature"], 0.7)
                self.assertTrue(candidate["temperature_enabled"])
                self.assertEqual(candidate["max_tokens"], 4321)
                self.assertTrue(candidate["max_tokens_enabled"])
                self.assertTrue(candidate["is_thinking"])
                self.assertEqual(candidate["thinking_effort"], "high")
                for field in (
                    "top_p", "top_p_enabled",
                    "presence_penalty", "presence_penalty_enabled",
                    "frequency_penalty", "frequency_penalty_enabled",
                ):
                    self.assertNotIn(field, candidate)
        finally:
            settings_store.SETTINGS_PATH = original_path

    def test_schema_v3_dirty_sampling_fields_are_pruned_and_persisted_at_all_levels(self):

        legacy_fields = (
            "top_p", "top_p_enabled",
            "presence_penalty", "presence_penalty_enabled",
            "frequency_penalty", "frequency_penalty_enabled",
        )
        legacy_values = {
            "top_p": 0.2,
            "top_p_enabled": True,
            "presence_penalty": 0.4,
            "presence_penalty_enabled": True,
            "frequency_penalty": 0.6,
            "frequency_penalty_enabled": True,
        }
        original_path = settings_store.SETTINGS_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                settings_store.SETTINGS_PATH = os.path.join(tmp, "settings.json")
                with open(settings_store.SETTINGS_PATH, "w", encoding="utf-8") as f:
                    json.dump({
                        "settings_schema_version": 3,
                        "custom_top_level": "keep-me",
                        **legacy_values,
                        "providers": {
                            "route-a": {
                                "name": "Route A",
                                "api_key": "sk-a",
                                "base_url": "https://api.example.com/v1",
                                "available_models": ["model-a"],
                                **legacy_values,
                            },
                        },
                        "ai_tasks": {
                            "basic_analysis": {
                                "provider_key": "route-a",
                                "model": "model-a",
                                "temperature": 0.7,
                                "temperature_enabled": True,
                                "max_tokens": 4321,
                                "max_tokens_enabled": True,
                                "is_thinking": True,
                                "thinking_effort": "high",
                                **legacy_values,
                            },
                        },
                    }, f)

                loaded = load_settings()
                with open(settings_store.SETTINGS_PATH, "r", encoding="utf-8") as f:
                    persisted = json.load(f)

            loaded_route = loaded["ai_tasks"]["basic_analysis"]
            persisted_route = persisted["ai_tasks"]["basic_analysis"]
            for candidate in (loaded, persisted):
                self.assertEqual(candidate["settings_schema_version"], 4)
                self.assertEqual(candidate["custom_top_level"], "keep-me")
                for field in legacy_fields:
                    self.assertNotIn(field, candidate)
                for field in legacy_fields:
                    self.assertNotIn(field, candidate["providers"]["route-a"])

            for candidate in (loaded_route, persisted_route):
                self.assertEqual(candidate["provider_key"], "route-a")
                self.assertEqual(candidate["model"], "model-a")
                self.assertEqual(candidate["temperature"], 0.7)
                self.assertTrue(candidate["temperature_enabled"])
                self.assertEqual(candidate["max_tokens"], 4321)
                self.assertTrue(candidate["max_tokens_enabled"])
                self.assertTrue(candidate["is_thinking"])
                self.assertEqual(candidate["thinking_effort"], "high")
                for field in legacy_fields:
                    self.assertNotIn(field, candidate)
        finally:
            settings_store.SETTINGS_PATH = original_path

    def test_v2_drops_legacy_sampling_fields_without_rerouting_task(self):

        original_path = settings_store.SETTINGS_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                settings_store.SETTINGS_PATH = os.path.join(tmp, "settings.json")
                with open(settings_store.SETTINGS_PATH, "w", encoding="utf-8") as f:
                    json.dump({
                        "settings_schema_version": 2,
                        "providers": {
                            "route-a": {
                                "name": "Route A",
                                "api_key": "sk-a",
                                "base_url": "https://api.example.com/v1",
                                "available_models": ["model-a"],
                            },
                            "route-b": {
                                "name": "Route B",
                                "api_key": "sk-b",
                                "base_url": "https://api.example.com/v1",
                                "available_models": ["model-b"],
                            },
                        },
                        "ai_tasks": {
                            "basic_analysis": {
                                "provider_key": "route-a",
                                "model": "model-a",
                                "temperature": 0.7,
                                "temperature_enabled": True,
                                "max_tokens": 4321,
                                "max_tokens_enabled": True,
                                "is_thinking": False,
                                "thinking_effort": "low",
                                "top_p": 0.2,
                                "top_p_enabled": True,
                                "presence_penalty": 0.4,
                                "presence_penalty_enabled": True,
                                "frequency_penalty": 0.6,
                                "frequency_penalty_enabled": True,
                            },
                        },
                    }, f)

                loaded = load_settings()
                route = loaded["ai_tasks"]["basic_analysis"]
                kwargs = build_chat_completion_kwargs({
                    **route,
                    **loaded["providers"][route["provider_key"]],
                }, [{"role": "user", "content": "hi"}])

            self.assertEqual(loaded["settings_schema_version"], 4)
            self.assertEqual(route["provider_key"], "route-a")
            self.assertEqual(route["model"], "model-a")
            self.assertEqual(route["temperature"], 0.7)
            self.assertTrue(route["temperature_enabled"])
            self.assertEqual(route["max_tokens"], 4321)
            self.assertTrue(route["max_tokens_enabled"])
            self.assertFalse(route["is_thinking"])
            self.assertEqual(route["thinking_effort"], "low")
            self.assertEqual(kwargs["temperature"], 0.7)
            self.assertEqual(kwargs["max_tokens"], 4321)
            for field in (
                "top_p", "top_p_enabled",
                "presence_penalty", "presence_penalty_enabled",
                "frequency_penalty", "frequency_penalty_enabled",
            ):
                self.assertNotIn(field, route)
                self.assertNotIn(field, kwargs)
        finally:
            settings_store.SETTINGS_PATH = original_path

    def test_legacy_provider_inference_fields_migrate_to_explicit_task_routes(self):

        original_path = settings_store.SETTINGS_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                settings_store.SETTINGS_PATH = os.path.join(tmp, "settings.json")
                with open(settings_store.SETTINGS_PATH, "w", encoding="utf-8") as f:
                    json.dump({
                        "active_provider": "legacy",
                        "providers": {
                            "legacy": {
                                "name": "Legacy",
                                "api_key": "sk-legacy",
                                "base_url": "https://api.example.com/v1",
                            "model": "legacy-model",
                            "temperature": 0.9,
                            "temperature_enabled": True,
                            "max_tokens": 4321,
                            "max_tokens_enabled": True,
                            "is_thinking": True,
                            "thinking_effort": "high",
                                "available_models": ["legacy-model", "other-model"],
                            }
                        },
                    }, f)

                loaded = load_settings()

            self.assertEqual(loaded["ai_tasks"]["basic_analysis"]["provider_key"], "legacy")
            self.assertEqual(loaded["ai_tasks"]["basic_analysis"]["model"], "legacy-model")
            self.assertEqual(loaded["ai_tasks"]["basic_analysis"]["temperature"], 0.9)
            self.assertEqual(loaded["ai_tasks"]["basic_analysis"]["max_tokens"], 4321)
            self.assertTrue(loaded["ai_tasks"]["basic_analysis"]["is_thinking"])
            self.assertEqual(loaded["ai_tasks"]["basic_analysis"]["thinking_effort"], "high")
            self.assertNotIn("active_provider", loaded)
            self.assertEqual(set(loaded["providers"]["legacy"]), {
                "name", "api_key", "base_url", "available_models",
            })
        finally:
            settings_store.SETTINGS_PATH = original_path

    def test_ai_task_routes_reject_missing_provider_references(self):

        original_path = settings_store.SETTINGS_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                settings_store.SETTINGS_PATH = os.path.join(tmp, "settings.json")
                tasks = {
                    key: {**value, "provider_key": "demo", "model": "demo-model"}
                    for key, value in DEFAULT_AI_TASK_OPTIONS.items()
                }
                tasks["paper_import"]["provider_key"] = "missing"
                with open(settings_store.SETTINGS_PATH, "w", encoding="utf-8") as f:
                    json.dump({
                        "settings_schema_version": 2,
                        "providers": {
                            "demo": {
                                "name": "Demo",
                                "api_key": "sk-demo",
                                "base_url": "https://api.example.com/v1",
                                "available_models": ["demo-model"],
                            }
                        },
                        "ai_tasks": tasks,
                    }, f)

                with self.assertRaisesRegex(ValueError, "PDF 元数据提取.*供应商不存在"):
                    save_ai_tasks(tasks)
        finally:
            settings_store.SETTINGS_PATH = original_path

    def test_current_schema_load_preserves_invalid_routes_for_validation(self):

        original_path = settings_store.SETTINGS_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                settings_store.SETTINGS_PATH = os.path.join(tmp, "settings.json")
                with open(settings_store.SETTINGS_PATH, "w", encoding="utf-8") as f:
                    json.dump({
                        "settings_schema_version": 2,
                        "providers": {
                            "demo": {
                                "name": "Demo",
                                "api_key": "sk-demo",
                                "base_url": "https://api.example.com/v1",
                                "available_models": ["demo-model"],
                            }
                        },
                        "ai_tasks": {
                            "paper_import": {"provider_key": "missing", "model": ""},
                        },
                    }, f)

                loaded = load_settings()

            self.assertEqual(loaded["ai_tasks"]["paper_import"]["provider_key"], "missing")
            self.assertEqual(loaded["ai_tasks"]["paper_import"]["model"], "")
        finally:
            settings_store.SETTINGS_PATH = original_path

    def test_legacy_settings_get_default_task_routes_and_profiles(self):

        original_path = settings_store.SETTINGS_PATH
        with tempfile.TemporaryDirectory() as tmp:
            settings_store.SETTINGS_PATH = os.path.join(tmp, "settings.json")
            with open(settings_store.SETTINGS_PATH, "w", encoding="utf-8") as f:
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

            loaded = load_settings()

        settings_store.SETTINGS_PATH = original_path

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

        original_path = settings_store.SETTINGS_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                settings_store.SETTINGS_PATH = os.path.join(tmp, "settings.json")
                with open(settings_store.SETTINGS_PATH, "w", encoding="utf-8") as f:
                    json.dump({"session_secret": "stable-secret"}, f)

                loaded = load_settings()

            self.assertEqual(loaded["session_secret"], "stable-secret")
        finally:
            settings_store.SETTINGS_PATH = original_path

    def test_legacy_schedule_is_upgraded_to_full_daily_pipeline_defaults(self):

        original_path = settings_store.SETTINGS_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                settings_store.SETTINGS_PATH = os.path.join(tmp, "settings.json")
                with open(settings_store.SETTINGS_PATH, "w", encoding="utf-8") as f:
                    json.dump({
                        "schedule": {"enabled": True, "hour": 8, "minute": 30},
                    }, f)

                loaded = load_settings()["schedule"]

            self.assertEqual(loaded["days_of_week"], ["mon", "tue", "wed", "thu", "fri", "sat", "sun"])
            self.assertEqual(loaded["fetch_days"], 3)
            self.assertEqual(loaded["analyze_limit"], 1000)
            self.assertEqual(loaded["fetch_retry_interval_minutes"], 10)
            self.assertEqual(loaded["fetch_max_retries"], 20)
            self.assertEqual((loaded["hour"], loaded["minute"]), (8, 30))
        finally:
            settings_store.SETTINGS_PATH = original_path

    def test_get_session_secret_generates_and_persists_secret(self):

        original_path = settings_store.SETTINGS_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                settings_store.SETTINGS_PATH = os.path.join(tmp, "settings.json")

                secret = get_session_secret()
                with open(settings_store.SETTINGS_PATH, "r", encoding="utf-8") as f:
                    saved = json.load(f)

            self.assertGreaterEqual(len(secret), 32)
            self.assertEqual(saved["session_secret"], secret)
        finally:
            settings_store.SETTINGS_PATH = original_path

    def test_personalization_config_is_trimmed_and_preserved(self):

        original_path = settings_store.SETTINGS_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                settings_store.SETTINGS_PATH = os.path.join(tmp, "settings.json")
                with open(settings_store.SETTINGS_PATH, "w", encoding="utf-8") as f:
                    json.dump({
                        "personalization": {"research_interests": "  robot learning  "},
                    }, f)

                loaded = load_settings()
                interest_hash = get_research_interest_hash(loaded["personalization"]["research_interests"])

            self.assertEqual(loaded["personalization"]["research_interests"], "robot learning")
            self.assertEqual(len(interest_hash), 64)
        finally:
            settings_store.SETTINGS_PATH = original_path

    def test_webdav_backup_config_is_preserved_and_masked(self):

        original_path = settings_store.SETTINGS_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                settings_store.SETTINGS_PATH = os.path.join(tmp, "settings.json")
                with open(settings_store.SETTINGS_PATH, "w", encoding="utf-8") as f:
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

                loaded = load_settings()
                masked = get_webdav_backup_config(mask_password=True)

            self.assertTrue(loaded["webdav_backup"]["enabled"])
            self.assertEqual(loaded["webdav_backup"]["url"], "https://dav.example.com/root/")
            self.assertEqual(loaded["webdav_backup"]["password"], "secret")
            self.assertEqual(loaded["webdav_backup"]["remote_dir"], "backups/arxiv")
            self.assertEqual(loaded["webdav_backup"]["history_days"], 3)
            self.assertNotIn("password", masked)
            self.assertEqual(masked["password_masked"], "******")
        finally:
            settings_store.SETTINGS_PATH = original_path

    def test_save_webdav_backup_config_preserves_existing_password_when_blank(self):

        original_path = settings_store.SETTINGS_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                settings_store.SETTINGS_PATH = os.path.join(tmp, "settings.json")
                with open(settings_store.SETTINGS_PATH, "w", encoding="utf-8") as f:
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

                self.assertTrue(save_webdav_backup_config({
                    "enabled": False,
                    "url": "https://dav.example.com/new",
                    "username": "bob",
                    "password": "",
                    "remote_dir": "new",
                    "history_days": 5,
                }))
                saved = load_settings()["webdav_backup"]

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
            settings_store.SETTINGS_PATH = original_path

    def test_task_config_merges_provider_credentials_and_task_overrides(self):

        original_path = settings_store.SETTINGS_PATH
        with tempfile.TemporaryDirectory() as tmp:
            settings_store.SETTINGS_PATH = os.path.join(tmp, "settings.json")
            with open(settings_store.SETTINGS_PATH, "w", encoding="utf-8") as f:
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

            cfg = get_ai_task_config("deep_reading")

        settings_store.SETTINGS_PATH = original_path

        self.assertEqual(cfg["api_key"], "sk-smart")
        self.assertEqual(cfg["provider_key"], "smart")
        self.assertEqual(cfg["model"], "qwen3-smart")
        kwargs = build_chat_completion_kwargs(cfg, [{"role": "user", "content": "hi"}])
        self.assertNotIn("temperature", kwargs)
        self.assertEqual(kwargs["extra_body"]["thinking_budget"], 8192)
        self.assertEqual(kwargs["max_tokens"], 6000)

    def test_recommendation_task_config_is_independent_route(self):

        original_path = settings_store.SETTINGS_PATH
        with tempfile.TemporaryDirectory() as tmp:
            settings_store.SETTINGS_PATH = os.path.join(tmp, "settings.json")
            with open(settings_store.SETTINGS_PATH, "w", encoding="utf-8") as f:
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

            cfg = get_ai_task_config("recommendation")

        settings_store.SETTINGS_PATH = original_path

        self.assertEqual(cfg["task_key"], "recommendation")
        self.assertEqual(cfg["provider_key"], "rec")
        self.assertEqual(cfg["api_key"], "sk-rec")
        self.assertEqual(cfg["model"], "rec-model")
        self.assertEqual(cfg["max_tokens"], 321)

    def test_legacy_deep_reading_prompt_migrates_to_qa_only(self):

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
        profiles = _normalize_prompt_profiles({
            "deep_reading": {"system": "s", "instruction": legacy_prompt}
        })
        instruction = profiles["deep_reading"]["instruction"]

        self.assertIn("自定义问题？", instruction)
        self.assertIn('"qa_analysis"', instruction)
        for text in ('"tags"', '"rating"', '"summary_cn"', '"value_comment"', "{tag_candidates}", "{rating_criteria}"):
            self.assertNotIn(text, instruction)

    def test_custom_deep_reading_prompt_is_preserved(self):

        custom_prompt = '请按我自己的结构返回 {"qa_analysis": "详细内容"}，并重点分析机器人实验。'
        profiles = _normalize_prompt_profiles({
            "deep_reading": {"system": "s", "instruction": custom_prompt}
        })

        self.assertEqual(profiles["deep_reading"]["instruction"], custom_prompt)

    def test_weak_qa_only_prompt_migrates_to_strict_all_questions_prompt(self):

        weak_prompt = """请对论文内容进行深度阅读分析，只生成 Q&A 深度阅读内容。

请严格返回合法 JSON，不要返回额外解释：

{
  "qa_analysis": "### Q1: 问题一？\\n\\n详细回答...\\n\\n### Q2: 问题二？\\n\\n详细回答..."
}

重要要求：
1. qa_analysis 中每个 Q&A 使用 Markdown 标题格式（### Qn: 问题）
"""
        profiles = _normalize_prompt_profiles({
            "deep_reading": {"system": "s", "instruction": weak_prompt}
        })
        instruction = profiles["deep_reading"]["instruction"]

        self.assertIn("必须按顺序完整回答以下 2 个问题", instruction)
        self.assertIn("问题一？", instruction)
        self.assertIn("问题二？", instruction)
        self.assertIn("必须输出 Q1 到 Q2 的全部条目", instruction)


if __name__ == "__main__":
    unittest.main()
