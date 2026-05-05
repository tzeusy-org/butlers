"""Tests for the Calendar completed-instance Chronicler projection adapter.

Covers the title fallback chain when the upstream Google Calendar event has
no summary/title — the adapter should pick the next most-meaningful field
from the joined ``calendar_events`` row (title → location → truncated
description → schema-qualified placeholder).

Also covers the butler-managed calendar exclusion guard (defence-in-depth):
instances whose ``calendar_sources.lane = 'butler'`` must never be projected
into the user's Chronicle Calendar lane. Cross-schema dedup via
``origin_instance_ref`` collapse (regression for "five Labour Day bars" bug).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from butlers.chronicler.adapters.calendar import (
    BUTLER_MANAGED_SOURCE_KINDS,
    EPISODE_TYPE_SCHEDULED_BLOCK,
    SOURCE_NAME,
    CalendarCompletedAdapter,
)
from butlers.chronicler.models import Episode

_NOW = datetime(2026, 4, 1, 10, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Row(dict):
    """asyncpg.Record-like dict subclass."""

    def __getattr__(self, name: str) -> object:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def _make_row(
    *,
    metadata: dict | None = None,
    event_title: str | None = None,
    event_description: str | None = None,
    event_location: str | None = None,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
) -> _Row:
    starts_at = starts_at or (_NOW - timedelta(hours=1))
    ends_at = ends_at or _NOW
    return _Row(
        {
            "id": uuid4(),
            "event_id": uuid4(),
            "source_id": uuid4(),
            "origin_instance_ref": "evt:abc:2026-04-01T09:00:00Z",
            "starts_at": starts_at,
            "ends_at": ends_at,
            "status": "confirmed",
            "timezone": "UTC",
            "metadata": metadata if metadata is not None else {},
            "updated_at": ends_at,
            "event_title": event_title,
            "event_description": event_description,
            "event_location": event_location,
        }
    )


class _AsyncCtx:
    def __init__(self, obj: object) -> None:
        self._obj = obj

    async def __aenter__(self) -> object:
        return self._obj

    async def __aexit__(self, *_: object) -> None:
        return None


def _chronicler_pool() -> AsyncMock:
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=_AsyncCtx(None))
    conn.fetchrow = AsyncMock(return_value=None)
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


async def _project_one(row: _Row) -> Episode:
    """Drive ``_project_row`` directly with a single row and capture the Episode."""
    adapter = CalendarCompletedAdapter(butler_schemas=("butler_test",))
    captured: list[Episode] = []

    async def _fake_upsert(_conn: object, episode: Episode) -> Episode:
        captured.append(episode)
        return episode

    cp = _chronicler_pool()
    with patch(
        "butlers.chronicler.adapters.calendar.upsert_episode",
        side_effect=_fake_upsert,
    ):
        await adapter._project_row(cp, "butler_test", row)
    assert captured, "upsert_episode was not invoked"
    return captured[0]


# ---------------------------------------------------------------------------
# Title fallback chain
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_title_uses_metadata_summary_when_present() -> None:
    """When instance metadata has a summary, it wins over event-level fields."""
    row = _make_row(
        metadata={"summary": "Standup"},
        event_title="Wrong Event Title",
        event_location="Conference Room A",
    )
    ep = await _project_one(row)
    assert ep.title == "Standup"


@pytest.mark.unit
async def test_title_falls_back_to_event_title_when_metadata_empty() -> None:
    """No summary in instance metadata → use the joined ``calendar_events.title``."""
    row = _make_row(
        metadata={},
        event_title="Sprint Planning",
        event_location="Zoom",
    )
    ep = await _project_one(row)
    assert ep.title == "Sprint Planning"


@pytest.mark.unit
async def test_title_falls_back_to_truncated_description() -> None:
    long_desc = (
        "This is a fairly long description that should be truncated to keep "
        "the projected episode title manageable for downstream consumers."
    )
    row = _make_row(
        metadata={},
        event_title=None,
        event_location=None,
        event_description=long_desc,
    )
    ep = await _project_one(row)
    assert ep.title is not None
    assert ep.title.startswith("This is a fairly long")
    assert len(ep.title) <= 80
    assert ep.title.endswith("…")


@pytest.mark.unit
async def test_title_final_fallback_when_no_richer_context() -> None:
    """All richer fields blank/whitespace → schema-qualified placeholder."""
    row = _make_row(
        metadata={},
        event_title="   ",  # whitespace-only must not win
        event_location="",
        event_description=None,
    )
    ep = await _project_one(row)
    assert ep.title == "butler_test: calendar block"


@pytest.mark.unit
async def test_episode_basic_fields() -> None:
    starts = _NOW - timedelta(hours=1)
    ends = _NOW
    row = _make_row(
        starts_at=starts,
        ends_at=ends,
        event_title="Lunch with Jordan",
    )
    ep = await _project_one(row)
    assert ep.source_name == SOURCE_NAME
    assert ep.episode_type == EPISODE_TYPE_SCHEDULED_BLOCK
    assert ep.start_at == starts
    assert ep.end_at == ends
    assert ep.title == "Lunch with Jordan"


# ---------------------------------------------------------------------------
# Butler-managed calendar exclusion (Track B defence-in-depth)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_butler_managed_source_kinds_includes_scheduler_and_reminders() -> None:
    """The documented butler-managed source kinds must be present in the constant."""
    assert "internal_scheduler" in BUTLER_MANAGED_SOURCE_KINDS
    assert "internal_reminders" in BUTLER_MANAGED_SOURCE_KINDS


def _make_pool_with_rows(rows: list[_Row] | None, *, table_exists: bool = True) -> AsyncMock:
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=table_exists)
    conn.fetch = AsyncMock(return_value=rows if rows is not None else [])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


@pytest.mark.unit
async def test_fetch_instances_sql_excludes_butler_lane_no_since() -> None:
    """The SQL emitted for the no-since path must contain the butler-lane guard."""
    pool = _make_pool_with_rows([])
    adapter = CalendarCompletedAdapter(butler_schemas=("test_schema",))

    now = datetime.now(UTC)
    await adapter._fetch_instances(pool, "test_schema", None, now)

    fetch_args = pool.acquire.return_value._obj.fetch.call_args[0]
    sql = fetch_args[0] if fetch_args else ""
    assert "cs.lane != 'butler'" in sql, (
        "Exclusion guard 'cs.lane != \\'butler\\'' must appear in the no-since SQL query"
    )
    assert "INNER JOIN" in sql.upper() or "JOIN" in sql.upper(), (
        "calendar_sources join must be present"
    )


@pytest.mark.unit
async def test_project_user_lane_rows_are_still_projected() -> None:
    """User-lane calendar events continue to be projected after the fix."""
    user_row = _make_row(event_title="Dentist appointment")

    adapter = CalendarCompletedAdapter(butler_schemas=("test_schema",))
    captured: list[Episode] = []

    async def _fake_upsert(_conn: object, episode: Episode) -> Episode:
        captured.append(episode)
        return episode

    with (
        patch.object(
            adapter,
            "_fetch_instances",
            new=AsyncMock(return_value=[user_row]),
        ),
        patch(
            "butlers.chronicler.adapters.calendar.upsert_episode",
            side_effect=_fake_upsert,
        ),
    ):
        result = await adapter.project(
            MagicMock(),
            chronicler_pool=_chronicler_pool(),
            since=None,
        )

    assert result.rows_projected == 1
    assert result.episodes_closed == 1
    assert len(captured) == 1
    assert captured[0].title == "Dentist appointment"


# ---------------------------------------------------------------------------
# Cross-schema fan-out collapse
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_project_collapses_same_origin_instance_across_schemas() -> None:
    """Same Google Calendar event in N schemas projects to ONE chronicler episode.

    Regression for the "five Labour Day bars" bug. The dedup key is
    ``origin_instance_ref`` alone (the upstream Google Calendar identifier).
    """
    shared_origin_ref = "evt:labour_day:2026-05-01T00:00:00Z"
    rows_by_schema = {
        "schema_a": [_make_row(event_title="Labour Day")],
        "schema_b": [_make_row(event_title="Labour Day")],
        "schema_c": [_make_row(event_title="Labour Day")],
    }
    for rows in rows_by_schema.values():
        for row in rows:
            row["origin_instance_ref"] = shared_origin_ref

    adapter = CalendarCompletedAdapter(
        butler_schemas=tuple(rows_by_schema.keys()),
    )
    captured: list[Episode] = []

    async def _fake_upsert(_conn: object, episode: Episode) -> Episode:
        captured.append(episode)
        return episode

    async def _fake_fetch(_pool: object, schema: str, _since: object, _now: object) -> list[_Row]:
        return rows_by_schema[schema]

    with (
        patch.object(adapter, "_fetch_instances", new=AsyncMock(side_effect=_fake_fetch)),
        patch(
            "butlers.chronicler.adapters.calendar.upsert_episode",
            side_effect=_fake_upsert,
        ),
    ):
        result = await adapter.project(
            MagicMock(),
            chronicler_pool=_chronicler_pool(),
            since=None,
        )

    assert result.rows_projected == 1, "Cross-schema fan-out must collapse to a single projection"
    assert len(captured) == 1
    assert captured[0].source_ref == f"calendar:{shared_origin_ref}"


@pytest.mark.unit
async def test_project_collapses_same_origin_under_multiple_event_ids_in_one_schema() -> None:
    """Two rows in ONE schema sharing origin_instance_ref collapse to a single episode.

    The unique constraint on calendar_event_instances is
    ``(event_id, origin_instance_ref)``, so the calendar sync can legitimately
    insert duplicate origin_instance_ref rows under different event_ids.
    Chronicler must still emit one episode.
    """
    shared_origin_ref = "evt:dup_event_ids:2026-05-01T07:00:00Z"
    row1 = _make_row(event_title="Daily standup")
    row2 = _make_row(event_title="Daily standup")
    row1["origin_instance_ref"] = shared_origin_ref
    row2["origin_instance_ref"] = shared_origin_ref
    row1["event_id"] = uuid4()
    row2["event_id"] = uuid4()

    adapter = CalendarCompletedAdapter(butler_schemas=("schema_only",))
    captured: list[Episode] = []

    async def _fake_upsert(_conn: object, episode: Episode) -> Episode:
        captured.append(episode)
        return episode

    with (
        patch.object(
            adapter,
            "_fetch_instances",
            new=AsyncMock(return_value=[row1, row2]),
        ),
        patch(
            "butlers.chronicler.adapters.calendar.upsert_episode",
            side_effect=_fake_upsert,
        ),
    ):
        result = await adapter.project(
            MagicMock(),
            chronicler_pool=_chronicler_pool(),
            since=None,
        )

    assert result.rows_projected == 1
    assert len(captured) == 1
    assert captured[0].source_ref == f"calendar:{shared_origin_ref}"
