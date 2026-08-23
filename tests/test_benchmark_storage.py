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

    def test_storage_case_writes_do_not_enforce_frozen_business_rule(self):
        suite_id = bench.create_benchmark_suite(
            "pilot", "pprb-pilot-2026.08.13-r1", {"system": "s"}, {"system": "s2"},
        )
        paper_ref = bench.add_benchmark_suite_paper(
            suite_id, {"paper_key": "paper-1", "full_text": "text"},
        )
        case_id = bench.add_benchmark_case(_freeze_case_fields(
            suite_id=suite_id, paper_ref_id=paper_ref,
        ))
        bench.update_benchmark_suite(suite_id, status="frozen")

        updated = bench.update_benchmark_case(
            case_id, {"question": "编辑后的问题"}, note="storage test",
        )
        self.assertEqual(updated["question"], "编辑后的问题")
        self.assertEqual(updated["status"], "pending")
        reviewed = bench.set_case_review(case_id, "accepted", note="storage test")
        self.assertEqual(reviewed["status"], "accepted")

        bench.set_cases_review(suite_id, "rejected", note="storage test")
        self.assertEqual(bench.get_benchmark_case(case_id)["status"], "rejected")

    def test_replace_retry_failed_response_requires_same_run_and_status(self):
        suite_id = bench.create_benchmark_suite(
            "pilot", "pprb-pilot-2026.08.13-r1", {"system": "s"}, {"system": "s2"},
        )
        paper_ref = bench.add_benchmark_suite_paper(
            suite_id, {"paper_key": "paper-1", "full_text": "text"},
        )
        run_id = bench.create_benchmark_run(suite_id)
        other_run_id = bench.create_benchmark_run(suite_id)
        candidate_id = bench.add_benchmark_candidate(
            run_id, 1, "candidate", {"provider_key": "p", "model": "m"}, "hash", {},
        )
        response_id = bench.save_benchmark_response(
            run_id, candidate_id, paper_ref, "deep_reading", 0, None,
            [{"role": "user", "content": "retry"}], "partial", {}, "retry_failed", "error",
            {"retry_count": 2}, 30.0, continuation_count=0, retry_count=2,
            error_json={"timeout": True},
        )
        replacement = {
            "prompt_snapshot": [{"role": "user", "content": "retry"}],
            "raw_output": "complete",
            "parsed": {"q1": "answer"},
            "status": "ok",
            "finish_reason": "stop",
            "usage_json": {"total_tokens": 42},
            "latency_ms": 12.5,
            "continuation_count": 1,
            "retry_count": 0,
            "error_json": {},
        }

        with self.assertRaises(ValueError):
            bench.replace_retry_failed_response(response_id, other_run_id, replacement)
        self.assertEqual(
            bench.replace_retry_failed_response(response_id, run_id, replacement), response_id,
        )
        updated = bench.get_response(response_id)
        self.assertEqual(updated["status"], "ok")
        self.assertEqual(updated["raw_output"], "complete")
        self.assertEqual(updated["usage_json"], {"total_tokens": 42})
        self.assertEqual(updated["continuation_count"], 1)
        self.assertEqual(updated["retry_count"], 0)
        self.assertEqual(updated["error_json"], {})
        with self.assertRaises(ValueError):
            bench.replace_retry_failed_response(response_id, run_id, replacement)

    def test_clone_reusable_response_resets_target_cost_metadata(self):
        suite_id = bench.create_benchmark_suite(
            "pilot", "pprb-pilot-2026.08.13-r1", {"system": "s"}, {"system": "s2"},
            checksum="suite-hash",
        )
        source_paper = bench.add_benchmark_suite_paper(
            suite_id, {"paper_key": "paper-1", "full_text": "text"},
        )
        source_run = bench.create_benchmark_run(suite_id, runner_version="runner-1")
        source_candidate = bench.add_benchmark_candidate(
            source_run, 1, "source", {"provider_key": "p", "model": "m"},
            "candidate-hash", {"model": "m"},
        )
        source_response = bench.save_benchmark_response(
            source_run, source_candidate, source_paper, "deep_reading", 0, None,
            [{"role": "user", "content": "paper"}], "answer", {"q1": "answer"}, "ok", "stop",
            {"total_tokens": 999}, 123.5, continuation_count=2, retry_count=1,
            error_json={"source": "none"},
        )
        self.assertEqual(bench.count_run_calls(source_run), 4)

        target_run = bench.create_benchmark_run(suite_id, runner_version="runner-1")
        target_candidate = bench.add_benchmark_candidate(
            target_run, 1, "target", {"provider_key": "p", "model": "m"},
            "candidate-hash", {"model": "m"},
        )
        cloned = bench.clone_reusable_response(
            source_response, target_run, target_candidate, source_paper,
        )
        cloned_data = bench.get_response(cloned)
        self.assertEqual(cloned_data["prompt_snapshot"], [{"role": "user", "content": "paper"}])
        self.assertEqual(cloned_data["raw_output"], "answer")
        self.assertEqual(cloned_data["parsed"], {"q1": "answer"})
        self.assertEqual(cloned_data["status"], "ok")
        self.assertEqual(cloned_data["reused_from_response_id"], source_response)
        self.assertEqual(cloned_data["usage_json"], {})
        self.assertEqual(cloned_data["latency_ms"], 0)
        self.assertEqual(cloned_data["continuation_count"], 0)
        self.assertEqual(cloned_data["retry_count"], 0)
        self.assertEqual(cloned_data["error_json"], {})
        self.assertEqual(bench.count_run_calls(target_run), 0)

    def test_v7_run_queue_claim_and_terminal_transitions_are_idempotent(self):
        suite_id = bench.create_benchmark_suite("pilot", "pprb-pilot-2026.08.13-r1",
                                                {"system": "s"}, {"system": "s2"})
        run_id = bench.create_benchmark_run(suite_id, status="queued")
        self.assertEqual(bench.get_benchmark_run(run_id)["status"], "queued")
        self.assertTrue(bench.claim_benchmark_run(run_id))
        self.assertFalse(bench.claim_benchmark_run(run_id))
        self.assertEqual(bench.get_benchmark_run(run_id)["status"], "running")

        bench.set_run_status(run_id, "completed")
        bench.set_run_status(run_id, "completed")
        completed = bench.get_benchmark_run(run_id)
        self.assertEqual(completed["status"], "completed")
        self.assertIsNotNone(completed["finished_at"])
        self.assertFalse(bench.claim_benchmark_run(run_id))

    def test_v7_interrupt_marks_queued_and_running_only(self):
        suite_id = bench.create_benchmark_suite("pilot", "pprb-pilot-2026.08.13-r1",
                                                {"system": "s"}, {"system": "s2"})
        queued_id = bench.create_benchmark_run(suite_id, status="queued")
        running_id = bench.create_benchmark_run(suite_id, status="running")
        completed_id = bench.create_benchmark_run(suite_id, status="completed")

        self.assertEqual(bench.mark_interrupted_runs(), 2)
        runs = {run["id"]: run for run in bench.list_benchmark_runs()}
        self.assertEqual(runs[queued_id]["status"], "interrupted")
        self.assertEqual(runs[running_id]["status"], "interrupted")
        self.assertEqual(runs[completed_id]["status"], "completed")
        self.assertTrue(runs[queued_id]["finished_at"])
        self.assertTrue(runs[running_id]["finished_at"])

    def test_v7_response_reuse_matches_exact_slot_and_clone_does_not_charge(self):
        suite_one = bench.create_benchmark_suite(
            "pilot", "pprb-pilot-2026.08.13-r1", {"system": "s"}, {"system": "s2"},
            checksum="suite-hash",
        )
        source_paper = bench.add_benchmark_suite_paper(
            suite_one, {"paper_key": "paper-1", "full_text": "text"},
        )
        source_run = bench.create_benchmark_run(suite_one, runner_version="runner-1")
        source_candidate = bench.add_benchmark_candidate(
            source_run, 1, "source", {"provider_key": "p", "model": "m"},
            "candidate-hash", {"model": "m"},
        )
        source_response = bench.save_benchmark_response(
            source_run, source_candidate, source_paper, "deep_reading", 0, None,
            [], "answer", {"q1": "answer"}, "ok", "stop", {}, 1,
            retry_count=1, error_json={"transient": False},
        )
        self.assertEqual(bench.count_run_calls(source_run), 2)

        suite_two = bench.create_benchmark_suite(
            "pilot", "pprb-pilot-2026.08.13-r2", {"system": "s"}, {"system": "s2"},
            checksum="suite-hash",
        )
        target_paper = bench.add_benchmark_suite_paper(
            suite_two, {"paper_key": "paper-1", "full_text": "text"},
        )
        target_run = bench.create_benchmark_run(suite_two, runner_version="runner-1")
        target_candidate = bench.add_benchmark_candidate(
            target_run, 1, "target", {"provider_key": "p", "model": "m"},
            "candidate-hash", {"model": "m"},
        )

        reusable = bench.find_reusable_response(
            "suite-hash", "runner-1", "candidate-hash", paper_key="paper-1",
            track="deep_reading", repeat_index=0, round_index=None,
        )
        self.assertEqual(reusable["id"], source_response)
        cloned = bench.clone_reusable_response(
            source_response, target_run, target_candidate, target_paper,
        )
        self.assertEqual(bench.count_run_calls(target_run), 0)
        cloned_data = bench.get_response(cloned)
        self.assertEqual(cloned_data["reused_from_response_id"], source_response)
        self.assertEqual(cloned_data["raw_output"], "answer")
        self.assertEqual(cloned_data["usage_json"], {})
        self.assertEqual(cloned_data["latency_ms"], 0)
        self.assertEqual(cloned_data["continuation_count"], 0)
        self.assertEqual(cloned_data["retry_count"], 0)
        self.assertEqual(cloned_data["error_json"], {})

        failed_run = bench.create_benchmark_run(suite_one, runner_version="runner-1")
        failed_candidate = bench.add_benchmark_candidate(
            failed_run, 1, "failed", {"provider_key": "p", "model": "m"},
            "candidate-hash", {"model": "m"},
        )
        bench.save_benchmark_response(
            failed_run, failed_candidate, source_paper, "deep_reading", 0, None,
            [], "failed", {}, "retry_failed", "", {}, 0,
            retry_count=2, error_json={"timeout": True},
        )
        reusable_after_failure = bench.find_reusable_response(
            "suite-hash", "runner-1", "candidate-hash", paper_key="paper-1",
            track="deep_reading", repeat_index=0, round_index=None,
        )
        self.assertIn(reusable_after_failure["id"], {source_response, cloned})
        self.assertNotEqual(reusable_after_failure["status"], "retry_failed")

    def test_v7_scoring_revision_activation_case_audit_and_judgment_actor(self):
        suite_id = bench.create_benchmark_suite("pilot", "pprb-pilot-2026.08.13-r1",
                                                {"system": "s"}, {"system": "s2"})
        paper_ref = bench.add_benchmark_suite_paper(
            suite_id, {"paper_key": "paper-1", "full_text": "text"},
        )
        run_id = bench.create_benchmark_run(suite_id)
        revision = bench.create_benchmark_scoring_revision(
            run_id, "judge-hash", primary_route_key="benchmark_judge",
            primary_route_snapshot={"model": "judge"},
            primary_prompt_snapshot={"instruction": "score"},
        )
        self.assertEqual(revision["primary_route_snapshot"]["model"], "judge")
        self.assertEqual(
            bench.create_scoring_revision(run_id, "judge-hash")["id"], revision["id"],
        )
        bench.set_benchmark_scoring_revision_status(revision["id"], "running")
        completed = bench.set_scoring_revision_status(revision["id"], "completed")
        self.assertIsNotNone(completed["finished_at"])
        run = bench.set_active_scoring_revision(run_id, "judge-hash")
        self.assertEqual(run["active_scoring_revision"], "judge-hash")

        case_id = bench.add_benchmark_case(_freeze_case_fields(
            suite_id=suite_id, paper_ref_id=paper_ref,
        ))
        first_revision = bench.append_benchmark_case_revision(
            case_id, "edit", {"question": "old"}, {"question": "new"},
            actor_user_id=7, actor_username="reviewer", note="修订",
        )
        self.assertEqual(first_revision["actor_username"], "reviewer")
        reviewed = bench.set_case_review(
            case_id, "accepted", note="通过", actor_user_id=7, actor_username="reviewer",
        )
        self.assertEqual(reviewed["status"], "accepted")
        revisions = bench.list_case_revisions(case_id)
        self.assertEqual([item["revision"] for item in revisions], [1, 2])
        self.assertEqual(revisions[1]["after_json"]["status"], "accepted")

        candidate_id = bench.add_benchmark_candidate(
            run_id, 1, "candidate", {"provider_key": "p", "model": "m"},
            "hash", {"model": "m"},
        )
        response_id = bench.save_benchmark_response(
            run_id, candidate_id, paper_ref, "deep_reading", 0, None,
            [], "answer", {"q1": "answer"}, "ok", "stop", {}, 0,
        )
        bench.save_benchmark_judgment(
            run_id, response_id, "human", "human", "judge-hash", case_id,
            [{"condition": "c", "met": 1}], 100, False, 1,
            {"source": "ui"}, actor_user_id=7, actor_username="reviewer",
        )
        judgment = bench.get_run_judgments(run_id)[0]
        self.assertEqual(judgment["actor_user_id"], 7)
        self.assertEqual(judgment["actor_username"], "reviewer")


if __name__ == "__main__":
    unittest.main()
