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
        import fetcher
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


if __name__ == "__main__":
    unittest.main()
