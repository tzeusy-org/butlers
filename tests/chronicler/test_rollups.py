"""Unit tests for `butlers.chronicler.rollups` (bu-u30as, telemetry-
distillation bead 3).

Covers the pure `compute_daily_lane_rollup` aggregation function and the
`materialize_daily_rollups` async orchestrator's window/upsert wiring
against a mocked pool. Pure-unit tests — no Docker / PostgreSQL required.
The real-Postgres bit-for-bit regression against the live
`aggregate/by-category` endpoint lives in
`tests/integration/test_daily_rollups_integration.py`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest

from butlers.chronicler.aggregations import LANES
from butlers.chronicler.rollups import (
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_TIMEZONE,
    compute_daily_lane_rollup,
    materialize_daily_rollups,
)

pytestmark = pytest.mark.unit

_DAY_START = datetime(2026, 7, 1, 0, 0, 0, tzinfo=UTC)
_DAY_END = datetime(2026, 7, 2, 0, 0, 0, tzinfo=UTC)


def _episode(
    *,
    source_name: str,
    episode_type: str,
    start_at: datetime,
    end_at: datetime | None = None,
    layer: str = "activity",
    trigger_source: str | None = None,
) -> dict[str, Any]:
    return {
        "source_name": source_name,
        "episode_type": episode_type,
        "start_at": start_at,
        "end_at": end_at,
        "layer": layer,
        "trigger_source": trigger_source,
    }


# ---------------------------------------------------------------------------
# compute_daily_lane_rollup
# ---------------------------------------------------------------------------


def test_returns_zero_filled_row_for_every_lane_with_no_episodes() -> None:
    result = compute_daily_lane_rollup([], day_start_utc=_DAY_START, day_end_utc=_DAY_END)
    assert set(result.keys()) == set(LANES)
    for lane, totals in result.items():
        assert totals == {"seconds": 0.0, "episode_count": 0}


def test_single_episode_within_window_counts_toward_its_lane() -> None:
    episodes = [
        _episode(
            source_name="spotify.session_summary",
            episode_type="listening_episode",
            start_at=_DAY_START + timedelta(hours=1),
            end_at=_DAY_START + timedelta(hours=2),
        )
    ]
    result = compute_daily_lane_rollup(episodes, day_start_utc=_DAY_START, day_end_utc=_DAY_END)
    assert result["play"]["seconds"] == pytest.approx(3600.0)
    assert result["play"]["episode_count"] == 1
    # Every other lane stays zero-filled.
    for lane in LANES - {"play"}:
        assert result[lane]["seconds"] == 0.0
        assert result[lane]["episode_count"] == 0


def test_episode_is_clipped_to_the_window_boundary() -> None:
    """An episode spanning midnight only counts the in-window slice."""
    episodes = [
        _episode(
            source_name="spotify.session_summary",
            episode_type="listening_episode",
            start_at=_DAY_START - timedelta(hours=1),
            end_at=_DAY_START + timedelta(hours=1),
        )
    ]
    result = compute_daily_lane_rollup(episodes, day_start_utc=_DAY_START, day_end_utc=_DAY_END)
    assert result["play"]["seconds"] == pytest.approx(3600.0)


def test_open_ended_episode_clips_to_day_end() -> None:
    episodes = [
        _episode(
            source_name="spotify.session_summary",
            episode_type="listening_episode",
            start_at=_DAY_START + timedelta(hours=23),
            end_at=None,
        )
    ]
    result = compute_daily_lane_rollup(episodes, day_start_utc=_DAY_START, day_end_utc=_DAY_END)
    assert result["play"]["seconds"] == pytest.approx(3600.0)


def test_episode_entirely_outside_window_is_dropped() -> None:
    episodes = [
        _episode(
            source_name="spotify.session_summary",
            episode_type="listening_episode",
            start_at=_DAY_END + timedelta(hours=1),
            end_at=_DAY_END + timedelta(hours=2),
        )
    ]
    result = compute_daily_lane_rollup(episodes, day_start_utc=_DAY_START, day_end_utc=_DAY_END)
    assert result["play"]["episode_count"] == 0


def test_intent_layer_episode_never_counts() -> None:
    """Calendar (intent-layer) rows must resolve to no lane — the
    "calendar = 5h" fix aggregations.lane_for_activity already enforces;
    the rollup must inherit that gating, not re-derive it."""
    episodes = [
        _episode(
            source_name="google_calendar.completed",
            episode_type="scheduled_block",
            start_at=_DAY_START + timedelta(hours=1),
            end_at=_DAY_START + timedelta(hours=5),
            layer="intent",
        )
    ]
    result = compute_daily_lane_rollup(episodes, day_start_utc=_DAY_START, day_end_utc=_DAY_END)
    for lane in LANES:
        assert result[lane]["seconds"] == 0.0
        assert result[lane]["episode_count"] == 0


def test_evidence_layer_episode_never_counts() -> None:
    episodes = [
        _episode(
            source_name="home_assistant.sensor_activity",
            episode_type="room_activity_episode",
            start_at=_DAY_START + timedelta(hours=1),
            end_at=_DAY_START + timedelta(hours=2),
            layer="evidence",
        )
    ]
    result = compute_daily_lane_rollup(episodes, day_start_utc=_DAY_START, day_end_utc=_DAY_END)
    assert result["rest"]["episode_count"] == 0


def test_unmapped_source_never_counts() -> None:
    episodes = [
        _episode(
            source_name="some.unknown_source",
            episode_type="mystery_episode",
            start_at=_DAY_START + timedelta(hours=1),
            end_at=_DAY_START + timedelta(hours=2),
        )
    ]
    result = compute_daily_lane_rollup(episodes, day_start_utc=_DAY_START, day_end_utc=_DAY_END)
    for lane in LANES:
        assert result[lane]["episode_count"] == 0


def test_overlapping_same_lane_episodes_union_not_sum() -> None:
    """Two overlapping episodes in the same lane must count the union of
    their spans once, never the sum — same union_seconds contract the live
    endpoint relies on."""
    episodes = [
        _episode(
            source_name="spotify.session_summary",
            episode_type="listening_episode",
            start_at=_DAY_START + timedelta(hours=1),
            end_at=_DAY_START + timedelta(hours=3),
        ),
        _episode(
            source_name="spotify.session_summary",
            episode_type="listening_episode",
            start_at=_DAY_START + timedelta(hours=2),
            end_at=_DAY_START + timedelta(hours=4),
        ),
    ]
    result = compute_daily_lane_rollup(episodes, day_start_utc=_DAY_START, day_end_utc=_DAY_END)
    # Union of [1,3) and [2,4) = [1,4) = 3 hours, not 4 hours summed.
    assert result["play"]["seconds"] == pytest.approx(3 * 3600.0)
    assert result["play"]["episode_count"] == 2


def test_core_sessions_route_trigger_source_resolves_to_conversations_lane() -> None:
    episodes = [
        _episode(
            source_name="core.sessions",
            episode_type="work",
            start_at=_DAY_START + timedelta(hours=1),
            end_at=_DAY_START + timedelta(hours=2),
            trigger_source="route",
        )
    ]
    result = compute_daily_lane_rollup(episodes, day_start_utc=_DAY_START, day_end_utc=_DAY_END)
    assert result["work"]["seconds"] == pytest.approx(3600.0)
    assert result["work"]["episode_count"] == 1


# ---------------------------------------------------------------------------
# materialize_daily_rollups (mocked pool)
# ---------------------------------------------------------------------------


async def test_materialize_rejects_non_positive_lookback_days() -> None:
    with pytest.raises(ValueError, match="lookback_days"):
        await materialize_daily_rollups(AsyncMock(), lookback_days=0)


async def test_materialize_rejects_unknown_timezone() -> None:
    with pytest.raises(ValueError, match="Unknown timezone"):
        await materialize_daily_rollups(AsyncMock(), timezone="Not/AZone")


async def test_materialize_only_processes_fully_elapsed_local_days(monkeypatch) -> None:
    """The current, still-partial local day must never be materialized."""
    upserted_dates: list[date] = []

    async def _fake_upsert(pool, *, local_date, lane, seconds, episode_count, timezone):
        upserted_dates.append(local_date)
        return None

    monkeypatch.setattr("butlers.chronicler.rollups.upsert_daily_rollup", _fake_upsert)

    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=[])

    now = datetime(2026, 7, 5, 8, 0, 0, tzinfo=UTC)
    result = await materialize_daily_rollups(mock_pool, timezone="UTC", lookback_days=3, now=now)

    assert date(2026, 7, 5).isoformat() not in result["days_processed"]
    assert set(result["days_processed"]) == {
        date(2026, 7, 2).isoformat(),
        date(2026, 7, 3).isoformat(),
        date(2026, 7, 4).isoformat(),
    }
    assert date(2026, 7, 5) not in upserted_dates


async def test_materialize_upserts_every_lane_per_processed_day(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    async def _fake_upsert(pool, *, local_date, lane, seconds, episode_count, timezone):
        calls.append(
            {
                "local_date": local_date,
                "lane": lane,
                "seconds": seconds,
                "episode_count": episode_count,
                "timezone": timezone,
            }
        )
        return None

    monkeypatch.setattr("butlers.chronicler.rollups.upsert_daily_rollup", _fake_upsert)

    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=[])

    now = datetime(2026, 7, 2, 8, 0, 0, tzinfo=UTC)
    result = await materialize_daily_rollups(mock_pool, timezone="UTC", lookback_days=1, now=now)

    assert result["days_processed"] == [date(2026, 7, 1).isoformat()]
    assert len(calls) == len(LANES)
    assert {c["lane"] for c in calls} == set(LANES)
    for c in calls:
        assert c["local_date"] == date(2026, 7, 1)
        assert c["seconds"] == 0
        assert c["episode_count"] == 0
        assert c["timezone"] == "UTC"


async def test_materialize_defaults() -> None:
    assert DEFAULT_TIMEZONE == "Asia/Singapore"
    assert DEFAULT_LOOKBACK_DAYS == 7
