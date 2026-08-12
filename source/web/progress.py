"""In-process task progress registry."""

import threading
import time

from flask import g, has_request_context

progress_store = {}
progress_lock = threading.Lock()


def update_progress(task_id, data, user_id=None):
    if user_id is None and has_request_context():
        user = getattr(g, "current_user", None)
        user_id = user.get("id") if user else None
    with progress_lock:
        existing = progress_store.get(task_id)
        if existing and existing.get("user_id") != user_id:
            return False
        progress_store[task_id] = {**data, "user_id": user_id, "timestamp": time.time()}
        return True


def get_progress(task_id, user_id=None):
    with progress_lock:
        data = progress_store.get(task_id)
        if not data or data.get("user_id") == user_id:
            return data
        return None
