"""Dashboard audit emit helper.

Provides :func:`emit_dashboard_audit` for writing rows to
``switchboard.dashboard_audit_log`` from the API layer.  This is the HTTP
request-side counterpart of :func:`butlers.core.audit.write_audit_entry` (which
runs inside butler daemons).

Design notes
------------
- Silently swallows all errors so audit logging never breaks the primary
  operation.
- Body redaction strips fields in :data:`_REDACTED_FIELDS` before storing
  ``request_summary``.  The redaction list is intentionally conservative;
  extend it when new sensitive field names appear.
- Callers that want explicit ``operation`` strings (e.g. ``"contact_info_delete"``)
  should pass ``operation`` directly.  The middleware uses a generic
  ``"{METHOD} {path}"`` operation string for broad coverage.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Field names whose values must NEVER be stored in audit logs.
# Comparison is case-insensitive and applied to both request body keys and
# query-parameter names.
_REDACTED_FIELDS: frozenset[str] = frozenset(
    {
        "password",
        "secret",
        "token",
        "api_key",
        "apikey",
        "value",  # contact_info.value may contain credentials
        "access_token",
        "refresh_token",
        "private_key",
        "credential",
        "credentials",
        "authorization",
    }
)

_REDACT_SENTINEL = "[REDACTED]"


def redact_body(body: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy of *body* with sensitive field values replaced.

    Only top-level keys are redacted.  Nested structures (e.g. dicts inside
    arrays) are replaced wholesale with ``"[REDACTED]"`` when the *parent*
    key matches — nested keys are not individually inspected.

    This keeps the redaction logic simple and auditable.
    """
    result: dict[str, Any] = {}
    for key, val in body.items():
        if key.lower() in _REDACTED_FIELDS:
            result[key] = _REDACT_SENTINEL
        else:
            result[key] = val
    return result


async def emit_dashboard_audit(
    db_manager: Any,
    *,
    butler: str,
    operation: str,
    method: str,
    path: str,
    path_params: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    response_status: int | None = None,
    trace_id: str | None = None,
    result: str = "success",
    error: str | None = None,
) -> None:
    """Insert one row into ``switchboard.dashboard_audit_log``.

    Parameters
    ----------
    db_manager:
        :class:`~butlers.api.db.DatabaseManager` instance.  If ``None``, the
        call is a silent no-op (used during startup before DB is available).
    butler:
        Canonical butler name (e.g. ``"relationship"``).
    operation:
        Short stable operation label (e.g. ``"contact_info_delete"`` or
        ``"DELETE /api/relationship/contacts/{contact_id}/contact-info/{info_id}"``).
    method:
        HTTP method (``"DELETE"``, ``"PATCH"``, ``"POST"``, …).
    path:
        Request path (``"/api/relationship/contacts/…"``).
    path_params:
        Path parameter names → values (for replay-ability).
    body:
        Parsed request body dict (will be redacted before storage).
    response_status:
        HTTP response status code.
    trace_id:
        Optional trace / correlation ID.
    result:
        ``"success"`` or ``"error"``.
    error:
        Error message when *result* is ``"error"``.
    """
    if db_manager is None:
        return

    request_summary: dict[str, Any] = {
        "method": method,
        "path": path,
    }
    if path_params:
        request_summary["path_params"] = path_params
    if body:
        request_summary["body"] = redact_body(body)
    if response_status is not None:
        request_summary["response_status"] = response_status
    if trace_id:
        request_summary["trace_id"] = trace_id

    try:
        pool = db_manager.pool("switchboard")
        await pool.execute(
            "INSERT INTO dashboard_audit_log "
            "(butler, operation, request_summary, result, error, user_context) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            butler,
            operation,
            json.dumps(request_summary),
            result,
            error,
            json.dumps({}),
        )
    except Exception:
        logger.warning(
            "Failed to emit dashboard audit entry: butler=%s operation=%s method=%s path=%s",
            butler,
            operation,
            method,
            path,
            exc_info=True,
        )
