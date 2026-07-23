"""Backward-compatible SQLite storage interface."""

from source.reports import generate_report_content
from source.storage import *
from source.storage import __all__ as _storage_all
from source.storage.connection import DB_DIR, DB_PATH

__all__ = [*_storage_all, "generate_report_content", "DB_DIR", "DB_PATH"]
