"""
test_prompt_validation.py — 由 tests/test_ai_provider_config.py 拆分迁入（Q20），方法体逐字节一致。
"""

import unittest

from settings import build_chat_completion_kwargs, validate_prompt_template


class PromptValidationTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
