"""/balance and /trends carry the personal-baseline band and state (bu-q7vx1q.80).

Same mocked-storage seam as ``test_balance_trends_api.py``; field names are unchanged and the
band and state are additions.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from tests.chronicler.test_balance_trends_api import (
    _BALANCE_ENDPOINT,
    _TRENDS_ENDPOINT,
    _build_app,
    _get,
    _rollup,
)

pytestmark = pytest.mark.unit

_DAY = date(2026, 7, 5)
_HOUR = 3600


def _history(lane: str, values: list[int]) -> list:
    return [_rollup(_DAY - timedelta(days=i), lane, v) for i, v in enumerate(values, start=1)]


async def _balance_lane(monkeypatch, rollups, lane="work", **params):
    app = _build_app(monkeypatch, rollups=rollups, flags=[])
    resp = await _get(app, _BALANCE_ENDPOINT, {"date": _DAY.isoformat(), **params})
    assert resp.status_code == 200
    return {row["lane"]: row for row in resp.json()["data"]["lanes"]}[lane]


async def test_balance_carries_the_band_and_a_within_state(monkeypatch: pytest.MonkeyPatch):
    rollups = [_rollup(_DAY, "work", 9 * _HOUR), *_history("work", [8 * _HOUR] * 10)]

    work = await _balance_lane(monkeypatch, rollups)

    assert work["state"] == "within"
    assert work["baseline_seconds"] == 8 * _HOUR
    assert work["baseline_sample_days"] == 10
    assert work["band_low"] < 8 * _HOUR < work["band_high"]
    assert work["band_low"] <= work["seconds"] <= work["band_high"]


async def test_balance_with_thin_history_is_insufficient_not_within(
    monkeypatch: pytest.MonkeyPatch,
):
    rollups = [_rollup(_DAY, "work", 9 * _HOUR), *_history("work", [8 * _HOUR] * 3)]

    work = await _balance_lane(monkeypatch, rollups)

    assert work["state"] == "insufficient_history"
    assert work["baseline_seconds"] is None
    assert work["delta_seconds"] is None
    assert work["band_low"] is None and work["band_high"] is None
    assert work["baseline_sample_days"] == 3


async def test_balance_scores_a_spike_above_and_a_drop_below(monkeypatch: pytest.MonkeyPatch):
    history = _history("work", [8 * _HOUR] * 14)

    spike = await _balance_lane(monkeypatch, [_rollup(_DAY, "work", 18 * _HOUR), *history])
    drop = await _balance_lane(monkeypatch, [_rollup(_DAY, "work", 0), *history])

    assert (spike["state"], drop["state"]) == ("above", "below")


async def test_trends_median_center_is_not_dragged_by_an_earlier_spike(
    monkeypatch: pytest.MonkeyPatch,
):
    values = [8 * _HOUR] * 27
    values.insert(5, 20 * _HOUR)  # one 20h day inside the lookback window
    rollups = [_rollup(_DAY, "work", 8 * _HOUR + 600), *_history("work", values)]
    app = _build_app(monkeypatch, rollups=rollups, flags=[])

    resp = await _get(app, _TRENDS_ENDPOINT, {"window": "week", "end_date": _DAY.isoformat()})

    work = next(s for s in resp.json()["data"]["lanes"] if s["lane"] == "work")
    today = next(d for d in work["days"] if d["local_date"] == _DAY.isoformat())
    assert today["baseline_seconds"] == 8 * _HOUR  # a mean would be above 8h30m
    assert today["state"] == "within"
    # The spike day itself is correctly an anomaly; today, judged against the median, is not.
    flagged = {(x["lane"], x["local_date"]) for x in resp.json()["data"]["anomalies"]}
    assert ("work", _DAY.isoformat()) not in flagged
    assert ("work", (_DAY - timedelta(days=6)).isoformat()) in flagged


async def test_trends_flags_a_drop_as_a_drop_and_unscored_days_carry_no_state(
    monkeypatch: pytest.MonkeyPatch,
):
    rollups = [_rollup(_DAY, "work", 0), *_history("work", [8 * _HOUR] * 14)]
    app = _build_app(monkeypatch, rollups=rollups, flags=[])

    resp = await _get(app, _TRENDS_ENDPOINT, {"window": "month", "end_date": _DAY.isoformat()})

    data = resp.json()["data"]
    (anomaly,) = [
        a for a in data["anomalies"] if a["lane"] == "work" and a["local_date"] == _DAY.isoformat()
    ]
    assert anomaly["direction"] == "drop"
    work = next(s for s in data["lanes"] if s["lane"] == "work")
    unmaterialized = [d for d in work["days"] if d["status"] != "materialized"]
    assert unmaterialized and all(d["state"] is None for d in unmaterialized)


async def test_trends_with_too_few_baseline_days_flags_nothing(monkeypatch: pytest.MonkeyPatch):
    rollups = [_rollup(_DAY, "work", 20 * _HOUR), *_history("work", [_HOUR] * 4)]
    app = _build_app(monkeypatch, rollups=rollups, flags=[])

    resp = await _get(app, _TRENDS_ENDPOINT, {"window": "week", "end_date": _DAY.isoformat()})

    assert resp.json()["data"]["anomalies"] == []


async def test_trends_forwards_a_short_lookback_to_the_scorer(monkeypatch: pytest.MonkeyPatch):
    rollups = [_rollup(_DAY, "work", 20 * _HOUR), *_history("work", [_HOUR] * 3)]
    app = _build_app(monkeypatch, rollups=rollups, flags=[])

    resp = await _get(
        app,
        _TRENDS_ENDPOINT,
        {"window": "week", "end_date": _DAY.isoformat(), "lookback_days": 3},
    )

    flagged = {(a["lane"], a["local_date"]) for a in resp.json()["data"]["anomalies"]}
    assert ("work", _DAY.isoformat()) in flagged
