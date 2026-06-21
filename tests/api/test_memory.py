"""Tests for memory API endpoints.

Covers:
- ?butler= filter on /api/memory/episodes
  - Filter present + matching butler → fan-out restricted to that pool only
  - Filter absent → all rows across pools returned (existing behaviour)
  - Filter present + unknown butler → empty 200 (pool never queried; early exit)
- GET /api/memory/reembed/pending — count stale embeddings per tier
- POST /api/memory/reembed — trigger a synchronous re-embed run
- ?source_episode_id= filter on /api/memory/facts (episode provenance)
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
    consolidation_status: str = "pending",
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
        "consolidation_status": consolidation_status,
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


class _Record(dict):
    """Dict subclass standing in for an asyncpg Record.

    Supports both ``record[key]`` and ``record.get(key)`` with real None
    values, unlike MagicMock-based records whose ``.get`` returns a truthy
    mock.  Used by fact rows where ``_row_to_fact`` calls ``r.get(...)``.
    """


def _make_record(row: dict) -> _Record:
    """Return an asyncpg-Record-like mapping for the given row dict."""
    return _Record(row)


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


@pytest.mark.parametrize(
    "status",
    ["pending", "consolidated", "failed", "dead_letter"],
)
async def test_episodes_status_filter_adds_where_clause(app, status):
    """?status=<valid> filters on the consolidation_status column."""
    rows = [_make_episode_row(butler="atlas", consolidation_status=status)]
    mock_db = _wire_memory_db(app, rows=rows)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/memory/episodes", params={"status": status})

    assert resp.status_code == 200
    body = resp.json()
    # Two default pools ("atlas", "memory") each return the single mocked row,
    # so guard against a vacuous pass before checking element properties.
    assert len(body["data"]) == 2
    assert all(ep["consolidation_status"] == status for ep in body["data"])

    fetch_calls = mock_db.pool_mock.fetch.call_args_list
    assert any(
        "consolidation_status = $1" in call.args[0] and status in call.args[1:]
        for call in fetch_calls
    )


async def test_episodes_status_filter_combines_with_butler(app):
    """?butler and ?status both land in the WHERE clause with correct ordinals."""
    rows = [_make_episode_row(butler="atlas", consolidation_status="dead_letter")]
    mock_db = _wire_memory_db(app, rows=rows)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/memory/episodes",
            params={"butler": "atlas", "status": "dead_letter"},
        )

    assert resp.status_code == 200
    queried_names = {call.args[0] for call in mock_db.pool_lookup.call_args_list}
    assert queried_names == {"atlas"}

    fetch_calls = mock_db.pool_mock.fetch.call_args_list
    assert any(
        "butler = $1" in call.args[0]
        and "consolidation_status = $2" in call.args[0]
        and "atlas" in call.args[1:]
        and "dead_letter" in call.args[1:]
        for call in fetch_calls
    )


async def test_episodes_invalid_status_returns_422(app):
    """An out-of-enum ?status value is rejected by FastAPI validation (422)."""
    mock_db = _wire_memory_db(app, rows=[])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/memory/episodes", params={"status": "bogus"})

    assert resp.status_code == 422
    # No pool should have been queried for an invalid request.
    mock_db.pool_mock.fetch.assert_not_called()


# ---------------------------------------------------------------------------
# GET /api/memory/facts — source_episode_id filter
# ---------------------------------------------------------------------------


class _FactsPool:
    """Fake pool for the facts list endpoint.

    ``fetchval`` returns the precomputed total for the count query; ``fetch``
    returns the configured fact rows for the facts query and an empty list for
    any other query (e.g. public.entities name resolution).  Every ``fetch``
    call is recorded so tests can assert the WHERE clause and bound args.
    """

    def __init__(self, *, rows: list[dict], total: int) -> None:
        self._rows = [_make_record(r) for r in rows]
        self._total = total
        self.fetch_calls: list[tuple] = []

    async def fetchval(self, query: str, *args: object):
        return self._total

    async def fetch(self, query: str, *args: object):
        self.fetch_calls.append((query, args))
        if "FROM facts" in query:
            return self._rows
        return []


class _FactsDB:
    """DatabaseManager stand-in returning a distinct _FactsPool per butler."""

    def __init__(self, pools: dict[str, _FactsPool]) -> None:
        self._pools = pools
        self.butler_names = list(pools)

    def pool(self, name: str) -> _FactsPool:
        if name not in self._pools:
            raise KeyError(f"No pool for butler: {name}")
        return self._pools[name]


async def test_facts_source_episode_id_filter_adds_where_clause(app):
    """?source_episode_id binds a WHERE clause on the FK column."""
    episode_id = str(uuid.uuid4())
    fact_id = uuid.uuid4()
    row = _make_fact_row(fact_id=fact_id)
    row["source_episode_id"] = episode_id
    pool = _FactsPool(rows=[row], total=1)
    db = _FactsDB({"atlas": pool})
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/memory/facts", params={"source_episode_id": episode_id})

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) == 1
    assert body["data"][0]["source_episode_id"] == episode_id

    # The WHERE clause filtered on source_episode_id with the episode id bound.
    facts_fetches = [c for c in pool.fetch_calls if "FROM facts" in c[0]]
    assert facts_fetches
    assert any("source_episode_id = $1" in c[0] and episode_id in c[1] for c in facts_fetches)


async def test_facts_nonexistent_episode_returns_empty_not_error(app):
    """A valid-but-unmatched source_episode_id yields an empty 200, not an error."""
    episode_id = str(uuid.uuid4())
    pool = _FactsPool(rows=[], total=0)
    db = _FactsDB({"atlas": pool})
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/memory/facts", params={"source_episode_id": episode_id})

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["meta"]["total"] == 0


# ---------------------------------------------------------------------------
# GET /api/memory/facts — importance_min filter
# ---------------------------------------------------------------------------


async def test_facts_importance_min_filter_adds_where_clause(app):
    """?importance_min binds a ``importance >= $n`` WHERE clause."""
    row = _make_fact_row(fact_id=uuid.uuid4())
    row["importance"] = 8.0
    pool = _FactsPool(rows=[row], total=1)
    db = _FactsDB({"atlas": pool})
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/memory/facts", params={"importance_min": 8})

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) == 1
    assert body["data"][0]["importance"] == 8.0

    # The WHERE clause filtered on importance >= with the threshold bound.
    facts_fetches = [c for c in pool.fetch_calls if "FROM facts" in c[0]]
    assert facts_fetches
    assert any("importance >= $1" in c[0] and 8.0 in c[1] for c in facts_fetches)


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


async def test_stats_facts_produced_tracks_latest_run_not_max(app):
    """facts_produced reflects the LATEST run's value, even when it is smaller.

    Regression guard: a buggy MAX/SUM over facts_produced would pass the
    sibling aggregate test (where the newest run also has the most facts).
    Here the globally-latest run has *fewer* facts than an older run, so any
    implementation that picks the largest (or sums) facts_produced fails.
    """
    older = datetime(2026, 6, 10, tzinfo=UTC)
    newer = datetime(2026, 6, 12, tzinfo=UTC)
    db = _StatsDB(
        {
            # Older run with a LARGE facts_produced.
            "atlas": _StatsPool(
                counts={"consolidation_status = 'dead_letter'": 1},
                last_runs={"atlas": {"consolidated_at": older, "facts_produced": 99}},
            ),
            # Globally-latest run, but with a SMALL facts_produced.
            "memory": _StatsPool(
                counts={"consolidation_status = 'dead_letter'": 4},
                last_runs={"memory": {"consolidated_at": newer, "facts_produced": 2}},
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
    # last_consolidation_at = MAX(consolidated_at) across pools.
    assert data["last_consolidation_at"] == str(newer)
    # facts_produced follows the LATEST run (2), NOT the max (99) or sum (101).
    assert data["last_consolidation_facts_produced"] == 2
    # dead_letter_episodes = SUM across pools.
    assert data["dead_letter_episodes"] == 5


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


async def test_reembed_pending_without_butler_skips_non_memory_pools(app, monkeypatch):
    """Omitting ?butler aggregates memory-capable pools and skips other schemas."""
    from butlers.modules.memory import reembedding as _reembedding

    chronicler_pool = AsyncMock()
    general_pool = AsyncMock()
    health_pool = AsyncMock()
    pools = {
        "chronicler": chronicler_pool,
        "general": general_pool,
        "health": health_pool,
    }
    db_mock = MagicMock()
    db_mock.butler_names = list(pools)
    db_mock.pool = MagicMock(side_effect=lambda name: pools[name])
    app.dependency_overrides[_get_db_manager] = lambda: db_mock

    async def _fake_count_pending(pool, current_model, tier=None):
        if pool is chronicler_pool:
            raise RuntimeError('column "embedding" does not exist')
        if pool is general_pool:
            return {"episodes": 1, "facts": 2, "rules": 3}
        return {"episodes": 4, "facts": 5, "rules": 6}

    monkeypatch.setattr(_reembedding, "count_pending", _fake_count_pending)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/memory/reembed/pending")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["counts"] == {"episodes": 5, "facts": 7, "rules": 9}
    assert data["total"] == 21
    queried_names = {call.args[0] for call in db_mock.pool.call_args_list}
    assert queried_names == {"chronicler", "general", "health"}


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


# ---------------------------------------------------------------------------
# POST /api/memory/facts/{fact_id}/confirm
# ---------------------------------------------------------------------------


def _make_fact_row(
    *,
    fact_id: uuid.UUID,
    subject: str = "owner",
    predicate: str = "likes",
    content: str = "Owner likes tea",
    last_confirmed_at: datetime | None = None,
) -> dict:
    """Build a dict mimicking an asyncpg Record for the facts table."""
    return {
        "id": fact_id,
        "subject": subject,
        "predicate": predicate,
        "content": content,
        "importance": 5.0,
        "confidence": 0.9,
        "decay_rate": 0.008,
        "permanence": "standard",
        "source_butler": "atlas",
        "source_episode_id": None,
        "session_id": None,
        "supersedes_id": None,
        "entity_id": None,
        "object_entity_id": None,
        "validity": "active",
        "scope": "global",
        "reference_count": 0,
        "created_at": _NOW,
        "last_referenced_at": None,
        "last_confirmed_at": last_confirmed_at,
        "tags": None,
        "metadata": None,
    }


class _ConfirmPool:
    """Fake pool for the confirm endpoint.

    Holds at most one fact row keyed by id.  ``fetchrow`` returns that row when
    the id matches (used both for the initial locate and the post-confirm
    re-fetch).  ``execute`` stamps ``last_confirmed_at`` and reports
    ``UPDATE 1``/``UPDATE 0`` exactly like asyncpg + storage.confirm_memory.
    Entity-name resolution (public.entities) returns no rows.
    """

    def __init__(self, *, fact: dict | None) -> None:
        self._fact = fact
        self.execute_calls: list[tuple] = []

    async def fetchrow(self, query: str, *args: object):
        if (
            self._fact is not None
            and "FROM facts WHERE id" in query
            and args[0] == self._fact["id"]
        ):
            return _make_record(self._fact)
        return None

    async def fetch(self, query: str, *args: object):
        # _resolve_entity_names queries public.entities — no linked entities here.
        return []

    async def execute(self, query: str, *args: object) -> str:
        self.execute_calls.append((query, args))
        if self._fact is not None and args[0] == self._fact["id"]:
            self._fact["last_confirmed_at"] = _NOW
            return "UPDATE 1"
        return "UPDATE 0"


class _ConfirmDB:
    """DatabaseManager stand-in returning a distinct _ConfirmPool per butler."""

    def __init__(self, pools: dict[str, _ConfirmPool]) -> None:
        self._pools = pools
        self.butler_names = list(pools)

    def pool(self, name: str) -> _ConfirmPool:
        if name not in self._pools:
            raise KeyError(f"No pool for butler: {name}")
        return self._pools[name]


async def test_confirm_fact_reinks_and_returns_updated_fact(app):
    """POST confirm stamps last_confirmed_at and returns the updated Fact."""
    fact_id = uuid.uuid4()
    holding = _ConfirmPool(fact=_make_fact_row(fact_id=fact_id, last_confirmed_at=None))
    db = _ConfirmDB({"atlas": holding, "memory": _ConfirmPool(fact=None)})
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/api/memory/facts/{fact_id}/confirm")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["id"] == str(fact_id)
    # last_confirmed_at was previously null and is now stamped.
    assert data["last_confirmed_at"] is not None
    # The confirming UPDATE ran exactly once, on the pool that holds the fact.
    assert len(holding.execute_calls) == 1
    assert "last_confirmed_at = now()" in holding.execute_calls[0][0]


async def test_confirm_fact_404_when_not_found(app):
    """POST confirm returns 404 when no pool holds the fact."""
    fact_id = uuid.uuid4()
    db = _ConfirmDB({"atlas": _ConfirmPool(fact=None), "memory": _ConfirmPool(fact=None)})
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/api/memory/facts/{fact_id}/confirm")

    assert resp.status_code == 404


async def test_confirm_fact_400_on_malformed_id(app):
    """POST confirm returns 400 when the path id is not a valid UUID."""
    db = _ConfirmDB({"atlas": _ConfirmPool(fact=None)})
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/memory/facts/not-a-uuid/confirm")

    assert resp.status_code == 400


async def test_confirm_fact_503_when_no_pools_available(app):
    """POST confirm returns 503 when no memory pools are registered."""
    fact_id = uuid.uuid4()
    db = _ConfirmDB({})
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/api/memory/facts/{fact_id}/confirm")

    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# POST /api/memory/facts/{fact_id}/retract
# ---------------------------------------------------------------------------


class _RetractPool:
    """Fake pool for the retract endpoint.

    Holds at most one fact row keyed by id.  ``fetchrow`` returns that row when
    the id matches (used both for the initial locate via storage.forget_memory's
    re-fetch and the post-retract re-fetch).  ``execute`` flips ``validity`` to
    ``'retracted'`` and reports ``UPDATE 1``/``UPDATE 0`` exactly like asyncpg +
    storage.forget_memory.  Entity-name resolution (public.entities) returns no
    rows.
    """

    def __init__(self, *, fact: dict | None) -> None:
        self._fact = fact
        self.execute_calls: list[tuple] = []

    async def fetchrow(self, query: str, *args: object):
        if (
            self._fact is not None
            and "FROM facts WHERE id" in query
            and args[0] == self._fact["id"]
        ):
            return _make_record(self._fact)
        return None

    async def fetch(self, query: str, *args: object):
        # _resolve_entity_names queries public.entities — no linked entities here.
        return []

    async def execute(self, query: str, *args: object) -> str:
        self.execute_calls.append((query, args))
        if self._fact is not None and args[0] == self._fact["id"]:
            self._fact["validity"] = "retracted"
            return "UPDATE 1"
        return "UPDATE 0"


async def test_retract_fact_invalidates_and_returns_updated_fact(app):
    """POST retract flips validity to 'retracted' and returns the updated Fact."""
    fact_id = uuid.uuid4()
    holding = _RetractPool(fact=_make_fact_row(fact_id=fact_id))
    db = _ConfirmDB({"atlas": holding, "memory": _RetractPool(fact=None)})
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/api/memory/facts/{fact_id}/retract")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["id"] == str(fact_id)
    # validity was previously 'active' and is now 'retracted'.
    assert data["validity"] == "retracted"
    # The retracting UPDATE ran exactly once, on the pool that holds the fact.
    assert len(holding.execute_calls) == 1
    assert "validity = 'retracted'" in holding.execute_calls[0][0]


async def test_retract_fact_404_when_not_found(app):
    """POST retract returns 404 when no pool holds the fact."""
    fact_id = uuid.uuid4()
    db = _ConfirmDB({"atlas": _RetractPool(fact=None), "memory": _RetractPool(fact=None)})
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/api/memory/facts/{fact_id}/retract")

    assert resp.status_code == 404


async def test_retract_fact_400_on_malformed_id(app):
    """POST retract returns 400 when the path id is not a valid UUID."""
    db = _ConfirmDB({"atlas": _RetractPool(fact=None)})
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/memory/facts/not-a-uuid/retract")

    assert resp.status_code == 400


async def test_retract_fact_503_when_no_pools_available(app):
    """POST retract returns 503 when no memory pools are registered."""
    fact_id = uuid.uuid4()
    db = _ConfirmDB({})
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/api/memory/facts/{fact_id}/retract")

    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/memory/inspect — register-shaped result rows (bu-by2n0)
# ---------------------------------------------------------------------------


def _make_rule_row(
    *,
    rule_id: uuid.UUID,
    content: str = "Always confirm before deleting",
    maturity: str = "established",
) -> dict:
    """Build a dict mimicking an asyncpg Record for the rules table."""
    return {
        "id": rule_id,
        "content": content,
        "scope": "global",
        "maturity": maturity,
        "confidence": 0.7,
        "decay_rate": 0.01,
        "permanence": "standard",
        "effectiveness_score": 0.42,
        "applied_count": 9,
        "success_count": 7,
        "harmful_count": 2,
        "source_episode_id": None,
        "source_butler": "atlas",
        "created_at": _NOW,
        "last_applied_at": None,
        "last_evaluated_at": None,
        "tags": None,
        "metadata": None,
    }


class _InspectPool:
    """Fake pool for the inspect endpoint.

    Routes ``fetch`` by FROM clause: episode/fact/rule queries return their
    configured rows; the entity-name resolution query (public.entities) and any
    other query return an empty list.  Records every fetch for query assertions.
    """

    def __init__(
        self,
        *,
        episodes: list[dict] | None = None,
        facts: list[dict] | None = None,
        rules: list[dict] | None = None,
    ) -> None:
        self._episodes = [_make_record(r) for r in (episodes or [])]
        self._facts = [_make_record(r) for r in (facts or [])]
        self._rules = [_make_record(r) for r in (rules or [])]
        self.fetch_calls: list[tuple] = []

    async def fetchval(self, query: str, *args: object):
        return 0

    async def fetch(self, query: str, *args: object):
        self.fetch_calls.append((query, args))
        if "FROM episodes" in query:
            return self._episodes
        if "FROM facts" in query:
            return self._facts
        if "FROM rules" in query:
            return self._rules
        return []


def _inspect_db(pool: _InspectPool) -> _FactsDB:
    """Single-butler DatabaseManager stand-in for inspect tests."""
    return _FactsDB({"atlas": pool})  # type: ignore[arg-type]


async def test_inspect_fact_result_carries_full_register_fields(app):
    """A fact inspect result embeds the full Fact register row (subject/confidence/...)."""
    fact_id = uuid.uuid4()
    row = _make_fact_row(fact_id=fact_id, subject="owner", predicate="likes")
    pool = _InspectPool(facts=[row])
    app.dependency_overrides[_get_db_manager] = lambda: _inspect_db(pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/memory/inspect", params={"kind": "fact"})

    assert resp.status_code == 200
    results = resp.json()["data"]
    assert len(results) == 1
    r = results[0]
    # Backward-compat flat fields still present.
    assert r["id"] == str(fact_id)
    assert r["kind"] == "fact"
    assert "content" in r
    assert "metadata" in r
    # New register-shaped fact payload mirrors GET /facts.
    assert r["fact"] is not None
    assert r["fact"]["subject"] == "owner"
    assert r["fact"]["predicate"] == "likes"
    assert r["fact"]["confidence"] == 0.9
    assert r["fact"]["validity"] == "active"
    assert r["fact"]["permanence"] == "standard"
    # Other kinds' payloads are absent.
    assert r["rule"] is None
    assert r["episode"] is None


async def test_inspect_rule_result_carries_maturity_and_tally(app):
    """A rule inspect result embeds the full Rule register row (maturity + counts)."""
    rule_id = uuid.uuid4()
    row = _make_rule_row(rule_id=rule_id, maturity="proven")
    pool = _InspectPool(rules=[row])
    app.dependency_overrides[_get_db_manager] = lambda: _inspect_db(pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/memory/inspect", params={"kind": "rule"})

    assert resp.status_code == 200
    results = resp.json()["data"]
    assert len(results) == 1
    r = results[0]
    assert r["id"] == str(rule_id)
    assert r["kind"] == "rule"
    assert r["rule"] is not None
    assert r["rule"]["maturity"] == "proven"
    assert r["rule"]["applied_count"] == 9
    assert r["rule"]["success_count"] == 7
    assert r["rule"]["harmful_count"] == 2
    assert r["fact"] is None
    assert r["episode"] is None


async def test_inspect_episode_result_carries_importance_and_status(app):
    """An episode inspect result embeds the full Episode register row."""
    row = _make_episode_row(content="Logged in", consolidation_status="dead_letter")
    pool = _InspectPool(episodes=[row])
    app.dependency_overrides[_get_db_manager] = lambda: _inspect_db(pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/memory/inspect", params={"kind": "episode"})

    assert resp.status_code == 200
    results = resp.json()["data"]
    assert len(results) == 1
    r = results[0]
    assert r["kind"] == "episode"
    assert r["episode"] is not None
    assert r["episode"]["importance"] == 0.5
    assert r["episode"]["consolidation_status"] == "dead_letter"
    assert r["episode"]["consolidated"] is False
    assert r["fact"] is None
    assert r["rule"] is None


async def test_inspect_all_kinds_each_carry_their_register_payload(app):
    """Unscoped inspect returns one of each kind, each with its register payload."""
    pool = _InspectPool(
        episodes=[_make_episode_row()],
        facts=[_make_fact_row(fact_id=uuid.uuid4())],
        rules=[_make_rule_row(rule_id=uuid.uuid4())],
    )
    app.dependency_overrides[_get_db_manager] = lambda: _inspect_db(pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/memory/inspect")

    assert resp.status_code == 200
    results = resp.json()["data"]
    by_kind = {r["kind"]: r for r in results}
    assert by_kind["fact"]["fact"] is not None
    assert by_kind["rule"]["rule"] is not None
    assert by_kind["episode"]["episode"] is not None


class _InspectEntityPool(_InspectPool):
    """``_InspectPool`` that also resolves ``public.entities`` name lookups.

    The base pool returns ``[]`` for the entity-resolution query (no linked
    entities).  This variant returns the configured entity rows so the inspect
    handler's ``_resolve_entity_names`` pass populates ``entity_name`` /
    ``object_entity_name`` on the embedded fact, exactly like GET /facts.
    """

    def __init__(self, *, entities: dict[uuid.UUID, str], **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._entities = entities

    async def fetch(self, query: str, *args: object):
        self.fetch_calls.append((query, args))
        if "FROM public.entities" in query:
            requested = set(args[0])
            return [
                _make_record({"id": eid, "canonical_name": name})
                for eid, name in self._entities.items()
                if eid in requested
            ]
        if "FROM episodes" in query:
            return self._episodes
        if "FROM facts" in query:
            return self._facts
        if "FROM rules" in query:
            return self._rules
        return []


async def test_inspect_fact_result_resolves_embedded_entity_names(app):
    """An inspect fact whose embedded fact has entity_id/object_entity_id gets
    entity_name/object_entity_name resolved, mirroring GET /facts (#2199)."""
    fact_id = uuid.uuid4()
    subject_entity_id = uuid.uuid4()
    object_entity_id = uuid.uuid4()
    row = _make_fact_row(fact_id=fact_id)
    row["entity_id"] = subject_entity_id
    row["object_entity_id"] = object_entity_id
    pool = _InspectEntityPool(
        facts=[row],
        entities={subject_entity_id: "Owner", object_entity_id: "Green Tea"},
    )
    app.dependency_overrides[_get_db_manager] = lambda: _inspect_db(pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/memory/inspect", params={"kind": "fact"})

    assert resp.status_code == 200
    results = resp.json()["data"]
    assert len(results) == 1
    embedded = results[0]["fact"]
    assert embedded is not None
    # The embedded ids carry through, and the resolver populated the names.
    assert embedded["entity_id"] == str(subject_entity_id)
    assert embedded["object_entity_id"] == str(object_entity_id)
    assert embedded["entity_name"] == "Owner"
    assert embedded["object_entity_name"] == "Green Tea"
    # The resolution actually queried public.entities (not a hardcoded value).
    entity_fetches = [c for c in pool.fetch_calls if "FROM public.entities" in c[0]]
    assert entity_fetches


# ---------------------------------------------------------------------------
# GET /api/memory/facts/{fact_id} — superseded_by reverse lookup (bu-awo8k.8)
# ---------------------------------------------------------------------------


class _GetFactPool:
    """Fake pool for the single-fact GET endpoint.

    Holds at most one fact row keyed by id and an optional ``superseder_id`` —
    the id of a (newer) fact whose ``supersedes_id`` points at the held fact.
    ``fetchrow`` answers the two queries ``get_fact`` issues: the fact-by-id
    SELECT and the reverse ``WHERE supersedes_id = $1`` lookup.  Entity-name
    resolution (public.entities) returns no rows.
    """

    def __init__(self, *, fact: dict | None, superseder_id: uuid.UUID | None = None) -> None:
        self._fact = fact
        self._superseder_id = superseder_id

    async def fetchrow(self, query: str, *args: object):
        # get_fact passes the raw string path param; the held id is a UUID.
        # asyncpg coerces; the fake matches on the string form.
        wanted = str(self._fact["id"]) if self._fact is not None else None
        got = str(args[0]) if args else None
        if "WHERE supersedes_id" in query:
            if self._superseder_id is not None and got == wanted:
                return _make_record({"id": self._superseder_id})
            return None
        if "FROM facts WHERE id" in query and got == wanted:
            return _make_record(self._fact)
        return None

    async def fetch(self, query: str, *args: object):
        # _resolve_entity_names queries public.entities — no linked entities here.
        return []


async def test_get_fact_superseded_by_set_when_another_fact_supersedes_it(app):
    """GET fact populates superseded_by with the id of the superseding fact."""
    fact_id = uuid.uuid4()
    superseder_id = uuid.uuid4()
    holding = _GetFactPool(fact=_make_fact_row(fact_id=fact_id), superseder_id=superseder_id)
    db = _ConfirmDB({"atlas": holding, "memory": _GetFactPool(fact=None)})
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/memory/facts/{fact_id}")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["id"] == str(fact_id)
    assert data["superseded_by"] == str(superseder_id)
    # Forward link is independent and unaffected.
    assert data["supersedes_id"] is None


async def test_get_fact_superseded_by_none_when_nothing_supersedes_it(app):
    """GET fact leaves superseded_by None when no fact supersedes it."""
    fact_id = uuid.uuid4()
    holding = _GetFactPool(fact=_make_fact_row(fact_id=fact_id), superseder_id=None)
    db = _ConfirmDB({"atlas": holding, "memory": _GetFactPool(fact=None)})
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/memory/facts/{fact_id}")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["id"] == str(fact_id)
    assert data["superseded_by"] is None


async def test_get_fact_preserves_existing_fields_additively(app):
    """The superseded_by addition does not disturb existing Fact fields."""
    fact_id = uuid.uuid4()
    holding = _GetFactPool(
        fact=_make_fact_row(
            fact_id=fact_id,
            subject="owner",
            predicate="likes",
            content="Owner likes tea",
        ),
        superseder_id=None,
    )
    db = _ConfirmDB({"atlas": holding, "memory": _GetFactPool(fact=None)})
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/memory/facts/{fact_id}")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["subject"] == "owner"
    assert data["predicate"] == "likes"
    assert data["content"] == "Owner likes tea"
    assert data["validity"] == "active"
    assert data["scope"] == "global"
    assert data["importance"] == 5.0


async def test_get_fact_404_when_not_found(app):
    """GET fact returns 404 when no pool holds the fact."""
    fact_id = uuid.uuid4()
    db = _ConfirmDB({"atlas": _GetFactPool(fact=None), "memory": _GetFactPool(fact=None)})
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/memory/facts/{fact_id}")

    assert resp.status_code == 404
