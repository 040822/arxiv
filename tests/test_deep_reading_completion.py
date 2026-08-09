import json
import unittest
from unittest.mock import patch

class DeepReadingCompletionTests(unittest.TestCase):
    def setUp(self):
        import source.analysis.papers as analyzer
        self.analyzer = analyzer
        self.paper = {
            "id": 1,
            "arxiv_id": "2607.12345",
            "title": "Test Paper",
            "authors": ["Alice"],
            "abstract": "Abstract",
            "pdf_url": "https://arxiv.org/pdf/2607.12345",
        }
        self.profile = {
            "system": "system",
            "instruction": """必须回答：
### Q1: 问题一？
### Q2: 问题二？
""",
        }

    def test_missing_question_is_repaired_and_merged(self):
        first = json.dumps({"qa_analysis": "### Q1: 问题一？\n\n回答一"}, ensure_ascii=False)
        repair = json.dumps({"qa_analysis": "### Q2: 问题二？\n\n回答二"}, ensure_ascii=False)

        with patch.object(self.analyzer, "get_prompt_profile", return_value=self.profile), \
             patch.object(self.analyzer, "get_paper_full_text", return_value="FULL PAPER"), \
             patch.object(self.analyzer, "_call_ai_raw", side_effect=[
                 (first, {"finish_reason": "stop"}),
                 (repair, {"finish_reason": "stop"}),
             ]) as call:
            _, result, error = self.analyzer.analyze_paper_full(self.paper)

        self.assertIsNone(error)
        self.assertTrue(result["complete"])
        self.assertTrue(result["continuation_used"])
        self.assertEqual(result["missing_questions"], [])
        self.assertIn("### Q1:", result["qa_analysis"])
        self.assertIn("### Q2:", result["qa_analysis"])
        self.assertEqual(call.call_count, 2)

    def test_truncated_json_is_continued_from_the_raw_assistant_prefix(self):
        prefix = '{"qa_analysis":"### Q1: 问题一？\\n\\n回答一\\n\\n### Q2: 问题二？\\n\\n回'
        suffix = '答二"}'

        with patch.object(self.analyzer, "get_prompt_profile", return_value=self.profile), \
             patch.object(self.analyzer, "get_paper_full_text", return_value="FULL PAPER"), \
             patch.object(self.analyzer, "_call_ai_raw", side_effect=[
                 (prefix, {"finish_reason": "length"}),
                 (suffix, {"finish_reason": "stop"}),
             ]) as call:
            _, result, error = self.analyzer.analyze_paper_full(self.paper)

        self.assertIsNone(error)
        self.assertTrue(result["complete"])
        self.assertTrue(result["continuation_used"])
        self.assertIn("回答二", result["qa_analysis"])
        continuation_messages = call.call_args_list[1].args[0]
        self.assertEqual(continuation_messages[-2], {"role": "assistant", "content": prefix})

    def test_length_limited_repair_remains_incomplete(self):
        first = json.dumps({"qa_analysis": "### Q1: 问题一？\n\n回答一"}, ensure_ascii=False)
        repair = json.dumps({"qa_analysis": "### Q2: 问题二？\n\n不完整回答"}, ensure_ascii=False)

        with patch.object(self.analyzer, "get_prompt_profile", return_value=self.profile), \
             patch.object(self.analyzer, "get_paper_full_text", return_value="FULL PAPER"), \
             patch.object(self.analyzer, "_call_ai_raw", side_effect=[
                 (first, {"finish_reason": "stop"}),
                 (repair, {"finish_reason": "length"}),
             ]):
            _, result, error = self.analyzer.analyze_paper_full(self.paper)

        self.assertIsNone(error)
        self.assertFalse(result["complete"])
        self.assertEqual(result["missing_questions"], ["Q2"])

    def test_failed_json_continuation_returns_incomplete_metadata(self):
        with patch.object(self.analyzer, "get_prompt_profile", return_value=self.profile), \
             patch.object(self.analyzer, "get_paper_full_text", return_value="FULL PAPER"), \
             patch.object(self.analyzer, "_call_ai_raw", side_effect=[
                 ('{"qa_analysis":"### Q1:', {"finish_reason": "length"}),
                 ('still invalid', {"finish_reason": "length"}),
             ]):
            _, result, error = self.analyzer.analyze_paper_full(self.paper)

        self.assertIsNone(error)
        self.assertFalse(result["complete"])
        self.assertTrue(result["continuation_used"])
        self.assertEqual(result["missing_questions"], ["Q1", "Q2"])
        self.assertEqual(result["finish_reason"], "length")


if __name__ == "__main__":
    unittest.main()
