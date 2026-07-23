"""In-process task progress registry."""

import threading
import time

progress_store = {}
progress_lock = threading.Lock()


def update_progress(task_id, data):
    with progress_lock:
        progress_store[task_id] = {**data, "timestamp": time.time()}


def get_progress(task_id):
    with progress_lock:
        return progress_store.get(task_id)
