"""Consistent SQLite snapshot support."""

import os
import sqlite3


def copy_sqlite_snapshot(source_path, snapshot_path):
    """Copy a committed, consistent SQLite database using the online backup API."""
    if not os.path.exists(source_path):
        raise FileNotFoundError(f"数据库文件不存在: {source_path}")
    snapshot_dir = os.path.dirname(snapshot_path)
    if snapshot_dir:
        os.makedirs(snapshot_dir, exist_ok=True)

    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    destination = sqlite3.connect(snapshot_path)
    try:
        source.backup(destination)
    except Exception:
        destination.close()
        source.close()
        if os.path.exists(snapshot_path):
            os.remove(snapshot_path)
        raise
    else:
        destination.close()
        source.close()
    return snapshot_path
