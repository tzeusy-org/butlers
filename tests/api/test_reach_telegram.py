"""Telegram /reach route: connector-credential only, owner-verified, narrowing only (S3c-1)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.deps import get_db
from butlers.api.owner_auth.config import OwnerAuthConfig
from butlers.api.owner_auth.http import OwnerAuthMiddleware
from butlers.api.owner_auth.service import AuthError, OwnerAuthService
from butlers.api.routers import reach_telegram
from butlers.core.approval_callbacks import APPROVAL_CALLBACK_CONNECTOR_TOKEN_HEADER
from butlers.core.owner_auth_context import verified_http_principal
from tests.api.auth_helpers import _DomainOwnerState

pytestmark = pytest.mark.unit

PATH = "/api/attention/reach/telegram"
GOOD = {APPROVAL_CALLBACK_CONNECTOR_TOKEN_HEADER: "connector-key"}


def _app(
    monkeypatch, *, owner: tuple | None = (object(), True), service=None
) -> tuple[FastAPI, list]:
    seen: list = []
    monkeypatch.setattr(
        "butlers.identity.resolve_owner_channel_via_definer", AsyncMock(return_value=owner)
    )
    app = FastAPI()
    app.include_router(reach_telegram.router)
    app.add_middleware(
        OwnerAuthMiddleware,
        config=OwnerAuthConfig(
            "https://butlers.example.test",
            "butlers.example.test",
            "owner-key",
            "test",
            ("127.0.0.1",),
        ),
    )
    app.state.approval_callback_connector_token = "connector-key"
    base = _DomainOwnerState("owner-key")
    app.state.owner_auth_service = service if service is not None else base
    db = MagicMock()
    db.pool = MagicMock(return_value=object())
    app.dependency_overrides[get_db] = lambda: db

    @app.middleware("http")
    async def _principal(request, call_next):
        seen.append(verified_http_principal.get())
        return await call_next(request)

    return app, seen


class _Service(_DomainOwnerState):
    owner_channel_clauses: AsyncMock
    owner_channel_revoke: AsyncMock
    owner_channel_propose: AsyncMock
    attention_call: AsyncMock


def _service(*, clauses=None, revoke=None, propose=None) -> _Service:
    state = _Service("owner-key")
    state.owner_channel_clauses = AsyncMock(return_value=clauses or [])
    state.owner_channel_revoke = revoke or AsyncMock()
    state.owner_channel_propose = propose or AsyncMock(return_value="p1")
    state.attention_call = AsyncMock()
    return state


async def _post(app: FastAPI, body: dict, headers: dict | None = None) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post(PATH, json=body, headers=headers or {})


LIST = {"action": "list", "sender_id": "42", "chat_id": "42"}


async def test_the_route_is_connector_credential_only(monkeypatch):
    app, _ = _app(monkeypatch, service=_service())
    assert (await _post(app, LIST)).status_code == 401
    wrong = {APPROVAL_CALLBACK_CONNECTOR_TOKEN_HEADER: "nope"}
    assert (await _post(app, LIST, wrong)).status_code == 401
    assert (await _post(app, LIST, GOOD)).status_code == 200


async def test_the_credential_opens_no_adjacent_route(monkeypatch):
    app, _ = _app(monkeypatch, service=_service())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        other = await client.post("/api/attention/reach/other", json=LIST, headers=GOOD)
        assert other.status_code == 401
        assert (await client.get(PATH, headers=GOOD)).status_code == 401


async def test_list_returns_the_clauses_for_the_primary_owner(monkeypatch):
    service = _service(clauses=[{"id": "c1"}])
    app, _ = _app(monkeypatch, service=service)
    response = await _post(app, LIST, GOOD)
    assert response.json()["data"] == {"status": "ok", "clauses": [{"id": "c1"}]}
    service.owner_channel_clauses.assert_awaited_once_with()


async def test_revoke_passes_only_the_clause_id_to_the_narrowing_action(monkeypatch):
    clause = uuid4()
    service = _service()
    app, _ = _app(monkeypatch, service=service)
    body = {"action": "revoke", "sender_id": "42", "chat_id": "42", "clause_id": str(clause)}
    response = await _post(app, body, GOOD)
    assert response.json()["data"] == {"status": "revoked"}
    service.owner_channel_revoke.assert_awaited_once_with(str(clause))
    service.attention_call.assert_not_awaited()


async def test_a_non_owner_or_non_primary_sender_is_refused_without_touching_the_store(
    monkeypatch,
):
    for owner in (None, (object(), False)):
        service = _service()
        app, _ = _app(monkeypatch, owner=owner, service=service)
        response = await _post(app, LIST, GOOD)
        assert response.json()["data"] == {"status": "refused"}
        service.owner_channel_clauses.assert_not_awaited()
        service.owner_channel_revoke.assert_not_awaited()


async def test_the_route_cannot_express_a_grant(monkeypatch):
    service = _service()
    app, _ = _app(monkeypatch, service=service)
    body = {"action": "create", "sender_id": "42", "chat_id": "42"}
    assert (await _post(app, body, GOOD)).status_code == 422
    extra = LIST | {"scope": "anything", "sender_domain": "evil.example"}
    assert (await _post(app, extra, GOOD)).status_code == 422
    service.owner_channel_clauses.assert_not_awaited()
    service.owner_channel_revoke.assert_not_awaited()


async def test_revoke_needs_a_clause_and_an_unknown_clause_is_a_status(monkeypatch):
    service = _service(revoke=AsyncMock(side_effect=AuthError("INVALID_REQUEST")))
    app, _ = _app(monkeypatch, service=service)
    missing = {"action": "revoke", "sender_id": "42", "chat_id": "42"}
    assert (await _post(app, missing, GOOD)).status_code == 422
    unknown = missing | {"clause_id": str(uuid4())}
    response = await _post(app, unknown, GOOD)
    assert response.json()["data"] == {"status": "unknown_clause"}


async def test_the_connector_credential_never_becomes_an_owner_principal(monkeypatch):
    app, seen = _app(monkeypatch, service=_service())
    await _post(app, LIST, GOOD)
    assert seen == [None]


async def test_an_unavailable_owner_lookup_fails_closed(monkeypatch):
    service = _service()
    app, _ = _app(monkeypatch, service=service)
    monkeypatch.setattr(
        "butlers.identity.resolve_owner_channel_via_definer",
        AsyncMock(side_effect=RuntimeError("down")),
    )
    assert (await _post(app, LIST, GOOD)).status_code == 503
    service.owner_channel_clauses.assert_not_awaited()
    service.owner_channel_revoke.assert_not_awaited()


async def test_the_list_is_refused_to_any_chat_that_is_not_the_owners(monkeypatch):
    service = _service(clauses=[{"id": "c1"}])
    app, _ = _app(monkeypatch, service=service)
    owners = {"42": (object(), True)}

    async def resolve(_pool, _channel, value, **_):
        return owners.get(value)

    monkeypatch.setattr("butlers.identity.resolve_owner_channel_via_definer", resolve)
    group = LIST | {"chat_id": "-100123"}
    assert (await _post(app, group, GOOD)).json()["data"] == {"status": "refused"}
    service.owner_channel_clauses.assert_not_awaited()
    assert (await _post(app, LIST, GOOD)).json()["data"]["status"] == "ok"


def test_the_service_exposes_no_grant_path_to_the_owner_channel():
    names = {n for n in dir(OwnerAuthService) if "owner_channel" in n}
    assert names == {
        "owner_channel_clauses",
        "owner_channel_revoke",
        "owner_channel_propose",
        "_owner_channel_call",
    }


PROPOSE = {
    "action": "propose",
    "sender_id": "42",
    "chat_id": "42",
    "sender_domain": "bank.example",
    "channel": "email",
}


async def test_propose_passes_only_matchers_to_the_proposal_function(monkeypatch):
    service = _service()
    app, _ = _app(monkeypatch, service=service)
    response = await _post(app, PROPOSE, GOOD)
    assert response.json()["data"] == {"status": "proposed"}
    service.owner_channel_propose.assert_awaited_once_with(
        {
            "sender_domain": "bank.example",
            "channel": "email",
            "security_kind": None,
            "scope": None,
        }
    )
    # Never a clause: the granting authoring call is not touched.
    service.attention_call.assert_not_awaited()
    service.owner_channel_revoke.assert_not_awaited()


async def test_propose_is_refused_for_a_non_owner_or_a_group_chat(monkeypatch):
    for owner in (None, (object(), False)):
        service = _service()
        app, _ = _app(monkeypatch, owner=owner, service=service)
        assert (await _post(app, PROPOSE, GOOD)).json()["data"] == {"status": "refused"}
        service.owner_channel_propose.assert_not_awaited()
    service = _service()
    app, _ = _app(monkeypatch, service=service)
    owners = {"42": (object(), True)}

    async def resolve(_pool, _channel, value, **_):
        return owners.get(value)

    monkeypatch.setattr("butlers.identity.resolve_owner_channel_via_definer", resolve)
    group = PROPOSE | {"chat_id": "-100123"}
    assert (await _post(app, group, GOOD)).json()["data"] == {"status": "refused"}
    service.owner_channel_propose.assert_not_awaited()


async def test_propose_rejects_message_text_and_unbounded_matchers(monkeypatch):
    service = _service()
    app, _ = _app(monkeypatch, service=service)
    for bad in (
        PROPOSE | {"text": "please let everything through"},
        PROPOSE | {"channel": "sms"},
        PROPOSE | {"scope": "forever"},
        PROPOSE | {"sender_domain": "a" * 300},
        {k: v for k, v in PROPOSE.items() if k not in {"sender_domain", "channel"}},
    ):
        assert (await _post(app, bad, GOOD)).status_code == 422
    # Matchers belong to propose alone.
    assert (await _post(app, LIST | {"channel": "email"}, GOOD)).status_code == 422
    service.owner_channel_propose.assert_not_awaited()


async def test_propose_maps_definer_refusals_to_statuses(monkeypatch):
    for code, status in (("INVALID_REQUEST", "invalid"), ("RATE_LIMITED", "too_many_pending")):
        service = _service(propose=AsyncMock(side_effect=AuthError(code)))
        app, _ = _app(monkeypatch, service=service)
        assert (await _post(app, PROPOSE, GOOD)).json()["data"] == {"status": status}


async def test_the_connector_credential_cannot_call_clause_create_or_confirm(monkeypatch):
    service = _service()
    app, _ = _app(monkeypatch, service=service)
    from butlers.api.routers.schedules import router as schedules_router

    app.include_router(schedules_router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for method, path in (
            ("POST", "/api/butlers/attention/break-through-clauses"),
            ("POST", f"/api/butlers/attention/break-through-proposals/{uuid4()}/confirm"),
            ("DELETE", f"/api/butlers/attention/break-through-proposals/{uuid4()}"),
            ("GET", "/api/butlers/attention/break-through-proposals"),
        ):
            response = await client.request(
                method, path, json={"scope": "always", "channel": "email"}, headers=GOOD
            )
            assert response.status_code in {401, 403}, (method, path)
    service.attention_call.assert_not_awaited()


async def test_owner_channel_propose_hands_the_sql_a_dict_not_a_json_string():
    """The pool's jsonb codec encodes the argument; a pre-encoded string would be double-encoded."""
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import patch

    connection = MagicMock()
    connection.execute = AsyncMock()
    connection.fetchval = AsyncMock(return_value={"id": "p1"})
    connection.transaction = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None), __aexit__=AsyncMock(return_value=False)
        )
    )

    @asynccontextmanager
    async def acquire():
        yield connection

    pool: Any = SimpleNamespace(acquire=acquire)
    service = OwnerAuthService(pool, MagicMock())
    with patch("butlers.api.owner_auth.service.register_jsonb_codec", AsyncMock()) as codec:
        assert await service.owner_channel_propose({"channel": "email"}) == "p1"
    codec.assert_awaited_once()
    call = connection.fetchval.await_args
    assert call is not None
    args = call.args
    assert "owner_channel_propose" in args[0]
    assert args[1] == {"channel": "email"} and isinstance(args[1], dict)
