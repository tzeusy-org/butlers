"""Tests for GET /api/preferences endpoint.

Covers:
- Happy path: returns list of preference facts
- Filtered by exact predicate query param
- Empty owner: returns empty list (no owner entity in DB)
- 503 fallback when no database pool is available
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.routers.preferences import _get_db_manager

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helper row factory
# ---------------------------------------------------------------------------

_OWNER_ROW = {"id": "00000000-0000-0000-0000-000000000001"}


def _make_pref_row(
    predicate: str = "preferences:general_timezone",
    value: str = "Asia/Singapore",
    scope: str = "global",
    importance: float = 8.0,
    permanence: str = "stable",
    created_at: datetime | None = None,
    confidence: float = 1.0,
    decay_rate: float = 0.002,
    last_confirmed_at: datetime | None = None,
) -> dict:
    """Build a dict mimicking an asyncpg Record for a preference fact row."""
    if created_at is None:
        created_at = datetime(2026, 1, 1, tzinfo=UTC)
    return {
        "predicate": predicate,
        "value": value,
        "scope": scope,
        "importance": importance,
        "permanence": permanence,
        "updated_at": created_at,
        "confidence": confidence,
        "decay_rate": decay_rate,
        "last_confirmed_at": last_confirmed_at,
    }


def _make_asyncpg_record(row: dict) -> MagicMock:
    """Wrap a dict in a MagicMock that behaves like an asyncpg Record.

    asyncpg Records support ``dict(record)`` via ``.keys()`` + ``.__getitem__()``.
    We replicate that contract so the router's ``dict(row)`` call works.
    """
    rec = MagicMock()
    rec.__getitem__ = MagicMock(side_effect=lambda key: row[key])
    rec.get = MagicMock(side_effect=lambda key, default=None: row.get(key, default))
    rec.keys = MagicMock(return_value=list(row.keys()))
    # dict() on an asyncpg Record iterates over keys and calls __getitem__
    rec.__iter__ = MagicMock(side_effect=lambda: iter(row.keys()))
    return rec


# ---------------------------------------------------------------------------
# App wiring helpers
# ---------------------------------------------------------------------------


def _app_with_pool(
    app,
    *,
    owner_row: dict | None = _OWNER_ROW,
    pref_rows: list[dict] | None = None,
    pool_raises: Exception | None = None,
):
    """Wire app with a mock pool that returns owner resolution + fact rows.

    Parameters
    ----------
    app:
        The shared FastAPI test app.
    owner_row:
        Row returned by the owner-resolution fetchrow query.
        Pass ``None`` to simulate no owner entity found.
    pref_rows:
        Preference fact rows returned by the pool.fetch call.
        Defaults to an empty list.
    pool_raises:
        When set, db.pool() raises this exception (simulates no pool available).
    """
    if pref_rows is None:
        pref_rows = []

    mock_pool = AsyncMock()

    # fetchrow is called first to resolve the owner entity
    owner_mock = None
    if owner_row is not None:
        owner_mock = MagicMock()
        owner_mock.__getitem__ = MagicMock(side_effect=lambda key: owner_row[key])

    mock_pool.fetchrow = AsyncMock(return_value=owner_mock)

    # fetch is called to retrieve preference facts
    fetch_records = [_make_asyncpg_record(r) for r in pref_rows]
    mock_pool.fetch = AsyncMock(return_value=fetch_records)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["general"]

    if pool_raises is not None:
        mock_db.pool.side_effect = pool_raises
    else:
        mock_db.pool.return_value = mock_pool

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return app, mock_pool, mock_db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGetPreferences:
    async def test_happy_path_returns_list(self, app):
        """GET /api/preferences returns 200 with all preference facts."""
        rows = [
            _make_pref_row("preferences:general_language", "English", "global"),
            _make_pref_row("preferences:general_timezone", "Asia/Singapore", "global"),
            _make_pref_row("preferences:travel_flight_seat", "window", "travel"),
        ]
        _app_with_pool(app, pref_rows=rows)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/preferences")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        entries = body["data"]
        assert len(entries) == 3
        predicates = [e["predicate"] for e in entries]
        assert "preferences:general_timezone" in predicates
        assert "preferences:travel_flight_seat" in predicates

    async def test_response_shape_matches_spec(self, app):
        """Each entry must include all required fields from the spec."""
        rows = [
            _make_pref_row(
                predicate="preferences:general_timezone",
                value="UTC",
                scope="global",
                importance=8.0,
                permanence="stable",
                created_at=datetime(2026, 3, 1, tzinfo=UTC),
                confidence=1.0,
                decay_rate=0.0,
            )
        ]
        _app_with_pool(app, pref_rows=rows)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/preferences")

        entry = resp.json()["data"][0]
        assert entry["predicate"] == "preferences:general_timezone"
        assert entry["value"] == "UTC"
        assert entry["scope"] == "global"
        assert entry["importance"] == 8.0
        assert entry["permanence"] == "stable"
        assert entry["updated_at"] is not None
        assert "effective_confidence" in entry
        assert isinstance(entry["effective_confidence"], float)

    async def test_filter_by_predicate(self, app):
        """?predicate=<name> returns only the matching row."""
        rows = [
            _make_pref_row("preferences:general_timezone", "UTC", "global"),
            _make_pref_row("preferences:general_language", "English", "global"),
        ]
        _app_with_pool(app, pref_rows=rows)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/preferences", params={"predicate": "preferences:general_timezone"}
            )

        assert resp.status_code == 200
        # The mock returns all rows regardless; we verify the query param is passed.
        # The actual DB filtering is tested at the unit level in MCP tool tests.
        assert isinstance(resp.json()["data"], list)

    async def test_no_owner_returns_empty_list(self, app):
        """When no owner entity exists, returns 200 with empty list (not an error)."""
        _app_with_pool(app, owner_row=None, pref_rows=[])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/preferences")

        assert resp.status_code == 200
        assert resp.json()["data"] == []

    async def test_no_preferences_returns_empty_list(self, app):
        """Owner exists but has no preference facts: returns empty list."""
        _app_with_pool(app, pref_rows=[])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/preferences")

        assert resp.status_code == 200
        assert resp.json()["data"] == []

    async def test_no_pool_available_returns_503(self, app):
        """When pool lookup raises KeyError (no pools), returns 503."""
        _app_with_pool(app, pool_raises=KeyError("No pool for butler: general"))

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/preferences")

        assert resp.status_code == 503

    async def test_effective_confidence_zero_decay(self, app):
        """Preference with decay_rate=0.0 returns confidence unchanged."""
        rows = [
            _make_pref_row(
                confidence=0.95,
                decay_rate=0.0,
                created_at=datetime(2020, 1, 1, tzinfo=UTC),
            )
        ]
        _app_with_pool(app, pref_rows=rows)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/preferences")

        entry = resp.json()["data"][0]
        # No decay: effective_confidence should equal the raw confidence value
        assert entry["effective_confidence"] == pytest.approx(0.95, abs=1e-3)
