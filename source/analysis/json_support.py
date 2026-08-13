"""Repair and isolate JSON objects returned by chat models."""

import re


def _repair_invalid_json_escapes(content):
    """Remove only invalid escapes inside JSON strings, preserving valid escapes."""
    result = []
    in_string = False
    escaped = False
    i = 0
    valid_simple_escapes = set('"\\/bfnrt')
    while i < len(content):
        char = content[i]
        if not in_string:
            result.append(char)
            if char == '"':
                in_string = True
            i += 1
            continue
        if escaped:
            if char in valid_simple_escapes:
                result.append(char)
            elif char == "u":
                hex_part = content[i + 1:i + 5]
                if len(hex_part) == 4 and all(c in "0123456789abcdefABCDEF" for c in hex_part):
                    result.append(char)
                    result.append(hex_part)
                    i += 4
                else:
                    result.pop()
                    result.append(char)
            else:
                result.pop()
                result.append(char)
            escaped = False
            i += 1
            continue
        if char == "\\":
            result.append(char)
            escaped = True
            i += 1
            continue
        result.append(char)
        if char == '"':
            in_string = False
        i += 1
    if escaped and result and result[-1] == "\\":
        result.pop()
    return "".join(result)


_CONTROL_ESCAPES = {"\n": "\\n", "\r": "\\r", "\t": "\\t", "\f": "\\f", "\b": "\\b"}


def _repair_raw_control_chars(content):
    """把 JSON 字符串内部的原始控制字符转义为合法形式。

    部分模型会在 JSON 字符串值内输出真实换行/制表符，
    直接 json.loads 会报 Invalid control character。
    """
    result = []
    in_string = False
    escaped = False
    for char in content:
        if not in_string:
            result.append(char)
            if char == '"':
                in_string = True
            continue
        if escaped:
            result.append(char)
            escaped = False
            continue
        if char == "\\":
            result.append(char)
            escaped = True
            continue
        if char == '"':
            result.append(char)
            in_string = False
            continue
        if char in _CONTROL_ESCAPES:
            result.append(_CONTROL_ESCAPES[char])
            continue
        result.append(char)
    return "".join(result)


def _extract_first_json_object(content):
    """提取首个配平的大括号 JSON 对象（字符串感知、支持转义）。

    部分模型在续写时会输出两个完整 JSON 对象，
    直接整体解析会失败；生产与 benchmark 解析均可用此函数兜底。
    """
    content = str(content or "")
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(content):
        if not in_string:
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return content[:index + 1]
            elif char == '"':
                in_string = True
            continue
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            in_string = False
    return content


def _clean_json_content(content):
    """Strip Markdown fences, isolate a JSON object and repair invalid escapes."""
    if content.startswith("```"):
        lines = content.split("\n")
        content = "\n".join(lines[1:])
        if content.endswith("```"):
            content = content[:-3].strip()
    json_match = re.search(r"\{[\s\S]*\}", content)
    if json_match:
        content = json_match.group(0)
    return _repair_raw_control_chars(_repair_invalid_json_escapes(content))
