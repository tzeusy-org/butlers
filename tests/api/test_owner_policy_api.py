"""The owner-policy dashboard routes are the one mutation path (bu-dhloi1.10, AC4).

Every route needs the owner's verified dashboard session; writer outcomes map
to closed error codes. The writer itself is covered against Postgres in
``tests/switchboard/test_owner_policy_postgres.py``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.db import DatabaseManager
from butlers.api.deps import get_db
from butlers.api.routers import owner_policy as routes
from butlers.capability_broker.contracts import BrokerRefused, Catalog, Reason
from butlers.core.dashboard_stepup import verified_dashboard_session
from butlers.core.owner_policy import PolicyConflict, PolicyUnavailable, StepUpRequired
from tests.capability_broker.helpers import capability

pytestmark = pytest.mark.unit

RECEIPT = {
    "owner_action_id": str(uuid4()),
    "term_id": str(uuid4()),
    "action": "create",
    "from_version": None,
    "to_version": 1,
    "generation": 4,
}
TERM = {
    "id": RECEIPT["term_id"],
    "version": 1,
    "term": "never",
    "selector_kind": "region",
    "selector": "messenger",
}


class _Pool:
    @asynccontextmanager
    async def acquire(self):
        yield object()


def _app(session: str | None) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def owner_auth(request, call_next):  # what OwnerAuthMiddleware sets
        token = verified_dashboard_session.set(session)
        try:
            return await call_next(request)
        finally:
            verified_dashboard_session.reset(token)

    app.include_router(routes.router)
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = _Pool()
    app.dependency_overrides[get_db] = lambda: db
    return app


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _create_body(**overrides):
    return {
        "owner_action_id": str(uuid4()),
        "selector_kind": "region",
        "selector": "messenger",
        "term": "never",
        **overrides,
    }


class _Writer:
    outcome: object = RECEIPT

    def __init__(self, connection):
        pass

    async def create(self, *args, **kwargs):
        return self._result()

    async def tighten(self, *args, **kwargs):
        return self._result()

    def _result(self):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@pytest.fixture
def writer(monkeypatch):
    monkeypatch.setattr(routes, "OwnerPolicyWriter", _Writer)
    _Writer.outcome = RECEIPT
    return _Writer


async def test_every_route_refuses_without_the_owner_dashboard_session(writer):
    async with _client(_app(None)) as client:
        responses = [
            await client.get("/api/owner-policy"),
            await client.post("/api/owner-policy/terms", json=_create_body()),
            await client.post(
                f"/api/owner-policy/terms/{uuid4()}/tighten",
                json={"owner_action_id": str(uuid4()), "expected_version": 1, "term": "never"},
            ),
            await client.get("/api/owner-policy/holds", params={"goal_id": str(uuid4())}),
        ]
    assert [r.status_code for r in responses] == [403] * 4
    assert {r.json()["detail"]["error"] for r in responses} == {"owner_dashboard_session_required"}


async def test_the_owner_session_creates_and_reads_back_the_typed_term(writer, monkeypatch):
    async def listed(connection):
        return {
            "generation": 4,
            "terms": [{**TERM, "resources": {}, "expires_at": None, "active": True}],
        }

    monkeypatch.setattr(routes, "list_terms", listed)
    async with _client(_app("session-digest")) as client:
        created = await client.post("/api/owner-policy/terms", json=_create_body())
        read = await client.get("/api/owner-policy")
    assert created.status_code == 201 and created.json()["data"] == RECEIPT
    assert read.json()["data"]["generation"] == 4
    assert read.json()["data"]["terms"][0]["term"] == "never"


@pytest.mark.parametrize(
    ("outcome", "status", "error"),
    [
        (StepUpRequired(), 403, "dashboard_step_up_required"),
        (PolicyConflict(), 409, "owner_policy_conflict"),
        (ValueError("selector"), 422, "owner_policy_invalid"),
        (PolicyUnavailable(), 503, "owner_policy_unavailable"),
    ],
)
async def test_writer_outcomes_map_to_closed_errors(writer, outcome, status, error):
    writer.outcome = outcome
    body = {"owner_action_id": str(uuid4()), "expected_version": 2, "term": "always_ask"}
    async with _client(_app("session-digest")) as client:
        response = await client.post(f"/api/owner-policy/terms/{uuid4()}/tighten", json=body)
    assert (response.status_code, response.json()["detail"]["error"]) == (status, error)


@pytest.mark.parametrize(
    "body",
    [
        _create_body(term="allow"),
        _create_body(selector_kind="mode"),
        _create_body(granted=True),  # no field may widen a grant
    ],
)
async def test_a_body_outside_the_closed_vocabulary_is_rejected(writer, body):
    async with _client(_app("session-digest")) as client:
        response = await client.post("/api/owner-policy/terms", json=body)
    assert response.status_code == 422


class _Catalog:
    def __init__(self, refused=False, alias_free=("to",)):
        self.refused, self.alias_free = refused, frozenset(alias_free)

    async def load(self, connection):
        if self.refused:
            raise BrokerRefused(Reason.UNAVAILABLE)
        send = capability(
            "send",
            "communicate",
            "messenger",
            resource_params=("to",),
            alias_free_params=self.alias_free,
        )
        return Catalog("v1", (send,))


@pytest.mark.parametrize(
    ("resources", "catalog", "status", "error"),
    [
        ({"account": ["a1"]}, _Catalog(), 422, "owner_policy_invalid"),  # undeclared key
        ({"to": ["  "]}, _Catalog(), 422, "owner_policy_invalid"),  # no canonical id
        # An aliasable param ("primary", "me", name-or-id) cannot narrow a deny-list.
        ({"to": ["a"]}, _Catalog(alias_free=()), 422, "owner_policy_narrowing_unsupported"),
        ({"to": ["a"]}, _Catalog(refused=True), 503, "owner_policy_unavailable"),
    ],
)
async def test_a_narrowing_is_checked_against_the_current_catalog(
    monkeypatch, resources, catalog, status, error
):
    """The real writer: these refuse before any database write."""
    monkeypatch.setattr(routes, "catalog_source", lambda: catalog)
    body = _create_body(selector_kind="capability", selector="messenger__send", resources=resources)
    async with _client(_app("session-digest")) as client:
        response = await client.post("/api/owner-policy/terms", json=body)
    assert (response.status_code, response.json()["detail"]["error"]) == (status, error)


@pytest.mark.parametrize("subject", ["goal_id", "effect_id", "refusal_id"])
async def test_holds_show_the_typed_receipt_for_one_subject(writer, monkeypatch, subject):
    subject_id = uuid4()
    hold = {
        "disposition": "parked",
        "capability": "messenger__send",
        "invocation_id": str(uuid4()),
        "effect_id": str(uuid4()),
        "refusal_id": None,
        "receipt": {
            "stage": "owner_law",
            "constraint": "always_ask",
            "generation": 4,
            "terms": [TERM],
        },
        "created_at": "2026-11-05T00:00:00+00:00",
    }
    seen = []

    async def holds(connection, kind, value):
        seen.append((kind, value))
        return [hold]

    monkeypatch.setattr(routes, "holds_for", holds)
    async with _client(_app("session-digest")) as client:
        response = await client.get("/api/owner-policy/holds", params={subject: str(subject_id)})
    assert response.json()["data"] == [hold] and seen == [(subject, subject_id)]


@pytest.mark.parametrize("params", [{}, {"goal_id": str(uuid4()), "effect_id": str(uuid4())}])
async def test_holds_need_exactly_one_subject(writer, params):
    async with _client(_app("session-digest")) as client:
        response = await client.get("/api/owner-policy/holds", params=params)
    assert (response.status_code, response.json()["detail"]["error"]) == (
        422,
        "owner_policy_invalid",
    )
