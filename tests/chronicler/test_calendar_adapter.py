"""Tests for the Calendar completed-instance Chronicler projection adapter.

Covers the title fallback chain when the upstream Google Calendar event has
no summary/title — the adapter should pick the next most-meaningful field
from the joined ``calendar_events`` row (title → location → truncated
description → schema-qualified placeholder) instead of always falling back
to ``"{schema}: calendar block"``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from butlers.chronicler.adapters.calendar import (
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
async def test_title_falls_back_to_location_when_no_event_title() -> None:
    row = _make_row(
        metadata={},
        event_title=None,
        event_location="Conference Room B",
        event_description="Some longer description text",
    )
    ep = await _project_one(row)
    assert ep.title == "Conference Room B"


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
    # Truncation marker present.
    assert ep.title.endswith("…")


@pytest.mark.unit
async def test_title_uses_short_description_verbatim() -> None:
    row = _make_row(
        metadata={},
        event_title=None,
        event_location=None,
        event_description="Quick chat",
    )
    ep = await _project_one(row)
    assert ep.title == "Quick chat"


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
async def test_payload_exposes_event_level_fields() -> None:
    """The payload must surface description/location/title for downstream UIs."""
    row = _make_row(
        metadata={},
        event_title="Sprint Planning",
        event_location="Zoom",
        event_description="Plan the next sprint",
    )
    ep = await _project_one(row)
    assert ep.payload["title"] == "Sprint Planning"
    assert ep.payload["location"] == "Zoom"
    assert ep.payload["description"] == "Plan the next sprint"


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
