"""Regression tests for ?butler= filter on /api/memory/episodes.

Verifies:
- Filter present + matching butler → only that butler's rows returned
- Filter absent → all rows across pools returned (existing behaviour)
- Filter present + unknown butler → empty 200 (rows filtered by SQL WHERE, pool still queried)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.routers.memory import _get_db_manager

pytestmark = pytest.mark.unit

_NOW = datetime.now(tz=UTC)


def _make_episode_row(
    *,
    butler: str = "atlas",
    content: str = "Test episode",
) -> dict:
    """Build a dict mimicking an asyncpg Record for the episodes table."""
    return {
        "id": uuid.uuid4(),
        "butler": butler,
        "session_id": uuid.uuid4(),
        "content": content,
        "importance": 0.5,
        "reference_count": 0,
        "consolidated": False,
        "created_at": _NOW,
        "last_referenced_at": None,
        "expires_at": None,
        "metadata": None,
    }


def _make_mock_record(row: dict) -> MagicMock:
    """Return a MagicMock that behaves like an asyncpg Record for row access."""
    m = MagicMock()
    m.__getitem__ = MagicMock(side_effect=lambda key: row[key])
    return m


def _wire_memory_db(app, *, rows: list[dict], total: int | None = None) -> MagicMock:
    """Wire app with a mock DatabaseManager returning the given episode rows."""
    if total is None:
        total = len(rows)

    mock_records = [_make_mock_record(r) for r in rows]

    mock_pool = AsyncMock()
    mock_pool.fetchval = AsyncMock(return_value=total)
    mock_pool.fetch = AsyncMock(return_value=mock_records)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["atlas", "memory"]
    mock_db.pool = MagicMock(return_value=mock_pool)

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return mock_db


# ---------------------------------------------------------------------------
# GET /api/memory/episodes
# ---------------------------------------------------------------------------


async def test_episodes_no_filter_returns_all_rows(app):
    """Omitting ?butler returns aggregated rows from all pools."""
    rows = [
        _make_episode_row(butler="atlas", content="atlas ep"),
        _make_episode_row(butler="memory", content="memory ep"),
    ]
    _wire_memory_db(app, rows=rows)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/memory/episodes")

    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert "meta" in body
    # Two pools each return 2 rows → 4 total (fan-out merges)
    assert len(body["data"]) == 4


async def test_episodes_butler_filter_applied_in_sql(app):
    """?butler=atlas passes the filter into the SQL WHERE clause.

    The mock pool returns rows matching the filter — we verify the query
    param is accepted, the response is 200, and the returned items have
    the correct butler value.
    """
    rows = [_make_episode_row(butler="atlas", content="atlas only")]
    mock_db = _wire_memory_db(app, rows=rows)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/memory/episodes", params={"butler": "atlas"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"][0]["butler"] == "atlas"

    # Verify the SQL passed to the pool included a WHERE clause with the
    # butler argument.  The pool is called once per butler_name in
    # _fan_out_memory_queries — check at least one call carried 'atlas'.
    call_args_list = mock_db.pool.return_value.fetch.call_args_list
    assert any("atlas" in str(call) for call in call_args_list)


async def test_episodes_unknown_butler_returns_empty_200(app):
    """?butler=nonexistent passes the filter through; SQL returns no rows → empty 200."""
    # The pool returns no rows because the WHERE clause matched nothing.
    _wire_memory_db(app, rows=[], total=0)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/memory/episodes", params={"butler": "nonexistent"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["meta"]["total"] == 0


async def test_episodes_butler_filter_combined_with_consolidated(app):
    """?butler and ?consolidated can be combined; both land in the WHERE clause."""
    rows = [_make_episode_row(butler="atlas")]
    mock_db = _wire_memory_db(app, rows=rows)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/memory/episodes", params={"butler": "atlas", "consolidated": "false"}
        )

    assert resp.status_code == 200
    # Both args should appear in the fetch call
    call_args_list = mock_db.pool.return_value.fetch.call_args_list
    all_calls = str(call_args_list)
    assert "atlas" in all_calls
    assert "False" in all_calls or "false" in all_calls.lower()
