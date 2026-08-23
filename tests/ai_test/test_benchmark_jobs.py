"""RED tests for durable/background benchmark execution semantics."""

import json
import os
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

from source.storage import connection as db_connection
from source.storage import init_db
from source.settings import store as settings_store


class BenchmarkJobContractTests(unittest.TestCase):
    """These tests intentionally describe the worker-facing public contract.

    The fixture uses small fakes instead of a full suite so the first red
    artifact isolates job submission, retry classification, and calibration.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = db_connection.DB_PATH
        self.old_settings = settings_store.SETTINGS_PATH
        db_connection.DB_PATH = os.path.join(self.tmp.name, "papers.db")
        settings_store.SETTINGS_PATH = os.path.join(self.tmp.name, "settings.json")
        with open(settings_store.SETTINGS_PATH, "w", encoding="utf-8") as handle:
            json.dump({"settings_schema_version": 4, "providers": {}}, handle)
        init_db()

    def tearDown(self):
        db_connection.DB_PATH = self.old_db
        settings_store.SETTINGS_PATH = self.old_settings
        self.tmp.cleanup()

    def test_selectable_papers_unpacks_browse_result(self):
        import source.benchmark as bm

        with patch("source.storage.papers.browse_papers", return_value=([{
            "id": 1, "paper_key": "p1", "title": "Title", "published_date": "2026-01-01",
        }], 1)):
            self.assertEqual(bm.list_selectable_papers(), [{
                "id": 1, "paper_key": "p1", "title": "Title", "published_date": "2026-01-01",
            }])

    def test_transient_model_failure_retries_twice_then_reports_retry_failed(self):
        from source.benchmark import _ai

        class TooManyRequests(Exception):
            status_code = 429

        calls = []

        def fail(*args, **kwargs):
            calls.append(1)
            raise TooManyRequests("busy")

        with patch.object(_ai, "call_model", side_effect=fail), patch.object(_ai.time, "sleep") as sleep:
            with self.assertRaises(_ai.RetryFailed) as ctx:
                _ai.call_model_with_retries({}, [], "benchmark_test")
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleep.call_args_list, [unittest.mock.call(1), unittest.mock.call(2)])
        self.assertEqual(ctx.exception.retry_count, 2)

    def test_context_window_errors_are_narrow_and_candidate_calls_persist_capability_result(self):
        from source.benchmark import _ai, runner

        class BadRequest(Exception):
            status_code = 400

        context = BadRequest("context_length_exceeded: maximum context length is 128k tokens")
        token_context = BadRequest("token-limit exceeded")
        auth = BadRequest("invalid api key")
        self.assertTrue(_ai.is_context_window_error(context))
        self.assertTrue(_ai.is_context_window_error(token_context))
        self.assertFalse(_ai.is_context_window_error(auth))

        with patch.object(runner, "call_model_with_retries", side_effect=_ai.ContextWindowError(
            "context window exceeded", cause=context,
        )):
            result, calls = runner.run_chat_round(
                {"paper_chat_prompt": {"system": "", "instruction": ""}},
                {"title": "T", "full_text": "text"}, {}, {}, [], [],
            )
        self.assertEqual(calls, 1)
        self.assertEqual(result["status"], "context_error")
        self.assertEqual(result["parsed"], {})
        self.assertNotIn("api_key", json.dumps(result["error_json"]))

    def test_context_window_deep_reading_is_saved_as_zero_output(self):
        from source.benchmark import _ai, runner

        context = RuntimeError("maximum context length exceeded")
        context.status_code = 400
        with patch.object(runner, "call_model_with_retries", side_effect=_ai.ContextWindowError(
            "context window exceeded", cause=context,
        )):
            result, calls = runner.run_deep_reading(
                {
                    "deep_reading_prompt": {
                        "system": "",
                        "instruction": "### Q1: Explain the method",
                    },
                },
                {"title": "T", "full_text": "text"}, {}, {},
            )
        self.assertEqual(calls, 1)
        self.assertEqual(result["status"], "context_error")
        self.assertEqual(result["parsed"], {})
        self.assertEqual(result["raw_output"], "")
        self.assertEqual(result["error_json"]["category"], "context_window")

    def test_auth_and_unrelated_bad_requests_still_fail_without_retry(self):
        from source.benchmark import _ai

        class BadRequest(Exception):
            status_code = 400

        with patch.object(_ai, "call_model", side_effect=BadRequest("invalid request")):
            with self.assertRaises(BadRequest):
                _ai.call_model_with_retries({}, [], "benchmark_test")

    def test_review_sampling_expands_for_anomalies_on_both_tracks(self):
        from source.benchmark import judge

        groups = []
        for track, offset in (("deep_reading", 0), ("chat", 100)):
            for repeat_index in range(10):
                response_id = offset + repeat_index + 1
                status = "format_error" if repeat_index == 0 else "ok"
                groups.append({
                    "candidate_id": 1,
                    "candidate_provider_key": "candidate-provider",
                    "track": track,
                    "repeat_index": repeat_index,
                    "responses": [{"id": response_id, "status": status}],
                    "response_ids": {
                        "q1" if track == "deep_reading" else "chat_round1": response_id,
                    },
                })

        sampled = judge.review_sampling(groups, [])
        self.assertEqual(len(sampled), 4)
        self.assertEqual(
            {(group["track"], group["repeat_index"]) for group in sampled},
            {
                ("deep_reading", 0), ("deep_reading", 1),
                ("chat", 0), ("chat", 1),
            },
        )

    def test_background_submission_returns_task_and_run_without_waiting(self):
        import source.benchmark as bm

        gate = threading.Event()
        finished = threading.Event()
        with patch.object(bm, "_prepare_run", return_value=17), patch.object(
            bm, "_execute_run", side_effect=lambda *args, **kwargs: (gate.wait(2), finished.set())
        ), patch("source.storage.benchmark.claim_benchmark_run", return_value=True):
            submitted = bm.submit_start_run(1, [{"label": "x", "config": {}}])
            self.assertEqual(submitted["run_id"], 17)
            self.assertTrue(submitted["task_id"])
            self.assertIn(submitted["status"], {"queued", "running"})
            self.assertFalse(finished.is_set())
            gate.set()
            self.assertTrue(finished.wait(2))
            deadline = time.time() + 2
            while time.time() < deadline:
                if bm.get_task(submitted["task_id"])["status"] == "completed":
                    break
                time.sleep(0.01)
            self.assertEqual(bm.get_task(submitted["task_id"])["status"], "completed")

    def test_human_condition_scores_are_weighted_and_validate_ownership(self):
        import source.benchmark.judge as judge

        case = {"rubric": {"conditions": [
            {"text": "核心", "weight": 3},
            {"text": "次要", "weight": 1},
        ]}}
        result = judge._score_for_case(case, {"conditions": [
            {"condition": "核心", "met": 1},
            {"condition": "次要", "met": 0.5},
        ]})
        self.assertEqual(result["score"], 87.5)
        with self.assertRaises(ValueError):
            judge._validate_human_conditions(case, [{"condition": "核心", "met": 1}])

    def test_calibration_uses_active_revision_and_only_same_named_conditions(self):
        import source.benchmark.judge as judge

        judgments = [
            {"response_id": 1, "case_id": 1, "judge_role": "primary",
             "scoring_revision": "old", "score": 0,
             "condition_scores": [{"condition": "核心", "met": 0}]},
            {"response_id": 1, "case_id": 1, "judge_role": "human",
             "scoring_revision": "old", "score": 100,
             "condition_scores": [{"condition": "核心", "met": 1}]},
            {"response_id": 2, "case_id": 2, "judge_role": "primary",
             "scoring_revision": "new", "score": 100,
             "condition_scores": [
                 {"condition": "核心", "met": 1},
                 {"condition": "主裁判独有", "met": 1},
             ]},
            {"response_id": 2, "case_id": 2, "judge_role": "human",
             "scoring_revision": "new", "score": 50,
             "condition_scores": [
                 {"condition": "核心", "met": 0.5},
                 {"condition": "人工独有", "met": 1},
             ]},
        ]
        with patch("source.storage.benchmark.get_run_judgments", return_value=judgments):
            result = judge.calibrate({"id": 1, "active_scoring_revision": "new"})
        self.assertEqual(result["compared"], 1)
        self.assertEqual(result["condition_agreement"], 0.0)
        self.assertEqual(result["mean_score_error"], 50.0)

    def test_historical_empty_conditions_do_not_fake_condition_agreement(self):
        import source.benchmark.judge as judge

        judgments = [
            {"response_id": 1, "case_id": 1, "judge_role": "primary",
             "scoring_revision": "old", "score": 100, "condition_scores": []},
            {"response_id": 1, "case_id": 1, "judge_role": "human",
             "scoring_revision": "old", "score": 50, "condition_scores": []},
        ]
        with patch("source.storage.benchmark.get_run_judgments", return_value=judgments):
            result = judge.calibrate({"id": 1, "active_scoring_revision": ""})
        self.assertEqual(result["compared"], 1)
        self.assertIsNone(result["condition_agreement"])
        self.assertEqual(result["mean_score_error"], 50.0)

    def test_report_collect_excludes_human_judgment_from_old_revision(self):
        from source.benchmark import report

        judgments = [
            {"response_id": 1, "case_id": 1, "judge_role": "human",
             "scoring_revision": "old", "score": 99},
            {"response_id": 1, "case_id": 1, "judge_role": "primary",
             "scoring_revision": "new", "score": 60},
        ]
        with patch("source.storage.benchmark.get_run_judgments", return_value=judgments):
            collected = report._collect({"id": 1, "active_scoring_revision": "new"})
        self.assertNotIn("human", collected[(1, 1)])
        self.assertEqual(collected[(1, 1)]["primary"]["score"], 60)


if __name__ == "__main__":
    unittest.main()
