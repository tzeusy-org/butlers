"""Unit tests verifying tombstone_reason field wiring in models and storage.

Covers:
- Episode and PointEvent dataclasses carry tombstone_reason adjacent to tombstone_at.
- upsert_episode / upsert_point_event include tombstone_reason in the SQL column list
  and bind the field value as a parameter.
- _row_to_episode / _row_to_point_event (exercised via the upsert return path) read
  tombstone_reason from the returned row.

These are pure-unit tests that mock the asyncpg connection; no Docker or live DB
required.  Integration round-trip coverage lives in
roster/chronicler/tests/test_storage_integration.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.chronicler.models import Episode, PointEvent
from butlers.chronicler.storage import upsert_episode, upsert_point_event

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 4, 30, 0, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Dataclass field presence
# ---------------------------------------------------------------------------


def test_episode_has_tombstone_reason_field() -> None:
    """Episode dataclass exposes tombstone_reason with a None default."""
    ep = Episode(
        source_name="test",
        source_ref="ref-1",
        episode_type="session",
        start_at=_NOW,
    )
    assert hasattr(ep, "tombstone_reason"), "Episode must have tombstone_reason field"
    assert ep.tombstone_reason is None


def test_point_event_has_tombstone_reason_field() -> None:
    """PointEvent dataclass exposes tombstone_reason with a None default."""
    ev = PointEvent(
        source_name="test",
        source_ref="ref-1",
        event_type="click",
        occurred_at=_NOW,
    )
    assert hasattr(ev, "tombstone_reason"), "PointEvent must have tombstone_reason field"
    assert ev.tombstone_reason is None


def test_episode_tombstone_reason_adjacent_to_tombstone_at() -> None:
    """tombstone_reason must be defined immediately after tombstone_at in Episode."""
    import dataclasses

    field_names = [f.name for f in dataclasses.fields(Episode)]
    at_idx = field_names.index("tombstone_at")
    reason_idx = field_names.index("tombstone_reason")
    assert reason_idx == at_idx + 1, (
        f"tombstone_reason should be adjacent to tombstone_at in Episode; "
        f"got positions {at_idx} and {reason_idx}"
    )


def test_point_event_tombstone_reason_adjacent_to_tombstone_at() -> None:
    """tombstone_reason must be defined immediately after tombstone_at in PointEvent."""
    import dataclasses

    field_names = [f.name for f in dataclasses.fields(PointEvent)]
    at_idx = field_names.index("tombstone_at")
    reason_idx = field_names.index("tombstone_reason")
    assert reason_idx == at_idx + 1, (
        f"tombstone_reason should be adjacent to tombstone_at in PointEvent; "
        f"got positions {at_idx} and {reason_idx}"
    )


# ---------------------------------------------------------------------------
# SQL wiring: upsert_episode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_episode_sql_includes_tombstone_reason_column() -> None:
    """INSERT column list for episodes must include tombstone_reason."""
    row = _make_record(
        id=None,
        source_name="s",
        source_ref="r",
        episode_type="session",
        start_at=_NOW,
        end_at=None,
        precision="exact",
        title=None,
        payload="{}",
        privacy="normal",
        retention_days=None,
        tombstone_at=None,
        tombstone_reason=None,
        created_at=_NOW,
        updated_at=_NOW,
    )
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=row)

    episode = Episode(
        source_name="s",
        source_ref="r",
        episode_type="session",
        start_at=_NOW,
        tombstone_at=_NOW,
        tombstone_reason="heartbeat_session",
    )
    result = await upsert_episode(pool, episode)

    sql: str = pool.fetchrow.call_args.args[0]
    assert "tombstone_reason" in sql, "upsert_episode SQL must name tombstone_reason column"
    assert "EXCLUDED.tombstone_reason" in sql, (
        "upsert_episode ON CONFLICT DO UPDATE must set tombstone_reason = EXCLUDED.tombstone_reason"
    )

    # Parameter value must be bound.
    positional_args = pool.fetchrow.call_args.args
    assert "heartbeat_session" in positional_args, (
        "tombstone_reason value must appear in the SQL parameters"
    )

    # Return value hydrated correctly from row.
    assert result.tombstone_reason is None  # mock row returns None


@pytest.mark.asyncio
async def test_upsert_episode_tombstone_reason_round_trips_via_returned_row() -> None:
    """Return value from upsert_episode carries tombstone_reason from the DB row."""
    row = _make_record(
        id=None,
        source_name="s",
        source_ref="r",
        episode_type="session",
        start_at=_NOW,
        end_at=None,
        precision="exact",
        title=None,
        payload="{}",
        privacy="normal",
        retention_days=None,
        tombstone_at=_NOW,
        tombstone_reason="heartbeat_session",
        created_at=_NOW,
        updated_at=_NOW,
    )
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=row)

    episode = Episode(
        source_name="s",
        source_ref="r",
        episode_type="session",
        start_at=_NOW,
        tombstone_at=_NOW,
        tombstone_reason="heartbeat_session",
    )
    result = await upsert_episode(pool, episode)
    assert result.tombstone_reason == "heartbeat_session", (
        "upsert_episode must hydrate tombstone_reason from the RETURNING row"
    )


# ---------------------------------------------------------------------------
# SQL wiring: upsert_point_event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_point_event_sql_includes_tombstone_reason_column() -> None:
    """INSERT column list for point_events must include tombstone_reason."""
    row = _make_record(
        id=None,
        source_name="s",
        source_ref="r",
        event_type="click",
        occurred_at=_NOW,
        precision="exact",
        title=None,
        payload="{}",
        privacy="normal",
        retention_days=None,
        tombstone_at=None,
        tombstone_reason=None,
        created_at=_NOW,
        updated_at=_NOW,
    )
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=row)

    event = PointEvent(
        source_name="s",
        source_ref="r",
        event_type="click",
        occurred_at=_NOW,
        tombstone_at=_NOW,
        tombstone_reason="stale_ping",
    )
    result = await upsert_point_event(pool, event)

    sql: str = pool.fetchrow.call_args.args[0]
    assert "tombstone_reason" in sql, "upsert_point_event SQL must name tombstone_reason column"
    assert "EXCLUDED.tombstone_reason" in sql, (
        "upsert_point_event ON CONFLICT DO UPDATE must set tombstone_reason = EXCLUDED.tombstone_reason"
    )

    positional_args = pool.fetchrow.call_args.args
    assert "stale_ping" in positional_args, (
        "tombstone_reason value must appear in the SQL parameters"
    )

    assert result.tombstone_reason is None  # mock row returns None


@pytest.mark.asyncio
async def test_upsert_point_event_tombstone_reason_round_trips_via_returned_row() -> None:
    """Return value from upsert_point_event carries tombstone_reason from the DB row."""
    row = _make_record(
        id=None,
        source_name="s",
        source_ref="r",
        event_type="click",
        occurred_at=_NOW,
        precision="exact",
        title=None,
        payload="{}",
        privacy="normal",
        retention_days=None,
        tombstone_at=_NOW,
        tombstone_reason="stale_ping",
        created_at=_NOW,
        updated_at=_NOW,
    )
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=row)

    event = PointEvent(
        source_name="s",
        source_ref="r",
        event_type="click",
        occurred_at=_NOW,
        tombstone_at=_NOW,
        tombstone_reason="stale_ping",
    )
    result = await upsert_point_event(pool, event)
    assert result.tombstone_reason == "stale_ping", (
        "upsert_point_event must hydrate tombstone_reason from the RETURNING row"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(**kwargs: object) -> MagicMock:
    """Return a MagicMock that acts like an asyncpg.Record for the given key/value pairs."""
    record = MagicMock()
    record.__getitem__ = MagicMock(side_effect=lambda k: kwargs[k])
    return record
