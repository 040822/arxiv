"""Flask benchmark routes: admin management page + JSON API (admin-only)."""

import json
import logging
import time
import uuid

from flask import Blueprint, Response, jsonify, request

from source.benchmark import (
    BenchmarkError,
    create_draft,
    estimate_calls,
    edit_case,
    freeze_suite,
    get_task,
    get_report,
    get_run,
    get_route_configs,
    get_suite,
    list_cases,
    list_runs,
    list_selectable_papers,
    list_suite_papers,
    list_suites,
    review_all_cases,
    review_case,
    save_route_config,
    set_human_judgment,
    submit_generate_cases,
    submit_rejudge_run,
    submit_resume_run,
    submit_start_run,
)
from source.settings import get_all_providers

from .auth import current_user
from .progress import get_progress, update_progress

logger = logging.getLogger(__name__)

bp = Blueprint("benchmark", __name__)


def _error(message, status=400):
    return jsonify({"status": "error", "message": str(message)}), status


def _safe_server_error(message):
    """Return a stable public error while keeping storage/provider details private."""
    logger.exception("benchmark web operation failed: %s", message)
    return _error(message, 500)


def _actor_snapshot():
    try:
        actor = current_user() or {}
    except RuntimeError:
        # Direct unit calls may not have a Flask request context.  Production
        # requests are protected by the admin route policy and always carry it.
        actor = {}
    except Exception:
        logger.exception("benchmark actor lookup failed")
        actor = {}
    return actor.get("id"), str(actor.get("username") or "")


def _new_task_id(kind, resource_id):
    return f"benchmark-{kind}-{resource_id}-{uuid.uuid4().hex}"


def _progress_callback(task_id, phase_label, user_id):
    def callback(data):
        update_progress(task_id, {"phase": phase_label, **data}, user_id=user_id)
    return callback


def _queue_response(result, web_task_id, user_id, message):
    """Expose a Web-owned progress id and retain the core task id privately."""
    result = dict(result or {})
    core_task_id = result.get("task_id")
    core_task = get_task(core_task_id) if core_task_id else None
    task_status = (core_task or {}).get("status")
    status = (
        task_status if task_status in {"completed", "error"}
        else (result.get("status") or "queued")
    )
    payload = {
        **result,
        "status": "ok",
        "task_status": status,
        "task_id": web_task_id,
        "job_task_id": core_task_id,
        "message": message,
    }
    update_progress(
        web_task_id,
        {
            "status": status,
            "message": (
                "Benchmark 后台任务失败" if status == "error"
                else "Benchmark 后台任务完成" if status == "completed"
                else message
            ),
            "job_task_id": core_task_id,
        },
        user_id=user_id,
    )
    return jsonify(payload), 202


def _current_task_state(data):
    """Merge core task terminal state so SSE closes even without a callback."""
    state = dict(data or {})
    state.pop("error", None)
    if state.get("status") == "error":
        state["message"] = "Benchmark 后台任务失败"
    core_task_id = state.get("job_task_id")
    if not core_task_id:
        return state
    task = get_task(core_task_id)
    if not task:
        return state
    task_status = task.get("status")
    if task_status == "error":
        state.update({
            "status": "error",
            "message": "Benchmark 后台任务失败",
        })
    elif task_status == "completed":
        state.update({
            "status": "completed",
            "message": state.get("message") or "Benchmark 后台任务完成",
        })
    elif task_status == "running" and state.get("status") == "queued":
        state["status"] = "running"
    return state


@bp.route("/api/benchmark/suites")
def api_benchmark_suites():
    """题库列表。"""
    try:
        return jsonify({"status": "ok", "suites": list_suites(), "message": "题库列表已加载"})
    except Exception:
        return _safe_server_error("题库列表暂时不可用")


@bp.route("/api/benchmark/papers")
def api_benchmark_papers():
    """可选论文列表（用于建题库）。"""
    query = (request.args.get("q") or "").strip().lower()
    try:
        papers = list_selectable_papers()
        if query:
            papers = [
                paper for paper in papers
                if query in str(paper.get("title") or "").lower()
                or query in str(paper.get("paper_key") or "").lower()
            ]
        return jsonify({
            "status": "ok", "papers": papers[:50], "message": "论文列表已加载",
        })
    except BenchmarkError as exc:
        return _error(exc)
    except Exception:
        return _safe_server_error("论文列表暂时不可用")


@bp.route("/api/benchmark/drafts", methods=["POST"])
def api_benchmark_create_draft():
    """创建题库草稿：{subset_name, paper_keys: []}"""
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
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
    except Exception:
        return _safe_server_error("题库草稿暂时不可用")


@bp.route("/api/benchmark/suites/<int:suite_id>")
def api_benchmark_suite(suite_id):
    """题库详情（元数据 + 论文 + 题目 + 运行记录）。"""
    try:
        suite = get_suite(suite_id)
        suite["papers"] = list_suite_papers(suite_id)
        suite["cases"] = list_cases(suite_id)
        runs = list_runs(suite_id)
        for run in runs:
            run["candidate_count"] = len(run.get("candidates") or [])
        suite["runs"] = runs
        return jsonify({"status": "ok", "suite": suite, "message": "题库详情已加载"})
    except BenchmarkError as exc:
        return _error(exc, 404)
    except Exception:
        return _safe_server_error("题库详情暂时不可用")


@bp.route("/api/benchmark/suites/<int:suite_id>/generate", methods=["POST"])
def api_benchmark_generate_cases(suite_id):
    """对题库全部论文执行出题调用。"""
    user_id, _username = _actor_snapshot()
    task_id = _new_task_id("generate", suite_id)
    try:
        update_progress(
            task_id,
            {"status": "queued", "message": "出题任务已排队", "phase": "author"},
            user_id=user_id,
        )
        result = submit_generate_cases(
            suite_id,
            progress_callback=_progress_callback(task_id, "author", user_id),
        )
        return _queue_response(result, task_id, user_id, "出题任务已排队")
    except BenchmarkError as exc:
        update_progress(task_id, {"status": "error", "message": str(exc)}, user_id=user_id)
        return _error(exc)
    except Exception:
        update_progress(
            task_id, {"status": "error", "message": "出题任务提交失败"}, user_id=user_id,
        )
        return _safe_server_error("出题任务暂时不可用")


@bp.route("/api/benchmark/cases/<int:case_id>/review", methods=["POST"])
def api_benchmark_review_case(case_id):
    """人工审核/编辑单个题目：{edits, decision, note}"""
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    actor_user_id, actor_username = _actor_snapshot()
    try:
        edits = data.get("edits") or {}
        decision = data.get("decision")
        note = data.get("note", "")
        if edits:
            case = edit_case(
                case_id,
                edits,
                actor_user_id=actor_user_id,
                actor_username=actor_username,
                note=note,
                decision=decision,
            )
        else:
            case = review_case(
                case_id,
                decision or "",
                note=note,
                actor_user_id=actor_user_id,
                actor_username=actor_username,
            )
        return jsonify({"status": "ok", "case": case, "message": "审核已保存"})
    except BenchmarkError as exc:
        return _error(exc)
    except (TypeError, ValueError) as exc:
        return _error(exc)
    except Exception:
        return _safe_server_error("题目审核暂时不可用")


@bp.route("/api/benchmark/suites/<int:suite_id>/review-all", methods=["POST"])
def api_benchmark_review_all(suite_id):
    """批量审核全部题目：{decision: accept/reject, note}"""
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    actor_user_id, actor_username = _actor_snapshot()
    try:
        suite = review_all_cases(
            suite_id,
            data.get("decision", ""),
            note=data.get("note", ""),
            actor_user_id=actor_user_id,
            actor_username=actor_username,
        )
        return jsonify({"status": "ok", "suite": suite, "message": "批量审核完成"})
    except BenchmarkError as exc:
        return _error(exc)
    except Exception:
        return _safe_server_error("批量审核暂时不可用")


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
    except Exception:
        return _safe_server_error("题库冻结暂时不可用")


@bp.route("/api/benchmark/suites/<int:suite_id>/estimate")
def api_benchmark_estimate(suite_id):
    """运行前调用量估算：?candidates=&repeats=&max_calls="""
    try:
        candidates = max(1, request.args.get("candidates", 1, type=int))
        repeats = max(1, min(5, request.args.get("repeats", 1, type=int)))
        estimate = estimate_calls(suite_id, candidates, repeats)
        return jsonify({"status": "ok", "estimate": estimate, "message": "调用量估算已完成"})
    except BenchmarkError as exc:
        return _error(exc)
    except Exception:
        return _safe_server_error("调用量估算暂时不可用")


@bp.route("/api/benchmark/suites/<int:suite_id>/runs", methods=["POST"])
def api_benchmark_start_run(suite_id):
    """启动一次评测运行：{candidates: [{label, config}], repeats, max_calls}"""
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    user_id, _username = _actor_snapshot()
    task_id = _new_task_id("run", suite_id)
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
        update_progress(
            task_id,
            {"status": "queued", "message": "评测运行已排队", "phase": "run"},
            user_id=user_id,
        )
        result = submit_start_run(
            suite_id,
            candidates,
            repeats=data.get("repeats", 1),
            max_calls=data.get("max_calls"),
            progress_callback=_progress_callback(task_id, "run", user_id),
        )
        return _queue_response(result, task_id, user_id, "评测运行已排队")
    except (BenchmarkError, ValueError) as exc:
        update_progress(task_id, {"status": "error", "message": str(exc)}, user_id=user_id)
        return _error(exc)
    except Exception:
        update_progress(
            task_id, {"status": "error", "message": "评测运行提交失败"}, user_id=user_id,
        )
        return _safe_server_error("评测运行暂时不可用")


@bp.route("/api/benchmark/runs/<int:run_id>")
def api_benchmark_run(run_id):
    """运行详情。"""
    try:
        return jsonify({"status": "ok", "run": get_run(run_id), "message": "运行详情已加载"})
    except BenchmarkError as exc:
        return _error(exc, 404)
    except Exception:
        return _safe_server_error("运行详情暂时不可用")


@bp.route("/api/benchmark/runs/<int:run_id>/resume", methods=["POST"])
def api_benchmark_resume_run(run_id):
    """恢复中断/失败的运行。"""
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    user_id, _username = _actor_snapshot()
    task_id = _new_task_id("resume", run_id)
    try:
        update_progress(
            task_id,
            {"status": "queued", "message": "恢复任务已排队", "phase": "run"},
            user_id=user_id,
        )
        result = submit_resume_run(
            run_id,
            max_calls=data.get("max_calls"),
            progress_callback=_progress_callback(task_id, "run", user_id),
        )
        return _queue_response(result, task_id, user_id, "恢复任务已排队")
    except (BenchmarkError, ValueError) as exc:
        update_progress(task_id, {"status": "error", "message": str(exc)}, user_id=user_id)
        return _error(exc)
    except Exception:
        update_progress(
            task_id, {"status": "error", "message": "恢复任务提交失败"}, user_id=user_id,
        )
        return _safe_server_error("恢复任务暂时不可用")


@bp.route("/api/benchmark/runs/<int:run_id>/report")
def api_benchmark_report(run_id):
    """运行报告（双轨分榜、逐题明细、成本与裁判状态）。"""
    try:
        return jsonify({"status": "ok", "report": get_report(run_id), "message": "运行报告已加载"})
    except BenchmarkError as exc:
        return _error(exc, 404)
    except Exception:
        return _safe_server_error("运行报告暂时不可用")


@bp.route("/api/benchmark/runs/<int:run_id>/rejudge", methods=["POST"])
def api_benchmark_rejudge_run(run_id):
    """只使用已保存候选输出重新裁判，不增加候选调用量。"""
    user_id, _username = _actor_snapshot()
    task_id = _new_task_id("rejudge", run_id)
    try:
        update_progress(
            task_id,
            {"status": "queued", "message": "重新裁判任务已排队", "phase": "judge"},
            user_id=user_id,
        )
        result = submit_rejudge_run(
            run_id,
            progress_callback=_progress_callback(task_id, "judge", user_id),
        )
        return _queue_response(result, task_id, user_id, "重新裁判任务已排队")
    except BenchmarkError as exc:
        update_progress(task_id, {"status": "error", "message": str(exc)}, user_id=user_id)
        return _error(exc)
    except Exception:
        update_progress(
            task_id, {"status": "error", "message": "重新裁判提交失败"}, user_id=user_id,
        )
        return _safe_server_error("重新裁判任务暂时不可用")


@bp.route("/api/benchmark/judgments", methods=["POST"])
def api_benchmark_human_judgment():
    """人工覆盖裁判判定：{run_id, response_id, case_id, condition_scores, notes}."""
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    actor_user_id, actor_username = _actor_snapshot()
    try:
        condition_scores = data.get("condition_scores")
        if not isinstance(condition_scores, list) or not condition_scores:
            raise ValueError("人工判定必须提交完整 condition_scores 数组")
        set_human_judgment(
            int(data["run_id"]), int(data["response_id"]), int(data["case_id"]),
            condition_scores,
            notes=data.get("notes", ""),
            actor_user_id=actor_user_id,
            actor_username=actor_username,
            hallucination_critical=bool(data.get("hallucination_critical", False)),
        )
        return jsonify({"status": "ok", "message": "人工判定已保存（优先级最高）"})
    except (BenchmarkError, ValueError, KeyError, TypeError) as exc:
        return _error(exc)
    except Exception:
        return _safe_server_error("人工判定暂时不可用")


@bp.route("/api/benchmark/routes")
def api_benchmark_routes():
    """benchmark 任务路由配置（不含任何凭据）。"""
    try:
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
            "message": "Benchmark 路由已加载",
        })
    except Exception:
        return _safe_server_error("Benchmark 路由暂时不可用")


@bp.route("/api/benchmark/routes/<task_key>", methods=["POST"])
def api_benchmark_save_route(task_key):
    """保存单个 benchmark 任务路由配置。"""
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    try:
        config = save_route_config(task_key, data.get("config") or {})
        return jsonify({"status": "ok", "config": config, "message": "路由已保存"})
    except ValueError as exc:
        return _error(exc)
    except Exception:
        return _safe_server_error("Benchmark 路由保存暂时不可用")


@bp.route("/api/benchmark/progress/<task_id>")
def api_benchmark_progress(task_id):
    """Benchmark 任务进度 SSE。"""
    try:
        actor = current_user() or {}
    except RuntimeError:
        actor = {}
    user_id = actor.get("id")

    def generate():
        last_data = None
        while True:
            try:
                data = get_progress(task_id, user_id=user_id)
                state = _current_task_state(data) if data else None
            except Exception:
                logger.exception("benchmark progress stream failed for %s", task_id)
                state = {"status": "error", "message": "Benchmark 进度暂时不可用"}
            if state and state != last_data:
                yield f"data: {json.dumps(state, ensure_ascii=False)}\n\n"
                last_data = state
                if state.get("status") in ("completed", "error"):
                    break
            time.sleep(0.5)

    return Response(generate(), mimetype="text/event-stream")
