"""Unit coverage for the Home energy week-bucket baselines (bu-q7vx1q.78, synthetic values)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.connectors.home_assistant_statistics import HAStatisticsError
from butlers.core.baselines import BaselineState, compute_baselines, score
from butlers.jobs.home_baselines import (
    HISTORY_WEEKS,
    TOTAL_KEY,
    EnergyFinding,
    _total_history,
    anomalies_from,
    energy_metric,
    fetch_weekly_history,
    scored_week_anchor,
    unjudged_names,
    usual_total_kwh,
    weekly_totals,
)

pytestmark = pytest.mark.unit

_END = datetime(2026, 10, 19, 6, tzinfo=UTC)
_ANCHOR = scored_week_anchor(_END)  # 2026-10-12


def _hours(week: int, *, per_hour: float, skip: int = 0) -> list[dict]:
    """Hourly buckets for window ``week`` (1 = the week before the scored one)."""
    start = _END - timedelta(days=7 * (week + 1))
    return [
        {"start": (start + timedelta(hours=h)).timestamp() * 1000, "change": per_hour}
        for h in range(168 - skip)
    ]


def test_the_metric_is_a_four_week_minimum_week_bucket_above_only() -> None:
    metric = energy_metric(TOTAL_KEY)

    assert (metric.bucket, metric.min_n, metric.concern, metric.unit) == ("week", 4, "above", "kWh")
    assert metric.window_days == HISTORY_WEEKS * 7
    assert metric.expected_signal_key == "home:baseline:energy.total"


def test_each_complete_week_is_summed_under_its_start_date() -> None:
    series = _hours(1, per_hour=0.1) + _hours(2, per_hour=0.2)

    totals = weekly_totals(series, end=_END)

    assert totals == {
        _ANCHOR - timedelta(days=7): pytest.approx(16.8),
        _ANCHOR - timedelta(days=14): pytest.approx(33.6),
    }


def test_the_scored_week_itself_is_not_history() -> None:
    assert weekly_totals(_hours(0, per_hour=0.1), end=_END) == {}


def test_a_boundary_bucket_belongs_to_the_week_that_starts_there() -> None:
    boundary = _END - timedelta(days=7)  # first hour of the scored week, last of none before
    series = [{"start": boundary.isoformat(), "change": 5.0}]

    assert weekly_totals(series, end=_END) == {}


def test_a_week_with_a_recorder_gap_is_left_out_not_understated() -> None:
    series = _hours(1, per_hour=0.1, skip=40) + _hours(2, per_hour=0.1)

    totals = weekly_totals(series, end=_END)

    assert list(totals) == [_ANCHOR - timedelta(days=14)]


@pytest.mark.parametrize("series", [None, [], "x", [{"start": "bad", "change": 1.0}]])
def test_unusable_series_have_no_history(series: object) -> None:
    assert weekly_totals(series, end=_END) == {}


def test_iso_and_second_epoch_starts_are_understood() -> None:
    start = _END - timedelta(days=10)
    iso = [{"start": (start + timedelta(hours=h)).isoformat(), "change": 0.1} for h in range(168)]
    secs = [{"start": (start + timedelta(hours=h)).timestamp(), "change": 0.1} for h in range(168)]

    assert weekly_totals(iso, end=_END) == weekly_totals(secs, end=_END)


async def test_an_unreachable_history_is_empty_so_every_band_reports_thin_history() -> None:
    client = MagicMock()
    client.get_statistics = AsyncMock(side_effect=HAStatisticsError("timeout", scope="command"))

    with patch("butlers.jobs.home_baselines.HAStatisticsClient", return_value=client):
        history = await fetch_weekly_history(["sensor.a"], ha_url="u", ha_token="t", end=_END)

    assert history == {}


async def test_the_history_request_spans_nine_weeks_of_hourly_change() -> None:
    client = MagicMock()
    client.get_statistics = AsyncMock(return_value={"sensor.a": _hours(1, per_hour=0.1)})

    with patch("butlers.jobs.home_baselines.HAStatisticsClient", return_value=client):
        history = await fetch_weekly_history(["sensor.a"], ha_url="u", ha_token="t", end=_END)

    kwargs = client.get_statistics.await_args.kwargs
    assert kwargs["period"] == "hour" and tuple(kwargs["types"]) == ("change",)
    assert datetime.fromisoformat(kwargs["end"]) - datetime.fromisoformat(
        kwargs["start"]
    ) == timedelta(days=63)
    assert list(history["sensor.a"]) == [_ANCHOR - timedelta(days=7)]


def test_the_house_total_history_keeps_only_weeks_every_device_has() -> None:
    week_a, week_b = _ANCHOR - timedelta(days=7), _ANCHOR - timedelta(days=14)
    history = {"a": {week_a: 5.0, week_b: 6.0}, "b": {week_a: 1.0}}

    assert _total_history(history, ["a", "b"]) == {week_a: 6.0}
    assert _total_history(history, []) == {}


def _finding(key: str, entity_id: str | None, kwh: float, history: list[float]) -> EnergyFinding:
    metric = energy_metric(key)
    samples = {_ANCHOR - timedelta(days=7 * (i + 1)): v for i, v in enumerate(history)}
    now = datetime(2026, 10, 12, 6, tzinfo=UTC)
    (baseline,) = compute_baselines(metric, samples, as_of=_ANCHOR, now=now)
    return EnergyFinding(
        key,
        entity_id,
        entity_id or "House total",
        kwh,
        score(metric, kwh, baseline, now=now),
        baseline,
    )


def test_an_anomaly_carries_the_usual_range_and_grades_double_as_high() -> None:
    steady = [20.0, 22.0, 21.0, 19.0, 23.0, 21.0]
    findings = [
        _finding("energy.device:a", "a", 30.0, steady),
        _finding("energy.device:b", "b", 50.0, steady),
        _finding("energy.device:c", "c", 22.0, steady),
    ]

    by_id = {a["entity_id"]: a for a in anomalies_from(findings)}

    assert set(by_id) == {"a", "b"}
    assert by_id["a"]["severity"] == "anomaly" and by_id["b"]["severity"] == "high"
    assert by_id["a"]["usual_low"] < 21.0 < by_id["a"]["usual_high"]
    assert by_id["a"]["pct_above"] == pytest.approx(42.9, abs=0.1)


def test_thin_history_is_listed_as_not_judged_and_is_never_an_anomaly() -> None:
    findings = [_finding("energy.device:new", "new", 99.0, [10.0, 11.0])]

    assert anomalies_from(findings) == []
    assert unjudged_names(findings) == ["new"]
    assert findings[0].state is BaselineState.INSUFFICIENT_HISTORY


def test_the_house_total_is_never_a_device_anomaly_and_gives_the_trend_baseline() -> None:
    steady = [100.0, 102.0, 98.0, 101.0, 99.0]
    findings = [_finding(TOTAL_KEY, None, 300.0, steady)]

    assert anomalies_from(findings) == []
    assert usual_total_kwh(findings) == pytest.approx(100.0)
    assert usual_total_kwh([_finding(TOTAL_KEY, None, 300.0, [100.0])]) is None
