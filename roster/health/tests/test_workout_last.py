"""workout_last: "when did I last <activity>?" over workout_session facts (no DB)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from butlers.tools.health.workouts import (
    NO_WORKOUT_REASON,
    activity_matches,
    workout_last,
)

pytestmark = pytest.mark.unit


def _row(
    start: datetime,
    activity: str = "running",
    *,
    minutes: float = 30,
    detection: str = "auto",
    end: datetime | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "activity_type": activity,
        "duration_ms": int(minutes * 60000),
        "detection": detection,
        "source": "google_health",
    }
    if end is not None:
        metadata["end_time"] = end.isoformat()
    return {"id": uuid.uuid4(), "valid_at": start, "metadata": metadata}


class _Pool:
    """Fake fact store honouring the keyset paging the tool uses."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.fetches = 0

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        assert "workout_session" in query and "validity = 'active'" in query
        self.fetches += 1
        ordered = sorted(self._rows, key=lambda r: (r["valid_at"], str(r["id"])), reverse=True)
        if len(args) == 3:
            cursor = (args[1], str(args[2]))
            ordered = [r for r in ordered if (r["valid_at"], str(r["id"])) < cursor]
        return ordered[: args[0]]


@pytest.mark.parametrize(
    ("query", "stored", "expected"),
    [
        ("run", "running", True),
        ("jog", "running", True),
        ("running", "running", True),
        ("bike", "biking", True),
        ("ride", "biking", True),
        ("weights", "weightlifting", True),
        ("trail run", "trail running", True),
        ("run", "trail running", True),
        ("swim", "running", False),
        ("yoga", "running", False),
        ("running", "walking", False),
        (None, "anything", True),
        ("  ", "anything", True),
    ],
)
def test_activity_matching_vocabulary(query: str | None, stored: str, expected: bool) -> None:
    assert activity_matches(query, stored) is expected


async def test_match_returns_latest_with_fields() -> None:
    row = _row(datetime(2026, 10, 10, 6, 30, tzinfo=UTC), minutes=42.5)
    result = await workout_last(_Pool([row]), "run", timezone="UTC")
    assert result["found"] is True
    assert result["id"] == str(row["id"])
    assert result["activity"] == "running"
    assert result["duration_minutes"] == 42.5
    assert result["started_at"] == "2026-10-10T06:30:00+00:00"


async def test_no_match_is_explicit_absence() -> None:
    pool = _Pool([_row(datetime(2026, 10, 10, 6, 30, tzinfo=UTC), "running")])
    result = await workout_last(pool, "swimming")
    assert result == {"found": False, "reason": NO_WORKOUT_REASON, "activity": "swimming"}


async def test_empty_store_is_explicit_absence() -> None:
    result = await workout_last(_Pool([]))
    assert result["found"] is False
    assert result["reason"] == NO_WORKOUT_REASON


async def test_latest_of_multiple_wins() -> None:
    older = _row(datetime(2026, 10, 1, 6, 0, tzinfo=UTC))
    newest = _row(datetime(2026, 10, 12, 6, 0, tzinfo=UTC))
    middle = _row(datetime(2026, 10, 5, 6, 0, tzinfo=UTC))
    other = _row(datetime(2026, 10, 15, 6, 0, tzinfo=UTC), "swimming")
    result = await workout_last(_Pool([older, newest, middle, other]), "run")
    assert result["id"] == str(newest["id"])
    any_result = await workout_last(_Pool([older, newest, middle, other]))
    assert any_result["id"] == str(other["id"])


async def test_source_attribution() -> None:
    device = await workout_last(
        _Pool([_row(datetime(2026, 10, 1, 6, tzinfo=UTC), detection="auto")]), "run"
    )
    manual = await workout_last(
        _Pool([_row(datetime(2026, 10, 1, 6, tzinfo=UTC), detection="manual")]), "run"
    )
    assert device["source"] == "device"
    assert manual["source"] == "owner_logged"
    assert device["provider"] == "google_health"


async def test_owner_logged_beats_overlapping_device_record() -> None:
    manual = _row(datetime(2026, 10, 10, 6, 0, tzinfo=UTC), detection="manual", minutes=45)
    device = _row(datetime(2026, 10, 10, 6, 10, tzinfo=UTC), detection="auto", minutes=30)
    result = await workout_last(_Pool([manual, device]), "run")
    assert result["id"] == str(manual["id"])
    assert result["source"] == "owner_logged"


async def test_non_overlapping_device_record_still_wins_when_later() -> None:
    manual = _row(datetime(2026, 10, 10, 6, 0, tzinfo=UTC), detection="manual", minutes=30)
    device = _row(datetime(2026, 10, 11, 6, 0, tzinfo=UTC), detection="auto")
    result = await workout_last(_Pool([manual, device]), "run")
    assert result["id"] == str(device["id"])


async def test_day_boundary_renders_owner_local_date() -> None:
    # 2026-10-10 23:30 UTC is already 2026-10-11 in Auckland (+13, NZDT).
    row = _row(datetime(2026, 10, 10, 23, 30, tzinfo=UTC))
    result = await workout_last(_Pool([row]), "run", timezone="Pacific/Auckland")
    assert result["started_at"] == "2026-10-11T12:30:00+13:00"
    assert result["started_at_utc"] == "2026-10-10T23:30:00+00:00"
    assert result["timezone"] == "Pacific/Auckland"


async def test_dst_transition_changes_offset() -> None:
    # US fall-back is 2026-11-01 06:00 UTC: 05:30 UTC is 01:30 EDT, 07:30 UTC is 02:30 EST.
    before = _row(datetime(2026, 11, 1, 5, 30, tzinfo=UTC))
    after = _row(datetime(2026, 11, 1, 7, 30, tzinfo=UTC))
    r_before = await workout_last(_Pool([before]), "run", timezone="America/New_York")
    r_after = await workout_last(_Pool([after]), "run", timezone="America/New_York")
    assert r_before["started_at"] == "2026-11-01T01:30:00-04:00"
    assert r_after["started_at"] == "2026-11-01T02:30:00-05:00"


async def test_unknown_timezone_falls_back_to_utc() -> None:
    row = _row(datetime(2026, 10, 10, 6, 30, tzinfo=UTC))
    result = await workout_last(_Pool([row]), "run", timezone="Not/AZone")
    assert result["timezone"] == "UTC"


async def test_match_past_the_first_page_is_found() -> None:
    walks = [
        _row(datetime(2026, 10, 1, 6, tzinfo=UTC) + timedelta(days=i), "walking") for i in range(7)
    ]
    swim = _row(datetime(2026, 9, 1, 6, tzinfo=UTC), "swimming")
    pool = _Pool([*walks, swim])
    result = await workout_last(pool, "swim", page_size=3)
    assert result["found"] is True
    assert result["id"] == str(swim["id"])
    assert pool.fetches > 1


async def test_overlapping_owner_record_on_the_next_page_still_wins() -> None:
    device = _row(datetime(2026, 10, 10, 6, 10, tzinfo=UTC), detection="auto")
    manual = _row(datetime(2026, 10, 10, 6, 0, tzinfo=UTC), detection="manual", minutes=45)
    pool = _Pool([device, manual])
    result = await workout_last(pool, "run", page_size=1)
    assert result["id"] == str(manual["id"])
