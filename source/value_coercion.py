"""Shared primitive value coercion."""


def as_bool(value, default=False):
    """Return value as bool using the project's JSON/form conventions."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def as_float(value, default=0.0):
    """Return value as float, falling back for empty or invalid input."""
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def as_int(value, default=0):
    """Return value as int, falling back for empty or invalid input."""
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default
