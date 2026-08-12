"""Case authoring: generate deep-reading reference answers and chat scripts."""

import json
import logging

from source.analysis.json_support import _clean_json_content

from ._ai import call_model
from .config import get_route_config, get_route_prompts, resolve_model_config
from .evidence import check_evidence, parse_deep_reading_questions

logger = logging.getLogger(__name__)

CHAT_ROUNDS = 3


def _paper_payload(paper):
    """构造出题用冻结论文上下文（与生产 deep_reading 相同的字段顺序）。"""
    authors = paper.get("authors") or []
    if isinstance(authors, list):
        authors = ", ".join(authors)
    return {
        "arxiv_id": paper.get("arxiv_id", ""),
        "title": paper.get("title", ""),
        "authors": authors,
        "abstract": paper.get("abstract", ""),
        "paper_text": paper.get("full_text", ""),
    }


def _call_author(route_key, messages, paper_ref):
    cfg = resolve_model_config(get_route_config(route_key))
    content, usage = call_model(cfg, messages, route_key, paper_ref=paper_ref)
    return json.loads(_clean_json_content(content)), usage


def _build_messages(profile, payload):
    return [
        {"role": "system", "content": profile.get("system", "")},
        {"role": "user", "content": "冻结论文上下文（JSON，固定字段顺序）：\n" + json.dumps(payload, ensure_ascii=False, indent=2)},
        {"role": "user", "content": profile.get("instruction", "")},
    ]


def _validate_rubric(rubric):
    """校验 rubric：conditions 非空、权重为正且总和约 1。"""
    conditions = (rubric or {}).get("conditions") or []
    if not isinstance(conditions, list) or not conditions:
        return False
    total = 0.0
    for condition in conditions:
        try:
            weight = float(condition.get("weight", 0))
        except (TypeError, ValueError):
            return False
        if weight <= 0 or not str(condition.get("text") or "").strip():
            return False
        total += weight
    return 0.9 <= total <= 1.1


def _review_evidence(paper, item, problems):
    """校验证据；证据缺失或全部失败时给 problems 追加原因，返回校验后的证据列表。"""
    evidence = item.get("evidence") or []
    if not isinstance(evidence, list) or not evidence:
        problems.append("证据为空，无法从全文验证")
        return []
    matched = check_evidence(evidence, paper.get("full_text", ""))
    failed = [fragment for fragment, ok in matched.items() if not ok]
    if failed:
        problems.append(f"证据无法在冻结全文中精确匹配（{len(failed)}/{len(evidence)} 条）")
        return [fragment for fragment, ok in matched.items() if ok]
    return evidence


def _save_case(paper, suite_id, track, position, kind, question, reference_answer,
               evidence, rubric, requires_reject, problems, author_raw):
    from source.storage.benchmark import add_benchmark_case, set_case_review

    case_id = add_benchmark_case({
        "suite_id": suite_id,
        "paper_ref_id": paper["id"],
        "track": track,
        "position": position,
        "kind": kind,
        "question": question,
        "reference_answer": reference_answer,
        "evidence": evidence,
        "rubric": rubric,
        "requires_reject": requires_reject,
        "author_raw": {"problems": problems, **author_raw},
    })
    if problems:
        set_case_review(case_id, "rejected", note="自动驳回：" + "；".join(problems))
    return {"position": position, "kind": kind, "ok": not problems, "problems": problems}


def generate_cases_for_paper(suite_id, paper, suite):
    """对一篇论文执行两次出题调用并写入题目；返回该论文的结果摘要。"""
    deep_questions = parse_deep_reading_questions(
        (suite.get("deep_reading_prompt") or {}).get("instruction", "")
    )
    summary = {"paper_key": paper["paper_key"], "deep_reading": [], "chat": []}
    payload = _paper_payload(paper)
    author_payload = {
        "paper": payload,
        "deep_reading_questions": [
            {"number": q["number"], "title": q["title"]} for q in deep_questions
        ],
    }
    profile = get_route_prompts()["benchmark_author"]
    try:
        result, _usage = _call_author(
            "benchmark_author", _build_messages(profile, author_payload), paper
        )
    except Exception as exc:
        logger.warning(f"benchmark author call failed for {paper['paper_key']}: {exc}")
        summary["error"] = f"出题调用失败: {exc}"
        return summary

    for item in result.get("deep_reading") or []:
        try:
            number = int(str(item.get("kind") or "").lstrip("q"))
        except (TypeError, ValueError):
            continue
        question = next(
            (q["title"] for q in deep_questions if q["number"] == number), ""
        )
        if not question:
            continue
        problems = []
        evidence = _review_evidence(paper, item, problems)
        if not _validate_rubric(item.get("rubric")):
            problems.append("rubric 缺少可判定条件或权重不合法")
        if not str(item.get("reference_answer") or "").strip():
            problems.append("参考答案为空")
        summary["deep_reading"].append(_save_case(
            paper, suite_id, "deep_reading", number, f"q{number}", question,
            str(item.get("reference_answer") or ""), evidence,
            item.get("rubric") or {}, bool(item.get("requires_reject")), problems,
            {"generated": True},
        ))

    for position, item in enumerate((result.get("chat_script") or [])[:CHAT_ROUNDS], start=1):
        problems = []
        evidence = _review_evidence(paper, item, problems)
        if not _validate_rubric(item.get("rubric")):
            problems.append("rubric 缺少可判定条件或权重不合法")
        if not str(item.get("question") or "").strip():
            problems.append("交流问题为空")
        if not str(item.get("reference_answer") or "").strip():
            problems.append("参考答案为空")
        summary["chat"].append(_save_case(
            paper, suite_id, "chat", position, f"chat_round{position}",
            str(item.get("question") or ""), str(item.get("reference_answer") or ""),
            evidence, item.get("rubric") or {}, bool(item.get("requires_reject")),
            problems, {"generated": True},
        ))
    return summary
