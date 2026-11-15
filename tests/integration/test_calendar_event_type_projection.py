"""Typed Google event fields persist through the real core chain (bu-s11n0s.4 S1).

The production parser and projector write ``calendar_events.event_type`` and
``working_location_type``/``working_location_label`` (core_341); every readback is a fresh query on its own connection after the
projector returned. Payloads are synthetic (shaped after the public Events resource reference).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from butlers.modules.calendar import CalendarModule, _google_event_to_calendar_event

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


def _payload(**extra: Any) -> dict[str, Any]:
    return {
        "id": "typed-1",
        "summary": "Working from",
        "start": {"date": "2026-07-01"},
        "end": {"date": "2026-07-02"},
        **extra,
    }


async def _project(module: CalendarModule, source: Any, payload: dict[str, Any]) -> None:
    event = _google_event_to_calendar_event(payload, fallback_timezone="Asia/Singapore")
    assert event is not None
    await module._project_provider_changes(
        source_id=source,
        provider_name="google",
        calendar_id="primary",
        updated_events=[event],
        cancelled_ids=[],
    )


async def _read(pool: Any, origin_ref: str) -> tuple[Any, ...]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT event_type, working_location_type, working_location_label, all_day, "
            "metadata->>'provider' AS provider FROM calendar_events WHERE origin_ref = $1",
            origin_ref,
        )
    assert row is not None
    return tuple(row)


async def test_typed_fields_are_written_replaced_and_cleared(migrated_core_postgres_pool) -> None:
    async with migrated_core_postgres_pool() as pool:
        source = await pool.fetchval(
            """INSERT INTO calendar_sources (source_key,source_kind,lane,provider,calendar_id)
               VALUES ('provider:google:primary','provider_event','user','google','primary')
               RETURNING id"""
        )
        module = CalendarModule()
        module._db = SimpleNamespace(pool=pool, db_name="general")
        module._butler_name = "general"
        assert await module._projection_tables_available() is True

        home = {"type": "homeOffice", "homeOffice": {}}
        await _project(
            module, source, _payload(eventType="workingLocation", workingLocationProperties=home)
        )
        assert await _read(pool, "typed-1") == (
            "workingLocation",
            "homeOffice",
            "home office",
            True,
            "google",
        )

        office = {"type": "officeLocation", "officeLocation": {"label": "HQ"}}
        await _project(
            module, source, _payload(eventType="workingLocation", workingLocationProperties=office)
        )
        assert (await _read(pool, "typed-1"))[:3] == ("workingLocation", "officeLocation", "HQ")

        # The provider drops the properties: the location is cleared, not kept.
        await _project(module, source, _payload(eventType="workingLocation"))
        assert (await _read(pool, "typed-1"))[:3] == ("workingLocation", None, None)

        # A type change replaces the type; an unknown type survives.
        await _project(module, source, _payload(eventType="someFutureType"))
        assert (await _read(pool, "typed-1"))[:3] == ("someFutureType", None, None)

        await _project(module, source, _payload(id="ooo-1", eventType="outOfOffice"))
        assert (await _read(pool, "ooo-1"))[:3] == ("outOfOffice", None, None)

        # The checks refuse what the normalizer never produces.
        with pytest.raises(Exception, match="calendar_events_event_type_length_check"):
            await pool.execute(
                "UPDATE calendar_events SET event_type = '' WHERE origin_ref = 'ooo-1'"
            )
        # A type without its label: the pair check must not pass on a NULL result.
        with pytest.raises(Exception, match="calendar_events_working_location_check"):
            await pool.execute(
                "UPDATE calendar_events SET working_location_type = 'homeOffice' "
                "WHERE origin_ref = 'ooo-1'"
            )
