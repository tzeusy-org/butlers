"""Unit tests for finance category spend baselines (bu-q7vx1q.79); synthetic values, fake pool."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.baselines import METHOD_VERSION, BaselineState
from butlers.jobs import finance_spend_baselines as fsb
from butlers.jobs.briefing import run_finance_briefing_contribution

# Wednesday 2026-04-29 12:00 UTC: the owner-local week started Monday 2026-04-27.
_NOW = datetime(2026, 4, 29, 12, 0, tzinfo=UTC)
_MONDAY = date(2026, 4, 27)


def _band_row(
    category: str,
    currency: str,
    *,
    center: float | None = 150.0,
    dispersion: float | None = 20.0,
    status: str = "ready",
    computed_at: datetime | None = None,
    n_observed: int = 10,
) -> dict[str, Any]:
    return {
        "metric_key": fsb.spend_key(category, currency),
        "bucket_value": "all",
        "method_version": METHOD_VERSION,
        "status": status,
        "measurability": "measurable",
        "unit": currency,
        "center": center,
        "dispersion": dispersion,
        "k_mad": 2.6,
        "n_observed": n_observed,
        "n_expected": 26,
        "coverage": n_observed / 26,
        "input_digest": "d",
        "computed_at": computed_at or _NOW - timedelta(days=1),
    }


def _spend_row(category: str, currency: str, spend: float, last: datetime) -> dict[str, Any]:
    return {
        "category": category,
        "currency": currency,
        "week_spend": spend,
        "last_posted": last,
    }


class _SpendPool:
    """Answers the three reads the module makes, by SQL text."""

    def __init__(
        self,
        *,
        history: list[dict[str, Any]] | None = None,
        week: list[dict[str, Any]] | None = None,
        bands: list[dict[str, Any]] | None = None,
        facts: list[dict[str, Any]] | None = None,
    ) -> None:
        self.history = history or []
        self.week = week or []
        self.bands = bands or []
        self.facts = facts or []
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchval(self, sql: str, *args: Any) -> bool:
        return False

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.calls.append((sql, args))
        if "FROM metric_baselines" in sql:
            wanted = set(args[0])
            return [row for row in self.bands if row["metric_key"] in wanted]
        if "FROM facts" in sql:
            return self.facts
        if "GROUP BY category, currency" in sql:
            return self.week
        if "FROM transactions" in sql:
            return self.history
        raise AssertionError(f"unexpected SQL: {sql}")


# ---------------------------------------------------------------------------
# Declarations
# ---------------------------------------------------------------------------


def test_metric_declaration_and_week_start_are_weekly_currency_scoped_owner_local() -> None:
    metric = fsb.spend_metric("dining", "SGD")
    assert metric.key == "spend.category:dining:SGD"
    assert (metric.bucket, metric.window_days, metric.min_n) == ("week", 182, 4)
    assert (metric.unit, metric.concern, metric.k_mad) == ("SGD", "above", 2.6)
    assert metric.expected_signal_key == "finance:baseline:spend.category:dining:SGD"
    assert metric.refresh_interval == timedelta(days=7)
    # Two currencies are two metrics; four weeks of spend clear the coverage floor.
    assert fsb.spend_key("dining", "SGD") != fsb.spend_key("dining", "USD")
    assert metric.min_n / (metric.window_days // 7) >= metric.min_coverage

    # Weeks start on the owner-local Monday.
    assert fsb.week_start(_NOW, UTC) == _MONDAY
    assert fsb.week_start(datetime(2026, 5, 3, 23, 59, tzinfo=UTC), UTC) == _MONDAY
    assert fsb.week_start(datetime(2026, 5, 4, 0, 0, tzinfo=UTC), UTC) == date(2026, 5, 4)
    # Sunday 20:00 UTC is already Monday 04:00 in SGT.
    moment = datetime(2026, 5, 3, 20, 0, tzinfo=UTC)
    assert fsb.week_start(moment, timezone(timedelta(hours=8))) == date(2026, 5, 4)


# ---------------------------------------------------------------------------
# Owner-local weeks
# ---------------------------------------------------------------------------


async def test_history_is_bucketed_per_category_currency_and_week() -> None:
    rows = [
        {
            "category": "dining",
            "currency": "SGD",
            "posted_at": _NOW - timedelta(days=9),
            "amount": 10,
        },
        {
            "category": "dining",
            "currency": "SGD",
            "posted_at": _NOW - timedelta(days=8),
            "amount": 5,
        },
        {
            "category": "dining",
            "currency": "USD",
            "posted_at": _NOW - timedelta(days=8),
            "amount": 7,
        },
        {
            "category": "dining",
            "currency": "SGD",
            "posted_at": _NOW - timedelta(days=16),
            "amount": 3,
        },
    ]
    series = await fsb.fetch_weekly_spend(_SpendPool(history=rows), as_of=_MONDAY, zone=UTC)
    assert series[("dining", "SGD")] == {date(2026, 4, 20): 15.0, date(2026, 4, 13): 3.0}
    assert series[("dining", "USD")] == {date(2026, 4, 20): 7.0}

    pool = _SpendPool()
    await fsb.fetch_weekly_spend(pool, as_of=_MONDAY, zone=UTC)
    _, (lower, upper) = pool.calls[0]
    assert upper == datetime(2026, 4, 27, tzinfo=UTC)  # stops before the week in progress
    assert lower == datetime(2025, 10, 27, tzinfo=UTC)  # 26 Mondays ending 2026-04-20


# ---------------------------------------------------------------------------
# Producer
# ---------------------------------------------------------------------------


async def test_refresh_stores_one_floored_band_per_category_currency() -> None:
    def _weeks(amount: float, n: int) -> list[dict[str, Any]]:
        return [
            {
                "category": "dining",
                "currency": "SGD",
                "posted_at": datetime(2026, 4, 20, 9, tzinfo=UTC) - timedelta(weeks=i),
                "amount": amount,
            }
            for i in range(n)
        ]

    usd = [
        {
            "category": "dining",
            "currency": "USD",
            "posted_at": datetime(2026, 4, 20, 9, tzinfo=UTC) - timedelta(weeks=i),
            "amount": 40,
        }
        for i in range(2)
    ]
    stored = []

    async def _upsert(pool, baseline):
        stored.append(baseline)
        return True

    boom = AsyncMock(side_effect=AssertionError("expected-signal must not be used"))
    # Patch the core attribute and, so a direct ``from ... import upsert_expected_signal`` in the
    # module is caught too, the module's own name (absent today, hence create=True).
    with (
        patch.object(fsb, "upsert_baseline", _upsert),
        patch("butlers.core.expected_signals.upsert_expected_signal", boom),
        patch.object(fsb, "upsert_expected_signal", boom, create=True),
    ):
        changed = await fsb.refresh_category_baselines(
            _SpendPool(history=_weeks(100.0, 10) + usd), now=_NOW, zone=UTC
        )
    boom.assert_not_awaited()
    by_key = {b.metric_key: b for b in stored}
    assert len(changed) == 2
    sgd = by_key["spend.category:dining:SGD"]
    assert (sgd.status, sgd.center, sgd.n_observed, sgd.n_expected) == ("ready", 100.0, 10, 26)
    assert sgd.measurability == "measurable"
    # A flat category (MAD zero) keeps a floor: k_mad * max(1, 10% of the usual week).
    assert sgd.dispersion == pytest.approx(10.0)
    assert sgd.band == pytest.approx((74.0, 126.0))
    usd_band = by_key["spend.category:dining:USD"]
    assert (usd_band.status, usd_band.center, usd_band.n_observed) == (
        "insufficient_history",
        None,
        2,
    )


# ---------------------------------------------------------------------------
# Consumers: week-to-date against the stored band
# ---------------------------------------------------------------------------


async def _score(pool: _SpendPool, *, active_since: datetime | None = None):
    return await fsb.score_week_to_date(
        pool, now=_NOW, zone=UTC, active_since=active_since or _NOW - timedelta(days=1)
    )


async def test_spend_above_the_band_is_a_concerning_deviation() -> None:
    pool = _SpendPool(
        week=[_spend_row("dining", "SGD", 310.0, _NOW - timedelta(hours=2))],
        bands=[_band_row("dining", "SGD")],
    )
    (finding,) = await _score(pool)
    assert finding.deviation.state is BaselineState.ABOVE
    assert finding.is_anomaly
    assert finding.usual_range == pytest.approx((98.0, 202.0))
    assert fsb.usual_range_words(finding) == "usual SGD 98.00 to SGD 202, drawn from 10 of 26 weeks"
    assert fsb.money("SGD", 310.0) == "SGD 310"
    assert fsb.money("SGD", 9.5) == "SGD 9.50"

    # The same category in another currency is scored against its own band, never mixed.
    both = _SpendPool(
        week=[
            _spend_row("dining", "SGD", 310.0, _NOW - timedelta(hours=2)),
            _spend_row("dining", "USD", 310.0, _NOW - timedelta(hours=2)),
        ],
        bands=[
            _band_row("dining", "SGD"),
            _band_row("dining", "USD", center=400.0, dispersion=50.0),
        ],
    )
    states = {f.currency: f.deviation.state for f in await _score(both)}
    assert states == {"SGD": BaselineState.ABOVE, "USD": BaselineState.WITHIN}


async def test_band_freshness_boundary_and_missing_bands_are_never_an_anomaly() -> None:
    async def _state(age_days: int) -> BaselineState:
        pool = _SpendPool(
            week=[_spend_row("dining", "SGD", 310.0, _NOW - timedelta(hours=2))],
            bands=[_band_row("dining", "SGD", computed_at=_NOW - timedelta(days=age_days))],
        )
        (finding,) = await _score(pool)
        assert finding.is_anomaly is (finding.deviation.state is BaselineState.ABOVE)
        return finding.deviation.state

    assert await _state(14) is BaselineState.ABOVE
    assert await _state(15) is BaselineState.STALE

    # No band, or no band table at all: nothing is judged.
    import asyncpg

    class _NoTable(_SpendPool):
        async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
            if "FROM metric_baselines" in sql:
                raise asyncpg.UndefinedTableError("metric_baselines")
            return await super().fetch(sql, *args)

    week = [_spend_row("dining", "SGD", 310.0, _NOW - timedelta(hours=2))]
    assert await _score(_SpendPool(week=week)) == []
    assert await _score(_NoTable(week=week)) == []


async def test_only_categories_active_since_the_cutoff_are_scored_from_monday() -> None:
    pool = _SpendPool(
        week=[
            _spend_row("dining", "SGD", 310.0, _NOW - timedelta(days=1)),  # exactly at cutoff
            _spend_row("travel", "SGD", 310.0, _NOW - timedelta(days=3)),
        ],
        bands=[_band_row("dining", "SGD"), _band_row("travel", "SGD")],
    )
    findings = await _score(pool, active_since=_NOW - timedelta(days=1))
    assert [f.category for f in findings] == ["dining"]
    _, (start, end) = pool.calls[0]
    assert start == datetime(2026, 4, 27, tzinfo=UTC)  # the owner-local Monday
    assert end == _NOW


# ---------------------------------------------------------------------------
# Briefing
# ---------------------------------------------------------------------------


class _BriefingPool(_SpendPool):
    """Bills and subscriptions are empty; spend and bands come from the fake."""

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        if "FROM bills" in sql or "FROM subscriptions" in sql:
            return []
        return await super().fetch(sql, *args)


async def _briefing(pool: _BriefingPool) -> tuple[dict[str, Any], dict[str, Any]]:
    write = AsyncMock()
    with patch("butlers.jobs.briefing._write_contribution", write):
        result = await run_finance_briefing_contribution(pool, None)
    return result, write.await_args.args[1]


async def test_briefing_reports_only_a_week_above_the_usual_range() -> None:
    recent = datetime.now(UTC) - timedelta(minutes=1)
    pool = _BriefingPool(
        week=[
            _spend_row("dining", "SGD", 310.0, recent),
            _spend_row("groceries", "SGD", 120.0, recent),
            _spend_row("travel", "USD", 900.0, recent),
        ],
        bands=[
            _band_row("dining", "SGD", computed_at=datetime.now(UTC)),
            _band_row("groceries", "SGD", computed_at=datetime.now(UTC)),
            _band_row(
                "travel",
                "USD",
                center=None,
                dispersion=None,
                status="insufficient_history",
                n_observed=2,
                computed_at=datetime.now(UTC),
            ),
        ],
    )
    result, envelope = await _briefing(pool)
    assert result["spending_anomalies"] == 1
    (line,) = [h for h in envelope["highlights"] if h["category"] == "spending"]
    assert line["text"] == (
        "Spending above your usual range in dining: SGD 310 so far this week, "
        "usual SGD 98.00 to SGD 202, drawn from 10 of 26 weeks"
    )
    assert "travel" not in envelope["summary"]
    assert "1 spending anomaly/ies detected." in envelope["summary"]
    # Nothing says another category is normal, within range or on track.
    text = " ".join([envelope["summary"]] + [h["text"] for h in envelope["highlights"]]).lower()
    assert "groceries" not in text
    for banned in ("normal", "within", "as usual", "on track"):
        assert banned not in text


# ---------------------------------------------------------------------------
# Fact retirement
# ---------------------------------------------------------------------------


async def test_retirement_retracts_only_the_finance_butlers_system_facts() -> None:
    pool = _SpendPool(facts=[{"id": "f1"}, {"id": "f2"}])
    forget = AsyncMock(side_effect=[True, False])
    with patch("butlers.modules.memory.storage.forget_memory", forget):
        assert await fsb.retire_spending_baseline_facts(pool) == 1
    sql, _ = pool.calls[0]
    assert "predicate = 'spending_baseline'" in sql
    assert "content_authority = 'system'" in sql
    assert "source_butler = 'finance'" in sql
    assert [call.args[1:] for call in forget.await_args_list] == [("fact", "f1"), ("fact", "f2")]

    class _Broken(_SpendPool):
        async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
            raise RuntimeError("no facts")

    assert await fsb.retire_spending_baseline_facts(_Broken()) == 0


# ---------------------------------------------------------------------------
# No measurability claim, no "normal" claim
# ---------------------------------------------------------------------------
