"""Regression tests for ?butler= filter on /api/memory/episodes.

Verifies:
- Filter present + matching butler → fan-out restricted to that pool only
- Filter absent → all rows across pools returned (existing behaviour)
- Filter present + unknown butler → empty 200 (pool never queried; early exit)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

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


class _MockDB:
    """Minimal DatabaseManager stand-in for fan-out tests.

    Attributes
    ----------
    pool_mock:
        The single AsyncMock pool returned for any known butler.
    pool_lookup:
        MagicMock wrapping the pool() method, used to assert which butler
        names were queried.
    """

    def __init__(self, *, rows: list[dict], total: int, known_butlers: list[str]) -> None:
        mock_records = [_make_mock_record(r) for r in rows]

        self.pool_mock = AsyncMock()
        self.pool_mock.fetchval = AsyncMock(return_value=total)
        self.pool_mock.fetch = AsyncMock(return_value=mock_records)

        self._known = known_butlers
        self.butler_names = known_butlers

        def _pool(name: str):
            if name not in self._known:
                raise KeyError(f"No pool for butler: {name}")
            return self.pool_mock

        self.pool_lookup = MagicMock(side_effect=_pool)

    def pool(self, name: str):  # type: ignore[override]
        return self.pool_lookup(name)


def _wire_memory_db(
    app,
    *,
    rows: list[dict],
    total: int | None = None,
    known_butlers: list[str] | None = None,
) -> _MockDB:
    """Wire app with a mock DatabaseManager returning the given episode rows.

    *known_butlers* sets db.butler_names (default: ["atlas", "memory"]).
    db.pool() raises KeyError for any name not in known_butlers, mirroring
    real DatabaseManager behaviour.
    """
    if total is None:
        total = len(rows)
    if known_butlers is None:
        known_butlers = ["atlas", "memory"]

    mock_db = _MockDB(rows=rows, total=total, known_butlers=known_butlers)
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return mock_db


# ---------------------------------------------------------------------------
# GET /api/memory/episodes
# ---------------------------------------------------------------------------


async def test_episodes_no_filter_fans_out_to_all_pools(app):
    """Omitting ?butler fans out to all pools and aggregates rows."""
    rows = [
        _make_episode_row(butler="atlas", content="atlas ep"),
        _make_episode_row(butler="memory", content="memory ep"),
    ]
    mock_db = _wire_memory_db(app, rows=rows)

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
    # pool() should have been called for both registered butlers
    queried_names = {call.args[0] for call in mock_db.pool_lookup.call_args_list}
    assert queried_names == {"atlas", "memory"}


async def test_episodes_butler_filter_narrows_fan_out_to_single_pool(app):
    """?butler=atlas restricts fan-out to the atlas pool only.

    The mock returns one row; the response is 200 with that row, and only the
    atlas pool is queried — the memory pool is never touched.
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

    # Only the atlas pool should have been looked up — not the memory pool.
    queried_names = {call.args[0] for call in mock_db.pool_lookup.call_args_list}
    assert queried_names == {"atlas"}

    # The SQL WHERE clause bound the butler value as the first positional arg.
    fetch_calls = mock_db.pool_mock.fetch.call_args_list
    assert any("butler = $1" in call.args[0] and "atlas" in call.args[1:] for call in fetch_calls)


async def test_episodes_unknown_butler_returns_empty_200_without_querying_any_pool(app):
    """?butler=nonexistent returns 200 + empty without hitting any pool.

    With the fan-out optimisation, an unknown butler triggers an early exit
    before any pool connection is used.
    """
    mock_db = _wire_memory_db(app, rows=[], total=0)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/memory/episodes", params={"butler": "nonexistent"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["meta"]["total"] == 0
    # pool() raised KeyError → no fetch/fetchval calls on any pool
    mock_db.pool_mock.fetch.assert_not_called()
    mock_db.pool_mock.fetchval.assert_not_called()


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
    # Only the atlas pool should have been queried.
    queried_names = {call.args[0] for call in mock_db.pool_lookup.call_args_list}
    assert queried_names == {"atlas"}
    # Both args should appear in the fetch call — check positional args directly.
    fetch_calls = mock_db.pool_mock.fetch.call_args_list
    assert any("butler = $1" in call.args[0] and "atlas" in call.args[1:] for call in fetch_calls)
    assert any(
        "consolidated = $2" in call.args[0] and False in call.args[1:] for call in fetch_calls
    )
