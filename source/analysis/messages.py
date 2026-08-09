"""Stable/dynamic message construction for short and long input tasks."""

import json

from source.config import RATING_CRITERIA, TAG_CANDIDATES
from source.settings import get_prompt_profile


def _authors_text(paper_data):
    authors = paper_data.get("authors", "")
    if isinstance(authors, list):
        return ", ".join(authors)
    return authors


def _render_instruction(instruction):
    return (instruction or "").replace(
        "{tag_candidates}", ", ".join(TAG_CANDIDATES[:30]),
    ).replace("{rating_criteria}", RATING_CRITERIA)


def _build_task_messages(task_key, payload):
    """Build cache-stable task messages with long-input instructions last."""
    profile = get_prompt_profile(task_key)
    system = {"role": "system", "content": profile.get("system", "")}
    instruction = {"role": "user", "content": _render_instruction(profile.get("instruction", ""))}
    data = {
        "role": "user",
        "content": "动态输入数据（JSON，固定字段顺序）：\n"
        + json.dumps(payload, ensure_ascii=False, indent=2),
    }
    if task_key in {"deep_reading", "paper_import"}:
        return [system, data, instruction]
    return [system, instruction, data]
