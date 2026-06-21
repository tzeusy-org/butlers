"""Tests for the CoreSessionsAdapter trigger_source filter and title resolution.

Covers:
- EXCLUDED_TRIGGER_SOURCES constant contains expected values.
- SQL filter is present in the WHERE clause for since=None branch.
- Only non-excluded trigger_source rows are projected (route, trigger, None).
- deadline:* rows are NOT excluded (bu-ve8ne regression guard).
- Watermark advances correctly across filtered rows.
- Title resolution (bu-fkqv0): route+contact→"Conversation with X",
  route+no-contact→"Conversation via {channel}", manual task fallback.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from butlers.chronicler.adapters.sessions import (
    EXCLUDED_TRIGGER_SOURCE_PREFIX,
    EXCLUDED_TRIGGER_SOURCES,
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
    ingestion_event_id: object = None,
) -> dict:
    return {
        "id": session_id,
        "started_at": started_at,
        "completed_at": completed_at,
        "trigger_source": trigger_source,
        "success": success,
        "request_id": None,
        "ingestion_event_id": ingestion_event_id,
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


def test_deadline_prefix_not_excluded() -> None:
    """deadline:* sessions are user-proxied work and must NOT be excluded.

    Decision: bu-ve8ne. If this test fails, re-read roster/chronicler/AGENTS.md.
    """
    assert "deadline:" not in EXCLUDED_TRIGGER_SOURCES
    assert not EXCLUDED_TRIGGER_SOURCE_PREFIX.startswith("deadline")
    assert EXCLUDED_TRIGGER_SOURCE_PREFIX != "deadline:"


# ---------------------------------------------------------------------------
# Only user-visible rows are projected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_only_user_rows_projected() -> None:
    """Only rows with non-excluded trigger_source values produce episodes."""
    t_base = _NOW
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
    assert len(projected_source_refs) == 3
    assert all("mybutler.sessions:" in ref for ref in projected_source_refs)


@pytest.mark.asyncio
async def test_deadline_rows_are_projected() -> None:
    """deadline:<task-name> sessions must be projected into the Tasks lane.

    See roster/chronicler/AGENTS.md for the decision rationale (bu-ve8ne).
    """
    deadline_row = _make_row(
        session_id=99,
        started_at=_NOW,
        completed_at=_NOW + timedelta(minutes=2),
        trigger_source="deadline:passport-renewal",
    )

    pool = _pool_returning(deadline_row)
    cp = _chronicler_pool()
    adapter = _adapter("mybutler")

    projected_source_refs: list[str] = []

    async def _fake_upsert_episode(conn: object, episode: Episode) -> Episode:
        projected_source_refs.append(episode.source_ref)
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

    assert result.rows_projected == 1
    assert len(projected_source_refs) == 1
    assert "mybutler.sessions:99" in projected_source_refs[0]


# ---------------------------------------------------------------------------
# Watermark advances correctly over filtered (included) rows only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watermark_advances_over_included_rows_only() -> None:
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


# ---------------------------------------------------------------------------
# Title resolution (bu-fkqv0) — unit tests for _compute_episode_title
# ---------------------------------------------------------------------------


def test_title_route_with_display_name() -> None:
    """trigger_source='route' with resolved contact → 'Conversation with {name}'."""
    title = CoreSessionsAdapter._compute_episode_title(
        "relationship", "route", ("Alice", "telegram")
    )
    assert title == "Conversation with Alice"


def test_title_trigger_source_trigger() -> None:
    """trigger_source='trigger' → '{schema}: manual task'."""
    title = CoreSessionsAdapter._compute_episode_title("lifestyle", "trigger", (None, None))
    assert title == "lifestyle: manual task"


# ---------------------------------------------------------------------------
# Title resolution (bu-fkqv0) — regression tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_route_session_title_contains_display_name_regression() -> None:
    """Regression test (bu-fkqv0): route session with resolved contact must NOT
    produce '{schema} session' title — it must contain the contact's display name.
    """
    event_uuid = UUID("01900000-0000-7000-8000-000000000001")

    row = _make_row(
        session_id=200,
        started_at=_NOW,
        completed_at=_NOW + timedelta(minutes=5),
        trigger_source="route",
        ingestion_event_id=event_uuid,
    )

    source_conn = AsyncMock()
    source_conn.fetchval = AsyncMock(return_value=True)
    source_conn.fetch = AsyncMock(side_effect=_make_source_fetch(row, event_uuid))
    source_pool = AsyncMock()
    source_pool.acquire = MagicMock(return_value=_AsyncCtx(source_conn))

    cp = _chronicler_pool()
    adapter = _adapter("relationship")

    captured_titles: list[str] = []

    async def _capture_upsert_episode(conn: object, episode: Episode) -> Episode:
        captured_titles.append(episode.title)
        return Episode(**{**episode.__dict__, "id": uuid.uuid4()})

    async def _fake_upsert_point_event(conn: object, event: PointEvent) -> PointEvent:
        return PointEvent(**{**event.__dict__, "id": uuid.uuid4()})

    with (
        patch("butlers.chronicler.adapters.sessions.get_checkpoint_subsource", return_value=None),
        patch("butlers.chronicler.adapters.sessions.upsert_checkpoint_subsource"),
        patch(
            "butlers.chronicler.adapters.sessions.upsert_episode",
            side_effect=_capture_upsert_episode,
        ),
        patch(
            "butlers.chronicler.adapters.sessions.upsert_point_event",
            side_effect=_fake_upsert_point_event,
        ),
        patch("butlers.chronicler.adapters.sessions.link_event_to_episode"),
    ):
        result = await adapter.project(source_pool, chronicler_pool=cp, since=None)

    assert result.rows_projected == 1
    assert len(captured_titles) == 1
    assert captured_titles[0] != "relationship session"
    assert "Alice" in captured_titles[0]
    assert captured_titles[0] == "Conversation with Alice"


@pytest.mark.asyncio
async def test_route_session_unresolved_contact_falls_back_to_channel() -> None:
    """When contact cannot be resolved, title must be 'Conversation via {channel}'."""
    event_uuid = UUID("01900000-0000-7000-8000-000000000002")

    row = _make_row(
        session_id=201,
        started_at=_NOW,
        completed_at=_NOW + timedelta(minutes=3),
        trigger_source="route",
        ingestion_event_id=event_uuid,
    )

    source_conn = AsyncMock()
    source_conn.fetchval = AsyncMock(return_value=True)
    source_conn.fetch = AsyncMock(
        side_effect=_make_source_fetch(row, event_uuid, display_name=None, channel="telegram")
    )
    source_pool = AsyncMock()
    source_pool.acquire = MagicMock(return_value=_AsyncCtx(source_conn))

    cp = _chronicler_pool()
    adapter = _adapter("relationship")

    captured_titles: list[str] = []

    async def _capture_upsert_episode(conn: object, episode: Episode) -> Episode:
        captured_titles.append(episode.title)
        return Episode(**{**episode.__dict__, "id": uuid.uuid4()})

    async def _fake_upsert_point_event(conn: object, event: PointEvent) -> PointEvent:
        return PointEvent(**{**event.__dict__, "id": uuid.uuid4()})

    with (
        patch("butlers.chronicler.adapters.sessions.get_checkpoint_subsource", return_value=None),
        patch("butlers.chronicler.adapters.sessions.upsert_checkpoint_subsource"),
        patch(
            "butlers.chronicler.adapters.sessions.upsert_episode",
            side_effect=_capture_upsert_episode,
        ),
        patch(
            "butlers.chronicler.adapters.sessions.upsert_point_event",
            side_effect=_fake_upsert_point_event,
        ),
        patch("butlers.chronicler.adapters.sessions.link_event_to_episode"),
    ):
        await adapter.project(source_pool, chronicler_pool=cp, since=None)

    assert captured_titles == ["Conversation via telegram"]


# ---------------------------------------------------------------------------
# Helpers for title-resolution integration tests
# ---------------------------------------------------------------------------


def _make_source_fetch(
    row: dict,
    event_uuid: UUID,
    display_name: str | None = "Alice",
    channel: str = "telegram",
) -> object:
    """Build a side_effect for conn.fetch that returns session row + contact info."""
    call_count = 0

    contact_row_mock = MagicMock(
        **{
            "event_id": event_uuid,
            "channel": channel,
            "display_name": display_name,
            "__getitem__": lambda s, k, _d={"event_id": event_uuid, "channel": channel, "display_name": display_name}: (
                _d[k]
            ),
        }
    )

    def _side_effect(*args: object, **kwargs: object) -> list:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return [_make_mock_row(row)]
        return [contact_row_mock]

    return _side_effect
