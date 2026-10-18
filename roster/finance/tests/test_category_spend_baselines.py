"""Real-PG behavior of category spend bands and anomaly_scan (bu-q7vx1q.79); synthetic values."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

_docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not _docker_available, reason="Docker not available"),
]

# Sunday 12:00 UTC; the owner-local (UTC) week started Monday 2026-04-27.
_NOW = datetime(2026, 5, 3, 12, 0, tzinfo=UTC)
_LATE = _NOW - timedelta(hours=1)
_MONDAY = datetime(2026, 4, 27, 9, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container, migration_db_name(), chains=["core", "finance"]
    )


@pytest.fixture
async def pool(migrated_db_url: str):
    p = await asyncpg.create_pool(
        migrated_db_url, min_size=1, max_size=3, init=register_jsonb_codec
    )
    await p.execute("TRUNCATE TABLE transactions, metric_baselines")
    yield p
    await p.close()


async def _spend(pool, *, category: str, currency: str, amount: str, posted_at: datetime) -> None:
    await pool.execute(
        """
        INSERT INTO transactions (posted_at, merchant, amount, currency, direction, category)
        VALUES ($1, 'Cafe', $2, $3, 'debit', $4)
        """,
        posted_at,
        Decimal(amount),
        currency,
        category,
    )


async def _seed_history(pool, *, currency: str, weeks: int, amounts: list[str]) -> None:
    """One debit per complete week, newest first, cycling through ``amounts``."""
    for i in range(weeks):
        await _spend(
            pool,
            category="dining",
            currency=currency,
            amount=amounts[i % len(amounts)],
            posted_at=_MONDAY - timedelta(weeks=i + 1),
        )


async def _velocity(pool, now=_NOW):
    from butlers.tools.finance.anomaly_detection import anomaly_scan

    result = await anomaly_scan(pool, days_back=7, sensitivity="medium", now=now)
    return [a for a in result["anomalies"] if a["type"] == "category_velocity_anomaly"]


async def _refresh(pool):
    from butlers.jobs.finance_spend_baselines import refresh_category_baselines

    return await refresh_category_baselines(pool, now=_NOW, zone=UTC)


async def test_spike_week_is_one_anomaly_with_its_band_and_row(pool) -> None:
    await _seed_history(pool, currency="SGD", weeks=10, amounts=["100", "110", "90"])
    await _spend(pool, category="dining", currency="SGD", amount="400", posted_at=_LATE)
    await _refresh(pool)

    (anomaly,) = await _velocity(pool)
    assert (anomaly["category"], anomaly["currency"]) == ("dining", "SGD")
    assert anomaly["amount"] == "400.00"
    assert anomaly["mad_units"] > 2.6
    assert anomaly["band"]["low"] < 100 < anomaly["band"]["high"] < 400
    assert anomaly["baseline_evidence"]["n_observed"] == 10
    assert anomaly["baseline_evidence"]["n_expected"] == 26
    assert "SGD" in anomaly["explanation"] and "10 of 26 weeks" in anomaly["explanation"]

    row = await pool.fetchrow(
        "SELECT status, unit FROM metric_baselines WHERE metric_key = $1",
        "spend.category:dining:SGD",
    )
    assert (row["status"], row["unit"]) == ("ready", "SGD")


async def test_the_same_category_in_another_currency_with_thin_history_is_silent(pool) -> None:
    await _seed_history(pool, currency="SGD", weeks=10, amounts=["100", "110", "90"])
    await _seed_history(pool, currency="USD", weeks=2, amounts=["100"])
    await _spend(pool, category="dining", currency="SGD", amount="400", posted_at=_LATE)
    await _spend(pool, category="dining", currency="USD", amount="400", posted_at=_LATE)
    await _refresh(pool)

    anomalies = await _velocity(pool)
    assert [a["currency"] for a in anomalies] == ["SGD"]
    usd = await pool.fetchrow(
        "SELECT status, n_observed FROM metric_baselines WHERE metric_key = $1",
        "spend.category:dining:USD",
    )
    assert (usd["status"], usd["n_observed"]) == ("insufficient_history", 2)


async def test_a_normal_week_is_not_an_anomaly(pool) -> None:
    await _seed_history(pool, currency="SGD", weeks=10, amounts=["100", "110", "90"])
    await _spend(pool, category="dining", currency="SGD", amount="105", posted_at=_LATE)
    await _refresh(pool)
    assert await _velocity(pool) == []


async def test_without_stored_bands_the_scan_claims_nothing(pool) -> None:
    await _seed_history(pool, currency="SGD", weeks=10, amounts=["100", "110", "90"])
    await _spend(pool, category="dining", currency="SGD", amount="400", posted_at=_LATE)
    assert await _velocity(pool) == []


async def test_a_stale_band_is_not_used(pool) -> None:
    await _seed_history(pool, currency="SGD", weeks=10, amounts=["100", "110", "90"])
    await _spend(pool, category="dining", currency="SGD", amount="400", posted_at=_LATE)
    await _refresh(pool)
    await pool.execute("UPDATE metric_baselines SET computed_at = $1", _NOW - timedelta(days=15))
    assert await _velocity(pool) == []


async def test_a_rerun_with_unchanged_inputs_is_a_noop(pool) -> None:
    await _seed_history(pool, currency="SGD", weeks=10, amounts=["100", "110", "90"])
    first = await _refresh(pool)
    second = await _refresh(pool)
    assert len(first) == 1
    assert second == []
    assert await pool.fetchval("SELECT count(*) FROM metric_baselines") == 1


async def test_soft_deleted_spend_is_not_history(pool) -> None:
    await _seed_history(pool, currency="SGD", weeks=10, amounts=["100"])
    await pool.execute("UPDATE transactions SET deleted_at = now()")
    await _refresh(pool)
    assert await pool.fetchval("SELECT count(*) FROM metric_baselines") == 0


async def test_only_complete_weeks_are_history(pool) -> None:
    await _seed_history(pool, currency="SGD", weeks=5, amounts=["100"])
    await _spend(pool, category="dining", currency="SGD", amount="900", posted_at=_LATE)
    await _refresh(pool)
    row = await pool.fetchrow(
        "SELECT n_observed, center FROM metric_baselines WHERE metric_key = $1",
        "spend.category:dining:SGD",
    )
    assert (row["n_observed"], row["center"]) == (5, 100.0)


async def test_spend_posted_at_the_scan_instant_counts_toward_the_week(pool) -> None:
    await _seed_history(pool, currency="SGD", weeks=10, amounts=["100", "110", "90"])
    await _spend(pool, category="dining", currency="SGD", amount="400", posted_at=_NOW)
    await _refresh(pool)
    from butlers.jobs.finance_spend_baselines import score_week_to_date

    (finding,) = await score_week_to_date(
        pool, now=_NOW, zone=UTC, active_since=_NOW - timedelta(days=1)
    )
    assert finding.week_spend == 400.0
