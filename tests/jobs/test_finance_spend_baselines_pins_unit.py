"""Pins for two behaviours of the category spend bands that the broader unit suite left open.

- The scan marks a category finding ``high`` at exactly ``2 * k_mad`` MAD units and ``medium``
  just below it.
- The briefing scores week-to-date spend in the owner's briefing zone.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

from butlers.jobs import finance_spend_baselines as fsb
from butlers.jobs.briefing import run_finance_briefing_contribution

_NOW = datetime(2026, 4, 29, 12, 0, tzinfo=UTC)
_SCAN = "butlers.tools.finance.anomaly_detection"


def _finding(mad_units: float) -> SimpleNamespace:
    metric = fsb.spend_metric("dining", "SGD")
    return SimpleNamespace(
        category="dining",
        currency="SGD",
        week_spend=500.0,
        metric=metric,
        is_anomaly=True,
        usual_range=(80.0, 200.0),
        deviation=SimpleNamespace(mad_units=mad_units),
    )


async def _scan_severity(mad_units: float) -> str:
    from butlers.tools.finance import anomaly_detection as ad

    pool = MagicMock()
    recent = {
        "id": "t1",
        "merchant": "Cafe",
        "amount": 12.0,
        "currency": "SGD",
        "posted_at": _NOW - timedelta(days=1),
        "category": "dining",
    }
    pool.fetch = AsyncMock(side_effect=[[recent], []])
    with (
        patch(f"{_SCAN}._has_deleted_at", AsyncMock(return_value=False)),
        patch(
            f"{_SCAN}.compute_baselines",
            AsyncMock(return_value={"status": "ok", "merchant_baselines": []}),
        ),
        patch(f"{_SCAN}.resolve_budget_zone", AsyncMock(return_value=UTC)),
        patch(f"{_SCAN}.score_week_to_date", AsyncMock(return_value=[_finding(mad_units)])),
        patch(f"{_SCAN}.finding_evidence", lambda finding: {}),
        patch(f"{_SCAN}.usual_range_words", lambda finding: "usual range"),
    ):
        result = await ad.anomaly_scan(pool, now=_NOW)
    (velocity,) = [a for a in result["anomalies"] if a["type"] == "category_velocity_anomaly"]
    return velocity["severity"]


async def test_severity_is_high_at_exactly_twice_k_mad() -> None:
    k_mad = fsb.spend_metric("dining", "SGD").k_mad
    assert await _scan_severity(2 * k_mad) == "high"


async def test_severity_is_medium_just_below_twice_k_mad() -> None:
    k_mad = fsb.spend_metric("dining", "SGD").k_mad
    assert await _scan_severity(2 * k_mad - 0.01) == "medium"


async def test_briefing_scores_week_to_date_in_the_owner_briefing_zone() -> None:
    zone = ZoneInfo("America/New_York")
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetchval = AsyncMock(return_value=False)
    pool.execute = AsyncMock(return_value="OK")
    score = AsyncMock(return_value=[])
    with (
        patch("butlers.jobs.briefing.owner_briefing_zone", AsyncMock(return_value=zone)),
        patch("butlers.jobs.briefing.score_week_to_date", score),
        patch("butlers.jobs.briefing._write_contribution", AsyncMock()),
    ):
        await run_finance_briefing_contribution(pool, None)

    score.assert_awaited_once()
    assert score.await_args.kwargs["zone"] is zone
    assert score.await_args.args == (pool,)
