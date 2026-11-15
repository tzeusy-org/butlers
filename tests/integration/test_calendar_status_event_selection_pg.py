"""Status events stay out of the meeting selectors on real Postgres (bu-s11n0s.4 S2).

Timed out-of-office, working-location, focus and ordinary events are projected through the
production parser and projector (core_341), then each selector's own SQL runs on a fresh
connection. Payloads are synthetic (shaped after the public Events resource reference).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from butlers.jobs import calendar_prep, meeting_card
from butlers.modules.calendar import CalendarModule, _google_event_to_calendar_event
from butlers.modules.calendar.working_location import (
    CurrentWorkingLocation,
    current_working_location,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

_START = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)


def _timed(event_id: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": event_id,
        "summary": event_id,
        "start": {"dateTime": _START.isoformat()},
        "end": {"dateTime": (_START + timedelta(hours=1)).isoformat()},
        **extra,
    }


async def test_meeting_card_and_prep_selectors_skip_status_events(
    migrated_core_postgres_pool,
) -> None:
    async with migrated_core_postgres_pool() as pool:
        source = await pool.fetchval(
            """INSERT INTO calendar_sources (source_key,source_kind,lane,provider,calendar_id)
               VALUES ('provider:google:primary','provider_event','user','google','primary')
               RETURNING id"""
        )
        module = CalendarModule()
        module._db = SimpleNamespace(pool=pool, db_name="general")
        module._butler_name = "general"
        location = {"type": "homeOffice", "homeOffice": {}}
        payloads = [
            _timed("meeting"),
            _timed("focus", eventType="focusTime"),
            _timed("ooo", eventType="outOfOffice"),
            _timed("location", eventType="workingLocation", workingLocationProperties=location),
        ]
        events = [_google_event_to_calendar_event(p, fallback_timezone="UTC") for p in payloads]
        await module._project_provider_changes(
            source_id=source,
            provider_name="google",
            calendar_id="primary",
            updated_events=events,
            cancelled_ids=[],
        )
        ids = {
            row["origin_ref"]: row["id"]
            for row in await pool.fetch("SELECT id, origin_ref FROM calendar_events")
        }
        assert set(ids) == {"meeting", "focus", "ooo", "location"}

        async with pool.acquire() as conn:
            cards = await conn.fetch(
                meeting_card._OCCURRENCE_SQL,
                _START - timedelta(minutes=5),
                _START + timedelta(minutes=5),
            )
        assert {row["event_id"] for row in cards} == {ids["meeting"], ids["focus"]}

        # Prep reads entity-linked events: link one person to every event.
        entity = await pool.fetchval(
            "INSERT INTO public.entities (canonical_name, entity_type) "
            "VALUES ('Sam', 'person') RETURNING id"
        )
        for event_id in ids.values():
            await pool.execute(
                "INSERT INTO calendar_event_entities (event_id, entity_id) VALUES ($1, $2)",
                event_id,
                entity,
            )
        async with pool.acquire() as conn:
            prep = await conn.fetch(
                calendar_prep._UPCOMING_LINKED_EVENTS_SQL,
                _START - timedelta(hours=1),
                _START + timedelta(hours=1),
            )
        assert {row["event_id"] for row in prep} == {ids["meeting"], ids["focus"]}

        # S3: the working-location read finds only the declaration, through its own SQL.
        async with pool.acquire() as conn:
            during = await current_working_location(
                conn, now=_START + timedelta(minutes=30), zone="Asia/Singapore"
            )
            after = await current_working_location(
                conn, now=_START + timedelta(hours=2), zone="Asia/Singapore"
            )
        assert during == CurrentWorkingLocation(
            type="homeOffice", label="home office", until=_START + timedelta(hours=1)
        )
        assert after is None
