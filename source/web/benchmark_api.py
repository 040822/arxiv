"""Flask benchmark routes: admin management page + JSON API (admin-only)."""

import json
import logging

from flask import Blueprint, Response, jsonify, render_template, request

from source.benchmark import (
    BenchmarkError,
    create_draft,
    estimate_calls,
    freeze_suite,
    generate_cases,
    get_report,
    get_run,
    get_route_configs,
    get_suite,
    list_cases,
    list_selectable_papers,
    list_suites,
    resume_run,
    review_all_cases,
    review_case,
    save_route_config,
    set_human_judgment,
    start_run,
)
from source.settings import get_all_providers

from .progress import get_progress, update_progress

logger = logging.getLogger(__name__)

bp = Blueprint("benchmark", __name__)


def _error(message, status=400):
    return jsonify({"status": "error", "message": str(message)}), status


def _progress_callback(task_id, phase_label):
    def callback(data):
        update_progress(task_id, {"phase": phase_label, **data})
    return callback


@bp.route("/benchmark")
def benchmark_page():
    """Benchmark 管理页（仅 admin 可访问，路由策略默认 admin）。"""
    return render_template("benchmark.html")


@bp.route("/api/benchmark/suites")
def api_benchmark_suites():
    """题库列表。"""
    return jsonify({"status": "ok", "suites": list_suites()})


@bp.route("/api/benchmark/papers")
def api_benchmark_papers():
    """可选论文列表（用于建题库）。"""
    query = (request.args.get("q") or "").strip().lower()
    papers = list_selectable_papers()
    if query:
        papers = [
            paper for paper in papers
            if query in str(paper.get("title") or "").lower()
            or query in str(paper.get("paper_key") or "").lower()
        ]
    return jsonify({"status": "ok", "papers": papers[:50]})


@bp.route("/api/benchmark/drafts", methods=["POST"])
def api_benchmark_create_draft():
    """创建题库草稿：{subset_name, paper_keys: []}"""
    data = request.get_json(silent=True) or {}
    try:
        suite, failures = create_draft(
            str(data.get("subset_name") or "").strip(),
            [str(key).strip() for key in (data.get("paper_keys") or []) if str(key).strip()],
        )
        return jsonify({
            "status": "ok", "suite": suite,
            "failures": failures,
            "message": f"题库已创建：{suite['version_label']}（{suite['paper_count']} 篇论文）",
        })
    except (BenchmarkError, ValueError) as exc:
        return _error(exc)


@bp.route("/api/benchmark/suites/<int:suite_id>")
def api_benchmark_suite(suite_id):
    """题库详情（元数据 + 论文 + 题目 + 运行记录）。"""
    try:
        suite = get_suite(suite_id)
        suite["papers"] = _list_suite_papers(suite_id)
        suite["cases"] = list_cases(suite_id)
        runs = _list_runs(suite_id)
        for run in runs:
            run["candidate_count"] = len(run.get("candidates") or [])
        suite["runs"] = runs
        return jsonify({"status": "ok", "suite": suite})
    except BenchmarkError as exc:
        return _error(exc, 404)


def _list_suite_papers(suite_id):
    from source.storage.benchmark import list_suite_papers
    return list_suite_papers(suite_id)


def _list_runs(suite_id):
    from source.storage.benchmark import list_benchmark_runs
    return list_benchmark_runs(suite_id)


@bp.route("/api/benchmark/suites/<int:suite_id>/generate", methods=["POST"])
def api_benchmark_generate_cases(suite_id):
    """对题库全部论文执行出题调用。"""
    task_id = request.args.get("task_id", f"benchmark-generate-{suite_id}")
    try:
        update_progress(task_id, {"status": "running", "message": "开始出题...", "phase": "author"})
        summaries = generate_cases(
            suite_id, progress_callback=_progress_callback(task_id, "author")
        )
        ok_count = sum(
            1 for paper in summaries
            for item in paper.get("deep_reading", []) + paper.get("chat", [])
            if item.get("ok")
        )
        rejected_count = sum(
            1 for paper in summaries
            for item in paper.get("deep_reading", []) + paper.get("chat", [])
            if not item.get("ok")
        )
        errors = [paper for paper in summaries if paper.get("error")]
        update_progress(task_id, {"status": "completed", "message": "出题完成"})
        return jsonify({
            "status": "ok",
            "summaries": summaries,
            "ok_cases": ok_count,
            "rejected_cases": rejected_count,
            "errors": errors,
            "message": f"出题完成：{ok_count} 道通过校验，{rejected_count} 道被自动驳回"
                       + (f"，{len(errors)} 篇论文调用失败" if errors else ""),
        })
    except BenchmarkError as exc:
        update_progress(task_id, {"status": "error", "message": str(exc)})
        return _error(exc)


@bp.route("/api/benchmark/cases/<int:case_id>/review", methods=["POST"])
def api_benchmark_review_case(case_id):
    """人工审核单个题目：{decision: accept/reject/pending, note}"""
    data = request.get_json(silent=True) or {}
    try:
        case = review_case(case_id, data.get("decision", ""), note=data.get("note", ""))
        return jsonify({"status": "ok", "case": case, "message": "审核已保存"})
    except BenchmarkError as exc:
        return _error(exc)


@bp.route("/api/benchmark/suites/<int:suite_id>/review-all", methods=["POST"])
def api_benchmark_review_all(suite_id):
    """批量审核全部题目：{decision: accept/reject, note}"""
    data = request.get_json(silent=True) or {}
    try:
        suite = review_all_cases(suite_id, data.get("decision", ""), note=data.get("note", ""))
        return jsonify({"status": "ok", "suite": suite, "message": "批量审核完成"})
    except BenchmarkError as exc:
        return _error(exc)


@bp.route("/api/benchmark/suites/<int:suite_id>/freeze", methods=["POST"])
def api_benchmark_freeze(suite_id):
    """冻结题库（校验全文、证据与人工确认）。"""
    try:
        suite = freeze_suite(suite_id)
        return jsonify({
            "status": "ok", "suite": suite,
            "message": f"题库已冻结：{suite['version_label']}（校验和 {suite['suite_checksum'][:12]}…）",
        })
    except BenchmarkError as exc:
        return _error(exc)


@bp.route("/api/benchmark/suites/<int:suite_id>/estimate")
def api_benchmark_estimate(suite_id):
    """运行前调用量估算：?candidates=&repeats=&max_calls="""
    try:
        candidates = max(1, request.args.get("candidates", 1, type=int))
        repeats = max(1, min(5, request.args.get("repeats", 1, type=int)))
        estimate = estimate_calls(suite_id, candidates, repeats)
        return jsonify({"status": "ok", "estimate": estimate})
    except BenchmarkError as exc:
        return _error(exc)


@bp.route("/api/benchmark/suites/<int:suite_id>/runs", methods=["POST"])
def api_benchmark_start_run(suite_id):
    """启动一次评测运行：{candidates: [{label, config}], repeats, max_calls}"""
    data = request.get_json(silent=True) or {}
    task_id = request.args.get("task_id", f"benchmark-run-{suite_id}")
    try:
        candidates = []
        for item in data.get("candidates") or []:
            config = dict(item.get("config") or {})
            config = {
                key: config[key]
                for key in ("provider_key", "model", "is_thinking", "thinking_effort",
                            "temperature_enabled", "temperature",
                            "max_tokens_enabled", "max_tokens")
                if key in config
            }
            candidates.append({"label": str(item.get("label") or ""), "config": config})
        if not candidates:
            return _error("请至少选择 1 个候选模型")
        update_progress(task_id, {"status": "running", "message": "评测运行开始...", "phase": "run"})
        run_id = start_run(
            suite_id, candidates,
            repeats=data.get("repeats", 1),
            max_calls=data.get("max_calls"),
            progress_callback=_progress_callback(task_id, "run"),
        )
        run = get_run(run_id)
        update_progress(task_id, {"status": "completed", "message": "评测运行结束"})
        return jsonify({"status": "ok", "run_id": run_id, "run": run})
    except (BenchmarkError, ValueError) as exc:
        update_progress(task_id, {"status": "error", "message": str(exc)})
        return _error(exc)


@bp.route("/api/benchmark/runs/<int:run_id>")
def api_benchmark_run(run_id):
    """运行详情。"""
    try:
        return jsonify({"status": "ok", "run": get_run(run_id)})
    except BenchmarkError as exc:
        return _error(exc, 404)


@bp.route("/api/benchmark/runs/<int:run_id>/resume", methods=["POST"])
def api_benchmark_resume_run(run_id):
    """恢复中断/失败的运行。"""
    task_id = request.args.get("task_id", f"benchmark-resume-{run_id}")
    try:
        update_progress(task_id, {"status": "running", "message": "恢复运行...", "phase": "run"})
        resume_run(run_id, progress_callback=_progress_callback(task_id, "run"))
        update_progress(task_id, {"status": "completed", "message": "运行已恢复并完成"})
        return jsonify({"status": "ok", "run": get_run(run_id), "message": "运行已恢复并完成"})
    except (BenchmarkError, ValueError) as exc:
        update_progress(task_id, {"status": "error", "message": str(exc)})
        return _error(exc)


@bp.route("/api/benchmark/runs/<int:run_id>/report")
def api_benchmark_report(run_id):
    """运行报告（双轨分榜、逐题明细、成本与裁判状态）。"""
    try:
        return jsonify({"status": "ok", "report": get_report(run_id)})
    except BenchmarkError as exc:
        return _error(exc, 404)


@bp.route("/api/benchmark/judgments", methods=["POST"])
def api_benchmark_human_judgment():
    """人工覆盖裁判判定：{run_id, response_id, case_id, score, notes}"""
    data = request.get_json(silent=True) or {}
    try:
        set_human_judgment(
            int(data["run_id"]), int(data["response_id"]), int(data["case_id"]),
            data.get("score", 0), notes=data.get("notes", ""),
        )
        return jsonify({"status": "ok", "message": "人工判定已保存（优先级最高）"})
    except (BenchmarkError, ValueError, KeyError, TypeError) as exc:
        return _error(exc)


@bp.route("/api/benchmark/routes")
def api_benchmark_routes():
    """benchmark 任务路由配置（不含任何凭据）。"""
    providers = {}
    for key, provider in (get_all_providers() or {}).items():
        providers[key] = {
            "name": provider.get("name", key),
            "available_models": provider.get("available_models") or [],
        }
    return jsonify({
        "status": "ok",
        "routes": get_route_configs(),
        "providers": providers,
    })


@bp.route("/api/benchmark/routes/<task_key>", methods=["POST"])
def api_benchmark_save_route(task_key):
    """保存单个 benchmark 任务路由配置。"""
    data = request.get_json(silent=True) or {}
    try:
        config = save_route_config(task_key, data.get("config") or {})
        return jsonify({"status": "ok", "config": config, "message": "路由已保存"})
    except ValueError as exc:
        return _error(exc)


@bp.route("/api/benchmark/progress/<task_id>")
def api_benchmark_progress(task_id):
    """Benchmark 任务进度 SSE。"""
    from .auth import current_user

    user_id = current_user()["id"]

    def generate():
        last_data = None
        while True:
            data = get_progress(task_id, user_id=user_id)
            if data and data != last_data:
                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                last_data = data
                if data.get("status") in ("completed", "error"):
                    break
            import time
            time.sleep(0.5)

    return Response(generate(), mimetype="text/event-stream")
