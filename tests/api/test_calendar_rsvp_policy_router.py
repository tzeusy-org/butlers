"""Owner RSVP policy routes (bu-5ii4gj.9 S1, REQ-calendar-invitation-docket-001).

Thin routes over ``OwnerAuthService.rsvp_policy_call``; the definer's session, CSRF and vocabulary
checks are covered on real PostgreSQL in ``tests/migrations/test_core_388_calendar_rsvp_policies.py``.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from butlers.api.owner_auth.service import AuthError, Authority, OwnerAuthService
from butlers.api.routers.calendar_rsvp_policies import router

pytestmark = pytest.mark.unit

PID = str(uuid.uuid4())
GOOD = [{"kind": "overlaps_focus_or_out_of_office", "value": True}]
BODY = {"position": 1, "predicates": GOOD, "verdict": "declined", "send_updates": "none"}


def _client(call: AsyncMock) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.owner_auth_service = SimpleNamespace(rsvp_policy_call=call)

    @app.middleware("http")
    async def _state(request, call_next):
        request.state.owner_authority = object()
        return await call_next(request)

    return TestClient(app)


def test_create_forwards_only_the_policy_fields() -> None:
    call = AsyncMock(return_value={"outcome": "created", "id": PID, "version": 1})
    response = _client(call).post(
        "/api/calendar/rsvp-policies", json=BODY, headers={"x-csrf-token": "c1"}
    )
    assert response.status_code == 200
    action, _authority, csrf = call.await_args.args
    assert (action, csrf) == ("create", "c1")
    assert call.await_args.kwargs == BODY


@pytest.mark.parametrize(
    "predicates",
    [
        [{"kind": "title_contains", "value": "board"}],
        [{"kind": "description", "value": "x"}],
        [{"kind": "recurring", "value": "yes"}],
        [{"kind": "attendee_count", "value": 4}],
        [],
    ],
)
def test_a_predicate_outside_the_vocabulary_never_reaches_the_store(predicates) -> None:
    call = AsyncMock()
    response = _client(call).post(
        "/api/calendar/rsvp-policies", json=BODY | {"predicates": predicates}
    )
    assert response.status_code == 422
    call.assert_not_awaited()


@pytest.mark.parametrize(
    "extra",
    [{"actor": "owner"}, {"authority": "host"}, {"owner": "x"}, {"version": 9}, {"id": PID}],
)
def test_caller_supplied_identity_fields_are_rejected(extra) -> None:
    call = AsyncMock()
    assert _client(call).post("/api/calendar/rsvp-policies", json=BODY | extra).status_code == 422
    call.assert_not_awaited()


def test_update_sends_only_what_changed_and_refuses_an_empty_change() -> None:
    call = AsyncMock(return_value={"outcome": "updated", "id": PID, "version": 2})
    client = _client(call)
    assert (
        client.patch(
            f"/api/calendar/rsvp-policies/{PID}",
            json={"verdict": "tentative", "expected_version": 1},
        ).status_code
        == 200
    )
    assert call.await_args.args[0] == "update"
    assert call.await_args.kwargs == {"id": PID, "expected_version": 1, "verdict": "tentative"}
    assert client.patch(f"/api/calendar/rsvp-policies/{PID}", json={}).status_code == 422
    only_version = {"expected_version": 1}
    assert client.patch(f"/api/calendar/rsvp-policies/{PID}", json=only_version).status_code == 422
    no_version = {"verdict": "tentative"}
    assert client.patch(f"/api/calendar/rsvp-policies/{PID}", json=no_version).status_code == 422


def test_a_stale_version_is_a_409() -> None:
    call = AsyncMock(return_value={"outcome": "refused", "reason": "stale_version"})
    response = _client(call).patch(
        f"/api/calendar/rsvp-policies/{PID}", json={"verdict": "accepted", "expected_version": 1}
    )
    assert response.status_code == 409
    assert response.json()["error"]["details"] == {"reason": "stale_version"}


def test_revoke_and_list_reach_the_definer() -> None:
    call = AsyncMock(return_value={"outcome": "revoked", "id": PID, "version": 3})
    client = _client(call)
    assert client.post(f"/api/calendar/rsvp-policies/{PID}/revoke").status_code == 200
    assert call.await_args.args[0] == "revoke" and call.await_args.kwargs == {"id": PID}
    call.return_value = {"policies": []}
    assert client.get("/api/calendar/rsvp-policies").json()["data"] == {"policies": []}
    assert call.await_args.args[0] == "list"


def test_unknown_policy_is_404_and_a_revoked_one_is_409() -> None:
    unknown = AsyncMock(return_value={"outcome": "refused", "reason": "unknown_policy"})
    assert _client(unknown).post(f"/api/calendar/rsvp-policies/{PID}/revoke").status_code == 404
    revoked = AsyncMock(return_value={"outcome": "refused", "reason": "policy_revoked"})
    response = _client(revoked).patch(
        f"/api/calendar/rsvp-policies/{PID}", json={"verdict": "accepted", "expected_version": 1}
    )
    assert response.status_code == 409


def test_a_lost_write_outcome_is_reported_as_unknown() -> None:
    call = AsyncMock(side_effect=AuthError("AUTH_UNAVAILABLE"))
    response = _client(call).post("/api/calendar/rsvp-policies", json=BODY)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "RSVP_POLICY_OUTCOME_UNKNOWN"


def test_no_owner_authority_is_forbidden() -> None:
    app = FastAPI()
    app.include_router(router)
    assert TestClient(app).get("/api/calendar/rsvp-policies").status_code in {401, 403}


async def test_service_refuses_a_non_cookie_authority_and_unknown_actions() -> None:
    service = OwnerAuthService.__new__(OwnerAuthService)
    api_key = Authority(method="api_key", expires_at=None)
    with pytest.raises(AuthError):
        await service.rsvp_policy_call("create", api_key, "csrf")
    with pytest.raises(AuthError):
        await service.rsvp_policy_call("delete", api_key, "csrf")
