"""SQLite storage for the private paper reading benchmark."""

import json
import logging

from .connection import get_connection
from source.value_coercion import as_float, as_int

logger = logging.getLogger(__name__)


BENCHMARK_ROUTE_KEYS = ("benchmark_author", "benchmark_judge", "benchmark_judge_review")
BENCHMARK_RUN_STATUSES = (
    "queued", "running", "interrupted", "completed", "error",
)
BENCHMARK_CASE_STATUSES = ("pending", "accepted", "rejected")
BENCHMARK_SCORING_REVISION_STATUSES = (
    "pending", "running", "completed", "interrupted", "error",
)
REUSABLE_RESPONSE_STATUSES = {
    "ok", "format_error", "truncated", "empty", "reused",
}


def _now():
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# benchmark_routes（自管理任务路由，不进入共享 ai_tasks 设置）
# ---------------------------------------------------------------------------

def get_benchmark_routes():
    """读取全部 benchmark 任务路由（benchmark_author/benchmark_judge/benchmark_judge_review）。"""
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM benchmark_routes ORDER BY task_key").fetchall()
    routes = {}
    for row in rows:
        routes[row["task_key"]] = dict(row)
    return routes


def get_benchmark_route(task_key):
    """读取单个 benchmark 任务路由，不存在时返回 None。"""
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM benchmark_routes WHERE task_key = ?", (task_key,)).fetchone()
    return dict(row) if row else None


def save_benchmark_route(task_key, config):
    """保存或更新 benchmark 任务路由；返回保存后的路由字典。"""
    task_key = str(task_key or "").strip()
    if task_key not in BENCHMARK_ROUTE_KEYS:
        raise ValueError(f"未知 benchmark 任务路由: {task_key or '未设置'}")
    config = dict(config or {})
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO benchmark_routes (
                task_key, provider_key, model, is_thinking, thinking_effort,
                temperature_enabled, temperature, max_tokens_enabled, max_tokens, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_key) DO UPDATE SET
                provider_key = excluded.provider_key,
                model = excluded.model,
                is_thinking = excluded.is_thinking,
                thinking_effort = excluded.thinking_effort,
                temperature_enabled = excluded.temperature_enabled,
                temperature = excluded.temperature,
                max_tokens_enabled = excluded.max_tokens_enabled,
                max_tokens = excluded.max_tokens,
                updated_at = excluded.updated_at
            """,
            (
                task_key,
                str(config.get("provider_key") or "").strip(),
                str(config.get("model") or "").strip(),
                1 if config.get("is_thinking") else 0,
                str(config.get("thinking_effort") or "medium"),
                1 if config.get("temperature_enabled") else 0,
                as_float(config.get("temperature"), 0.2),
                1 if config.get("max_tokens_enabled", True) else 0,
                max(1, min(200000, as_int(config.get("max_tokens"), 2000))),
                _now(),
            ),
        )
    return get_benchmark_route(task_key)


# ---------------------------------------------------------------------------
# benchmark_suites
# ---------------------------------------------------------------------------

def create_benchmark_suite(subset_name, version_label, deep_reading_prompt, paper_chat_prompt, checksum=""):
    """创建 benchmark 题库草稿，返回 suite_id。"""
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO benchmark_suites (
                subset_name, version_label, status, deep_reading_prompt,
                paper_chat_prompt, suite_checksum, updated_at
            ) VALUES (?, ?, 'draft', ?, ?, ?, ?)
            """,
            (
                str(subset_name or "").strip(),
                str(version_label or "").strip(),
                json.dumps(deep_reading_prompt or {}, ensure_ascii=False),
                json.dumps(paper_chat_prompt or {}, ensure_ascii=False),
                str(checksum or ""),
                _now(),
            ),
        )
        suite_id = cursor.lastrowid
    return suite_id


def get_benchmark_suite(suite_id):
    """读取题库元数据与统计（论文数、各轨道已接受题目数）。"""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM benchmark_suites WHERE id = ?", (as_int(suite_id),)
        ).fetchone()
    if not row:
        return None
    suite = dict(row)
    suite["deep_reading_prompt"] = _loads(suite.get("deep_reading_prompt"), {})
    suite["paper_chat_prompt"] = _loads(suite.get("paper_chat_prompt"), {})
    suite["scoring_revision"] = _loads(suite.get("scoring_revision"), {})
    with get_connection() as conn:
        paper_count = conn.execute(
            "SELECT COUNT(*) FROM benchmark_suite_papers WHERE suite_id = ?", (suite["id"],)
        ).fetchone()[0]
        case_counts = {
            r["track"]: r["n"]
            for r in conn.execute(
                """
                SELECT track, COUNT(*) AS n FROM benchmark_cases
                WHERE suite_id = ? AND status = 'accepted'
                GROUP BY track
                """,
                (suite["id"],),
            ).fetchall()
        }
    suite["paper_count"] = paper_count
    suite["accepted_case_counts"] = case_counts
    return suite


def list_benchmark_suites():
    """按创建时间倒序列出题库。"""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM benchmark_suites ORDER BY id DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def update_benchmark_suite(suite_id, **fields):
    """更新题库字段（status/suite_checksum/scoring_revision），返回更新后的字典。"""
    allowed = {"status", "suite_checksum", "scoring_revision"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return get_benchmark_suite(suite_id)
    values = {}
    sets = []
    for key, value in updates.items():
        sets.append(f"{key} = ?")
        if key == "scoring_revision":
            values[key] = json.dumps(value or {}, ensure_ascii=False) if not isinstance(value, str) else value
        else:
            values[key] = value
    sets.append("updated_at = ?")
    values["updated_at"] = _now()
    with get_connection() as conn:
        conn.execute(
            f"UPDATE benchmark_suites SET {', '.join(sets)} WHERE id = ?",
            tuple(values.values()) + (as_int(suite_id),),
        )
    return get_benchmark_suite(suite_id)


# ---------------------------------------------------------------------------
# benchmark_suite_papers
# ---------------------------------------------------------------------------

def add_benchmark_suite_paper(suite_id, snapshot):
    """写入一篇论文快照，已存在（同 suite+paper_key）时跳过，返回 paper_ref_id。"""
    snapshot = dict(snapshot or {})
    paper_key = str(snapshot.get("paper_key") or "").strip()
    if not paper_key:
        raise ValueError("论文快照缺少 paper_key")
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM benchmark_suite_papers WHERE suite_id = ? AND paper_key = ?",
            (as_int(suite_id), paper_key),
        ).fetchone()
        if existing:
            return existing["id"]
        cursor = conn.execute(
            """
            INSERT INTO benchmark_suite_papers (
                suite_id, paper_id, paper_key, title, authors, source_type, source_id,
                abstract, full_text, text_chars, text_sha256, pdf_sha256,
                extractor_version, position
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                as_int(suite_id),
                snapshot.get("paper_id"),
                paper_key,
                str(snapshot.get("title") or ""),
                json.dumps(snapshot.get("authors") or [], ensure_ascii=False),
                str(snapshot.get("source_type") or ""),
                str(snapshot.get("source_id") or ""),
                str(snapshot.get("abstract") or ""),
                str(snapshot.get("full_text") or ""),
                as_int(snapshot.get("text_chars")),
                str(snapshot.get("text_sha256") or ""),
                str(snapshot.get("pdf_sha256") or ""),
                str(snapshot.get("extractor_version") or ""),
                as_int(snapshot.get("position"), 0),
            ),
        )
        return cursor.lastrowid


def list_suite_papers(suite_id):
    """读取题库内论文快照列表。"""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM benchmark_suite_papers WHERE suite_id = ? ORDER BY position, id",
            (as_int(suite_id),),
        ).fetchall()
    papers = []
    for row in rows:
        item = dict(row)
        item["authors"] = _loads(item.get("authors"), [])
        papers.append(item)
    return papers


def get_suite_paper(paper_ref_id):
    """读取单篇论文快照。"""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM benchmark_suite_papers WHERE id = ?", (as_int(paper_ref_id),)
        ).fetchone()
    if not row:
        return None
    item = dict(row)
    item["authors"] = _loads(item.get("authors"), [])
    return item


# ---------------------------------------------------------------------------
# benchmark_cases
# ---------------------------------------------------------------------------

def add_benchmark_case(case):
    """写入一个题目（草稿状态）；同 suite+paper+track+position 重复时跳过。"""
    case = dict(case or {})
    required = ("suite_id", "paper_ref_id", "track", "position", "question")
    for key in required:
        if case.get(key) in (None, ""):
            raise ValueError(f"题目缺少字段: {key}")
    with get_connection() as conn:
        existing = conn.execute(
            """
            SELECT id FROM benchmark_cases
            WHERE suite_id = ? AND paper_ref_id = ? AND track = ? AND position = ?
            """,
            (as_int(case["suite_id"]), as_int(case["paper_ref_id"]),
             str(case["track"]), as_int(case["position"])),
        ).fetchone()
        if existing:
            return existing["id"]
        cursor = conn.execute(
            """
            INSERT INTO benchmark_cases (
                suite_id, paper_ref_id, track, position, kind, question,
                reference_answer, evidence, rubric, requires_reject, status,
                author_raw
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                as_int(case["suite_id"]),
                as_int(case["paper_ref_id"]),
                str(case["track"]),
                as_int(case["position"]),
                str(case.get("kind") or ""),
                str(case["question"]),
                str(case.get("reference_answer") or ""),
                json.dumps(case.get("evidence") or [], ensure_ascii=False),
                json.dumps(case.get("rubric") or {}, ensure_ascii=False),
                1 if case.get("requires_reject") else 0,
                json.dumps(case.get("author_raw") or {}, ensure_ascii=False),
            ),
        )
        return cursor.lastrowid


def list_suite_cases(suite_id, track=None, status=None):
    """列出题库题目；可按轨道和状态过滤。"""
    sql = "SELECT * FROM benchmark_cases WHERE suite_id = ?"
    params = [as_int(suite_id)]
    if track:
        sql += " AND track = ?"
        params.append(str(track))
    if status:
        sql += " AND status = ?"
        params.append(str(status))
    sql += " ORDER BY paper_ref_id, track, position"
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_decode_case(row) for row in rows]


def get_benchmark_case(case_id):
    """读取单个题目（含论文上下文）。"""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM benchmark_cases WHERE id = ?", (as_int(case_id),)
        ).fetchone()
    if not row:
        return None
    return _decode_case(row)


def update_benchmark_case(
    case_id, fields=None, actor_user_id=None, actor_username="", note="", **kwargs
):
    """Edit a case and append one immutable audit revision.

    Lifecycle rules such as whether a suite is frozen belong to the benchmark
    service.  This storage function only validates the case/fields and writes
    the update and audit row in one transaction.
    """
    case_id = as_int(case_id)
    updates = dict(fields or {})
    updates.update(kwargs)
    allowed = {
        "track", "position", "kind", "question", "reference_answer",
        "evidence", "rubric", "requires_reject", "author_raw",
    }
    updates = {key: value for key, value in updates.items() if key in allowed}
    with get_connection() as conn:
        before_row = conn.execute(
            "SELECT * FROM benchmark_cases WHERE id = ?",
            (case_id,),
        ).fetchone()
        if not before_row:
            raise ValueError("题目不存在")
        before = _decode_case(before_row)
        if not updates:
            return before
        sets = []
        params = []
        for key, value in updates.items():
            if key in {"evidence", "rubric", "author_raw"}:
                value = json.dumps(value or ([] if key == "evidence" else {}), ensure_ascii=False)
            elif key == "requires_reject":
                value = 1 if value else 0
            elif key in {"position"}:
                value = as_int(value)
            else:
                value = str(value or "")
            sets.append(f"{key} = ?")
            params.append(value)
        sets.extend(["status = 'pending'", "review_note = ''", "reviewed_at = NULL"])
        conn.execute(
            f"UPDATE benchmark_cases SET {', '.join(sets)} WHERE id = ?",
            tuple(params) + (case_id,),
        )
        after_row = conn.execute(
            "SELECT * FROM benchmark_cases WHERE id = ?", (case_id,)
        ).fetchone()
        after = _decode_case(after_row)
        _append_case_revision_conn(
            conn, case_id, "edit", before, after,
            actor_user_id=actor_user_id, actor_username=actor_username, note=note,
        )
    return get_benchmark_case(case_id)


def edit_benchmark_case(*args, **kwargs):
    """Compatibility alias for :func:`update_benchmark_case`."""
    return update_benchmark_case(*args, **kwargs)


def set_case_review(case_id, status, note="", actor_user_id=None, actor_username=""):
    """Write one case review and its audit row in one transaction.

    The benchmark service owns the suite lifecycle/frozen check; storage only
    validates the case and requested status.
    """
    case_id = as_int(case_id)
    status = str(status or "").strip()
    if status not in BENCHMARK_CASE_STATUSES:
        raise ValueError(f"未知题目审核状态: {status}")
    with get_connection() as conn:
        before_row = conn.execute(
            "SELECT * FROM benchmark_cases WHERE id = ?", (case_id,)
        ).fetchone()
        if not before_row:
            raise ValueError("题目不存在")
        before = _decode_case(before_row)
        reviewed_at = _now()
        conn.execute(
            "UPDATE benchmark_cases SET status = ?, review_note = ?, reviewed_at = ? WHERE id = ?",
            (status, str(note or ""), reviewed_at, case_id),
        )
        after_row = conn.execute(
            "SELECT * FROM benchmark_cases WHERE id = ?", (case_id,)
        ).fetchone()
        _append_case_revision_conn(
            conn, case_id, "review", before, _decode_case(after_row),
            actor_user_id=actor_user_id, actor_username=actor_username,
            note=note,
        )
    return get_benchmark_case(case_id)


def set_cases_review(suite_id, status, note="", actor_user_id=None, actor_username=""):
    """Write reviews for all cases in a suite in one transaction.

    Frozen-suite policy is enforced by the benchmark service, not storage.
    """
    status = str(status or "").strip()
    if status not in BENCHMARK_CASE_STATUSES:
        raise ValueError(f"未知题目审核状态: {status}")
    with get_connection() as conn:
        suite = conn.execute(
            "SELECT id FROM benchmark_suites WHERE id = ?", (as_int(suite_id),)
        ).fetchone()
        if not suite:
            raise ValueError("题库不存在")
        rows = conn.execute(
            """
            SELECT * FROM benchmark_cases WHERE suite_id = ? ORDER BY id
            """,
            (as_int(suite_id),),
        ).fetchall()
        reviewed_at = _now()
        for before_row in rows:
            before = _decode_case(before_row)
            conn.execute(
                "UPDATE benchmark_cases SET status = ?, review_note = ?, reviewed_at = ? WHERE id = ?",
                (status, str(note or ""), reviewed_at, before["id"]),
            )
            after_row = conn.execute(
                "SELECT * FROM benchmark_cases WHERE id = ?", (before["id"],)
            ).fetchone()
            _append_case_revision_conn(
                conn, before["id"], "review", before, _decode_case(after_row),
                actor_user_id=actor_user_id, actor_username=actor_username,
                note=note,
            )


# ---------------------------------------------------------------------------
# benchmark_runs / benchmark_candidates
# ---------------------------------------------------------------------------

def create_benchmark_run(
    suite_id, repeats=1, max_calls=None, runner_version="", status="running",
):
    """创建一次评测运行，返回 run_id。"""
    repeats = max(1, min(5, as_int(repeats, 1)))
    status = str(status or "running").strip()
    if status not in BENCHMARK_RUN_STATUSES:
        raise ValueError(f"未知运行状态: {status}")
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO benchmark_runs (suite_id, status, repeats, max_calls, runner_version)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                as_int(suite_id), status, repeats,
                as_int(max_calls) if max_calls else None,
                str(runner_version or ""),
            ),
        )
        return cursor.lastrowid


def add_benchmark_candidate(run_id, position, label, config, config_hash, actual_params):
    """写入一个候选模型配置快照，返回 candidate_id。"""
    config = dict(config or {})
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO benchmark_candidates (
                run_id, position, label, provider_key, provider_name, model,
                is_thinking, thinking_effort, temperature_enabled, temperature,
                max_tokens_enabled, max_tokens, config_hash, actual_params
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                as_int(run_id),
                as_int(position, 0),
                str(label or ""),
                str(config.get("provider_key") or ""),
                str(config.get("provider_name") or ""),
                str(config.get("model") or ""),
                1 if config.get("is_thinking") else 0,
                str(config.get("thinking_effort") or "medium"),
                1 if config.get("temperature_enabled") else 0,
                as_float(config.get("temperature"), 0.2),
                1 if config.get("max_tokens_enabled", True) else 0,
                as_int(config.get("max_tokens"), 4000),
                str(config_hash or ""),
                json.dumps(_sanitize_actual_params(actual_params), ensure_ascii=False),
            ),
        )
        return cursor.lastrowid


def list_run_candidates(run_id):
    """读取一次运行的全部候选配置。"""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM benchmark_candidates WHERE run_id = ? ORDER BY position, id",
            (as_int(run_id),),
        ).fetchall()
    return [_decode_candidate(row) for row in rows]


def get_benchmark_run(run_id):
    """读取运行元数据与统计。"""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM benchmark_runs WHERE id = ?", (as_int(run_id),)
        ).fetchone()
    if not row:
        return None
    run = dict(row)
    run["candidates"] = list_run_candidates(run["id"])
    with get_connection() as conn:
        run["response_count"] = conn.execute(
            "SELECT COUNT(*) FROM benchmark_responses WHERE run_id = ?", (run["id"],)
        ).fetchone()[0]
        run["judgment_count"] = conn.execute(
            "SELECT COUNT(*) FROM benchmark_judgments WHERE run_id = ?", (run["id"],)
        ).fetchone()[0]
    return run


def list_benchmark_runs(suite_id=None):
    """列出运行记录（可只列某个题库）。"""
    sql = "SELECT * FROM benchmark_runs"
    params = []
    if suite_id:
        sql += " WHERE suite_id = ?"
        params.append(as_int(suite_id))
    sql += " ORDER BY id DESC"
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def set_run_status(run_id, status):
    """更新运行状态；进入终态时记录完成时间。"""
    status = str(status or "").strip()
    if status not in BENCHMARK_RUN_STATUSES:
        raise ValueError(f"未知运行状态: {status}")
    terminal = status in ("interrupted", "completed", "error")
    finished_at = _now() if terminal else None
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE benchmark_runs
            SET status = ?,
                finished_at = CASE
                    WHEN ? IS NULL THEN NULL
                    ELSE COALESCE(finished_at, ?)
                END
            WHERE id = ?
            """,
            (status, finished_at, finished_at, as_int(run_id)),
        )


def claim_benchmark_run(run_id):
    """Atomically claim a queued run for the in-process worker.

    Only a queued row can be claimed.  This makes duplicate worker delivery
    harmless: the first UPDATE returns one affected row and all later claims
    return False, including claims after a terminal transition.
    """
    with get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE benchmark_runs
            SET status = 'running', finished_at = NULL
            WHERE id = ? AND status = 'queued'
            """,
            (as_int(run_id),),
        )
        return cursor.rowcount == 1


def update_run_max_calls(run_id, max_calls):
    """Raise a run's candidate-call ceiling for an explicit resume.

    The caller enforces the public strict-increase rule; this storage helper
    keeps the update transactional and refuses accidental decreases.
    """
    max_calls = as_int(max_calls)
    if max_calls <= 0:
        raise ValueError("max_calls 必须是正整数")
    with get_connection() as conn:
        row = conn.execute(
            "SELECT max_calls FROM benchmark_runs WHERE id = ?", (as_int(run_id),)
        ).fetchone()
        if not row:
            raise ValueError("运行不存在")
        previous = row["max_calls"]
        if previous is None or max_calls <= int(previous):
            raise ValueError("新的 max_calls 必须严格高于当前上限")
        conn.execute(
            "UPDATE benchmark_runs SET max_calls = ? WHERE id = ?",
            (max_calls, as_int(run_id)),
        )


def consume_candidate_call(run_id):
    """Atomically charge one candidate API attempt.

    Returns ``True`` when the attempt is within the persisted ceiling and
    ``False`` when the finite budget is already exhausted.  Judges do not use
    this function: ``candidate_calls_made`` is intentionally a candidate-only
    budget counter.
    """
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            """
            UPDATE benchmark_runs
            SET candidate_calls_made = candidate_calls_made + 1
            WHERE id = ?
              AND (max_calls IS NULL OR candidate_calls_made < max_calls)
            """,
            (as_int(run_id),),
        )
        return cursor.rowcount == 1


def mark_interrupted_runs():
    """将遗留的 queued/running 运行标记为 interrupted（应用启动时调用）。

    benchmark 表尚未迁移（旧库或测试桩）时安全跳过。
    """
    try:
        with get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE benchmark_runs
                SET status = 'interrupted', finished_at = COALESCE(finished_at, ?)
                WHERE status IN ('queued', 'running')
                """,
                (_now(),),
            )
            return cursor.rowcount
    except Exception as exc:
        logger.debug(f"mark_interrupted_runs skipped: {exc}")
        return 0


def mark_interrupted():
    """Compatibility alias for callers using the shorter v7 name."""
    return mark_interrupted_runs()


# ---------------------------------------------------------------------------
# benchmark_responses
# ---------------------------------------------------------------------------

def save_benchmark_response(run_id, candidate_id, paper_ref_id, track, repeat_index,
                            round_index, prompt_snapshot, raw_output, parsed, status,
                            finish_reason, usage_json, latency_ms, continuation_count=0,
                            retry_count=0, error_json=None, reused_from_response_id=None):
    """保存一条候选输出；相同键（候选/论文/轨道/重复槽/轮次）已存在时跳过并返回已有 id。"""
    with get_connection() as conn:
        existing = conn.execute(
            """
            SELECT id FROM benchmark_responses
            WHERE candidate_id = ? AND paper_ref_id = ? AND track = ? AND repeat_index = ? AND round_index IS ?
            """,
            (as_int(candidate_id), as_int(paper_ref_id), str(track),
             as_int(repeat_index), round_index),
        ).fetchone()
        if existing:
            return existing["id"]
        cursor = conn.execute(
            """
            INSERT INTO benchmark_responses (
                run_id, candidate_id, paper_ref_id, track, repeat_index, round_index,
                prompt_snapshot, raw_output, parsed, status, finish_reason,
                usage_json, latency_ms, continuation_count, retry_count,
                error_json, reused_from_response_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                as_int(run_id), as_int(candidate_id), as_int(paper_ref_id), str(track),
                as_int(repeat_index), round_index,
                json.dumps(prompt_snapshot or [], ensure_ascii=False),
                str(raw_output or ""),
                json.dumps(parsed or {}, ensure_ascii=False),
                str(status or "ok"),
                str(finish_reason or ""),
                json.dumps(usage_json or {}, ensure_ascii=False),
                as_float(latency_ms, 0),
                as_int(continuation_count, 0),
                max(0, as_int(retry_count, 0)),
                json.dumps(error_json or {}, ensure_ascii=False),
                as_int(reused_from_response_id) if reused_from_response_id else None,
            ),
        )
        # Direct storage callers (including legacy repair/import tooling) may
        # insert a response without passing through the runner's pre-call
        # charge. Never lower the persisted counter—failed API attempts are
        # intentionally retained—but bring it up to the response-derived
        # minimum so historical semantics remain truthful.
        conn.execute(
            """
            UPDATE benchmark_runs
            SET candidate_calls_made = MAX(
                candidate_calls_made,
                COALESCE(
                    (SELECT SUM(
                        1 + COALESCE(continuation_count, 0)
                          + COALESCE(retry_count, 0)
                    )
                     FROM benchmark_responses WHERE run_id = ?),
                    0
                )
            )
            WHERE id = ?
            """,
            (as_int(run_id), as_int(run_id)),
        )
        return cursor.lastrowid


def get_response(response_id):
    """读取单条候选输出。"""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM benchmark_responses WHERE id = ?", (as_int(response_id),)
        ).fetchone()
    if not row:
        return None
    return _decode_response(row)


def get_existing_response(candidate_id, paper_ref_id, track, repeat_index, round_index):
    """按唯一键查找已保存的候选输出，返回 id 或 None（用于输出复用）。"""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id FROM benchmark_responses
            WHERE candidate_id = ? AND paper_ref_id = ? AND track = ? AND repeat_index = ? AND round_index IS ?
            """,
            (as_int(candidate_id), as_int(paper_ref_id), str(track),
             as_int(repeat_index), round_index),
        ).fetchone()
    return row["id"] if row else None


def replace_retry_failed_response(response_id, run_id, response=None, *payload, **fields):
    """Atomically replace a retry-failed response in its original slot.

    ``response_id`` must belong to ``run_id`` and currently have
    ``status='retry_failed'``.  The response slot, candidate ownership and
    reuse provenance are immutable; only the result payload and attempt
    metadata are replaced.  Candidate call accounting is intentionally left
    untouched because the runner reserves every retry before calling the API.

    The preferred form passes a response mapping returned by the runner::

        replace_retry_failed_response(response_id, run_id, response)

    Keyword fields are also accepted for small storage/import callers.  The
    function returns the unchanged response id, matching
    :func:`save_benchmark_response`.
    """
    if payload:
        positional_values = [response, *payload]
        positional_names = (
            "prompt_snapshot", "raw_output", "parsed", "status", "finish_reason",
            "usage_json", "latency_ms", "continuation_count", "retry_count", "error_json",
        )
        if len(positional_values) > len(positional_names):
            raise TypeError("replace_retry_failed_response 参数过多")
        response_values = dict(zip(positional_names, positional_values))
    elif response is None:
        response_values = {}
    elif isinstance(response, dict):
        response_values = dict(response)
    else:
        # The first positional payload field is prompt_snapshot.  Supporting
        # this form keeps the API convenient for callers mirroring
        # save_benchmark_response's payload order.
        response_values = {"prompt_snapshot": response}
    response_values.update(fields)

    prompt_snapshot = response_values.get("prompt_snapshot") or []
    raw_output = str(response_values.get("raw_output") or "")
    parsed = response_values.get("parsed") or {}
    status = str(response_values.get("status") or "ok")
    finish_reason = str(response_values.get("finish_reason") or "")
    usage_json = response_values.get("usage_json") or {}
    latency_ms = as_float(response_values.get("latency_ms"), 0)
    continuation_count = max(0, as_int(response_values.get("continuation_count"), 0))
    retry_count = max(0, as_int(response_values.get("retry_count"), 0))
    error_json = response_values.get("error_json") or {}
    response_id = as_int(response_id)
    run_id = as_int(run_id)

    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT br.id, br.run_id, br.status, c.run_id AS candidate_run_id
            FROM benchmark_responses AS br
            JOIN benchmark_candidates AS c ON c.id = br.candidate_id
            WHERE br.id = ? AND br.run_id = ?
            """,
            (response_id, run_id),
        ).fetchone()
        if not row:
            raise ValueError("候选输出不存在或不属于目标运行")
        if row["candidate_run_id"] != run_id:
            raise ValueError("候选输出所属候选与目标运行不一致")
        if row["status"] != "retry_failed":
            raise ValueError("只有 retry_failed 响应可以被替换")
        cursor = conn.execute(
            """
            UPDATE benchmark_responses
            SET prompt_snapshot = ?, raw_output = ?, parsed = ?,
                status = ?, finish_reason = ?, usage_json = ?,
                latency_ms = ?, continuation_count = ?, retry_count = ?,
                error_json = ?
            WHERE id = ? AND run_id = ? AND status = 'retry_failed'
            """,
            (
                json.dumps(prompt_snapshot, ensure_ascii=False),
                raw_output,
                json.dumps(parsed, ensure_ascii=False),
                status,
                finish_reason,
                json.dumps(usage_json, ensure_ascii=False),
                latency_ms,
                continuation_count,
                retry_count,
                json.dumps(error_json, ensure_ascii=False),
                response_id,
                run_id,
            ),
        )
        if cursor.rowcount != 1:
            raise ValueError("retry_failed 响应已被其他事务替换")
    return response_id


def replace_benchmark_response(*args, **kwargs):
    """Compatibility alias for :func:`replace_retry_failed_response`."""
    return replace_retry_failed_response(*args, **kwargs)


def find_reusable_response(
    suite_checksum=None, runner_version=None, config_hash=None,
    paper_key=None, track=None, repeat_index=0, round_index=None,
    paper_ref_id=None,
):
    """Find the newest completed response matching an exact reusable slot.

    Response and candidate ids are intentionally not part of this lookup:
    those ids are run-local.  The stable identity is the frozen suite
    checksum, runner version, candidate config hash, paper key, track and
    repeat/round slot.  A target ``paper_ref_id`` may be supplied instead of
    ``paper_key``; its key is used to match snapshots from another run.
    Infrastructure failures and non-terminal rows are never reusable.
    """
    if paper_key is None and paper_ref_id is None:
        raise ValueError("复用查询必须提供 paper_key 或 paper_ref_id")
    conditions = [
        "s.suite_checksum = ?",
        "r.runner_version = ?",
        "c.config_hash = ?",
        "br.track = ?",
        "br.repeat_index = ?",
        "br.round_index IS ?",
        "br.status IN ({})".format(", ".join("?" for _ in REUSABLE_RESPONSE_STATUSES)),
    ]
    params = [
        str(suite_checksum or ""), str(runner_version or ""),
        str(config_hash or ""), str(track or ""), as_int(repeat_index),
        round_index,
        *sorted(REUSABLE_RESPONSE_STATUSES),
    ]
    if paper_key is None:
        conditions.append(
            "p.paper_key = (SELECT paper_key FROM benchmark_suite_papers WHERE id = ?)"
        )
        params.append(as_int(paper_ref_id))
    else:
        conditions.append("p.paper_key = ?")
        params.append(str(paper_key))
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT br.*
            FROM benchmark_responses AS br
            JOIN benchmark_candidates AS c ON c.id = br.candidate_id
            JOIN benchmark_runs AS r ON r.id = br.run_id
            JOIN benchmark_suites AS s ON s.id = r.suite_id
            JOIN benchmark_suite_papers AS p ON p.id = br.paper_ref_id
            WHERE {conditions}
            ORDER BY br.id DESC
            LIMIT 1
            """.format(conditions=" AND ".join(conditions)),
            params,
        ).fetchone()
    return _decode_response(row) if row else None


def get_reusable_response(*args, **kwargs):
    """Compatibility alias for :func:`find_reusable_response`."""
    return find_reusable_response(*args, **kwargs)


def _response_slot_matches(conn, response_id, target_run_id, target_candidate_id,
                           target_paper_ref_id):
    row = conn.execute(
        """
        SELECT br.track, br.repeat_index, br.round_index,
               src_p.paper_key AS source_paper_key,
               target_p.paper_key AS target_paper_key,
               c.run_id AS candidate_run_id,
               r.suite_id AS source_suite_id,
               target_run.suite_id AS target_suite_id
        FROM benchmark_responses AS br
        JOIN benchmark_suite_papers AS src_p ON src_p.id = br.paper_ref_id
        JOIN benchmark_candidates AS c ON c.id = ?
        JOIN benchmark_runs AS r ON r.id = br.run_id
        JOIN benchmark_runs AS target_run ON target_run.id = ?
        JOIN benchmark_suite_papers AS target_p ON target_p.id = ?
        WHERE br.id = ? AND target_p.suite_id = target_run.suite_id
        """,
        (
            as_int(target_candidate_id), as_int(target_run_id),
            as_int(target_paper_ref_id), as_int(response_id),
        ),
    ).fetchone()
    if not row:
        raise ValueError("候选输出不存在")
    if row["candidate_run_id"] != as_int(target_run_id):
        raise ValueError("目标候选不属于目标运行")
    if row["source_paper_key"] != row["target_paper_key"]:
        raise ValueError("源输出与目标论文快照不匹配")
    return row


def clone_reusable_response(source_response_id, target_run_id, target_candidate_id,
                            target_paper_ref_id):
    """Clone a reusable response into a new run without charging its budget."""
    with get_connection() as conn:
        source = conn.execute(
            "SELECT * FROM benchmark_responses WHERE id = ?",
            (as_int(source_response_id),),
        ).fetchone()
        if not source:
            raise ValueError("源候选输出不存在")
        if source["status"] not in REUSABLE_RESPONSE_STATUSES:
            raise ValueError("该候选输出不可复用")
        slot = _response_slot_matches(
            conn, source_response_id, target_run_id,
            target_candidate_id, target_paper_ref_id,
        )
        existing = conn.execute(
            """
            SELECT id FROM benchmark_responses
            WHERE candidate_id = ? AND paper_ref_id = ? AND track = ?
              AND repeat_index = ? AND round_index IS ?
            """,
            (
                as_int(target_candidate_id), as_int(target_paper_ref_id),
                slot["track"], slot["repeat_index"], slot["round_index"],
            ),
        ).fetchone()
        if existing:
            return existing["id"]
        cursor = conn.execute(
            """
            INSERT INTO benchmark_responses (
                run_id, candidate_id, paper_ref_id, track, repeat_index, round_index,
                prompt_snapshot, raw_output, parsed, status, finish_reason,
                usage_json, latency_ms, continuation_count, retry_count, error_json,
                reused_from_response_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                as_int(target_run_id), as_int(target_candidate_id),
                as_int(target_paper_ref_id), source["track"], source["repeat_index"],
                source["round_index"], source["prompt_snapshot"], source["raw_output"],
                source["parsed"], source["status"], source["finish_reason"],
                "{}", 0, 0, 0, "{}",
                as_int(source_response_id),
            ),
        )
        # Deliberately do not update benchmark_runs.candidate_calls_made here:
        # this row represents a previously paid API attempt.
        return cursor.lastrowid


def clone_benchmark_response(*args, **kwargs):
    """Compatibility alias for :func:`clone_reusable_response`."""
    return clone_reusable_response(*args, **kwargs)


def get_run_responses(run_id):
    """读取一次运行的全部候选输出。"""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM benchmark_responses WHERE run_id = ? ORDER BY id",
            (as_int(run_id),),
        ).fetchall()
    return [_decode_response(row) for row in rows]


def count_run_calls(run_id):
    """统计一次运行已产生的模型调用次数（用于预算检查）。"""
    with get_connection() as conn:
        try:
            row = conn.execute(
                "SELECT candidate_calls_made FROM benchmark_runs WHERE id = ?",
                (as_int(run_id),),
            ).fetchone()
        except Exception:
            row = None
            legacy_counter = conn.execute(
                "SELECT COALESCE(SUM(1 + COALESCE(continuation_count, 0)), 0) "
                "FROM benchmark_responses WHERE run_id = ?",
                (as_int(run_id),),
            ).fetchone()[0]
            return legacy_counter
        if row is not None:
            return int(row["candidate_calls_made"] or 0)
        # Keep this helper useful for an isolated legacy fixture that predates
        # benchmark_runs v6; normal application databases always take the
        # persisted counter path above.
        return conn.execute(
            "SELECT COALESCE(SUM(1 + COALESCE(continuation_count, 0) "
            "+ COALESCE(retry_count, 0)), 0) "
            "FROM benchmark_responses WHERE run_id = ?",
            (as_int(run_id),),
        ).fetchone()[0]


# ---------------------------------------------------------------------------
# benchmark_judgments
# ---------------------------------------------------------------------------

def save_benchmark_judgment(run_id, response_id, judge_role, judge_route_key,
                            scoring_revision, case_id, condition_scores, score,
                            hallucination_critical, confidence, raw_json, notes="",
                            actor_user_id=None, actor_username=""):
    """保存一条裁判/人工判定；同键重复时更新原记录。"""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO benchmark_judgments (
                run_id, response_id, judge_role, judge_route_key, scoring_revision,
                case_id, condition_scores, score, hallucination_critical,
                confidence, raw_json, notes, actor_user_id, actor_username
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(response_id, judge_role, case_id, scoring_revision) DO UPDATE SET
                judge_route_key = excluded.judge_route_key,
                condition_scores = excluded.condition_scores,
                score = excluded.score,
                hallucination_critical = excluded.hallucination_critical,
                confidence = excluded.confidence,
                raw_json = excluded.raw_json,
                notes = excluded.notes,
                actor_user_id = excluded.actor_user_id,
                actor_username = excluded.actor_username
            """,
            (
                as_int(run_id), as_int(response_id), str(judge_role), str(judge_route_key),
                str(scoring_revision),
                as_int(case_id),
                json.dumps(condition_scores or [], ensure_ascii=False),
                as_float(score, 0),
                1 if hallucination_critical else 0,
                as_float(confidence) if confidence is not None else None,
                json.dumps(raw_json or {}, ensure_ascii=False),
                str(notes or ""),
                as_int(actor_user_id) if actor_user_id is not None else None,
                str(actor_username or ""),
            ),
        )


def get_run_judgments(run_id):
    """读取一次运行的全部判定。"""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM benchmark_judgments WHERE run_id = ? ORDER BY id",
            (as_int(run_id),),
        ).fetchall()
    return [_decode_judgment(row) for row in rows]


def get_response_judgment(response_id, judge_role, scoring_revision, case_id):
    """读取一条已有的判定（用于只重判不重跑时复用）。"""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT * FROM benchmark_judgments
            WHERE response_id = ? AND judge_role = ? AND case_id = ? AND scoring_revision = ?
            """,
            (as_int(response_id), str(judge_role), as_int(case_id), str(scoring_revision)),
        ).fetchone()
    if not row:
        return None
    return _decode_judgment(row)


# ---------------------------------------------------------------------------
# v7 scoring revisions and case audit history
# ---------------------------------------------------------------------------

def create_benchmark_scoring_revision(
    run_id, revision_key, primary_route_key="", review_route_key="",
    primary_route_snapshot=None, review_route_snapshot=None,
    primary_prompt_snapshot=None, review_prompt_snapshot=None, status="pending",
):
    """Create an immutable scoring configuration snapshot for a run.

    Repeating the same ``run_id``/``revision_key`` is idempotent and returns
    the original row.  The revision is switched active separately, after the
    judge worker has completed successfully.
    """
    revision_key = str(revision_key or "").strip()
    status = str(status or "pending").strip()
    if not revision_key:
        raise ValueError("评分版本缺少 revision_key")
    if status not in BENCHMARK_SCORING_REVISION_STATUSES:
        raise ValueError(f"未知评分版本状态: {status}")
    with get_connection() as conn:
        if not conn.execute(
            "SELECT 1 FROM benchmark_runs WHERE id = ?", (as_int(run_id),)
        ).fetchone():
            raise ValueError("运行不存在")
        existing = conn.execute(
            """
            SELECT id FROM benchmark_scoring_revisions
            WHERE run_id = ? AND revision_key = ?
            """,
            (as_int(run_id), revision_key),
        ).fetchone()
        if existing:
            revision_id = existing["id"]
        else:
            cursor = conn.execute(
                """
                INSERT INTO benchmark_scoring_revisions (
                    run_id, revision_key, status, primary_route_key, review_route_key,
                    primary_route_snapshot, review_route_snapshot,
                    primary_prompt_snapshot, review_prompt_snapshot
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    as_int(run_id), revision_key, status,
                    str(primary_route_key or ""), str(review_route_key or ""),
                    json.dumps(primary_route_snapshot or {}, ensure_ascii=False),
                    json.dumps(review_route_snapshot or {}, ensure_ascii=False),
                    json.dumps(primary_prompt_snapshot or {}, ensure_ascii=False),
                    json.dumps(review_prompt_snapshot or {}, ensure_ascii=False),
                ),
            )
            revision_id = cursor.lastrowid
    return get_benchmark_scoring_revision(revision_id)


def create_scoring_revision(*args, **kwargs):
    """Compatibility alias for :func:`create_benchmark_scoring_revision`."""
    return create_benchmark_scoring_revision(*args, **kwargs)


def get_benchmark_scoring_revision(revision_id):
    """Read one scoring revision with decoded JSON snapshots."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM benchmark_scoring_revisions WHERE id = ?",
            (as_int(revision_id),),
        ).fetchone()
    return _decode_scoring_revision(row) if row else None


def get_scoring_revision(run_id, revision_key):
    """Read a scoring revision by its run-local stable key."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT * FROM benchmark_scoring_revisions
            WHERE run_id = ? AND revision_key = ?
            """,
            (as_int(run_id), str(revision_key or "")),
        ).fetchone()
    return _decode_scoring_revision(row) if row else None


def list_benchmark_scoring_revisions(run_id):
    """List scoring revisions in creation order."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM benchmark_scoring_revisions
            WHERE run_id = ? ORDER BY id
            """,
            (as_int(run_id),),
        ).fetchall()
    return [_decode_scoring_revision(row) for row in rows]


def set_benchmark_scoring_revision_status(revision_id, status, error_json=None):
    """Update a revision worker status and its terminal timestamps."""
    status = str(status or "").strip()
    if status not in BENCHMARK_SCORING_REVISION_STATUSES:
        raise ValueError(f"未知评分版本状态: {status}")
    started_at = _now() if status == "running" else None
    finished_at = _now() if status in ("completed", "interrupted", "error") else None
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM benchmark_scoring_revisions WHERE id = ?",
            (as_int(revision_id),),
        ).fetchone()
        if not row:
            raise ValueError("评分版本不存在")
        conn.execute(
            """
            UPDATE benchmark_scoring_revisions
            SET status = ?,
                started_at = CASE WHEN ? IS NULL THEN started_at ELSE COALESCE(started_at, ?) END,
                finished_at = CASE WHEN ? IS NULL THEN NULL ELSE COALESCE(finished_at, ?) END,
                error_json = ?
            WHERE id = ?
            """,
            (
                status, started_at, started_at, finished_at, finished_at,
                json.dumps(error_json or {}, ensure_ascii=False), as_int(revision_id),
            ),
        )
    return get_benchmark_scoring_revision(revision_id)


def set_scoring_revision_status(*args, **kwargs):
    """Compatibility alias for :func:`set_benchmark_scoring_revision_status`."""
    return set_benchmark_scoring_revision_status(*args, **kwargs)


def set_active_scoring_revision(run_id, revision_key):
    """Atomically make a completed scoring revision the report source."""
    revision_key = str(revision_key or "").strip()
    with get_connection() as conn:
        revision = conn.execute(
            """
            SELECT id, status FROM benchmark_scoring_revisions
            WHERE run_id = ? AND revision_key = ?
            """,
            (as_int(run_id), revision_key),
        ).fetchone()
        if not revision:
            raise ValueError("评分版本不存在")
        if revision["status"] != "completed":
            raise ValueError("只有已完成的评分版本可以设为 active")
        cursor = conn.execute(
            """
            UPDATE benchmark_runs SET active_scoring_revision = ? WHERE id = ?
            """,
            (revision_key, as_int(run_id)),
        )
        if cursor.rowcount != 1:
            raise ValueError("运行不存在")
    return get_benchmark_run(run_id)


def activate_scoring_revision(*args, **kwargs):
    """Compatibility alias for :func:`set_active_scoring_revision`."""
    return set_active_scoring_revision(*args, **kwargs)


def _case_revision_snapshot(case):
    """Return JSON-safe mutable case fields for audit records."""
    if case is None:
        return {}
    fields = (
        "id", "suite_id", "paper_ref_id", "track", "position", "kind",
        "question", "reference_answer", "evidence", "rubric", "requires_reject",
        "status", "review_note", "reviewed_at", "author_raw",
    )
    return {key: case.get(key) for key in fields if key in case}


def _append_case_revision_conn(
    conn, case_id, action, before, after, actor_user_id=None,
    actor_username="", note="",
):
    case_id = as_int(case_id)
    case = conn.execute(
        "SELECT suite_id FROM benchmark_cases WHERE id = ?", (case_id,)
    ).fetchone()
    if not case:
        raise ValueError("题目不存在")
    revision = conn.execute(
        """
        SELECT COALESCE(MAX(revision), 0) + 1 AS next_revision
        FROM benchmark_case_revisions WHERE case_id = ?
        """,
        (case_id,),
    ).fetchone()["next_revision"]
    cursor = conn.execute(
        """
        INSERT INTO benchmark_case_revisions (
            case_id, suite_id, revision, action, actor_user_id, actor_username,
            before_json, after_json, note
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            case_id, case["suite_id"], as_int(revision), str(action or ""),
            as_int(actor_user_id) if actor_user_id is not None else None,
            str(actor_username or ""),
            json.dumps(_case_revision_snapshot(before), ensure_ascii=False),
            json.dumps(_case_revision_snapshot(after), ensure_ascii=False),
            str(note or ""),
        ),
    )
    return cursor.lastrowid


def append_benchmark_case_revision(
    case_id, action, before=None, after=None, actor_user_id=None,
    actor_username="", note="",
):
    """Append one immutable case edit/review audit record."""
    with get_connection() as conn:
        revision_id = _append_case_revision_conn(
            conn, case_id, action, before or {}, after or {},
            actor_user_id=actor_user_id, actor_username=actor_username, note=note,
        )
    return get_benchmark_case_revision(revision_id)


def append_case_revision(*args, **kwargs):
    """Compatibility alias for :func:`append_benchmark_case_revision`."""
    return append_benchmark_case_revision(*args, **kwargs)


def get_benchmark_case_revision(revision_id):
    """Read one case audit record with decoded snapshots."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM benchmark_case_revisions WHERE id = ?",
            (as_int(revision_id),),
        ).fetchone()
    return _decode_case_revision(row) if row else None


def list_benchmark_case_revisions(case_id):
    """List audit records for one case in revision order."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM benchmark_case_revisions
            WHERE case_id = ? ORDER BY revision, id
            """,
            (as_int(case_id),),
        ).fetchall()
    return [_decode_case_revision(row) for row in rows]


def list_case_revisions(*args, **kwargs):
    """Compatibility alias for :func:`list_benchmark_case_revisions`."""
    return list_benchmark_case_revisions(*args, **kwargs)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _loads(value, default):
    if not value:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _sanitize_actual_params(value):
    """Keep candidate request snapshots free of prompts and credentials."""
    if isinstance(value, dict):
        return {
            str(key): _sanitize_actual_params(item)
            for key, item in value.items()
            if str(key).lower() not in {"messages", "api_key", "apikey", "authorization"}
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_actual_params(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _decode_case(row):
    item = dict(row)
    item["evidence"] = _loads(item.get("evidence"), [])
    item["rubric"] = _loads(item.get("rubric"), {})
    item["author_raw"] = _loads(item.get("author_raw"), {})
    return item


def _decode_candidate(row):
    item = dict(row)
    item["actual_params"] = _loads(item.get("actual_params"), {})
    return item


def _decode_response(row):
    item = dict(row)
    item.setdefault("reused_from_response_id", None)
    item.setdefault("retry_count", 0)
    item.setdefault("error_json", {})
    item["prompt_snapshot"] = _loads(item.get("prompt_snapshot"), [])
    item["parsed"] = _loads(item.get("parsed"), {})
    item["usage_json"] = _loads(item.get("usage_json"), {})
    item["error_json"] = _loads(item.get("error_json"), {})
    return item


def _decode_judgment(row):
    item = dict(row)
    item.setdefault("actor_user_id", None)
    item.setdefault("actor_username", "")
    item["condition_scores"] = _loads(item.get("condition_scores"), [])
    item["raw_json"] = _loads(item.get("raw_json"), {})
    return item


def _decode_scoring_revision(row):
    item = dict(row)
    for key in (
        "primary_route_snapshot", "review_route_snapshot",
        "primary_prompt_snapshot", "review_prompt_snapshot", "error_json",
    ):
        item[key] = _loads(item.get(key), {})
    return item


def _decode_case_revision(row):
    item = dict(row)
    item["before_json"] = _loads(item.get("before_json"), {})
    item["after_json"] = _loads(item.get("after_json"), {})
    return item
