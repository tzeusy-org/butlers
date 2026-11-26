"""The briefing and overlay jobs read the owner's day in the owner's zone (bu-5ii4gj.5.1).

They used to read it in a fixed UTC+8, so a trip to another zone keyed the day contributions and
overlays on the wrong date. For an owner in Singapore (or no saved zone, which falls back to
Singapore) every key must stay exactly what the fixed reading produced.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

from butlers.jobs.briefing import SGT, briefing_day, day_start_utc, today_sgt
from butlers.jobs.calendar_overlay import (
    _local_date_str,
    overlay_key,
    run_travel_calendar_overlay_contribution,
)
from tests.jobs.calendar_contribution_harness import (
    ContributionStateStore,
    patch_state,
    source_reads,
)

pytestmark = pytest.mark.unit

LA = ZoneInfo("America/Los_Angeles")
INSTANT = datetime(
    2026, 3, 25, 3, 0, tzinfo=UTC
)  # 11:00 on the 25th in Singapore, 20:00 on the 24th in LA


def _zone(zone):
    return patch("butlers.jobs.briefing.owner_briefing_zone", AsyncMock(return_value=zone))


async def test_the_day_follows_the_owners_zone():
    with patch("butlers.jobs.briefing._now", return_value=INSTANT), _zone(LA):
        day, zone = await briefing_day(object())
    assert day == date(2026, 3, 24) and zone is LA


async def test_an_sgt_owner_keys_every_day_exactly_as_the_fixed_reading_did():
    """The cutover is a no-op for an SGT owner: same date at every hour across a month."""
    start = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)
    for hours in range(0, 24 * 31, 5):
        instant = start + timedelta(hours=hours)
        with patch("butlers.jobs.briefing._now", return_value=instant), _zone(SGT):
            day, _ = await briefing_day(object())
        assert day == instant.astimezone(SGT).date(), instant


async def test_an_unreadable_zone_falls_back_to_the_sgt_date():
    pool = AsyncMock()
    pool.fetchval.side_effect = OSError("settings down")
    with patch("butlers.jobs.briefing._now", return_value=INSTANT):
        day, zone = await briefing_day(pool)
    assert zone is SGT and day == date(2026, 3, 25)
    with patch("butlers.jobs.briefing.datetime") as clock:
        clock.now.return_value = INSTANT
        assert today_sgt() == date(2026, 3, 25)


def test_local_midnight_is_dst_correct():
    assert day_start_utc(date(2026, 3, 8), LA) == datetime(2026, 3, 8, 8, 0, tzinfo=UTC)  # PST
    assert day_start_utc(date(2026, 3, 9), LA) == datetime(2026, 3, 9, 7, 0, tzinfo=UTC)  # PDT


def test_a_timestamp_buckets_onto_its_owner_zone_date():
    late = datetime(2026, 3, 25, 3, 0, tzinfo=UTC)
    assert _local_date_str(late, LA) == "2026-03-24"
    assert _local_date_str(late, SGT) == "2026-03-25"


async def test_the_travel_overlay_window_and_dates_follow_the_owner_zone():
    leg = {
        "type": "flight",
        "carrier": "XX",
        "departure_city": "A",
        "arrival_city": "B",
        "departure_at": datetime(2026, 3, 25, 3, 0, tzinfo=UTC),  # 20:00 on the 24th in LA
        "arrival_at": datetime(2026, 3, 25, 9, 0, tzinfo=UTC),
        "pnr": None,
        "seat": None,
        "confirmation_number": "C1",
        "trip_name": "Trip",
        "trip_id": "t1",
    }
    pool = source_reads([leg], [])
    state = ContributionStateStore()
    with (
        patch("butlers.jobs.briefing._now", return_value=INSTANT),
        _zone(LA),
        patch_state("butlers.jobs.calendar_overlay", state),
    ):
        await run_travel_calendar_overlay_contribution(pool, None)

    window_start = pool.fetch.await_args_list[0].args[1]
    assert window_start == day_start_utc(date(2026, 3, 24), LA)
    today_key = overlay_key("2026-03-24")
    assert today_key in state.store and overlay_key("2026-03-23") not in state.store
    kinds = [e["kind"] for e in state.store[today_key]["entries"]]
    assert "departure" in kinds  # the 20:00 PDT departure lands on the 24th, not the 25th
