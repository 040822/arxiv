"""Benchmark storage & migration unit tests (isolated temp SQLite)."""

import json
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

from source.storage import connection as db_connection
from source.storage import init_db
from source.storage import benchmark as bench


def _freeze_case_fields(**overrides):
    fields = {
        "suite_id": 1,
        "paper_ref_id": 1,
        "track": "deep_reading",
        "position": 1,
        "kind": "q1",
        "question": "本文方法是什么？",
        "reference_answer": "FROB 方法",
        "evidence": ["FROB 是一种新方法"],
        "rubric": {"conditions": [{"text": "正确说明方法", "weight": 1.0}]},
        "requires_reject": False,
        "author_raw": {"generated": True},
    }
    fields.update(overrides)
    return fields


class BenchmarkStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db_path = db_connection.DB_PATH
        db_connection.DB_PATH = os.path.join(self.tmp.name, "papers.db")
        init_db()

    def tearDown(self):
        db_connection.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    def test_migration_v5_creates_all_benchmark_tables(self):
        conn = sqlite3.connect(db_connection.DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        finally:
            conn.close()
        expected = {
            "benchmark_routes", "benchmark_suites", "benchmark_suite_papers",
            "benchmark_cases", "benchmark_runs", "benchmark_candidates",
            "benchmark_responses", "benchmark_judgments",
        }
        self.assertTrue(expected.issubset(tables))

    def test_route_save_and_load_with_validation(self):
        saved = bench.save_benchmark_route("benchmark_author", {
            "provider_key": "p1", "model": "m1", "is_thinking": True,
            "thinking_effort": "high", "max_tokens": 8000,
        })
        self.assertEqual(saved["provider_key"], "p1")
        self.assertEqual(saved["model"], "m1")
        routes = bench.get_benchmark_routes()
        self.assertIn("benchmark_author", routes)
        with self.assertRaises(ValueError):
            bench.save_benchmark_route("not_a_route", {"model": "x"})

    def test_suite_paper_and_case_lifecycle(self):
        suite_id = bench.create_benchmark_suite(
            "pilot", "pprb-pilot-2026.08.13-r1",
            {"system": "s", "instruction": "### Q1: 方法"}, {"system": "s2", "instruction": "i"},
        )
        paper_ref = bench.add_benchmark_suite_paper(suite_id, {
            "paper_id": 7, "paper_key": "2608.00001", "title": "T",
            "authors": ["A"], "full_text": "FROB 是一种新方法，用于测试。",
            "text_chars": 20, "text_sha256": "abc",
        })
        duplicate = bench.add_benchmark_suite_paper(suite_id, {
            "paper_id": 7, "paper_key": "2608.00001", "title": "T",
        })
        self.assertEqual(paper_ref, duplicate)
        papers = bench.list_suite_papers(suite_id)
        self.assertEqual(len(papers), 1)

        case_id = bench.add_benchmark_case(_freeze_case_fields(
            suite_id=suite_id, paper_ref_id=paper_ref,
        ))
        bench.add_benchmark_case(_freeze_case_fields(
            suite_id=suite_id, paper_ref_id=paper_ref,
        ))  # 重复写入应跳过
        cases = bench.list_suite_cases(suite_id)
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["status"], "pending")

        updated = bench.set_case_review(case_id, "accepted", note="OK")
        self.assertEqual(updated["status"], "accepted")
        self.assertEqual(updated["review_note"], "OK")

        suite = bench.get_benchmark_suite(suite_id)
        self.assertEqual(suite["paper_count"], 1)
        self.assertEqual(suite["accepted_case_counts"].get("deep_reading"), 1)

    def test_run_candidates_responses_and_budget_count(self):
        suite_id = bench.create_benchmark_suite("pilot", "pprb-pilot-2026.08.13-r1",
                                                {"system": "s"}, {"system": "s2"})
        paper_ref = bench.add_benchmark_suite_paper(suite_id, {
            "paper_key": "2608.00001", "full_text": "text",
        })
        run_id = bench.create_benchmark_run(suite_id, repeats=1, max_calls=10, runner_version="t1")
        candidate_id = bench.add_benchmark_candidate(
            run_id, 1, "cand", {"provider_key": "p1", "provider_name": "P", "model": "m1"},
            "hash123", {"temperature": 0.2},
        )
        resp_id = bench.save_benchmark_response(
            run_id, candidate_id, paper_ref, "deep_reading", 0, None,
            [{"role": "user", "content": "hi"}], "raw", {"q1": "ans"}, "ok", "stop",
            {"total_tokens": 10}, 5.0, 0,
        )
        case_id = bench.add_benchmark_case(_freeze_case_fields(
            suite_id=suite_id, paper_ref_id=paper_ref,
        ))
        again = bench.save_benchmark_response(
            run_id, candidate_id, paper_ref, "deep_reading", 0, None,
            [{"role": "user", "content": "hi"}], "raw2", {"q1": "ans2"}, "ok", "stop",
            {"total_tokens": 10}, 5.0, 0,
        )
        self.assertEqual(resp_id, again)
        self.assertEqual(bench.get_run_responses(run_id)[0]["raw_output"], "raw")
        self.assertEqual(bench.count_run_calls(run_id), 1)
        self.assertEqual(bench.get_existing_response(candidate_id, paper_ref, "deep_reading", 0, None), resp_id)
        self.assertIsNone(bench.get_existing_response(candidate_id, paper_ref, "chat", 0, 1))

        bench.save_benchmark_judgment(
            run_id, resp_id, "primary", "benchmark_judge", "r1", case_id,
            [{"condition": "c", "met": 1.0}], 100.0, False, 0.9, {"x": 1},
        )
        bench.save_benchmark_judgment(
            run_id, resp_id, "primary", "benchmark_judge", "r1", case_id,
            [{"condition": "c", "met": 0.5}], 50.0, False, 0.8, {"x": 2},
        )
        judgments = bench.get_run_judgments(run_id)
        self.assertEqual(len(judgments), 1)
        self.assertEqual(judgments[0]["score"], 50.0)

    def test_candidate_call_reservation_is_atomic_and_never_exceeds_budget(self):
        suite_id = bench.create_benchmark_suite("pilot", "pprb-pilot-2026.08.13-r1",
                                                {"system": "s"}, {"system": "s2"})
        run_id = bench.create_benchmark_run(suite_id, max_calls=7)

        def reserve(_):
            return bench.consume_candidate_call(run_id)

        with ThreadPoolExecutor(max_workers=12) as executor:
            results = list(executor.map(reserve, range(32)))
        self.assertEqual(sum(results), 7)
        self.assertEqual(bench.get_benchmark_run(run_id)["candidate_calls_made"], 7)
        self.assertFalse(bench.consume_candidate_call(run_id))
        self.assertEqual(bench.get_benchmark_run(run_id)["candidate_calls_made"], 7)

    def test_actual_params_are_structured_and_credentials_are_not_persisted(self):
        suite_id = bench.create_benchmark_suite("pilot", "pprb-pilot-2026.08.13-r1",
                                                {"system": "s"}, {"system": "s2"})
        run_id = bench.create_benchmark_run(suite_id)
        bench.add_benchmark_candidate(
            run_id, 1, "cand",
            {"provider_key": "p1", "provider_name": "P", "model": "m1", "api_key": "sk-secret"},
            "hash123",
            {"model": "m1", "extra_body": {"thinking_budget": 8192},
             "reasoning_effort": "high", "max_completion_tokens": 321,
             "messages": [{"role": "user", "content": "secret"}], "api_key": "sk-secret"},
        )
        candidate = bench.list_run_candidates(run_id)[0]
        self.assertIsInstance(candidate["actual_params"], dict)
        self.assertEqual(candidate["actual_params"]["max_completion_tokens"], 321)
        self.assertNotIn("api_key", candidate["actual_params"])
        self.assertNotIn("messages", candidate["actual_params"])

    def test_response_derived_counter_includes_continuation_attempts(self):
        suite_id = bench.create_benchmark_suite("pilot", "pprb-pilot-2026.08.13-r1",
                                                {"system": "s"}, {"system": "s2"})
        paper_ref = bench.add_benchmark_suite_paper(suite_id, {"paper_key": "p", "full_text": "text"})
        run_id = bench.create_benchmark_run(suite_id)
        candidate_id = bench.add_benchmark_candidate(
            run_id, 1, "cand", {"provider_key": "p1", "model": "m1"}, "h", {},
        )
        bench.save_benchmark_response(
            run_id, candidate_id, paper_ref, "deep_reading", 0, None,
            [], "raw", {"q1": "a"}, "ok", "stop", {}, 0, continuation_count=2,
        )
        self.assertEqual(bench.count_run_calls(run_id), 3)

    def test_run_status_transitions_and_interrupt_marking(self):
        suite_id = bench.create_benchmark_suite("pilot", "pprb-pilot-2026.08.13-r1",
                                                {"system": "s"}, {"system": "s2"})
        run_id = bench.create_benchmark_run(suite_id)
        bench.set_run_status(run_id, "completed")
        bench.create_benchmark_run(suite_id)
        bench.mark_interrupted_runs()
        runs = {run["id"]: run for run in bench.list_benchmark_runs()}
        self.assertEqual(runs[run_id]["status"], "completed")
        running = [run for run in runs.values() if run["status"] == "running"]
        self.assertEqual(running, [])
        interrupted = [run for run in runs.values() if run["status"] == "interrupted"]
        self.assertEqual(len(interrupted), 1)

    def test_suite_update_fields(self):
        suite_id = bench.create_benchmark_suite("pilot", "pprb-pilot-2026.08.13-r1",
                                                {"system": "s"}, {"system": "s2"})
        bench.update_benchmark_suite(suite_id, status="frozen", suite_checksum="c1",
                                     scoring_revision={"label": "r1"})
        suite = bench.get_benchmark_suite(suite_id)
        self.assertEqual(suite["status"], "frozen")
        self.assertEqual(suite["suite_checksum"], "c1")
        self.assertEqual(suite["scoring_revision"]["label"], "r1")


if __name__ == "__main__":
    unittest.main()
