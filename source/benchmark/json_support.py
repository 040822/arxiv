"""JSON envelope helpers used only by the benchmark author and judges."""

import json
import re


def _clean_json_content(content):
    """Strip Markdown fences and tolerate common escaped JSON wrappers."""
    text = str(content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _extract_first_json_object(content):
    """Extract the first balanced JSON object from model output."""
    text = _clean_json_content(content)
    start = text.find("{")
    if start < 0:
        raise ValueError("模型输出未包含 JSON 对象")
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    raise ValueError("模型输出 JSON 对象不完整")


def loads_first_json(content):
    """Decode the first JSON object, including a second escaped envelope."""
    raw = _extract_first_json_object(content)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return json.loads(raw.replace("\\n", "\n").replace('\\"', '"'))
