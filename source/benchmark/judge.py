"""Judging: primary/review/human judgments and per-case scoring."""

import json
import logging
import math
import hashlib

from .json_support import _clean_json_content, _extract_first_json_object

from ._ai import RetryFailed, call_model_with_retries
from .config import (
    build_actual_request_params, get_route_config, get_route_prompts,
    resolve_model_config,
)

logger = logging.getLogger(__name__)

MET_VALUES = {"met": 1.0, "partial": 0.5, "unmet": 0.0}
HALLUCINATION_CAP = 60.0
LOW_CONFIDENCE_THRESHOLD = 0.7


def scoring_revision_key(suite_checksum=""):
    """Hash judge routes and prompts without credentials or dynamic output."""
    payload = {
        "suite_checksum": str(suite_checksum or ""),
        "routes": {
            key: _safe_route_snapshot(key)
            for key in ("benchmark_judge", "benchmark_judge_review")
        },
        "prompts": get_route_prompts(),
    }

    def sanitize(value):
        if isinstance(value, dict):
            return {
                str(key): sanitize(item)
                for key, item in value.items()
                if str(key).lower() not in {"api_key", "apikey", "authorization"}
            }
        if isinstance(value, (list, tuple)):
            return [sanitize(item) for item in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)

    canonical = json.dumps(sanitize(payload), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def _safe_route_snapshot(route_key):
    cfg = resolve_model_config(get_route_config(route_key))
    return {
        "provider_key": cfg.get("provider_key", ""),
        "provider_name": cfg.get("provider_name", ""),
        "model": cfg.get("model", ""),
        "is_thinking": bool(cfg.get("is_thinking")),
        "thinking_effort": cfg.get("thinking_effort", "medium"),
        "actual_params": build_actual_request_params(cfg),
    }


def _judge_payload(track, paper, cases):
    return {
        "track": track,
        "paper": {"arxiv_id": paper.get("arxiv_id", ""), "title": paper.get("title", "")},
        "cases": cases,
    }


def _build_cases(paper_cases, response_by_kind):
    """把题目与候选输出组合成裁判输入；返回 (payload_cases, missing)。"""
    payload_cases = []
    missing = []
    for case in paper_cases:
        output = response_by_kind.get(case["kind"])
        if output is None or not str(output or "").strip():
            missing.append(case)
            continue
        payload_cases.append({
            "id": case["id"],
            "kind": case["kind"],
            "position": case.get("position", 0),
            "question": case.get("question", ""),
            "reference_answer": case.get("reference_answer", ""),
            "evidence": case.get("evidence", []),
            "rubric": case.get("rubric", {}),
            "requires_reject": bool(case.get("requires_reject")),
            "candidate_output": str(output),
        })
    return payload_cases, missing


def _call_judge(route_key, messages, paper_ref):
    cfg = resolve_model_config(get_route_config(route_key))
    content, usage = call_model_with_retries(cfg, messages, route_key, paper_ref=paper_ref)
    return json.loads(_clean_json_content(_extract_first_json_object(content))), usage


def _judge_messages(route_key, payload):
    profile = get_route_prompts()[route_key]
    return [
        {"role": "system", "content": profile.get("system", "")},
        {"role": "user", "content": profile.get("instruction", "")},
        {"role": "user", "content": "待评分输入（JSON，固定字段顺序）：\n" + json.dumps(payload, ensure_ascii=False, indent=2)},
    ]


def _score_for_case(case, score_entry):
    """由裁判逐条件结果计算单题分数；严重幻觉时最高 60 分。"""
    returned_conditions = score_entry.get("conditions") or []
    if isinstance(returned_conditions, dict):
        returned_conditions = [returned_conditions]
    rubric_conditions = ((case.get("rubric") or {}).get("conditions") or [])
    if isinstance(rubric_conditions, dict):
        rubric_conditions = [rubric_conditions]

    # The frozen rubric is the denominator.  A missing/invalid judge entry is
    # explicitly unmet rather than silently dropping the condition.
    expected_conditions = rubric_conditions or returned_conditions
    by_text = {}
    for condition in returned_conditions:
        key = str(condition.get("condition") or condition.get("text") or "").strip()
        if key:
            by_text.setdefault(key, condition)

    def _weight(condition):
        try:
            value = float(condition.get("weight", 1.0))
        except (TypeError, ValueError):
            value = 1.0
        return value if value > 0 else 0.0

    def _value(condition):
        met = condition.get("met") if condition is not None else None
        if isinstance(met, bool):
            return 1.0 if met else 0.0
        if isinstance(met, (int, float)):
            return {1: 1.0, 0.5: 0.5, 0: 0.0}.get(met)
        return MET_VALUES.get(str(met).strip().lower())

    condition_scores = []
    total = 0.0
    weight_sum = 0.0
    for index, rubric_condition in enumerate(expected_conditions):
        text = str(
            rubric_condition.get("text")
            or rubric_condition.get("condition")
            or ""
        ).strip()
        returned = by_text.get(text)
        if returned is None and not rubric_conditions and index < len(returned_conditions):
            returned = returned_conditions[index]
        value = _value(returned)
        if value is None:
            value = 0.0
        weight = _weight(rubric_condition)
        note = (returned or {}).get("note") or (
            "缺少该条件判定" if returned is None else ""
        )
        condition_scores.append({
            "condition": text or str(
                (returned or {}).get("condition")
                or (returned or {}).get("text")
                or ""
            ),
            "met": value,
            "weight": weight,
            "critical": bool(rubric_condition.get("critical")),
            "note": str(note),
        })
        total += value * weight
        weight_sum += weight
    if weight_sum <= 0 and expected_conditions:
        # A malformed all-zero rubric remains deterministic and does not
        # create a divide-by-zero escape hatch; use equal weights as fallback.
        weight_sum = float(len(expected_conditions))
        total = sum(item["met"] for item in condition_scores)
    score = (total / weight_sum * 100.0) if weight_sum > 0 else 0.0
    hallucination = bool(score_entry.get("hallucination_critical"))
    if hallucination:
        score = min(score, HALLUCINATION_CAP)
    confidence = score_entry.get("confidence")
    try:
        confidence = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        confidence = None
    return {
        "score": round(score, 1),
        "condition_scores": condition_scores,
        "hallucination_critical": hallucination,
        "confidence": confidence,
        "note": str(score_entry.get("note") or ""),
    }


def _condition_map(judgment):
    """Normalize a stored judgment's condition-level values for comparison."""
    return {
        str(item.get("condition") or item.get("text") or "").strip(): item.get("met")
        for item in (judgment or {}).get("condition_scores", [])
        if str(item.get("condition") or item.get("text") or "").strip()
    }


def _merge_judgment_pair(primary, review):
    """Resolve primary/review output without ranking a condition conflict."""
    primary_map = _condition_map(primary)
    review_map = _condition_map(review)
    disagreement = primary_map != review_map
    if disagreement:
        return {
            "score": None,
            "needs_human_review": True,
            "primary_score": primary.get("score") if primary else None,
            "review_score": review.get("score") if review else None,
        }
    chosen = review or primary
    return {
        "score": chosen.get("score") if chosen else None,
        "needs_human_review": False,
        "primary_score": primary.get("score") if primary else None,
        "review_score": review.get("score") if review else None,
    }


def _judge_group(run_id, route_key, judge_role, scoring_revision, group, paper_ref):
    """对 (候选, 论文, 轨道, 重复槽) 执行一次裁判调用；缺题内容直接计零。"""
    from source.storage.benchmark import save_benchmark_judgment

    from source.storage.benchmark import get_response_judgment

    payload_cases, missing = _build_cases(group["cases"], group["output_by_kind"])
    expected_response_cases = [
        (group["response_ids"].get(case["kind"]), case["id"])
        for case in payload_cases
        if group["response_ids"].get(case["kind"]) is not None
    ]
    if expected_response_cases and all(
        get_response_judgment(response_id, judge_role, scoring_revision, case_id)
        for response_id, case_id in expected_response_cases
    ):
        return 0
    for case in missing:
        response_id = group["response_ids"].get(case["kind"])
        if response_id is None:
            continue  # 候选响应缺失属于基础设施失败，不计零
        response = next(
            (item for item in group.get("responses", []) if item.get("id") == response_id),
            None,
        )
        if response and response.get("status") == "retry_failed":
            # A temporary provider failure is infrastructure state, not a
            # candidate score.  Leave the case unjudged for later recovery.
            continue
        save_benchmark_judgment(
            run_id, response_id, "system", route_key,
            scoring_revision, case["id"], [], 0.0, False, None,
            {"reason": "missing_q"}, "缺题计零",
        )
    if not payload_cases:
        return 0
    payload = _judge_payload(group["track"], group["paper"], payload_cases)
    try:
        result, _usage = _call_judge(route_key, _judge_messages(route_key, payload), paper_ref)
    except RetryFailed:
        raise
    except Exception as exc:
        logger.warning(f"benchmark judge call failed for {group['paper_key']}/{group['track']}: {exc}")
        return 0
    scores = result.get("scores") or []
    by_kind = {str(entry.get("kind")): entry for entry in scores}
    # 位置兜底：裁判可能把 kind 重命名（如 chat_round1 → q1），按输入顺序对齐
    if len(scores) == len(payload_cases):
        for case, entry in zip(payload_cases, scores):
            by_kind.setdefault(str(case["kind"]), entry)
    for case in payload_cases:
        entry = by_kind.get(str(case["kind"]))
        if entry is None:
            save_benchmark_judgment(
                run_id, group["response_ids"].get(case["kind"]), judge_role, route_key,
                scoring_revision, case["id"], [], 0.0, False, None,
                {"reason": "judge_missing_kind", "judge_response": result},
                "裁判未返回该题判定",
            )
            continue
        result_data = _score_for_case(case, entry)
        save_benchmark_judgment(
            run_id, group["response_ids"].get(case["kind"]), judge_role, route_key,
            scoring_revision, case["id"], result_data["condition_scores"],
            result_data["score"], result_data["hallucination_critical"],
            result_data["confidence"], entry, result_data["note"],
        )
    return len(scores)


def build_groups(run, responses):
    """把运行响应按 (候选, 论文, 轨道, 重复槽) 分组，附带该组题目与输出。"""
    from source.storage.benchmark import get_benchmark_case

    candidate_by_id = {candidate["id"]: candidate for candidate in run.get("candidates", [])}
    groups = []
    by_key = {}
    for response in responses:
        key = (response["candidate_id"], response["paper_ref_id"],
               response["track"], response["repeat_index"])
        by_key.setdefault(key, []).append(response)
    for (candidate_id, paper_ref_id, track, repeat_index), response_list in by_key.items():
        paper = next(
            (p for p in run["papers"] if p["id"] == paper_ref_id), None
        )
        if paper is None:
            continue
        cases = [
            case for case in run["cases"]
            if case["paper_ref_id"] == paper_ref_id and case["track"] == track
        ]
        cases.sort(key=lambda case: case["position"])
        response_by_round = {r["round_index"]: r for r in response_list if r.get("round_index")}
        output_by_kind = {}
        response_ids = {}
        for case in cases:
            if track == "deep_reading":
                response = response_list[0] if response_list else None
                if response is not None:
                    output_by_kind[case["kind"]] = (response.get("parsed") or {}).get(case["kind"], "")
                    response_ids[case["kind"]] = response["id"]
            else:
                response = response_by_round.get(case["position"])
                if response is not None:
                    output_by_kind[case["kind"]] = (response.get("parsed") or {}).get("answer", "")
                    response_ids[case["kind"]] = response["id"]
        groups.append({
            "candidate_id": candidate_id,
            "candidate_provider_key": (candidate_by_id.get(candidate_id) or {}).get("provider_key", ""),
            "paper_ref_id": paper_ref_id,
            "paper": paper,
            "paper_key": paper.get("paper_key", ""),
            "track": track,
            "repeat_index": repeat_index,
            "cases": cases,
            "responses": response_list,
            "output_by_kind": output_by_kind,
            "response_ids": response_ids,
        })
    return groups


def review_sampling(groups, judgments, judge_provider_keys=None):
    """Select review groups with complete anomaly coverage.

    The 10% floor applies only to ordinary groups.  Every low-confidence,
    critical-hallucination, format-anomalous, or same-provider group is
    always included, even when that exceeds the nominal sample cap.
    """
    judge_provider_keys = {
        str(key) for key in (judge_provider_keys or []) if str(key or "").strip()
    }
    flagged_response_ids = {
        judgment["response_id"]
        for judgment in judgments
        if judgment.get("judge_role") == "primary"
        and (
            (judgment.get("confidence") is not None
             and judgment.get("confidence") < LOW_CONFIDENCE_THRESHOLD)
            or judgment.get("hallucination_critical")
        )
    }

    anomalous_response_ids = {
        response_id
        for group in groups
        for response_id in group.get("response_ids", {}).values()
        if any(
            response.get("id") == response_id and response.get("status") != "ok"
            for response in group.get("responses", [])
        )
    }

    by_group = {}
    for group in groups:
        key = (group["candidate_id"], group["track"])
        by_group.setdefault(key, []).append(group)
    sampled = []
    for key, group_list in by_group.items():
        count = max(1, math.ceil(len(group_list) * 0.1))
        flagged = [g for g in group_list if any(
            response_id in flagged_response_ids or response_id in anomalous_response_ids
            for response_id in g["response_ids"].values()
        ) or (
            bool(judge_provider_keys)
            and g.get("candidate_provider_key") in judge_provider_keys
        )]
        # A format anomaly can exist even when the response was not represented
        # in response_ids (for example an empty chat answer); include the group
        # directly as a second safety net.
        flagged.extend(
            g for g in group_list
            if g not in flagged and any(
                response.get("status") != "ok" for response in g.get("responses", [])
            )
        )
        # Preserve insertion order while de-duplicating flagged groups.
        flagged = list(dict.fromkeys(id(g) for g in flagged))
        flagged = [next(g for g in group_list if id(g) == group_id) for group_id in flagged]
        others = [g for g in group_list if g not in flagged]
        # The ordinary floor is a separate sample from the anomaly set.  An
        # anomalous group must never consume the candidate/track's baseline
        # 10% slot; this keeps the nominal sample while adding every flagged
        # group, even when the anomaly count is already above that floor.
        sampled.extend(flagged + others[:count])
    return sampled


def judge_run(run):
    """对一次运行执行主裁判与抽样复核；返回判定统计。"""
    from source.storage.benchmark import (
        create_benchmark_scoring_revision,
        get_run_judgments,
        get_scoring_revision,
        set_active_scoring_revision,
        set_benchmark_scoring_revision_status,
    )

    responses = run["responses"]
    groups = build_groups(run, responses)
    scoring_revision = scoring_revision_key((run.get("suite") or {}).get("suite_checksum"))
    existing_revision = get_scoring_revision(run["id"], scoring_revision)
    if existing_revision and existing_revision.get("status") == "completed":
        # A route may be switched back to a previously completed revision.
        # Make that revision authoritative before returning the reuse result.
        set_active_scoring_revision(run["id"], scoring_revision)
        return {"judged_groups": 0, "reviewed_groups": 0, "judged_cases": 0,
                "scoring_revision": scoring_revision, "reused": True}
    revision = create_benchmark_scoring_revision(
        run["id"], scoring_revision,
        primary_route_key="benchmark_judge", review_route_key="benchmark_judge_review",
        primary_route_snapshot=_safe_route_snapshot("benchmark_judge"),
        review_route_snapshot=_safe_route_snapshot("benchmark_judge_review"),
        primary_prompt_snapshot=get_route_prompts().get("benchmark_judge"),
        review_prompt_snapshot=get_route_prompts().get("benchmark_judge_review"),
    )
    set_benchmark_scoring_revision_status(revision["id"], "running")

    try:
        judged = 0
        for group in groups:
            if not group["cases"]:
                continue
            paper_ref = {"paper_id": group["paper"].get("paper_id"), "arxiv_id": group["paper"].get("arxiv_id", "")}
            judged += _judge_group(
                run["id"], "benchmark_judge", "primary", scoring_revision, group, paper_ref
            )

        primary_judgments = [
            item for item in get_run_judgments(run["id"])
            if item.get("scoring_revision") == scoring_revision
        ]
        judge_provider_keys = set()
        for route_key in ("benchmark_judge", "benchmark_judge_review"):
            try:
                route_cfg = resolve_model_config(get_route_config(route_key))
                if route_cfg.get("provider_key"):
                    judge_provider_keys.add(route_cfg["provider_key"])
            except Exception:
                continue
        sampled = review_sampling(groups, primary_judgments, judge_provider_keys=judge_provider_keys)
        for group in sampled:
            paper_ref = {"paper_id": group["paper"].get("paper_id"), "arxiv_id": group["paper"].get("arxiv_id", "")}
            _judge_group(
                run["id"], "benchmark_judge_review", "review", scoring_revision, group, paper_ref
            )
        set_benchmark_scoring_revision_status(revision["id"], "completed")
        set_active_scoring_revision(run["id"], scoring_revision)
        return {"judged_groups": len(groups), "reviewed_groups": len(sampled),
                "judged_cases": judged, "scoring_revision": scoring_revision}
    except Exception as exc:
        set_benchmark_scoring_revision_status(
            revision["id"], "error", {"message": str(exc), "type": type(exc).__name__},
        )
        raise


def _validate_human_conditions(case, condition_scores):
    """Require exactly one normalized 0/0.5/1 value for every rubric item."""
    rubric = ((case or {}).get("rubric") or {}).get("conditions") or []
    if isinstance(rubric, dict):
        rubric = [rubric]
    if not isinstance(condition_scores, list):
        raise ValueError("人工判定必须提交 condition_scores 数组")
    expected = [str(item.get("text") or item.get("condition") or "").strip() for item in rubric]
    if not expected or any(not item for item in expected):
        raise ValueError("题目 rubric 条件无效")
    seen = set()
    normalized = []
    for item in condition_scores:
        key = str(item.get("condition") or item.get("text") or "").strip()
        if key in seen or key not in expected:
            raise ValueError("人工判定条件必须与 rubric 一一对应")
        value = item.get("met")
        if isinstance(value, bool):
            value = 1.0 if value else 0.0
        try:
            value = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("人工判定 met 必须为 0、0.5 或 1") from exc
        if value not in (0.0, 0.5, 1.0):
            raise ValueError("人工判定 met 必须为 0、0.5 或 1")
        seen.add(key)
        normalized.append({"condition": key, "met": value, "note": str(item.get("note") or "")})
    if set(expected) != seen or len(expected) != len(seen):
        raise ValueError("人工判定必须覆盖全部 rubric 条件")
    return normalized


def set_human_judgment(run_id, response_id, case_id, condition_scores, notes="",
                       actor_user_id=None, actor_username="",
                       hallucination_critical=False):
    """人工覆盖判定：优先级最高，且不覆盖原始裁判记录。"""
    from source.storage.benchmark import save_benchmark_judgment

    from source.storage.benchmark import (
        get_benchmark_case, get_benchmark_run, get_benchmark_suite, get_response,
    )
    run = get_benchmark_run(run_id)
    if not run:
        raise ValueError("运行不存在")
    response = get_response(response_id)
    case = get_benchmark_case(case_id)
    if not response or response.get("run_id") != int(run_id):
        raise ValueError("response_id 不属于该运行")
    if not case or case.get("suite_id") != run.get("suite_id"):
        raise ValueError("case_id 不属于该运行题库")
    if response.get("paper_ref_id") != case.get("paper_ref_id") or response.get("track") != case.get("track"):
        raise ValueError("response_id 与 case_id 不匹配")
    suite = get_benchmark_suite(run["suite_id"])
    scoring_revision = str(run.get("active_scoring_revision") or "") or scoring_revision_key(
        (suite or {}).get("suite_checksum")
    )
    normalized = _validate_human_conditions(case, condition_scores)
    scored = _score_for_case(case, {
        "conditions": normalized,
        "hallucination_critical": bool(hallucination_critical),
    })
    save_benchmark_judgment(
        run_id, response_id, "human", "human", scoring_revision, case_id,
        scored["condition_scores"], scored["score"],
        bool(hallucination_critical), None,
        {"human": True, "hallucination_critical": bool(hallucination_critical)},
        str(notes or ""),
        actor_user_id=actor_user_id, actor_username=actor_username,
    )


def calibrate(run):
    """用人工判定计算裁判逐条件一致率与分数误差（pilot 校准）。"""
    judgments = _collect_judgments(run)
    pairs = []
    for case_id, by_response in judgments.items():
        for response_id, roles in by_response.items():
            human = roles.get("human")
            primary = roles.get("primary")
            if human is None or primary is None:
                continue
            pairs.append((primary, human))
    if not pairs:
        return {"compared": 0, "condition_agreement": None, "mean_score_error": None, "calibrated": False}
    condition_total = 0
    condition_agree = 0
    score_errors = []
    for primary, human in pairs:
        score_errors.append(abs(primary["score"] - human["score"]))
        primary_conditions = {c["condition"]: c["met"] for c in primary.get("condition_scores", [])}
        human_conditions = {c["condition"]: c["met"] for c in human.get("condition_scores", [])}
        # Only same-named conditions are comparable.  A historical human
        # judgment with an empty condition list remains readable, but cannot
        # inflate or dilute condition agreement.
        for key in set(primary_conditions) & set(human_conditions):
            if primary_conditions.get(key) == human_conditions.get(key):
                condition_agree += 1
            condition_total += 1
    agreement = condition_agree / condition_total if condition_total else None
    mean_error = sum(score_errors) / len(score_errors)
    calibrated = len(pairs) >= 10 and agreement is not None and agreement >= 0.8 and mean_error <= 10
    return {
        "compared": len(pairs),
        "condition_agreement": round(agreement, 3) if agreement is not None else None,
        "mean_score_error": round(mean_error, 2),
        "calibrated": calibrated,
    }


def _collect_judgments(run):
    from source.storage.benchmark import get_run_judgments

    judgments = get_run_judgments(run["id"])
    active_revision = str(run.get("active_scoring_revision") or "").strip()
    if active_revision:
        judgments = [
            item for item in judgments
            if item.get("scoring_revision") == active_revision
        ]
    by_case = {}
    for judgment in judgments:
        by_case.setdefault(judgment["case_id"], {}).setdefault(
            judgment["response_id"], {}
        )[judgment["judge_role"]] = judgment
    return by_case
