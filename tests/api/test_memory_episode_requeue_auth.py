"""The requeue route sits behind the central owner-auth boundary (bu-kqnum.14.3).

Runs the real ``OwnerAuthMiddleware`` and the real memory router with a fake auth service
shaped like ``tests/api/test_api_key_middleware.py::app_with_boundary``. The route adds no
per-route owner check, so every rejection below is the boundary's, and none may reach a
memory pool.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.deps import get_db
from butlers.api.owner_auth.config import OwnerAuthConfig
from butlers.api.owner_auth.http import OwnerAuthMiddleware
from butlers.api.owner_auth.service import AuthError
from butlers.api.routers.memory import router

pytestmark = pytest.mark.unit

ORIGIN = "https://butlers.example.test"
CONFIG = OwnerAuthConfig(ORIGIN, "butlers.example.test", "synthetic-key", "test", ("127.0.0.1",))
COOKIE_NAME = "__Host-butlers-test-owner"
PATH = "/api/memory/episodes/00000000-0000-0000-0000-000000000001/requeue"


class _ForbiddenDB:
    """Any attempt to reach a memory pool fails the test."""

    butler_names = ["atlas"]
    touched = False

    def pool(self, name: str):
        type(self).touched = True
        raise AssertionError("a rejected request reached a memory pool")

    def schema_for_butler(self, name: str):
        type(self).touched = True
        raise AssertionError("a rejected request reached the db manager")


def _build(*, unavailable: bool = False):
    app = FastAPI()
    calls: list[tuple] = []

    async def authorize(session_token=None, api_key=None, csrf_token=None, unsafe=False):
        calls.append((session_token, api_key, csrf_token, unsafe))
        if unavailable:
            raise AuthError("AUTH_UNAVAILABLE")
        if api_key is not None:
            if api_key != "synthetic-key":
                raise AuthError("UNAUTHORIZED")
        elif session_token != "session-sentinel":
            raise AuthError("UNAUTHORIZED")
        elif unsafe and csrf_token != "csrf-sentinel":
            raise AuthError("FORBIDDEN")
        return SimpleNamespace(method="cookie", expires_at=None)

    app.state.owner_auth_service = SimpleNamespace(authorize=AsyncMock(side_effect=authorize))
    app.add_middleware(OwnerAuthMiddleware, config=CONFIG)
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: _ForbiddenDB()
    return app, calls


@pytest.mark.parametrize(
    ("headers", "unavailable", "status", "code", "authorize_called"),
    [
        pytest.param({}, False, 401, "UNAUTHORIZED", True, id="no-key-no-cookie"),
        pytest.param({"X-API-Key": "wrong"}, False, 401, "UNAUTHORIZED", True, id="wrong-key"),
        pytest.param(
            {"Cookie": f"{COOKIE_NAME}=stale", "Origin": ORIGIN},
            False,
            401,
            "UNAUTHORIZED",
            True,
            id="stale-cookie-owner-origin",
        ),
        pytest.param(
            {"Cookie": f"{COOKIE_NAME}=stale", "Origin": "https://evil.example.test"},
            False,
            403,
            "FORBIDDEN",
            False,
            id="stale-cookie-foreign-origin",
        ),
        pytest.param(
            {"Cookie": f"{COOKIE_NAME}=stale"},
            False,
            403,
            "FORBIDDEN",
            False,
            id="stale-cookie-no-origin",
        ),
        pytest.param(
            {"Cookie": f"{COOKIE_NAME}=session-sentinel", "Origin": ORIGIN},
            False,
            403,
            "FORBIDDEN",
            True,
            id="valid-cookie-no-csrf",
        ),
        pytest.param({}, True, 503, "AUTH_UNAVAILABLE", True, id="auth-store-unavailable"),
    ],
)
async def test_the_boundary_rejects_before_the_handler_and_never_touches_a_pool(
    headers, unavailable, status, code, authorize_called
):
    app, calls = _build(unavailable=unavailable)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        response = await client.post(PATH, headers=headers)

    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert bool(calls) is authorize_called
    assert _ForbiddenDB.touched is False
