"""Primitive runtime-settings value coercion helpers."""


def _as_bool(value, default=False):
    """将前端/JSON 中的布尔值安全转换为 bool。"""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _as_float(value, default):
    """将数值配置安全转换为 float。"""
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value, default):
    """将数值配置安全转换为 int。"""
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default
