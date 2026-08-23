"""Benchmark web API handler tests (FakeRequest + patched services).

自管理 Flask app context（不复用共享 web test base 单例，
避免与其它 Web 测试类在随机顺序下互相干扰）。
"""

import unittest
import importlib
import types
from pathlib import Path
from unittest.mock import patch

from .common import FakeRequest, _plain_jsonify, install_import_stubs


web_benchmark_api = None


class BenchmarkWebApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_import_stubs()
        global web_benchmark_api
        web_benchmark_api = importlib.import_module("source.web.benchmark_api")
        cls.web_benchmark_api = web_benchmark_api
        from source.web.application import app
        cls._app_context = app.app_context()
        cls._app_context.push()
        cls._original_jsonify = web_benchmark_api.jsonify
        web_benchmark_api.jsonify = _plain_jsonify

    @classmethod
    def tearDownClass(cls):
        web_benchmark_api.jsonify = cls._original_jsonify
        cls._app_context.pop()

    def setUp(self):
        self.web = self.web_benchmark_api

    def test_suite_list_endpoint_returns_suites(self):
        with patch.object(self.web, "list_suites", return_value=[{"id": 1}]):
            result = self.web.api_benchmark_suites()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["suites"], [{"id": 1}])
        self.assertIn("message", result)

    def test_create_draft_validates_input(self):
        self.web.request = FakeRequest({"subset_name": "pilot", "paper_keys": ["a", "b"]})
        with patch.object(self.web, "create_draft", return_value=(
            {"id": 9, "version_label": "pprb-pilot-2026.08.13-r1", "paper_count": 2}, [],
        )):
            result = self.web.api_benchmark_create_draft()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["suite"]["id"], 9)

    def test_create_draft_rejects_empty_paper_keys(self):
        self.web.request = FakeRequest({"subset_name": "pilot", "paper_keys": []})
        result, status = self.web.api_benchmark_create_draft()
        self.assertEqual(status, 400)
        self.assertIn("论文", result["message"])

    def test_generate_cases_reports_counts_and_errors(self):
        self.web.request = FakeRequest({})
        with patch.object(self.web, "current_user", return_value={"id": 9, "username": "admin"}), \
             patch.object(self.web, "submit_generate_cases", return_value={
                 "task_id": "core-author-1", "status": "queued",
             }):
            result = self.web.api_benchmark_generate_cases(3)
        payload, status = result
        self.assertEqual(status, 202)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["task_status"], "queued")
        self.assertTrue(payload["task_id"].startswith("benchmark-generate-3-"))
        self.assertEqual(payload["job_task_id"], "core-author-1")
        self.assertIn("message", payload)

    def test_generate_cases_failure_is_propagated(self):
        self.web.request = FakeRequest({})
        from source.benchmark import BenchmarkError
        with patch.object(self.web, "current_user", return_value={"id": 9, "username": "admin"}), \
             patch.object(self.web, "submit_generate_cases", side_effect=BenchmarkError("题库已冻结")):
            result, status = self.web.api_benchmark_generate_cases(3)
        self.assertEqual(status, 400)
        self.assertIn("冻结", result["message"])

    def test_queued_response_uses_ok_envelope_and_task_status(self):
        with patch.object(self.web, "get_task", return_value=None), \
             patch.object(self.web, "update_progress") as update_progress:
            result = self.web._queue_response(
                {"task_id": "core-1", "status": "queued"},
                "web-1", 9, "任务已排队",
            )
        payload, status = result
        self.assertEqual(status, 202)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["task_status"], "queued")
        self.assertEqual(update_progress.call_args.args[1]["status"], "queued")

    def test_start_run_requires_candidates(self):
        self.web.request = FakeRequest({"candidates": []})
        result, status = self.web.api_benchmark_start_run(1)
        self.assertEqual(status, 400)
        self.assertIn("候选", result["message"])

    def test_start_run_passes_config_and_budget(self):
        payload = {
            "candidates": [{"label": "c1", "config": {"provider_key": "p", "model": "m", "max_tokens": 999}}],
            "repeats": 2,
            "max_calls": 50,
        }
        self.web.request = FakeRequest(payload)
        with patch.object(self.web, "current_user", return_value={"id": 9, "username": "admin"}), \
             patch.object(self.web, "submit_start_run", return_value={
                 "task_id": "core-run-1", "run_id": 7, "status": "queued",
             }) as submit_start_run:
            result = self.web.api_benchmark_start_run(1)
        payload, status = result
        self.assertEqual(status, 202)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["task_status"], "queued")
        self.assertEqual(payload["run_id"], 7)
        submit_start_run.assert_called_once()
        args = submit_start_run.call_args
        self.assertEqual(args.args[0], 1)
        self.assertEqual(args.kwargs["repeats"], 2)
        self.assertEqual(args.kwargs["max_calls"], 50)
        candidate = args.args[1][0]
        self.assertEqual(candidate["config"]["max_tokens"], 999)

    def test_start_run_returns_clear_startup_benchmark_error(self):
        payload = {
            "candidates": [{"label": "c1", "config": {"provider_key": "p", "model": "m"}}],
            "max_calls": 1,
        }
        self.web.request = FakeRequest(payload)
        from source.benchmark import BenchmarkError
        with patch.object(self.web, "current_user", return_value={"id": 9, "username": "admin"}), \
             patch.object(self.web, "submit_start_run", side_effect=BenchmarkError("候选调用预算不足：至少需要 4 次，当前仅 1 次")):
            result, status = self.web.api_benchmark_start_run(1)
        self.assertEqual(status, 400)
        self.assertIn("候选调用预算不足", result["message"])

    def test_resume_run_passes_higher_max_calls(self):
        self.web.request = FakeRequest({"max_calls": 120})
        with patch.object(self.web, "current_user", return_value={"id": 9, "username": "admin"}), \
             patch.object(self.web, "submit_resume_run", return_value={
                 "task_id": "core-resume-1", "run_id": 7, "status": "queued",
             }) as submit_resume_run:
            result = self.web.api_benchmark_resume_run(7)
        payload, status = result
        self.assertEqual(status, 202)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["task_status"], "queued")
        submit_resume_run.assert_called_once()
        self.assertEqual(submit_resume_run.call_args.args[0], 7)
        self.assertEqual(submit_resume_run.call_args.kwargs["max_calls"], 120)

    def test_resume_run_returns_clear_benchmark_error(self):
        self.web.request = FakeRequest({"max_calls": 80})
        from source.benchmark import BenchmarkError
        with patch.object(self.web, "current_user", return_value={"id": 9, "username": "admin"}), \
             patch.object(self.web, "submit_resume_run", side_effect=BenchmarkError("候选调用预算已耗尽；恢复前必须严格上调 max_calls")):
            result, status = self.web.api_benchmark_resume_run(7)
        self.assertEqual(status, 400)
        self.assertIn("严格上调 max_calls", result["message"])

    def test_human_judgment_requires_complete_condition_scores(self):
        self.web.request = FakeRequest({"run_id": 1, "response_id": 2, "case_id": 3, "notes": "abc"})
        with patch.object(self.web, "current_user", return_value={"id": 9, "username": "admin"}), \
             patch.object(self.web, "set_human_judgment"):
            result, status = self.web.api_benchmark_human_judgment()
        self.assertEqual(status, 400)

    def test_human_judgment_uses_authenticated_actor_and_condition_scores(self):
        payload = {
            "run_id": 1, "response_id": 2, "case_id": 3,
            "condition_scores": [
                {"condition": "c1", "met": 1},
                {"condition": "c2", "met": 0.5},
            ], "notes": "reviewed",
            "actor_user_id": 999, "actor_username": "attacker",
        }
        self.web.request = FakeRequest(payload)
        with patch.object(self.web, "current_user", return_value={"id": 9, "username": "admin"}), \
             patch.object(self.web, "set_human_judgment") as set_judgment:
            result = self.web.api_benchmark_human_judgment()
        self.assertEqual(result["status"], "ok")
        kwargs = set_judgment.call_args.kwargs
        self.assertEqual(kwargs["actor_user_id"], 9)
        self.assertEqual(kwargs["actor_username"], "admin")
        self.assertEqual(set_judgment.call_args.args[3], payload["condition_scores"])

    def test_case_review_accepts_edits_and_authenticated_actor(self):
        self.web.request = FakeRequest({
            "decision": "accept", "note": "fixed",
            "edits": {"question": "new question", "evidence": ["exact text"]},
        })
        with patch.object(self.web, "current_user", return_value={"id": 9, "username": "admin"}), \
             patch.object(self.web, "edit_case", return_value={"id": 3, "status": "accepted"}) as edit_case:
            result = self.web.api_benchmark_review_case(3)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(edit_case.call_args.args[0], 3)
        self.assertEqual(edit_case.call_args.args[1]["question"], "new question")
        self.assertEqual(edit_case.call_args.kwargs["actor_user_id"], 9)
        self.assertEqual(edit_case.call_args.kwargs["actor_username"], "admin")

    def test_rejudge_returns_accepted_with_task_identifiers(self):
        self.web.request = FakeRequest({})
        with patch.object(self.web, "current_user", return_value={"id": 9, "username": "admin"}), \
             patch.object(self.web, "submit_rejudge_run", return_value={
                 "task_id": "core-rejudge-1", "run_id": 7, "status": "queued",
             }) as submit_rejudge:
            result = self.web.api_benchmark_rejudge_run(7)
        payload, status = result
        self.assertEqual(status, 202)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["task_status"], "queued")
        self.assertTrue(payload["task_id"].startswith("benchmark-rejudge-7-"))
        self.assertEqual(payload["job_task_id"], "core-rejudge-1")
        submit_rejudge.assert_called_once()

    def test_papers_storage_failure_is_safe_json(self):
        self.web.request = FakeRequest({}, method="GET")
        with patch.object(self.web, "list_selectable_papers", side_effect=RuntimeError("secret sqlite path")):
            result, status = self.web.api_benchmark_papers()
        self.assertEqual(status, 500)
        self.assertEqual(result["status"], "error")
        self.assertIn("论文列表", result["message"])
        self.assertNotIn("secret sqlite path", result["message"])

    def test_progress_terminates_when_core_task_fails(self):
        self.web.request = FakeRequest({}, method="GET")
        with patch.object(self.web, "current_user", return_value={"id": 9, "username": "admin"}), \
             patch.object(self.web, "get_progress", return_value={
                 "status": "running", "job_task_id": "core-1", "message": "running",
             }), \
             patch.object(self.web, "get_task", return_value={
                 "status": "error", "error": "provider down",
             }):
            response = self.web.api_benchmark_progress("web-1")
            chunks = list(response.response)
        self.assertEqual(len(chunks), 1)
        self.assertIn('"status": "error"', chunks[0])
        self.assertIn("后台任务失败", chunks[0])
        self.assertNotIn("provider down", chunks[0])

    def _assert_unexpected_exception_is_safe_json(self, result):
        self.assertIsInstance(result, tuple)
        payload, status = result
        self.assertEqual(status, 500)
        self.assertEqual(payload["status"], "error")
        self.assertIn("message", payload)
        self.assertNotIn("secret provider error", payload["message"])

    def test_unexpected_exceptions_from_json_endpoints_are_safe(self):
        self.web.request = FakeRequest({})
        cases = [
            ("api_benchmark_create_draft", ()),
            ("api_benchmark_suite", (1,)),
            ("api_benchmark_review_case", (1,)),
            ("api_benchmark_review_all", (1,)),
            ("api_benchmark_freeze", (1,)),
            ("api_benchmark_estimate", (1,)),
            ("api_benchmark_run", (1,)),
            ("api_benchmark_report", (1,)),
            ("api_benchmark_save_route", ("benchmark_author",)),
        ]
        patches = [
            patch.object(self.web, "create_draft", side_effect=RuntimeError("secret provider error")),
            patch.object(self.web, "get_suite", side_effect=RuntimeError("secret provider error")),
            patch.object(self.web, "review_case", side_effect=RuntimeError("secret provider error")),
            patch.object(self.web, "review_all_cases", side_effect=RuntimeError("secret provider error")),
            patch.object(self.web, "freeze_suite", side_effect=RuntimeError("secret provider error")),
            patch.object(self.web, "estimate_calls", side_effect=RuntimeError("secret provider error")),
            patch.object(self.web, "get_run", side_effect=RuntimeError("secret provider error")),
            patch.object(self.web, "get_report", side_effect=RuntimeError("secret provider error")),
            patch.object(self.web, "save_route_config", side_effect=RuntimeError("secret provider error")),
        ]
        entered = []
        try:
            for service_patch in patches:
                entered.append(service_patch.__enter__())
            for endpoint_name, args in cases:
                with self.subTest(endpoint=endpoint_name):
                    self._assert_unexpected_exception_is_safe_json(
                        getattr(self.web, endpoint_name)(*args)
                    )
        finally:
            for service_patch in reversed(patches):
                service_patch.__exit__(None, None, None)

    def test_routes_unexpected_exception_is_safe_json(self):
        with patch.object(self.web, "get_all_providers", side_effect=RuntimeError("secret provider error")):
            self._assert_unexpected_exception_is_safe_json(self.web.api_benchmark_routes())

    def test_human_judgment_unexpected_exception_is_safe_json(self):
        self.web.request = FakeRequest({
            "run_id": 1,
            "response_id": 2,
            "case_id": 3,
            "condition_scores": [{"condition": "c1", "met": 1}],
        })
        with patch.object(self.web, "set_human_judgment", side_effect=RuntimeError("secret provider error")):
            self._assert_unexpected_exception_is_safe_json(self.web.api_benchmark_human_judgment())

    def test_routes_endpoint_masks_secrets(self):
        with patch.object(self.web, "get_all_providers", return_value={
            "test": {"name": "Test", "api_key": "sk-secret", "available_models": ["m1"]},
        }), patch.object(self.web, "get_route_configs", return_value={
            "benchmark_author": {"provider_key": "test", "model": "m1"},
        }):
            result = self.web.api_benchmark_routes()
        data = result
        self.assertEqual(data["providers"]["test"]["name"], "Test")
        self.assertNotIn("api_key", data["providers"]["test"])
        self.assertEqual(data["routes"]["benchmark_author"]["model"], "m1")

    def test_save_route_validates_task_key(self):
        self.web.request = FakeRequest({"config": {"model": "m"}})
        from source.benchmark.config import save_route_config as real_save
        with patch.object(self.web, "save_route_config", side_effect=ValueError("未知 benchmark 任务路由")):
            result, status = self.web.api_benchmark_save_route("not_a_route")
        self.assertEqual(status, 400)

    def test_suite_detail_includes_runs_with_candidate_count(self):
        suite = {"id": 1, "version_label": "pprb-pilot-2026.08.13-r1", "paper_count": 1}
        runs = [{"id": 5, "candidates": [{"id": 1}]}]
        with patch.object(self.web, "get_suite", return_value=suite), \
             patch.object(self.web, "list_suite_papers", return_value=[]), \
             patch.object(self.web, "list_cases", return_value=[]), \
             patch.object(self.web, "list_runs", return_value=runs):
            result = self.web.api_benchmark_suite(1)
        self.assertEqual(result["suite"]["runs"][0]["candidate_count"], 1)

    def test_benchmark_page_and_startup_use_public_benchmark_interfaces(self):
        root = Path(__file__).resolve().parents[2]
        api_source = (root / "source" / "web" / "benchmark_api.py").read_text(encoding="utf-8")
        pages_source = (root / "source" / "web" / "pages.py").read_text(encoding="utf-8")
        application_source = (root / "source" / "web" / "application.py").read_text(encoding="utf-8")
        self.assertIn('@bp.route("/benchmark")', pages_source)
        self.assertIn('render_template("benchmark.html")', pages_source)
        self.assertNotIn('def benchmark_page', api_source)
        self.assertNotIn("render_template", api_source)
        self.assertNotIn("source.storage.benchmark", api_source)
        self.assertNotIn("source.storage.benchmark", application_source)
        self.assertIn("mark_interrupted_runs", application_source)


if __name__ == "__main__":
    unittest.main()
