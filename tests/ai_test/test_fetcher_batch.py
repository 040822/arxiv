"""
test_fetcher_batch.py — 由 tests/test_ai_provider_config.py 拆分迁入（Q20），方法体逐字节一致。
"""

import unittest
import types
from datetime import datetime, timezone
from unittest.mock import patch

from .common import (
    install_import_stubs,
)


class FetchBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_import_stubs()
        import source.ingestion as fetcher
        cls.fetcher = fetcher

    def test_recent_fetch_does_not_skip_because_old_data_exists(self):
        calls = []
        progress_messages = []

        def fake_fetch_range(categories, start_date, end_date):
            calls.append((categories, start_date, end_date))
            return [{"arxiv_id": "2606.00001"}]

        with patch.object(self.fetcher, "get_fetch_config", return_value={
            "request_delay": 3,
            "batch_days": 30,
            "batch_delay": 0,
        }), patch.object(self.fetcher, "_fetch_date_range", side_effect=fake_fetch_range):
            papers = self.fetcher.fetch_batch(
                categories=["cs.RO"],
                total_days=1,
                batch_days=30,
                batch_delay=0,
                progress_callback=progress_messages.append,
            )

        self.assertEqual(papers, [{"arxiv_id": "2606.00001"}])
        self.assertEqual(len(calls), 1)
        self.assertNotIn("无需重复抓取", " ".join(m.get("message", "") for m in progress_messages))

    def test_fetch_errors_are_reported_instead_of_hidden_as_zero_results(self):
        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def results(self, search):
                raise RuntimeError("HTTP 429 Too Many Requests")

        fake_arxiv = types.SimpleNamespace(
            Client=FakeClient,
            Search=lambda **kwargs: kwargs,
            SortCriterion=types.SimpleNamespace(SubmittedDate="submitted"),
            SortOrder=types.SimpleNamespace(Descending="descending"),
        )

        with patch.object(self.fetcher, "arxiv", fake_arxiv), \
             patch.object(self.fetcher, "get_fetch_config", return_value={"request_delay": 3}), \
             patch.object(self.fetcher.logger, "error"):
            with self.assertRaisesRegex(RuntimeError, "429"):
                self.fetcher._fetch_date_range(
                    ["cs.RO"],
                    datetime(2026, 5, 28, tzinfo=timezone.utc),
                    datetime(2026, 6, 4, tzinfo=timezone.utc),
                )

    def test_date_range_uses_saved_request_delay_when_not_overridden(self):
        client_kwargs = []

        class FakeClient:
            def __init__(self, *args, **kwargs):
                client_kwargs.append(kwargs)

            def results(self, search):
                return []

        fake_arxiv = types.SimpleNamespace(
            Client=FakeClient,
            Search=lambda **kwargs: kwargs,
            SortCriterion=types.SimpleNamespace(SubmittedDate="submitted"),
            SortOrder=types.SimpleNamespace(Descending="descending"),
        )

        with patch.object(self.fetcher, "arxiv", fake_arxiv), \
             patch.object(self.fetcher, "get_fetch_config", return_value={"request_delay": 30}):
            self.fetcher._fetch_date_range(
                ["cs.RO"],
                datetime(2026, 5, 28, tzinfo=timezone.utc),
                datetime(2026, 6, 4, tzinfo=timezone.utc),
            )

        self.assertEqual(client_kwargs[0]["delay_seconds"], 30)

    def test_fetch_by_date_uses_utc_date_boundaries(self):
        with patch.object(self.fetcher, "_fetch_date_range", return_value=[]) as fetch_range:
            self.fetcher.fetch_by_date("2026-06-01", categories=["cs.RO"])

        _, start_date, end_date = fetch_range.call_args.args
        self.assertEqual(start_date, datetime(2026, 6, 1, tzinfo=timezone.utc))
        self.assertEqual(end_date, datetime(2026, 6, 2, tzinfo=timezone.utc))

    def test_fetch_latest_papers_defaults_to_one_day_window(self):
        with patch.object(self.fetcher, "get_fetch_config", return_value={
            "request_delay": 3, "batch_days": 30, "batch_delay": 0,
        }), patch.object(self.fetcher, "fetch_batch", return_value=[]) as fetch_batch:
            self.fetcher.fetch_latest_papers(categories=["cs.RO"])

        self.assertEqual(fetch_batch.call_args.kwargs["total_days"], 1)

    def test_fetch_latest_papers_uses_given_days_window(self):
        with patch.object(self.fetcher, "get_fetch_config", return_value={
            "request_delay": 3, "batch_days": 30, "batch_delay": 0,
        }), patch.object(self.fetcher, "fetch_batch", return_value=[]) as fetch_batch:
            self.fetcher.fetch_latest_papers(categories=["cs.RO"], days=5)

        self.assertEqual(fetch_batch.call_args.kwargs["total_days"], 5)

    def test_date_range_query_uses_submitted_date_filter(self):
        queries = []

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def results(self, search):
                queries.append(search["query"])
                return []

        fake_arxiv = types.SimpleNamespace(
            Client=FakeClient,
            Search=lambda **kwargs: kwargs,
            SortCriterion=types.SimpleNamespace(SubmittedDate="submitted"),
            SortOrder=types.SimpleNamespace(Descending="descending"),
        )

        with patch.object(self.fetcher, "arxiv", fake_arxiv), \
             patch.object(self.fetcher, "get_fetch_config", return_value={"request_delay": 3}):
            self.fetcher._fetch_date_range(
                ["cs.RO"],
                datetime(2026, 5, 28, tzinfo=timezone.utc),
                datetime(2026, 6, 4, tzinfo=timezone.utc),
            )

        self.assertIn("cat:cs.RO", queries[0])
        self.assertIn("submittedDate:[202605280000 TO 202606040000]", queries[0])

    def _fake_result(self, arxiv_id, published):
        return types.SimpleNamespace(
            entry_id=f"http://arxiv.org/abs/{arxiv_id}v1",
            published=published,
            updated=published,
            title=f"Title {arxiv_id}",
            summary="Abstract",
            authors=[types.SimpleNamespace(__str__=lambda self: "Author")],
            categories=[types.SimpleNamespace(__str__=lambda self: "cs.RO")],
            primary_category=types.SimpleNamespace(__str__=lambda self: "cs.RO"),
            pdf_url=f"https://arxiv.org/pdf/{arxiv_id}v1",
        )

    def test_date_range_keeps_only_window_papers_and_backfills_with_dedupe(self):
        window_start = datetime(2026, 6, 1, tzinfo=timezone.utc)
        window_end = datetime(2026, 6, 2, tzinfo=timezone.utc)
        # 顺序为最新在前：窗口外新论文 → 窗口内两篇 → 早于窗口的论文触发终止
        results = [
            self._fake_result("2606.00003", datetime(2026, 6, 2, 1, 0, tzinfo=timezone.utc)),
            self._fake_result("2606.00002", datetime(2026, 6, 1, 20, 0, tzinfo=timezone.utc)),
            self._fake_result("2606.00001", datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)),
            self._fake_result("2606.00000", datetime(2026, 5, 31, 23, 0, tzinfo=timezone.utc)),
        ]

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def results(self, search):
                return list(results)

        fake_arxiv = types.SimpleNamespace(
            Client=FakeClient,
            Search=lambda **kwargs: kwargs,
            SortCriterion=types.SimpleNamespace(SubmittedDate="submitted"),
            SortOrder=types.SimpleNamespace(Descending="descending"),
        )

        inserted = []

        def fake_insert(paper_data):
            inserted.append(paper_data["arxiv_id"])
            return 1

        with patch.object(self.fetcher, "arxiv", fake_arxiv), \
             patch.object(self.fetcher, "get_fetch_config", return_value={"request_delay": 3}), \
             patch.object(self.fetcher, "paper_exists", return_value=False), \
             patch.object(self.fetcher, "insert_paper", side_effect=fake_insert):
            papers = self.fetcher._fetch_date_range(["cs.RO"], window_start, window_end)

        # 窗口外新论文被跳过、早于窗口的触发终止；仅窗口内两篇入库
        self.assertEqual(inserted, ["2606.00002", "2606.00001"])
        self.assertEqual(len(papers), 2)

        # 第二次抓取：数据库已有 → paper_exists=True → 不重复入库（滚动窗口补抓语义）
        inserted.clear()
        with patch.object(self.fetcher, "arxiv", fake_arxiv), \
             patch.object(self.fetcher, "get_fetch_config", return_value={"request_delay": 3}), \
             patch.object(self.fetcher, "paper_exists", return_value=True), \
             patch.object(self.fetcher, "insert_paper", side_effect=fake_insert):
            papers = self.fetcher._fetch_date_range(["cs.RO"], window_start, window_end)

        self.assertEqual(inserted, [])
        self.assertEqual(papers, [])


if __name__ == "__main__":
    unittest.main()
