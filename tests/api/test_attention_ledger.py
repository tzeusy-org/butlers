"""Tests for GET /api/attention/ledger and /api/attention/ledger/summary --
the attention ledger's first reader (bu-tdd4k.4).

A shared pool that is genuinely unreachable (KeyError from db.pool("switchboard"))
must be distinguishable from a truthful "no rows match" result: both currently
render an empty/all-zero payload, but only the unreachable case must carry
``source_available: false`` (mirrors ``tests/api/test_notifications.py``).

The summary endpoint's ``suppressed_never_delivered`` flag is the marquee
signal this whole reader exists to surface -- the exact live failure the
epic fixed for secrets_lifecycle (120 suppressed / 0 delivered, bu-tdd4k.2).
"""

from __future__ import annotations

from datetime import UTC
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.routers.attention_ledger import _get_db_manager

pytestmark = pytest.mark.unit


def _make_unavailable_db() -> MagicMock:
    """A DatabaseManager stand-in whose switchboard pool is unreachable."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.side_effect = KeyError("switchboard")
    return mock_db


def _make_available_db() -> tuple[MagicMock, AsyncMock]:
    """A DatabaseManager stand-in with a working switchboard pool."""
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=0)
    pool.fetch = AsyncMock(return_value=[])
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = pool
    return mock_db, pool


def _row(**overrides: object) -> dict:
    base = {
        "origin_butler": "secrets_lifecycle",
        "delivered": 0,
        "coalesced": 0,
        "deferred": 3,
        "suppressed": 120,
        "failed": 0,
        "total": 123,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# GET /api/attention/ledger -- degraded envelope
# ---------------------------------------------------------------------------


async def test_list_ledger_flags_source_unavailable_when_pool_unreachable(app):
    app.dependency_overrides[_get_db_manager] = _make_unavailable_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/attention/ledger")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["source_available"] is False


async def test_list_ledger_reports_source_available_on_success(app):
    mock_db, _pool = _make_available_db()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/attention/ledger")

    assert resp.status_code == 200
    assert resp.json()["source_available"] is True


async def test_list_ledger_missing_table_degrades_to_empty_not_unavailable(app):
    """An unmigrated DB (UndefinedTableError) is a truthful empty page --
    distinct from a genuinely unreachable pool."""

    class _UndefinedTableError(Exception):
        pass

    _UndefinedTableError.__name__ = "UndefinedTableError"

    pool = AsyncMock()
    pool.fetchval = AsyncMock(side_effect=_UndefinedTableError("relation does not exist"))
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = pool
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/attention/ledger")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["source_available"] is True


# ---------------------------------------------------------------------------
# GET /api/attention/ledger -- filters thread into the SQL
# ---------------------------------------------------------------------------


async def test_list_ledger_applies_since_until_and_filters(app):
    mock_db, pool = _make_available_db()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/attention/ledger"
            "?since=2026-07-01T00:00:00Z&until=2026-07-08T00:00:00Z"
            "&intent=send&source=notify&outcome=suppressed&origin_butler=secrets_lifecycle"
        )

    assert resp.status_code == 200
    fetchval_call = pool.fetchval.call_args_list[0]
    sql = fetchval_call.args[0]
    assert "occurred_at >=" in sql
    assert "occurred_at <=" in sql
    assert "intent = " in sql
    assert "source = " in sql
    assert "outcome = " in sql
    assert "origin_butler = " in sql
    # since, until, intent, source, outcome, origin_butler -- six bound values.
    assert len(fetchval_call.args) - 1 == 6


async def test_list_ledger_no_filters_omits_every_predicate(app):
    mock_db, pool = _make_available_db()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/attention/ledger")

    assert resp.status_code == 200
    sql = pool.fetchval.call_args_list[0].args[0]
    assert "WHERE" not in sql


# ---------------------------------------------------------------------------
# GET /api/attention/ledger/summary -- degraded envelope
# ---------------------------------------------------------------------------


async def test_summary_flags_source_unavailable_when_pool_unreachable(app):
    app.dependency_overrides[_get_db_manager] = _make_unavailable_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/attention/ledger/summary")

    assert resp.status_code == 200
    body = resp.json()
    assert body["by_source"] == []
    assert body["flagged_sources"] == []
    assert body["source_available"] is False


async def test_summary_defaults_to_a_seven_day_window_when_since_omitted(app):
    mock_db, pool = _make_available_db()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/attention/ledger/summary")

    assert resp.status_code == 200
    body = resp.json()
    assert body["since"] is not None
    assert body["until"] is None
    # The bound `since` argument passed to the query is the same value echoed
    # back in the response envelope.
    fetch_call = pool.fetch.call_args_list[0]
    assert len(fetch_call.args) - 1 == 1


async def test_summary_explicit_since_is_respected(app):
    mock_db, pool = _make_available_db()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/attention/ledger/summary?since=2026-01-01T00:00:00Z")

    assert resp.status_code == 200
    body = resp.json()
    assert body["since"].startswith("2026-01-01T00:00:00")


# ---------------------------------------------------------------------------
# GET /api/attention/ledger/summary -- suppressed_never_delivered is the
# marquee flag this endpoint exists to compute.
# ---------------------------------------------------------------------------


async def test_summary_flags_suppressed_never_delivered_source():
    """secrets_lifecycle: 120 suppressed, 0 delivered -- the exact live
    failure bu-tdd4k.2 fixed. Must render suppressed_never_delivered=True."""
    from datetime import datetime

    from butlers.api.routers.attention_ledger import _query_ledger_summary

    pool = AsyncMock()
    pool.fetch = AsyncMock(
        return_value=[
            _row(origin_butler="secrets_lifecycle", delivered=0, suppressed=120, total=123),
            _row(origin_butler="home", delivered=10, suppressed=2, deferred=0, total=12),
        ]
    )

    result = await _query_ledger_summary(
        pool,
        since=datetime(2026, 7, 1, tzinfo=UTC),
        until=None,
        intent=None,
        source=None,
        origin_butler=None,
    )

    by_name = {s.origin_butler: s for s in result.by_source}
    assert by_name["secrets_lifecycle"].suppressed_never_delivered is True
    assert by_name["home"].suppressed_never_delivered is False
    assert result.flagged_sources == ["secrets_lifecycle"]


async def test_summary_source_with_no_suppression_is_not_flagged():
    from datetime import datetime

    from butlers.api.routers.attention_ledger import _query_ledger_summary

    pool = AsyncMock()
    pool.fetch = AsyncMock(
        return_value=[_row(origin_butler="finance", delivered=5, suppressed=0, total=8)]
    )

    result = await _query_ledger_summary(
        pool,
        since=datetime(2026, 7, 1, tzinfo=UTC),
        until=None,
        intent=None,
        source=None,
        origin_butler=None,
    )

    assert result.by_source[0].suppressed_never_delivered is False
    assert result.flagged_sources == []


async def test_summary_counts_failed_outcome_separately_from_deferred(app):
    """bu-hmdqz.3: 'failed' is its own counted column, distinct from 'deferred'."""
    from datetime import datetime

    from butlers.api.routers.attention_ledger import _query_ledger_summary

    pool = AsyncMock()
    pool.fetch = AsyncMock(
        return_value=[
            _row(origin_butler="secrets_lifecycle", delivered=0, deferred=0, failed=21, total=21),
        ]
    )

    result = await _query_ledger_summary(
        pool,
        since=datetime(2026, 7, 1, tzinfo=UTC),
        until=None,
        intent=None,
        source=None,
        origin_butler=None,
    )

    assert result.by_source[0].failed == 21
    assert result.by_source[0].deferred == 0
    fetch_sql = pool.fetch.call_args_list[0].args[0]
    assert "outcome = 'failed'" in fetch_sql


async def test_summary_endpoint_end_to_end_surfaces_flagged_source(app):
    """The full HTTP round trip surfaces the flag, not just the pure helper."""
    mock_db, pool = _make_available_db()
    pool.fetch = AsyncMock(
        return_value=[
            _row(origin_butler="secrets_lifecycle", delivered=0, suppressed=120, total=123)
        ]
    )
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/attention/ledger/summary")

    assert resp.status_code == 200
    body = resp.json()
    assert body["flagged_sources"] == ["secrets_lifecycle"]
    assert body["by_source"][0]["suppressed_never_delivered"] is True
