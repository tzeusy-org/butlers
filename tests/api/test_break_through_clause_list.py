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


PROPOSALS = (router.prefix or "") + "/attention/break-through-proposals"
PROPOSAL_ID = "11111111-1111-4111-8111-111111111111"


def test_the_owner_cookie_lists_pending_proposals_through_the_authoring_call() -> None:
    call = AsyncMock(return_value={"proposals": [{"id": "p1", "channel": "email"}]})
    client = _client(authority=object(), service=SimpleNamespace(attention_call=call))

    response = client.get(PROPOSALS)

    assert response.status_code == 200
    assert response.json()["data"]["proposals"] == [{"id": "p1", "channel": "email"}]
    assert call.await_args is not None
    assert call.await_args.args[0] == "proposal_list"


@pytest.mark.parametrize(
    ("method", "suffix", "action"),
    [("POST", "/confirm", "proposal_confirm"), ("DELETE", "", "proposal_dismiss")],
)
def test_confirm_and_dismiss_forward_the_csrf_header_and_the_proposal_id(
    method: str, suffix: str, action: str
) -> None:
    call = AsyncMock(return_value={"id": "c1"})
    client = _client(authority="authority", service=SimpleNamespace(attention_call=call))

    response = client.request(
        method, f"{PROPOSALS}/{PROPOSAL_ID}{suffix}", headers={"x-csrf-token": "tok"}
    )

    assert response.status_code == 200
    assert call.await_args is not None
    assert call.await_args.args == (action, "authority", "tok")
    assert call.await_args.kwargs == {"id": PROPOSAL_ID}


@pytest.mark.parametrize(("method", "suffix"), [("POST", "/confirm"), ("DELETE", ""), ("GET", "")])
def test_confirm_dismiss_and_list_require_a_cookie_session(method: str, suffix: str) -> None:
    call = AsyncMock()
    client = _client(authority=None, service=SimpleNamespace(attention_call=call))

    path = PROPOSALS + (f"/{PROPOSAL_ID}{suffix}" if method != "GET" else "")
    assert client.request(method, path).status_code == 403
    call.assert_not_awaited()


def test_an_expired_or_consumed_proposal_is_refused_by_the_store() -> None:
    call = AsyncMock(side_effect=AuthError("INVALID_REQUEST"))
    client = _client(authority=object(), service=SimpleNamespace(attention_call=call))

    response = client.post(f"{PROPOSALS}/{PROPOSAL_ID}/confirm", headers={"x-csrf-token": "t"})

    assert response.status_code == 400


DECAY = (router.prefix or "") + "/attention/break-through-decay"
CLAUSE_ID = "22222222-2222-4222-8222-222222222222"


def test_the_decay_read_and_keep_go_through_the_closed_authoring_call() -> None:
    call = AsyncMock(return_value={"decay": []})
    client = _client(authority="authority", service=SimpleNamespace(attention_call=call))

    assert client.get(DECAY).status_code == 200
    assert call.await_args is not None and call.await_args.args[0] == "decay_state"
    response = client.post(
        (router.prefix or "") + f"/attention/break-through-clauses/{CLAUSE_ID}/keep",
        headers={"x-csrf-token": "tok"},
    )
    assert response.status_code == 200
    assert call.await_args.args == ("clause_keep", "authority", "tok")
    assert call.await_args.kwargs == {"id": CLAUSE_ID}


def test_keep_and_the_decay_read_require_a_cookie_session() -> None:
    call = AsyncMock()
    client = _client(authority=None, service=SimpleNamespace(attention_call=call))

    assert client.get(DECAY).status_code == 403
    keep = (router.prefix or "") + f"/attention/break-through-clauses/{CLAUSE_ID}/keep"
    assert client.post(keep).status_code == 403
    call.assert_not_awaited()
