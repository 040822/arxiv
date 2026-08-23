"""Private paper reading benchmark (deep module).

对 Web/CLI 调用方只暴露以下接口；模型调用、版本校验、评分聚合与重试细节
隐藏在模块内部。
"""

import json
import logging
import math
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
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
from .config import (
    build_actual_request_params, get_route_configs, resolve_model_config,
    save_route_config,
)
from .evidence import (
    config_hash,
    parse_deep_reading_questions,
    suite_checksum,
    text_sha256,
)

logger = logging.getLogger(__name__)

_WORKER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="benchmark-worker")
_TASKS = {}
_TASKS_LOCK = threading.Lock()

EXTRACTOR_VERSION = "pymupdf-benchmark-v1"
# Persisted on every run created through this public module.  Keep this value
# stable for the lifetime of the benchmark runner; an empty value is reserved
# for historical runs created before runner-version snapshots existed.
RUNNER_VERSION = "pprb-runner-v7"
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


def list_suite_papers(suite_id):
    """列出题库冻结的论文快照，供外部调用方读取。"""
    from source.storage.benchmark import list_suite_papers as _list_suite_papers
    return _list_suite_papers(suite_id)


def list_runs(suite_id=None):
    """列出 Benchmark 运行记录，可按题库筛选。"""
    from source.storage.benchmark import list_benchmark_runs
    return list_benchmark_runs(suite_id)


def mark_interrupted_runs():
    """将进程遗留的 running 运行标记为 interrupted。"""
    from source.storage.benchmark import mark_interrupted_runs as _mark_interrupted_runs
    return _mark_interrupted_runs()


def get_suite(suite_id):
    return _require_suite(suite_id)


def list_selectable_papers(limit=MAX_SELECT_PAPERS):
    """列出可选的业务论文（id/paper_key/title/published_date）。"""
    from source.storage.papers import browse_papers
    rows, _total = browse_papers(limit=min(limit, MAX_SELECT_PAPERS))
    return [
        {
            "id": row.get("id"),
            "paper_key": row.get("paper_key", ""),
            "title": row.get("title", ""),
            "published_date": row.get("published_date", ""),
        }
        for row in rows
    ]


def _new_task(kind, run_id=None):
    task_id = f"benchmark-{kind}-{uuid.uuid4().hex}"
    with _TASKS_LOCK:
        _TASKS[task_id] = {
            "task_id": task_id, "kind": kind, "run_id": run_id,
            "status": "queued", "error": "",
        }
    return task_id


def get_task(task_id):
    """Return a lightweight in-process task status snapshot."""
    with _TASKS_LOCK:
        item = _TASKS.get(str(task_id or ""))
        return dict(item) if item else None


def _set_task(task_id, **fields):
    with _TASKS_LOCK:
        if task_id in _TASKS:
            _TASKS[task_id].update(fields)


def _submit_task(kind, fn, *args, run_id=None, **kwargs):
    task_id = _new_task(kind, run_id=run_id)

    def worker():
        _set_task(task_id, status="running")
        try:
            result = fn(*args, **kwargs)
            _set_task(task_id, status="completed", result=result)
            return result
        except Exception as exc:
            _set_task(task_id, status="error", error=str(exc))
            raise

    _WORKER.submit(worker)
    return task_id


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


def generate_cases(suite_id, progress_callback=None, background=False):
    """对题库全部论文执行出题调用；返回逐论文结果摘要。"""
    if background:
        return submit_generate_cases(suite_id, progress_callback=progress_callback)
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
    from source.storage.benchmark import update_benchmark_suite
    update_benchmark_suite(suite_id, status="review")
    return summaries


def submit_generate_cases(suite_id, progress_callback=None):
    """Queue authoring and return a task identifier immediately."""
    _require_not_frozen(suite_id)
    task_id = _submit_task(
        "generate", generate_cases, suite_id,
        progress_callback=progress_callback,
    )
    return {"task_id": task_id, "status": "queued"}


generate_cases_background = submit_generate_cases


def list_cases(suite_id, track=None, status=None):
    from source.storage.benchmark import list_suite_cases
    return list_suite_cases(suite_id, track=track, status=status)


def review_case(case_id, decision, note="", actor_user_id=None, actor_username=""):
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
    return set_case_review(
        case_id, status, note=note,
        actor_user_id=actor_user_id, actor_username=actor_username,
    )


def review_all_cases(suite_id, decision, note="", actor_user_id=None, actor_username=""):
    """批量审核全部题目（accept/reject）。"""
    suite = _require_not_frozen(suite_id)
    from source.storage.benchmark import set_cases_review
    status = {"accept": "accepted", "reject": "rejected"}.get(decision)
    if not status:
        raise BenchmarkError("decision 必须是 accept/reject")
    set_cases_review(
        suite_id, status, note=note,
        actor_user_id=actor_user_id, actor_username=actor_username,
    )
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
    """估算正常逻辑调用与最坏重试调用（不落库）。

    Candidate work has four logical calls per paper/repeat (one deep-reading
    response plus three chat rounds).  A deep response may consume an initial
    call and a continuation, and every logical call may use two retries, so
    the candidate worst case is 15 attempts per paper/repeat.  Judge/review
    calls use the same three-attempt retry ceiling but never consume the
    candidate budget.
    """
    suite = _require_suite(suite_id)
    paper_count = int(suite.get("paper_count", 0) or 0)
    repeats = max(1, min(5, int(repeats or 1)))
    candidate_count = max(0, int(candidate_count or 0))
    candidate_calls = candidate_count * paper_count * repeats * (1 + CHAT_ROUNDS)
    candidate_worst_calls = candidate_count * paper_count * repeats * (
        3 + 3 + CHAT_ROUNDS * 3
    )
    judge_calls = candidate_count * paper_count * 2 * repeats
    judge_worst_calls = judge_calls * 3
    normal_review_groups_per_candidate = 2 * max(
        1, math.ceil(paper_count * repeats * 0.1)
    )
    worst_review_groups_per_candidate = 2 * paper_count * repeats
    review_calls = candidate_count * normal_review_groups_per_candidate
    review_worst_calls = candidate_count * worst_review_groups_per_candidate * 3
    normal_total_calls = candidate_calls + judge_calls + review_calls
    worst_total_calls = candidate_worst_calls + judge_worst_calls + review_worst_calls
    return {
        "paper_count": paper_count,
        "candidates": candidate_count,
        "repeats": repeats,
        # Compatibility fields retained for the existing estimate UI.
        "measured_calls": candidate_calls,
        "judge_calls": judge_calls,
        "review_calls": review_calls,
        "worst_total_calls": worst_total_calls,
        # Explicit normal/worst breakdown for budget and operator display.
        "candidate_calls": candidate_calls,
        "normal_candidate_calls": candidate_calls,
        "candidate_worst_calls": candidate_worst_calls,
        "worst_candidate_attempts": candidate_worst_calls,
        "judge_worst_calls": judge_worst_calls,
        "normal_judge_calls": judge_calls,
        "worst_judge_attempts": judge_worst_calls,
        "review_worst_calls": review_worst_calls,
        "normal_review_calls": review_calls,
        "worst_review_attempts": review_worst_calls,
        "normal_review_groups": candidate_count * normal_review_groups_per_candidate,
        "worst_review_groups": candidate_count * worst_review_groups_per_candidate,
        "normal_total_calls": normal_total_calls,
        "worst_candidate_calls": candidate_worst_calls,
        "worst_judge_calls": judge_worst_calls,
        "worst_review_calls": review_worst_calls,
        "normal": {
            "candidate_calls": candidate_calls,
            "judge_calls": judge_calls,
            "review_calls": review_calls,
            "total_calls": normal_total_calls,
        },
        "worst": {
            "candidate_calls": candidate_worst_calls,
            "judge_calls": judge_worst_calls,
            "review_calls": review_worst_calls,
            "total_calls": worst_total_calls,
        },
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


def _prepare_run(suite_id, candidates, repeats=1, max_calls=None):
    """Validate and persist a queued run before any worker/API call."""
    suite = _require_suite(suite_id)
    if suite.get("status") != "frozen":
        raise BenchmarkError("题库未冻结，无法运行")
    if not candidates:
        raise BenchmarkError("请至少选择 1 个候选模型")
    repeats = max(1, min(5, int(repeats or 1)))

    if max_calls is not None:
        try:
            max_calls = int(max_calls)
        except (TypeError, ValueError) as exc:
            raise BenchmarkError("max_calls 必须是正整数") from exc
        if max_calls <= 0:
            raise BenchmarkError("max_calls 必须是正整数")

    # Resolve and freeze the actual request payload before creating a run.  A
    # candidate's UI-only fields may differ while the provider receives exactly
    # the same request; such candidates are ambiguous and must be rejected at
    # startup rather than after partial work has been persisted.
    candidate_snapshots = []
    seen_hashes = {}
    for position, candidate in enumerate(candidates, start=1):
        cfg = resolve_model_config(candidate.get("config") or {})
        actual_params = build_actual_request_params(cfg)
        candidate_hash = config_hash(cfg, actual_params)
        duplicate = seen_hashes.get(candidate_hash)
        if duplicate is not None:
            raise BenchmarkError(
                f"候选启动参数重复：第 {duplicate} 个与第 {position} 个候选的实际请求参数相同"
            )
        seen_hashes[candidate_hash] = position
        candidate_snapshots.append((candidate, cfg, actual_params, candidate_hash))

    from source.storage.benchmark import find_reusable_response, list_suite_papers
    suite_papers = list_suite_papers(suite_id)
    required_candidate_calls = 0
    for _candidate, _cfg, _actual_params, candidate_hash in candidate_snapshots:
        for paper in suite_papers:
            for repeat_index in range(repeats):
                slots = [("deep_reading", None)] + [
                    ("chat", round_index) for round_index in range(1, CHAT_ROUNDS + 1)
                ]
                for track, round_index in slots:
                    reusable = find_reusable_response(
                        suite_checksum=suite.get("suite_checksum"),
                        runner_version=RUNNER_VERSION,
                        config_hash=candidate_hash,
                        paper_key=paper.get("paper_key"), track=track,
                        repeat_index=repeat_index, round_index=round_index,
                    )
                    if reusable is None or reusable.get("status") == "retry_failed":
                        required_candidate_calls += 1
    if max_calls is not None and max_calls < required_candidate_calls:
        raise BenchmarkError(
            f"候选调用预算不足：至少需要 {required_candidate_calls} 次，当前仅 {max_calls} 次"
        )

    from source.storage.benchmark import add_benchmark_candidate, create_benchmark_run
    run_id = create_benchmark_run(
        suite_id,
        repeats=repeats,
        max_calls=max_calls,
        runner_version=RUNNER_VERSION,
        status="queued",
    )
    for position, (candidate, cfg, actual_params, candidate_hash) in enumerate(
        candidate_snapshots, start=1
    ):
        add_benchmark_candidate(
            run_id, position,
            str(candidate.get("label") or cfg.get("model", "")),
            cfg, candidate_hash, actual_params,
        )
    return run_id


def _run_claimed(run_id, progress_callback=None, propagate_errors=False):
    from source.storage.benchmark import claim_benchmark_run, set_run_status

    if not claim_benchmark_run(run_id):
        return run_id
    try:
        _execute_run(run_id, progress_callback=progress_callback)
        set_run_status(run_id, "completed")
    except _runner.BudgetExceeded:
        set_run_status(run_id, "interrupted")
        if propagate_errors:
            raise
    except Exception as exc:
        logger.error(f"benchmark run {run_id} failed: {exc}")
        set_run_status(run_id, "error")
        if propagate_errors:
            raise
    return run_id


def start_run(suite_id, candidates, repeats=1, max_calls=None, progress_callback=None,
              background=False):
    """Run synchronously for CLI/backward compatibility and return run id.

    Web/background callers should use :func:`submit_start_run`; keeping this
    small synchronous facade preserves the established source API for scripts
    and tests while sharing exactly the same queued/claimed worker path.
    """
    if background:
        return submit_start_run(
            suite_id, candidates, repeats=repeats, max_calls=max_calls,
            progress_callback=progress_callback,
        )
    run_id = _prepare_run(suite_id, candidates, repeats=repeats, max_calls=max_calls)
    return _run_claimed(run_id, progress_callback=progress_callback)


def submit_start_run(suite_id, candidates, repeats=1, max_calls=None,
                     progress_callback=None):
    """Queue a run and return ``{task_id, run_id, status}`` immediately."""
    run_id = _prepare_run(suite_id, candidates, repeats=repeats, max_calls=max_calls)
    task_id = _submit_task(
        "run", _run_claimed, run_id,
        progress_callback=progress_callback, propagate_errors=True, run_id=run_id,
    )
    return {"task_id": task_id, "run_id": run_id, "status": "queued"}


start_run_background = submit_start_run


def resume_run(run_id, max_calls=None, progress_callback=None, background=False):
    """恢复一次中断/失败的运行：跳过已保存的响应，继续未完成部分。"""
    if background:
        return submit_resume_run(
            run_id, max_calls=max_calls, progress_callback=progress_callback,
        )
    run = _load_run(run_id)
    if run.get("status") not in ("interrupted", "error", "running"):
        raise BenchmarkError(f"运行状态为 {run.get('status')}，不可恢复")
    current_max_calls = run.get("max_calls")
    calls_made = int(run.get("candidate_calls_made") or 0)
    from source.storage.benchmark import set_run_status, update_run_max_calls
    if max_calls is not None:
        try:
            requested_max_calls = int(max_calls)
        except (TypeError, ValueError) as exc:
            raise BenchmarkError("max_calls 必须是正整数") from exc
        if requested_max_calls <= 0:
            raise BenchmarkError("max_calls 必须是正整数")
        if current_max_calls is None or requested_max_calls <= int(current_max_calls):
            raise BenchmarkError("恢复时 max_calls 只能严格上调")
        update_run_max_calls(run_id, requested_max_calls)
    elif current_max_calls is not None and calls_made >= int(current_max_calls):
        raise BenchmarkError("候选调用预算已耗尽；恢复前必须严格上调 max_calls")
    set_run_status(run_id, "queued")
    return _run_claimed(run_id, progress_callback=progress_callback)


def submit_resume_run(run_id, max_calls=None, progress_callback=None):
    """Queue a recoverable run and return its task/run identifiers."""
    run = _load_run(run_id)
    if run.get("status") not in ("interrupted", "error", "running"):
        raise BenchmarkError(f"运行状态为 {run.get('status')}，不可恢复")
    current_max_calls = run.get("max_calls")
    calls_made = int(run.get("candidate_calls_made") or 0)
    from source.storage.benchmark import set_run_status, update_run_max_calls
    if max_calls is not None:
        try:
            requested_max_calls = int(max_calls)
        except (TypeError, ValueError) as exc:
            raise BenchmarkError("max_calls 必须是正整数") from exc
        if requested_max_calls <= 0:
            raise BenchmarkError("max_calls 必须是正整数")
        if current_max_calls is None or requested_max_calls <= int(current_max_calls):
            raise BenchmarkError("恢复时 max_calls 只能严格上调")
        update_run_max_calls(run_id, requested_max_calls)
    elif current_max_calls is not None and calls_made >= int(current_max_calls):
        raise BenchmarkError("候选调用预算已耗尽；恢复前必须严格上调 max_calls")
    set_run_status(run_id, "queued")
    task_id = _submit_task(
        "resume", _run_claimed, run_id,
        progress_callback=progress_callback, propagate_errors=True, run_id=run_id,
    )
    return {"task_id": task_id, "run_id": run_id, "status": "queued"}


resume_run_background = submit_resume_run


def _execute_run(run_id, progress_callback=None):
    """执行被测调用（深度阅读 + 三轮交流），跳过已保存响应（输出复用）。"""
    from source.storage.benchmark import (
        consume_candidate_call,
        get_existing_response,
        get_response,
        find_reusable_response,
        clone_reusable_response,
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
    total_expected = len(papers) * len(candidates) * run["repeats"] * (1 + CHAT_ROUNDS)

    def _progress(phase, message, done):
        if progress_callback:
            progress_callback({
                "current": done, "total": total_expected, "phase": phase, "message": message,
            })

    def _charge_budget():
        if not consume_candidate_call(run_id):
            max_calls = run.get("max_calls")
            raise _runner.BudgetExceeded(f"调用数达到硬预算 {max_calls}")

    def _maybe_run(key, runner_fn):
        candidate_id, paper_id, track, repeat_index, round_index = key
        existing = get_existing_response(
            candidate_id, paper_id, track, repeat_index, round_index
        )
        if existing is not None:
            saved = get_response(existing)
            if saved and saved.get("status") != "retry_failed":
                return existing

        # Cross-run reuse is keyed only by frozen content, runner version and
        # the exact candidate request hash.  It deliberately happens before
        # budget reservation and therefore cannot consume the target budget.
        candidate = next((item for item in candidates if item["id"] == candidate_id), None)
        paper = next((item for item in papers if item["id"] == paper_id), None)
        if candidate and paper:
            reusable = find_reusable_response(
                suite_checksum=suite.get("suite_checksum"),
                runner_version=run.get("runner_version") or RUNNER_VERSION,
                config_hash=candidate.get("config_hash"),
                paper_key=paper.get("paper_key"), track=track,
                repeat_index=repeat_index, round_index=round_index,
            )
            if reusable and reusable.get("status") != "retry_failed":
                return clone_reusable_response(
                    reusable["id"], run_id, candidate_id, paper_id,
                )
        response, calls = runner_fn()
        if existing is not None:
            # The v7 storage API intentionally keeps response slots unique.
            # A resumed retry_failed slot is the one exceptional case where
            # the slot must be replaced after a successful retry; preserve its
            # original id so judgments/provenance remain stable.
            from source.storage.benchmark import replace_retry_failed_response
            replace_retry_failed_response(existing, run_id, response)
            response_id = existing
        else:
            response_id = save_benchmark_response(
                run_id, candidate_id, paper_id, track, repeat_index, round_index,
                response["prompt_snapshot"], response["raw_output"], response["parsed"],
                response["status"], response["finish_reason"], response["usage_json"],
                response["latency_ms"], response.get("continuation_count", 0),
                retry_count=response.get("retry_count", 0),
                error_json=response.get("error_json") or {},
            )
        from source.storage.benchmark import get_benchmark_run
        calls_made = int((get_benchmark_run(run_id) or {}).get("candidate_calls_made") or 0)
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
                if prior_response and prior_response.get("status") != "retry_failed":
                    history.append((prior_response.get("parsed") or {}).get("answer", ""))
        return _runner.run_chat_round(
            suite, paper, cfg, paper_ref, chat_cases, history,
            charge_callback=_charge_budget,
        )

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
                    lambda: _runner.run_deep_reading(
                        suite, paper, cfg, paper_ref, charge_callback=_charge_budget,
                    ),
                )
                for round_index in range(1, CHAT_ROUNDS + 1):
                    _maybe_run(
                        (candidate["id"], paper["id"], "chat", repeat_index, round_index),
                        lambda round_index=round_index: _chat_step(
                            suite, paper, cfg, paper_ref, chat_cases, round_index,
                            candidate["id"], repeat_index,
                        ),
                    )
    failed_responses = [
        response for response in _load_run(run_id)["responses"]
        if response.get("status") == "retry_failed"
    ]
    if failed_responses:
        raise RuntimeError(
            f"{len(failed_responses)} 个候选逻辑调用临时失败，等待提高预算或恢复后重试"
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
    report = _report.build_report(run)
    if not str(run.get("runner_version") or "").strip():
        report.setdefault("warnings", []).append(
            "历史运行缺少 runner_version，无法确认执行器版本"
        )
    return report


def rejudge_run(run_id, progress_callback=None, background=False):
    """Judge saved candidate outputs under a new route/prompt revision only."""
    if background:
        return submit_rejudge_run(run_id, progress_callback=progress_callback)
    run = _load_run(run_id)
    if run.get("status") not in ("completed", "interrupted", "error"):
        raise BenchmarkError(f"运行状态为 {run.get('status')}，当前不可重判")
    if not run.get("responses"):
        raise BenchmarkError("运行没有可重判的候选输出")
    if progress_callback:
        progress_callback({"phase": "judge", "status": "running", "message": "开始重新裁判"})
    result = _judge.judge_run(run)
    if progress_callback:
        progress_callback({"phase": "judge", "status": "completed", "message": "重新裁判完成"})
    return {"run_id": run_id, **(result or {})}


def submit_rejudge_run(run_id, progress_callback=None):
    """Queue a judge-only task; candidate responses and budget are untouched."""
    run = _load_run(run_id)
    if run.get("status") not in ("completed", "interrupted", "error"):
        raise BenchmarkError(f"运行状态为 {run.get('status')}，当前不可重判")
    task_id = _submit_task(
        "rejudge", rejudge_run, run_id,
        progress_callback=progress_callback, run_id=run_id,
    )
    return {"task_id": task_id, "run_id": run_id, "status": "queued"}


rejudge_run_background = submit_rejudge_run


def edit_case(case_id, fields, actor_user_id=None, actor_username="", note="",
              decision=None):
    """Edit and revalidate a pre-freeze case, optionally accepting atomically."""
    from source.storage.benchmark import get_benchmark_case, get_suite_paper, update_benchmark_case

    case = get_benchmark_case(case_id)
    if not case:
        raise BenchmarkError("题目不存在")
    suite = _require_not_frozen(case["suite_id"])
    allowed = {"question", "reference_answer", "evidence", "rubric", "requires_reject"}
    unknown = set(fields or {}) - allowed
    if unknown:
        raise BenchmarkError("题目编辑包含不允许的字段")
    paper = get_suite_paper(case["paper_ref_id"])
    if not paper:
        raise BenchmarkError("题目论文快照不存在")
    candidate = {**case, **dict(fields or {})}
    evidence = candidate.get("evidence") or []
    if not isinstance(evidence, list) or not evidence:
        raise BenchmarkError("证据不能为空")
    from .author import _validate_rubric
    from .evidence import check_evidence
    if not all(check_evidence([fragment], paper.get("full_text", "")).get(fragment) for fragment in evidence):
        raise BenchmarkError("证据必须精确匹配冻结全文")
    if not _validate_rubric(candidate.get("rubric")):
        raise BenchmarkError("rubric 缺少可判定条件或权重不合法")
    updated = update_benchmark_case(
        case_id, fields=fields, actor_user_id=actor_user_id,
        actor_username=actor_username, note=note,
    )
    if decision is not None:
        decision = str(decision).strip()
        if decision not in {"accept", "reject", "pending"}:
            raise BenchmarkError("decision 必须是 accept/reject/pending")
        updated = review_case(
            case_id, decision, note=note,
            actor_user_id=actor_user_id, actor_username=actor_username,
        )
    return updated


def list_case_revisions(case_id):
    from source.storage.benchmark import list_benchmark_case_revisions
    return list_benchmark_case_revisions(case_id)


def list_scoring_revisions(run_id):
    from source.storage.benchmark import list_benchmark_scoring_revisions
    return list_benchmark_scoring_revisions(run_id)


def set_human_judgment(run_id, response_id, case_id, condition_scores, notes="",
                       actor_user_id=None, actor_username="",
                       hallucination_critical=False):
    _judge.set_human_judgment(
        run_id, response_id, case_id, condition_scores, notes=notes,
        hallucination_critical=hallucination_critical,
        actor_user_id=actor_user_id, actor_username=actor_username,
    )


__all__ = [
    "BenchmarkError",
    "CHAT_ROUNDS",
    "create_draft",
    "generate_cases",
    "submit_generate_cases",
    "list_cases",
    "review_case",
    "review_all_cases",
    "freeze_suite",
    "list_suites",
    "list_suite_papers",
    "list_runs",
    "mark_interrupted_runs",
    "get_suite",
    "list_selectable_papers",
    "estimate_calls",
    "start_run",
    "submit_start_run",
    "start_run_background",
    "resume_run",
    "submit_resume_run",
    "resume_run_background",
    "rejudge_run",
    "submit_rejudge_run",
    "rejudge_run_background",
    "edit_case",
    "list_case_revisions",
    "list_scoring_revisions",
    "get_task",
    "get_run",
    "get_report",
    "set_human_judgment",
    "get_route_configs",
    "save_route_config",
    "resolve_model_config",
    "EXTRACTOR_VERSION",
    "RUNNER_VERSION",
]
