"""Report aggregation: per-case → per-paper → per-track scores and costs."""

import logging

logger = logging.getLogger(__name__)

PILOT_PAPER_LIMIT = 8


def _resolve_case_score(judgments_by_case):
    """Resolve a case, withholding a primary/review condition conflict."""
    from .judge import _merge_judgment_pair

    primary = judgments_by_case.get("primary")
    review = judgments_by_case.get("review")
    human = judgments_by_case.get("human")
    if human is not None:
        return human
    if primary is not None and review is not None:
        merged = _merge_judgment_pair(primary, review)
        if merged["needs_human_review"]:
            return None
        return review
    for role in ("human", "review", "system", "primary"):
        judgment = judgments_by_case.get(role)
        if judgment is not None:
            return judgment
    return None


def _case_resolution(judgments_by_case):
    """Return report fields for one response/case pair."""
    from .judge import _merge_judgment_pair

    primary = judgments_by_case.get("primary")
    review = judgments_by_case.get("review")
    human = judgments_by_case.get("human")
    if primary is not None and review is not None:
        merged = _merge_judgment_pair(primary, review)
        needs_human_review = bool(merged["needs_human_review"] and human is None)
    else:
        needs_human_review = False
    selected = human
    if selected is None and not needs_human_review:
        selected = review or judgments_by_case.get("system") or primary
    return {
        "judgment": selected,
        "score": selected.get("score") if selected is not None else None,
        "primary_score": primary.get("score") if primary is not None else None,
        "review_score": review.get("score") if review is not None else None,
        "needs_human_review": needs_human_review,
    }


def _collect(run):
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
        by_case.setdefault(
            (judgment["response_id"], judgment["case_id"]), {}
        )[judgment["judge_role"]] = judgment
    return by_case


def build_report(run):
    """聚合一次运行的评分、成本与版本信息，生成报告字典。"""
    from .judge import calibrate

    judgments_by_key = _collect(run)
    suite = run.get("suite") or {}
    cases = run["cases"]
    papers = run["papers"]
    responses = run["responses"]
    candidates = run["candidates"]

    response_by_id = {response["id"]: response for response in responses}
    case_by_id = {case["id"]: case for case in cases}
    paper_by_id = {paper["id"]: paper for paper in papers}

    calibration = calibrate(run)
    warnings = _warnings(run, calibration)
    tracks = {"deep_reading": [], "chat": []}

    for candidate in candidates:
        for track in ("deep_reading", "chat"):
            entry = _track_entry(
                candidate, track, run, case_by_id, paper_by_id, response_by_id,
                judgments_by_key, warnings,
            )
            tracks[track].append(entry)
    for track in tracks:
        tracks[track].sort(key=lambda item: item["score"] or -1, reverse=True)

    usage = _usage_totals(run, responses)
    return {
        "run_id": run["id"],
        "suite": {
            "id": suite.get("id"),
            "subset_name": suite.get("subset_name", ""),
            "version_label": suite.get("version_label", ""),
            "suite_checksum": suite.get("suite_checksum", ""),
        },
        "status": run.get("status"),
        "repeats": run.get("repeats"),
        "runner_version": run.get("runner_version", ""),
        "paper_count": len(papers),
        "is_pilot": len(papers) < PILOT_PAPER_LIMIT,
        "warnings": warnings,
        "tracks": tracks,
        "usage": usage,
        "calibration": calibration,
        "created_at": run.get("created_at"),
        "finished_at": run.get("finished_at"),
        "candidate_calls_made": int(run.get("candidate_calls_made") or 0),
        "max_calls": run.get("max_calls"),
    }


def _track_entry(candidate, track, run, case_by_id, paper_by_id, response_by_id,
                 judgments_by_key, warnings):
    papers = run["papers"]
    per_paper = {}
    per_case = []
    usage = {"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0,
             "cached_tokens": 0, "latency_ms": 0.0, "calls": 0,
             "continuation_count": 0, "retry_count": 0,
             "retry_failed": 0, "empty": 0}
    hallucination_critical = 0
    missing_qs = 0

    responses = [
        response for response in run["responses"]
        if response["candidate_id"] == candidate["id"] and response["track"] == track
    ]
    if not responses:
        warnings.append(f"候选「{candidate['label']}」轨道 {track} 无响应，未计分")
        return {"candidate_id": candidate["id"], "label": candidate["label"], "track": track,
                "score": None, "per_paper": {}, "per_case": [], "usage": usage,
                "hallucination_critical": 0, "missing_qs": 0}

    for response in responses:
        if response.get("reused_from_response_id") is not None:
            continue
        response_usage = response.get("usage_json") or {}
        usage["total_tokens"] += int(response_usage.get("total_tokens", 0))
        usage["prompt_tokens"] += int(response_usage.get("prompt_tokens", 0))
        usage["completion_tokens"] += int(response_usage.get("completion_tokens", 0))
        usage["cached_tokens"] += int(response_usage.get("cached_tokens", 0))
        usage["latency_ms"] += float(response.get("latency_ms") or 0)
        continuation_count = int(response.get("continuation_count") or 0)
        retry_count = int(response.get("retry_count") or 0)
        usage["calls"] += 1 + continuation_count + retry_count
        usage["continuation_count"] += continuation_count
        usage["retry_count"] += retry_count
        if response.get("status") == "retry_failed":
            usage["retry_failed"] += 1
        if response.get("status") == "empty":
            usage["empty"] += 1

    for paper in papers:
        paper_cases = [
            case for case in run["cases"]
            if case["paper_ref_id"] == paper["id"] and case["track"] == track
        ]
        paper_scores = []
        for case in paper_cases:
            if track == "deep_reading":
                response_ids = [
                    r["id"] for r in responses if r["paper_ref_id"] == paper["id"]
                ]
            else:
                response_ids = [
                    r["id"] for r in responses
                    if r["paper_ref_id"] == paper["id"] and r["round_index"] == case["position"]
                ]
            if not response_ids:
                per_case.append({
                    "paper_key": paper.get("paper_key", ""),
                    "paper_ref_id": paper.get("id"),
                    "response_id": None,
                    "case_id": case.get("id"),
                    "kind": case.get("kind", ""),
                    "position": case.get("position", 0),
                    "question": case.get("question", ""),
                    "score": None,
                    "primary_score": None,
                    "review_score": None,
                    "needs_human_review": False,
                    "judge": None,
                    "hallucination_critical": False,
                    "confidence": None,
                })
                continue
            for response_id in response_ids:
                resolution = _case_resolution(
                    judgments_by_key.get((response_id, case["id"]), {})
                )
                judgment = resolution["judgment"]
                score = resolution["score"]
                roles = judgments_by_key.get((response_id, case["id"]), {})
                per_case.append({
                    "paper_key": paper.get("paper_key", ""),
                    "paper_ref_id": paper.get("id"),
                    "response_id": response_id,
                    "case_id": case.get("id"),
                    "kind": case.get("kind", ""),
                    "position": case.get("position", 0),
                    "question": case.get("question", ""),
                    "score": score,
                    "primary_score": resolution["primary_score"],
                    "review_score": resolution["review_score"],
                    "needs_human_review": resolution["needs_human_review"],
                    "judge": judgment["judge_role"] if judgment else None,
                    "hallucination_critical": bool(
                        judgment and judgment["hallucination_critical"]
                    ) or any(
                        bool(item.get("hallucination_critical"))
                        for item in roles.values()
                    ),
                    "confidence": judgment["confidence"] if judgment else None,
                })
                if judgment:
                    if judgment["hallucination_critical"]:
                        hallucination_critical += 1
                    if (judgment.get("raw_json") or {}).get("reason") == "missing_q":
                        missing_qs += 1
                    if score is not None:
                        paper_scores.append(score)
        per_paper[paper.get("paper_key", "")] = round(sum(paper_scores) / len(paper_scores), 1) if paper_scores else None

    scored_papers = [value for value in per_paper.values() if value is not None]
    score = round(sum(scored_papers) / len(scored_papers), 1) if scored_papers else None

    usage["latency_ms"] = round(usage["latency_ms"], 1)
    return {
        "candidate_id": candidate["id"],
        "label": candidate["label"],
        "track": track,
        "score": score,
        "per_paper": per_paper,
        "per_case": per_case,
        "usage": usage,
        "hallucination_critical": hallucination_critical,
        "missing_qs": missing_qs,
    }


def _usage_totals(run, responses):
    totals = {"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0,
              "cached_tokens": 0, "latency_ms": 0.0, "retry_count": 0,
              "calls": sum(
                  0 if r.get("reused_from_response_id") is not None else (
                      1 + int(r.get("continuation_count") or 0)
                      + int(r.get("retry_count") or 0)
                  )
                  for r in responses
              )}
    for response in responses:
        if response.get("reused_from_response_id") is not None:
            continue
        usage = response.get("usage_json") or {}
        totals["total_tokens"] += int(usage.get("total_tokens", 0))
        totals["prompt_tokens"] += int(usage.get("prompt_tokens", 0))
        totals["completion_tokens"] += int(usage.get("completion_tokens", 0))
        totals["cached_tokens"] += int(usage.get("cached_tokens", 0))
        totals["latency_ms"] += float(response.get("latency_ms") or 0)
        totals["retry_count"] += int(response.get("retry_count") or 0)
    totals["latency_ms"] = round(totals["latency_ms"], 1)
    return totals


def _warnings(run, calibration):
    warnings = []
    suite = run.get("suite") or {}
    paper_count = len(run.get("papers") or [])
    if paper_count < PILOT_PAPER_LIMIT:
        warnings.append(f"当前样本 {paper_count} 篇论文，单篇/少量 pilot 不构成可靠模型排名")
    if not calibration.get("calibrated"):
        warnings.append("裁判未经校准（人工判定样本不足），结果可能带 LLM 裁判偏置")
    judge_provider = None
    try:
        from .config import get_route_config, resolve_model_config
        judge_cfg = resolve_model_config(get_route_config("benchmark_judge"))
        judge_provider = judge_cfg.get("provider_key")
    except Exception:
        pass
    for candidate in run.get("candidates") or []:
        if judge_provider and candidate.get("provider_key") == judge_provider:
            warnings.append(
                f"候选「{candidate.get('label')}」与主裁判同属供应商 {judge_provider}，存在同家族偏置风险"
            )
    max_tokens = {int(c.get("max_tokens") or 0) for c in run.get("candidates") or []}
    if len(max_tokens) > 1:
        warnings.append("候选输出上限不一致，属于非等预算比较")
    if not suite.get("scoring_revision"):
        warnings.append("缺少评分版本记录（scoring_revision）")
    return warnings
