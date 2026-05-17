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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from starlette.requests import Request

logger = logging.getLogger(__name__)

# Single-user deployment: every authenticated dashboard request is the owner.
# We still record this explicitly (rather than leaving an empty dict) so the
# audit log row carries an attributable principal — multi-principal support
# would replace this constant with a real session/JWT lookup.
_OWNER_PRINCIPAL = "owner"
_DASHBOARD_SOURCE = "dashboard"

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
    """Return a deep copy of *body* with sensitive field values replaced.

    Redaction is applied recursively: nested dicts and lists-of-dicts are
    walked so that sensitive keys at any depth are replaced with
    ``"[REDACTED]"``.  Non-dict list elements (strings, ints, …) are left
    unchanged.

    Matching is case-insensitive against :data:`_REDACTED_FIELDS`.
    """
    result: dict[str, Any] = {}
    for key, val in body.items():
        if key.lower() in _REDACTED_FIELDS:
            result[key] = _REDACT_SENTINEL
        elif isinstance(val, dict):
            result[key] = redact_body(val)
        elif isinstance(val, list):
            result[key] = [redact_body(item) if isinstance(item, dict) else item for item in val]
        else:
            result[key] = val
    return result


def build_user_context(
    request: Request | None = None,
    *,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the ``user_context`` JSONB payload for an audit row.

    Butlers is a single-user deployment, so the principal is always the
    ``owner``.  Even so, this helper records *how* the request reached the
    dashboard (source, client IP, whether an API key was presented) so the
    audit log answers the operator's "who/where" question without forcing
    callers to reconstruct request state from scratch.

    Parameters
    ----------
    request:
        The Starlette/FastAPI request, if one is in scope.  When ``None``
        (e.g. emits from a background task), only the principal/source
        defaults are recorded.
    extra:
        Optional additional fields merged on top of the defaults.  Useful for
        explicit emits that already know a higher-level actor label
        (``"dashboard:rest-api"``) or want to add semantic provenance.

    Returns
    -------
    dict[str, Any]
        A small JSON-safe dict with at least ``principal`` and ``source``.
    """
    context: dict[str, Any] = {
        "principal": _OWNER_PRINCIPAL,
        "source": _DASHBOARD_SOURCE,
    }

    if request is not None:
        client = request.client
        if client is not None and client.host:
            context["client_ip"] = client.host

        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            # X-Forwarded-For may be a comma-separated chain; first entry is
            # the original client.
            context["forwarded_for"] = forwarded_for.split(",")[0].strip()

        user_agent = request.headers.get("user-agent")
        if user_agent:
            # Cap to keep audit rows compact; full UA is rarely useful.
            context["user_agent"] = user_agent[:256]

        # Whether the request presented an API key.  We do NOT record the key
        # itself (it would be a credential leak) — only its presence.
        context["api_key_authenticated"] = bool(request.headers.get("x-api-key"))

    if extra:
        context.update(extra)

    return context


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
    request: Request | None = None,
    user_context: dict[str, Any] | None = None,
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
    request:
        The originating Starlette/FastAPI request.  When supplied, the request
        is used to derive ``user_context`` (client IP, user agent, etc.) via
        :func:`build_user_context`.  Ignored when ``user_context`` is provided
        explicitly.
    user_context:
        Explicit ``user_context`` payload.  When ``None`` (default), the
        helper derives one from *request* if provided, otherwise records the
        owner-only defaults.  Callers should pass this when they already have
        a richer actor identity (e.g. a background dispatcher with its own
        principal label).
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

    if user_context is None:
        user_context = build_user_context(request)

    # Pre-coerce non-JSON-safe values (e.g. UUIDs in path_params) to strings,
    # then hand the codec a plain dict — wrapping with json.dumps() here would
    # double-encode and store a JSONB string scalar instead of an object.
    safe_summary = json.loads(json.dumps(request_summary, default=str))
    safe_context = json.loads(json.dumps(user_context, default=str))

    try:
        pool = db_manager.pool("switchboard")
        await pool.execute(
            "INSERT INTO dashboard_audit_log "
            "(butler, operation, request_summary, result, error, user_context) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            butler,
            operation,
            safe_summary,
            result,
            error,
            safe_context,
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
