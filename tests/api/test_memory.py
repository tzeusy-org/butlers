"""Tests for memory API endpoints.

Covers:
- ?butler= filter on /api/memory/episodes
  - Filter present + matching butler → fan-out restricted to that pool only
  - Filter absent → all rows across pools returned (existing behaviour)
  - Filter present + unknown butler → empty 200 (pool never queried; early exit)
- GET /api/memory/reembed/pending — count stale embeddings per tier
- POST /api/memory/reembed — trigger a synchronous re-embed run
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


# ---------------------------------------------------------------------------
# GET /api/memory/stats — consolidation fields
# ---------------------------------------------------------------------------


class _StatsPool:
    """Fake pool that answers the /stats fan-out queries by SQL substring.

    *counts* maps a SQL substring → integer fetchval result (defaults 0).
    *last_run* maps butler name → fetchrow dict for public.consolidation_runs
    (None when that butler has no run).
    """

    def __init__(
        self,
        *,
        counts: dict[str, int] | None = None,
        last_runs: dict[str, dict | None] | None = None,
    ) -> None:
        self._counts = counts or {}
        self._last_runs = last_runs or {}

    async def fetchval(self, query: str, *args: object) -> int:
        for needle, value in self._counts.items():
            if needle in query:
                return value
        return 0

    async def fetchrow(self, query: str, *args: object) -> dict | None:
        if "consolidation_runs" in query:
            butler = args[0]
            return self._last_runs.get(butler)
        return None


class _StatsDB:
    """DatabaseManager stand-in returning a distinct _StatsPool per butler."""

    def __init__(self, pools: dict[str, _StatsPool]) -> None:
        self._pools = pools
        self.butler_names = list(pools)

    def pool(self, name: str) -> _StatsPool:
        if name not in self._pools:
            raise KeyError(f"No pool for butler: {name}")
        return self._pools[name]


async def test_stats_consolidation_fields_aggregate_across_pools(app):
    """dead_letter_episodes sums; last_consolidation picks the globally-latest run."""
    older = datetime(2026, 6, 10, tzinfo=UTC)
    newer = datetime(2026, 6, 12, tzinfo=UTC)
    db = _StatsDB(
        {
            "atlas": _StatsPool(
                counts={"consolidation_status = 'dead_letter'": 2},
                last_runs={"atlas": {"consolidated_at": older, "facts_produced": 7}},
            ),
            "memory": _StatsPool(
                counts={"consolidation_status = 'dead_letter'": 3},
                last_runs={"memory": {"consolidated_at": newer, "facts_produced": 11}},
            ),
        }
    )
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/memory/stats")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["dead_letter_episodes"] == 5
    # newer run (memory) wins over older (atlas)
    assert data["last_consolidation_at"] == str(newer)
    assert data["last_consolidation_facts_produced"] == 11


async def test_stats_consolidation_fields_default_when_no_runs(app):
    """No consolidation_runs rows → null timestamp/facts, dead_letter defaults to 0."""
    db = _StatsDB(
        {
            "atlas": _StatsPool(counts={}, last_runs={"atlas": None}),
        }
    )
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/memory/stats")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["last_consolidation_at"] is None
    assert data["last_consolidation_facts_produced"] is None
    assert data["dead_letter_episodes"] == 0
    # Existing fields remain present (backward-compatible).
    assert data["total_episodes"] == 0
    assert data["total_facts"] == 0


# ---------------------------------------------------------------------------
# Helpers for reembed endpoint tests
# ---------------------------------------------------------------------------


def _make_reembed_db(
    app,
    *,
    known_butlers: list[str] | None = None,
) -> tuple[AsyncMock, MagicMock]:
    """Wire app with a minimal mock DB for reembed endpoint tests.

    Returns (pool_mock, db_mock) for assertion inspection.
    """
    if known_butlers is None:
        known_butlers = ["memory"]

    pool_mock = AsyncMock()
    db_mock = MagicMock()
    db_mock.butler_names = known_butlers

    def _pool(name: str):
        if name not in known_butlers:
            raise KeyError(f"No pool for butler: {name}")
        return pool_mock

    db_mock.pool = MagicMock(side_effect=_pool)
    app.dependency_overrides[_get_db_manager] = lambda: db_mock
    return pool_mock, db_mock


# ---------------------------------------------------------------------------
# GET /api/memory/reembed/pending
# ---------------------------------------------------------------------------


async def test_reembed_pending_returns_counts_for_all_tiers(app, monkeypatch):
    """GET returns tier counts from count_pending() wrapped in ApiResponse."""
    from butlers.modules.memory import reembedding as _reembedding

    _make_reembed_db(app)

    expected_counts = {"episodes": 3, "facts": 7, "rules": 1}

    async def _fake_count_pending(pool, current_model, tier=None):
        return dict(expected_counts)

    monkeypatch.setattr(_reembedding, "count_pending", _fake_count_pending)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/memory/reembed/pending",
            params={"butler": "memory", "current_model": "all-MiniLM-L6-v2"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    data = body["data"]
    assert data["counts"] == expected_counts
    assert data["total"] == 11  # 3 + 7 + 1
    assert data["current_model"] == "all-MiniLM-L6-v2"


async def test_reembed_pending_uses_default_model_when_omitted(app, monkeypatch):
    """GET uses the default embedding model when current_model is not specified."""
    from butlers.modules.memory import reembedding as _reembedding

    _make_reembed_db(app)
    captured: list[str] = []

    async def _fake_count_pending(pool, current_model, tier=None):
        captured.append(current_model)
        return {"episodes": 0, "facts": 0, "rules": 0}

    monkeypatch.setattr(_reembedding, "count_pending", _fake_count_pending)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/memory/reembed/pending", params={"butler": "memory"})

    assert resp.status_code == 200
    assert captured == ["all-MiniLM-L6-v2"]


async def test_reembed_pending_404_for_unknown_butler(app):
    """GET returns 404 when the requested butler pool is not registered."""
    _make_reembed_db(app, known_butlers=["memory"])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/memory/reembed/pending", params={"butler": "nonexistent"})

    assert resp.status_code == 404


async def test_reembed_pending_400_on_bad_tier(app, monkeypatch):
    """GET returns 400 when count_pending raises ValueError for an invalid tier."""
    from butlers.modules.memory import reembedding as _reembedding

    _make_reembed_db(app)

    async def _fake_count_pending(pool, current_model, tier=None):
        raise ValueError("Unknown tier 'bogus'. Must be one of: ['episodes', 'facts', 'rules']")

    monkeypatch.setattr(_reembedding, "count_pending", _fake_count_pending)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/memory/reembed/pending",
            params={"butler": "memory", "current_model": "all-MiniLM-L6-v2"},
        )

    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/memory/reembed
# ---------------------------------------------------------------------------


async def test_reembed_post_dry_run_returns_result(app, monkeypatch):
    """POST with dry_run=true returns ReembedResult without writing to DB."""
    from butlers.modules.memory import reembedding as _reembedding

    _make_reembed_db(app)

    # The _fake_embedding_engine autouse fixture (root conftest.py) already
    # prevents any real sentence-transformers model load.  No extra engine stub
    # is required here; the reembedding.run stub below never uses the engine.

    dry_run_result = _reembedding.ReembedResult(
        dry_run=True,
        current_model="all-MiniLM-L6-v2",
        tiers_processed=["episodes", "facts", "rules"],
        counts={"episodes": 2, "facts": 5, "rules": 0},
        errors=[],
    )

    async def _fake_run(pool, engine, *, dry_run, tiers, batch_size):
        return dry_run_result

    monkeypatch.setattr(_reembedding, "run", _fake_run)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/memory/reembed",
            json={"butler": "memory", "dry_run": True, "batch_size": 50},
        )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["dry_run"] is True
    assert data["counts"] == {"episodes": 2, "facts": 5, "rules": 0}
    assert data["total"] == 7
    assert data["errors"] == []
    assert data["current_model"] == "all-MiniLM-L6-v2"


async def test_reembed_post_live_run_passes_correct_args(app, monkeypatch):
    """POST with dry_run=false passes correct params to reembedding.run()."""
    from butlers.modules.memory import reembedding as _reembedding

    _make_reembed_db(app)

    # _fake_embedding_engine autouse fixture (root conftest.py) prevents real
    # model loads; no extra engine stub needed here.

    captured: list[dict] = []

    async def _fake_run(pool, engine, *, dry_run, tiers, batch_size):
        captured.append({"dry_run": dry_run, "tiers": tiers, "batch_size": batch_size})
        return _reembedding.ReembedResult(
            dry_run=False,
            current_model="all-MiniLM-L6-v2",
            tiers_processed=["facts"],
            counts={"facts": 12},
            errors=[],
        )

    monkeypatch.setattr(_reembedding, "run", _fake_run)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/memory/reembed",
            json={
                "butler": "memory",
                "dry_run": False,
                "tiers": ["facts"],
                "batch_size": 100,
            },
        )

    assert resp.status_code == 200
    assert len(captured) == 1
    call = captured[0]
    assert call["dry_run"] is False
    assert call["tiers"] == ["facts"]
    assert call["batch_size"] == 100


async def test_reembed_post_400_on_invalid_tier(app, monkeypatch):
    """POST returns 400 when reembedding.run raises ValueError for invalid tiers."""
    from butlers.modules.memory import reembedding as _reembedding

    _make_reembed_db(app)

    # _fake_embedding_engine autouse fixture (root conftest.py) prevents real
    # model loads; no extra engine stub needed here.

    async def _fake_run(pool, engine, *, dry_run, tiers, batch_size):
        raise ValueError("Unknown tiers: ['bogus']")

    monkeypatch.setattr(_reembedding, "run", _fake_run)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/memory/reembed",
            json={"butler": "memory", "dry_run": True, "tiers": ["bogus"]},
        )

    assert resp.status_code == 400


async def test_reembed_post_404_for_unknown_butler(app):
    """POST returns 404 when the requested butler pool is not registered."""
    _make_reembed_db(app, known_butlers=["memory"])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/memory/reembed",
            json={"butler": "nonexistent", "dry_run": True},
        )

    assert resp.status_code == 404
