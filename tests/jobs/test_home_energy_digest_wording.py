"""What the energy digest may claim, path by path (bu-q7vx1q.78).

"Within normal range" means the house total sits inside its own ready baseline band; thin
history, a total above its band and any device anomaly each read differently. Every case drives
``run_energy_digest`` and reads the message it sends.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from butlers.jobs.home import run_energy_digest
from butlers.jobs.home_baselines import anomalies_from, scored_week_anchor, total_is_within
from tests.jobs.test_home_energy_digest import _finding, _make_energy_row, _make_pool

pytestmark = pytest.mark.unit

_NORMAL = "Energy usage within normal range this week."
_TOTAL = "energy.total"
_HVAC = "energy.device:sensor.hvac_energy"
_STEADY = [100.0, 102.0, 98.0, 101.0, 99.0, 100.0]
_FROZEN = datetime(2026, 10, 19, 6, 40, 12, tzinfo=UTC)


class _Frozen(datetime):
    @classmethod
    def now(cls, tz=None):
        return _FROZEN.astimezone(tz) if tz else _FROZEN


def _stats() -> dict:
    return {
        "available": True,
        "statistics": {"sensor.hvac_energy": {"weekly_sum": 120.0}},
        "unsupported_entity_ids": [],
    }


async def _digest(findings, *, score_side_effect=None):
    pool = _make_pool(
        snapshot_count=1,
        snapshot_rows=[_make_energy_row("sensor.hvac_energy", "100", "HVAC Energy")],
    )
    score = AsyncMock(return_value=(findings, False), side_effect=score_side_effect)
    history = AsyncMock(return_value={})
    retire = AsyncMock(return_value=0)
    with (
        patch("butlers.jobs.home._send_notify", new_callable=AsyncMock) as notify,
        patch(
            "butlers.jobs.home.resolve_owner_entity_info",
            new_callable=AsyncMock,
            return_value="value",
        ),
        patch(
            "butlers.jobs.home._fetch_weekly_statistics",
            new_callable=AsyncMock,
            return_value=_stats(),
        ),
        patch("butlers.jobs.home.fetch_weekly_history", history),
        patch("butlers.jobs.home.score_energy_week", score),
        patch("butlers.jobs.home.retire_prose_energy_baselines", retire),
        patch("butlers.jobs.home.store_fact", new_callable=AsyncMock),
        patch("butlers.jobs.home.datetime", _Frozen),
    ):
        try:
            await run_energy_digest(pool, None)
        finally:
            message = notify.await_args.args[1] if notify.await_args else None
    return {
        "pool": pool,
        "message": message,
        "notify": notify,
        "score": score,
        "history": history,
        "retire": retire,
    }


def _device(kwh: float, history: list[float]):
    return _finding(_HVAC, "sensor.hvac_energy", "HVAC Energy", kwh, history)


async def test_thin_history_lists_the_total_and_never_calls_the_week_normal() -> None:
    out = await _digest(
        [_finding(_TOTAL, None, "House total", 120.0, [100.0, 101.0]), _device(120.0, [100.0])]
    )

    assert (
        "Not enough weeks yet to tell what is usual for: House total, HVAC Energy."
        in (out["message"])
    )
    assert _NORMAL not in out["message"]


async def test_a_total_above_its_band_is_not_normal_even_when_no_device_is_flagged() -> None:
    out = await _digest(
        [
            _finding(_TOTAL, None, "House total", 175.0, _STEADY),
            _device(100.0, [100.0, 101.0, 99.0, 100.0, 100.0]),
        ]
    )

    assert "Total: 120.0 kWh (+20.0% vs baseline)" in out["message"]
    assert "Anomaly alerts" not in out["message"]
    assert _NORMAL not in out["message"]


async def test_a_total_within_its_band_with_no_anomaly_is_normal() -> None:
    out = await _digest(
        [
            _finding(_TOTAL, None, "House total", 101.0, _STEADY),
            _device(100.0, [100.0, 101.0, 99.0, 100.0, 100.0]),
        ]
    )

    assert _NORMAL in out["message"]
    assert "Not enough weeks yet" not in out["message"]


async def test_a_device_anomaly_is_reported_with_its_usual_range_and_is_not_normal() -> None:
    out = await _digest(
        [
            _finding(_TOTAL, None, "House total", 101.0, _STEADY),
            _device(300.0, [100.0, 101.0, 99.0, 100.0, 100.0]),
        ]
    )

    assert "HVAC Energy" in out["message"] and "usual 9" in out["message"]
    assert "more than double its baseline" in out["message"]
    assert _NORMAL not in out["message"]


async def test_a_total_below_its_band_is_not_called_normal() -> None:
    out = await _digest([_finding(_TOTAL, None, "House total", 10.0, _STEADY), _device(10.0, [])])

    assert _NORMAL not in out["message"]


async def test_the_trend_is_measured_against_the_totals_own_usual_week() -> None:
    out = await _digest([_finding(_TOTAL, None, "House total", 120.0, _STEADY)])

    assert "(+20.0% vs baseline)" in out["message"]


async def test_the_old_prose_baseline_facts_are_retired_on_every_run() -> None:
    out = await _digest([_finding(_TOTAL, None, "House total", 101.0, _STEADY)])

    out["retire"].assert_awaited_once_with(out["pool"])


async def test_the_scored_week_is_the_trailing_week_that_ends_at_the_run_hour() -> None:
    out = await _digest([_finding(_TOTAL, None, "House total", 101.0, _STEADY)])

    end = _FROZEN.replace(minute=0, second=0, microsecond=0)
    assert out["history"].await_args.kwargs["end"] == end
    assert out["score"].await_args.kwargs["as_of"] == scored_week_anchor(end)
    assert scored_week_anchor(end).isoformat() == "2026-10-12"


async def test_a_baseline_store_failure_fails_the_digest_closed() -> None:
    with pytest.raises(RuntimeError, match="db down"):
        await _digest(
            [_finding(_TOTAL, None, "House total", 101.0, _STEADY)],
            score_side_effect=RuntimeError("db down"),
        )


def test_severity_turns_high_exactly_at_double_the_usual_centre() -> None:
    steady = [20.0, 20.0, 20.0, 20.0, 20.0, 20.0]
    center = _device(20.0, steady).baseline.center

    below = anomalies_from([_device(2 * center - 0.01, steady)])
    exact = anomalies_from([_device(2 * center, steady)])

    assert [a["severity"] for a in below] == ["anomaly"]
    assert [a["severity"] for a in exact] == ["high"]


def test_only_a_ready_within_total_counts_as_normal() -> None:
    within = _finding(_TOTAL, None, "House total", 101.0, _STEADY)
    thin = _finding(_TOTAL, None, "House total", 101.0, [100.0])
    above = _finding(_TOTAL, None, "House total", 400.0, _STEADY)
    device_only = _device(100.0, _STEADY)

    assert total_is_within([within, device_only]) is True
    assert total_is_within([thin]) is False
    assert total_is_within([above]) is False
    assert total_is_within([device_only]) is False
    assert total_is_within([]) is False
