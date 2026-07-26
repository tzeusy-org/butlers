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
        self, *, rows: list[dict], total: int | None = None, raises: Exception | None = None
    ) -> None:
        self.butler_names = ["finance"]
        self.pool_mock = AsyncMock()
        if raises is not None:
            self.pool_mock.fetch = AsyncMock(side_effect=raises)
            self.pool_mock.fetchval = AsyncMock(side_effect=raises)
        else:
            self.pool_mock.fetchval = AsyncMock(
                return_value=total if total is not None else len(rows)
            )
            self.pool_mock.fetch = AsyncMock(return_value=rows)

    def pool(self, name: str):
        if name not in self.butler_names:
            raise KeyError(name)
        return self.pool_mock


def _wire_db(
    app, *, rows: list[dict], total: int | None = None, raises: Exception | None = None
) -> _MockDB:
    mock_db = _MockDB(rows=rows, total=total, raises=raises)
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
