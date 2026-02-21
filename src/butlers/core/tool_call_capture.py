"""Runtime MCP tool-call capture utilities.

Tracks executed MCP tool calls keyed by runtime session id so higher-level
handling can reconcile parser-extracted tool calls with ground-truth tool
execution observed inside the daemon.
"""

from __future__ import annotations

import contextvars
import threading
from collections import defaultdict
from typing import Any

_runtime_session_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_runtime_session_id_var", default=None
)
_captured_tool_calls: dict[str, list[dict[str, Any]]] = defaultdict(list)
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


def capture_tool_call(
    *,
    tool_name: str,
    module_name: str | None = None,
    input_payload: dict[str, Any] | None = None,
) -> None:
    """Append an executed tool call for the current runtime session context."""
    session_id = get_current_runtime_session_id()
    if not session_id:
        return

    record: dict[str, Any] = {"name": tool_name}
    if module_name:
        record["module"] = module_name
    if isinstance(input_payload, dict) and input_payload:
        record["input"] = input_payload

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
