"""In-process task progress registry."""

import threading
import time

from flask import g, has_request_context

progress_store = {}
progress_lock = threading.Lock()
TERMINAL_STATUSES = frozenset({"completed", "error"})
TERMINAL_PROGRESS_TTL_SECONDS = 10 * 60


def _terminal_progress_expired(progress, now):
    return (
        progress.get("status") in TERMINAL_STATUSES
        and now - progress.get("timestamp", 0) >= TERMINAL_PROGRESS_TTL_SECONDS
    )


def update_progress(task_id, data, user_id=None):
    if user_id is None and has_request_context():
        user = getattr(g, "current_user", None)
        user_id = user.get("id") if user else None
    with progress_lock:
        now = time.time()
        expired_keys = [
            key for key, progress in progress_store.items()
            if _terminal_progress_expired(progress, now)
        ]
        for expired_key in expired_keys:
            progress_store.pop(expired_key, None)

        key = (user_id, task_id)
        progress_store[key] = {**data, "user_id": user_id, "timestamp": now}
        return True


def get_progress(task_id, user_id=None):
    with progress_lock:
        key = (user_id, task_id)
        data = progress_store.get(key)
        if data and _terminal_progress_expired(data, time.time()):
            progress_store.pop(key, None)
            return None
        return data
