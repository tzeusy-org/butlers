"""Runtime MCP tool-call capture utilities.

Tracks executed MCP tool calls keyed by runtime session id so higher-level
handling can reconcile parser-extracted tool calls with ground-truth tool
execution observed inside the daemon.
"""

from __future__ import annotations

import contextvars
import json
import threading
from collections import defaultdict
from typing import Any

_runtime_session_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_runtime_session_id_var", default=None
)
_captured_tool_calls: dict[str, list[dict[str, Any]]] = defaultdict(list)
_runtime_routing_context: dict[str, dict[str, Any]] = {}
_capture_lock = threading.Lock()


def set_current_runtime_session_id(session_id: str | None) -> contextvars.Token[str | None]:
    """Set runtime session id for the current request/task context."""
    return _runtime_session_id_var.set(session_id)


def reset_current_runtime_session_id(token: contextvars.Token[str | None]) -> None:
    """Restore runtime session id for the current request/task context."""
    _runtime_session_id_var.reset(token)


def get_current_runtime_session_id() -> str | None:
    """Return runtime session id bound to the current request/task context."""
    return _runtime_session_id_var.get()


def ensure_runtime_session_capture(session_id: str) -> None:
    """Ensure capture buffer exists for runtime session id."""
    with _capture_lock:
        _captured_tool_calls.setdefault(session_id, [])


def _json_safe(value: Any) -> Any:
    """Return a JSON-safe representation for persisted tool call payloads."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [_json_safe(item) for item in value]
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if hasattr(value, "model_dump"):
        try:
            return _json_safe(value.model_dump(mode="json"))  # type: ignore[attr-defined]
        except Exception:
            return str(value)
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def capture_tool_call(
    *,
    tool_name: str,
    module_name: str | None = None,
    input_payload: dict[str, Any] | None = None,
    outcome: str | None = None,
    result_payload: Any | None = None,
    error: str | None = None,
) -> None:
    """Append an executed tool call for the current runtime session context."""
    session_id = get_current_runtime_session_id()
    if not session_id:
        return

    record: dict[str, Any] = {"name": tool_name}
    if module_name:
        record["module"] = module_name
    if isinstance(input_payload, dict) and input_payload:
        record["input"] = _json_safe(input_payload)
    if outcome:
        record["outcome"] = outcome
    if result_payload is not None:
        record["result"] = _json_safe(result_payload)
    if error:
        record["error"] = error

    with _capture_lock:
        _captured_tool_calls[session_id].append(record)


def consume_runtime_session_tool_calls(session_id: str) -> list[dict[str, Any]]:
    """Return and clear captured executed tool calls for session id."""
    with _capture_lock:
        return list(_captured_tool_calls.pop(session_id, []))


def discard_runtime_session_tool_calls(session_id: str) -> None:
    """Drop captured executed tool calls for session id."""
    with _capture_lock:
        _captured_tool_calls.pop(session_id, None)


def set_runtime_session_routing_context(
    session_id: str,
    context: dict[str, Any] | None,
) -> None:
    """Set routing context payload for a runtime session id."""
    if not isinstance(context, dict) or not context:
        return
    with _capture_lock:
        _runtime_routing_context[session_id] = _json_safe(context)


def get_runtime_session_routing_context(session_id: str) -> dict[str, Any] | None:
    """Return routing context payload for runtime session id."""
    with _capture_lock:
        payload = _runtime_routing_context.get(session_id)
        if not isinstance(payload, dict):
            return None
        return dict(payload)


def get_current_runtime_session_routing_context() -> dict[str, Any] | None:
    """Return routing context payload for current request/task session id."""
    session_id = get_current_runtime_session_id()
    if not session_id:
        return None
    return get_runtime_session_routing_context(session_id)


def clear_runtime_session_routing_context(session_id: str) -> None:
    """Drop routing context payload for runtime session id."""
    with _capture_lock:
        _runtime_routing_context.pop(session_id, None)
