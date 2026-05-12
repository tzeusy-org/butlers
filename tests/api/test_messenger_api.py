"""Unit tests for messenger butler API endpoints [bu-iuol4.35].

Coverage:
- GET /api/messenger/delivery-stats  — success, empty, pool missing
- GET /api/messenger/circuit-status  — success, empty, pool missing
- GET /api/messenger/queue-depth     — success, empty, pool missing
- GET /api/messenger/dead-letters    — success, empty, pool missing
"""

from __future__ import annotations

import sys
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit

_NOW = datetime.now(tz=UTC)

# ---------------------------------------------------------------------------
# Bootstrap: create_app() triggers router discovery which loads the
# messenger_api_router module into sys.modules.  We extract the
# _get_db_manager stub from that module so dependency_overrides uses
# the same object identity that FastAPI registered.
# ---------------------------------------------------------------------------

_APP_SEED = create_app(api_key="")
_messenger_get_db_manager = sys.modules["messenger_api_router"]._get_db_manager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Row(dict):
    """dict subclass mimicking asyncpg Record."""

    def __getitem__(self, key):
        return super().__getitem__(key)


def _row(**kwargs) -> _Row:
    return _Row(kwargs)


def _make_pool(
    *,
    fetchval_results: list | None = None,
    fetch_results: list[list] | None = None,
) -> AsyncMock:
    """Return an AsyncMock pool with configurable fetch* side effects.

    ``fetchval_results`` is a list consumed in call order by ``fetchval``.
    ``fetch_results`` is a list of row-lists consumed in call order by ``fetch``.
    """
    pool = AsyncMock()

    fetchval_queue = list(fetchval_results or [])

    async def _fetchval(*args, **kwargs):
        return fetchval_queue.pop(0) if fetchval_queue else 0

    pool.fetchval = AsyncMock(side_effect=_fetchval)

    fetch_queue = list(fetch_results or [[]])

    async def _fetch(*args, **kwargs):
        return fetch_queue.pop(0) if fetch_queue else []

    pool.fetch = AsyncMock(side_effect=_fetch)

    return pool


def _make_app(pool) -> object:
    """Wire a fresh app with the given pool under the messenger butler."""
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool
    app = create_app(api_key="")
    app.dependency_overrides[_messenger_get_db_manager] = lambda: db
    return app


def _make_missing_pool_app() -> object:
    """Wire a fresh app where the messenger pool lookup raises KeyError."""
    db = MagicMock(spec=DatabaseManager)
    db.pool.side_effect = KeyError("No pool for butler: messenger")
    app = create_app(api_key="")
    app.dependency_overrides[_messenger_get_db_manager] = lambda: db
    return app


# ---------------------------------------------------------------------------
# GET /api/messenger/delivery-stats
# ---------------------------------------------------------------------------


async def test_delivery_stats_success():
    """Returns aggregated counts for the window."""
    status_rows = [
        _row(status="delivered", cnt=42),
        _row(status="failed", cnt=3),
        _row(status="dead_lettered", cnt=1),
        _row(status="pending", cnt=5),
    ]
    pool = _make_pool(
        # fetchval calls: retried count, then dispatched_at
        fetchval_results=[7, _NOW],
        # fetch call: status group-by
        fetch_results=[status_rows],
    )
    app = _make_app(pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/messenger/delivery-stats?window_hours=24")

    assert resp.status_code == 200
    body = resp.json()
    assert body["window_hours"] == 24
    assert body["delivered"] == 42
    assert body["failed"] == 3
    assert body["dead_letter"] == 1
    assert body["pending"] == 5
    assert body["retried"] == 7
    assert body["dispatched_at"] is not None


async def test_delivery_stats_empty():
    """Returns zeros when there are no deliveries in the window."""
    pool = _make_pool(
        fetchval_results=[0, None],
        fetch_results=[[]],
    )
    app = _make_app(pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/messenger/delivery-stats?window_hours=1")

    assert resp.status_code == 200
    body = resp.json()
    assert body["delivered"] == 0
    assert body["failed"] == 0
    assert body["dead_letter"] == 0
    assert body["pending"] == 0
    assert body["retried"] == 0
    assert body["dispatched_at"] is None


async def test_delivery_stats_pool_missing():
    """Returns 503 when the messenger pool is unavailable."""
    app = _make_missing_pool_app()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/messenger/delivery-stats")

    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/messenger/circuit-status
# ---------------------------------------------------------------------------


async def test_circuit_status_success():
    """Returns channel circuit states derived from recent delivery outcomes.

    dead_lettered rows are counted as failures (same as 'failed' status).
    Response includes source='db_approximation' to document the divergence
    from the real in-memory CircuitBreaker state.
    """
    circuit_rows = [
        _row(channel="telegram", failures=5, successes=0, last_activity=_NOW),
        _row(channel="email", failures=0, successes=10, last_activity=_NOW),
    ]
    pool = _make_pool(fetch_results=[circuit_rows])
    app = _make_app(pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/messenger/circuit-status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "db_approximation"
    channels = {ch["name"]: ch for ch in body["channels"]}
    assert channels["telegram"]["state"] == "open"
    assert channels["telegram"]["failure_rate_15m"] == 1.0
    assert channels["email"]["state"] == "closed"
    assert channels["email"]["failure_rate_15m"] == 0.0


async def test_circuit_status_empty():
    """Returns empty channels list when no recent activity."""
    pool = _make_pool(fetch_results=[[]])
    app = _make_app(pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/messenger/circuit-status")

    assert resp.status_code == 200
    assert resp.json()["channels"] == []


async def test_circuit_status_pool_missing():
    """Returns 503 when the messenger pool is unavailable."""
    app = _make_missing_pool_app()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/messenger/circuit-status")

    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/messenger/queue-depth
# ---------------------------------------------------------------------------


async def test_queue_depth_success():
    """Returns total (derived from channel counts) and per-channel queue depth.

    Now that the priority column exists, by_priority is populated from the
    second fetch which returns rows grouped by priority.
    """
    channel_rows = [
        _row(channel="telegram", cnt=8),
        _row(channel="email", cnt=3),
    ]
    priority_rows = [
        _row(priority="high", cnt=4),
        _row(priority="medium", cnt=7),
    ]
    pool = _make_pool(
        # No fetchval call — total is derived from channel-count sum.
        fetch_results=[channel_rows, priority_rows],
    )
    app = _make_app(pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/messenger/queue-depth")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 11  # sum of 8 + 3
    assert body["by_channel"]["telegram"] == 8
    assert body["by_channel"]["email"] == 3
    assert body["by_priority"]["high"] == 4
    assert body["by_priority"]["medium"] == 7


async def test_queue_depth_by_priority_populated():
    """by_priority is non-empty when the priority column exists and rows are present."""
    channel_rows = [_row(channel="telegram", cnt=5)]
    priority_rows = [
        _row(priority="high", cnt=2),
        _row(priority="medium", cnt=3),
    ]
    pool = _make_pool(fetch_results=[channel_rows, priority_rows])
    app = _make_app(pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/messenger/queue-depth")

    assert resp.status_code == 200
    body = resp.json()
    assert body["by_priority"] != {}, "by_priority should be populated when priority rows exist"
    assert body["by_priority"]["high"] == 2
    assert body["by_priority"]["medium"] == 3


async def test_queue_depth_empty():
    """Returns zeros when queue is empty."""
    pool = _make_pool(
        fetch_results=[[], []],
    )
    app = _make_app(pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/messenger/queue-depth")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["by_channel"] == {}


async def test_queue_depth_pool_missing():
    """Returns 503 when the messenger pool is unavailable."""
    app = _make_missing_pool_app()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/messenger/queue-depth")

    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/messenger/dead-letters
# ---------------------------------------------------------------------------


async def test_dead_letters_success():
    """Returns dead-letter entries with correct field mapping."""
    dl_id = uuid.uuid4()
    rows = [
        _row(
            id=dl_id,
            channel="telegram",
            target_identity="user_42",
            error_summary="Provider timeout",
            last_attempt_at=_NOW,
            total_attempts=3,
        )
    ]
    pool = _make_pool(fetch_results=[rows])
    app = _make_app(pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/messenger/dead-letters?limit=5")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["letters"]) == 1
    entry = body["letters"][0]
    assert entry["id"] == str(dl_id)
    assert entry["channel"] == "telegram"
    assert entry["recipient_id"] == "user_42"
    assert entry["error_message"] == "Provider timeout"
    assert entry["retry_count"] == 3


async def test_dead_letters_empty():
    """Returns empty list when no dead letters exist."""
    pool = _make_pool(fetch_results=[[]])
    app = _make_app(pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/messenger/dead-letters")

    assert resp.status_code == 200
    assert resp.json()["letters"] == []


async def test_dead_letters_pool_missing():
    """Returns 503 when the messenger pool is unavailable."""
    app = _make_missing_pool_app()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/messenger/dead-letters")

    assert resp.status_code == 503


async def test_dead_letters_default_limit():
    """Default limit of 20 is used when not specified."""
    pool = _make_pool(fetch_results=[[]])
    app = _make_app(pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/messenger/dead-letters")

    assert resp.status_code == 200
    # Verify the SQL was called with limit=20
    call_args = pool.fetch.call_args
    assert call_args[0][-1] == 20  # last positional arg is the limit
