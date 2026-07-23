"""Shared response and task-log handling for manual task endpoints."""

from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable, Mapping

from flask import jsonify

from source.storage import finish_task_log, start_task_log


@dataclass(frozen=True)
class TaskEndpointResult:
    """Describe an HTTP response and the matching terminal task-log state."""

    payload: Mapping[str, Any]
    log_status: str
    log_message: str
    log_detail: str = ""
    http_status: int = 200

    @classmethod
    def ok(
        cls,
        message: str,
        *,
        payload: Mapping[str, Any] | None = None,
        detail: str = "",
    ) -> "TaskEndpointResult":
        response_payload = {"status": "ok", **dict(payload or {}), "message": message}
        return cls(
            payload=response_payload,
            log_status="success",
            log_message=message,
            log_detail=detail,
        )

    @classmethod
    def error(
        cls,
        message: str,
        *,
        http_status: int,
        log_message: str | None = None,
        detail: str = "",
    ) -> "TaskEndpointResult":
        return cls(
            payload={"status": "error", "message": message},
            log_status="error",
            log_message=log_message or message,
            log_detail=detail,
            http_status=http_status,
        )


def _endpoint_dependency(
    endpoint: Callable[..., Any],
    name: str,
    default: Callable[..., Any],
) -> Callable[..., Any]:
    """Keep module-level patch seams used by legacy endpoint callers."""

    dependency = endpoint.__globals__.get(name)
    return dependency if callable(dependency) else default


def task_endpoint(task_name: str, start_message: str):
    """Wrap a manual task endpoint with one balanced task-log lifecycle."""

    def decorate(endpoint):
        @wraps(endpoint)
        def wrapped(*args, **kwargs):
            start_log = _endpoint_dependency(endpoint, "start_task_log", start_task_log)
            finish_log = _endpoint_dependency(endpoint, "finish_task_log", finish_task_log)
            response_json = _endpoint_dependency(endpoint, "jsonify", jsonify)
            log_id = start_log(task_name, start_message)
            try:
                result = endpoint(*args, **kwargs)
                if not isinstance(result, TaskEndpointResult):
                    raise TypeError(
                        f"{endpoint.__name__} must return TaskEndpointResult"
                    )
                finish_log(
                    log_id,
                    result.log_status,
                    result.log_message,
                    result.log_detail,
                )
                response = response_json(dict(result.payload))
                if result.http_status == 200:
                    return response
                return response, result.http_status
            except Exception as exc:
                finish_log(log_id, "error", str(exc))
                return response_json({"status": "error", "message": str(exc)}), 500

        return wrapped

    return decorate
