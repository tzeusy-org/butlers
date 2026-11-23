"""Endpoint custody routes and the dashboard custody call (bu-s11n0s.1 slice 2a).

The routes are thin: they forward a closed request to ``OwnerAuthService.custody_call``, which
calls the core_347 definer with the session digest and a CSRF digest. The definer's own checks
are covered on real PostgreSQL in ``tests/migrations/test_core_347_custody_command_engine.py``.
"""

from __future__ import annotations

import hashlib
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from butlers.api.owner_auth.service import AuthError, Authority, OwnerAuthService
from butlers.api.routers.custody import router

pytestmark = pytest.mark.unit

FACT = str(uuid.uuid4())
HOLD = str(uuid.uuid4())
COMMAND = str(uuid.uuid4())


def _client(call: AsyncMock, *, authority: object | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.owner_auth_service = SimpleNamespace(custody_call=call)
    owner = authority if authority is not None else object()

    @app.middleware("http")
    async def _state(request, call_next):
        request.state.owner_authority = owner
        return await call_next(request)

    return TestClient(app)


def test_hold_forwards_the_closed_request_with_the_csrf_header() -> None:
    call = AsyncMock(return_value={"outcome": "held", "holds": []})
    response = _client(call).post(
        "/api/custody/holds",
        json={"command_id": COMMAND, "fact_ids": [FACT], "reason": "lost"},
        headers={"x-csrf-token": "csrf-1"},
    )
    assert response.status_code == 200
    assert response.json()["data"] == {"outcome": "held", "holds": []}
    action, _authority, csrf = call.await_args.args
    assert (action, csrf) == ("hold", "csrf-1")
    assert call.await_args.kwargs == {"command_id": COMMAND, "fact_ids": [FACT], "reason": "lost"}


@pytest.mark.parametrize(
    "body",
    [
        {"command_id": COMMAND, "fact_ids": [FACT], "reason": "lost", "authority": "host"},
        {"command_id": COMMAND, "fact_ids": [FACT], "reason": "lost", "owner_entity_id": FACT},
        {"command_id": COMMAND, "fact_ids": [], "reason": "lost"},
        {"command_id": COMMAND, "fact_ids": ["owner@example.test"], "reason": "lost"},
        {"command_id": COMMAND, "fact_ids": [FACT], "reason": "bored"},
    ],
)
def test_caller_fields_and_raw_identifiers_are_rejected_before_the_store(body) -> None:
    call = AsyncMock()
    assert _client(call).post("/api/custody/holds", json=body).status_code == 422
    call.assert_not_awaited()


@pytest.mark.parametrize("action", ["release", "replace"])
def test_dispositions_carry_the_hold_and_expected_generation(action) -> None:
    call = AsyncMock(return_value={"outcome": action + "d"})
    response = _client(call).post(
        f"/api/custody/holds/{HOLD}/{action}",
        json={"command_id": COMMAND, "expected_generation": 2},
    )
    assert response.status_code == 200
    assert call.await_args.args[0] == action
    assert call.await_args.kwargs == {
        "hold_id": HOLD,
        "command_id": COMMAND,
        "expected_generation": 2,
    }


@pytest.mark.parametrize(
    ("reason", "status"),
    [("stale_generation", 409), ("still_bound", 409), ("not_found", 404)],
)
def test_refusals_keep_their_reason(reason, status) -> None:
    call = AsyncMock(return_value={"outcome": "refused", "reason": reason})
    response = _client(call).post(
        f"/api/custody/holds/{HOLD}/release",
        json={"command_id": COMMAND, "expected_generation": 1},
    )
    assert response.status_code == status
    assert response.json()["error"]["details"] == {"reason": reason}


def test_an_unavailable_store_on_a_mutation_reports_an_unknown_outcome() -> None:
    call = AsyncMock(side_effect=AuthError("AUTH_UNAVAILABLE"))
    response = _client(call).post(
        "/api/custody/holds",
        json={"command_id": COMMAND, "fact_ids": [FACT], "reason": "lost"},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "CUSTODY_OUTCOME_UNKNOWN"


def test_auth_refusals_keep_their_status() -> None:
    call = AsyncMock(side_effect=AuthError("UNAUTHORIZED"))
    assert _client(call).get("/api/custody/holds").status_code == 401


def test_read_back_and_list_use_the_read_actions() -> None:
    call = AsyncMock(return_value={"outcome": "unknown_command", "command_id": COMMAND})
    client = _client(call)
    response = client.get(f"/api/custody/commands/{COMMAND}")
    assert response.status_code == 200
    assert call.await_args.args[0] == "status"
    assert call.await_args.kwargs == {"command_id": COMMAND}
    client.get("/api/custody/holds")
    assert call.await_args.args[0] == "list"


async def test_custody_call_is_cookie_only_and_sends_digests() -> None:
    config = SimpleNamespace(origin="https://d.test", rp_id="d.test", api_key=None)
    service = OwnerAuthService(None, config)
    captured: dict = {}

    async def capability(sql: str, action: str, values: dict) -> dict:
        captured.update(sql=sql, action=action, values=values)
        return {"outcome": "held"}

    service._capability_call = capability  # type: ignore[method-assign]
    with pytest.raises(AuthError) as header_mode:
        await service.custody_call("hold", Authority("header", None), "c")
    assert header_mode.value.code == "FORBIDDEN"
    with pytest.raises(AuthError) as unknown:
        await service.custody_call("delete", Authority("cookie", None, "sd"), "c")
    assert unknown.value.code == "INVALID_REQUEST"
    assert captured == {}

    result = await service.custody_call(
        "hold", Authority("cookie", None, "session-digest"), "csrf", command_id=COMMAND
    )
    assert result == {"outcome": "held"}
    assert captured["sql"] == "SELECT public.custody_dashboard($1,$2::jsonb)"
    assert captured["values"] == {
        "command_id": COMMAND,
        "session_digest": "session-digest",
        "csrf_digest": hashlib.sha256(b"csrf").hexdigest(),
    }
