"""Benchmark end-to-end pipeline tests with a queued fake model.

覆盖：草稿创建 → 出题 → 逐题审核 → 冻结 → 预算 → 运行 → 裁判/复核 → 报告；
以及缺题计零、严重幻觉封顶、冻结不可变、输出复用与恢复、凭据不落库。
"""

import json
import os
import tempfile
import types
import unittest
from unittest.mock import patch

from source.storage import connection as db_connection
from source.settings import store as settings_store
from source.storage import init_db, insert_paper

from .common import DummyHttpxClient, DummyOpenAI, install_import_stubs


FULL_TEXT = (
    "This paper proposes FROB, a new method for robot navigation. "
    "The method uses a neural policy trained on 1000 simulated episodes. "
    "Experiments show a 20 percent improvement over baselines. "
    "A known limitation is sensitivity to lighting changes. "
    "The method does not handle dynamic obstacles well."
)


def completion(content, finish_reason="stop", prompt_tokens=120, completion_tokens=60, cached=20):
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content=content),
            finish_reason=finish_reason,
        )],
        usage=types.SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            prompt_tokens_details=types.SimpleNamespace(cached_tokens=cached),
        ),
    )


class QueueOpenAI(DummyOpenAI):
    queue = []

    @classmethod
    def push(cls, *items):
        cls.queue.extend(items)

    @classmethod
    def clear(cls):
        cls.queue = []

    def _create_chat_completion(self, **kwargs):
        DummyOpenAI.last_chat_kwargs = kwargs
        if not QueueOpenAI.queue:
            raise AssertionError("意外的模型调用（队列已空）")
        return QueueOpenAI.queue.pop(0)


def _case_payload(kind, question, reference_answer, evidence, weight=1.0, critical=False,
                  requires_reject=False):
    return {
        "kind": kind,
        "question": question,
        "reference_answer": reference_answer,
        "evidence": evidence,
        "rubric": {"conditions": [{"text": "判定条件", "weight": weight, "critical": critical}]},
        "requires_reject": requires_reject,
    }


def author_response():
    deep = [
        _case_payload("q1", "问题与动机是什么？", "FROB 针对机器人导航问题",
                      ["This paper proposes FROB, a new method for robot navigation."]),
        _case_payload("q2", "相关研究有哪些？", "与神经策略基线对比",
                      ["a neural policy trained on 1000 simulated episodes"]),
        _case_payload("q3", "方法是什么？", "神经策略方法 FROB",
                      ["FROB, a new method"]),
        _case_payload("q4", "实验设置与结果？", "提升 20 个百分点",
                      ["a 20 percent improvement over baselines"]),
        _case_payload("q5", "局限是什么？", "对光照变化敏感",
                      ["A known limitation is sensitivity to lighting changes"]),
        _case_payload("q6", "整体总结", "FROB 是导航新方法",
                      ["This paper proposes FROB"]),
    ]
    chat = [
        _case_payload("chat_round1", "请准确解释本文方法", "FROB 是神经策略导航方法",
                      ["FROB, a new method"]),
        _case_payload("chat_round2", "方法在实验中的表现如何？", "提升 20 个百分点",
                      ["20 percent improvement"]),
        _case_payload("chat_round3", "该方法有哪些局限？", "对光照敏感且不处理动态障碍",
                      ["does not handle dynamic obstacles well"], requires_reject=True),
    ]
    return {"deep_reading": deep, "chat_script": chat}


def deep_reading_response():
    sections = [
        ("1", "问题与动机是什么？", "FROB 针对机器人导航问题"),
        ("2", "相关研究有哪些？", "与神经策略基线对比"),
        ("3", "方法是什么？", "神经策略方法 FROB"),
        ("4", "实验设置与结果？", "提升 20 个百分点"),
        ("5", "局限是什么？", "对光照变化敏感"),
        ("6", "整体总结", "FROB 是导航新方法"),
    ]
    blocks = "\n\n".join(f"### Q{num}: {question}\n\n{answer}" for num, question, answer in sections)
    return json.dumps({"qa_analysis": blocks}, ensure_ascii=False)


def judge_response(kinds, met=1, hallucination=False, confidence=0.9):
    scores = [
        {
            "kind": kind,
            "conditions": [{"condition": "判定条件", "met": met}],
            "hallucination_critical": hallucination,
            "confidence": confidence,
            "note": "",
        }
        for kind in kinds
    ]
    return json.dumps({"scores": scores}, ensure_ascii=False)


class BenchmarkPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_import_stubs()
        import source.analysis.client as client_mod
        cls._original_openai = client_mod.OpenAI
        cls._original_http = client_mod.DefaultHttpxClient
        client_mod.OpenAI = QueueOpenAI
        client_mod.DefaultHttpxClient = DummyHttpxClient

    @classmethod
    def tearDownClass(cls):
        import source.analysis.client as client_mod
        client_mod.OpenAI = cls._original_openai
        client_mod.DefaultHttpxClient = cls._original_http

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db_path = db_connection.DB_PATH
        self.original_settings_path = settings_store.SETTINGS_PATH
        db_connection.DB_PATH = os.path.join(self.tmp.name, "papers.db")
        settings_store.SETTINGS_PATH = os.path.join(self.tmp.name, "settings.json")
        with open(settings_store.SETTINGS_PATH, "w", encoding="utf-8") as handle:
            json.dump({
                "settings_schema_version": 4,
                "providers": {
                    "test": {
                        "name": "Test",
                        "api_key": "sk-test-secret",
                        "base_url": "https://api.test/v1",
                        "available_models": ["m1"],
                    }
                },
            }, handle)
        init_db()
        QueueOpenAI.clear()
        DummyOpenAI.last_chat_kwargs = None
        import source.benchmark as bm
        for task_key in ("benchmark_author", "benchmark_judge", "benchmark_judge_review"):
            bm.save_route_config(task_key, {
                "provider_key": "test", "model": "m1", "is_thinking": False,
                "thinking_effort": "medium", "temperature_enabled": True,
                "temperature": 0.1, "max_tokens_enabled": True, "max_tokens": 3000,
            })

    def tearDown(self):
        db_connection.DB_PATH = self.original_db_path
        settings_store.SETTINGS_PATH = self.original_settings_path
        QueueOpenAI.clear()
        self.tmp.cleanup()

    def _add_paper(self, key="2608.00001"):
        return insert_paper({
            "paper_key": key, "arxiv_id": key, "source_type": "arxiv",
            "source_id": key, "ingest_mode": "feed", "title": "FROB Navigation",
            "authors": ["Alice", "Bob"], "abstract": "A navigation method.",
            "categories": ["cs.RO"], "primary_category": "cs.RO",
            "published_date": "2026-08-01",
        })

    def _create_frozen_suite(self, key="2608.00001"):
        import source.benchmark as bm
        self._add_paper(key)
        with patch.object(bm, "_extract_full_text", return_value=FULL_TEXT):
            suite, failures = bm.create_draft("pilot", [key])
        self.assertEqual(failures, [])
        QueueOpenAI.push(completion(json.dumps(author_response(), ensure_ascii=False)))
        bm.generate_cases(suite["id"])
        cases = bm.list_cases(suite["id"])
        self.assertEqual(len(cases), 9)
        for case in cases:
            self.assertEqual(case["status"], "pending")
        bm.review_all_cases(suite["id"], "accept")
        return bm.freeze_suite(suite["id"])

    def _candidate(self, label="cand-a"):
        return {
            "label": label,
            "config": {
                "provider_key": "test", "model": "m1", "is_thinking": False,
                "thinking_effort": "low", "temperature_enabled": True,
                "temperature": 0.2, "max_tokens_enabled": True, "max_tokens": 2000,
            },
        }

    def _run_queues(self):
        """一次完整运行的模型调用队列（1 论文、1 候选、1 重复）。"""
        QueueOpenAI.push(
            completion(deep_reading_response()),
            completion("第一轮回答：FROB 是神经策略导航方法"),
            completion("第二轮回答：实验提升 20 个百分点"),
            completion("第三轮回答：局限是对光照敏感，不处理动态障碍"),
            completion(judge_response(["q1", "q2", "q3", "q4", "q5", "q6"])),
            completion(judge_response(["chat_round1", "chat_round2", "chat_round3"])),
            completion(judge_response(["q1", "q2", "q3", "q4", "q5", "q6"], confidence=0.85)),
            completion(judge_response(["chat_round1", "chat_round2", "chat_round3"], confidence=0.85)),
        )

    def test_full_pipeline_two_tracks_and_report(self):
        import source.benchmark as bm
        suite = self._create_frozen_suite()
        self.assertEqual(suite["status"], "frozen")
        self.assertTrue(suite["suite_checksum"])

        estimate = bm.estimate_calls(suite["id"], 1, 1)
        self.assertEqual(estimate["measured_calls"], 4)
        self.assertEqual(estimate["normal_candidate_calls"], 4)
        self.assertEqual(estimate["candidate_worst_calls"], 15)
        self.assertEqual(estimate["worst_candidate_attempts"], 15)
        self.assertEqual(estimate["judge_calls"], 2)
        self.assertEqual(estimate["normal_judge_calls"], 2)
        self.assertEqual(estimate["judge_worst_calls"], 6)
        self.assertEqual(estimate["worst_judge_attempts"], 6)
        self.assertEqual(estimate["review_calls"], 2)
        self.assertEqual(estimate["normal_review_calls"], 2)
        self.assertEqual(estimate["review_worst_calls"], 6)
        self.assertEqual(estimate["worst_review_attempts"], 6)
        self.assertEqual(estimate["normal_total_calls"], 8)
        self.assertEqual(estimate["worst_total_calls"], 27)
        self.assertEqual(estimate["normal"]["total_calls"], 8)
        self.assertEqual(estimate["worst"]["total_calls"], 27)

        self._run_queues()
        run_id = bm.start_run(suite["id"], [self._candidate()])
        run = bm.get_run(run_id)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(len(run["responses"]), 4)
        self.assertEqual(QueueOpenAI.queue, [])

        report = bm.get_report(run_id)
        self.assertEqual(report["paper_count"], 1)
        self.assertTrue(report["runner_version"])
        self.assertEqual(report["runner_version"], bm.RUNNER_VERSION)
        self.assertTrue(report["is_pilot"])
        for track in ("deep_reading", "chat"):
            entries = report["tracks"][track]
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["score"], 100.0)
            self.assertEqual(entries[0]["per_paper"]["2608.00001"], 100.0)
        self.assertGreater(report["usage"]["total_tokens"], 0)
        self.assertIn("pilot", report["warnings"][0])
        self.assertFalse(report["calibration"]["calibrated"])

        serialized = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("sk-test-secret", serialized)
        candidate = run["candidates"][0]
        self.assertNotIn("api_key", candidate)
        self.assertTrue(candidate["config_hash"])
        self.assertEqual(candidate["actual_params"]["model"], "m1")
        self.assertEqual(candidate["actual_params"]["max_tokens"], 2000)
        self.assertNotIn("messages", candidate["actual_params"])

    def test_public_run_wrappers_and_historical_runner_warning(self):
        import source.benchmark as bm
        from source.storage import benchmark as bench

        suite = self._create_frozen_suite()
        self.assertEqual(bm.list_suite_papers(suite["id"]), bench.list_suite_papers(suite["id"]))

        # Seed a pre-v6-style run through storage: its empty version must be
        # preserved, while the public report makes the missing provenance
        # explicit instead of fabricating a current runner version.
        legacy_run_id = bench.create_benchmark_run(suite["id"])
        self.assertEqual(bm.get_run(legacy_run_id)["runner_version"], "")
        self.assertTrue(any(run["id"] == legacy_run_id for run in bm.list_runs(suite["id"])))

        self.assertGreaterEqual(bm.mark_interrupted_runs(), 1)
        self.assertEqual(bm.get_run(legacy_run_id)["status"], "interrupted")
        report = bm.get_report(legacy_run_id)
        self.assertTrue(any("历史运行缺少 runner_version" in warning for warning in report["warnings"]))

    def test_missing_q_scores_zero_and_is_reported(self):
        import source.benchmark as bm
        suite = self._create_frozen_suite()

        full = json.loads(deep_reading_response())
        blocks = full["qa_analysis"].split("\n\n")
        missing_q6 = "\n\n".join(block for block in blocks if not block.startswith("### Q6"))
        QueueOpenAI.push(
            completion(json.dumps({"qa_analysis": missing_q6}, ensure_ascii=False)),
            completion("续写仍缺少 Q6"),
            completion("第一轮回答"),
            completion("第二轮回答"),
            completion("第三轮回答"),
            completion(judge_response(["q1", "q2", "q3", "q4", "q5"])),
            completion(judge_response(["chat_round1", "chat_round2", "chat_round3"])),
            completion(judge_response(["q1", "q2", "q3", "q4", "q5"], confidence=0.85)),
            completion(judge_response(["chat_round1", "chat_round2", "chat_round3"], confidence=0.85)),
        )
        run_id = bm.start_run(suite["id"], [self._candidate()])
        report = bm.get_report(run_id)
        deep = report["tracks"]["deep_reading"][0]
        q6 = next(item for item in deep["per_case"] if item["kind"] == "q6")
        self.assertEqual(q6["score"], 0.0)
        self.assertEqual(q6["judge"], "system")
        self.assertEqual(deep["missing_qs"], 1)
        self.assertAlmostEqual(deep["score"], 100.0 * 5 / 6, places=1)

    def test_hallucination_caps_case_score_at_60(self):
        import source.benchmark as bm
        suite = self._create_frozen_suite()
        QueueOpenAI.push(
            completion(deep_reading_response()),
            completion("第一轮回答"), completion("第二轮回答"), completion("第三轮回答"),
            completion(judge_response(["q1", "q2", "q3", "q4", "q5", "q6"], hallucination=True)),
            completion(judge_response(["chat_round1", "chat_round2", "chat_round3"], hallucination=True)),
            completion(judge_response(["q1", "q2", "q3", "q4", "q5", "q6"],
                                      hallucination=True, confidence=0.85)),
            completion(judge_response(["chat_round1", "chat_round2", "chat_round3"],
                                      hallucination=True, confidence=0.85)),
        )
        run_id = bm.start_run(suite["id"], [self._candidate()])
        report = bm.get_report(run_id)
        for track in ("deep_reading", "chat"):
            entry = report["tracks"][track][0]
            self.assertEqual(entry["score"], 60.0)
            self.assertEqual(entry["hallucination_critical"], 6 if track == "deep_reading" else 3)

    def test_budget_interrupts_and_resume_reuses_outputs(self):
        import source.benchmark as bm
        suite = self._create_frozen_suite()

        # 预算 4 次被测调用：deep + deep continuation + chat r1 + chat r2；
        # chat r3 在启动前扣费时发现预算耗尽而中断。
        QueueOpenAI.push(
            completion(deep_reading_response(), finish_reason="length"),
            completion(deep_reading_response()),
            completion("第一轮回答"), completion("第二轮回答"),
        )
        run_id = bm.start_run(suite["id"], [self._candidate()], max_calls=4)
        run = bm.get_run(run_id)
        self.assertEqual(run["status"], "interrupted")
        self.assertEqual(len(run["responses"]), 3)
        self.assertEqual(QueueOpenAI.queue, [])

        # 恢复：严格上调预算后只补 chat r3，然后裁判与复核
        QueueOpenAI.push(
            completion("第三轮回答"),
            completion(judge_response(["q1", "q2", "q3", "q4", "q5", "q6"])),
            completion(judge_response(["chat_round1", "chat_round2", "chat_round3"])),
            completion(judge_response(["q1", "q2", "q3", "q4", "q5", "q6"], confidence=0.85)),
            completion(judge_response(["chat_round1", "chat_round2", "chat_round3"], confidence=0.85)),
        )
        bm.resume_run(run_id, max_calls=5)
        run = bm.get_run(run_id)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(len(run["responses"]), 4)
        self.assertEqual(QueueOpenAI.queue, [])

        # 恢复运行不重复生成已保存响应：deep 响应只有一条且内容未被替换
        deep_responses = [r for r in run["responses"] if r["track"] == "deep_reading"]
        self.assertEqual(len(deep_responses), 1)
        self.assertIn("FROB 针对机器人导航问题", deep_responses[0]["parsed"]["q1"])

    def test_chat_history_carries_candidate_own_answers(self):
        import source.benchmark as bm
        suite = self._create_frozen_suite()
        QueueOpenAI.push(
            completion(deep_reading_response()),
            completion("第一轮回答：FROB 是神经策略导航方法"),
            completion("第二轮回答：实验提升 20 个百分点"),
            completion("第三轮回答：局限是对光照敏感"),
            completion(judge_response(["q1", "q2", "q3", "q4", "q5", "q6"])),
            completion(judge_response(["chat_round1", "chat_round2", "chat_round3"])),
            completion(judge_response(["q1", "q2", "q3", "q4", "q5", "q6"], confidence=0.85)),
            completion(judge_response(["chat_round1", "chat_round2", "chat_round3"], confidence=0.85)),
        )
        run_id = bm.start_run(suite["id"], [self._candidate()])
        run = bm.get_run(run_id)
        chat_r2 = next(r for r in run["responses"] if r["track"] == "chat" and r["round_index"] == 2)
        contents = [m["content"] for m in chat_r2["prompt_snapshot"] if m["role"] == "assistant"]
        self.assertEqual(contents, ["第一轮回答：FROB 是神经策略导航方法"])
        chat_r3 = next(r for r in run["responses"] if r["track"] == "chat" and r["round_index"] == 3)
        contents = [m["content"] for m in chat_r3["prompt_snapshot"] if m["role"] == "assistant"]
        self.assertEqual(len(contents), 2)

    def test_frozen_suite_is_immutable_and_run_requires_frozen(self):
        import source.benchmark as bm
        suite = self._create_frozen_suite()
        cases = bm.list_cases(suite["id"])
        with self.assertRaises(bm.BenchmarkError):
            bm.review_case(cases[0]["id"], "reject", note="改题")
        with self.assertRaises(bm.BenchmarkError):
            bm.generate_cases(suite["id"])

        with patch.object(bm, "_extract_full_text", return_value=FULL_TEXT):
            draft, _ = bm.create_draft("draft2", ["2608.00001"])
        with self.assertRaises(bm.BenchmarkError):
            bm.start_run(draft["id"], [self._candidate()])

    def test_auto_reject_evidence_and_freeze_blocked(self):
        import source.benchmark as bm
        self._add_paper()
        with patch.object(bm, "_extract_full_text", return_value=FULL_TEXT):
            suite, _failures = bm.create_draft("pilot", ["2608.00001"])
        response = author_response()
        response["deep_reading"][0]["evidence"] = ["这段文字不存在于论文中"]
        QueueOpenAI.push(completion(json.dumps(response, ensure_ascii=False)))
        bm.generate_cases(suite["id"])
        cases = bm.list_cases(suite["id"])
        q1 = next(case for case in cases if case["kind"] == "q1")
        self.assertEqual(q1["status"], "rejected")
        self.assertIn("自动驳回", q1["review_note"])

        remaining = [case for case in bm.list_cases(suite["id"]) if case["status"] != "rejected"]
        for case in remaining:
            bm.review_case(case["id"], "accept")
        with self.assertRaises(bm.BenchmarkError) as ctx:
            bm.freeze_suite(suite["id"])
        self.assertIn("Q1", str(ctx.exception))

    def test_qa_sections_extract_from_malformed_json_envelopes(self):
        from source.benchmark.runner import _qa_sections_from_content
        # 模型把转义换行与原始换行混合、并在字符串内嵌入第二个 JSON 包络
        content = (
            '{"qa_analysis": "### Q1: 动机\\n\\n第一段回答。\\n\\n'
            '### Q2: 方法\\n\\n第二段回答。\\n\\n'
            '{\\n  \\"qa_analysis\\": \\"### Q2: 方法\\n\\n重复包络回答。\\"\\n}"}'
        )
        sections = _qa_sections_from_content(content)
        self.assertEqual(sorted(sections.keys()), [1, 2])
        self.assertIn("第一段回答", sections[1])

    def test_judge_kind_rename_falls_back_to_position(self):
        import source.benchmark as bm
        suite = self._create_frozen_suite()
        QueueOpenAI.push(
            completion(deep_reading_response()),
            completion("第一轮回答"), completion("第二轮回答"), completion("第三轮回答"),
            # 裁判把 chat_round1/2/3 重命名为 q1/q2/q3（按顺序返回）
            completion(judge_response(["q1", "q2", "q3"])),
            completion(judge_response(["chat_round1", "chat_round2", "chat_round3"])),
            completion(judge_response(["q1", "q2", "q3"], confidence=0.85)),
            completion(judge_response(["chat_round1", "chat_round2", "chat_round3"], confidence=0.85)),
        )
        run_id = bm.start_run(suite["id"], [self._candidate()])
        report = bm.get_report(run_id)
        chat = report["tracks"]["chat"][0]
        self.assertEqual(chat["score"], 100.0)
        self.assertTrue(all(item["score"] == 100.0 for item in chat["per_case"]))

    def test_partial_evidence_keeps_case_pending_with_warning(self):
        import source.benchmark as bm
        self._add_paper()
        with patch.object(bm, "_extract_full_text", return_value=FULL_TEXT):
            suite, _failures = bm.create_draft("pilot", ["2608.00001"])
        response = author_response()
        response["deep_reading"][0]["evidence"] = [
            "This paper proposes FROB, a new method for robot navigation.",
            "这段文字不存在于论文中",
        ]
        QueueOpenAI.push(completion(json.dumps(response, ensure_ascii=False)))
        bm.generate_cases(suite["id"])
        q1 = next(c for c in bm.list_cases(suite["id"]) if c["kind"] == "q1")
        self.assertEqual(q1["status"], "pending")
        self.assertIn("自动提示", q1["review_note"])
        self.assertIn("这段文字不存在于论文中", q1["author_raw"].get("evidence_raw", []))

    def test_start_run_requires_frozen_suite_and_candidates(self):
        import source.benchmark as bm
        suite = self._create_frozen_suite()
        with self.assertRaises(bm.BenchmarkError):
            bm.start_run(suite["id"], [])
        with self.assertRaises(ValueError):
            bm.resolve_model_config({"provider_key": "missing", "model": "m1"})

    def test_start_run_rejects_duplicate_effective_request_parameters(self):
        import source.benchmark as bm
        from source.storage import benchmark as bench
        suite = self._create_frozen_suite()
        first = self._candidate("first")
        second = self._candidate("second")
        # Disabled optional fields are absent from the actual request, so these
        # two configurations must be rejected as the same candidate.
        first["config"].update({"temperature_enabled": False, "temperature": 0.1})
        second["config"].update({"temperature_enabled": False, "temperature": 1.9})
        before = bench.list_benchmark_runs(suite["id"])
        with self.assertRaisesRegex(bm.BenchmarkError, "重复|相同"):
            bm.start_run(suite["id"], [first, second], max_calls=8)
        self.assertEqual(bench.list_benchmark_runs(suite["id"]), before)

    def test_failed_candidate_api_attempt_is_persistently_charged(self):
        import source.benchmark as bm
        suite = self._create_frozen_suite()
        # The preflight budget is sufficient for the logical run; the queued
        # model is intentionally empty, so the first API attempt raises after
        # its atomic reservation.
        run_id = bm.start_run(suite["id"], [self._candidate()], max_calls=4)
        run = bm.get_run(run_id)
        self.assertEqual(run["status"], "error")
        self.assertEqual(run["candidate_calls_made"], 1)

    def test_budget_is_rejected_before_start_when_full_candidate_work_does_not_fit(self):
        import source.benchmark as bm
        suite = self._create_frozen_suite()
        with self.assertRaisesRegex(bm.BenchmarkError, "预算"):
            bm.start_run(suite["id"], [self._candidate()], max_calls=3)

    def test_resume_budget_must_strictly_increase_and_counts_continuations(self):
        import source.benchmark as bm
        suite = self._create_frozen_suite()
        QueueOpenAI.push(
            completion(deep_reading_response(), finish_reason="length"),
            completion(deep_reading_response()),
            completion("第一轮回答"), completion("第二轮回答"),
        )
        # Deep reading continuation consumes the second candidate call.  The
        # fourth call leaves the run exhausted before chat round three.
        run_id = bm.start_run(suite["id"], [self._candidate()], max_calls=4)
        run = bm.get_run(run_id)
        self.assertEqual(run["candidate_calls_made"], 4)
        with self.assertRaisesRegex(bm.BenchmarkError, "上调|增加"):
            bm.resume_run(run_id, max_calls=4)

    def test_weighted_rubric_and_unmet_conditions_are_exact(self):
        from source.benchmark.judge import _score_for_case
        result = _score_for_case(
            {"rubric": {"conditions": [
                {"text": "核心", "weight": 3},
                {"text": "次要", "weight": 1},
            ]}},
            {"conditions": [
                {"condition": "核心", "met": "met"},
                # The judge omitted the second rubric item: it is unmet, not
                # silently removed from the denominator.
            ]},
        )
        self.assertEqual(result["score"], 75.0)
        self.assertEqual(result["condition_scores"][-1]["met"], 0.0)

    def test_primary_review_condition_conflict_waits_for_human_and_report_marks_it(self):
        import source.benchmark as bm
        suite = self._create_frozen_suite()
        QueueOpenAI.push(
            completion(deep_reading_response()),
            completion("第一轮回答"), completion("第二轮回答"), completion("第三轮回答"),
            completion(judge_response(["q1", "q2", "q3", "q4", "q5", "q6"], met=1)),
            completion(judge_response(["chat_round1", "chat_round2", "chat_round3"], met=1)),
            completion(judge_response(["q1", "q2", "q3", "q4", "q5", "q6"], met=0, confidence=0.85)),
            completion(judge_response(["chat_round1", "chat_round2", "chat_round3"], met=1, confidence=0.85)),
        )
        run_id = bm.start_run(suite["id"], [self._candidate()])
        report = bm.get_report(run_id)
        deep = report["tracks"]["deep_reading"][0]
        q1 = next(item for item in deep["per_case"] if item["kind"] == "q1")
        self.assertIsNotNone(q1["response_id"])
        self.assertIsNotNone(q1["case_id"])
        self.assertEqual(q1["primary_score"], 100.0)
        self.assertEqual(q1["review_score"], 0.0)
        self.assertTrue(q1["needs_human_review"])
        self.assertIsNone(q1["score"])
        self.assertIsNone(deep["score"])

        bm.set_human_judgment(
            run_id, q1["response_id"], q1["case_id"],
            [{"condition": "判定条件", "met": 0.5}], notes="人工裁决",
        )
        resolved = bm.get_report(run_id)["tracks"]["deep_reading"][0]
        resolved_q1 = next(item for item in resolved["per_case"] if item["kind"] == "q1")
        self.assertEqual(resolved_q1["score"], 50.0)
        self.assertFalse(resolved_q1["needs_human_review"])
        self.assertEqual(resolved["score"], 50.0)

    def test_actual_request_snapshot_preserves_non_message_parameters_and_protocol_mapping(self):
        from source.benchmark.config import build_actual_request_params

        with patch(
            "source.benchmark.config.build_chat_completion_kwargs",
            return_value={
                "model": "mock-model",
                "messages": [{"role": "user", "content": "secret prompt"}],
                "extra_body": {"vendor_flag": True},
                "reasoning_effort": "high",
                "max_completion_tokens": 321,
                "api_key": "sk-secret",
            },
        ):
            actual = build_actual_request_params({"model": "mock-model"})
        self.assertEqual(actual, {
            "model": "mock-model",
            "extra_body": {"vendor_flag": True},
            "reasoning_effort": "high",
            "max_completion_tokens": 321,
        })

        openai = build_actual_request_params({
            "base_url": "https://api.openai.com/v1", "model": "o3-mini",
            "is_thinking": True, "thinking_effort": "high",
            "max_tokens_enabled": True, "max_tokens": 123,
        })
        self.assertEqual(openai["reasoning_effort"], "high")
        self.assertEqual(openai["max_completion_tokens"], 123)
        qwen = build_actual_request_params({
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "model": "qwen-max", "is_thinking": True,
            "thinking_effort": "high", "max_tokens_enabled": True, "max_tokens": 123,
        })
        self.assertEqual(qwen["extra_body"]["enable_thinking"], True)
        self.assertIn("thinking_budget", qwen["extra_body"])
        deepseek_v4 = build_actual_request_params({
            "base_url": "https://api.deepseek.com", "model": "deepseek-v4",
            "is_thinking": True, "thinking_effort": "high",
            "max_tokens_enabled": True, "max_tokens": 123,
        })
        self.assertEqual(deepseek_v4["reasoning_effort"], "high")
        self.assertEqual(deepseek_v4["extra_body"]["thinking"], {"type": "enabled"})
        deepseek_legacy = build_actual_request_params({
            "base_url": "https://api.deepseek.com", "model": "deepseek-reasoner",
            "is_thinking": True, "thinking_effort": "high",
            "max_tokens_enabled": True, "max_tokens": 123,
        })
        self.assertEqual(deepseek_legacy["max_tokens"], 123)
        self.assertNotIn("messages", json.dumps(actual))
        self.assertNotIn("sk-secret", json.dumps(actual))

    def test_cross_run_reuse_skips_candidate_budget_and_keeps_provenance(self):
        import source.benchmark as bm
        suite = self._create_frozen_suite()
        self._run_queues()
        first_run = bm.start_run(suite["id"], [self._candidate()])
        first_responses = bm.get_run(first_run)["responses"]
        self.assertEqual(len(first_responses), 4)

        # Candidate slots are all reusable, so max_calls=1 is enough for the
        # new run.  The four calls below are judge/review calls only.
        QueueOpenAI.push(
            completion(judge_response(["q1", "q2", "q3", "q4", "q5", "q6"])),
            completion(judge_response(["chat_round1", "chat_round2", "chat_round3"])),
            completion(judge_response(["q1", "q2", "q3", "q4", "q5", "q6"], confidence=0.85)),
            completion(judge_response(["chat_round1", "chat_round2", "chat_round3"], confidence=0.85)),
        )
        second_run = bm.start_run(suite["id"], [self._candidate()], max_calls=1)
        second = bm.get_run(second_run)
        self.assertEqual(second["candidate_calls_made"], 0)
        self.assertTrue(all(item["reused_from_response_id"] for item in second["responses"]))
        reused_report = bm.get_report(second_run)
        self.assertEqual(reused_report["candidate_calls_made"], 0)
        self.assertEqual(reused_report["usage"]["total_tokens"], 0)
        self.assertEqual(reused_report["usage"]["latency_ms"], 0.0)
        self.assertEqual(reused_report["usage"]["retry_count"], 0)
        self.assertEqual(reused_report["usage"]["calls"], 0)

    def test_new_runner_version_does_not_reuse_v6_outputs(self):
        import source.benchmark as bm
        suite = self._create_frozen_suite()
        self._run_queues()
        first_run = bm.start_run(suite["id"], [self._candidate()])
        from source.storage.connection import get_connection
        with get_connection() as conn:
            conn.execute(
                "UPDATE benchmark_runs SET runner_version = 'pprb-runner-v6' WHERE id = ?",
                (first_run,),
            )
        QueueOpenAI.push(
            completion(deep_reading_response()),
            completion("第一轮回答"), completion("第二轮回答"), completion("第三轮回答"),
            completion(judge_response(["q1", "q2", "q3", "q4", "q5", "q6"])),
            completion(judge_response(["chat_round1", "chat_round2", "chat_round3"])),
            completion(judge_response(["q1", "q2", "q3", "q4", "q5", "q6"], confidence=0.85)),
            completion(judge_response(["chat_round1", "chat_round2", "chat_round3"], confidence=0.85)),
        )
        second_run = bm.start_run(suite["id"], [self._candidate()], max_calls=4)
        self.assertEqual(bm.get_run(second_run)["runner_version"], "pprb-runner-v7")
        self.assertEqual(bm.get_run(second_run)["candidate_calls_made"], 4)

    def test_case_edit_revalidates_and_records_actor_audit(self):
        import source.benchmark as bm
        self._add_paper()
        with patch.object(bm, "_extract_full_text", return_value=FULL_TEXT):
            suite, _ = bm.create_draft("edit", ["2608.00001"])
        QueueOpenAI.push(completion(json.dumps(author_response(), ensure_ascii=False)))
        bm.generate_cases(suite["id"])
        case = bm.list_cases(suite["id"])[0]
        edited = bm.edit_case(
            case["id"], {"reference_answer": "人工修订答案", "evidence": case["evidence"], "rubric": case["rubric"]},
            actor_user_id=7, actor_username="reviewer", note="人工修订", decision="accept",
        )
        self.assertEqual(edited["status"], "accepted")
        self.assertEqual(bm.list_case_revisions(case["id"])[-1]["actor_username"], "reviewer")

    def test_rejudge_activates_new_revision_without_candidate_calls(self):
        import source.benchmark as bm
        suite = self._create_frozen_suite()
        self._run_queues()
        run_id = bm.start_run(suite["id"], [self._candidate()])
        before = bm.get_run(run_id)
        old_revision = before["active_scoring_revision"]
        bm.save_route_config("benchmark_judge", {
            "provider_key": "test", "model": "m1", "is_thinking": False,
            "thinking_effort": "medium", "temperature_enabled": True,
            "temperature": 0.9, "max_tokens_enabled": True, "max_tokens": 3000,
        })
        QueueOpenAI.push(
            completion(judge_response(["q1", "q2", "q3", "q4", "q5", "q6"])),
            completion(judge_response(["chat_round1", "chat_round2", "chat_round3"])),
            completion(judge_response(["q1", "q2", "q3", "q4", "q5", "q6"], confidence=0.85)),
            completion(judge_response(["chat_round1", "chat_round2", "chat_round3"], confidence=0.85)),
        )
        bm.rejudge_run(run_id)
        after = bm.get_run(run_id)
        self.assertEqual(after["candidate_calls_made"], before["candidate_calls_made"])
        self.assertNotEqual(after["active_scoring_revision"], old_revision)

        # Switching the judge route back must reuse and reactivate the old
        # completed revision instead of leaving the newer one active.
        bm.save_route_config("benchmark_judge", {
            "provider_key": "test", "model": "m1", "is_thinking": False,
            "thinking_effort": "medium", "temperature_enabled": True,
            "temperature": 0.1, "max_tokens_enabled": True, "max_tokens": 3000,
        })
        bm.rejudge_run(run_id)
        self.assertEqual(bm.get_run(run_id)["active_scoring_revision"], old_revision)

    def test_human_condition_override_requires_exact_rubric_and_computes_score(self):
        import source.benchmark as bm
        suite = self._create_frozen_suite()
        self._run_queues()
        run_id = bm.start_run(suite["id"], [self._candidate()])
        run = bm.get_run(run_id)
        q1 = next(case for case in run["cases"] if case["kind"] == "q1")
        response = next(item for item in run["responses"] if item["track"] == "deep_reading")
        bm.set_human_judgment(
            run_id, response["id"], q1["id"],
            [{"condition": "判定条件", "met": 0.5}], notes="人工校准",
        )
        judgment = next(item for item in bm.get_run(run_id)["responses"] if item["id"] == response["id"])
        self.assertEqual(judgment["id"], response["id"])
        report_case = next(
            item for item in bm.get_report(run_id)["tracks"]["deep_reading"][0]["per_case"]
            if item["case_id"] == q1["id"]
        )
        self.assertEqual(report_case["score"], 50.0)
        with self.assertRaises(ValueError):
            bm.set_human_judgment(run_id, response["id"], q1["id"], [])

        with self.assertRaises(ValueError):
            bm.set_human_judgment(run_id, response["id"], q1["id"], 80)

        bm.set_human_judgment(
            run_id, response["id"], q1["id"],
            [{"condition": "判定条件", "met": 1}],
            hallucination_critical=True,
        )
        capped_case = next(
            item for item in bm.get_report(run_id)["tracks"]["deep_reading"][0]["per_case"]
            if item["case_id"] == q1["id"]
        )
        self.assertEqual(capped_case["score"], 60.0)


if __name__ == "__main__":
    unittest.main()
