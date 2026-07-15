"""Tests for the occupation-block inference adapter (bu-whhll.10)."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import asyncpg
import pytest

from butlers.chronicler.adapters.occupation import (
    ALL_DAY_MIN_HOURS,
    EPISODE_TYPE_OCCUPATION,
    SOURCE_NAME,
    OccupationInferredAdapter,
)
from butlers.chronicler.models import Confidence, Layer, Precision, Routine, RoutineOrigin


class _AsyncCtx:
    def __init__(self, obj: object) -> None:
        self._obj = obj

    async def __aenter__(self) -> object:
        return self._obj

    async def __aexit__(self, *_: object) -> None:
        pass


class _FakeConn:
    """A fake connection whose fetch/fetchval calls are answered in order."""

    def __init__(
        self,
        fetch_results: list[list[dict[str, object]]] | None = None,
        fetchval_results: list[bool] | None = None,
    ) -> None:
        self._fetch_results = list(fetch_results or [])
        self._fetchval_results = list(fetchval_results or [])
        self.fetch_calls: list[tuple] = []
        self.fetchval_calls: list[tuple] = []

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        self.fetch_calls.append((query, args))
        if not self._fetch_results:
            return []
        return self._fetch_results.pop(0)

    async def fetchval(self, query: str, *args: object) -> bool:
        self.fetchval_calls.append((query, args))
        if not self._fetchval_results:
            return False
        return self._fetchval_results.pop(0)


def _chronicler_pool(conn: _FakeConn) -> AsyncMock:
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


def _routine(
    *,
    dow_mask: int = 0b0011111,  # Mon-Fri
    window_start_local: time = time(9, 30),
    window_end_local: time = time(19, 30),
    timezone: str = "Asia/Singapore",
    label: str = "Mon-Fri 09:30-19:30",
) -> Routine:
    return Routine(
        dow_mask=dow_mask,
        window_start_local=window_start_local,
        window_end_local=window_end_local,
        label=label,
        timezone=timezone,
        support_count=10,
        confidence=0.9,
        evidence_summary={},
        origin=RoutineOrigin.MINED,
        enabled=True,
        id=uuid4(),
    )


# ── _maybe_project ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_maybe_project_no_corroborator_emits_nothing() -> None:
    conn = _FakeConn(fetch_results=[[], [], []])  # spotify, SSID, owner-outbound: none
    pool = _chronicler_pool(conn)
    adapter = OccupationInferredAdapter()
    routine = _routine()
    start_at = datetime(2026, 7, 6, 1, 30, tzinfo=UTC)
    end_at = datetime(2026, 7, 6, 11, 30, tzinfo=UTC)

    with patch("butlers.chronicler.adapters.occupation.upsert_episode") as mock_upsert:
        episode = await adapter._maybe_project(
            pool, routine, date(2026, 7, 6), start_at, end_at, entity_id=None
        )

    assert episode is None
    mock_upsert.assert_not_called()
    # Short-circuits before any contradictor fetchval query.
    assert conn.fetchval_calls == []


@pytest.mark.asyncio
async def test_maybe_project_corroborated_no_contradictor_emits_episode() -> None:
    spotify_id = uuid4()
    conn = _FakeConn(
        fetch_results=[[{"id": spotify_id}], [], []],  # spotify hit, SSID/outbound none
        fetchval_results=[False, False, False],  # movement, gaming, all-day calendar
    )
    pool = _chronicler_pool(conn)
    adapter = OccupationInferredAdapter()
    routine = _routine()
    start_at = datetime(2026, 7, 6, 1, 30, tzinfo=UTC)
    end_at = datetime(2026, 7, 6, 11, 30, tzinfo=UTC)

    upserted: list[object] = []

    async def _fake_upsert(conn_arg: object, episode: object) -> object:
        upserted.append(episode)
        episode.id = uuid4()
        return episode

    with (
        patch("butlers.chronicler.adapters.occupation.upsert_episode", side_effect=_fake_upsert),
        patch(
            "butlers.chronicler.adapters.occupation.upsert_owner_episode_entity",
            new=AsyncMock(),
        ),
    ):
        episode = await adapter._maybe_project(
            pool, routine, date(2026, 7, 6), start_at, end_at, entity_id=None
        )

    assert episode is not None
    assert upserted[0].source_name == SOURCE_NAME
    assert upserted[0].source_ref == f"chronicler.routines:{routine.id}:2026-07-06"
    assert upserted[0].episode_type == EPISODE_TYPE_OCCUPATION
    assert upserted[0].layer == Layer.ACTIVITY
    assert upserted[0].confidence == Confidence.LOW
    assert upserted[0].precision == Precision.HOUR
    assert upserted[0].evidence_refs == [str(spotify_id)]
    assert upserted[0].start_at == start_at
    assert upserted[0].end_at == end_at


@pytest.mark.asyncio
async def test_maybe_project_movement_contradictor_suppresses_episode() -> None:
    spotify_id = uuid4()
    conn = _FakeConn(
        fetch_results=[[{"id": spotify_id}], [], []],
        fetchval_results=[True],  # movement contradictor present
    )
    pool = _chronicler_pool(conn)
    adapter = OccupationInferredAdapter()
    routine = _routine()
    start_at = datetime(2026, 7, 6, 1, 30, tzinfo=UTC)
    end_at = datetime(2026, 7, 6, 11, 30, tzinfo=UTC)

    with patch("butlers.chronicler.adapters.occupation.upsert_episode") as mock_upsert:
        episode = await adapter._maybe_project(
            pool, routine, date(2026, 7, 6), start_at, end_at, entity_id=None
        )

    assert episode is None
    mock_upsert.assert_not_called()
    assert len(conn.fetchval_calls) == 1


@pytest.mark.asyncio
async def test_maybe_project_gaming_contradictor_suppresses_episode() -> None:
    spotify_id = uuid4()
    conn = _FakeConn(
        fetch_results=[[{"id": spotify_id}], [], []],
        fetchval_results=[False, True],  # movement clear, gaming contradictor present
    )
    pool = _chronicler_pool(conn)
    adapter = OccupationInferredAdapter()
    routine = _routine()
    start_at = datetime(2026, 7, 6, 1, 30, tzinfo=UTC)
    end_at = datetime(2026, 7, 6, 11, 30, tzinfo=UTC)

    with patch("butlers.chronicler.adapters.occupation.upsert_episode") as mock_upsert:
        episode = await adapter._maybe_project(
            pool, routine, date(2026, 7, 6), start_at, end_at, entity_id=None
        )

    assert episode is None
    mock_upsert.assert_not_called()
    assert len(conn.fetchval_calls) == 2


@pytest.mark.asyncio
async def test_maybe_project_all_day_calendar_contradictor_suppresses_episode() -> None:
    spotify_id = uuid4()
    conn = _FakeConn(
        fetch_results=[[{"id": spotify_id}], [], []],
        fetchval_results=[False, False, True],  # only the all-day calendar check trips
    )
    pool = _chronicler_pool(conn)
    adapter = OccupationInferredAdapter()
    routine = _routine()
    start_at = datetime(2026, 7, 6, 1, 30, tzinfo=UTC)
    end_at = datetime(2026, 7, 6, 11, 30, tzinfo=UTC)

    with patch("butlers.chronicler.adapters.occupation.upsert_episode") as mock_upsert:
        episode = await adapter._maybe_project(
            pool, routine, date(2026, 7, 6), start_at, end_at, entity_id=None
        )

    assert episode is None
    mock_upsert.assert_not_called()
    # Confirm the ALL_DAY_MIN_HOURS threshold was passed through to the query.
    last_query, last_args = conn.fetchval_calls[-1]
    assert "make_interval" in last_query
    assert last_args[-1] == ALL_DAY_MIN_HOURS


@pytest.mark.asyncio
async def test_maybe_project_owner_outbound_alone_corroborates() -> None:
    """Owner-outbound point events alone (no Spotify) are a sufficient corroborator."""
    outbound_id = uuid4()
    conn = _FakeConn(
        fetch_results=[[], [], [{"id": outbound_id}]],  # spotify/SSID none, outbound hit
        fetchval_results=[False, False, False],
    )
    pool = _chronicler_pool(conn)
    adapter = OccupationInferredAdapter()
    routine = _routine()
    start_at = datetime(2026, 7, 6, 1, 30, tzinfo=UTC)
    end_at = datetime(2026, 7, 6, 11, 30, tzinfo=UTC)

    upserted: list[object] = []

    async def _fake_upsert(conn_arg: object, episode: object) -> object:
        upserted.append(episode)
        episode.id = uuid4()
        return episode

    with (
        patch("butlers.chronicler.adapters.occupation.upsert_episode", side_effect=_fake_upsert),
        patch(
            "butlers.chronicler.adapters.occupation.upsert_owner_episode_entity",
            new=AsyncMock(),
        ),
    ):
        episode = await adapter._maybe_project(
            pool, routine, date(2026, 7, 6), start_at, end_at, entity_id=None
        )

    assert episode is not None
    assert upserted[0].evidence_refs == [str(outbound_id)]


@pytest.mark.asyncio
async def test_maybe_project_office_ssid_presence_alone_corroborates() -> None:
    ssid_episode_id = uuid4()
    conn = _FakeConn(
        fetch_results=[[], [{"id": ssid_episode_id}], []],
        fetchval_results=[False, False, False],
    )
    pool = _chronicler_pool(conn)
    adapter = OccupationInferredAdapter()
    routine = _routine()
    start_at = datetime(2026, 7, 6, 1, 30, tzinfo=UTC)
    end_at = datetime(2026, 7, 6, 11, 30, tzinfo=UTC)

    async def _fake_upsert(conn_arg: object, episode: object) -> object:
        episode.id = uuid4()
        return episode

    with (
        patch("butlers.chronicler.adapters.occupation.upsert_episode", side_effect=_fake_upsert),
        patch(
            "butlers.chronicler.adapters.occupation.upsert_owner_episode_entity",
            new=AsyncMock(),
        ),
    ):
        episode = await adapter._maybe_project(
            pool, routine, date(2026, 7, 6), start_at, end_at, entity_id=None
        )

    assert episode is not None
    assert episode.evidence_refs == [str(ssid_episode_id)]


# ── project() orchestration ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_project_skips_when_routines_table_missing() -> None:
    adapter = OccupationInferredAdapter()
    pool = AsyncMock()
    chronicler_pool = AsyncMock()

    with patch(
        "butlers.chronicler.adapters.occupation.list_routines",
        new=AsyncMock(side_effect=asyncpg.UndefinedTableError("no such table")),
    ):
        result = await adapter.project(pool, chronicler_pool=chronicler_pool, since=None)

    assert result.skipped is True
    assert result.skipped_reason == "chronicler.routines not found"


@pytest.mark.asyncio
async def test_project_no_enabled_routines_is_a_noop() -> None:
    adapter = OccupationInferredAdapter()
    pool = AsyncMock()
    chronicler_pool = AsyncMock()

    with patch(
        "butlers.chronicler.adapters.occupation.list_routines", new=AsyncMock(return_value=[])
    ):
        result = await adapter.project(pool, chronicler_pool=chronicler_pool, since=None)

    assert result.skipped is False
    assert result.rows_projected == 0


@pytest.mark.asyncio
async def test_project_iterates_only_matching_weekday_dates_in_lookback_window() -> None:
    """dow_mask=Monday-only over a 14-day lookback should visit exactly 2 Mondays."""
    routine = _routine(dow_mask=0b0000001)  # Monday only (bit 0)
    adapter = OccupationInferredAdapter()
    pool = AsyncMock()
    chronicler_pool = AsyncMock()

    fixed_now = datetime(2026, 7, 6, 3, 0, tzinfo=UTC)  # Monday, 11:00 SGT
    seen_dates: list[date] = []

    async def _fake_maybe_project(
        chronicler_pool_arg, routine_arg, local_date, start_at, end_at, *, entity_id=None
    ):
        seen_dates.append(local_date)
        return None

    with (
        patch(
            "butlers.chronicler.adapters.occupation.list_routines",
            new=AsyncMock(return_value=[routine]),
        ),
        patch(
            "butlers.chronicler.adapters.occupation.resolve_owner_entity_id",
            new=AsyncMock(return_value=None),
        ),
        patch("butlers.chronicler.adapters.occupation._now", return_value=fixed_now),
        patch.object(adapter, "_maybe_project", side_effect=_fake_maybe_project),
    ):
        result = await adapter.project(pool, chronicler_pool=chronicler_pool, since=None)

    assert seen_dates == [date(2026, 6, 22), date(2026, 6, 29)]
    assert result.rows_projected == 0  # _maybe_project stubbed to return None
    assert result.watermark == fixed_now


@pytest.mark.asyncio
async def test_project_counts_projected_episodes() -> None:
    routine = _routine(dow_mask=0b0000001)  # Monday only
    adapter = OccupationInferredAdapter()
    pool = AsyncMock()
    chronicler_pool = AsyncMock()

    fixed_now = datetime(2026, 7, 6, 3, 0, tzinfo=UTC)

    async def _fake_maybe_project(
        chronicler_pool_arg, routine_arg, local_date, start_at, end_at, *, entity_id=None
    ):
        return object()  # truthy stand-in for a projected Episode

    with (
        patch(
            "butlers.chronicler.adapters.occupation.list_routines",
            new=AsyncMock(return_value=[routine]),
        ),
        patch(
            "butlers.chronicler.adapters.occupation.resolve_owner_entity_id",
            new=AsyncMock(return_value=None),
        ),
        patch("butlers.chronicler.adapters.occupation._now", return_value=fixed_now),
        patch.object(adapter, "_maybe_project", side_effect=_fake_maybe_project),
    ):
        result = await adapter.project(pool, chronicler_pool=chronicler_pool, since=None)

    assert result.rows_projected == 2
    assert result.episodes_closed == 2
