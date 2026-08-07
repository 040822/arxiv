"""
test_web_papers_api.py — 由 tests/test_ai_provider_config.py 拆分迁入（Q20），方法体逐字节一致。
"""

import unittest
import importlib
from unittest.mock import patch

from .common import (
    FakeRequest,
    install_import_stubs,
    setup_web_test_base,
    teardown_web_test_base,
)


web_papers_api = None

class PapersApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_import_stubs()
        global web_papers_api
        web_papers_api = importlib.import_module("source.web.papers_api")
        cls.web_papers_api = web_papers_api
        setup_web_test_base()

    @classmethod
    def tearDownClass(cls):
        teardown_web_test_base()

    def test_batch_analyze_uses_selected_papers(self):
        web_papers_api = self.web_papers_api
        selected = [
            {"id": 1, "arxiv_id": "2601.00001", "title": "A", "authors": [], "abstract": ""},
            {"id": 2, "arxiv_id": "2601.00002", "title": "B", "authors": [], "abstract": ""},
        ]
        web_papers_api.request = FakeRequest({"arxiv_ids": ["2601.00001", "2601.00002"]})

        with patch.object(web_papers_api, "get_unanalyzed_papers_by_ids", return_value=selected), \
             patch.object(web_papers_api, "get_concurrency", return_value=3), \
             patch.object(web_papers_api, "analyze_papers", return_value=2) as analyze_papers:
            result = web_papers_api.api_batch_analyze_papers()

        self.assertEqual(result["status"], "ok")
        analyze_papers.assert_called_once_with(selected, concurrency=3)

    def test_reanalyze_updates_only_qa_analysis(self):
        web_papers_api = self.web_papers_api
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

        with patch.object(web_papers_api, "get_paper_by_arxiv_id", return_value=paper), \
             patch.object(web_papers_api, "analyze_paper_full", return_value=(paper, deep_result, None)), \
             patch.object(web_papers_api, "update_analysis") as update_analysis:
            result = web_papers_api.api_reanalyze_paper("2601.00009")

        self.assertEqual(result["status"], "ok")
        update_analysis.assert_called_once_with(9, {"qa_analysis": "### Q1: deep"})

    def test_reanalyze_warns_and_preserves_old_qa_when_repair_is_incomplete(self):
        web_papers_api = self.web_papers_api
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

        with patch.object(web_papers_api, "get_paper_by_arxiv_id", return_value=paper), \
             patch.object(web_papers_api, "analyze_paper_full", return_value=(paper, deep_result, None)), \
             patch.object(web_papers_api, "update_analysis") as update_analysis:
            result = web_papers_api.api_reanalyze_paper("2601.00009")

        self.assertEqual(result["status"], "warning")
        self.assertEqual(result["missing_questions"], ["Q2"])
        self.assertTrue(result["continuation_used"])
        update_analysis.assert_not_called()

    def test_add_paper_runs_basic_then_deep_reading(self):
        web_papers_api = self.web_papers_api
        web_papers_api.request = FakeRequest({"input": "2601.00010", "task_id": "t1"})
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

        with patch.object(web_papers_api, "parse_arxiv_id", return_value="2601.00010"), \
             patch.object(web_papers_api, "fetch_paper_by_id", return_value=paper), \
             patch.object(web_papers_api, "get_analysis_by_paper_id", return_value=None), \
             patch.object(web_papers_api, "analyze_paper_basic", side_effect=fake_basic), \
             patch.object(web_papers_api, "insert_analysis", side_effect=fake_insert), \
             patch.object(web_papers_api, "analyze_paper_full", side_effect=fake_deep), \
             patch.object(web_papers_api, "update_analysis", side_effect=fake_update):
            result = web_papers_api.api_add_paper()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["rating"], 4)
        self.assertEqual(result["tags"], ["VLA"])
        self.assertEqual(events, ["basic", "insert", "deep", "update"])

    def test_add_paper_preserves_basic_analysis_when_deep_reading_is_incomplete(self):
        web_papers_api = self.web_papers_api
        web_papers_api.request = FakeRequest({"input": "2601.00012", "task_id": "t1"})
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

        with patch.object(web_papers_api, "parse_arxiv_id", return_value="2601.00012"), \
             patch.object(web_papers_api, "fetch_paper_by_id", return_value=paper), \
             patch.object(web_papers_api, "get_analysis_by_paper_id", return_value=None), \
             patch.object(web_papers_api, "analyze_paper_basic", return_value=(paper, basic_result, None)), \
             patch.object(web_papers_api, "insert_analysis", return_value=1), \
             patch.object(web_papers_api, "analyze_paper_full", return_value=(paper, deep_result, None)), \
             patch.object(web_papers_api, "update_analysis") as update_analysis:
            result = web_papers_api.api_add_paper()

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["deep_reading_incomplete"])
        self.assertEqual(result["missing_questions"], ["Q6"])
        update_analysis.assert_not_called()

    def test_add_paper_with_manual_rating_only_still_runs_basic_analysis(self):
        web_papers_api = self.web_papers_api
        web_papers_api.request = FakeRequest({"input": "2601.00011", "task_id": "t1"})
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

        with patch.object(web_papers_api, "parse_arxiv_id", return_value="2601.00011"), \
             patch.object(web_papers_api, "fetch_paper_by_id", return_value=paper), \
             patch.object(web_papers_api, "get_analysis_by_paper_id", return_value={"rating": 4, "tags": [], "summary_cn": "", "value_comment": ""}), \
             patch.object(web_papers_api, "analyze_paper_basic", return_value=(paper, basic_result, None)) as basic, \
             patch.object(web_papers_api, "insert_analysis", return_value=None) as insert, \
             patch.object(web_papers_api, "analyze_paper_full", return_value=(paper, deep_result, None)), \
             patch.object(web_papers_api, "update_analysis", return_value=True):
            result = web_papers_api.api_add_paper()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["rating"], 4)
        basic.assert_called_once()
        insert.assert_called_once()


if __name__ == "__main__":
    unittest.main()
