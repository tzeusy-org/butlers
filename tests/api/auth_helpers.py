"""Explicit authenticated transport setup for domain-handler unit tests.

This is not authentication evidence. Dedicated owner-auth, CSRF, callback and
actor-denial tests import the production factory and exercise their own state.
The real owner middleware still executes; only its persistence service and the
client's known fixture header are supplied here. No production bypass exists.
"""

from __future__ import annotations

import hmac
from types import SimpleNamespace

from fastapi import FastAPI
from starlette.types import ASGIApp, Receive, Scope, Send

from butlers.api.app import create_app as create_production_app

_DOMAIN_KEY = "synthetic-domain-test-owner-key"


class _DomainOwnerState:
    def __init__(self, key: str) -> None:
        self._key = key

    async def authorize(
        self,
        *,
        session_token: str | None = None,
        api_key: str | None = None,
        csrf_token: str | None = None,
        unsafe: bool = False,
    ) -> SimpleNamespace:
        if not api_key or not hmac.compare_digest(api_key, self._key):
            from butlers.api.owner_auth.service import AuthError

            raise AuthError(401, "UNAUTHORIZED", "Synthetic owner header is required")
        return SimpleNamespace(method="header", expires_at=None)

    async def status(self, session_token: str | None = None) -> dict:
        return {
            "state": "configured_key",
            "authenticated": False,
            "session_expires_at": None,
        }


class _DomainClientHeader:
    """Supply the known test client's credential without changing auth logic."""

    def __init__(self, app: ASGIApp, key: str) -> None:
        self.app = app
        self.header = (b"x-api-key", key.encode())

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in {"http", "websocket"}:
            headers = scope.get("headers", [])
            if not any(name.lower() == b"x-api-key" for name, _ in headers):
                scope = {**scope, "headers": [*headers, self.header]}
        await self.app(scope, receive, send)


def create_authenticated_domain_app(**kwargs) -> FastAPI:
    """Build a real app with a synthetic authenticated domain-test client.

    Existing explicitly supplied headers are retained, including invalid ones.
    Callers testing missing credentials must use the production factory instead.
    This helper intentionally does not supply session, ceremony or CSRF methods.
    """
    key = kwargs.pop("api_key", None) or _DOMAIN_KEY
    app = create_production_app(api_key=key, **kwargs)
    app.state.owner_auth_service = _DomainOwnerState(key)
    app.add_middleware(_DomainClientHeader, key=key)
    return app
