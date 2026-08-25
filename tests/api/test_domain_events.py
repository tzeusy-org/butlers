"""Tests for GET /api/domain-events/{subscriptions,deliveries} — bu-317s5.

Mirrors the ``_MockDB``/``_Record`` harness style from ``tests/api/test_delegation.py``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest

from butlers.api.routers.domain_events import _get_db_manager

pytestmark = pytest.mark.unit

_NOW = datetime.now(tz=UTC)


class _Record(dict):
    """Dict subclass standing in for an asyncpg Record (supports ``.get`` with real None)."""


def _make_subscription_row(
    *,
    subscriber_butler: str = "finance",
    event_type: str = "travel.trip_booked",
    active: bool = True,
) -> _Record:
    return _Record(
        {
            "id": uuid.uuid4(),
            "subscriber_butler": subscriber_butler,
            "event_type": event_type,
            "active": active,
            "created_at": _NOW,
            "updated_at": _NOW,
        }
    )


def _make_delivery_row(
    *,
    subscriber_butler: str = "finance",
    event_type: str = "travel.trip_booked",
    source_butler: str = "travel",
    status: str = "delivered",
    task_id: uuid.UUID | None = None,
    task_name: str | None = None,
    error_message: str | None = None,
) -> _Record:
    return _Record(
        {
            "id": uuid.uuid4(),
            "event_id": uuid.uuid4(),
            "subscriber_butler": subscriber_butler,
            "status": status,
            "task_id": task_id,
            "task_name": task_name,
            "error_message": error_message,
            "delivered_at": _NOW if status == "delivered" else None,
            "created_at": _NOW,
            "updated_at": _NOW,
            "event_type": event_type,
            "source_butler": source_butler,
            "occurred_at": _NOW,
        }
    )


class _MockDB:
    """Minimal DatabaseManager stand-in — one pool serving the public tables."""

    def __init__(
        self,
        *,
        rows: list[dict],
        total: int | None = None,
        raises: Exception | None = None,
        reaction_rows: list[dict] | None = None,
        contract_rows: list[dict] | None = None,
    ) -> None:
        self.butler_names = ["finance"]
        self.pool_mock = AsyncMock()
        if raises is not None:
            self.pool_mock.fetch = AsyncMock(side_effect=raises)
            self.pool_mock.fetchval = AsyncMock(side_effect=raises)
            return
        self.pool_mock.fetchval = AsyncMock(return_value=total if total is not None else len(rows))

        async def _fetch(query: str, *_args):
            # Route by table so a reaction lookup never reads back delivery
            # rows -- a harness that answers every query with the same rows
            # can make a broken join look correct.
            if "domain_event_reactions" in query:
                return reaction_rows or []
            if "domain_event_contracts" in query:
                return contract_rows or []
            return rows

        self.pool_mock.fetch = AsyncMock(side_effect=_fetch)

    def pool(self, name: str):
        if name not in self.butler_names:
            raise KeyError(name)
        return self.pool_mock


def _wire_db(
    app,
    *,
    rows: list[dict],
    total: int | None = None,
    raises: Exception | None = None,
    reaction_rows: list[dict] | None = None,
    contract_rows: list[dict] | None = None,
) -> _MockDB:
    mock_db = _MockDB(
        rows=rows,
        total=total,
        raises=raises,
        reaction_rows=reaction_rows,
        contract_rows=contract_rows,
    )
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return mock_db


async def test_list_subscriptions_returns_rows(app):
    rows = [_make_subscription_row(), _make_subscription_row(subscriber_butler="health")]
    _wire_db(app, rows=rows)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/domain-events/subscriptions")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) == 2
    assert body["data"][0]["subscriber_butler"] == "finance"
    assert body["data"][0]["event_type"] == "travel.trip_booked"


async def test_list_subscriptions_passes_filters_through(app):
    mock_db = _wire_db(app, rows=[])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/domain-events/subscriptions",
            params={"subscriber_butler": "health", "event_type": "travel.trip_active"},
        )

    assert resp.status_code == 200
    fetch_query, *fetch_args = mock_db.pool_mock.fetch.await_args.args
    assert "subscriber_butler = $1" in fetch_query
    assert "event_type = $2" in fetch_query
    assert fetch_args[:2] == ["health", "travel.trip_active"]


async def test_list_subscriptions_surfaces_a_failed_source_as_an_error(app):
    """A genuinely failed query must never render as a truthful empty list."""
    _wire_db(app, rows=[], raises=RuntimeError("connection lost"))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/domain-events/subscriptions")

    assert resp.status_code == 500


async def test_list_deliveries_returns_rows_with_joined_event_fields(app):
    rows = [_make_delivery_row(task_id=uuid.uuid4(), task_name="domain-event-x-finance")]
    _wire_db(app, rows=rows)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/domain-events/deliveries")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) == 1
    assert body["meta"]["total"] == 1
    entry = body["data"][0]
    assert entry["status"] == "delivered"
    assert entry["event_type"] == "travel.trip_booked"
    assert entry["source_butler"] == "travel"
    assert entry["task_name"] == "domain-event-x-finance"


async def test_list_deliveries_rejects_invalid_status(app):
    _wire_db(app, rows=[])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/domain-events/deliveries", params={"status": "bogus"})

    assert resp.status_code == 400


async def test_list_deliveries_passes_filters_through(app):
    mock_db = _wire_db(app, rows=[])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/domain-events/deliveries",
            params={"subscriber_butler": "finance", "source_butler": "travel", "status": "failed"},
        )

    assert resp.status_code == 200
    fetch_query, *fetch_args = mock_db.pool_mock.fetch.await_args.args
    assert "d.subscriber_butler = $1" in fetch_query
    assert "e.source_butler = $2" in fetch_query
    assert "d.status = $3" in fetch_query
    assert fetch_args[:3] == ["finance", "travel", "failed"]


async def test_list_deliveries_surfaces_a_failed_source_as_an_error(app):
    _wire_db(app, rows=[], raises=RuntimeError("connection lost"))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/domain-events/deliveries")

    assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Reaction receipts and contract projection (bu-6jv4m.8)
# ---------------------------------------------------------------------------


def _make_reaction_row(
    *,
    event_id: uuid.UUID,
    subscriber_butler: str = "finance",
    status: str = "acted",
    session_id: str | None = "session-abc",
    note: str | None = "Opened a pre-budget.",
    evidence: list[dict] | None = None,
) -> _Record:
    return _Record(
        {
            "id": uuid.uuid4(),
            "event_id": event_id,
            "subscriber_butler": subscriber_butler,
            "status": status,
            "session_id": session_id,
            "task_name": "domain-event-x-finance",
            "note": note,
            "evidence": evidence if evidence is not None else [],
            "recorded_at": _NOW,
        }
    )


async def test_a_delivery_carries_its_reaction_outcome(app):
    delivery = _make_delivery_row()
    _wire_db(
        app,
        rows=[delivery],
        reaction_rows=[_make_reaction_row(event_id=delivery["event_id"])],
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/domain-events/deliveries")

    assert resp.status_code == 200
    entry = resp.json()["data"][0]
    assert entry["status"] == "delivered", "transport status must stay its own field"
    assert entry["reaction"]["status"] == "acted"
    assert entry["reaction"]["session_id"] == "session-abc"


async def test_a_delivered_wake_with_no_receipt_reports_no_reaction(app):
    """Positive control: 'delivered' must never be dressed up as an outcome."""
    _wire_db(app, rows=[_make_delivery_row()], reaction_rows=[])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/domain-events/deliveries")

    entry = resp.json()["data"][0]
    assert entry["status"] == "delivered"
    assert entry["reaction"] is None


async def test_the_reaction_trace_for_an_event_is_returned_in_order(app):
    event_id = uuid.uuid4()
    _wire_db(
        app,
        rows=[],
        reaction_rows=[
            _make_reaction_row(event_id=event_id, status="scheduled", session_id=None, note=None),
            _make_reaction_row(event_id=event_id, status="acted"),
        ],
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/domain-events/events/{event_id}/reactions")

    assert resp.status_code == 200
    assert [row["status"] for row in resp.json()["data"]] == ["scheduled", "acted"]


async def test_the_reaction_trace_rejects_a_malformed_event_id(app):
    _wire_db(app, rows=[])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/domain-events/events/not-a-uuid/reactions")

    assert resp.status_code == 400


async def test_contracts_are_listed_from_the_projection(app):
    _wire_db(
        app,
        rows=[],
        contract_rows=[
            _Record(
                {
                    "event_type": "travel.trip_booked",
                    "publisher": "travel",
                    "schema_version": 1,
                    "summary": "A brand-new trip container was created.",
                    "retention_policy": "standard",
                    "reaction_expectation": "expected",
                    "reaction_contract": "Consider a pre-budget check.",
                    "permitted_subscribers": ["finance"],
                    "required_fields": ["trip_id"],
                    "optional_fields": ["destination"],
                    "materialized_at": _NOW,
                }
            )
        ],
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/domain-events/contracts")

    assert resp.status_code == 200
    entry = resp.json()["data"][0]
    assert entry["event_type"] == "travel.trip_booked"
    assert entry["permitted_subscribers"] == ["finance"]
    assert entry["reaction_expectation"] == "expected"


async def test_reaction_reads_surface_a_failed_source_as_an_error(app):
    _wire_db(app, rows=[], raises=RuntimeError("connection lost"))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/domain-events/events/{uuid.uuid4()}/reactions")

    assert resp.status_code == 500
