"""Read side of the personal-baseline store (bu-q7vx1q.77); synthetic rows only."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest

from butlers.core.baselines import (
    Baseline,
    BaselineMetric,
    BaselineState,
    read_baseline,
    read_baselines,
    reading_from_baseline,
    score,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 10, 4, 6, tzinfo=UTC)

_METRIC = BaselineMetric(
    key="resting_hr",
    unit="bpm",
    bucket="day",
    window_days=60,
    min_n=14,
    min_coverage=0.5,
    k_mad=2.6,
    k_consecutive=3,
    concern="above",
    min_dispersion=1.0,
    expected_signal_key="resting_hr",
)

_READY = Baseline(
    metric_key="resting_hr",
    bucket_value="all",
    method_version="mad-v1",
    status="ready",
    measurability="measurable",
    unit="bpm",
    center=56.0,
    dispersion=2.0,
    n_observed=54,
    n_expected=60,
    coverage=0.9,
    k_mad=2.6,
    input_digest="d",
    computed_at=_NOW - timedelta(hours=5),
)


def _row(baseline: Baseline) -> dict[str, Any]:
    return {
        "metric_key": baseline.metric_key,
        "bucket_value": baseline.bucket_value,
        "method_version": baseline.method_version,
        "status": baseline.status,
        "measurability": baseline.measurability,
        "unit": baseline.unit,
        "center": baseline.center,
        "dispersion": baseline.dispersion,
        "k_mad": baseline.k_mad,
        "n_observed": baseline.n_observed,
        "n_expected": baseline.n_expected,
        "coverage": baseline.coverage,
        "input_digest": baseline.input_digest,
        "computed_at": baseline.computed_at,
    }


def _pool(*rows: Baseline) -> AsyncMock:
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[_row(b) for b in rows])
    return pool


def test_ready_baseline_reads_band_and_counts() -> None:
    reading = reading_from_baseline(_READY, now=_NOW)
    assert reading.state == "ready"
    assert reading.band == (56.0 - 2.6 * 2.0, 56.0 + 2.6 * 2.0)
    assert (reading.n_observed, reading.n_expected, reading.coverage) == (54, 60, 0.9)
    assert reading.stale is False
    assert reading.as_dict()["band"] == {"low": pytest.approx(50.8), "high": pytest.approx(61.2)}


def test_insufficient_history_has_no_band() -> None:
    thin = replace(_READY, status="insufficient_history", center=None, dispersion=None)
    reading = reading_from_baseline(thin, now=_NOW)
    assert reading.state == "insufficient_history"
    assert reading.band is None and reading.center is None
    assert reading.as_dict()["band"] is None


def test_unmeasurable_has_no_band() -> None:
    gap = replace(_READY, status="insufficient_history", measurability="unmeasurable")
    reading = reading_from_baseline(gap, now=_NOW)
    assert reading.state == "unmeasurable"
    assert reading.band is None


def test_stale_beyond_twice_the_refresh_interval_has_no_band() -> None:
    old = replace(_READY, computed_at=_NOW - timedelta(days=2, minutes=1))
    reading = reading_from_baseline(old, now=_NOW)
    assert reading.state == "stale"
    assert reading.stale is True
    assert reading.band is None
    assert reading.center is None
    assert reading.computed_at == old.computed_at


def test_exactly_twice_the_interval_is_still_fresh() -> None:
    edge = replace(_READY, computed_at=_NOW - timedelta(days=2))
    assert reading_from_baseline(edge, now=_NOW).state == "ready"


def test_custom_refresh_interval_moves_the_stale_edge() -> None:
    aged = replace(_READY, computed_at=_NOW - timedelta(days=3))
    assert reading_from_baseline(aged, now=_NOW).state == "stale"
    assert (
        reading_from_baseline(aged, now=_NOW, refresh_interval=timedelta(days=2)).state == "ready"
    )


def test_unmeasurable_outranks_stale_and_stale_outranks_thin_history() -> None:
    both = replace(
        _READY,
        measurability="unmeasurable",
        status="insufficient_history",
        computed_at=_NOW - timedelta(days=9),
    )
    assert reading_from_baseline(both, now=_NOW).state == "unmeasurable"
    thin_old = replace(
        _READY, status="insufficient_history", center=None, computed_at=_NOW - timedelta(days=9)
    )
    assert reading_from_baseline(thin_old, now=_NOW).state == "stale"


@pytest.mark.parametrize(
    "variant",
    [
        {},
        {"status": "insufficient_history", "center": None, "dispersion": None},
        {"measurability": "unmeasurable", "status": "insufficient_history"},
        {"computed_at": _NOW - timedelta(days=5)},
        {"computed_at": _NOW - timedelta(days=5), "measurability": "unmeasurable"},
    ],
)
def test_read_state_agrees_with_the_scorer(variant: dict[str, Any]) -> None:
    """The tab's words and the deviation notice never disagree about the same row."""
    baseline = replace(_READY, **variant)
    scored = score(_METRIC, 56.0, baseline, now=_NOW).state
    read = reading_from_baseline(baseline, now=_NOW).state
    expected = {
        BaselineState.UNMEASURABLE: "unmeasurable",
        BaselineState.STALE: "stale",
        BaselineState.INSUFFICIENT_HISTORY: "insufficient_history",
    }.get(scored, "ready")
    assert read == expected


async def test_read_baseline_without_a_row_is_a_typed_no_baseline() -> None:
    reading = await read_baseline(_pool(), "nonexistent", now=_NOW)
    assert reading.state == "no_baseline"
    assert reading.band is None
    assert reading.metric_key == "nonexistent"
    assert reading.computed_at is None
    assert reading.stale is False
    assert reading.as_dict()["n_observed"] == 0


async def test_read_baselines_returns_one_reading_per_key_in_request_order() -> None:
    hrv = replace(_READY, metric_key="hrv", unit="ms", center=40.0, dispersion=4.0)
    pool = _pool(hrv, _READY)
    readings = await read_baselines(pool, ["resting_hr", "sleep_duration", "hrv"], now=_NOW)
    assert [r.metric_key for r in readings] == ["resting_hr", "sleep_duration", "hrv"]
    assert [r.state for r in readings] == ["ready", "no_baseline", "ready"]


async def test_read_baselines_applies_each_keys_own_refresh_interval() -> None:
    weekly = replace(_READY, computed_at=_NOW - timedelta(days=5))
    pool = _pool(weekly)
    (daily,) = await read_baselines(pool, ["resting_hr"], now=_NOW)
    (slow,) = await read_baselines(
        pool, ["resting_hr"], now=_NOW, refresh_intervals={"resting_hr": timedelta(days=7)}
    )
    assert daily.state == "stale"
    assert slow.state == "ready"


async def test_read_baselines_with_no_keys_does_not_touch_the_pool() -> None:
    pool = _pool()
    assert await read_baselines(pool, [], now=_NOW) == []
    pool.fetch.assert_not_called()


async def test_read_is_a_single_select_over_the_callers_unqualified_table() -> None:
    pool = _pool(_READY)
    await read_baselines(pool, ["resting_hr"], now=_NOW)
    pool.fetch.assert_awaited_once()
    sql = pool.fetch.await_args.args[0]
    assert "FROM metric_baselines" in sql
    assert "ORDER BY metric_key, computed_at DESC" in sql
    upper = sql.upper()
    assert "INSERT" not in upper and "UPDATE" not in upper and "DELETE" not in upper
    assert "." not in sql.split("FROM metric_baselines")[1].split()[0]
