"""
test_runtime_propagation.py — 由旧测试拆分迁入，并包含运行时配置传播回归测试。
"""

import unittest
import tempfile
from unittest.mock import patch

from . import common
from .common import (
    FakeRequest,
    install_import_stubs,
    setup_web_test_base,
    teardown_web_test_base,
)
from source.settings.normalize import _normalize_fetch_config


web_papers_api = None

class RuntimeSettingPropagationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_import_stubs()
        import app
        import source.analysis.batch as analyzer
        import source.ingestion as fetcher
        import source.documents as pdf_reader
        cls.app = app
        cls.analyzer = analyzer
        cls.fetcher = fetcher
        cls.pdf_reader = pdf_reader
        setup_web_test_base()
        global web_papers_api
        web_papers_api = common.web_papers_api

    @classmethod
    def tearDownClass(cls):
        teardown_web_test_base()

    def test_api_papers_uses_saved_per_page_setting(self):
        web_papers_api.request = FakeRequest(args={"page": "2"})
        with patch.object(web_papers_api, "get_per_page", return_value=50), \
             patch.object(web_papers_api, "get_papers_with_analysis", return_value=[]) as get_papers:
            result = web_papers_api.api_papers()

        self.assertEqual(result, [])
        get_papers.assert_called_once()
        self.assertEqual(get_papers.call_args.kwargs["limit"], 50)
        self.assertEqual(get_papers.call_args.kwargs["offset"], 50)

    def test_api_papers_allows_request_per_page_override(self):
        web_papers_api.request = FakeRequest(args={"page": "3", "per_page": "10"})
        with patch.object(web_papers_api, "get_per_page", return_value=50), \
             patch.object(web_papers_api, "get_papers_with_analysis", return_value=[]) as get_papers:
            web_papers_api.api_papers()

        self.assertEqual(get_papers.call_args.kwargs["limit"], 10)
        self.assertEqual(get_papers.call_args.kwargs["offset"], 20)

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

    def test_analyze_papers_updates_existing_manual_rating_without_overwriting_rating(self):
        paper = {"id": 1, "arxiv_id": "2601.00001"}
        result = {"tags": ["VLA"], "summary_cn": "摘要", "summary_en": "", "rating": 0, "value_comment": "简评"}

        with patch.object(self.analyzer, "analyze_paper_basic", return_value=(paper, result, None)), \
             patch.object(self.analyzer, "insert_analysis", return_value=None), \
             patch.object(self.analyzer, "update_analysis", return_value=True) as update_analysis, \
             patch.object(self.analyzer.logger, "info"):
            count = self.analyzer.analyze_papers([paper], concurrency=1)

        self.assertEqual(count, 1)
        update_analysis.assert_called_once_with(1, {
            "rating": 0,
            "tags": ["VLA"],
            "summary_cn": "摘要",
            "summary_en": "",
            "value_comment": "简评",
        })

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

    def test_fetch_config_defaults_match_ui_contract(self):
        normalized = _normalize_fetch_config({})

        self.assertEqual(normalized, {
            "request_delay": 5.0,
            "batch_days": 30,
            "batch_delay": 10.0,
        })


    def test_fetch_config_is_normalized(self):
        normalized = _normalize_fetch_config({
            "request_delay": "1",
            "batch_days": "999",
            "batch_delay": "9999",
        })

        self.assertEqual(normalized, {
            "request_delay": 3.0,
            "batch_days": 365,
            "batch_delay": 1800.0,
        })


if __name__ == "__main__":
    unittest.main()
