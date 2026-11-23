"""``transaction_amounts_since``: the owner-watch read feed's one finance tool (bu-4c7nvp.1.5).

It returns the largest absolute amount recorded at each instant, in one currency, after a
cursor, and nothing else: no merchant, description, account or category.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta

import pytest

from butlers.roster_imports import roster_module

alerts = roster_module("finance", "tools/alerts")

_docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not _docker_available, reason="Docker not available"),
]

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
LAG = timedelta(seconds=60)


def us(moment: datetime) -> int:
    return (moment - EPOCH) // timedelta(microseconds=1)


@pytest.fixture
async def finance_pool(db):
    return await db("core", "memory", "finance")


async def insert(pool, amount, *, currency="USD", created_at=None, merchant="Acme", deleted=False):
    created_at = created_at or datetime.now(UTC) - timedelta(minutes=5)
    return str(
        await pool.fetchval(
            """
            INSERT INTO transactions (posted_at, merchant, amount, currency, direction, category,
                                      created_at, deleted_at)
            VALUES ($1, $2, $3, $4, 'debit', 'subscriptions', $5, $6) RETURNING id
            """,
            created_at,
            merchant,
            amount,
            currency,
            created_at,
            created_at if deleted else None,
        )
    )


async def test_the_result_is_exactly_the_declared_fields(finance_pool):
    await insert(finance_pool, 700, merchant="Secret Merchant")
    result = await alerts.transaction_amounts_since(finance_pool, "usd", 0, 100)
    assert set(result) == {"rows"}
    (row,) = result["rows"]
    assert set(row) == {"id", "amount", "currency", "recorded_us"}
    assert row["amount"] == "700.00" and row["currency"] == "USD"
    assert "Secret" not in repr(result)


async def test_rows_page_by_insertion_time_not_transaction_date(finance_pool):
    base = datetime.now(UTC) - timedelta(minutes=30)
    await insert(finance_pool, 100, created_at=base)
    late_import = await insert(finance_pool, 200, created_at=base + timedelta(minutes=10))
    # An import of an old-dated transaction: posted long ago, inserted just now.
    await finance_pool.execute(
        "UPDATE transactions SET posted_at = posted_at - interval '400 days' WHERE id = $1::uuid",
        late_import,
    )
    first = await alerts.transaction_amounts_since(finance_pool, "usd", 0, 1)
    after = first["rows"][0]["recorded_us"]
    second = await alerts.transaction_amounts_since(finance_pool, "usd", after, 100)
    assert [r["id"] for r in second["rows"]] == [late_import]  # not skipped by its old date


async def test_one_reading_per_instant_is_the_largest_absolute_amount(finance_pool):
    at = datetime.now(UTC) - timedelta(minutes=10)
    await insert(finance_pool, 50, created_at=at)
    big = await insert(finance_pool, -900, created_at=at)  # a refund counts by size
    await insert(finance_pool, 20, created_at=at)
    result = await alerts.transaction_amounts_since(finance_pool, "usd", 0, 100)
    (row,) = result["rows"]
    assert (row["id"], row["amount"]) == (big, "900.00")


async def test_other_currencies_deleted_rows_and_the_settle_lag_are_excluded(finance_pool):
    old = datetime.now(UTC) - timedelta(minutes=10)
    await insert(finance_pool, 500, currency="EUR", created_at=old)
    await insert(finance_pool, 500, created_at=old - timedelta(minutes=1), deleted=True)
    await insert(finance_pool, 500, created_at=datetime.now(UTC) - timedelta(seconds=5))
    assert (await alerts.transaction_amounts_since(finance_pool, "usd", 0, 100))["rows"] == []


async def test_the_limit_is_bounded_and_the_cursor_is_exclusive(finance_pool):
    base = datetime.now(UTC) - timedelta(minutes=60)
    for index in range(5):
        await insert(finance_pool, 10 + index, created_at=base + timedelta(minutes=index))
    rows = (await alerts.transaction_amounts_since(finance_pool, "usd", 0, 3))["rows"]
    assert len(rows) == 3
    rest = await alerts.transaction_amounts_since(finance_pool, "usd", rows[-1]["recorded_us"], 100)
    assert len(rest["rows"]) == 2
    with pytest.raises(ValueError):
        await alerts.transaction_amounts_since(finance_pool, "usd", 0, 101)
    with pytest.raises(ValueError):
        await alerts.transaction_amounts_since(finance_pool, "usd", -1, 10)
    with pytest.raises(ValueError):
        await alerts.transaction_amounts_since(finance_pool, "usdx", 0, 10)
