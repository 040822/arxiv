"""Private paper reading benchmark (deep module).

对 Web/CLI 调用方只暴露以下接口；模型调用、版本校验、评分聚合与重试细节
隐藏在模块内部。
"""

import logging
import math
import re
from datetime import datetime

from source.documents import (
    download_pdf,
    extract_text_from_pdf,
    get_cached_pdf_path,
    get_paper_pdf_path,
)

from . import author as _author
from . import judge as _judge
from . import report as _report
from . import runner as _runner
from .config import get_route_configs, save_route_config, resolve_model_config
from .evidence import (
    config_hash,
    parse_deep_reading_questions,
    suite_checksum,
    text_sha256,
)

logger = logging.getLogger(__name__)

EXTRACTOR_VERSION = "pymupdf-benchmark-v1"
CHAT_ROUNDS = _author.CHAT_ROUNDS
MAX_SELECT_PAPERS = 500


class BenchmarkError(RuntimeError):
    """Benchmark 业务规则错误。"""


def _require_suite(suite_id):
    from source.storage.benchmark import get_benchmark_suite
    suite = get_benchmark_suite(suite_id)
    if not suite:
        raise BenchmarkError("题库不存在")
    return suite


def _require_not_frozen(suite_id):
    suite = _require_suite(suite_id)
    if suite.get("status") == "frozen":
        raise BenchmarkError("题库已冻结，不可修改；如需变更请克隆新版本")
    return suite


def _extract_full_text(paper_data):
    """提取论文全文用于冻结快照；失败返回空串（冻结时会阻止）。"""
    arxiv_id = paper_data.get("arxiv_id", "")
    paper_key = paper_data.get("paper_key") or arxiv_id
    try:
        if paper_data.get("pdf_local_path") or not arxiv_id:
            pdf_path = get_paper_pdf_path(paper_data, download=True)
        else:
            pdf_path = get_cached_pdf_path(arxiv_id)
            if not pdf_path and paper_data.get("pdf_url"):
                pdf_path = download_pdf(paper_data.get("pdf_url"), arxiv_id)
        if not pdf_path:
            return ""
        return extract_text_from_pdf(pdf_path) or ""
    except Exception as exc:
        logger.warning(f"benchmark full text extraction failed for {paper_key}: {exc}")
        return ""


def _next_version_label(subset_name):
    """生成 pprb-<subset>-YYYY.MM.DD-rN（当天同 subset 已有版本时 N+1）。"""
    from source.storage.benchmark import list_benchmark_suites
    today = datetime.now().strftime("%Y.%m.%d")
    prefix = f"pprb-{subset_name}-{today}-r"
    max_r = 0
    for suite in list_benchmark_suites():
        match = re.match(rf"^{re.escape(prefix)}(\d+)$", suite.get("version_label") or "")
        if match:
            max_r = max(max_r, int(match.group(1)))
    return f"{prefix}{max_r + 1}"


# ---------------------------------------------------------------------------
# 题库生命周期
# ---------------------------------------------------------------------------

def list_suites():
    from source.storage.benchmark import list_benchmark_suites
    return list_benchmark_suites()


def get_suite(suite_id):
    return _require_suite(suite_id)


def list_selectable_papers(limit=MAX_SELECT_PAPERS):
    """列出可选的业务论文（id/paper_key/title/published_date）。"""
    from source.storage.papers import browse_papers
    rows = browse_papers(limit=min(limit, MAX_SELECT_PAPERS))
    return [
        {
            "id": row.get("id"),
            "paper_key": row.get("paper_key", ""),
            "title": row.get("title", ""),
            "published_date": row.get("published_date", ""),
        }
        for row in rows
    ]


def create_draft(subset_name, paper_keys):
    """按论文 key 创建题库草稿：快照论文全文与生产 Prompt，校验文本非空。"""
    from source.settings import get_prompt_profiles
    from source.storage.benchmark import add_benchmark_suite_paper, create_benchmark_suite
    from source.storage.papers import get_paper_by_key

    subset_name = str(subset_name or "").strip()
    if not re.match(r"^[a-zA-Z0-9_-]{1,40}$", subset_name):
        raise BenchmarkError("subset_name 只能包含字母、数字、下划线与连字符（≤40 字符）")
    if not paper_keys:
        raise BenchmarkError("请至少选择 1 篇论文")
    profiles = get_prompt_profiles()
    deep_reading_prompt = profiles.get("deep_reading", {})
    paper_chat_prompt = profiles.get("paper_chat", {})
    if not parse_deep_reading_questions(deep_reading_prompt.get("instruction", "")):
        raise BenchmarkError("生产深度阅读 Prompt 中未找到 ### Qn: 问题，无法出题")

    version_label = _next_version_label(subset_name)
    suite_id = create_benchmark_suite(
        subset_name, version_label,
        {"system": deep_reading_prompt.get("system", ""), "instruction": deep_reading_prompt.get("instruction", "")},
        {"system": paper_chat_prompt.get("system", ""), "instruction": paper_chat_prompt.get("instruction", "")},
    )
    failures = []
    for position, paper_key in enumerate(paper_keys, start=1):
        paper = get_paper_by_key(str(paper_key).strip())
        if not paper:
            failures.append({"paper_key": paper_key, "error": "论文不存在"})
            continue
        full_text = _extract_full_text(paper)
        if not full_text:
            failures.append({"paper_key": paper_key, "error": "PDF 全文提取失败或为空"})
            continue
        authors = paper.get("authors") or []
        add_benchmark_suite_paper(suite_id, {
            "paper_id": paper.get("id"),
            "paper_key": paper.get("paper_key"),
            "title": paper.get("title", ""),
            "authors": authors if isinstance(authors, list) else [],
            "source_type": paper.get("source_type", ""),
            "source_id": paper.get("source_id", ""),
            "abstract": paper.get("abstract", ""),
            "full_text": full_text,
            "text_chars": len(full_text),
            "text_sha256": text_sha256(full_text),
            "pdf_sha256": paper.get("pdf_sha256", ""),
            "extractor_version": EXTRACTOR_VERSION,
            "position": position,
        })
    suite = get_suite(suite_id)
    if not suite.get("paper_count"):
        raise BenchmarkError("所有论文全文提取失败，题库未创建")
    return get_suite(suite_id), failures


def generate_cases(suite_id, progress_callback=None):
    """对题库全部论文执行出题调用；返回逐论文结果摘要。"""
    suite = _require_not_frozen(suite_id)
    from source.storage.benchmark import list_suite_papers
    papers = list_suite_papers(suite_id)
    summaries = []
    for index, paper in enumerate(papers, start=1):
        if progress_callback:
            progress_callback({
                "current": index, "total": len(papers), "phase": "author",
                "message": f"正在出题（{index}/{len(papers)}）：{paper.get('paper_key', '')}",
            })
        summaries.append(_author.generate_cases_for_paper(suite_id, paper, suite))
    if progress_callback:
        progress_callback({"status": "completed", "message": "出题完成"})
    return summaries


def list_cases(suite_id, track=None, status=None):
    from source.storage.benchmark import list_suite_cases
    return list_suite_cases(suite_id, track=track, status=status)


def review_case(case_id, decision, note=""):
    """人工审核单个题目：accept/reject/pending。"""
    from source.storage.benchmark import get_benchmark_case, set_case_review
    case = get_benchmark_case(case_id)
    if not case:
        raise BenchmarkError("题目不存在")
    suite = _require_not_frozen(case["suite_id"])
    decision = str(decision or "").strip()
    if decision not in ("accept", "reject", "pending"):
        raise BenchmarkError("decision 必须是 accept/reject/pending")
    status = {"accept": "accepted", "reject": "rejected", "pending": "pending"}[decision]
    return set_case_review(case_id, status, note=note)


def review_all_cases(suite_id, decision, note=""):
    """批量审核全部题目（accept/reject）。"""
    suite = _require_not_frozen(suite_id)
    from source.storage.benchmark import set_cases_review
    status = {"accept": "accepted", "reject": "rejected"}.get(decision)
    if not status:
        raise BenchmarkError("decision 必须是 accept/reject")
    set_cases_review(suite_id, status, note=note)
    return get_suite(suite_id)


def freeze_suite(suite_id):
    """冻结题库：校验全文、证据与人工确认后置为 frozen 并写入最终校验和。"""
    from source.storage.benchmark import (
        get_benchmark_suite,
        list_suite_cases,
        list_suite_papers,
        update_benchmark_suite,
    )
    suite = get_benchmark_suite(suite_id)
    if not suite:
        raise BenchmarkError("题库不存在")
    if suite.get("status") == "frozen":
        return suite
    papers = list_suite_papers(suite_id)
    cases = list_suite_cases(suite_id)
    problems = []

    for paper in papers:
        if not (paper.get("full_text") or "").strip():
            problems.append(f"论文 {paper['paper_key']} 全文为空，禁止冻结")
    paper_by_id = {paper["id"]: paper for paper in papers}
    for paper in papers:
        paper_cases = [c for c in cases if c["paper_ref_id"] == paper["id"]]
        accepted = [c for c in paper_cases if c["status"] == "accepted"]
        pending = [c for c in paper_cases if c["status"] == "pending"]
        if pending:
            problems.append(f"论文 {paper['paper_key']} 仍有 {len(pending)} 个题目未人工确认")
        deep_cases = {c["position"] for c in accepted if c["track"] == "deep_reading"}
        expected = {
            q["number"] for q in parse_deep_reading_questions(
                (suite.get("deep_reading_prompt") or {}).get("instruction", "")
            )
        }
        missing_deep = sorted(expected - deep_cases)
        if missing_deep:
            problems.append(f"论文 {paper['paper_key']} 深度阅读缺少已确认题目: Q{', Q'.join(map(str, missing_deep))}")
        chat_accepted = [c for c in accepted if c["track"] == "chat"]
        if len(chat_accepted) < CHAT_ROUNDS:
            problems.append(
                f"论文 {paper['paper_key']} 交流轨未确认满 {CHAT_ROUNDS} 轮（当前 {len(chat_accepted)}）"
            )
        for case in accepted:
            if not case["evidence"]:
                problems.append(f"题目 {case['kind']}（{paper['paper_key']}）没有证据片段")
            if not case.get("rubric", {}).get("conditions"):
                problems.append(f"题目 {case['kind']}（{paper['paper_key']}）缺少评分条件")
    if problems:
        raise BenchmarkError("冻结失败：\n- " + "\n- ".join(problems))

    checksum = suite_checksum(
        suite.get("subset_name"), suite.get("version_label"),
        suite.get("deep_reading_prompt"), suite.get("paper_chat_prompt"),
        papers, cases,
    )
    revision_label = f"{suite.get('version_label')}-scoring-r1"
    update_benchmark_suite(suite_id, status="frozen", suite_checksum=checksum)
    update_benchmark_suite(suite_id, scoring_revision={
        "label": revision_label,
        "suite_checksum": checksum,
    })
    return get_benchmark_suite(suite_id)


# ---------------------------------------------------------------------------
# 运行与报告
# ---------------------------------------------------------------------------

def estimate_calls(suite_id, candidate_count, repeats=1):
    """估算一次运行的模型调用数与裁判调用数（不落库）。"""
    suite = _require_suite(suite_id)
    paper_count = suite.get("paper_count", 0)
    repeats = max(1, min(5, int(repeats or 1)))
    candidate_count = max(0, int(candidate_count or 0))
    per_candidate = paper_count * (1 + CHAT_ROUNDS) * repeats
    judge_per_candidate = paper_count * 2 * repeats  # 每论文每轨道 1 次
    review_per_candidate = 2 * max(1, math.ceil(paper_count * repeats * 0.1))  # 每轨道至少 10%（下限 1 组）
    return {
        "paper_count": paper_count,
        "candidates": candidate_count,
        "repeats": repeats,
        "measured_calls": candidate_count * per_candidate,
        "judge_calls": candidate_count * judge_per_candidate,
        "review_calls": candidate_count * review_per_candidate,
        "worst_total_calls": candidate_count * (per_candidate + judge_per_candidate + review_per_candidate),
    }


def _load_run(run_id):
    from source.storage.benchmark import (
        get_benchmark_run,
        get_run_responses,
        list_run_candidates,
        list_suite_cases,
        list_suite_papers,
    )
    run = get_benchmark_run(run_id)
    if not run:
        raise BenchmarkError("运行不存在")
    run["suite"] = get_suite(run["suite_id"])
    run["papers"] = list_suite_papers(run["suite_id"])
    run["cases"] = [c for c in list_suite_cases(run["suite_id"]) if c["status"] == "accepted"]
    run["candidates"] = list_run_candidates(run_id)
    run["responses"] = get_run_responses(run_id)
    return run


def start_run(suite_id, candidates, repeats=1, max_calls=None, progress_callback=None):
    """启动一次评测运行：执行被测调用、裁判与复核；返回 run_id。

    candidates: [{"label": ..., "config": {...provider_key/model/参数...}}]
    """
    suite = _require_suite(suite_id)
    if suite.get("status") != "frozen":
        raise BenchmarkError("题库未冻结，无法运行")
    if not candidates:
        raise BenchmarkError("请至少选择 1 个候选模型")
    repeats = max(1, min(5, int(repeats or 1)))

    from source.storage.benchmark import (
        add_benchmark_candidate,
        create_benchmark_run,
        set_run_status,
    )
    run_id = create_benchmark_run(suite_id, repeats=repeats, max_calls=max_calls)
    run = _load_run(run_id)
    for position, candidate in enumerate(candidates, start=1):
        cfg = resolve_model_config(candidate.get("config") or {})
        from source.settings import build_chat_completion_kwargs
        kwargs = build_chat_completion_kwargs(cfg, [{"role": "user", "content": "ping"}])
        actual_params = {key: kwargs.get(key) for key in
                         ("temperature", "max_tokens", "thinking_effort", "reasoning_effort")}
        add_benchmark_candidate(
            run_id, position,
            str(candidate.get("label") or cfg.get("model", "")),
            cfg, config_hash(cfg), {key: value for key, value in actual_params.items() if value is not None},
        )
    try:
        _execute_run(run_id, progress_callback=progress_callback)
        set_run_status(run_id, "completed")
    except _runner.BudgetExceeded:
        set_run_status(run_id, "interrupted")
    except Exception as exc:
        logger.error(f"benchmark run {run_id} failed: {exc}")
        set_run_status(run_id, "error")
    return run_id


def resume_run(run_id, progress_callback=None):
    """恢复一次中断/失败的运行：跳过已保存的响应，继续未完成部分。"""
    run = _load_run(run_id)
    if run.get("status") not in ("interrupted", "error", "running"):
        raise BenchmarkError(f"运行状态为 {run.get('status')}，不可恢复")
    from source.storage.benchmark import set_run_status
    set_run_status(run_id, "running")
    try:
        _execute_run(run_id, progress_callback=progress_callback)
        set_run_status(run_id, "completed")
    except _runner.BudgetExceeded:
        set_run_status(run_id, "interrupted")
    except Exception as exc:
        logger.error(f"benchmark run {run_id} resume failed: {exc}")
        set_run_status(run_id, "error")
    return run_id


def _execute_run(run_id, progress_callback=None):
    """执行被测调用（深度阅读 + 三轮交流），跳过已保存响应（输出复用）。"""
    from source.storage.benchmark import (
        get_existing_response,
        list_run_candidates,
        list_suite_cases,
        list_suite_papers,
        save_benchmark_response,
    )
    run = _load_run(run_id)
    suite = run["suite"]
    papers = list_suite_papers(run["suite_id"])
    accepted_cases = [c for c in list_suite_cases(run["suite_id"]) if c["status"] == "accepted"]
    candidates = list_run_candidates(run_id)
    max_calls = run.get("max_calls")
    calls_made = 0
    total_expected = len(papers) * len(candidates) * run["repeats"] * (1 + CHAT_ROUNDS)

    def _progress(phase, message, done):
        if progress_callback:
            progress_callback({
                "current": done, "total": total_expected, "phase": phase, "message": message,
            })

    def _charge_budget():
        nonlocal calls_made
        if max_calls and calls_made >= max_calls:
            raise _runner.BudgetExceeded(f"调用数达到硬预算 {max_calls}")

    def _maybe_run(key, runner_fn):
        nonlocal calls_made
        candidate_id, paper_id, track, repeat_index, round_index = key
        existing = get_existing_response(
            candidate_id, paper_id, track, repeat_index, round_index
        )
        if existing is not None:
            return existing
        _charge_budget()
        response, calls = runner_fn()
        calls_made += calls
        response_id = save_benchmark_response(
            run_id, candidate_id, paper_id, track, repeat_index, round_index,
            response["prompt_snapshot"], response["raw_output"], response["parsed"],
            response["status"], response["finish_reason"], response["usage_json"],
            response["latency_ms"], response["continuation_count"],
        )
        _progress(track, f"已保存 {track} 响应", calls_made)
        return response_id

    def _chat_step(suite, paper, cfg, paper_ref, chat_cases, round_index, candidate_id,
                   repeat_index):
        from source.storage.benchmark import get_response
        history = []
        for previous in range(1, round_index):
            prior = get_existing_response(candidate_id, paper["id"], "chat", repeat_index, previous)
            if prior is not None:
                prior_response = get_response(prior)
                if prior_response:
                    history.append((prior_response.get("parsed") or {}).get("answer", ""))
        return _runner.run_chat_round(suite, paper, cfg, paper_ref, chat_cases, history)

    chat_cases_by_paper = {}
    for case in accepted_cases:
        if case["track"] == "chat":
            chat_cases_by_paper.setdefault(case["paper_ref_id"], []).append(case)

    for paper in papers:
        chat_cases = sorted(chat_cases_by_paper.get(paper["id"], []), key=lambda c: c["position"])
        paper_ref = {"paper_id": paper.get("paper_id"), "arxiv_id": paper.get("arxiv_id", "")}
        for candidate in candidates:
            cfg = resolve_model_config({
                "provider_key": candidate["provider_key"],
                "model": candidate["model"],
                "is_thinking": candidate["is_thinking"],
                "thinking_effort": candidate["thinking_effort"],
                "temperature_enabled": candidate["temperature_enabled"],
                "temperature": candidate["temperature"],
                "max_tokens_enabled": candidate["max_tokens_enabled"],
                "max_tokens": candidate["max_tokens"],
            })
            for repeat_index in range(run["repeats"]):
                _maybe_run(
                    (candidate["id"], paper["id"], "deep_reading", repeat_index, None),
                    lambda: _runner.run_deep_reading(suite, paper, cfg, paper_ref),
                )
                for round_index in range(1, CHAT_ROUNDS + 1):
                    _maybe_run(
                        (candidate["id"], paper["id"], "chat", repeat_index, round_index),
                        lambda round_index=round_index: _chat_step(
                            suite, paper, cfg, paper_ref, chat_cases, round_index,
                            candidate["id"], repeat_index,
                        ),
                    )
    if progress_callback:
        progress_callback({
            "current": total_expected, "total": total_expected, "phase": "judge",
            "message": "被测调用完成，开始裁判评分",
        })
    _judge.judge_run(_load_run(run_id))
    if progress_callback:
        progress_callback({
            "current": total_expected, "total": total_expected, "phase": "judge",
            "message": "裁判与复核完成", "status": "completed",
        })


def get_run(run_id):
    return _load_run(run_id)


def get_report(run_id):
    """生成运行报告；报告随运行的响应/判定变化重算，不覆盖历史裁判记录。"""
    run = _load_run(run_id)
    return _report.build_report(run)


def set_human_judgment(run_id, response_id, case_id, score, notes=""):
    _judge.set_human_judgment(run_id, response_id, case_id, score, notes=notes)


__all__ = [
    "BenchmarkError",
    "CHAT_ROUNDS",
    "create_draft",
    "generate_cases",
    "list_cases",
    "review_case",
    "review_all_cases",
    "freeze_suite",
    "list_suites",
    "get_suite",
    "list_selectable_papers",
    "estimate_calls",
    "start_run",
    "resume_run",
    "get_run",
    "get_report",
    "set_human_judgment",
    "get_route_configs",
    "save_route_config",
    "resolve_model_config",
    "EXTRACTOR_VERSION",
]
