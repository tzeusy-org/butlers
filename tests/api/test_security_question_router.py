"""Security question routes (bu-s11n0s.1 S5c, REQ-endpoint-custody-holds-008, dashboard half).

Thin routes over ``OwnerAuthService.security_question_call``; the definer's session, CSRF and
hold checks are covered on real PostgreSQL in
``tests/migrations/test_core_350_security_question_answer.py``.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from butlers.api.owner_auth.service import AuthError, Authority, OwnerAuthService
from butlers.api.routers.custody import router

pytestmark = pytest.mark.unit

EVENT = str(uuid.uuid4())
FACT = str(uuid.uuid4())
COMMAND = str(uuid.uuid4())
BASE = f"/api/custody/security-questions/{EVENT}"


def _client(call: AsyncMock) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.owner_auth_service = SimpleNamespace(security_question_call=call)

    @app.middleware("http")
    async def _state(request, call_next):
        request.state.owner_authority = object()
        return await call_next(request)

    return TestClient(app)


def test_yes_forwards_only_the_event_and_the_answer() -> None:
    call = AsyncMock(return_value={"outcome": "answered", "answer": "yes"})
    response = _client(call).post(
        f"{BASE}/answer", json={"answer": "yes"}, headers={"x-csrf-token": "c1"}
    )
    assert response.status_code == 200
    action, _authority, csrf = call.await_args.args
    assert (action, csrf) == ("answer", "c1")
    assert call.await_args.kwargs == {"event_id": EVENT, "answer": "yes"}


def test_no_forwards_the_command_targets_and_reason() -> None:
    call = AsyncMock(return_value={"outcome": "held", "answer": "no"})
    body = {"answer": "no", "command_id": COMMAND, "fact_ids": [FACT], "reason": "stolen"}
    assert _client(call).post(f"{BASE}/answer", json=body).status_code == 200
    assert call.await_args.kwargs == {"event_id": EVENT} | body


@pytest.mark.parametrize(
    "body",
    [
        {"answer": "yes", "fact_ids": [FACT]},
        {"answer": "yes", "command_id": COMMAND},
        {"answer": "no"},
        {"answer": "no", "command_id": COMMAND, "fact_ids": [FACT]},
        {"answer": "no", "command_id": COMMAND, "fact_ids": [], "reason": "lost"},
        {"answer": "no", "command_id": COMMAND, "fact_ids": ["a@b.test"], "reason": "lost"},
        {"answer": "maybe"},
        {"answer": "yes", "authority": "host"},
        {"answer": "yes", "actor": "owner"},
    ],
)
def test_malformed_and_caller_fields_are_rejected_before_the_store(body) -> None:
    call = AsyncMock()
    assert _client(call).post(f"{BASE}/answer", json=body).status_code == 422
    call.assert_not_awaited()


def test_read_adds_the_static_recovery_door() -> None:
    call = AsyncMock(return_value={"event_id": EVENT, "provider": "google", "candidates": []})
    response = _client(call).get(BASE)
    assert response.status_code == 200
    assert response.json()["data"]["recovery_door"].startswith("https://")
    assert call.await_args.args[0] == "get"


def test_unknown_question_is_404_and_a_refused_target_is_409() -> None:
    unknown = AsyncMock(return_value={"outcome": "refused", "reason": "unknown_question"})
    assert _client(unknown).get(BASE).status_code == 404
    refused = AsyncMock(return_value={"outcome": "refused", "reason": "not_owner_target"})
    body = {"answer": "no", "command_id": COMMAND, "fact_ids": [FACT], "reason": "lost"}
    assert _client(refused).post(f"{BASE}/answer", json=body).status_code == 409


def test_a_lost_answer_outcome_is_reported_as_unknown() -> None:
    call = AsyncMock(side_effect=AuthError("AUTH_UNAVAILABLE"))
    body = {"answer": "no", "command_id": COMMAND, "fact_ids": [FACT], "reason": "lost"}
    response = _client(call).post(f"{BASE}/answer", json=body)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "CUSTODY_OUTCOME_UNKNOWN"


async def test_service_refuses_a_non_cookie_authority_and_unknown_actions() -> None:
    service = OwnerAuthService.__new__(OwnerAuthService)
    api_key = Authority(method="api_key", expires_at=None)
    with pytest.raises(AuthError):
        await service.security_question_call("answer", api_key, "csrf")
    with pytest.raises(AuthError):
        await service.security_question_call("delete", api_key, "csrf")
