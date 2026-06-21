"""Tests for owner entity_id population across owner-only Chronicler adapters.

Covers:
- resolve_owner_entity_id: returns UUID on success, None on missing table,
  None on missing owner row, None on NULL entity_id, None on unexpected type.
- upsert_owner_episode_entity: executes INSERT, skips on None owner_id,
  skips on None episode_id.
- Per-adapter unit tests: mock resolve_owner_entity_id, exercise project(),
  assert upsert_owner_episode_entity was called with the resolved owner id
  (the owner lives in the episode_entities join table, not on the episode).
- No-owner fallback: mock owner returns None, assert project() still completes
  and projects the episode (does not raise).
- Adapters covered: FocusInferredAdapter, CoreSessionsAdapter,
  SpotifySessionAdapter, SteamPlayAdapter, OwnTracksPointAdapter (movement),
  ReadingInferredAdapter, GoogleHealthSleepAdapter, GoogleHealthWorkoutAdapter.
- PointEvent adapters (bu-kihe8): MealsAdapter, GoogleHealthStepsAdapter,
  GoogleHealthHeartRateAdapter now stamp entity_id on point events.

Issue: bu-4c1ks / bu-kihe8
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import asyncpg
import pytest

from butlers.chronicler.adapters._owner_entity import (
    resolve_owner_entity_id,
    upsert_owner_episode_entity,
)
from butlers.chronicler.adapters.focus import FocusInferredAdapter
from butlers.chronicler.adapters.google_health import (
    GoogleHealthHeartRateAdapter,
    GoogleHealthSleepAdapter,
    GoogleHealthStepsAdapter,
    GoogleHealthWorkoutAdapter,
)
from butlers.chronicler.adapters.meals import MealsAdapter
from butlers.chronicler.adapters.owntracks import OwnTracksPointAdapter
from butlers.chronicler.adapters.reading import ReadingInferredAdapter
from butlers.chronicler.adapters.sessions import CoreSessionsAdapter
from butlers.chronicler.adapters.spotify import SpotifySessionAdapter
from butlers.chronicler.adapters.steam import SteamPlayAdapter
from butlers.chronicler.models import Episode, PointEvent

_NOW = datetime(2026, 5, 1, 10, 0, 0, tzinfo=UTC)
_OWNER_ENTITY_ID = uuid4()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


class _AsyncCtx:
    """Async context manager that yields obj."""

    def __init__(self, obj: object) -> None:
        self._obj = obj

    async def __aenter__(self) -> object:
        return self._obj

    async def __aexit__(self, *_: object) -> None:
        pass


def _make_pool_with_fetchrow(row_value: object) -> AsyncMock:
    """Pool whose conn.fetchrow returns row_value."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=row_value)
    conn.fetchval = AsyncMock(return_value=True)  # table-exists checks
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


def _fake_record(entity_id: UUID | str | None) -> MagicMock:
    """asyncpg.Record-like mock with a single ``id`` key.

    resolve_owner_entity_id now reads ``SELECT id FROM public.entities`` (bu-jnaa3),
    so the owner entity UUID is projected as the ``id`` column.
    """
    rec = MagicMock(spec=asyncpg.Record)
    rec.__getitem__ = MagicMock(side_effect=lambda k: entity_id if k == "id" else None)
    return rec


def _chronicler_pool_tracked() -> tuple[AsyncMock, AsyncMock]:
    """Return (pool, conn) where conn.execute calls are trackable."""
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=None)
    conn.executemany = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=_AsyncCtx(None))
    conn.fetchrow = AsyncMock(return_value=None)
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool, conn


def _chronicler_pool_simple() -> AsyncMock:
    """Minimal chronicler pool for upsert calls that do not need tracking."""
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=_AsyncCtx(None))
    conn.fetchrow = AsyncMock(return_value=None)
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


def _episode_with_id(episode_id: UUID) -> Episode:
    """Return an Episode with a stable id for testing episode_entities writes."""
    return Episode(
        id=episode_id,
        source_name="test.source",
        source_ref="test:ref",
        episode_type="test_type",
        start_at=_NOW,
        end_at=_NOW + timedelta(hours=1),
    )


# ---------------------------------------------------------------------------
# resolve_owner_entity_id — unit tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("as_string", [False, True], ids=["uuid", "string"])
async def test_resolve_owner_entity_id_returns_uuid_when_found(as_string: bool) -> None:
    """Returns the owner UUID, coercing a string-stored entity_id to UUID."""
    stored = str(_OWNER_ENTITY_ID) if as_string else _OWNER_ENTITY_ID
    pool = _make_pool_with_fetchrow(_fake_record(stored))
    result = await resolve_owner_entity_id(pool)
    assert result == _OWNER_ENTITY_ID


@pytest.mark.unit
@pytest.mark.parametrize(
    "stored_entity_id",
    [None, 42],
    ids=["entity_id_null", "unexpected_type"],
)
async def test_resolve_owner_entity_id_returns_none_for_owner_row(stored_entity_id) -> None:
    """Returns None when the owner row's entity_id is NULL or an unexpected type."""
    pool = _make_pool_with_fetchrow(_fake_record(stored_entity_id))
    result = await resolve_owner_entity_id(pool)
    assert result is None


@pytest.mark.unit
async def test_resolve_owner_entity_id_returns_none_when_no_row() -> None:
    """Returns None when no contact has role 'owner' (no row)."""
    pool = _make_pool_with_fetchrow(None)
    result = await resolve_owner_entity_id(pool)
    assert result is None


@pytest.mark.unit
async def test_resolve_owner_entity_id_returns_none_on_db_error() -> None:
    """Returns None (and logs DEBUG) when the DB query raises PostgresError."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(side_effect=asyncpg.PostgresError())
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    result = await resolve_owner_entity_id(pool)
    assert result is None


# ---------------------------------------------------------------------------
# upsert_owner_episode_entity — unit tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_upsert_owner_episode_entity_executes_insert() -> None:
    """Executes INSERT INTO episode_entities when both IDs are provided."""
    conn = AsyncMock()
    episode_id = uuid4()
    owner_id = uuid4()
    await upsert_owner_episode_entity(conn, episode_id, owner_id=owner_id)
    conn.execute.assert_awaited_once()
    # The episode_id and owner_id are passed through as bound params (the join-table
    # write records the owner). SQL text shape is covered structurally elsewhere.
    call_args = conn.execute.call_args
    assert call_args.args[1] == episode_id
    assert call_args.args[2] == owner_id


@pytest.mark.unit
async def test_upsert_owner_episode_entity_skips_when_owner_none() -> None:
    """Does not execute any SQL when owner_id is None."""
    conn = AsyncMock()
    await upsert_owner_episode_entity(conn, uuid4(), owner_id=None)
    conn.execute.assert_not_awaited()


@pytest.mark.unit
async def test_upsert_owner_episode_entity_skips_when_episode_id_none() -> None:
    """Does not execute any SQL when episode_id is None (mocked upsert context)."""
    conn = AsyncMock()
    await upsert_owner_episode_entity(conn, None, owner_id=uuid4())
    conn.execute.assert_not_awaited()


# ---------------------------------------------------------------------------
# Per-adapter unit tests — entity_id stamped and episode_entities written
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_focus_adapter_stamps_entity_id_on_episode() -> None:
    """FocusInferredAdapter stamps entity_id on the projected episode."""
    episode_id = uuid4()
    captured: list[Episode] = []

    async def _fake_upsert(_conn: object, ep: Episode) -> Episode:
        captured.append(ep)
        return _episode_with_id(episode_id)

    cp = _chronicler_pool_simple()
    pool = _make_pool_with_fetchrow(None)  # no owner — tests graceful None path

    # Build a minimal sessions row that triggers a focus signal.
    dur = timedelta(minutes=60)
    row = MagicMock()
    row.__getitem__ = MagicMock(
        side_effect=lambda k: {
            "id": uuid4(),
            "source_name": "core.sessions",
            "source_ref": "core.sessions:1",
            "episode_type": "work",
            "start_at": _NOW,
            "end_at": _NOW + dur,
            "title": None,
            "payload": {"trigger_source": "trigger"},
            "created_at": _NOW,
            "overlaps_route": False,
        }[k]
    )

    adapter = FocusInferredAdapter()
    with (
        patch.object(adapter, "_fetch_candidate_rows", new=AsyncMock(return_value=[row])),
        patch("butlers.chronicler.adapters.focus.upsert_episode", side_effect=_fake_upsert),
        patch(
            "butlers.chronicler.adapters.focus.resolve_owner_entity_id",
            new=AsyncMock(return_value=_OWNER_ENTITY_ID),
        ),
        patch(
            "butlers.chronicler.adapters.focus.upsert_owner_episode_entity",
            new=AsyncMock(),
        ) as mock_upsert_entity,
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    # Episode was projected.
    assert result.rows_projected == 1
    assert len(captured) == 1
    # Owner is recorded in the episode_entities join table (not on the episode).
    mock_upsert_entity.assert_awaited_once()
    call_kw = mock_upsert_entity.call_args.kwargs
    assert call_kw["owner_id"] == _OWNER_ENTITY_ID


@pytest.mark.unit
async def test_focus_adapter_no_owner_fallback() -> None:
    """FocusInferredAdapter completes with entity_id=None when no owner found."""
    episode_id = uuid4()
    captured: list[Episode] = []

    async def _fake_upsert(_conn: object, ep: Episode) -> Episode:
        captured.append(ep)
        return _episode_with_id(episode_id)

    cp = _chronicler_pool_simple()
    pool = _make_pool_with_fetchrow(None)

    dur = timedelta(minutes=60)
    row = MagicMock()
    row.__getitem__ = MagicMock(
        side_effect=lambda k: {
            "id": uuid4(),
            "source_name": "core.sessions",
            "source_ref": "core.sessions:1",
            "episode_type": "work",
            "start_at": _NOW,
            "end_at": _NOW + dur,
            "title": None,
            "payload": {"trigger_source": "trigger"},
            "created_at": _NOW,
            "overlaps_route": False,
        }[k]
    )

    adapter = FocusInferredAdapter()
    with (
        patch.object(adapter, "_fetch_candidate_rows", new=AsyncMock(return_value=[row])),
        patch("butlers.chronicler.adapters.focus.upsert_episode", side_effect=_fake_upsert),
        patch(
            "butlers.chronicler.adapters.focus.resolve_owner_entity_id",
            new=AsyncMock(return_value=None),  # no owner
        ),
        patch("butlers.chronicler.adapters.focus.upsert_owner_episode_entity", new=AsyncMock()),
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    # Should still project (no exception raised) even when no owner is found.
    assert result.rows_projected == 1
    assert len(captured) == 1


@pytest.mark.unit
async def test_spotify_adapter_stamps_entity_id_on_episode() -> None:
    """SpotifySessionAdapter stamps entity_id on each listening episode."""
    episode_id = uuid4()
    captured: list[Episode] = []

    async def _fake_upsert(_conn: object, ep: Episode) -> Episode:
        captured.append(ep)
        return _episode_with_id(episode_id)

    cp = _chronicler_pool_simple()
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(AsyncMock()))

    spotify_row = MagicMock()
    spotify_row.__getitem__ = MagicMock(
        side_effect=lambda k: {
            "id": uuid4(),
            "idempotency_key": "spotify:session:abc",
            "endpoint_identity": "user:client",
            "spotify_user_id": "user123",
            "started_at": _NOW,
            "ended_at": _NOW + timedelta(minutes=30),
            "duration_seconds": 1800,
            "track_count": 3,
            "track_names": ["A", "B", "C"],
            "context_uri": "spotify:playlist:abc",
            "context_name": "Chill Mix",
            "recorded_at": _NOW,
        }[k]
    )

    adapter = SpotifySessionAdapter()
    with (
        patch.object(adapter, "_fetch_sessions", new=AsyncMock(return_value=[spotify_row])),
        patch("butlers.chronicler.adapters.spotify.upsert_episode", side_effect=_fake_upsert),
        patch(
            "butlers.chronicler.adapters.spotify.resolve_owner_entity_id",
            new=AsyncMock(return_value=_OWNER_ENTITY_ID),
        ),
        patch(
            "butlers.chronicler.adapters.spotify.upsert_owner_episode_entity",
            new=AsyncMock(),
        ) as mock_upsert_entity,
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.rows_projected == 1
    mock_upsert_entity.assert_awaited_once()
    assert mock_upsert_entity.call_args.kwargs["owner_id"] == _OWNER_ENTITY_ID


@pytest.mark.unit
async def test_steam_adapter_stamps_entity_id_on_episode() -> None:
    """SteamPlayAdapter stamps entity_id on each play episode."""
    from datetime import date

    episode_id = uuid4()
    captured: list[Episode] = []

    async def _fake_upsert(_conn: object, ep: Episode) -> Episode:
        captured.append(ep)
        return _episode_with_id(episode_id)

    cp = _chronicler_pool_simple()
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(AsyncMock()))

    steam_row = MagicMock()
    steam_row.__getitem__ = MagicMock(
        side_effect=lambda k: {
            "steam_id": "12345",
            "steam_account_id": None,
            "app_id": "730",
            "app_name": "Counter-Strike 2",
            "date": date(2026, 5, 1),
            "playtime_minutes": 90,
            "recorded_at": _NOW,
        }[k]
    )

    adapter = SteamPlayAdapter()
    with (
        patch.object(adapter, "_fetch_rows", new=AsyncMock(return_value=[steam_row])),
        patch("butlers.chronicler.adapters.steam.upsert_episode", side_effect=_fake_upsert),
        patch(
            "butlers.chronicler.adapters.steam.resolve_owner_entity_id",
            new=AsyncMock(return_value=_OWNER_ENTITY_ID),
        ),
        patch(
            "butlers.chronicler.adapters.steam.upsert_owner_episode_entity",
            new=AsyncMock(),
        ) as mock_upsert_entity,
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.rows_projected == 1
    mock_upsert_entity.assert_awaited_once()
    assert mock_upsert_entity.call_args.kwargs["owner_id"] == _OWNER_ENTITY_ID


@pytest.mark.unit
async def test_owntracks_movement_episode_stamps_entity_id() -> None:
    """OwnTracksPointAdapter stamps entity_id on movement_episode rows."""
    episode_id = uuid4()
    captured: list[Episode] = []

    async def _fake_upsert(_conn: object, ep: Episode) -> Episode:
        captured.append(ep)
        return _episode_with_id(episode_id)

    cp = _chronicler_pool_simple()
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(AsyncMock()))

    point_row = {
        "id": uuid4(),
        "idempotency_key": "owntracks:abc",
        "ts": _NOW,
        "lat": 1.0,
        "lon": 2.0,
        "accuracy": 10.0,
        "trigger": None,
        "event": None,
        "endpoint_identity": "user/device",
        "raw_payload": None,
        "recorded_at": _NOW,
    }

    adapter = OwnTracksPointAdapter()
    with (
        patch.object(adapter, "_fetch_points", new=AsyncMock(return_value=[point_row])),
        patch("butlers.chronicler.adapters.owntracks.upsert_point_event", new=AsyncMock()),
        patch("butlers.chronicler.adapters.owntracks.upsert_episode", side_effect=_fake_upsert),
        patch(
            "butlers.chronicler.adapters.owntracks.get_carryover", new=AsyncMock(return_value={})
        ),
        patch("butlers.chronicler.adapters.owntracks.save_carryover", new=AsyncMock()),
        patch(
            "butlers.chronicler.adapters.owntracks.resolve_owner_entity_id",
            new=AsyncMock(return_value=_OWNER_ENTITY_ID),
        ),
        patch(
            "butlers.chronicler.adapters.owntracks.upsert_owner_episode_entity",
            new=AsyncMock(),
        ) as mock_upsert_entity,
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.rows_projected == 1
    # movement episode was captured with entity_id
    assert len(captured) == 1
    mock_upsert_entity.assert_awaited_once()
    assert mock_upsert_entity.call_args.kwargs["owner_id"] == _OWNER_ENTITY_ID


@pytest.mark.unit
async def test_reading_calendar_row_stamps_entity_id() -> None:
    """ReadingInferredAdapter stamps entity_id on calendar-derived reading blocks."""
    episode_id = uuid4()
    captured: list[Episode] = []

    async def _fake_upsert(_conn: object, ep: Episode) -> Episode:
        captured.append(ep)
        return _episode_with_id(episode_id)

    cp = _chronicler_pool_simple()
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(AsyncMock()))

    cal_row = MagicMock()
    cal_row.__getitem__ = MagicMock(
        side_effect=lambda k: {
            "id": uuid4(),
            "source_name": "google_calendar.completed",
            "source_ref": "calendar:abc",
            "episode_type": "scheduled_block",
            "start_at": _NOW,
            "end_at": _NOW + timedelta(hours=1),
            "title": "Reading: War and Peace",
            "payload": {},
            "created_at": _NOW,
        }[k]
    )

    adapter = ReadingInferredAdapter()
    with (
        patch.object(adapter, "_fetch_calendar_rows", new=AsyncMock(return_value=[cal_row])),
        patch.object(adapter, "_fetch_reading_facts", new=AsyncMock(return_value=None)),
        patch("butlers.chronicler.adapters.reading.upsert_episode", side_effect=_fake_upsert),
        patch(
            "butlers.chronicler.adapters.reading.resolve_owner_entity_id",
            new=AsyncMock(return_value=_OWNER_ENTITY_ID),
        ),
        patch(
            "butlers.chronicler.adapters.reading.upsert_owner_episode_entity",
            new=AsyncMock(),
        ) as mock_upsert_entity,
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.rows_projected == 1
    mock_upsert_entity.assert_awaited_once()
    assert mock_upsert_entity.call_args.kwargs["owner_id"] == _OWNER_ENTITY_ID


@pytest.mark.unit
async def test_google_health_sleep_adapter_stamps_entity_id() -> None:
    """GoogleHealthSleepAdapter stamps entity_id on sleep episodes."""
    episode_id = uuid4()
    captured: list[Episode] = []

    async def _fake_upsert(_conn: object, ep: Episode) -> Episode:
        captured.append(ep)
        return _episode_with_id(episode_id)

    cp = _chronicler_pool_simple()
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(AsyncMock()))

    sleep_row = MagicMock()
    sleep_row.__getitem__ = MagicMock(
        side_effect=lambda k: {
            "id": uuid4(),
            "subject": None,
            "predicate": "sleep_session",
            "content": None,
            "metadata": {"duration_ms": 28800000},
            "valid_at": _NOW,
            "created_at": _NOW,
            "idempotency_key": "google_health:sleep:session123",
        }[k]
    )

    adapter = GoogleHealthSleepAdapter()
    with (
        patch.object(adapter, "_fetch_facts", new=AsyncMock(return_value=[sleep_row])),
        patch(
            "butlers.chronicler.adapters.google_health.get_carryover",
            new=AsyncMock(return_value={}),
        ),
        patch("butlers.chronicler.adapters.google_health.save_carryover", new=AsyncMock()),
        patch(
            "butlers.chronicler.adapters.google_health.upsert_episode",
            side_effect=_fake_upsert,
        ),
        patch(
            "butlers.chronicler.adapters.google_health.resolve_owner_entity_id",
            new=AsyncMock(return_value=_OWNER_ENTITY_ID),
        ),
        patch(
            "butlers.chronicler.adapters.google_health.upsert_owner_episode_entity",
            new=AsyncMock(),
        ) as mock_upsert_entity,
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.rows_projected == 1
    mock_upsert_entity.assert_awaited_once()
    assert mock_upsert_entity.call_args.kwargs["owner_id"] == _OWNER_ENTITY_ID


@pytest.mark.unit
async def test_google_health_workout_adapter_stamps_entity_id() -> None:
    """GoogleHealthWorkoutAdapter stamps entity_id on workout episodes."""
    episode_id = uuid4()
    captured: list[Episode] = []

    async def _fake_upsert(_conn: object, ep: Episode) -> Episode:
        captured.append(ep)
        return _episode_with_id(episode_id)

    cp = _chronicler_pool_simple()
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(AsyncMock()))

    workout_row = MagicMock()
    workout_row.__getitem__ = MagicMock(
        side_effect=lambda k: {
            "id": uuid4(),
            "subject": None,
            "predicate": "workout_session",
            "content": None,
            "metadata": {"activity_type": "running", "duration_ms": 3600000},
            "valid_at": _NOW,
            "created_at": _NOW,
            "idempotency_key": "google_health:workout:abc",
        }[k]
    )

    adapter = GoogleHealthWorkoutAdapter()
    with (
        patch.object(adapter, "_fetch_workout_facts", new=AsyncMock(return_value=[workout_row])),
        patch(
            "butlers.chronicler.adapters.google_health.upsert_episode",
            side_effect=_fake_upsert,
        ),
        patch(
            "butlers.chronicler.adapters.google_health.resolve_owner_entity_id",
            new=AsyncMock(return_value=_OWNER_ENTITY_ID),
        ),
        patch(
            "butlers.chronicler.adapters.google_health.upsert_owner_episode_entity",
            new=AsyncMock(),
        ) as mock_upsert_entity,
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.rows_projected == 1
    mock_upsert_entity.assert_awaited_once()
    assert mock_upsert_entity.call_args.kwargs["owner_id"] == _OWNER_ENTITY_ID


@pytest.mark.unit
async def test_sessions_adapter_stamps_entity_id_on_work_episode() -> None:
    """CoreSessionsAdapter stamps entity_id on work episodes."""
    episode_id = uuid4()
    captured_episodes: list[Episode] = []

    async def _fake_upsert(_conn: object, ep: Episode) -> Episode:
        captured_episodes.append(ep)
        return _episode_with_id(episode_id)

    cp, conn = _chronicler_pool_tracked()
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(AsyncMock()))

    session_row = MagicMock()
    session_row.__getitem__ = MagicMock(
        side_effect=lambda k: {
            "id": 1,
            "started_at": _NOW,
            "completed_at": _NOW + timedelta(minutes=30),
            "trigger_source": "trigger",
            "success": True,
            "request_id": None,
            "ingestion_event_id": None,
            "duration_ms": 1800000,
            "model": "claude-sonnet-4-6",
        }[k]
    )

    adapter = CoreSessionsAdapter(butler_schemas=("butler_test",))
    with (
        patch.object(adapter, "_get_schema_watermark", new=AsyncMock(return_value=None)),
        patch.object(
            adapter,
            "_fetch_sessions",
            new=AsyncMock(return_value=([session_row], _NOW)),
        ),
        patch.object(adapter, "_resolve_contacts", new=AsyncMock(return_value={})),
        patch(
            "butlers.chronicler.adapters.sessions.upsert_point_event",
            new=AsyncMock(return_value=MagicMock(id=uuid4())),
        ),
        patch("butlers.chronicler.adapters.sessions.upsert_episode", side_effect=_fake_upsert),
        patch("butlers.chronicler.adapters.sessions.link_event_to_episode", new=AsyncMock()),
        patch("butlers.chronicler.adapters.sessions.upsert_checkpoint_subsource", new=AsyncMock()),
        patch(
            "butlers.chronicler.adapters.sessions.resolve_owner_entity_id",
            new=AsyncMock(return_value=_OWNER_ENTITY_ID),
        ),
        patch(
            "butlers.chronicler.adapters.sessions.upsert_owner_episode_entity",
            new=AsyncMock(),
        ) as mock_upsert_entity,
    ):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    # Work episode was projected; owner goes into the episode_entities join table.
    work_episodes = [e for e in captured_episodes if e.episode_type == "work"]
    assert len(work_episodes) == 1
    mock_upsert_entity.assert_awaited()
    assert mock_upsert_entity.call_args.kwargs["owner_id"] == _OWNER_ENTITY_ID


# ---------------------------------------------------------------------------
# PointEvent adapters — entity_id stamped on point events (bu-kihe8)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_meals_adapter_stamps_entity_id_on_point_event() -> None:
    """MealsAdapter stamps entity_id on each eating_event point event."""
    import uuid as _uuid

    captured: list[PointEvent] = []

    async def _fake_upsert(_conn: object, ev: PointEvent) -> PointEvent:
        captured.append(ev)
        return ev

    cp = _chronicler_pool_simple()
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(AsyncMock()))

    meal_row = MagicMock()
    meal_row.__getitem__ = MagicMock(
        side_effect=lambda k: {
            "id": str(_uuid.uuid4()),
            "type": "lunch",
            "description": "Grilled chicken",
            "nutrition": None,
            "eaten_at": _NOW,
            "notes": None,
            "seq": 1,
        }[k]
    )

    adapter = MealsAdapter()
    with (
        patch.object(adapter, "_fetch_meals", new=AsyncMock(return_value=[meal_row])),
        patch("butlers.chronicler.adapters.meals.upsert_point_event", side_effect=_fake_upsert),
        patch(
            "butlers.chronicler.adapters.meals.resolve_owner_entity_id",
            new=AsyncMock(return_value=_OWNER_ENTITY_ID),
        ),
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.rows_projected == 1
    assert len(captured) == 1
    assert captured[0].entity_id == _OWNER_ENTITY_ID


@pytest.mark.unit
async def test_meals_adapter_no_owner_fallback_for_point_event() -> None:
    """MealsAdapter still projects with entity_id=None when no owner found."""
    import uuid as _uuid

    captured: list[PointEvent] = []

    async def _fake_upsert(_conn: object, ev: PointEvent) -> PointEvent:
        captured.append(ev)
        return ev

    cp = _chronicler_pool_simple()
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(AsyncMock()))

    meal_row = MagicMock()
    meal_row.__getitem__ = MagicMock(
        side_effect=lambda k: {
            "id": str(_uuid.uuid4()),
            "type": "breakfast",
            "description": "Oatmeal",
            "nutrition": None,
            "eaten_at": _NOW,
            "notes": None,
            "seq": 1,
        }[k]
    )

    adapter = MealsAdapter()
    with (
        patch.object(adapter, "_fetch_meals", new=AsyncMock(return_value=[meal_row])),
        patch("butlers.chronicler.adapters.meals.upsert_point_event", side_effect=_fake_upsert),
        patch(
            "butlers.chronicler.adapters.meals.resolve_owner_entity_id",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.rows_projected == 1
    assert captured[0].entity_id is None


@pytest.mark.unit
async def test_steps_adapter_stamps_entity_id_on_point_event() -> None:
    """GoogleHealthStepsAdapter stamps entity_id on each daily_steps point event."""
    captured: list[PointEvent] = []

    async def _fake_upsert(_conn: object, ev: PointEvent) -> PointEvent:
        captured.append(ev)
        return ev

    cp = _chronicler_pool_simple()
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(AsyncMock()))

    steps_row = MagicMock()
    steps_row.__getitem__ = MagicMock(
        side_effect=lambda k: {
            "id": uuid4(),
            "subject": None,
            "predicate": "daily_steps",
            "content": None,
            "metadata": {"value": 8500},
            "valid_at": _NOW,
            "created_at": _NOW,
            "idempotency_key": "health:steps:2026-05-01",
        }[k]
    )

    adapter = GoogleHealthStepsAdapter()
    with (
        patch(
            "butlers.chronicler.adapters.google_health._fetch_fact_rows",
            new=AsyncMock(return_value=[steps_row]),
        ),
        patch(
            "butlers.chronicler.adapters.google_health.upsert_point_event",
            side_effect=_fake_upsert,
        ),
        patch(
            "butlers.chronicler.adapters.google_health.resolve_owner_entity_id",
            new=AsyncMock(return_value=_OWNER_ENTITY_ID),
        ),
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.rows_projected == 1
    assert len(captured) == 1
    assert captured[0].entity_id == _OWNER_ENTITY_ID


@pytest.mark.unit
async def test_heart_rate_adapter_stamps_entity_id_on_point_event() -> None:
    """GoogleHealthHeartRateAdapter stamps entity_id on heart_rate_summary events."""
    captured: list[PointEvent] = []

    async def _fake_upsert(_conn: object, ev: PointEvent) -> PointEvent:
        captured.append(ev)
        return ev

    cp = _chronicler_pool_simple()
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(AsyncMock()))

    hr_row = MagicMock()
    hr_row.__getitem__ = MagicMock(
        side_effect=lambda k: {
            "id": uuid4(),
            "subject": None,
            "predicate": "measurement_resting_hr",
            "content": None,
            "metadata": {"resting_hr": 58},
            "valid_at": _NOW,
            "created_at": _NOW,
            "idempotency_key": "health:hr:2026-05-01",
        }[k]
    )

    adapter = GoogleHealthHeartRateAdapter()
    with (
        patch(
            "butlers.chronicler.adapters.google_health._fetch_fact_rows",
            new=AsyncMock(return_value=[hr_row]),
        ),
        patch(
            "butlers.chronicler.adapters.google_health.upsert_point_event",
            side_effect=_fake_upsert,
        ),
        patch(
            "butlers.chronicler.adapters.google_health.resolve_owner_entity_id",
            new=AsyncMock(return_value=_OWNER_ENTITY_ID),
        ),
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.rows_projected == 1
    assert len(captured) == 1
    assert captured[0].entity_id == _OWNER_ENTITY_ID
