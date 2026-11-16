"""GET /attention/break-through-clauses: an owner-cookie read of the one clause store (S3a).

The route reuses the closed cookie-only authoring call; it adds no store and no write.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from butlers.api.owner_auth.service import AuthError
from butlers.api.routers.schedules import router

pytestmark = pytest.mark.unit

PATH = (router.prefix or "") + "/attention/break-through-clauses"


def _client(*, authority: object | None, service: object | None) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.owner_auth_service = service

    @app.middleware("http")
    async def _state(request, call_next):
        request.state.owner_authority = authority
        return await call_next(request)

    return TestClient(app)


def test_the_owner_cookie_reads_the_list_through_the_closed_authoring_call() -> None:
    call = AsyncMock(return_value={"clauses": [{"id": "c1", "channel": "email"}]})
    client = _client(authority=object(), service=SimpleNamespace(attention_call=call))

    response = client.get(PATH, headers={"x-csrf-token": "t"})

    assert response.status_code == 200
    assert response.json()["data"] == {"clauses": [{"id": "c1", "channel": "email"}]}
    assert call.await_args.args[0] == "clause_list"


def test_no_owner_authority_is_refused_without_touching_the_store() -> None:
    call = AsyncMock()
    client = _client(authority=None, service=SimpleNamespace(attention_call=call))

    assert client.get(PATH).status_code == 403
    call.assert_not_awaited()


def test_a_refusal_from_the_store_keeps_its_status() -> None:
    call = AsyncMock(side_effect=AuthError("UNAUTHORIZED"))
    client = _client(authority=object(), service=SimpleNamespace(attention_call=call))

    assert client.get(PATH).status_code == 401
