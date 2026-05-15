"""Dashboard mutation audit middleware.

Records every non-GET dashboard API request to ``switchboard.dashboard_audit_log``
so that all write operations have an audit trail, even when individual route
handlers do not emit explicit audit entries.

The middleware is intentionally broad and cheap — it fires for every mutating
HTTP method (POST, PUT, PATCH, DELETE) on any ``/api/`` path.  Sensitive
operations (contact_info mutations, credential reveals, runtime-config patches)
may additionally emit explicit audit rows with richer ``operation`` labels via
:func:`~butlers.api.audit_emit.emit_dashboard_audit` directly from the route
handler.  The two layers compose: the middleware provides a safety net; explicit
emits provide human-readable operation labels for the most sensitive paths.

Cache invalidation (bu-qzjpm):
When an API mutation produces a row with ``result='error'`` in
``dashboard_audit_log``, the middleware invalidates the briefing cache so that
the next GET /api/dashboard/briefing reflects the new error row immediately
rather than waiting up to 5 minutes for TTL expiry.  The middleware uses
:meth:`~butlers.api.briefing.cache.BriefingCache.invalidate_all` because it has
no per-request access to the owner's contact id.

Design constraints
------------------
- Never raise: all errors inside ``dispatch`` are caught and logged.
- Reads the request body for audit summary but does not consume it for the
  downstream handler (Starlette rebuilds the receive callable from bytes).
- Body size cap: bodies larger than :data:`_MAX_BODY_BYTES` are not stored in
  full — a ``body_truncated=true`` flag is set instead.
- The butler name is inferred from the path prefix
  (``/api/{butler}/…`` → butler = ``{butler}``).  Paths that don't match that
  pattern fall back to ``"dashboard"``.
- ``X-Trace-Id`` response header is set to the same ``trace_id`` that is stored
  in the audit row, allowing clients to correlate a request failure with the
  corresponding audit log entry.  The header is written BEFORE the audit emit so
  it is present even when the DB write fails.
"""

from __future__ import annotations

import json
import logging
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from butlers.api.audit_emit import emit_dashboard_audit, redact_body
from butlers.api.briefing.cache import get_cache
from butlers.api.deps import get_db_manager

logger = logging.getLogger(__name__)

# HTTP methods that trigger an audit write.
_MUTATING_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Maximum request body bytes to include in the audit row.
_MAX_BODY_BYTES: int = 16_384  # 16 KiB — generous but bounded

# Path prefixes that are NOT interesting to audit (health probes, static files).
_SKIP_PREFIXES: tuple[str, ...] = ("/health", "/api/health")


def _infer_butler(path: str) -> str:
    """Derive a butler name from the request path.

    ``/api/relationship/…`` → ``"relationship"``
    ``/api/audit-log/…``    → ``"dashboard"``
    ``/api/health``         → ``"dashboard"``
    Any other pattern       → ``"dashboard"``
    """
    # Strip leading slash and split
    parts = path.lstrip("/").split("/")
    # parts[0] == "api", parts[1] == butler name (when present)
    if len(parts) >= 2 and parts[0] == "api" and parts[1]:
        # Normalise slug to avoid noise (e.g. "audit-log" → "dashboard")
        candidate = parts[1]
        # Known "infrastructure" prefixes that don't map to a single butler
        _INFRA = {"audit-log", "health", "oauth", "cli-auth", "sse", "search", "catalog"}
        if candidate not in _INFRA:
            return candidate
    return "dashboard"


def _collect_path_params(path: str) -> dict[str, str]:
    """Extract UUID-shaped path segments as path params (best-effort).

    Rather than parsing the route template (which would require coupling to
    FastAPI internals), we collect any path segments that look like UUIDs and
    label them ``param_0``, ``param_1``, etc.  This is sufficient for
    replay-ability (e.g. reconstructing which contact_info row was deleted).
    """
    params: dict[str, str] = {}
    idx = 0
    for segment in path.split("/"):
        if len(segment) == 36 and segment.count("-") == 4:
            try:
                uuid.UUID(segment)
                params[f"param_{idx}"] = segment
                idx += 1
            except ValueError:
                pass
    return params


class DashboardAuditMiddleware(BaseHTTPMiddleware):
    """ASGI middleware: record every non-GET ``/api/`` request to the audit log.

    Instantiate via :func:`~butlers.api.app.create_app` after adding other
    middleware.  The middleware reads ``db_manager`` lazily from the
    application-level dependency on each request so it works correctly before
    and after startup (when the pool is not yet available the audit write is a
    silent no-op).
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        # Only audit mutating methods
        if request.method not in _MUTATING_METHODS:
            return await call_next(request)

        path = request.url.path

        # Skip health probes and non-API paths
        if not path.startswith("/api/") or any(path.startswith(p) for p in _SKIP_PREFIXES):
            return await call_next(request)

        # Collect the request body (bounded) without consuming it for downstream.
        raw_body: bytes = b""
        body_truncated = False
        try:
            raw_body = await request.body()
            if len(raw_body) > _MAX_BODY_BYTES:
                raw_body = raw_body[:_MAX_BODY_BYTES]
                body_truncated = True
        except Exception:
            logger.debug("DashboardAuditMiddleware: could not read request body", exc_info=True)

        # Parse body as JSON for redaction; fall back to raw string on parse error.
        # Both dict and list bodies are handled; list items are recursively redacted.
        parsed_body: dict | None = None
        if raw_body:
            try:
                parsed = json.loads(raw_body)
                if isinstance(parsed, dict):
                    parsed_body = redact_body(parsed)
                    if body_truncated:
                        parsed_body["__body_truncated__"] = True
                elif isinstance(parsed, list):
                    redacted_list = [
                        redact_body(item) if isinstance(item, dict) else item for item in parsed
                    ]
                    parsed_body = {"__list__": redacted_list}
                    if body_truncated:
                        parsed_body["__body_truncated__"] = True
                else:
                    parsed_body = {"__value__": parsed}
                    if body_truncated:
                        parsed_body["__body_truncated__"] = True
            except json.JSONDecodeError:
                parsed_body = {"__raw__": raw_body.decode(errors="replace")}
                if body_truncated:
                    parsed_body["__body_truncated__"] = True

        # Trace ID: re-use one from scope if available, otherwise generate.
        trace_id: str | None = request.scope.get("trace_id") or str(uuid.uuid4())

        # Call downstream handler and capture status code.
        response: Response = await call_next(request)
        status_code = response.status_code
        result = "success" if status_code < 400 else "error"

        # Expose the trace_id on the response so clients can correlate failures
        # with audit log entries.  Set this BEFORE the audit write so the header
        # is present even when the audit emit fails.
        response.headers["X-Trace-Id"] = trace_id

        # Infer butler and collect path params for replay-ability.
        butler = _infer_butler(path)
        path_params = _collect_path_params(path)

        # Build a human-readable operation string.
        operation = f"{request.method} {path}"

        # Emit — fire and forget (emit_dashboard_audit swallows all errors).
        try:
            db_manager = get_db_manager()
        except RuntimeError:
            # DB not initialized yet (startup race or test environment)
            return response

        await emit_dashboard_audit(
            db_manager,
            butler=butler,
            operation=operation,
            method=request.method,
            path=path,
            path_params=path_params or None,
            body=parsed_body,
            response_status=status_code,
            trace_id=trace_id,
            result=result,
            error=f"HTTP {status_code}" if result == "error" else None,
        )

        # Invalidate the briefing cache when the audit row has result='error'
        # (category b from bu-qzjpm).  Errors written to dashboard_audit_log
        # are one of the three sources the briefing reads to compute attention
        # items; invalidating immediately prevents the cache from serving a
        # stale 'quiet' briefing to an owner who just saw a failing operation.
        if result == "error":
            get_cache().invalidate_all()

        return response
