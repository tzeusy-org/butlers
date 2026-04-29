"""Tests for the CoreSessionsAdapter trigger_source filter.

Covers:
- EXCLUDED_TRIGGER_SOURCES constant contains expected values.
- EXCLUDED_TRIGGER_SOURCE_PREFIX is 'schedule:'.
- SQL filter is present in the WHERE clause for since=None and since-set branches.
- Only non-excluded trigger_source rows are projected (route, trigger, None).
- tick, qa, healing, schedule:foo rows are excluded at the SQL layer.
- Watermark advances correctly across filtered rows (watermark math uses only
  included rows returned by the query, not the raw unfiltered table).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.chronicler.adapters.sessions import (
    EXCLUDED_TRIGGER_SOURCE_PREFIX,
    EXCLUDED_TRIGGER_SOURCES,
    SOURCE_NAME,
    CoreSessionsAdapter,
)
from butlers.chronicler.models import Episode, PointEvent

_NOW = datetime(2026, 4, 29, 10, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(
    session_id: int = 1,
    started_at: datetime = _NOW,
    completed_at: datetime | None = None,
    trigger_source: str | None = "route",
    success: bool = True,
    duration_ms: int | None = None,
    model: str = "claude-sonnet-4-6",
) -> dict:
    return {
        "id": session_id,
        "started_at": started_at,
        "completed_at": completed_at,
        "trigger_source": trigger_source,
        "success": success,
        "request_id": None,
        "duration_ms": duration_ms,
        "model": model,
    }


def _make_mock_row(r: dict) -> MagicMock:
    return MagicMock(**r, **{"__getitem__": lambda s, k, _r=r: _r[k]})


class _AsyncCtx:
    def __init__(self, obj: object) -> None:
        self._obj = obj

    async def __aenter__(self) -> object:
        return self._obj

    async def __aexit__(self, *_: object) -> None:
        pass


class _NullCtx:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_: object) -> None:
        pass


def _pool_returning(*rows: dict) -> AsyncMock:
    """Return a mock asyncpg pool whose fetch() returns the given rows."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)  # table-exists check
    conn.fetch = AsyncMock(return_value=[_make_mock_row(r) for r in rows])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


def _pool_table_missing() -> AsyncMock:
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=False)
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


def _chronicler_pool() -> AsyncMock:
    """Minimal chronicler pool that accepts upsert calls."""
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=_NullCtx())
    conn.fetchrow = AsyncMock(return_value=None)
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


def _adapter(*schemas: str) -> CoreSessionsAdapter:
    return CoreSessionsAdapter(butler_schemas=tuple(schemas) or ("mybutler",))


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_excluded_trigger_sources_contains_expected_values() -> None:
    assert "tick" in EXCLUDED_TRIGGER_SOURCES
    assert "qa" in EXCLUDED_TRIGGER_SOURCES
    assert "healing" in EXCLUDED_TRIGGER_SOURCES


def test_excluded_trigger_sources_is_frozenset() -> None:
    assert isinstance(EXCLUDED_TRIGGER_SOURCES, frozenset)


def test_excluded_trigger_source_prefix_is_schedule() -> None:
    assert EXCLUDED_TRIGGER_SOURCE_PREFIX == "schedule:"


# ---------------------------------------------------------------------------
# SQL filter present in WHERE clause — since=None branch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_filter_in_sql_since_none() -> None:
    """When since=None, the fetch query must exclude operational trigger sources."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = _adapter("mybutler")
    cp = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.sessions.get_checkpoint_subsource", return_value=None),
        patch("butlers.chronicler.adapters.sessions.upsert_checkpoint_subsource"),
        patch("butlers.chronicler.adapters.sessions.upsert_point_event"),
        patch("butlers.chronicler.adapters.sessions.upsert_episode"),
        patch("butlers.chronicler.adapters.sessions.link_event_to_episode"),
    ):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    assert conn.fetch.await_count == 1
    query: str = conn.fetch.call_args.args[0]
    assert "trigger_source" in query
    assert "!= ALL" in query
    assert "NOT LIKE" in query


@pytest.mark.asyncio
async def test_filter_in_sql_since_set() -> None:
    """When since is set, the fetch query must exclude operational trigger sources."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = _adapter("mybutler")
    cp = _chronicler_pool()
    since = _NOW - timedelta(hours=1)

    with (
        patch("butlers.chronicler.adapters.sessions.get_checkpoint_subsource", return_value=None),
        patch("butlers.chronicler.adapters.sessions.upsert_checkpoint_subsource"),
        patch("butlers.chronicler.adapters.sessions.upsert_point_event"),
        patch("butlers.chronicler.adapters.sessions.upsert_episode"),
        patch("butlers.chronicler.adapters.sessions.link_event_to_episode"),
    ):
        await adapter.project(pool, chronicler_pool=cp, since=since)

    assert conn.fetch.await_count == 1
    query: str = conn.fetch.call_args.args[0]
    assert "trigger_source" in query
    assert "!= ALL" in query
    assert "NOT LIKE" in query


# ---------------------------------------------------------------------------
# Only user-visible rows are projected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_only_user_rows_projected() -> None:
    """Only rows with non-excluded trigger_source values produce episodes.

    Seeds one row each for: tick, qa, healing, schedule:foo (excluded)
    and route, trigger, None (included). The mock pool returns only the
    included rows — matching what the SQL filter at the DB layer would do.
    Only the included rows should be projected.
    """
    t_base = _NOW

    # These are the rows the DB would return after the SQL filter.
    included_rows = [
        _make_row(session_id=10, started_at=t_base, trigger_source="route"),
        _make_row(
            session_id=11, started_at=t_base + timedelta(minutes=1), trigger_source="trigger"
        ),
        _make_row(session_id=12, started_at=t_base + timedelta(minutes=2), trigger_source=None),
    ]

    pool = _pool_returning(*included_rows)
    cp = _chronicler_pool()
    adapter = _adapter("mybutler")

    projected_source_refs: list[str] = []

    async def _fake_upsert_episode(conn: object, episode: Episode) -> Episode:
        projected_source_refs.append(episode.source_ref)
        ep = Episode(**{**episode.__dict__, "id": uuid.uuid4()})
        return ep

    async def _fake_upsert_point_event(conn: object, event: PointEvent) -> PointEvent:
        # _project_row asserts that returned event.id is not None.
        return PointEvent(**{**event.__dict__, "id": uuid.uuid4()})

    with (
        patch("butlers.chronicler.adapters.sessions.get_checkpoint_subsource", return_value=None),
        patch("butlers.chronicler.adapters.sessions.upsert_checkpoint_subsource"),
        patch(
            "butlers.chronicler.adapters.sessions.upsert_episode",
            side_effect=_fake_upsert_episode,
        ),
        patch(
            "butlers.chronicler.adapters.sessions.upsert_point_event",
            side_effect=_fake_upsert_point_event,
        ),
        patch("butlers.chronicler.adapters.sessions.link_event_to_episode"),
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.rows_projected == 3
    # Each included row produces exactly one episode.
    assert len(projected_source_refs) == 3
    assert all("mybutler.sessions:" in ref for ref in projected_source_refs)


@pytest.mark.asyncio
async def test_excluded_rows_not_projected() -> None:
    """Rows with operational trigger_source values must not produce episodes.

    The mock pool simulates the SQL filter having been applied: it returns
    zero rows when only excluded sources are present, matching the expected
    DB behaviour.
    """
    # Pool returns no rows — as if the SQL filter excluded all of them.
    pool = _pool_returning()
    cp = _chronicler_pool()
    adapter = _adapter("mybutler")

    with (
        patch("butlers.chronicler.adapters.sessions.get_checkpoint_subsource", return_value=None),
        patch("butlers.chronicler.adapters.sessions.upsert_checkpoint_subsource"),
        patch("butlers.chronicler.adapters.sessions.upsert_episode") as mock_upsert_ep,
        patch("butlers.chronicler.adapters.sessions.upsert_point_event") as mock_upsert_pe,
        patch("butlers.chronicler.adapters.sessions.link_event_to_episode"),
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.rows_projected == 0
    mock_upsert_ep.assert_not_called()
    mock_upsert_pe.assert_not_called()


# ---------------------------------------------------------------------------
# SQL exclusion parameters are passed correctly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exclusion_array_passed_as_parameter_since_none() -> None:
    """The excluded-source list is passed as a query parameter (not inlined)."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = _adapter("mybutler")
    cp = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.sessions.get_checkpoint_subsource", return_value=None),
        patch("butlers.chronicler.adapters.sessions.upsert_checkpoint_subsource"),
        patch("butlers.chronicler.adapters.sessions.upsert_episode"),
        patch("butlers.chronicler.adapters.sessions.upsert_point_event"),
        patch("butlers.chronicler.adapters.sessions.link_event_to_episode"),
    ):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    call_args = conn.fetch.call_args
    # Args: (query, batch_limit, excluded_exact_list, excluded_prefix_pattern)
    excluded_list = call_args.args[2]
    prefix_pattern = call_args.args[3]

    assert set(excluded_list) == EXCLUDED_TRIGGER_SOURCES
    assert prefix_pattern == EXCLUDED_TRIGGER_SOURCE_PREFIX + "%"


@pytest.mark.asyncio
async def test_exclusion_array_passed_as_parameter_since_set() -> None:
    """The excluded-source list and prefix pattern are passed as query parameters."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = _adapter("mybutler")
    cp = _chronicler_pool()
    since = _NOW - timedelta(hours=2)

    with (
        patch("butlers.chronicler.adapters.sessions.get_checkpoint_subsource", return_value=None),
        patch("butlers.chronicler.adapters.sessions.upsert_checkpoint_subsource"),
        patch("butlers.chronicler.adapters.sessions.upsert_episode"),
        patch("butlers.chronicler.adapters.sessions.upsert_point_event"),
        patch("butlers.chronicler.adapters.sessions.link_event_to_episode"),
    ):
        await adapter.project(pool, chronicler_pool=cp, since=since)

    call_args = conn.fetch.call_args
    # Args: (query, since, batch_limit, excluded_exact_list, excluded_prefix_pattern)
    assert call_args.args[1] == since
    excluded_list = call_args.args[3]
    prefix_pattern = call_args.args[4]

    assert set(excluded_list) == EXCLUDED_TRIGGER_SOURCES
    assert prefix_pattern == EXCLUDED_TRIGGER_SOURCE_PREFIX + "%"


# ---------------------------------------------------------------------------
# Watermark advances correctly over filtered (included) rows only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watermark_advances_over_included_rows_only() -> None:
    """Watermark reflects only rows returned by the SQL-filtered query.

    The SQL filter excludes operational rows before they reach Python, so
    the watermark math here operates on the already-filtered result set.
    This test verifies that the max started_at of the included rows becomes
    the schema watermark.
    """
    t1 = _NOW
    t2 = _NOW + timedelta(hours=1)

    included_rows = [
        _make_row(session_id=20, started_at=t1, trigger_source="route"),
        _make_row(session_id=21, started_at=t2, trigger_source="trigger"),
    ]

    pool = _pool_returning(*included_rows)
    cp = _chronicler_pool()
    adapter = _adapter("mybutler")

    async def _fake_upsert_episode(conn: object, episode: Episode) -> Episode:
        return Episode(**{**episode.__dict__, "id": uuid.uuid4()})

    async def _fake_upsert_point_event(conn: object, event: PointEvent) -> PointEvent:
        return PointEvent(**{**event.__dict__, "id": uuid.uuid4()})

    with (
        patch("butlers.chronicler.adapters.sessions.get_checkpoint_subsource", return_value=None),
        patch("butlers.chronicler.adapters.sessions.upsert_checkpoint_subsource"),
        patch(
            "butlers.chronicler.adapters.sessions.upsert_episode",
            side_effect=_fake_upsert_episode,
        ),
        patch(
            "butlers.chronicler.adapters.sessions.upsert_point_event",
            side_effect=_fake_upsert_point_event,
        ),
        patch("butlers.chronicler.adapters.sessions.link_event_to_episode"),
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.watermark == t2


@pytest.mark.asyncio
async def test_source_name_constant() -> None:
    assert SOURCE_NAME == "core.sessions"
