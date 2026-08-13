"""
test_analyzer_routing.py — 由 tests/test_ai_provider_config.py 拆分迁入（Q20），方法体逐字节一致。
"""

import unittest
import json
import types
from unittest.mock import patch

from .common import (
    install_import_stubs,
)


class AiCallRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_import_stubs()
        import source.analysis as analyzer
        import source.analysis.core as analysis_core
        import source.analysis.json_support as json_support
        import source.analysis.learning as analysis_learning
        import source.analysis.papers as analysis_papers
        import source.analysis.usage as analysis_usage
        cls.analyzer = analyzer
        cls.analysis_core = analysis_core
        cls.json_support = json_support
        cls.analysis_learning = analysis_learning
        cls.analysis_papers = analysis_papers
        cls.analysis_usage = analysis_usage

    def test_basic_analysis_uses_short_profile_without_qa(self):
        paper = {
            "id": 1,
            "arxiv_id": "2601.00001",
            "title": "A Robot Paper",
            "authors": ["Alice", "Bob"],
            "abstract": "This is the abstract.",
        }
        fake_result = {"tags": ["VLA"], "rating": 4, "summary_cn": "摘要", "value_comment": "有价值"}

        with patch.object(self.analysis_papers, "_call_ai", return_value=(fake_result, None)) as call:
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

        with patch.object(self.analysis_papers, "_call_ai", return_value=(fake_result, None)):
            _, result, error = self.analyzer.analyze_paper_basic(paper)

        self.assertIsNone(error)
        self.assertEqual(result["rating"], 5)

    def test_json_cleaner_repairs_invalid_escapes_without_breaking_latex(self):
        content = (
            '{"qa_analysis": "valid latex: \\\\alpha and unicode \\\\u03b1; '
            'invalid markdown \\_ and latex \\uparrow"}'
        )

        cleaned = self.json_support._clean_json_content(content)
        parsed = json.loads(cleaned)

        self.assertIn(r"\alpha", parsed["qa_analysis"])
        self.assertIn(r"\u03b1", cleaned)
        self.assertIn("invalid markdown _", parsed["qa_analysis"])
        self.assertIn("latex uparrow", parsed["qa_analysis"])

    def test_json_cleaner_repairs_raw_control_chars_inside_strings(self):
        content = '{"qa_analysis": "第一行\n第二行\t带制表符"}'
        cleaned = self.json_support._clean_json_content(content)
        parsed = json.loads(cleaned)
        self.assertEqual(parsed["qa_analysis"], "第一行\n第二行\t带制表符")

    def test_extract_first_json_object_handles_nested_and_escaped_content(self):
        content = '前文 {"a": "含}花括号和\\"引号\\"", "b": 1} 后文 {"c": 2}'
        first = self.json_support._extract_first_json_object(content)
        cleaned = self.json_support._clean_json_content(first)
        self.assertEqual(json.loads(cleaned), {"a": '含}花括号和"引号"', "b": 1})

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

        with patch.object(self.analysis_papers, "get_paper_full_text", return_value="FULL PDF TEXT") as full_text, \
             patch.object(self.analysis_papers, "_call_ai_raw", return_value=(fake_raw, {"finish_reason": "stop"})) as call:
            _, result, error = self.analyzer.analyze_paper_full(paper)

        self.assertIsNone(error)
        self.assertIn("qa_analysis", result)
        self.assertTrue(result["complete"])
        self.assertFalse(result["continuation_used"])
        self.assertEqual(result["missing_questions"], [])
        full_text.assert_called_once_with("https://arxiv.org/pdf/2601.00001", "2601.00001", max_chars=None)
        messages, _, task_key = call.call_args.args
        self.assertEqual(task_key, "deep_reading")
        self.assertIn('"paper_text": "FULL PDF TEXT"', messages[1]["content"])
        self.assertEqual(messages[1]["role"], "user")
        self.assertIn("### Q1:", messages[2]["content"])
        self.assertEqual(messages[2]["role"], "user")

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

        with patch.object(self.analysis_papers, "_call_ai", return_value=(fake_result, None)) as call:
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

        with patch.object(self.analysis_core, "get_cached_pdf_path", return_value="/tmp/cached.pdf"), \
             patch.object(self.analysis_core, "extract_text_from_pdf", return_value="PDF TEXT") as extract_text, \
             patch.object(self.analysis_core, "download_pdf") as download_pdf:
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

        with patch.object(self.analysis_core, "get_cached_pdf_path", return_value=None), \
             patch.object(self.analysis_core, "download_pdf", return_value="/tmp/missing.pdf"), \
             patch.object(self.analysis_core, "extract_text_from_pdf", side_effect=RuntimeError("bad pdf")):
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

        with patch.object(self.analysis_core, "get_learning_paper_text", return_value={
            "paper_text": "FULL PDF TEXT",
            "used_pdf_cache": True,
            "used_pdf_full_text": True,
        }), patch.object(self.analysis_learning, "_call_ai_raw", return_value=(response, {
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
        extracted = self.analysis_usage._extract_usage(types.SimpleNamespace(usage=usage))

        self.assertEqual(extracted["cached_tokens"], 90)
        self.assertEqual(extracted["cache_miss_tokens"], 30)


if __name__ == "__main__":
    unittest.main()
