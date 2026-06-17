"""Tests for GET /api/system/insights/delivery-state.

Spec: openspec/specs/insight-delivery/spec.md (bu-dl98i.3.3)

Coverage:
- Happy path: correct aggregates from seeded delivery rows
- Empty state: all zeros + null last_delivery_at when table is empty
- Delivered-only state: queued=0, delivered=N, failed=0
- Failed state: filters candidates with delivery_attempt_count>=3 correctly
- Dedup/cooldown-filtered not counted as failed
- Degraded: 503 when switchboard pool unavailable
- Degraded: 200 with zeros when table query fails (table may not exist)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.system import _get_db_manager

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 6, 17, 12, 0, 0, tzinfo=UTC)
_DELIVERED_AT = _NOW - timedelta(hours=2)


def _make_app_with_db(mock_db):
    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return app


def _make_pool(row: dict) -> AsyncMock:
    """Return an AsyncMock pool that yields `row` from fetchrow."""
    pool = AsyncMock()
    rec = MagicMock()
    rec.__getitem__ = MagicMock(side_effect=lambda k: row[k])
    rec.get = MagicMock(side_effect=lambda k, default=None: row.get(k, default))
    pool.fetchrow = AsyncMock(return_value=rec)
    return pool


def _make_db(pool: AsyncMock) -> MagicMock:
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = pool
    return mock_db


# ---------------------------------------------------------------------------
# Happy path: correct aggregates
# ---------------------------------------------------------------------------


async def test_happy_path_returns_correct_aggregates():
    """Endpoint returns queued/delivered/failed counts from seeded rows."""
    pool = _make_pool(
        {
            "queued": 3,
            "delivered": 10,
            "failed": 2,
            "last_delivery_at": _DELIVERED_AT,
        }
    )
    mock_db = _make_db(pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_make_app_with_db(mock_db)),
        base_url="http://test",
    ) as client:
        resp = await client.get("/api/system/insights/delivery-state")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["queued"] == 3
    assert data["delivered"] == 10
    assert data["failed"] == 2
    assert data["last_delivery_at"] is not None
    # last_delivery_at should be parseable ISO 8601
    parsed = datetime.fromisoformat(data["last_delivery_at"])
    assert parsed.tzinfo is not None


# ---------------------------------------------------------------------------
# Empty state
# ---------------------------------------------------------------------------


async def test_empty_state_all_zeros_null_last_delivery():
    """No delivery activity → all zeros and null last_delivery_at."""
    pool = _make_pool(
        {
            "queued": 0,
            "delivered": 0,
            "failed": 0,
            "last_delivery_at": None,
        }
    )
    mock_db = _make_db(pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_make_app_with_db(mock_db)),
        base_url="http://test",
    ) as client:
        resp = await client.get("/api/system/insights/delivery-state")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["queued"] == 0
    assert data["delivered"] == 0
    assert data["failed"] == 0
    assert data["last_delivery_at"] is None


# ---------------------------------------------------------------------------
# Delivered-only state
# ---------------------------------------------------------------------------


async def test_delivered_only_no_queued_or_failed():
    """Some delivered, none queued or failed."""
    pool = _make_pool(
        {
            "queued": 0,
            "delivered": 5,
            "failed": 0,
            "last_delivery_at": _DELIVERED_AT,
        }
    )
    mock_db = _make_db(pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_make_app_with_db(mock_db)),
        base_url="http://test",
    ) as client:
        resp = await client.get("/api/system/insights/delivery-state")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["queued"] == 0
    assert data["delivered"] == 5
    assert data["failed"] == 0
    assert data["last_delivery_at"] is not None


# ---------------------------------------------------------------------------
# Failed count (delivery_attempt_count >= 3)
# ---------------------------------------------------------------------------


async def test_failed_counts_only_delivery_failures():
    """failed field counts only delivery-failure-filtered rows (delivery_attempt_count>=3).

    Cooldown/dedup filtered rows (delivery_attempt_count==0) must NOT be included.
    This is validated by ensuring the SQL query uses the correct WHERE clause —
    tested here via the aggregate value the mock returns.
    """
    pool = _make_pool(
        {
            "queued": 1,
            "delivered": 2,
            "failed": 4,  # delivery-failure filtered
            "last_delivery_at": _DELIVERED_AT,
        }
    )
    mock_db = _make_db(pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_make_app_with_db(mock_db)),
        base_url="http://test",
    ) as client:
        resp = await client.get("/api/system/insights/delivery-state")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["failed"] == 4

    # Verify the SQL used the correct delivery_attempt_count filter
    call_args = pool.fetchrow.call_args
    sql = call_args[0][0]
    assert "delivery_attempt_count >= 3" in sql, (
        "SQL must filter failed by delivery_attempt_count >= 3 to exclude "
        "cooldown/dedup-filtered candidates"
    )


# ---------------------------------------------------------------------------
# SQL contract: query touches public.insight_candidates
# ---------------------------------------------------------------------------


async def test_query_targets_public_insight_candidates():
    """Endpoint queries public.insight_candidates (not a private schema table)."""
    pool = _make_pool({"queued": 0, "delivered": 0, "failed": 0, "last_delivery_at": None})
    mock_db = _make_db(pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_make_app_with_db(mock_db)),
        base_url="http://test",
    ) as client:
        await client.get("/api/system/insights/delivery-state")

    call_args = pool.fetchrow.call_args
    sql = call_args[0][0]
    assert "public.insight_candidates" in sql


# ---------------------------------------------------------------------------
# Degraded: switchboard pool unavailable → 503
# ---------------------------------------------------------------------------


async def test_503_when_switchboard_pool_unavailable():
    """503 when switchboard database is not available."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.side_effect = KeyError("switchboard")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_make_app_with_db(mock_db)),
        base_url="http://test",
    ) as client:
        resp = await client.get("/api/system/insights/delivery-state")

    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Degraded: table query fails → 200 with zeros (graceful degrade)
# ---------------------------------------------------------------------------


async def test_graceful_degrade_when_table_query_fails():
    """When the insight_candidates query fails, return 200 with all-zero state.

    This handles pre-migration deployments where the table may not yet exist.
    """
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(side_effect=RuntimeError("relation does not exist"))
    mock_db = _make_db(pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_make_app_with_db(mock_db)),
        base_url="http://test",
    ) as client:
        resp = await client.get("/api/system/insights/delivery-state")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["queued"] == 0
    assert data["delivered"] == 0
    assert data["failed"] == 0
    assert data["last_delivery_at"] is None


# ---------------------------------------------------------------------------
# Response shape: all required fields present
# ---------------------------------------------------------------------------


async def test_response_shape_all_required_fields_present():
    """Response always contains all four required fields."""
    pool = _make_pool({"queued": 0, "delivered": 0, "failed": 0, "last_delivery_at": None})
    mock_db = _make_db(pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_make_app_with_db(mock_db)),
        base_url="http://test",
    ) as client:
        resp = await client.get("/api/system/insights/delivery-state")

    assert resp.status_code == 200
    data = resp.json()["data"]
    for field in ("queued", "delivered", "failed", "last_delivery_at"):
        assert field in data, f"Required field '{field}' missing from response"
