"""Paper chat, recall quiz, grading, and Socratic learning flows."""

import json
import logging

from source.settings import get_prompt_profile

from .core import _call_ai_raw, build_paper_learning_messages
from .json_support import _clean_json_content

logger = logging.getLogger(__name__)

def _parse_ai_json(content):
    cleaned = _clean_json_content(content)
    return json.loads(cleaned)


def _learning_meta(text_info, usage):
    return {
        "used_pdf_cache": bool(text_info.get("used_pdf_cache")),
        "used_pdf_full_text": bool(text_info.get("used_pdf_full_text")),
        **(usage or {}),
    }


def _quiz_question_count(mode):
    return 6 if mode == "standard6" else 3


def _normalise_quiz_questions(result, mode):
    count = _quiz_question_count(mode)
    questions = result.get("questions", []) if isinstance(result, dict) else []
    normalised = []
    for item in questions:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        if not question:
            continue
        expected = item.get("expected_points", [])
        if isinstance(expected, str):
            expected = [expected]
        elif not isinstance(expected, list):
            expected = []
        normalised.append({
            "question": question,
            "expected_points": [str(point).strip() for point in expected if str(point).strip()],
        })
        if len(normalised) >= count:
            break
    return normalised


def _normalise_feedback_result(result):
    result = result if isinstance(result, dict) else {}
    try:
        score = int(round(float(result.get("score", 0) or 0)))
    except (TypeError, ValueError):
        score = 0

    def list_value(key):
        value = result.get(key, [])
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if value:
            return [str(value).strip()]
        return []

    return {
        "score": max(0, min(5, score)),
        "feedback": str(result.get("feedback") or "").strip(),
        "correct_points": list_value("correct_points"),
        "missing_points": list_value("missing_points"),
        "misconceptions": list_value("misconceptions"),
        "improved_answer": str(result.get("improved_answer") or "").strip(),
    }


def _normalise_socratic_result(result):
    feedback = _normalise_feedback_result(result)
    feedback["next_question"] = str((result or {}).get("next_question") or "").strip()
    if not feedback["next_question"]:
        feedback["next_question"] = "请你先用自己的话概括这篇论文最核心的问题和方法。"
    return feedback


def chat_about_paper(paper_data, user_message, history=None):
    """基于 PDF 全文上下文与用户自由讨论论文。"""
    history = (history or [])[-12:]
    volatile = []
    for item in history:
        role = item.get("role") if isinstance(item, dict) else ""
        if role not in {"user", "assistant"}:
            continue
        volatile.append({"role": role, "content": str(item.get("content") or "")})
    volatile.append({"role": "user", "content": str(user_message or "").strip()})

    profile = get_prompt_profile("paper_chat")
    messages, text_info = build_paper_learning_messages(
        paper_data,
        "paper_chat",
        "论文自由讨论任务说明：\n" + profile.get("instruction", ""),
        volatile,
    )
    try:
        content, usage = _call_ai_raw(messages, paper_data, "paper_chat")
        return content.strip(), None, _learning_meta(text_info, usage)
    except Exception as e:
        logger.error(f"Paper chat error for {paper_data.get('arxiv_id', 'unknown')}: {e}")
        return None, str(e), _learning_meta(text_info, {})


def generate_paper_quiz(paper_data, mode="quick3"):
    """按需生成 3 题或 6 题主动回忆练习。"""
    mode = mode if mode in {"quick3", "standard6"} else "quick3"
    count = _quiz_question_count(mode)
    profile = get_prompt_profile("paper_quiz")
    volatile = [{
        "role": "user",
        "content": f"""本轮任务：生成 {count} 道论文主动回忆练习题。

请严格返回合法 JSON，不要返回额外解释：
{{
  "questions": [
    {{"question": "问题文本", "expected_points": ["参考要点1", "参考要点2"]}}
  ]
}}

要求：
- 题目必须覆盖论文主线、核心方法、关键实验、局限或适用边界
- 问题应促使用户用自己的话解释，不要只问名词定义
- expected_points 用于后续评分，写成简短要点
- 必须恰好返回 {count} 道题
""",
    }]
    messages, text_info = build_paper_learning_messages(
        paper_data,
        "paper_quiz",
        "论文主动问答任务说明：\n" + profile.get("instruction", ""),
        volatile,
    )
    try:
        content, usage = _call_ai_raw(messages, paper_data, "paper_quiz")
        questions = _normalise_quiz_questions(_parse_ai_json(content), mode)
        if len(questions) != count:
            return None, f"模型返回题目数量不正确：{len(questions)}/{count}", _learning_meta(text_info, usage)
        return questions, None, _learning_meta(text_info, usage)
    except Exception as e:
        logger.error(f"Paper quiz generation error for {paper_data.get('arxiv_id', 'unknown')}: {e}")
        return None, str(e), _learning_meta(text_info, {})


def grade_quiz_answer(paper_data, question, answer_text):
    """评价用户对单题的回答，并给出纠错反馈。"""
    profile = get_prompt_profile("paper_quiz")
    volatile = [{
        "role": "user",
        "content": "本轮任务：评价用户答案。\n"
        + json.dumps({
            "question": question.get("question", ""),
            "expected_points": question.get("expected_points", ""),
            "user_answer": answer_text,
        }, ensure_ascii=False, indent=2)
        + """

请严格返回合法 JSON，不要返回额外解释：
{
  "score": 0,
  "feedback": "总体反馈",
  "correct_points": ["用户答对的点"],
  "missing_points": ["用户漏掉的关键点"],
  "misconceptions": ["用户可能存在的误解"],
  "improved_answer": "一段更好的参考答案"
}

score 必须是 0 到 5 的整数。
""",
    }]
    messages, text_info = build_paper_learning_messages(
        paper_data,
        "paper_quiz",
        "论文主动问答任务说明：\n" + profile.get("instruction", ""),
        volatile,
    )
    try:
        content, usage = _call_ai_raw(messages, paper_data, "paper_quiz")
        feedback = _normalise_feedback_result(_parse_ai_json(content))
        return feedback, None, _learning_meta(text_info, usage)
    except Exception as e:
        logger.error(f"Paper quiz grading error for {paper_data.get('arxiv_id', 'unknown')}: {e}")
        return None, str(e), _learning_meta(text_info, {})


def socratic_reply(paper_data, session_history=None, user_answer=None):
    """独立苏格拉底追问模式：根据历史和用户回答生成反馈与下一问。"""
    profile = get_prompt_profile("paper_quiz")
    volatile_payload = {
        "session_history": session_history or [],
        "user_answer": user_answer or "",
        "is_first_turn": not session_history and not user_answer,
    }
    volatile = [{
        "role": "user",
        "content": "本轮任务：进行独立苏格拉底式论文追问。\n"
        + json.dumps(volatile_payload, ensure_ascii=False, indent=2)
        + """

请严格返回合法 JSON，不要返回额外解释：
{
  "score": 0,
  "feedback": "如果这是第一轮，可为空；否则指出用户答案的亮点、缺口或误解",
  "correct_points": [],
  "missing_points": [],
  "misconceptions": [],
  "improved_answer": "如果这是第一轮，可为空；否则给出更好的回答",
  "next_question": "下一轮追问"
}

要求：
- 第一轮只提出一个能打开论文主线的问题
- 后续轮次根据用户回答继续追问，不要直接讲完整答案
- next_question 必须具体、可回答，避免泛泛而谈
""",
    }]
    messages, text_info = build_paper_learning_messages(
        paper_data,
        "paper_quiz",
        "论文主动问答任务说明：\n" + profile.get("instruction", ""),
        volatile,
    )
    try:
        content, usage = _call_ai_raw(messages, paper_data, "paper_quiz")
        feedback = _normalise_socratic_result(_parse_ai_json(content))
        return feedback, None, _learning_meta(text_info, usage)
    except Exception as e:
        logger.error(f"Paper Socratic error for {paper_data.get('arxiv_id', 'unknown')}: {e}")
        return None, str(e), _learning_meta(text_info, {})


