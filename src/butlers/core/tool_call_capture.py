"""Runtime MCP tool-call capture utilities.

Tracks executed MCP tool calls keyed by runtime session id so higher-level
handling can reconcile parser-extracted tool calls with ground-truth tool
execution observed inside the daemon.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import threading
from collections import defaultdict
from typing import Any

_runtime_session_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_runtime_session_id_var", default=None
)
_runtime_trigger_source_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_runtime_trigger_source_var", default=None
)
_runtime_butler_name_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_runtime_butler_name_var", default=None
)
# Ambient handle to the current butler daemon's live switchboard MCP client,
# scoped to the duration of one deterministic scheduled-job dispatch (bu-tdd4k.3).
# Deterministic job handlers are invoked with a fixed ``(pool, job_args)``
# signature shared across every butler's job registry (see
# ``butlers.background.dispatch_scheduled_task``), so a job that needs to
# deliver a notification through the notify boundary (rather than bypassing it
# with a raw side-channel call) reads this contextvar instead of widening that
# signature for every registered job.
_runtime_switchboard_client_var: contextvars.ContextVar[Any | None] = contextvars.ContextVar(
    "_runtime_switchboard_client_var", default=None
)
# Ambient handle to the current butler daemon's ApprovalPushRuntime, scoped to
# the duration of one MCP tool call (bound by ``_McpRuntimeSessionGuard`` in
# guards.py) or one deterministic scheduled-job dispatch (bound by
# ``background.dispatch_scheduled_task``). Module code that parks a PENDING
# action (e.g. a domain butler's curation jobs) reads this instead of
# widening every job/tool signature to thread the runtime through by hand
# (bu-g27ib, mirroring the switchboard-client contextvar above).
_runtime_approval_push_runtime_var: contextvars.ContextVar[Any | None] = contextvars.ContextVar(
    "_runtime_approval_push_runtime_var", default=None
)
# Explicit global Codex authority, scoped to deterministic scheduled work.
# Job handlers retain their stable ``(pool, job_args)`` signature, while a
# scheduled discretion path can still inject the daemon-selected authority
# instead of mistaking its schema-local job pool for system-global state.
_runtime_codex_auth_authority_var: contextvars.ContextVar[Any | None] = contextvars.ContextVar(
    "_runtime_codex_auth_authority_var", default=None
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


def set_current_runtime_trigger_source(
    trigger_source: str | None,
) -> contextvars.Token[str | None]:
    """Set trigger_source for the current MCP request context."""
    return _runtime_trigger_source_var.set(trigger_source)


def reset_current_runtime_trigger_source(token: contextvars.Token[str | None]) -> None:
    """Restore trigger_source for the current MCP request context."""
    _runtime_trigger_source_var.reset(token)


def get_current_runtime_trigger_source() -> str | None:
    """Return trigger_source bound to the current MCP request context."""
    return _runtime_trigger_source_var.get()


def set_current_runtime_butler_name(
    butler_name: str | None,
) -> contextvars.Token[str | None]:
    """Set current executing butler name for the request/task context."""
    return _runtime_butler_name_var.set(butler_name)


def reset_current_runtime_butler_name(token: contextvars.Token[str | None]) -> None:
    """Restore current executing butler name for the request/task context."""
    _runtime_butler_name_var.reset(token)


def get_current_runtime_butler_name() -> str | None:
    """Return current executing butler name bound to the request/task context."""
    return _runtime_butler_name_var.get()


def set_current_switchboard_client(client: Any | None) -> contextvars.Token[Any | None]:
    """Set the live switchboard MCP client for the current task context."""
    return _runtime_switchboard_client_var.set(client)


def reset_current_switchboard_client(token: contextvars.Token[Any | None]) -> None:
    """Restore the switchboard MCP client binding for the current task context."""
    _runtime_switchboard_client_var.reset(token)


def get_current_switchboard_client() -> Any | None:
    """Return the switchboard MCP client bound to the current task context, if any."""
    return _runtime_switchboard_client_var.get()


def set_current_approval_push_runtime(runtime: Any | None) -> contextvars.Token[Any | None]:
    """Set the ambient ApprovalPushRuntime for the current task context."""
    return _runtime_approval_push_runtime_var.set(runtime)


def reset_current_approval_push_runtime(token: contextvars.Token[Any | None]) -> None:
    """Restore the ApprovalPushRuntime binding for the current task context."""
    _runtime_approval_push_runtime_var.reset(token)


def get_current_approval_push_runtime() -> Any | None:
    """Return the ApprovalPushRuntime bound to the current task context, if any.

    ``None`` means "no runtime wired for this call site" -- the same fallback
    ``modules.approvals.park.park_pending_action`` already treats as
    "attempt no push" (e.g. a butler with no live switchboard connection).
    """
    return _runtime_approval_push_runtime_var.get()


def set_current_codex_auth_authority(authority: Any | None) -> contextvars.Token[Any | None]:
    """Bind the explicit system-global Codex authority to this task context."""
    return _runtime_codex_auth_authority_var.set(authority)


def reset_current_codex_auth_authority(token: contextvars.Token[Any | None]) -> None:
    """Restore the prior scheduled-work Codex authority binding."""
    _runtime_codex_auth_authority_var.reset(token)


def get_current_codex_auth_authority() -> Any | None:
    """Return the explicit Codex authority bound by scheduled dispatch, if any."""
    return _runtime_codex_auth_authority_var.get()


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


def fingerprint_tool_call_payload(value: Any) -> str:
    """Return a stable, non-reversible fingerprint for a tool input payload."""
    safe_value = _json_safe(value)
    encoded = json.dumps(
        safe_value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8", errors="replace")
    return hashlib.sha256(encoded).hexdigest()


def capture_tool_call(
    *,
    tool_name: str,
    module_name: str | None = None,
    input_payload: dict[str, Any] | None = None,
    input_fingerprint: str | None = None,
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
    if input_fingerprint:
        record["input_fingerprint"] = input_fingerprint
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


def peek_runtime_session_tool_calls(session_id: str) -> list[dict[str, Any]]:
    """Return captured executed tool calls for session id without clearing them.

    Used by mid-session readers (e.g. ``conversation_reply``) that need the
    calls captured so far without disturbing the buffer the Spawner still
    drains at session finish via ``consume_runtime_session_tool_calls``.
    """
    with _capture_lock:
        return list(_captured_tool_calls.get(session_id, []))


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
