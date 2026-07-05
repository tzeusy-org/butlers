"""Unit tests for the deterministic weekly routine miner (bu-whhll.9).

Exercises ``compute_routine_candidates`` — the pure statistics function —
with synthetic episode fixtures. No DB, no LLM.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from butlers.chronicler.routines import (
    DEFAULT_TIMEZONE,
    compute_routine_candidates,
)

pytestmark = pytest.mark.unit

_TZ = ZoneInfo(DEFAULT_TIMEZONE)  # Asia/Singapore, UTC+8, no DST


def _local(d: date, hour: int, minute: int = 0) -> datetime:
    """Build an aware UTC datetime for local (Asia/Singapore) wall-clock time."""
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=_TZ).astimezone(UTC)


def _episode(
    *,
    source_name: str,
    episode_type: str,
    start: datetime,
    end: datetime,
    layer: str = "activity",
    trigger_source: str | None = None,
) -> dict:
    return {
        "source_name": source_name,
        "episode_type": episode_type,
        "start_at": start,
        "end_at": end,
        "layer": layer,
        "trigger_source": trigger_source,
    }


def _desk_episode(d: date, start_hour: float, end_hour: float) -> dict:
    """A spotify listening episode (desk-signal category 'music')."""
    start_minute = int(round((start_hour % 1) * 60))
    end_minute = int(round((end_hour % 1) * 60))
    return _episode(
        source_name="spotify.session_summary",
        episode_type="listening_episode",
        start=_local(d, int(start_hour), start_minute),
        end=_local(d, int(end_hour), end_minute),
    )


def _movement_episode(d: date, start_hour: float, end_hour: float) -> dict:
    """An owntracks movement episode (contradictor category 'travel')."""
    return _episode(
        source_name="owntracks.points",
        episode_type="movement_episode",
        start=_local(d, int(start_hour), int(round((start_hour % 1) * 60))),
        end=_local(d, int(end_hour), int(round((end_hour % 1) * 60))),
    )


def _gaming_episode(d: date, start_hour: float, end_hour: float) -> dict:
    """A steam play episode (contradictor category 'gaming')."""
    return _episode(
        source_name="steam.play_history",
        episode_type="play_episode",
        start=_local(d, int(start_hour), int(round((start_hour % 1) * 60))),
        end=_local(d, int(end_hour), int(round((end_hour % 1) * 60))),
    )


def _weekdays(start: date, count: int) -> list[date]:
    """Return *count* consecutive Mon-Fri dates starting from the first
    weekday on/after *start*."""
    out: list[date] = []
    cursor = start
    while cursor.weekday() > 4:
        cursor += timedelta(days=1)
    while len(out) < count:
        if cursor.weekday() <= 4:
            out.append(cursor)
        cursor += timedelta(days=1)
    return out


# A Monday, chosen arbitrarily and far from any DST edge (Singapore has none).
_ANCHOR_MONDAY = date(2026, 5, 4)
assert _ANCHOR_MONDAY.weekday() == 0


# ── Stable pattern found ────────────────────────────────────────────────────


def test_stable_weekday_pattern_is_found() -> None:
    """6 weeks of Mon-Fri 09:30-19:30 desk signal, no contradictors -> found."""
    mining_start = _ANCHOR_MONDAY
    mining_end = mining_start + timedelta(days=6 * 7)
    episodes = [_desk_episode(d, 9.5, 19.5) for d in _weekdays(mining_start, 6 * 5)]

    candidates = compute_routine_candidates(
        episodes,
        mining_start_date=mining_start,
        mining_end_date=mining_end,
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.dow_mask == 0b0011111  # Mon-Fri
    assert candidate.window_start_local == time(9, 30)
    assert candidate.window_end_local == time(19, 30)
    assert candidate.support_count == 30  # 6 weeks * 5 weekdays
    assert candidate.confidence == pytest.approx(1.0)
    assert "Mon-Fri" in candidate.label
    assert "09:30" in candidate.label and "19:30" in candidate.label
    assert candidate.evidence_summary["description"] == (
        "continuous desk signals, no movement, no gaming"
    )


# ── Unstable pattern not found ──────────────────────────────────────────────


def test_unstable_pattern_is_not_found() -> None:
    """Only 2 of 6 weeks show the pattern -> below MIN_SUPPORT_RATIO/COUNT."""
    mining_start = _ANCHOR_MONDAY
    mining_end = mining_start + timedelta(days=6 * 7)
    weekdays = _weekdays(mining_start, 6 * 5)

    # Group weekdays into weeks of 5; only weeks 0 and 3 get the desk signal.
    weeks = [weekdays[i : i + 5] for i in range(0, len(weekdays), 5)]
    episodes = []
    for week_idx in (0, 3):
        for d in weeks[week_idx]:
            episodes.append(_desk_episode(d, 9.5, 19.5))

    candidates = compute_routine_candidates(
        episodes,
        mining_start_date=mining_start,
        mining_end_date=mining_end,
    )

    assert candidates == []


# ── Layer exclusion ─────────────────────────────────────────────────────────


def test_intent_and_evidence_layer_episodes_are_excluded() -> None:
    """Non-activity-layer rows (intent calendar, raw evidence) never count,
    even when they otherwise look like a perfect desk-signal pattern."""
    mining_start = _ANCHOR_MONDAY
    mining_end = mining_start + timedelta(days=6 * 7)
    weekdays = _weekdays(mining_start, 6 * 5)

    # A calendar "focus block" covering the exact window, every weekday,
    # but stamped layer='intent' (planned, never counted) — must not
    # produce a routine on its own.
    intent_only = [
        _episode(
            source_name="google_calendar.completed",
            episode_type="scheduled_block",
            start=_local(d, 9, 30),
            end=_local(d, 19, 30),
            layer="intent",
        )
        for d in weekdays
    ]
    # A raw evidence-layer point-derived episode (defensive: should not
    # happen for spotify in production, but the function must not trust the
    # source_name alone — only layer='activity' rows count).
    evidence_only = [
        _episode(
            source_name="spotify.session_summary",
            episode_type="listening_episode",
            start=_local(d, 9, 30),
            end=_local(d, 19, 30),
            layer="evidence",
        )
        for d in weekdays
    ]

    candidates = compute_routine_candidates(
        intent_only + evidence_only,
        mining_start_date=mining_start,
        mining_end_date=mining_end,
    )

    assert candidates == []


def test_activity_layer_alongside_intent_still_counts_only_activity() -> None:
    """Mixing in intent-layer noise must not inflate or otherwise corrupt the
    activity-only mined result."""
    mining_start = _ANCHOR_MONDAY
    mining_end = mining_start + timedelta(days=6 * 7)
    weekdays = _weekdays(mining_start, 6 * 5)

    episodes = [_desk_episode(d, 9.5, 19.5) for d in weekdays]
    # Add a same-window intent (calendar) episode every day — should be a no-op.
    episodes += [
        _episode(
            source_name="google_calendar.completed",
            episode_type="scheduled_block",
            start=_local(d, 9, 30),
            end=_local(d, 19, 30),
            layer="intent",
        )
        for d in weekdays
    ]

    candidates = compute_routine_candidates(
        episodes,
        mining_start_date=mining_start,
        mining_end_date=mining_end,
    )

    assert len(candidates) == 1
    assert candidates[0].confidence == pytest.approx(1.0)
    assert candidates[0].support_count == 30


# ── Contradictors: movement / gaming ────────────────────────────────────────


def test_movement_contradictor_suppresses_the_slot() -> None:
    """A desk signal overlapped by a movement episode does not qualify —
    confirms the 'no movement' half of the routine definition."""
    mining_start = _ANCHOR_MONDAY
    mining_end = mining_start + timedelta(days=6 * 7)
    weekdays = _weekdays(mining_start, 6 * 5)

    episodes = [_desk_episode(d, 9.5, 19.5) for d in weekdays]
    # Every day, a movement episode covers the middle 2 hours -> that slice
    # of the window can never be part of a stable "no movement" pattern.
    episodes += [_movement_episode(d, 14.0, 16.0) for d in weekdays]

    candidates = compute_routine_candidates(
        episodes,
        mining_start_date=mining_start,
        mining_end_date=mining_end,
    )

    # The contiguous run breaks around 14:00-16:00; the longest surviving
    # run is the morning slice (09:30-14:00), still >= MIN_WINDOW_SLOTS.
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.window_start_local == time(9, 30)
    assert candidate.window_end_local == time(14, 0)


def test_gaming_contradictor_suppresses_the_slot() -> None:
    """A desk signal overlapped by gaming does not qualify — 'no gaming'."""
    mining_start = _ANCHOR_MONDAY
    mining_end = mining_start + timedelta(days=6 * 7)
    weekdays = _weekdays(mining_start, 6 * 5)

    episodes = [_desk_episode(d, 20.0, 23.0) for d in weekdays]
    episodes += [_gaming_episode(d, 20.0, 23.0) for d in weekdays]

    candidates = compute_routine_candidates(
        episodes,
        mining_start_date=mining_start,
        mining_end_date=mining_end,
    )

    assert candidates == []


# ── dow_mask edges ───────────────────────────────────────────────────────────


def test_dow_mask_bit_assignment_monday_is_bit_zero() -> None:
    """A Monday-only pattern sets exactly bit 0 (dow_mask == 1)."""
    mining_start = _ANCHOR_MONDAY
    mining_end = mining_start + timedelta(days=6 * 7)
    mondays = [d for d in _weekdays(mining_start, 6 * 5) if d.weekday() == 0]

    episodes = [_desk_episode(d, 9.0, 12.0) for d in mondays]

    candidates = compute_routine_candidates(
        episodes,
        mining_start_date=mining_start,
        mining_end_date=mining_end,
    )

    assert len(candidates) == 1
    assert candidates[0].dow_mask == 0b0000001
    assert candidates[0].label.startswith("Mon ")


def test_dow_mask_non_contiguous_days_grouped_by_identical_window() -> None:
    """Mon and Wed sharing an identical mined window group into one row with
    a non-contiguous dow_mask and a comma-separated label."""
    mining_start = _ANCHOR_MONDAY
    mining_end = mining_start + timedelta(days=6 * 7)
    weekdays = _weekdays(mining_start, 6 * 5)
    mon_wed = [d for d in weekdays if d.weekday() in (0, 2)]

    episodes = [_desk_episode(d, 9.0, 12.0) for d in mon_wed]

    candidates = compute_routine_candidates(
        episodes,
        mining_start_date=mining_start,
        mining_end_date=mining_end,
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.dow_mask == 0b0000101  # bit0 (Mon) + bit2 (Wed)
    assert candidate.label.startswith("Mon, Wed ")
    # support_count sums across both grouped weekdays.
    assert candidate.support_count == len(mon_wed)


def test_dow_mask_sunday_is_bit_six() -> None:
    """A Sunday-only pattern sets exactly bit 6 (dow_mask == 64)."""
    mining_start = _ANCHOR_MONDAY
    mining_end = mining_start + timedelta(days=6 * 7)
    sundays = []
    cursor = mining_start
    while cursor < mining_end:
        if cursor.weekday() == 6:
            sundays.append(cursor)
        cursor += timedelta(days=1)

    episodes = [_desk_episode(d, 10.0, 13.0) for d in sundays]

    candidates = compute_routine_candidates(
        episodes,
        mining_start_date=mining_start,
        mining_end_date=mining_end,
    )

    assert len(candidates) == 1
    assert candidates[0].dow_mask == 0b1000000


# ── Timezone boundary ────────────────────────────────────────────────────────


def test_early_morning_local_window_crossing_utc_midnight() -> None:
    """A 02:00-04:00 SGT (UTC+8) window is entirely on the PREVIOUS UTC
    calendar day (18:00-20:00 UTC). Slot-boundary math must use the local
    midnight anchor, not UTC midnight, or this window would be misattributed
    to the wrong local date/weekday."""
    mining_start = _ANCHOR_MONDAY
    mining_end = mining_start + timedelta(days=6 * 7)
    weekdays = _weekdays(mining_start, 6 * 5)

    for d in weekdays:
        start_utc = _local(d, 2, 0)
        # Sanity: this instant is on the previous UTC calendar day.
        assert start_utc.date() == d - timedelta(days=1)

    # Exactly 2h (4 slots) — the MIN_WINDOW_SLOTS floor.
    episodes = [_desk_episode(d, 2.0, 4.0) for d in weekdays]

    candidates = compute_routine_candidates(
        episodes,
        mining_start_date=mining_start,
        mining_end_date=mining_end,
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.dow_mask == 0b0011111  # still attributed to Mon-Fri (local)
    assert candidate.window_start_local == time(2, 0)
    assert candidate.window_end_local == time(4, 0)


def test_tz_boundary_respects_explicit_timezone_argument() -> None:
    """A different timezone shifts which UTC instants map to which local
    slot — passing timezone= must actually change the computation, not just
    be accepted and ignored."""
    mining_start = _ANCHOR_MONDAY
    mining_end = mining_start + timedelta(days=6 * 7)
    weekdays = _weekdays(mining_start, 6 * 5)

    # Build episodes anchored to UTC+8 local wall-clock 09:30-19:30.
    episodes = [_desk_episode(d, 9.5, 19.5) for d in weekdays]

    sgt_candidates = compute_routine_candidates(
        episodes,
        mining_start_date=mining_start,
        mining_end_date=mining_end,
        timezone="Asia/Singapore",
    )
    utc_candidates = compute_routine_candidates(
        episodes,
        mining_start_date=mining_start,
        mining_end_date=mining_end,
        timezone="UTC",
    )

    assert sgt_candidates[0].window_start_local == time(9, 30)
    # Interpreted as UTC, the same instants are 8 hours earlier locally.
    assert utc_candidates[0].window_start_local == time(1, 30)


def test_unknown_timezone_raises() -> None:
    with pytest.raises(ValueError):
        compute_routine_candidates(
            [],
            mining_start_date=_ANCHOR_MONDAY,
            mining_end_date=_ANCHOR_MONDAY + timedelta(days=7),
            timezone="Not/AZone",
        )


def test_empty_date_range_returns_no_candidates() -> None:
    assert (
        compute_routine_candidates(
            [_desk_episode(_ANCHOR_MONDAY, 9.5, 19.5)],
            mining_start_date=_ANCHOR_MONDAY,
            mining_end_date=_ANCHOR_MONDAY,
        )
        == []
    )
