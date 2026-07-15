"""Unit tests for `butlers.chronicler.flags` (bu-v76a7, telemetry-
distillation bead 4).

Covers the pure rule functions directly (no I/O) and the
`evaluate_and_write_daily_flags` async orchestrator against a mocked pool +
monkeypatched storage calls. Pure-unit tests — no Docker / PostgreSQL
required. The real-Postgres feeder_dark-suppresses-derived-flags scenario and
idempotent-re-run regression live in
`tests/integration/test_daily_rollup_flags_integration.py`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import croniter
import pytest

from butlers.chronicler.flags import (
    FEEDER_STALE_MULTIPLE,
    FLAG_FEEDER_DARK,
    FLAG_LANE_SHARE_OUTLIER,
    FLAG_ROUTINE_BREAK,
    FLAG_SLEEP_MISSING,
    LANE_SHARE_OUTLIER_MULTIPLE,
    LANE_SHARE_TRAILING_DAYS,
    MIN_EVIDENCE_FLOOR_SECONDS,
    MIN_LANE_SHARE_FLOOR,
    MIN_TRAILING_DAYS_FOR_MEDIAN,
    SOURCE_CRON_MINUTES,
    compute_lane_share_outliers,
    compute_routine_breaks,
    compute_sleep_missing,
    evaluate_and_write_daily_flags,
    is_source_dark,
)
from butlers.chronicler.models import (
    Compatibility,
    DailyRollup,
    ProjectionCheckpoint,
    Routine,
    SourceAdapterState,
)
from butlers.config import load_config

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 5, 8, 0, 0, tzinfo=UTC)


def _state(*, active: bool) -> SourceAdapterState:
    return SourceAdapterState(
        source_name="test.source",
        chronicler_compatibility=Compatibility.SUPPORTED,
        active=active,
    )


def _checkpoint(
    *, last_success_at: datetime | None = None, last_run_at: datetime | None = None
) -> ProjectionCheckpoint:
    return ProjectionCheckpoint(
        source_name="test.source",
        last_success_at=last_success_at,
        last_run_at=last_run_at,
    )


# ---------------------------------------------------------------------------
# is_source_dark
# ---------------------------------------------------------------------------


def test_no_registration_is_dark() -> None:
    assert is_source_dark(None, None, cron_minutes=30, now=_NOW) is True


def test_inactive_source_is_dark_even_with_fresh_checkpoint() -> None:
    checkpoint = _checkpoint(last_success_at=_NOW - timedelta(minutes=1))
    assert is_source_dark(_state(active=False), checkpoint, cron_minutes=30, now=_NOW) is True


def test_active_source_with_no_checkpoint_is_not_dark() -> None:
    """Nothing to measure staleness against — trust the active flag alone."""
    assert is_source_dark(_state(active=True), None, cron_minutes=30, now=_NOW) is False


def test_active_source_with_fresh_checkpoint_is_not_dark() -> None:
    checkpoint = _checkpoint(last_success_at=_NOW - timedelta(minutes=29))
    assert is_source_dark(_state(active=True), checkpoint, cron_minutes=30, now=_NOW) is False


def test_active_source_with_stale_checkpoint_is_dark() -> None:
    """Stale beyond FEEDER_STALE_MULTIPLE x cron interval, even though
    active is still True — the silent-failure case the design calls out."""
    assert FEEDER_STALE_MULTIPLE == 2
    checkpoint = _checkpoint(last_success_at=_NOW - timedelta(minutes=61))
    assert is_source_dark(_state(active=True), checkpoint, cron_minutes=30, now=_NOW) is True


def test_active_source_exactly_at_threshold_is_not_dark() -> None:
    checkpoint = _checkpoint(last_success_at=_NOW - timedelta(minutes=60))
    assert is_source_dark(_state(active=True), checkpoint, cron_minutes=30, now=_NOW) is False


def test_falls_back_to_last_run_at_when_no_success_timestamp() -> None:
    checkpoint = _checkpoint(last_run_at=_NOW - timedelta(minutes=61))
    assert is_source_dark(_state(active=True), checkpoint, cron_minutes=30, now=_NOW) is True


# ---------------------------------------------------------------------------
# compute_sleep_missing
# ---------------------------------------------------------------------------


def test_sleep_missing_fires_when_zero_seconds_and_source_healthy() -> None:
    assert compute_sleep_missing(0, sleep_source_dark=False) is True


def test_sleep_missing_does_not_fire_with_any_sleep_seconds() -> None:
    assert compute_sleep_missing(1800, sleep_source_dark=False) is False


def test_sleep_missing_suppressed_when_feeder_dark() -> None:
    """The core classify-before-flagging case: zero sleep seconds on a
    known-outage day must never read as a behavioral flag."""
    assert compute_sleep_missing(0, sleep_source_dark=True) is False


# ---------------------------------------------------------------------------
# compute_routine_breaks
# ---------------------------------------------------------------------------


def _candidate(*, label: str, has_block: bool) -> dict[str, Any]:
    return {"routine_id": str(uuid4()), "label": label, "has_occupation_block": has_block}


def test_routine_break_reports_candidates_without_a_block() -> None:
    candidates = [
        _candidate(label="desk work", has_block=False),
        _candidate(label="already covered", has_block=True),
    ]
    breaks = compute_routine_breaks(candidates, routine_source_dark=False)
    assert [b["label"] for b in breaks] == ["desk work"]


def test_routine_break_empty_when_no_candidates() -> None:
    assert compute_routine_breaks([], routine_source_dark=False) == []


def test_routine_break_suppressed_when_source_dark() -> None:
    """An outage on the writer/corroborator explains the missing block —
    must not report a behavioral break."""
    candidates = [_candidate(label="desk work", has_block=False)]
    assert compute_routine_breaks(candidates, routine_source_dark=True) == []


# ---------------------------------------------------------------------------
# compute_lane_share_outliers
# ---------------------------------------------------------------------------


def _trailing_uniform(lane: str, share: float, *, days: int, other_lane: str = "rest") -> dict:
    """Build `days` trailing days each with the same `lane` share."""
    base_date = date(2026, 6, 1)
    total = 10_000.0
    lane_seconds = total * share
    other_seconds = total - lane_seconds
    return {
        base_date + timedelta(days=i): {lane: lane_seconds, other_lane: other_seconds}
        for i in range(days)
    }


def test_low_evidence_day_never_flags_regardless_of_share() -> None:
    """Design-named guard: a day whose total tracked seconds fall below the
    floor must not produce a spurious 100%-share outlier."""
    today = {"work": MIN_EVIDENCE_FLOOR_SECONDS / 2}
    trailing = _trailing_uniform("work", 0.1, days=LANE_SHARE_TRAILING_DAYS)
    assert compute_lane_share_outliers(today, trailing) == {}


def test_insufficient_trailing_history_skips_lane() -> None:
    today = {"work": 10_000.0, "rest": 0.0}
    trailing = _trailing_uniform("work", 0.1, days=MIN_TRAILING_DAYS_FOR_MEDIAN - 1)
    assert "work" not in compute_lane_share_outliers(today, trailing)


def test_share_spike_beyond_multiple_flags_as_outlier() -> None:
    median_share = 0.1
    trailing = _trailing_uniform("work", median_share, days=LANE_SHARE_TRAILING_DAYS)
    today_share = median_share * LANE_SHARE_OUTLIER_MULTIPLE  # exactly at threshold
    today = {"work": 10_000.0 * today_share, "rest": 10_000.0 * (1 - today_share)}
    outliers = compute_lane_share_outliers(today, trailing)
    assert "work" in outliers
    assert outliers["work"]["median_share"] == pytest.approx(median_share)


def test_share_crash_below_inverse_multiple_flags_as_outlier() -> None:
    median_share = 0.4
    trailing = _trailing_uniform("work", median_share, days=LANE_SHARE_TRAILING_DAYS)
    today_share = median_share / LANE_SHARE_OUTLIER_MULTIPLE  # exactly at threshold
    today = {"work": 10_000.0 * today_share, "rest": 10_000.0 * (1 - today_share)}
    outliers = compute_lane_share_outliers(today, trailing)
    assert "work" in outliers


def test_share_within_normal_range_does_not_flag() -> None:
    median_share = 0.2
    trailing = _trailing_uniform("work", median_share, days=LANE_SHARE_TRAILING_DAYS)
    today = {"work": 2_100.0, "rest": 7_900.0}  # share 0.21, well within 2x
    assert "work" not in compute_lane_share_outliers(today, trailing)


def test_dark_lane_is_never_flagged() -> None:
    median_share = 0.1
    trailing = _trailing_uniform("work", median_share, days=LANE_SHARE_TRAILING_DAYS)
    today = {"work": 9_000.0, "rest": 1_000.0}  # wildly above median share
    outliers = compute_lane_share_outliers(today, trailing, dark_lanes=["work"])
    assert "work" not in outliers


def test_lane_with_zero_historical_median_but_nonzero_today_flags() -> None:
    trailing = {
        date(2026, 6, 1) + timedelta(days=i): {"rest": 10_000.0}
        for i in range(LANE_SHARE_TRAILING_DAYS)
    }
    today = {"travel": 5_000.0, "rest": 5_000.0}
    outliers = compute_lane_share_outliers(today, trailing)
    assert "travel" in outliers
    assert outliers["travel"]["median_share"] == 0


def test_low_share_lane_flap_does_not_flag_after_floor() -> None:
    """bu-pcm4t: a ~1%-median lane swinging to ~2.5% clears the symmetric 2x
    ratio but is below the min-share floor in BOTH windows, so it must not flag.

    Fail-before/pass-after in one assertion pair: with ``min_lane_share=0`` (the
    pre-floor behavior) the flap fires; with the floor it is suppressed. The
    ratio genuinely clears 2x, so the floor -- not the ratio -- is what kills it.
    """
    median_share = 0.01  # ~1%
    today_share = 0.025  # ~2.5% -> ratio 2.5x, clears 2x, but both windows sub-floor
    assert today_share / median_share >= LANE_SHARE_OUTLIER_MULTIPLE
    trailing = _trailing_uniform("travel", median_share, days=LANE_SHARE_TRAILING_DAYS)
    today = {"travel": 10_000.0 * today_share, "rest": 10_000.0 * (1 - today_share)}

    # Pre-floor (the bug): the flap fires.
    assert "travel" in compute_lane_share_outliers(today, trailing, min_lane_share=0.0)
    # With the floor: it does not.
    assert "travel" not in compute_lane_share_outliers(today, trailing)


def test_legitimate_high_share_swing_still_flags() -> None:
    """A meaningful lane (10% median) doubling to 25% is above the floor in both
    windows, so the floor leaves it untouched and it still fires."""
    median_share = 0.10
    today_share = 0.25  # ratio 2.5x
    trailing = _trailing_uniform("work", median_share, days=LANE_SHARE_TRAILING_DAYS)
    today = {"work": 10_000.0 * today_share, "rest": 10_000.0 * (1 - today_share)}
    assert "work" in compute_lane_share_outliers(today, trailing)


def test_min_share_floor_only_skips_when_both_windows_sub_floor() -> None:
    """The floor skips a lane only when it is trivial in BOTH windows. A lane
    below the floor in one window but at/above it in the other -- a genuine
    spike or crash -- is still evaluated (symmetric)."""
    # Sub-floor median, spikes to above the floor today (ratio > 2x) -> fires.
    low_median = MIN_LANE_SHARE_FLOOR / 2  # 2%, below floor
    spike_today = MIN_LANE_SHARE_FLOOR + 0.02  # 6%, above floor; ratio 3x
    trailing_spike = _trailing_uniform("travel", low_median, days=LANE_SHARE_TRAILING_DAYS)
    today_spike = {"travel": 10_000.0 * spike_today, "rest": 10_000.0 * (1 - spike_today)}
    assert "travel" in compute_lane_share_outliers(today_spike, trailing_spike)

    # Above-floor median, crashes to sub-floor today (ratio < 1/2x) -> fires.
    high_median = MIN_LANE_SHARE_FLOOR + 0.06  # 10%, above floor
    crash_today = high_median / 3  # ~3.3%, below floor; ratio 1/3
    trailing_crash = _trailing_uniform("work", high_median, days=LANE_SHARE_TRAILING_DAYS)
    today_crash = {"work": 10_000.0 * crash_today, "rest": 10_000.0 * (1 - crash_today)}
    assert "work" in compute_lane_share_outliers(today_crash, trailing_crash)


# ---------------------------------------------------------------------------
# evaluate_and_write_daily_flags (mocked storage)
# ---------------------------------------------------------------------------


class _FakeStorage:
    """Minimal in-memory fake for the storage calls flags.py performs,
    monkeypatched onto the `butlers.chronicler.flags` module namespace
    (the functions are imported there via `from ... import`).

    Every source in ``SOURCE_CRON_MINUTES`` defaults to healthy (active,
    fresh checkpoint) unless named in ``dark_source_names`` — tests only need
    to name the source(s) they want dark, not restate every other source's
    health.
    """

    def __init__(
        self,
        *,
        rollups_by_date: dict[date, dict[str, float]] | None = None,
        dark_source_names: set[str] | None = None,
        routines: list[Routine] | None = None,
        occupation_blocks_exist: bool = False,
    ) -> None:
        self.rollups_by_date = rollups_by_date or {}
        self.dark_source_names = dark_source_names or set()
        self.routines = routines or []
        self.occupation_blocks_exist = occupation_blocks_exist
        self.upserted: list[dict[str, Any]] = []
        self.deleted: list[dict[str, Any]] = []

    async def list_daily_rollups(self, pool, *, local_date):
        totals = self.rollups_by_date.get(local_date, {})
        return [
            DailyRollup(local_date=local_date, lane=lane, seconds=int(seconds))
            for lane, seconds in totals.items()
        ]

    async def get_source_state(self, pool, source_name):
        return _state(active=source_name not in self.dark_source_names)

    async def get_checkpoint(self, pool, source_name):
        if source_name in self.dark_source_names:
            return None
        return _checkpoint(last_success_at=_NOW - timedelta(minutes=1))

    async def list_routines(self, pool, *, enabled_only=False):
        return self.routines

    async def list_episodes(self, pool, **kwargs):
        return [object()] if self.occupation_blocks_exist else []

    async def upsert_daily_rollup_flag(self, pool, *, local_date, flag_type, severity, detail):
        self.upserted.append(
            {
                "local_date": local_date,
                "flag_type": flag_type,
                "severity": severity,
                "detail": detail,
            }
        )

    async def delete_daily_rollup_flag(self, pool, *, local_date, flag_type):
        self.deleted.append({"local_date": local_date, "flag_type": flag_type})


def _patch_storage(monkeypatch, fake: _FakeStorage) -> None:
    monkeypatch.setattr("butlers.chronicler.flags.list_daily_rollups", fake.list_daily_rollups)
    monkeypatch.setattr("butlers.chronicler.flags.get_source_state", fake.get_source_state)
    monkeypatch.setattr("butlers.chronicler.flags.get_checkpoint", fake.get_checkpoint)
    monkeypatch.setattr("butlers.chronicler.flags.list_routines", fake.list_routines)
    monkeypatch.setattr("butlers.chronicler.flags.list_episodes", fake.list_episodes)
    monkeypatch.setattr(
        "butlers.chronicler.flags.upsert_daily_rollup_flag", fake.upsert_daily_rollup_flag
    )
    monkeypatch.setattr(
        "butlers.chronicler.flags.delete_daily_rollup_flag", fake.delete_daily_rollup_flag
    )


async def test_healthy_day_with_zero_sleep_flags_sleep_missing_only(monkeypatch) -> None:
    local_date = date(2026, 7, 4)
    fake = _FakeStorage(rollups_by_date={local_date: {"sleep": 0, "work": 100}})
    _patch_storage(monkeypatch, fake)

    result = await evaluate_and_write_daily_flags(
        AsyncMock(), local_date=local_date, timezone="Asia/Singapore", now=_NOW
    )

    assert result["flags"][FLAG_SLEEP_MISSING] is True
    assert result["flags"][FLAG_FEEDER_DARK] is False
    upserted_types = {u["flag_type"] for u in fake.upserted}
    assert upserted_types == {FLAG_SLEEP_MISSING}
    # Every other managed flag_type must be reconciled away (deleted), not
    # silently left absent, so a previously-flagged day converges correctly.
    deleted_types = {d["flag_type"] for d in fake.deleted}
    assert deleted_types == {FLAG_FEEDER_DARK, FLAG_ROUTINE_BREAK, FLAG_LANE_SHARE_OUTLIER}


async def test_feeder_dark_suppresses_sleep_missing(monkeypatch) -> None:
    """The required feeder_dark-suppresses-derived-flags case: a dark
    google_health.measurements feeder must produce feeder_dark, but must not
    also fabricate sleep_missing from the resulting zero seconds."""
    local_date = date(2026, 7, 4)
    fake = _FakeStorage(
        rollups_by_date={local_date: {"sleep": 0, "work": 100}},
        dark_source_names={"google_health.measurements"},
    )
    _patch_storage(monkeypatch, fake)

    result = await evaluate_and_write_daily_flags(
        AsyncMock(), local_date=local_date, timezone="Asia/Singapore", now=_NOW
    )

    assert result["flags"][FLAG_FEEDER_DARK] is True
    assert result["flags"][FLAG_SLEEP_MISSING] is False
    assert result["dark_sources"] == ["google_health.measurements"]

    upserted_by_type = {u["flag_type"]: u for u in fake.upserted}
    assert set(upserted_by_type) == {FLAG_FEEDER_DARK}
    assert upserted_by_type[FLAG_FEEDER_DARK]["detail"] == {
        "dark_sources": ["google_health.measurements"]
    }
    # sleep_missing must be actively deleted (reconciled away), not just
    # never-upserted, in case a prior run had flagged it.
    deleted_types = {d["flag_type"] for d in fake.deleted}
    assert FLAG_SLEEP_MISSING in deleted_types


async def test_feeder_dark_suppresses_routine_break(monkeypatch) -> None:
    local_date = date(2026, 7, 6)  # Monday
    assert local_date.weekday() == 0
    routine = Routine(
        dow_mask=1 << 0,
        window_start_local=time(9, 0),
        window_end_local=time(17, 0),
        label="weekday desk block",
        timezone="Asia/Singapore",
        id=uuid4(),
    )
    fake = _FakeStorage(
        rollups_by_date={local_date: {"work": 0}},
        dark_source_names={"chronicler.occupation_inferred"},
        routines=[routine],
        occupation_blocks_exist=False,
    )
    _patch_storage(monkeypatch, fake)

    result = await evaluate_and_write_daily_flags(
        AsyncMock(), local_date=local_date, timezone="Asia/Singapore", now=_NOW
    )

    assert result["flags"][FLAG_ROUTINE_BREAK] is False
    assert result["flags"][FLAG_FEEDER_DARK] is True


async def test_routine_break_fires_when_healthy_and_no_block(monkeypatch) -> None:
    local_date = date(2026, 7, 6)  # Monday
    routine = Routine(
        dow_mask=1 << 0,
        window_start_local=time(9, 0),
        window_end_local=time(17, 0),
        label="weekday desk block",
        timezone="Asia/Singapore",
        id=uuid4(),
    )
    fake = _FakeStorage(
        rollups_by_date={local_date: {"work": 0}},
        routines=[routine],
        occupation_blocks_exist=False,
    )
    _patch_storage(monkeypatch, fake)

    result = await evaluate_and_write_daily_flags(
        AsyncMock(), local_date=local_date, timezone="Asia/Singapore", now=_NOW
    )

    assert result["flags"][FLAG_ROUTINE_BREAK] is True
    routine_flag = next(u for u in fake.upserted if u["flag_type"] == FLAG_ROUTINE_BREAK)
    assert routine_flag["detail"]["routines"][0]["label"] == "weekday desk block"


async def test_no_managed_flags_fire_on_a_fully_healthy_normal_day(monkeypatch) -> None:
    local_date = date(2026, 7, 4)
    fake = _FakeStorage(rollups_by_date={local_date: {"sleep": 1800, "work": 100}})
    _patch_storage(monkeypatch, fake)

    result = await evaluate_and_write_daily_flags(
        AsyncMock(), local_date=local_date, timezone="Asia/Singapore", now=_NOW
    )

    assert all(not fired for fired in result["flags"].values())
    assert fake.upserted == []
    assert {d["flag_type"] for d in fake.deleted} == {
        FLAG_FEEDER_DARK,
        FLAG_SLEEP_MISSING,
        FLAG_ROUTINE_BREAK,
        FLAG_LANE_SHARE_OUTLIER,
    }


# ---------------------------------------------------------------------------
# SOURCE_CRON_MINUTES vs roster/chronicler/butler.toml parity
# ---------------------------------------------------------------------------
#
# SOURCE_CRON_MINUTES (flags.py) is a hand-maintained mirror of the actual
# cron cadence declared in butler.toml (see flags.py's `[decision]` comment
# above the map) — no structured per-source schedule registry exists for
# `is_source_dark` to read instead. A drifted entry is silent: it produces
# either a false `feeder_dark` (map says a job runs more often than it does)
# or a missed real outage (map says less often), with no other signal to
# catch it. This asserts the map against the real TOML cron for every
# project-adapter job the four flag rules depend on, so a cadence edit to
# butler.toml without a matching flags.py edit fails CI instead of silently
# drifting.

_REPO_ROOT = Path(__file__).parent.parent.parent
_CHRONICLER_ROSTER_DIR = _REPO_ROOT / "roster" / "chronicler"

# job_name (butler.toml `[[butler.schedule]]` entry) -> the `source_name`
# its adapter projects to (see `jobs.py`'s `run_project_*` handlers for the
# job -> adapter wiring, and each `adapters/*.py`'s `SOURCE_NAME` constant
# for the adapter -> source_name mapping). Hand-maintained same as
# SOURCE_CRON_MINUTES itself — this test's job is to keep the *cron* value
# honest, not to derive the mapping structurally.
_FLAG_DEPENDENT_JOB_TO_SOURCE: dict[str, str] = {
    "chronicler_project_sessions": "core.sessions",
    "chronicler_project_spotify": "spotify.session_summary",
    "chronicler_project_steam": "steam.play_history",
    "chronicler_project_owntracks": "owntracks.points",
    "chronicler_project_owntracks_place_cluster": "owntracks.place_cluster",
    "chronicler_project_google_health_sleep": "google_health.measurements",
    "chronicler_project_meals": "health.meals",
    "chronicler_project_home_assistant": "home_assistant.history",
    "chronicler_project_home_assistant_sensor_activity": "home_assistant.sensor_activity",
    "chronicler_project_focus_inferred": "chronicler.focus_inferred",
    "chronicler_project_reading_inferred": "chronicler.reading_inferred",
    "chronicler_project_exercise_inferred": "chronicler.exercise_inferred",
    "chronicler_project_comms": "comms.message_bursts",
    "chronicler_project_activitywatch": "activitywatch.window",
    "chronicler_project_occupation_inferred": "chronicler.occupation_inferred",
}


def _cron_interval_minutes(cron: str) -> int:
    """Minutes between two consecutive fires of *cron*.

    Delegates to `croniter` rather than hand-parsing `*/N * * * *` vs
    `N * * * *` forms — robust to any cron shape butler.toml might use.
    """
    anchor = datetime(2026, 1, 5, 0, 0, 0)  # arbitrary Monday 00:00
    it = croniter.croniter(cron, anchor)
    first = it.get_next(datetime)
    second = it.get_next(datetime)
    return int((second - first).total_seconds() // 60)


def test_source_cron_minutes_covers_every_flag_dependent_source() -> None:
    """Guards the guard below: every `SOURCE_CRON_MINUTES` key must be
    exercised by `_FLAG_DEPENDENT_JOB_TO_SOURCE`, or a newly-added source
    could drift from butler.toml without this parity check ever seeing it."""
    assert set(SOURCE_CRON_MINUTES) == set(_FLAG_DEPENDENT_JOB_TO_SOURCE.values())


def test_source_cron_minutes_matches_butler_toml_cadence() -> None:
    config = load_config(_CHRONICLER_ROSTER_DIR)
    cron_by_job_name = {s.job_name: s.cron for s in config.schedules if s.job_name}

    missing_jobs = set(_FLAG_DEPENDENT_JOB_TO_SOURCE) - set(cron_by_job_name)
    assert not missing_jobs, (
        f"job_name(s) no longer declared in roster/chronicler/butler.toml: {missing_jobs}"
    )

    mismatches = {
        source_name: {
            "source_cron_minutes": SOURCE_CRON_MINUTES[source_name],
            "butler_toml_minutes": _cron_interval_minutes(cron_by_job_name[job_name]),
        }
        for job_name, source_name in _FLAG_DEPENDENT_JOB_TO_SOURCE.items()
        if SOURCE_CRON_MINUTES[source_name] != _cron_interval_minutes(cron_by_job_name[job_name])
    }
    assert not mismatches, (
        "SOURCE_CRON_MINUTES (flags.py) has drifted from the actual cron in "
        f"roster/chronicler/butler.toml: {mismatches}"
    )
