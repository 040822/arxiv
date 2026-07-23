"""Mapping helpers for paper rows returned by SQLite and adjacent adapters."""

import json
import logging

logger = logging.getLogger(__name__)


PAPER_JSON_LIST_FIELDS = ("authors", "categories", "tags")


def parse_paper_row(row):
    """Return a paper row dict with JSON list fields converted to Python lists."""
    result = dict(row)
    identifier = result.get("arxiv_id") or result.get("id") or "unknown"
    for field in PAPER_JSON_LIST_FIELDS:
        if field not in result:
            continue
        value = result[field]
        if isinstance(value, list):
            continue
        if value is None or value == "":
            result[field] = []
            continue
        try:
            parsed = json.loads(value) if isinstance(value, str) else value
        except (TypeError, json.JSONDecodeError):
            parsed = None
        if isinstance(parsed, list):
            result[field] = parsed
            continue
        logger.warning(
            "Invalid paper JSON list field %s for paper %s; using an empty list",
            field,
            identifier,
        )
        result[field] = []
    return result
