"""API error handling and authentication middleware.

Registers FastAPI exception handlers that convert domain exceptions into
standardised ``{"error": {"code": "...", "message": "...", "butler": "..."}}``
JSON responses.

Status code mapping:
- ``ButlerUnreachableError`` → 502 Bad Gateway
- ``ButlerNotFoundError`` (unknown butler lookup) → 404 Not Found
- ``ValueError`` → 400 Bad Request
- ``AuditTableNotAvailableError`` → 503 Service Unavailable, body ``{"error": "audit_unavailable"}``
  (dashboard-audit-log spec: mutation endpoints must propagate this rather than
  swallow it, so the state-change transaction they ran inside rolls back)
- Any other ``Exception`` → 500 Internal Server Error

Note: Only ``ButlerNotFoundError`` (a named subclass of ``KeyError``) is mapped
to 404.  Raw ``KeyError`` exceptions — e.g. from dict-access bugs in endpoint
handlers — are **not** caught here and will propagate to ``CatchAllErrorMiddleware``
as 500 responses with a stack trace in the server logs.  This prevents endpoint
bugs from silently masquerading as butler-routing errors.

Owner authentication is enforced by the outer ASGI boundary in
``owner_auth.http`` before domain handling. The former optional key-only
middleware was retired at the adopted owner-authentication cutover.
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from butlers.api.deps import ButlerNotFoundError, ButlerUnreachableError
from butlers.api.models import ErrorDetail, ErrorResponse
from butlers.api.routers.audit import AuditTableNotAvailableError

logger = logging.getLogger(__name__)


def _is_approval_callback_route(request: Request) -> bool:
    """Return whether the request is one of the connector's three callback routes.

    The connector credential is deliberately narrower than the dashboard API
    key: it can read one approval detail or transition it through the established
    approve/deny routes, and nothing else under ``/api``.
    """
    segments = request.url.path.split("/")
    if len(segments) not in {4, 5} or segments[:3] != ["", "api", "approvals"]:
        return False
    try:
        UUID(segments[3])
    except ValueError:
        return False
    if request.method == "GET":
        return len(segments) == 4
    return (
        request.method == "POST"
        and len(segments) == 5
        and segments[4]
        in {
            "approve",
            "deny",
        }
    )


async def _handle_butler_unreachable(
    request: Request,
    exc: ButlerUnreachableError,
) -> JSONResponse:
    """Return 502 when a butler MCP server cannot be reached."""
    logger.warning("Butler unreachable: %s", exc.butler_name, exc_info=exc)
    body = ErrorResponse(
        error=ErrorDetail(
            code="BUTLER_UNREACHABLE",
            message=str(exc),
            butler=exc.butler_name,
        )
    )
    return JSONResponse(status_code=502, content=body.model_dump())


async def _handle_butler_not_found(
    request: Request,
    exc: ButlerNotFoundError,
) -> JSONResponse:
    """Return 404 when a butler name is not found in the pool/registry."""
    butler_name = exc.butler_name
    logger.info("Butler not found: %s", butler_name)
    body = ErrorResponse(
        error=ErrorDetail(
            code="BUTLER_NOT_FOUND",
            message=f"Butler not found: {butler_name!r}",
            butler=butler_name,
        )
    )
    return JSONResponse(status_code=404, content=body.model_dump())


async def _handle_value_error(
    request: Request,
    exc: ValueError,
) -> JSONResponse:
    """Return 400 for validation / value errors."""
    logger.info("Validation error: %s", exc)
    body = ErrorResponse(
        error=ErrorDetail(
            code="VALIDATION_ERROR",
            message=str(exc),
        )
    )
    return JSONResponse(status_code=400, content=body.model_dump())


async def _handle_audit_table_unavailable(
    request: Request,
    exc: AuditTableNotAvailableError,
) -> JSONResponse:
    """Return 503 ``{"error": "audit_unavailable"}`` per the dashboard-audit-log spec.

    Raised by ``audit.append()`` when ``public.audit_log`` does not exist.
    Mutation endpoints must let this propagate (not catch-log-and-continue) so
    that it reaches here: the audit insert runs inside the same transaction as
    the state change it accompanies, so an uncaught exception here means that
    transaction already rolled back before this handler ever ran.
    """
    logger.warning("Audit table unavailable on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=503, content={"error": "audit_unavailable"})


class CatchAllErrorMiddleware(BaseHTTPMiddleware):
    """ASGI middleware that catches any unhandled exception and returns a 500.

    This sits above the Starlette exception handler layer, ensuring that
    even exceptions not caught by ``add_exception_handler`` are converted
    to the standard error envelope rather than bubbling up as raw 500s.
    """

    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
        except Exception:
            logger.error(
                "Unhandled exception on %s %s",
                request.method,
                request.url.path,
                exc_info=True,
            )
            body = ErrorResponse(
                error=ErrorDetail(
                    code="INTERNAL_ERROR",
                    message="Internal server error",
                )
            )
            return JSONResponse(status_code=500, content=body.model_dump())


def register_error_handlers(app: FastAPI) -> None:
    """Attach all exception handlers to the FastAPI application.

    Call this from ``create_app()`` after constructing the ``FastAPI`` instance.

    Domain-specific exceptions are registered via ``add_exception_handler``.
    The generic catch-all is an ASGI middleware that wraps the entire app
    to intercept any unhandled exception before Starlette's default
    ``ServerErrorMiddleware`` can convert it to a plain-text 500.

    ``OwnerAuthMiddleware`` is registered outside these handlers so auth
    failures terminate before general exception logging and body handling.
    """
    app.add_exception_handler(ButlerUnreachableError, _handle_butler_unreachable)  # type: ignore[arg-type]
    app.add_exception_handler(ButlerNotFoundError, _handle_butler_not_found)  # type: ignore[arg-type]
    app.add_exception_handler(ValueError, _handle_value_error)  # type: ignore[arg-type]
    app.add_exception_handler(
        AuditTableNotAvailableError,  # type: ignore[arg-type]
        _handle_audit_table_unavailable,  # type: ignore[arg-type]
    )
    app.add_middleware(CatchAllErrorMiddleware)
