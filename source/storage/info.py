"""Read-only information about SQLite database files."""

import os

from source.config import DB_PATH


def format_bytes(size_bytes):
    """Format a byte count for display."""
    size = int(size_bytes or 0)
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    if size < 1024 * 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} MB"
    return f"{size / 1024 / 1024 / 1024:.1f} GB"


def get_database_file_sizes(db_path=DB_PATH):
    """Return the disk usage of the SQLite main, WAL and SHM files."""
    files = []
    total = 0
    for path in (db_path, f"{db_path}-wal", f"{db_path}-shm"):
        if not os.path.exists(path):
            continue
        size = os.path.getsize(path)
        total += size
        files.append({
            "path": path,
            "name": os.path.basename(path),
            "size_bytes": size,
            "size": format_bytes(size),
        })
    return {"total_bytes": total, "total": format_bytes(total), "files": files}
