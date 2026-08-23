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
        self.assertEqual(result, {"status": "ok", "suites": [{"id": 1}]})

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
        with patch.object(self.web, "generate_cases", return_value=[
            {"paper_key": "p1", "deep_reading": [{"ok": True}], "chat": [{"ok": False}]},
        ]):
            result = self.web.api_benchmark_generate_cases(3)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["ok_cases"], 1)
        self.assertEqual(result["rejected_cases"], 1)

    def test_generate_cases_failure_is_propagated(self):
        self.web.request = FakeRequest({})
        from source.benchmark import BenchmarkError
        with patch.object(self.web, "generate_cases", side_effect=BenchmarkError("题库已冻结")):
            result, status = self.web.api_benchmark_generate_cases(3)
        self.assertEqual(status, 400)
        self.assertIn("冻结", result["message"])

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
        with patch.object(self.web, "start_run", return_value=7) as start_run, \
             patch.object(self.web, "get_run", return_value={"id": 7}):
            result = self.web.api_benchmark_start_run(1)
        self.assertEqual(result["run_id"], 7)
        start_run.assert_called_once()
        args = start_run.call_args
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
        with patch.object(self.web, "start_run", side_effect=BenchmarkError("候选调用预算不足：至少需要 4 次，当前仅 1 次")):
            result, status = self.web.api_benchmark_start_run(1)
        self.assertEqual(status, 400)
        self.assertIn("候选调用预算不足", result["message"])

    def test_resume_run_passes_higher_max_calls(self):
        self.web.request = FakeRequest({"max_calls": 120})
        with patch.object(self.web, "resume_run", return_value=7) as resume_run, \
             patch.object(self.web, "get_run", return_value={"id": 7}):
            result = self.web.api_benchmark_resume_run(7)
        self.assertEqual(result["status"], "ok")
        resume_run.assert_called_once()
        self.assertEqual(resume_run.call_args.args[0], 7)
        self.assertEqual(resume_run.call_args.kwargs["max_calls"], 120)

    def test_resume_run_returns_clear_benchmark_error(self):
        self.web.request = FakeRequest({"max_calls": 80})
        from source.benchmark import BenchmarkError
        with patch.object(self.web, "resume_run", side_effect=BenchmarkError("候选调用预算已耗尽；恢复前必须严格上调 max_calls")):
            result, status = self.web.api_benchmark_resume_run(7)
        self.assertEqual(status, 400)
        self.assertIn("严格上调 max_calls", result["message"])

    def test_human_judgment_requires_numeric_score(self):
        self.web.request = FakeRequest({"run_id": 1, "response_id": 2, "case_id": 3, "score": "abc"})
        with patch.object(self.web, "set_human_judgment", side_effect=ValueError("人工评分必须是 0-100 的数字")):
            result, status = self.web.api_benchmark_human_judgment()
        self.assertEqual(status, 400)

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
