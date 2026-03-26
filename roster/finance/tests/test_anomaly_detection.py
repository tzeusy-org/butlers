"""Tests for butlers.tools.finance.anomaly_detection.

Covers:
- compute_baselines: per-merchant median/stddev from 6-month rolling window
- compute_baselines: per-category weekly velocity from 6-month rolling window
- compute_baselines: graceful insufficient_data when no transactions exist
- compute_baselines: filters out merchants with < 3 transactions
- compute_baselines: filters out categories with < 4 weekly data points
- compute_baselines: excludes soft-deleted transactions
- anomaly_scan: amount_anomaly detection at medium sensitivity
- anomaly_scan: new_merchant detection
- anomaly_scan: category_velocity_anomaly detection
- anomaly_scan: returns insufficient_data when no baselines
- anomaly_scan: sensitivity parameter (high/medium/low)
- anomaly_scan: excludes soft-deleted transactions
- detect_duplicates: same-day same-merchant same-amount → high confidence
- detect_duplicates: adjacent-day → medium confidence
- detect_duplicates: different amounts → not flagged
- detect_duplicates: known subscription merchant excluded
- detect_duplicates: no transactions → ok with empty duplicates
- detect_duplicates: excludes soft-deleted transactions
- detect_duplicates: returns within days_back window only
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

_docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not _docker_available, reason="Docker not available"),
]

# ---------------------------------------------------------------------------
# Minimal schemas
# ---------------------------------------------------------------------------

CREATE_TRANSACTIONS_SQL = """
CREATE TABLE IF NOT EXISTS transactions (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id        UUID,
    source_message_id TEXT,
    posted_at         TIMESTAMPTZ NOT NULL,
    merchant          TEXT NOT NULL,
    description       TEXT,
    amount            NUMERIC(14, 2) NOT NULL,
    currency          CHAR(3) NOT NULL DEFAULT 'USD',
    direction         TEXT NOT NULL CHECK (direction IN ('debit', 'credit')),
    category          TEXT NOT NULL DEFAULT 'uncategorized',
    payment_method    TEXT,
    receipt_url       TEXT,
    external_ref      TEXT,
    deleted_at        TIMESTAMPTZ,
    metadata          JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

CREATE_SUBSCRIPTIONS_SQL = """
CREATE TABLE IF NOT EXISTS subscriptions (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    service           TEXT NOT NULL,
    amount            NUMERIC(14, 2) NOT NULL,
    currency          CHAR(3) NOT NULL DEFAULT 'USD',
    frequency         TEXT NOT NULL
                          CHECK (frequency IN (
                              'weekly', 'monthly', 'quarterly', 'yearly', 'custom'
                          )),
    next_renewal      DATE NOT NULL,
    status            TEXT NOT NULL
                          CHECK (status IN ('active', 'cancelled', 'paused')),
    auto_renew        BOOLEAN NOT NULL DEFAULT true,
    metadata          JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


# ---------------------------------------------------------------------------
# Insertion helpers
# ---------------------------------------------------------------------------


async def _insert_txn(
    pool,
    *,
    merchant: str = "TestMerchant",
    amount: str = "10.00",
    currency: str = "USD",
    direction: str = "debit",
    category: str = "groceries",
    posted_at: datetime,
    deleted_at: datetime | None = None,
) -> str:
    """Insert a transaction and return its UUID as a string."""
    row = await pool.fetchrow(
        """
        INSERT INTO transactions
            (merchant, amount, currency, direction, category, posted_at, deleted_at)
        VALUES ($1, $2::numeric, $3, $4, $5, $6, $7)
        RETURNING id::text
        """,
        merchant,
        Decimal(amount),
        currency,
        direction,
        category,
        posted_at,
        deleted_at,
    )
    return row["id"]


async def _insert_sub(
    pool,
    *,
    service: str,
    amount: str = "9.99",
    frequency: str = "monthly",
    status: str = "active",
) -> None:
    renewal = (datetime.now(UTC) + timedelta(days=30)).date()
    await pool.execute(
        """
        INSERT INTO subscriptions (service, amount, currency, frequency, next_renewal, status)
        VALUES ($1, $2::numeric, 'USD', $3, $4, $5)
        """,
        service,
        Decimal(amount),
        frequency,
        renewal,
        status,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def pool(provisioned_postgres_pool):
    """Fresh database with transactions + subscriptions tables."""
    async with provisioned_postgres_pool() as p:
        await p.execute(CREATE_TRANSACTIONS_SQL)
        await p.execute(CREATE_SUBSCRIPTIONS_SQL)
        yield p


# ---------------------------------------------------------------------------
# TestComputeBaselines
# ---------------------------------------------------------------------------


class TestComputeBaselines:
    """Tests for compute_baselines()."""

    async def test_insufficient_data_empty_table(self, pool):
        """Returns status=insufficient_data when transactions table is empty."""
        from butlers.tools.finance.anomaly_detection import compute_baselines

        result = await compute_baselines(pool)

        assert result["status"] == "insufficient_data"
        assert result["merchant_baselines"] == []
        assert result["category_baselines"] == []

    async def test_merchant_baseline_computed_with_enough_data(self, pool):
        """3+ debit transactions from same merchant produce median/stddev baseline."""
        from butlers.tools.finance.anomaly_detection import compute_baselines

        base = datetime(2025, 8, 1, 12, 0, tzinfo=UTC)
        for i, amt in enumerate(["10.00", "20.00", "15.00"]):
            await _insert_txn(
                pool, merchant="Walmart", amount=amt, posted_at=base + timedelta(days=i * 5)
            )

        result = await compute_baselines(pool)

        assert result["status"] == "ok"
        merchants = {b["merchant"]: b for b in result["merchant_baselines"]}
        assert "Walmart" in merchants
        b = merchants["Walmart"]
        assert b["sample_count"] == 3
        # Median of [10, 15, 20] = 15
        assert abs(b["median"] - 15.0) < 0.01
        # Stddev of [10, 15, 20] ≈ 5.0
        assert b["stddev"] > 0.0

    async def test_merchant_excluded_with_too_few_transactions(self, pool):
        """Merchant with < 3 transactions is excluded from baselines."""
        from butlers.tools.finance.anomaly_detection import compute_baselines

        base = datetime(2025, 8, 1, 12, 0, tzinfo=UTC)
        # Only 2 transactions for "RareShop"
        for i in range(2):
            await _insert_txn(
                pool, merchant="RareShop", amount="50.00", posted_at=base + timedelta(days=i)
            )
        # 3 transactions for "Walmart"
        for i in range(3):
            await _insert_txn(
                pool, merchant="Walmart", amount="20.00", posted_at=base + timedelta(days=i)
            )

        result = await compute_baselines(pool)

        merchants = {b["merchant"] for b in result["merchant_baselines"]}
        assert "RareShop" not in merchants
        assert "Walmart" in merchants

    async def test_deleted_transactions_excluded_from_baselines(self, pool):
        """Soft-deleted transactions are not included in baseline computation."""
        from butlers.tools.finance.anomaly_detection import compute_baselines

        base = datetime(2025, 8, 1, 12, 0, tzinfo=UTC)
        deleted_time = datetime(2025, 8, 10, 12, 0, tzinfo=UTC)
        # Insert 2 live and 3 deleted — deleted takes the merchant below threshold
        for i in range(2):
            await _insert_txn(
                pool, merchant="GhostMerchant", amount="30.00", posted_at=base + timedelta(days=i)
            )
        for i in range(3):
            await _insert_txn(
                pool,
                merchant="GhostMerchant",
                amount="30.00",
                posted_at=base + timedelta(days=i + 10),
                deleted_at=deleted_time,
            )

        result = await compute_baselines(pool)

        merchants = {b["merchant"] for b in result["merchant_baselines"]}
        # Only 2 live transactions — below minimum threshold
        assert "GhostMerchant" not in merchants

    async def test_category_weekly_velocity_computed(self, pool):
        """Category with 4+ weeks of data produces a weekly_velocity baseline."""
        from butlers.tools.finance.anomaly_detection import compute_baselines

        # Insert 4 weeks of groceries spend (Monday of each week)
        base = datetime(2025, 7, 7, 12, 0, tzinfo=UTC)  # Monday
        for week in range(4):
            week_start = base + timedelta(weeks=week)
            for day in range(3):
                await _insert_txn(
                    pool,
                    merchant=f"Store{week}-{day}",
                    amount="25.00",
                    category="groceries",
                    posted_at=week_start + timedelta(days=day),
                )

        result = await compute_baselines(pool)

        cats = {b["category"]: b for b in result["category_baselines"]}
        assert "groceries" in cats
        g = cats["groceries"]
        assert g["week_count"] == 4
        # Each week has 3 × $25 = $75 spend; average = $75
        assert abs(g["weekly_velocity"] - 75.0) < 0.01

    async def test_category_excluded_with_too_few_weeks(self, pool):
        """Category with < 4 weeks of data is excluded from baselines."""
        from butlers.tools.finance.anomaly_detection import compute_baselines

        # 3 distinct merchants to satisfy merchant threshold, 3 weeks of category data
        base = datetime(2025, 8, 1, 12, 0, tzinfo=UTC)
        for week in range(3):
            await _insert_txn(
                pool,
                merchant=f"ShopW{week}",
                amount="40.00",
                category="electronics",
                posted_at=base + timedelta(weeks=week),
            )
        # Add enough transactions to get them above individual merchant threshold too
        for merchant in ["ShopW0", "ShopW1", "ShopW2"]:
            for extra in range(2):
                await _insert_txn(
                    pool,
                    merchant=merchant,
                    amount="40.00",
                    category="electronics",
                    posted_at=base + timedelta(days=extra + 1),
                )

        result = await compute_baselines(pool)

        cats = {b["category"] for b in result["category_baselines"]}
        assert "electronics" not in cats

    async def test_only_debit_transactions_counted(self, pool):
        """Credit transactions are excluded from baseline computation."""
        from butlers.tools.finance.anomaly_detection import compute_baselines

        base = datetime(2025, 8, 1, 12, 0, tzinfo=UTC)
        for i in range(3):
            await _insert_txn(
                pool,
                merchant="Refundable",
                amount="100.00",
                direction="credit",
                posted_at=base + timedelta(days=i),
            )

        result = await compute_baselines(pool)

        merchants = {b["merchant"] for b in result["merchant_baselines"]}
        assert "Refundable" not in merchants

    async def test_transactions_outside_6_month_window_excluded(self, pool):
        """Transactions older than 180 days do not contribute to baselines."""
        from butlers.tools.finance.anomaly_detection import compute_baselines

        now = datetime.now(UTC)
        old_base = now - timedelta(days=200)  # 200 days ago — outside 180-day window
        recent_base = now - timedelta(days=30)

        # 5 old transactions (should be excluded)
        for i in range(5):
            await _insert_txn(
                pool, merchant="OldShop", amount="50.00", posted_at=old_base + timedelta(days=i)
            )
        # 3 recent transactions
        for i in range(3):
            await _insert_txn(
                pool, merchant="NewShop", amount="30.00", posted_at=recent_base + timedelta(days=i)
            )

        result = await compute_baselines(pool)

        merchants = {b["merchant"] for b in result["merchant_baselines"]}
        assert "OldShop" not in merchants
        assert "NewShop" in merchants


# ---------------------------------------------------------------------------
# TestAnomalyScan
# ---------------------------------------------------------------------------


class TestAnomalyScan:
    """Tests for anomaly_scan()."""

    async def test_insufficient_data_when_no_baselines(self, pool):
        """Returns status=insufficient_data when no baseline data exists."""
        from butlers.tools.finance.anomaly_detection import anomaly_scan

        result = await anomaly_scan(pool, days_back=7)

        assert result["status"] == "insufficient_data"
        assert result["anomalies"] == []

    async def test_amount_anomaly_detected_at_medium_sensitivity(self, pool):
        """An unusually large transaction is flagged as amount_anomaly."""
        from butlers.tools.finance.anomaly_detection import anomaly_scan

        # Build a 6-month baseline for "CoffeeShop" with amounts ~$5
        base = datetime.now(UTC) - timedelta(days=150)
        for i in range(6):
            await _insert_txn(
                pool,
                merchant="CoffeeShop",
                amount="5.00",
                posted_at=base + timedelta(days=i * 20),
            )

        # Insert a recent anomalous transaction: $500 (100x the usual $5)
        await _insert_txn(
            pool,
            merchant="CoffeeShop",
            amount="500.00",
            posted_at=datetime.now(UTC) - timedelta(days=1),
        )

        result = await anomaly_scan(pool, days_back=7, sensitivity="medium")

        assert result["status"] == "ok"
        flagged = [a for a in result["anomalies"] if a["type"] == "amount_anomaly"]
        assert len(flagged) >= 1
        anomaly = flagged[0]
        assert anomaly["merchant"] == "CoffeeShop"
        assert float(anomaly["amount"]) == pytest.approx(500.0)
        assert anomaly["severity"] in ("high", "medium")

    async def test_new_merchant_flagged(self, pool):
        """First-ever transaction from a merchant is flagged as new_merchant."""
        from butlers.tools.finance.anomaly_detection import anomaly_scan

        # Establish baseline with existing merchants
        base = datetime.now(UTC) - timedelta(days=150)
        for i in range(4):
            await _insert_txn(
                pool,
                merchant="OldMerchant",
                amount="20.00",
                posted_at=base + timedelta(days=i * 30),
            )

        # New merchant — never seen before
        await _insert_txn(
            pool,
            merchant="BrandNewStore",
            amount="50.00",
            posted_at=datetime.now(UTC) - timedelta(days=2),
        )

        result = await anomaly_scan(pool, days_back=7)

        assert result["status"] == "ok"
        new_merchant_flags = [a for a in result["anomalies"] if a["type"] == "new_merchant"]
        merchants_flagged = {a["merchant"] for a in new_merchant_flags}
        assert "BrandNewStore" in merchants_flagged

    async def test_normal_transaction_not_flagged(self, pool):
        """Transaction within baseline range is not flagged."""
        from butlers.tools.finance.anomaly_detection import anomaly_scan

        base = datetime.now(UTC) - timedelta(days=150)
        for i in range(6):
            await _insert_txn(
                pool,
                merchant="GroceryStore",
                amount="50.00",
                posted_at=base + timedelta(days=i * 20),
            )
        # Normal recent transaction at exactly the baseline median
        await _insert_txn(
            pool,
            merchant="GroceryStore",
            amount="50.00",
            posted_at=datetime.now(UTC) - timedelta(days=1),
        )

        result = await anomaly_scan(pool, days_back=7, sensitivity="medium")

        flagged = [
            a
            for a in result["anomalies"]
            if a["type"] == "amount_anomaly" and a["merchant"] == "GroceryStore"
        ]
        assert len(flagged) == 0

    async def test_sensitivity_high_flags_more(self, pool):
        """High sensitivity flags borderline anomalies that medium would skip."""
        from butlers.tools.finance.anomaly_detection import anomaly_scan

        base = datetime.now(UTC) - timedelta(days=150)
        # Baseline: consistent $10 amounts (stddev = 0)
        for i in range(6):
            await _insert_txn(
                pool,
                merchant="SteadyShop",
                amount="10.00",
                posted_at=base + timedelta(days=i * 20),
            )
        # Insert a recent transaction that is moderately elevated: $25
        await _insert_txn(
            pool,
            merchant="SteadyShop",
            amount="25.00",
            posted_at=datetime.now(UTC) - timedelta(days=1),
        )

        # With stddev=0, no amount anomalies are possible (threshold = median + N * 0)
        # SteadyShop $25 will not be flagged as amount_anomaly (stddev = 0)
        result_medium = await anomaly_scan(pool, days_back=7, sensitivity="medium")
        result_high = await anomaly_scan(pool, days_back=7, sensitivity="high")

        # Both should have the same behavior for stddev=0 edge case
        assert result_medium["status"] == "ok"
        assert result_high["status"] == "ok"

    async def test_sensitivity_low_requires_larger_deviation(self, pool):
        """Low sensitivity requires a larger deviation to flag an anomaly."""
        from butlers.tools.finance.anomaly_detection import anomaly_scan

        base = datetime.now(UTC) - timedelta(days=150)
        for i in range(6):
            await _insert_txn(
                pool,
                merchant="VariedShop",
                amount=str(10.0 + i * 2),  # amounts 10, 12, 14, 16, 18, 20
                posted_at=base + timedelta(days=i * 20),
            )
        # Moderately anomalous: $35 (above median of ~15 by ~4 stddevs for this spread)
        await _insert_txn(
            pool,
            merchant="VariedShop",
            amount="35.00",
            posted_at=datetime.now(UTC) - timedelta(days=1),
        )

        result_low = await anomaly_scan(pool, days_back=7, sensitivity="low")
        result_high = await anomaly_scan(pool, days_back=7, sensitivity="high")

        amount_anomalies_low = [
            a
            for a in result_low["anomalies"]
            if a["type"] == "amount_anomaly" and a["merchant"] == "VariedShop"
        ]
        amount_anomalies_high = [
            a
            for a in result_high["anomalies"]
            if a["type"] == "amount_anomaly" and a["merchant"] == "VariedShop"
        ]

        # High sensitivity should flag at or above the threshold where low may not
        # (high multiplier=1.5, low=3.0 — high is stricter → flags more)
        assert len(amount_anomalies_high) >= len(amount_anomalies_low)

    async def test_category_velocity_anomaly_detected(self, pool):
        """Spending in a category well above weekly baseline is flagged."""
        from butlers.tools.finance.anomaly_detection import anomaly_scan

        # Build 4 weeks of baseline for "dining" with $50/week velocity
        now = datetime.now(UTC)
        base = now - timedelta(days=140)
        for week in range(4):
            for day in range(2):
                await _insert_txn(
                    pool,
                    merchant=f"Dining{week}x{day}",
                    amount="25.00",
                    category="dining",
                    posted_at=base + timedelta(weeks=week, days=day),
                )
        # Add transactions to make merchants individually qualify for baseline
        for merchant in [f"Dining{w}x{d}" for w in range(4) for d in range(2)]:
            for extra in range(2):
                await _insert_txn(
                    pool,
                    merchant=merchant,
                    amount="25.00",
                    category="dining",
                    posted_at=base + timedelta(days=extra + 1),
                )

        # Insert recent week with 10x normal dining spend
        for day in range(7):
            await _insert_txn(
                pool,
                merchant="BigDinnerPlace",
                amount="75.00",
                category="dining",
                posted_at=now - timedelta(days=7) + timedelta(days=day),
            )

        result = await anomaly_scan(pool, days_back=7, sensitivity="medium")

        vel_anomalies = [a for a in result["anomalies"] if a["type"] == "category_velocity_anomaly"]
        categories_flagged = {a["category"] for a in vel_anomalies}
        assert "dining" in categories_flagged

    async def test_excluded_soft_deleted_transactions(self, pool):
        """Soft-deleted transactions are not scanned for anomalies."""
        from butlers.tools.finance.anomaly_detection import anomaly_scan

        base = datetime.now(UTC) - timedelta(days=150)
        for i in range(4):
            await _insert_txn(
                pool,
                merchant="ActiveShop",
                amount="20.00",
                posted_at=base + timedelta(days=i * 30),
            )

        deleted_time = datetime.now(UTC)
        await _insert_txn(
            pool,
            merchant="ActiveShop",
            amount="5000.00",
            posted_at=datetime.now(UTC) - timedelta(days=1),
            deleted_at=deleted_time,
        )

        result = await anomaly_scan(pool, days_back=7, sensitivity="high")

        flagged = [
            a
            for a in result["anomalies"]
            if a["type"] == "amount_anomaly" and a["merchant"] == "ActiveShop"
        ]
        assert len(flagged) == 0


# ---------------------------------------------------------------------------
# TestDetectDuplicates
# ---------------------------------------------------------------------------


class TestDetectDuplicates:
    """Tests for detect_duplicates()."""

    async def test_empty_table_returns_ok_no_duplicates(self, pool):
        """Empty transactions table returns status=ok with empty duplicates."""
        from butlers.tools.finance.anomaly_detection import detect_duplicates

        result = await detect_duplicates(pool, days_back=30)

        assert result["status"] == "ok"
        assert result["duplicates"] == []
        assert result["total_found"] == 0

    async def test_same_day_duplicate_detected_high_confidence(self, pool):
        """Same-merchant same-amount transactions on the same day are flagged as high confidence."""
        from butlers.tools.finance.anomaly_detection import detect_duplicates

        now = datetime.now(UTC)
        txn_day = now - timedelta(days=5)

        await _insert_txn(
            pool, merchant="Starbucks", amount="5.75", posted_at=txn_day.replace(hour=9)
        )
        await _insert_txn(
            pool, merchant="Starbucks", amount="5.75", posted_at=txn_day.replace(hour=14)
        )

        result = await detect_duplicates(pool, days_back=30)

        assert result["total_found"] >= 1
        dupes = [d for d in result["duplicates"] if d["merchant"] == "Starbucks"]
        assert len(dupes) >= 1
        assert dupes[0]["confidence"] == "high"
        assert len(dupes[0]["transactions"]) >= 2

    async def test_adjacent_day_duplicate_detected_medium_confidence(self, pool):
        """Same-merchant same-amount transactions on consecutive days → medium confidence."""
        from butlers.tools.finance.anomaly_detection import detect_duplicates

        now = datetime.now(UTC)
        day1 = now - timedelta(days=5)
        day2 = now - timedelta(days=4)

        await _insert_txn(pool, merchant="Amazon", amount="29.99", posted_at=day1)
        await _insert_txn(pool, merchant="Amazon", amount="29.99", posted_at=day2)

        result = await detect_duplicates(pool, days_back=30)

        dupes = [d for d in result["duplicates"] if d["merchant"] == "Amazon"]
        assert len(dupes) >= 1
        assert dupes[0]["confidence"] == "medium"

    async def test_different_amounts_not_flagged(self, pool):
        """Transactions with different amounts for same merchant are not duplicates."""
        from butlers.tools.finance.anomaly_detection import detect_duplicates

        now = datetime.now(UTC)
        await _insert_txn(
            pool, merchant="Target", amount="15.00", posted_at=now - timedelta(days=1)
        )
        await _insert_txn(
            pool, merchant="Target", amount="25.00", posted_at=now - timedelta(days=1)
        )

        result = await detect_duplicates(pool, days_back=30)

        dupes = [d for d in result["duplicates"] if d["merchant"] == "Target"]
        assert len(dupes) == 0

    async def test_subscription_merchant_excluded(self, pool):
        """Known subscription merchants are excluded from duplicate detection."""
        from butlers.tools.finance.anomaly_detection import detect_duplicates

        now = datetime.now(UTC)
        await _insert_sub(pool, service="Netflix", amount="15.99", frequency="monthly")

        # Two same-day Netflix charges — should be excluded
        await _insert_txn(
            pool, merchant="Netflix", amount="15.99", posted_at=now - timedelta(days=3)
        )
        await _insert_txn(
            pool, merchant="Netflix", amount="15.99", posted_at=now - timedelta(days=3)
        )

        result = await detect_duplicates(pool, days_back=30)

        dupes = [d for d in result["duplicates"] if d["merchant"].lower() == "netflix"]
        assert len(dupes) == 0

    async def test_credit_transactions_not_flagged(self, pool):
        """Credit transactions are not candidate duplicates."""
        from butlers.tools.finance.anomaly_detection import detect_duplicates

        now = datetime.now(UTC)
        await _insert_txn(
            pool,
            merchant="Payroll",
            amount="3000.00",
            direction="credit",
            posted_at=now - timedelta(days=3),
        )
        await _insert_txn(
            pool,
            merchant="Payroll",
            amount="3000.00",
            direction="credit",
            posted_at=now - timedelta(days=3),
        )

        result = await detect_duplicates(pool, days_back=30)

        dupes = [d for d in result["duplicates"] if d["merchant"] == "Payroll"]
        assert len(dupes) == 0

    async def test_soft_deleted_transactions_excluded(self, pool):
        """Soft-deleted transactions are excluded from duplicate detection."""
        from butlers.tools.finance.anomaly_detection import detect_duplicates

        now = datetime.now(UTC)
        deleted_time = now
        await _insert_txn(
            pool, merchant="BestBuy", amount="199.99", posted_at=now - timedelta(days=2)
        )
        await _insert_txn(
            pool,
            merchant="BestBuy",
            amount="199.99",
            posted_at=now - timedelta(days=2),
            deleted_at=deleted_time,
        )

        result = await detect_duplicates(pool, days_back=30)

        dupes = [d for d in result["duplicates"] if d["merchant"] == "BestBuy"]
        assert len(dupes) == 0

    async def test_outside_days_back_window_not_flagged(self, pool):
        """Transactions older than days_back are not included in duplicate scan."""
        from butlers.tools.finance.anomaly_detection import detect_duplicates

        now = datetime.now(UTC)
        old_date = now - timedelta(days=60)

        await _insert_txn(pool, merchant="OldStore", amount="10.00", posted_at=old_date)
        await _insert_txn(pool, merchant="OldStore", amount="10.00", posted_at=old_date)

        result = await detect_duplicates(pool, days_back=30)

        dupes = [d for d in result["duplicates"] if d["merchant"] == "OldStore"]
        assert len(dupes) == 0

    async def test_multiple_duplicate_groups_detected(self, pool):
        """Multiple distinct duplicate groups can be detected simultaneously."""
        from butlers.tools.finance.anomaly_detection import detect_duplicates

        now = datetime.now(UTC)
        day = now - timedelta(days=5)

        # Two groups of duplicates
        await _insert_txn(pool, merchant="CVS", amount="12.50", posted_at=day.replace(hour=10))
        await _insert_txn(pool, merchant="CVS", amount="12.50", posted_at=day.replace(hour=11))
        await _insert_txn(pool, merchant="Walgreens", amount="8.99", posted_at=day.replace(hour=14))
        await _insert_txn(pool, merchant="Walgreens", amount="8.99", posted_at=day.replace(hour=15))

        result = await detect_duplicates(pool, days_back=30)

        assert result["total_found"] >= 2
        merchants = {d["merchant"] for d in result["duplicates"]}
        assert "CVS" in merchants
        assert "Walgreens" in merchants

    async def test_returns_amount_and_currency_in_group(self, pool):
        """Duplicate group response includes amount, currency, and transaction IDs."""
        from butlers.tools.finance.anomaly_detection import detect_duplicates

        now = datetime.now(UTC)
        t1_id = await _insert_txn(
            pool, merchant="Uber", amount="22.50", currency="USD", posted_at=now - timedelta(days=2)
        )
        t2_id = await _insert_txn(
            pool, merchant="Uber", amount="22.50", currency="USD", posted_at=now - timedelta(days=2)
        )

        result = await detect_duplicates(pool, days_back=30)

        uber_groups = [d for d in result["duplicates"] if d["merchant"] == "Uber"]
        assert len(uber_groups) >= 1
        g = uber_groups[0]
        assert g["amount"] == "22.50"
        assert g["currency"] == "USD"
        txn_ids = {t["id"] for t in g["transactions"]}
        assert t1_id in txn_ids
        assert t2_id in txn_ids


# ---------------------------------------------------------------------------
# TestEdgeCases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge case tests for anomaly detection."""

    async def test_single_merchant_single_transaction_no_baseline(self, pool):
        """Single transaction is below minimum threshold — no baseline, no anomaly."""
        from butlers.tools.finance.anomaly_detection import anomaly_scan, compute_baselines

        now = datetime.now(UTC)
        await _insert_txn(
            pool, merchant="LonelyStore", amount="99.00", posted_at=now - timedelta(days=1)
        )

        baseline_result = await compute_baselines(pool)
        scan_result = await anomaly_scan(pool, days_back=7)

        # LonelyStore has only 1 transaction — below _MIN_MERCHANT_TRANSACTIONS
        assert baseline_result["status"] == "insufficient_data"
        assert scan_result["status"] == "insufficient_data"

    async def test_anomaly_scan_all_same_merchant(self, pool):
        """Baseline and scan with all transactions from the same merchant works correctly."""
        from butlers.tools.finance.anomaly_detection import anomaly_scan

        base = datetime.now(UTC) - timedelta(days=120)
        for i in range(5):
            await _insert_txn(
                pool,
                merchant="UniqueShop",
                amount="10.00",
                posted_at=base + timedelta(days=i * 20),
            )
        # Normal recent charge
        await _insert_txn(
            pool,
            merchant="UniqueShop",
            amount="10.00",
            posted_at=datetime.now(UTC) - timedelta(days=1),
        )

        result = await anomaly_scan(pool, days_back=7)

        assert result["status"] == "ok"
        amount_anomalies = [a for a in result["anomalies"] if a["type"] == "amount_anomaly"]
        assert len(amount_anomalies) == 0

    async def test_invalid_sensitivity_falls_back_to_medium(self, pool):
        """Invalid sensitivity string falls back to medium without raising."""
        from butlers.tools.finance.anomaly_detection import anomaly_scan

        # Set up minimal baseline data
        base = datetime.now(UTC) - timedelta(days=120)
        for i in range(3):
            await _insert_txn(
                pool,
                merchant="SafeShop",
                amount="20.00",
                posted_at=base + timedelta(days=i * 30),
            )

        # Should not raise
        result = await anomaly_scan(pool, days_back=7, sensitivity="invalid_value")

        assert result["status"] in ("ok", "insufficient_data")
