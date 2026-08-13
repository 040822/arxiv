"""Judging: primary/review/human judgments and per-case scoring."""

import json
import logging
import math

from source.analysis.json_support import _clean_json_content, _extract_first_json_object

from ._ai import call_model
from .config import get_route_config, get_route_prompts, resolve_model_config

logger = logging.getLogger(__name__)

MET_VALUES = {"met": 1.0, "partial": 0.5, "unmet": 0.0}
HALLUCINATION_CAP = 60.0
LOW_CONFIDENCE_THRESHOLD = 0.7


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
    content, usage = call_model(cfg, messages, route_key, paper_ref=paper_ref)
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
    conditions = score_entry.get("conditions") or []
    condition_scores = []
    total = 0.0
    weight_sum = 0.0
    for condition in conditions:
        met = condition.get("met")
        if isinstance(met, (int, float)) and not isinstance(met, bool):
            value = {1: 1.0, 0.5: 0.5, 0: 0.0}.get(met)
        else:
            value = MET_VALUES.get(str(met).lower())
        if value is None:
            continue
        condition_scores.append({
            "condition": str(condition.get("condition") or condition.get("text") or ""),
            "met": value,
            "note": str(condition.get("note") or ""),
        })
        weight = 1.0 / len(conditions)
        total += value * weight
        weight_sum += weight
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


def _judge_group(run_id, route_key, judge_role, scoring_revision, group, paper_ref):
    """对 (候选, 论文, 轨道, 重复槽) 执行一次裁判调用；缺题内容直接计零。"""
    from source.storage.benchmark import save_benchmark_judgment

    payload_cases, missing = _build_cases(group["cases"], group["output_by_kind"])
    for case in missing:
        response_id = group["response_ids"].get(case["kind"])
        if response_id is None:
            continue  # 候选响应缺失属于基础设施失败，不计零
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
            "paper_ref_id": paper_ref_id,
            "paper": paper,
            "paper_key": paper.get("paper_key", ""),
            "track": track,
            "repeat_index": repeat_index,
            "cases": cases,
            "output_by_kind": output_by_kind,
            "response_ids": response_ids,
        })
    return groups


def review_sampling(groups, judgments):
    """抽样复核：每候选每轨道至少 10%（下限 1 组），并覆盖低置信度/严重幻觉。"""
    flagged_response_ids = {
        judgment["response_id"]
        for judgment in judgments
        if judgment["judge_role"] == "primary"
        and (
            (judgment["confidence"] is not None and judgment["confidence"] < LOW_CONFIDENCE_THRESHOLD)
            or judgment["hallucination_critical"]
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
            response_id in flagged_response_ids for response_id in g["response_ids"].values()
        )]
        others = [g for g in group_list if g not in flagged]
        sampled.extend((flagged + others)[:count])
    return sampled


def judge_run(run):
    """对一次运行执行主裁判与抽样复核；返回判定统计。"""
    from source.storage.benchmark import get_run_judgments

    responses = run["responses"]
    groups = build_groups(run, responses)
    scoring_revision = str((run.get("suite") or {}).get("scoring_revision") or "") or "pprb-r1"

    judged = 0
    for group in groups:
        if not group["cases"]:
            continue
        paper_ref = {"paper_id": group["paper"].get("paper_id"), "arxiv_id": group["paper"].get("arxiv_id", "")}
        judged += _judge_group(
            run["id"], "benchmark_judge", "primary", scoring_revision, group, paper_ref
        )

    primary_judgments = get_run_judgments(run["id"])
    sampled = review_sampling(groups, primary_judgments)
    for group in sampled:
        paper_ref = {"paper_id": group["paper"].get("paper_id"), "arxiv_id": group["paper"].get("arxiv_id", "")}
        _judge_group(
            run["id"], "benchmark_judge_review", "review", scoring_revision, group, paper_ref
        )
    return {"judged_groups": len(groups), "reviewed_groups": len(sampled), "judged_cases": judged}


def set_human_judgment(run_id, response_id, case_id, score, notes=""):
    """人工覆盖判定：优先级最高，且不覆盖原始裁判记录。"""
    from source.storage.benchmark import save_benchmark_judgment

    try:
        score = max(0.0, min(100.0, float(score)))
    except (TypeError, ValueError):
        raise ValueError("人工评分必须是 0-100 的数字")
    run = None
    from source.storage.benchmark import get_benchmark_run
    run = get_benchmark_run(run_id)
    scoring_revision = str((run.get("suite") or {}).get("scoring_revision") or "") or "pprb-r1"
    save_benchmark_judgment(
        run_id, response_id, "human", "human", scoring_revision, case_id,
        [], score, False, None, {"human": True}, str(notes or ""),
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
        condition_total += 1
        condition_agree += 1 if abs(primary["score"] - human["score"]) <= 10 else 0
        score_errors.append(abs(primary["score"] - human["score"]))
        primary_conditions = {c["condition"]: c["met"] for c in primary.get("condition_scores", [])}
        human_conditions = {c["condition"]: c["met"] for c in human.get("condition_scores", [])}
        for key in set(primary_conditions) | set(human_conditions):
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
    by_case = {}
    for judgment in judgments:
        by_case.setdefault(judgment["case_id"], {}).setdefault(
            judgment["response_id"], {}
        )[judgment["judge_role"]] = judgment
    return by_case
