"""The workspace entry keeps the zone an event was authored in beside the display zone.

Before, ``timezone`` was overwritten with the display zone, so a 09:00 London call showed as 04:00
in another zone with nothing to say where it came from (bu-5ii4gj.5).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from butlers.api.routers.calendar_workspace import _normalize_entry

pytestmark = pytest.mark.unit


def _row(**overrides):
    row = {
        "instance_id": uuid4(),
        "event_id": uuid4(),
        "source_key": "provider:google:primary",
        "source_kind": "provider_event",
        "title": "Supplier call",
        "origin_ref": "evt-1",
        "instance_starts_at": datetime(2026, 7, 1, 8, 0, tzinfo=UTC),  # 09:00 in London (BST)
        "instance_ends_at": datetime(2026, 7, 1, 9, 0, tzinfo=UTC),
        "instance_timezone": "Europe/London",
        "event_timezone": "Europe/London",
        "event_metadata": {"source_type": "provider_event", "source_zone_from_payload": True},
        "instance_metadata": {},
        "source_metadata": {},
    }
    row.update(overrides)
    return row


def test_a_london_event_under_a_sydney_display_zone_keeps_its_own_zone():
    entry = _normalize_entry(_row(), view="user", display_tz=ZoneInfo("Australia/Sydney"))

    assert entry.source_timezone == "Europe/London"
    assert entry.timezone == "Australia/Sydney"
    # 08:00Z is 18:00 in Sydney (AEST, UTC+10): the converted start, not the authored 09:00.
    assert entry.start_at.hour == 18 and entry.start_at.utcoffset().total_seconds() == 10 * 3600


def test_the_instance_zone_wins_over_the_event_zone():
    entry = _normalize_entry(
        _row(instance_timezone="America/New_York", event_timezone="Europe/London"),
        view="user",
        display_tz=ZoneInfo("UTC"),
    )
    assert entry.source_timezone == "America/New_York"


@pytest.mark.parametrize("bad", [None, "", "  ", "Not/AZone", "UTC+8"])
def test_a_missing_or_invalid_zone_is_unknown_never_guessed(bad):
    entry = _normalize_entry(
        _row(instance_timezone=bad, event_timezone=bad),
        view="user",
        display_tz=ZoneInfo("Australia/Sydney"),
    )
    assert entry.source_timezone is None


def test_an_invalid_instance_zone_falls_through_to_the_event_zone():
    entry = _normalize_entry(
        _row(instance_timezone="Not/AZone", event_timezone="Europe/London"),
        view="user",
        display_tz=ZoneInfo("UTC"),
    )
    assert entry.source_timezone == "Europe/London"


@pytest.mark.parametrize(
    "metadata",
    [
        {"source_type": "provider_event"},  # the provider sent no zone: timezone is a default
        {"source_type": "provider_event", "source_zone_from_payload": False},
        {"source_type": "internal_scheduler", "source_zone_from_payload": True},
        {},
    ],
)
def test_a_write_time_default_zone_is_not_the_events_zone(metadata):
    # The scheduler stores the 'UTC' follow-the-owner sentinel; provider events with no
    # timeZone take the module config zone. Neither says where the event was authored.
    entry = _normalize_entry(
        _row(instance_timezone="UTC", event_timezone="UTC", event_metadata=metadata),
        view="user",
        display_tz=ZoneInfo("Asia/Singapore"),
    )
    assert entry.source_timezone is None


def _mapped(start: dict, end: dict):
    from butlers.modules.calendar.google_mapping import _google_event_to_calendar_event

    return _google_event_to_calendar_event(
        {"id": "evt1", "summary": "Call", "start": start, "end": end}, fallback_timezone="UTC"
    )


def test_the_real_google_mapping_records_whether_the_payload_carried_a_zone():
    with_zone = _mapped(
        {"dateTime": "2026-07-01T09:00:00+01:00", "timeZone": "Europe/London"},
        {"dateTime": "2026-07-01T10:00:00+01:00", "timeZone": "Europe/London"},
    )
    without = _mapped(
        {"dateTime": "2026-07-01T09:00:00+01:00"}, {"dateTime": "2026-07-01T10:00:00+01:00"}
    )
    assert with_zone.zone_from_payload is True and with_zone.timezone == "Europe/London"
    assert without.zone_from_payload is False and without.timezone == "UTC"
