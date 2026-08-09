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
    return _repair_invalid_json_escapes(content)
