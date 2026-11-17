"""GET /attention/break-through-clauses: an owner-cookie read of the one clause store (S3a).

The route reuses the closed cookie-only authoring call; it adds no store and no write.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from butlers.api.deps import get_db
from butlers.api.owner_auth.service import AuthError
from butlers.api.routers.schedules import router

pytestmark = pytest.mark.unit

_POOLS: list[SimpleNamespace] = []
PATH = (router.prefix or "") + "/attention/break-through-clauses"


def _client(
    *, authority: object | None, service: object | None, names: dict[str, str] | None = None
) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    pool = SimpleNamespace(
        fetch=AsyncMock(
            return_value=[{"id": k, "canonical_name": v} for k, v in (names or {}).items()]
        )
    )
    _POOLS.append(pool)
    app.dependency_overrides[get_db] = lambda: SimpleNamespace(pool=lambda _name: pool)
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
    assert call.await_args is not None
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


def test_a_clause_naming_an_entity_carries_its_display_name_and_others_do_not() -> None:
    call = AsyncMock(
        return_value={
            "clauses": [
                {"id": "c1", "entity_id": "e1", "channel": None},
                {"id": "c2", "entity_id": None, "channel": "email"},
            ]
        }
    )
    client = _client(
        authority=object(), service=SimpleNamespace(attention_call=call), names={"e1": "Mum"}
    )

    clauses = client.get(PATH).json()["data"]["clauses"]

    assert clauses[0]["entity_name"] == "Mum"
    assert "entity_name" not in clauses[1]


def test_an_unreadable_name_never_fails_the_list() -> None:
    call = AsyncMock(return_value={"clauses": [{"id": "c1", "entity_id": "e1"}]})
    client = _client(authority=object(), service=SimpleNamespace(attention_call=call))
    _POOLS[-1].fetch.side_effect = RuntimeError("down")

    response = client.get(PATH)

    assert response.status_code == 200
    assert "entity_name" not in response.json()["data"]["clauses"][0]
