"""Roster schedules fire at the time their own prompt or comment states. [bu-kqnum.3.7]

Hour-pinned crons are evaluated in the owner timezone (here Asia/Singapore) unless the schedule
pins a zone. Owner-facing schedules that name an owner-local time are anchored there; infrastructure
schedules commented in UTC are pinned with ``Etc/UTC`` (the literal ``UTC`` is the owner-zone
sentinel, so it cannot pin).
"""

from __future__ import annotations

import re
import zoneinfo
from datetime import UTC, datetime
from pathlib import Path

import pytest

from butlers.config import ScheduleSpec, load_config
from butlers.core.scheduler import _effective_schedule_timezone, _next_run

pytestmark = pytest.mark.unit

ROSTER = Path(__file__).resolve().parents[2] / "roster"
OWNER_ZONE = "Asia/Singapore"
# A fixed reference instant keeps the next-fire computation deterministic.
REFERENCE = datetime(2026, 10, 19, 5, 0, tzinfo=UTC)


def _schedules(butler: str) -> dict[str, ScheduleSpec]:
    return {s.name: s for s in load_config(ROSTER / butler).schedules}


def _fires_at(schedule: ScheduleSpec, *, zone: str = OWNER_ZONE) -> datetime:
    effective = _effective_schedule_timezone(schedule.timezone, zone)
    return _next_run(schedule.cron, timezone=effective, max_stagger_seconds=0, now=REFERENCE)


@pytest.mark.parametrize(
    ("butler", "name", "hour"),
    [
        ("general", "eod-tomorrow-prep", 23),  # "23:00 SGT"
        ("chronicler", "chronicler_gap_interview", 9),  # "09:00 owner-local"
        ("chronicler", "chronicler_day_close", 1),  # "01:05 day-close"
    ],
)
def test_owner_facing_schedules_fire_at_their_stated_local_hour(
    butler: str, name: str, hour: int
) -> None:
    schedule = _schedules(butler)[name]
    assert schedule.timezone is None
    local = _fires_at(schedule).astimezone(zoneinfo.ZoneInfo(OWNER_ZONE))
    assert local.hour == hour


@pytest.mark.parametrize(
    ("butler", "name", "utc_hour", "utc_minute"),
    [
        ("switchboard", "spend-rule-savings", 4, 15),
        ("switchboard", "fleet-case-lapse-sweep", 4, 10),
        ("switchboard", "commitment-escalation", 5, 40),
        ("health", "insight_scan", 7, 0),
    ],
)
def test_infrastructure_schedules_commented_in_utc_are_pinned_to_utc(
    butler: str, name: str, utc_hour: int, utc_minute: int
) -> None:
    schedule = _schedules(butler)[name]
    assert schedule.timezone == "Etc/UTC"
    # The pin survives a non-UTC owner zone: the effective zone is not the owner's.
    assert _effective_schedule_timezone(schedule.timezone, OWNER_ZONE) == "Etc/UTC"
    fired = _fires_at(schedule)
    assert (fired.hour, fired.minute) == (utc_hour, utc_minute)


def test_travel_check_is_pinned_to_utc_and_fires_at_eight_in_the_morning_sgt() -> None:
    """Cron `0 0` pinned to Etc/UTC is 08:00 SGT, matching the canonical travel schedule text."""
    schedule = _schedules("travel")["upcoming-travel-check"]
    assert schedule.cron == "0 0 * * *"
    assert schedule.timezone == "Etc/UTC"
    fired = _fires_at(schedule)
    assert (fired.hour, fired.minute) == (0, 0)
    assert fired.astimezone(zoneinfo.ZoneInfo(OWNER_ZONE)).hour == 8


def test_insight_delivery_window_is_pinned_to_utc() -> None:
    schedule = _schedules("switchboard")["insight-delivery-cycle"]
    assert schedule.timezone == "Etc/UTC"
    hours = {_fires_at(schedule).hour}
    assert hours <= set(range(6, 12))


def test_every_schedule_commented_with_a_utc_time_is_pinned() -> None:
    """A comment naming 'HH:MM UTC' above a schedule must be matched by an explicit pin."""
    stated = re.compile(r"\d{1,2}:\d{2}(?:-\d{1,2}:\d{2})?\s+UTC")
    unpinned: list[str] = []
    for toml in sorted(ROSTER.glob("*/butler.toml")):
        lines = toml.read_text().split("\n")
        schedules = _schedules(toml.parent.name)
        for index, line in enumerate(lines):
            if line.strip() != "[[butler.schedule]]":
                continue
            block_start = index
            while block_start > 0 and lines[block_start - 1].lstrip().startswith("#"):
                block_start -= 1
            header = "\n".join(lines[block_start:index])
            name = re.search(r'name = "([^"]+)"', "\n".join(lines[index : index + 3]))
            assert name is not None
            if stated.search(header) and schedules[name.group(1)].timezone is None:
                unpinned.append(f"{toml.parent.name}:{name.group(1)}")
    assert unpinned == []


def test_no_roster_comment_claims_the_scheduler_interprets_cron_in_utc() -> None:
    offenders = [
        str(p.relative_to(ROSTER))
        for p in sorted(ROSTER.glob("*/butler.toml"))
        if "scheduler interprets cron in UTC" in p.read_text()
    ]
    assert offenders == []
