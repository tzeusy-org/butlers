"""Category anomaly candidates dedup per owner-local week, not per day (bu-1fk2od).

The category velocity anomaly judges the owner-local week against its week-bucket band. Its
insight candidate used to be keyed per UTC day, so an above-band category was re-proposed on
every later day of that week with a new debit. The key is now the band's own week.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

import butlers.jobs._roster.finance_jobs  # noqa: F401  (registers the roster module)

pytestmark = pytest.mark.unit

SGT = ZoneInfo("Asia/Singapore")
LA = ZoneInfo("America/Los_Angeles")


def _fj():
    import sys

    return sys.modules["butlers.jobs._roster.finance_jobs"]


def _category(category="Groceries", currency="SGD", severity="medium"):
    return {
        "transaction_id": None,
        "merchant": None,
        "category": category,
        "currency": currency,
        "type": "category_velocity_anomaly",
        "severity": severity,
        "explanation": "above your usual range",
        "baseline_evidence": {"metric": "x"},
    }


def _transaction(txn_id="t1"):
    return {
        "transaction_id": txn_id,
        "merchant": "Big Store",
        "category": "Groceries",
        "currency": "SGD",
        "type": "amount_anomaly",
        "severity": "high",
        "explanation": "way above baseline",
    }


async def _run(monkeypatch, anomalies, *, as_of, zone=SGT):
    """Drive run_anomaly_insight_scan with a canned scan; return the proposals it made."""
    fj = _fj()
    proposals: list[dict] = []

    class _Scoped:
        async def __aenter__(self):
            return MagicMock()

        async def __aexit__(self, *exc):
            return False

    async def scan(conn, days_back=1, sensitivity="medium"):
        result = {"status": "ok", "anomalies": anomalies}
        if as_of is not None:
            result["as_of"] = as_of.isoformat()
        return result

    async def propose(pool, **kwargs):
        proposals.append(kwargs)
        return "accepted"

    monkeypatch.setattr(fj, "_finance_scoped_connection", lambda pool: _Scoped())
    monkeypatch.setattr(fj, "resolve_budget_zone", AsyncMock(return_value=zone))
    monkeypatch.setattr(fj, "refresh_category_baselines", AsyncMock())
    monkeypatch.setattr(fj, "retire_spending_baseline_facts", AsyncMock())
    monkeypatch.setattr(fj, "anomaly_scan", scan)
    monkeypatch.setattr(fj, "_propose", propose)
    await fj.run_anomaly_insight_scan(MagicMock())
    return proposals


def _keys(proposals):
    return [p["dedup_key"] for p in proposals]


async def test_two_days_of_one_week_above_band_share_one_key(monkeypatch) -> None:
    tuesday = await _run(monkeypatch, [_category()], as_of=datetime(2026, 10, 13, 9, 0, tzinfo=UTC))
    friday = await _run(
        monkeypatch, [_category(severity="medium")], as_of=datetime(2026, 10, 16, 9, 0, tzinfo=UTC)
    )

    assert _keys(tuesday) == _keys(friday)
    assert _keys(tuesday) == ["finance:anomaly:category-Groceries-SGD:week-2026-10-12"]


async def test_the_next_week_raises_the_category_again(monkeypatch) -> None:
    this_week = await _run(
        monkeypatch, [_category()], as_of=datetime(2026, 10, 16, 9, 0, tzinfo=UTC)
    )
    next_week = await _run(
        monkeypatch, [_category()], as_of=datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
    )

    assert _keys(next_week) == ["finance:anomaly:category-Groceries-SGD:week-2026-10-19"]
    assert _keys(this_week) != _keys(next_week)


async def test_the_week_is_the_owner_local_week_not_the_utc_week(monkeypatch) -> None:
    # Sunday 20:00 UTC is already Monday 04:00 in Singapore: the next owner-local week.
    singapore = await _run(
        monkeypatch, [_category()], as_of=datetime(2026, 10, 18, 20, 0, tzinfo=UTC)
    )
    # The same instant is still Sunday afternoon in Los Angeles: the week that is ending.
    los_angeles = await _run(
        monkeypatch, [_category()], as_of=datetime(2026, 10, 18, 20, 0, tzinfo=UTC), zone=LA
    )

    assert _keys(singapore)[0].endswith("week-2026-10-19")
    assert _keys(los_angeles)[0].endswith("week-2026-10-12")


async def test_a_severity_change_inside_the_week_does_not_raise_it_again(monkeypatch) -> None:
    medium = await _run(
        monkeypatch,
        [_category(severity="medium")],
        as_of=datetime(2026, 10, 13, 9, 0, tzinfo=UTC),
    )
    high = await _run(
        monkeypatch,
        [_category(severity="high")],
        as_of=datetime(2026, 10, 16, 9, 0, tzinfo=UTC),
    )

    assert _keys(medium) == _keys(high)


async def test_each_category_and_currency_has_its_own_weekly_key(monkeypatch) -> None:
    proposals = await _run(
        monkeypatch,
        [_category("Groceries", "SGD"), _category("Groceries", "USD"), _category("Dining", "SGD")],
        as_of=datetime(2026, 10, 14, 9, 0, tzinfo=UTC),
    )

    assert len(set(_keys(proposals))) == 3


async def test_the_weekly_key_is_held_for_the_whole_week(monkeypatch) -> None:
    proposals = await _run(
        monkeypatch, [_category()], as_of=datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
    )

    assert [p["cooldown_days"] for p in proposals] == [7]


async def test_transaction_anomalies_keep_their_per_day_key_and_cooldown(monkeypatch) -> None:
    # The job reads the clock itself: bracket the run so a UTC midnight crossing cannot flake.
    before = datetime.now(UTC).date()
    proposals = await _run(
        monkeypatch, [_transaction()], as_of=datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
    )
    after = datetime.now(UTC).date()

    assert _keys(proposals) in (
        [f"finance:anomaly:t1:{before.isoformat()}"],
        [f"finance:anomaly:t1:{after.isoformat()}"],
    )
    assert [p["cooldown_days"] for p in proposals] == [1]


async def test_a_scan_without_a_clock_still_keys_on_the_current_week(monkeypatch) -> None:
    fj = _fj()
    # The job reads the clock itself: bracket the run so a week boundary cannot flake.
    before = fj.week_start(datetime.now(UTC), SGT)
    proposals = await _run(monkeypatch, [_category()], as_of=None)
    after = fj.week_start(datetime.now(UTC), SGT)

    assert _keys(proposals) in (
        [f"finance:anomaly:category-Groceries-SGD:week-{before.isoformat()}"],
        [f"finance:anomaly:category-Groceries-SGD:week-{after.isoformat()}"],
    )


@pytest.mark.parametrize(
    ("category", "currency", "expected"),
    [
        ("Rent", "SGD", "category-Rent-SGD"),
        ("Rent", None, "category-Rent"),
    ],
)
def test_the_key_names_the_currency_only_when_there_is_one(category, currency, expected) -> None:
    anomaly = {**_category(category, currency or ""), "currency": currency}
    key = _fj()._anomaly_dedup_key(anomaly, today=date(2026, 10, 14), week=date(2026, 10, 12))
    assert key == f"finance:anomaly:{expected}:week-2026-10-12"
