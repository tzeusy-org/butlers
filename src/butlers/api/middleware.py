"""API error handling and authentication middleware.

Registers FastAPI exception handlers that convert domain exceptions into
standardised ``{"error": {"code": "...", "message": "...", "butler": "..."}}``
JSON responses.

Status code mapping:
- ``ButlerUnreachableError`` → 502 Bad Gateway
- ``KeyError`` (unknown butler lookup) → 404 Not Found
- ``ValueError`` → 400 Bad Request
- Any other ``Exception`` → 500 Internal Server Error

Also provides ``ApiKeyMiddleware`` for optional API-key authentication on all
``/api/*`` routes (excluding health endpoints).  Authentication is enabled only
when the ``DASHBOARD_API_KEY`` environment variable is set.  When the variable
is absent, the middleware is a no-op so that existing deployments that rely
solely on network-level access control remain unaffected.
"""

from __future__ import annotations

import hmac
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from butlers.api.deps import ButlerUnreachableError
from butlers.api.models import ErrorDetail, ErrorResponse

logger = logging.getLogger(__name__)

# Paths that are always public regardless of API-key configuration.
# These are used by liveness/readiness probes and must never require auth.
_PUBLIC_PATHS: frozenset[str] = frozenset({"/api/health", "/health"})


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


async def _handle_key_error(
    request: Request,
    exc: KeyError,
) -> JSONResponse:
    """Return 404 when a butler name is not found in the pool/registry."""
    butler_name = exc.args[0] if exc.args else None
    logger.info("Butler not found: %s", butler_name)
    body = ErrorResponse(
        error=ErrorDetail(
            code="BUTLER_NOT_FOUND",
            message=f"Butler not found: {butler_name}",
            butler=str(butler_name) if butler_name is not None else None,
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


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Optional API-key authentication middleware for all ``/api/*`` routes.

    Behaviour
    ---------
    - When ``DASHBOARD_API_KEY`` is **not set**, the middleware passes every
      request through without any check (opt-in, backward-compatible).
    - When ``DASHBOARD_API_KEY`` **is set**, every request to a path that:
        * starts with ``/api/``, *and*
        * is not in ``_PUBLIC_PATHS`` (``/api/health``, ``/health``)
      must supply a matching ``X-API-Key`` header.  A missing or incorrect
      header yields a 401 JSON response in the standard error envelope.

    Token comparison uses ``hmac.compare_digest`` to resist timing attacks.

    Usage
    -----
    Register via ``create_app()`` in ``app.py``.  The middleware reads the
    env var once at instantiation, so a restart is required to rotate the key.

    The env var name ``DASHBOARD_API_KEY`` is intentionally generic — it
    covers all butlers running behind this dashboard without requiring per-butler
    key management.
    """

    def __init__(self, app, api_key: str | None = None) -> None:
        super().__init__(app)
        # ``api_key`` is the already-resolved key (non-empty string → enabled,
        # ``None`` → disabled).  ``create_app()`` is responsible for reading
        # ``DASHBOARD_API_KEY`` from the environment and passing the resolved
        # value here.  Direct instantiation without ``create_app()`` will have
        # auth disabled unless a key is passed explicitly.
        self._api_key: str | None = api_key or None
        if self._api_key:
            logger.info("ApiKeyMiddleware: DASHBOARD_API_KEY is configured; auth is ENABLED")
        else:
            logger.debug("ApiKeyMiddleware: DASHBOARD_API_KEY not set; auth is DISABLED")

    async def dispatch(self, request: Request, call_next):
        # Auth disabled — pass through unconditionally.
        if not self._api_key:
            return await call_next(request)

        path = request.url.path

        # Public paths are always allowed (health/readiness probes).
        if path in _PUBLIC_PATHS:
            return await call_next(request)

        # Only enforce auth on /api/* routes.
        if not path.startswith("/api/"):
            return await call_next(request)

        # Check the header.
        provided_key = request.headers.get("X-API-Key", "")
        if not provided_key or not hmac.compare_digest(provided_key, self._api_key):
            logger.warning(
                "ApiKeyMiddleware: rejected request to %s (missing or invalid X-API-Key)",
                path,
            )
            body = ErrorResponse(
                error=ErrorDetail(
                    code="UNAUTHORIZED",
                    message="Missing or invalid API key. Provide a valid X-API-Key header.",
                )
            )
            return JSONResponse(status_code=401, content=body.model_dump())

        return await call_next(request)


def register_error_handlers(app: FastAPI) -> None:
    """Attach all exception handlers to the FastAPI application.

    Call this from ``create_app()`` after constructing the ``FastAPI`` instance.

    Domain-specific exceptions are registered via ``add_exception_handler``.
    The generic catch-all is an ASGI middleware that wraps the entire app
    to intercept any unhandled exception before Starlette's default
    ``ServerErrorMiddleware`` can convert it to a plain-text 500.

    Note: ``ApiKeyMiddleware`` is registered separately by ``create_app()``
    because it needs to wrap the entire ASGI stack (including static files)
    and its configuration is injected at app-creation time.
    """
    app.add_exception_handler(ButlerUnreachableError, _handle_butler_unreachable)  # type: ignore[arg-type]
    app.add_exception_handler(KeyError, _handle_key_error)  # type: ignore[arg-type]
    app.add_exception_handler(ValueError, _handle_value_error)  # type: ignore[arg-type]
    app.add_middleware(CatchAllErrorMiddleware)
