"""Consistent SQLite snapshot support."""

import os
import sqlite3
from contextlib import closing


def copy_sqlite_snapshot(source_path, snapshot_path):
    """Copy a committed, consistent SQLite database using the online backup API."""
    if not os.path.exists(source_path):
        raise FileNotFoundError(f"数据库文件不存在: {source_path}")
    snapshot_dir = os.path.dirname(snapshot_path)
    if snapshot_dir:
        os.makedirs(snapshot_dir, exist_ok=True)

    try:
        with closing(
            sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
        ) as source:
            with closing(sqlite3.connect(snapshot_path)) as destination:
                source.backup(destination)
    except Exception:
        if os.path.exists(snapshot_path):
            os.remove(snapshot_path)
        raise
    return snapshot_path
