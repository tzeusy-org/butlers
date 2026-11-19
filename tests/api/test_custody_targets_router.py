"""The owner-channel candidate read behind the manual hold (bu-s11n0s.1 S5h)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from butlers.api.owner_auth.service import AuthError, Authority, OwnerAuthService
from butlers.api.routers.custody import router

pytestmark = pytest.mark.unit


def _client(call: AsyncMock) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.owner_auth_service = SimpleNamespace(custody_candidates_call=call)

    @app.middleware("http")
    async def _state(request, call_next):
        request.state.owner_authority = object()
        return await call_next(request)

    return TestClient(app)


def test_the_targets_read_forwards_one_list_action() -> None:
    call = AsyncMock(return_value={"candidates": [{"fact_id": "f", "kind": "has-email"}]})
    response = _client(call).get("/api/custody/targets")
    assert response.status_code == 200
    assert response.json()["data"]["candidates"][0]["fact_id"] == "f"
    assert call.await_args.args[0] == "list" and call.await_args.kwargs == {}


def test_an_unavailable_read_is_an_error_not_an_empty_list() -> None:
    call = AsyncMock(side_effect=AuthError("AUTH_UNAVAILABLE"))
    response = _client(call).get("/api/custody/targets")
    assert response.status_code >= 500
    assert "candidates" not in response.text


async def test_the_service_call_is_cookie_only_and_sends_only_the_session() -> None:
    service = OwnerAuthService.__new__(OwnerAuthService)
    service._capability_call = AsyncMock(return_value={"candidates": []})  # type: ignore[method-assign]
    cookie = Authority(method="cookie", expires_at=None, session_digest="d" * 64)
    await service.custody_candidates_call("list", cookie, "csrf")
    sql, action, values = service._capability_call.await_args.args
    assert "custody_candidates_dashboard" in sql and action == "list"
    assert values == {"session_digest": "d" * 64}
    with pytest.raises(AuthError):
        await service.custody_candidates_call("list", Authority(method="api_key", expires_at=None), "")
    with pytest.raises(AuthError):
        await service.custody_candidates_call("hold", cookie, "csrf")
