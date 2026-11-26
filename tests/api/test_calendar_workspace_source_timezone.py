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
        "event_metadata": {},
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
