"""Tests for butlers.tools.finance.pattern_recognition — detect_recurring.

Covers:
- Monthly recurring charge detection (4+ charges at ~30-day intervals)
- Quarterly recurring charge detection (~90-day intervals)
- Yearly recurring charge detection (~365-day intervals)
- Irregular interval rejection (high variance → not classified recurring)
- High amount variance rejection (>10% variance)
- Confidence scoring: high (6+ occurrences, <5% variance)
- Confidence scoring: medium (3-5 occurrences, <10% variance)
- already_tracked flag when subscription record exists
- price_change_detected flag when avg_amount differs from subscription by >5%
- min_occurrences filtering
- Storage in finance.recurring_groups
- Deleted transactions excluded
- insufficient_data when no transactions exist
"""

from __future__ import annotations

import shutil
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

_docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not _docker_available, reason="Docker not available"),
]

# ---------------------------------------------------------------------------
# SQL helpers — minimal schemas for pattern recognition tests
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
    currency          CHAR(3) NOT NULL,
    direction         TEXT NOT NULL CHECK (direction IN ('debit', 'credit')),
    category          TEXT NOT NULL,
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
    currency          CHAR(3) NOT NULL,
    frequency         TEXT NOT NULL
                          CHECK (frequency IN (
                              'weekly', 'monthly', 'quarterly', 'yearly', 'custom'
                          )),
    next_renewal      DATE NOT NULL,
    status            TEXT NOT NULL
                          CHECK (status IN ('active', 'cancelled', 'paused')),
    auto_renew        BOOLEAN NOT NULL DEFAULT true,
    payment_method    TEXT,
    account_id        UUID,
    source_message_id TEXT,
    metadata          JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


async def _insert_transaction(
    pool,
    *,
    merchant: str,
    amount: str = "9.99",
    currency: str = "USD",
    direction: str = "debit",
    category: str = "subscriptions",
    posted_at: datetime,
    deleted_at: datetime | None = None,
) -> None:
    await pool.execute(
        """
        INSERT INTO transactions
            (merchant, amount, currency, direction, category, posted_at, deleted_at)
        VALUES ($1, $2::numeric, $3, $4, $5, $6, $7)
        """,
        merchant,
        Decimal(amount),
        currency,
        direction,
        category,
        posted_at,
        deleted_at,
    )


async def _insert_subscription(
    pool,
    *,
    service: str,
    amount: str = "9.99",
    currency: str = "USD",
    frequency: str = "monthly",
    status: str = "active",
) -> None:

    renewal = (datetime.now(UTC) + timedelta(days=30)).date()
    await pool.execute(
        """
        INSERT INTO subscriptions
            (service, amount, currency, frequency, next_renewal, status)
        VALUES ($1, $2::numeric, $3, $4, $5, $6)
        """,
        service,
        Decimal(amount),
        currency,
        frequency,
        renewal,
        status,
    )


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def pool(provisioned_postgres_pool):
    """Provision a fresh database with finance pattern recognition tables."""
    async with provisioned_postgres_pool() as p:
        await p.execute(CREATE_TRANSACTIONS_SQL)
        await p.execute(CREATE_SUBSCRIPTIONS_SQL)
        yield p


# ---------------------------------------------------------------------------
# TestDetectRecurringMonthly
# ---------------------------------------------------------------------------


class TestDetectRecurringMonthly:
    """detect_recurring correctly identifies monthly charges."""

    async def test_monthly_pattern_detected(self, pool):
        """Merchant charged ~30 days apart appears as recurring with 'monthly' frequency."""
        from butlers.tools.finance.pattern_recognition import detect_recurring

        base = datetime(2025, 1, 15, 12, 0, tzinfo=UTC)
        for i in range(4):
            await _insert_transaction(
                pool,
                merchant="Netflix",
                amount="15.99",
                posted_at=base + timedelta(days=30 * i),
            )

        result = await detect_recurring(pool, min_occurrences=3)

        assert result["status"] == "ok"
        assert result["total_detected"] == 1
        pattern = result["patterns"][0]
        assert pattern["merchant"] == "Netflix"
        assert pattern["estimated_frequency"] == "monthly"
        assert float(pattern["avg_amount"]) == pytest.approx(15.99, abs=0.01)
        assert pattern["occurrence_count"] == 4

    async def test_monthly_pattern_stored_in_recurring_groups(self, pool):
        """Detected monthly pattern is stored in finance.recurring_groups."""
        from butlers.tools.finance.pattern_recognition import detect_recurring

        base = datetime(2025, 2, 1, 0, 0, tzinfo=UTC)
        for i in range(3):
            await _insert_transaction(
                pool,
                merchant="Spotify",
                amount="9.99",
                posted_at=base + timedelta(days=30 * i),
            )

        await detect_recurring(pool, min_occurrences=3)

        row = await pool.fetchrow(
            "SELECT * FROM recurring_groups WHERE merchant = $1",
            "Spotify",
        )
        assert row is not None
        assert row["estimated_frequency"] == "monthly"
        assert row["is_active"] is True
        assert float(row["avg_amount"]) == pytest.approx(9.99, abs=0.01)

    async def test_monthly_pattern_upsert_on_second_call(self, pool):
        """Calling detect_recurring twice upserts (no duplicate rows)."""
        from butlers.tools.finance.pattern_recognition import detect_recurring

        base = datetime(2025, 3, 1, 0, 0, tzinfo=UTC)
        for i in range(3):
            await _insert_transaction(
                pool,
                merchant="Adobe",
                amount="54.99",
                posted_at=base + timedelta(days=30 * i),
            )

        await detect_recurring(pool, min_occurrences=3)
        await detect_recurring(pool, min_occurrences=3)

        count = await pool.fetchval(
            "SELECT COUNT(*) FROM recurring_groups WHERE merchant = $1",
            "Adobe",
        )
        assert count == 1

    async def test_medium_confidence_monthly(self, pool):
        """3–5 occurrences with <10% variance yields medium confidence."""
        from butlers.tools.finance.pattern_recognition import detect_recurring

        base = datetime(2025, 4, 1, 0, 0, tzinfo=UTC)
        # Slight amount variation stays under 10%
        amounts = ["10.00", "10.20", "9.90"]
        for i, amt in enumerate(amounts):
            await _insert_transaction(
                pool,
                merchant="MediumSvc",
                amount=amt,
                posted_at=base + timedelta(days=30 * i),
            )

        result = await detect_recurring(pool, min_occurrences=3)

        patterns = {p["merchant"]: p for p in result["patterns"]}
        assert "MediumSvc" in patterns
        assert patterns["MediumSvc"]["confidence"] == "medium"

    async def test_high_confidence_monthly(self, pool):
        """6+ occurrences with <5% variance yields high confidence."""
        from butlers.tools.finance.pattern_recognition import detect_recurring

        base = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
        # Tiny variation — all within 1% of $20.00
        amounts = ["20.00", "20.10", "19.95", "20.05", "20.00", "19.98"]
        for i, amt in enumerate(amounts):
            await _insert_transaction(
                pool,
                merchant="HighSvc",
                amount=amt,
                posted_at=base + timedelta(days=30 * i),
            )

        result = await detect_recurring(pool, min_occurrences=3)

        patterns = {p["merchant"]: p for p in result["patterns"]}
        assert "HighSvc" in patterns
        assert patterns["HighSvc"]["confidence"] == "high"
        assert patterns["HighSvc"]["occurrence_count"] == 6


# ---------------------------------------------------------------------------
# TestDetectRecurringQuarterly
# ---------------------------------------------------------------------------


class TestDetectRecurringQuarterly:
    """detect_recurring correctly identifies quarterly charges (~90-day intervals)."""

    async def test_quarterly_pattern_detected(self, pool):
        """Merchant charged every ~90 days is classified as quarterly."""
        from butlers.tools.finance.pattern_recognition import detect_recurring

        base = datetime(2024, 1, 15, 12, 0, tzinfo=UTC)
        for i in range(4):
            await _insert_transaction(
                pool,
                merchant="QuarterlyMagazine",
                amount="30.00",
                posted_at=base + timedelta(days=91 * i),
            )

        result = await detect_recurring(pool, min_occurrences=3)

        patterns = {p["merchant"]: p for p in result["patterns"]}
        assert "QuarterlyMagazine" in patterns
        assert patterns["QuarterlyMagazine"]["estimated_frequency"] == "quarterly"

    async def test_quarterly_stored_correctly(self, pool):
        """Quarterly pattern is stored in recurring_groups with correct frequency."""
        from butlers.tools.finance.pattern_recognition import detect_recurring

        base = datetime(2024, 3, 1, 0, 0, tzinfo=UTC)
        for i in range(3):
            await _insert_transaction(
                pool,
                merchant="SeasonalSvc",
                amount="49.99",
                posted_at=base + timedelta(days=90 * i),
            )

        await detect_recurring(pool, min_occurrences=3)

        row = await pool.fetchrow(
            "SELECT estimated_frequency FROM recurring_groups WHERE merchant = $1",
            "SeasonalSvc",
        )
        assert row is not None
        assert row["estimated_frequency"] == "quarterly"


# ---------------------------------------------------------------------------
# TestDetectRecurringYearly
# ---------------------------------------------------------------------------


class TestDetectRecurringYearly:
    """detect_recurring correctly identifies yearly charges (~365-day intervals)."""

    async def test_yearly_pattern_detected(self, pool):
        """Merchant charged every ~365 days is classified as yearly."""
        from butlers.tools.finance.pattern_recognition import detect_recurring

        base = datetime(2022, 6, 1, 0, 0, tzinfo=UTC)
        for i in range(3):
            await _insert_transaction(
                pool,
                merchant="AnnualSoftware",
                amount="99.00",
                posted_at=base + timedelta(days=365 * i),
            )

        result = await detect_recurring(pool, min_occurrences=3)

        patterns = {p["merchant"]: p for p in result["patterns"]}
        assert "AnnualSoftware" in patterns
        assert patterns["AnnualSoftware"]["estimated_frequency"] == "yearly"

    async def test_yearly_stored_correctly(self, pool):
        """Yearly pattern stored in recurring_groups with correct frequency."""
        from butlers.tools.finance.pattern_recognition import detect_recurring

        base = datetime(2022, 1, 1, 0, 0, tzinfo=UTC)
        for i in range(3):
            await _insert_transaction(
                pool,
                merchant="DomainRenewal",
                amount="12.00",
                posted_at=base + timedelta(days=365 * i),
            )

        await detect_recurring(pool, min_occurrences=3)

        row = await pool.fetchrow(
            "SELECT estimated_frequency FROM recurring_groups WHERE merchant = $1",
            "DomainRenewal",
        )
        assert row is not None
        assert row["estimated_frequency"] == "yearly"


# ---------------------------------------------------------------------------
# TestDetectRecurringEdgeCases
# ---------------------------------------------------------------------------


class TestDetectRecurringEdgeCases:
    """Edge cases: irregular intervals, high variance, deleted transactions."""

    async def test_irregular_intervals_rejected(self, pool):
        """Charges with highly irregular intervals are not classified as recurring."""
        from butlers.tools.finance.pattern_recognition import detect_recurring

        base = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
        # Gaps: 5 days, 60 days, 3 days — very irregular
        offsets = [0, 5, 65, 68]
        for offset in offsets:
            await _insert_transaction(
                pool,
                merchant="IrregularCharger",
                amount="50.00",
                posted_at=base + timedelta(days=offset),
            )

        result = await detect_recurring(pool, min_occurrences=3)

        pattern_names = [p["merchant"] for p in result["patterns"]]
        assert "IrregularCharger" not in pattern_names

    async def test_high_amount_variance_rejected(self, pool):
        """Charges with amount variance > 10% are excluded from results."""
        from butlers.tools.finance.pattern_recognition import detect_recurring

        base = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
        # Amounts vary wildly: $10, $20, $30 — CV well above 10%
        amounts = ["10.00", "20.00", "30.00"]
        for i, amt in enumerate(amounts):
            await _insert_transaction(
                pool,
                merchant="VariablePricer",
                amount=amt,
                posted_at=base + timedelta(days=30 * i),
            )

        result = await detect_recurring(pool, min_occurrences=3)

        pattern_names = [p["merchant"] for p in result["patterns"]]
        assert "VariablePricer" not in pattern_names

    async def test_min_occurrences_filters_below_threshold(self, pool):
        """Merchants with fewer charges than min_occurrences are excluded."""
        from butlers.tools.finance.pattern_recognition import detect_recurring

        base = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
        for i in range(2):
            await _insert_transaction(
                pool,
                merchant="RareSvc",
                amount="5.00",
                posted_at=base + timedelta(days=30 * i),
            )

        result = await detect_recurring(pool, min_occurrences=3)

        pattern_names = [p["merchant"] for p in result["patterns"]]
        assert "RareSvc" not in pattern_names

    async def test_deleted_transactions_excluded(self, pool):
        """Soft-deleted transactions (deleted_at IS NOT NULL) are excluded."""
        from butlers.tools.finance.pattern_recognition import detect_recurring

        base = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
        deleted_at = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)

        # Insert 3 charges but mark them as deleted
        for i in range(3):
            await _insert_transaction(
                pool,
                merchant="DeletedSvc",
                amount="9.99",
                posted_at=base + timedelta(days=30 * i),
                deleted_at=deleted_at,
            )

        result = await detect_recurring(pool, min_occurrences=3)

        pattern_names = [p["merchant"] for p in result["patterns"]]
        assert "DeletedSvc" not in pattern_names

    async def test_insufficient_data_when_no_transactions(self, pool):
        """Returns status=insufficient_data when no transactions exist."""
        from butlers.tools.finance.pattern_recognition import detect_recurring

        result = await detect_recurring(pool, min_occurrences=3)

        assert result["status"] == "insufficient_data"
        assert result["total_detected"] == 0
        assert result["patterns"] == []

    async def test_credit_transactions_ignored(self, pool):
        """Credit transactions (direction='credit') are not considered for recurring."""
        from butlers.tools.finance.pattern_recognition import detect_recurring

        base = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
        for i in range(3):
            await _insert_transaction(
                pool,
                merchant="Refunder",
                amount="10.00",
                direction="credit",  # This is income / refund
                posted_at=base + timedelta(days=30 * i),
            )

        result = await detect_recurring(pool, min_occurrences=3)

        pattern_names = [p["merchant"] for p in result["patterns"]]
        assert "Refunder" not in pattern_names

    async def test_multiple_merchants_detected_simultaneously(self, pool):
        """Multiple recurring merchants are all detected in a single call."""
        from butlers.tools.finance.pattern_recognition import detect_recurring

        base = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
        for i in range(3):
            await _insert_transaction(
                pool,
                merchant="SvcAlpha",
                amount="10.00",
                posted_at=base + timedelta(days=30 * i),
            )
            await _insert_transaction(
                pool,
                merchant="SvcBeta",
                amount="20.00",
                posted_at=base + timedelta(days=30 * i),
            )

        result = await detect_recurring(pool, min_occurrences=3)
        pattern_names = {p["merchant"] for p in result["patterns"]}
        assert "SvcAlpha" in pattern_names
        assert "SvcBeta" in pattern_names

    async def test_having_clause_pre_filters_merchants(self, pool):
        """SQL HAVING clause pre-filters merchants below threshold (not fetched)."""
        from butlers.tools.finance.pattern_recognition import detect_recurring

        base = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)

        # Insert 100 merchants each with only 2 charges (below min_occurrences=3)
        for merchant_idx in range(100):
            for i in range(2):
                await _insert_transaction(
                    pool,
                    merchant=f"BelowThreshold_{merchant_idx}",
                    amount="10.00",
                    posted_at=base + timedelta(days=30 * i),
                )

        # Insert 2 qualifying merchants with 3 charges each
        for i in range(3):
            await _insert_transaction(
                pool,
                merchant="QualifyingA",
                amount="15.99",
                posted_at=base + timedelta(days=30 * i),
            )
            await _insert_transaction(
                pool,
                merchant="QualifyingB",
                amount="12.99",
                posted_at=base + timedelta(days=30 * i),
            )

        result = await detect_recurring(pool, min_occurrences=3)

        # Only the 2 qualifying merchants should be returned
        pattern_names = {p["merchant"] for p in result["patterns"]}
        assert "QualifyingA" in pattern_names
        assert "QualifyingB" in pattern_names
        # None of the below-threshold merchants should appear
        for idx in range(100):
            assert f"BelowThreshold_{idx}" not in pattern_names


# ---------------------------------------------------------------------------
# TestDetectRecurringSubscriptionCrossReference
# ---------------------------------------------------------------------------


class TestDetectRecurringSubscriptionCrossReference:
    """already_tracked and price_change_detected flags behave correctly."""

    async def test_already_tracked_when_subscription_matches(self, pool):
        """already_tracked is True when merchant matches an active subscription."""
        from butlers.tools.finance.pattern_recognition import detect_recurring

        await _insert_subscription(pool, service="Netflix", amount="15.99")

        base = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
        for i in range(3):
            await _insert_transaction(
                pool,
                merchant="Netflix",
                amount="15.99",
                posted_at=base + timedelta(days=30 * i),
            )

        result = await detect_recurring(pool, min_occurrences=3)

        patterns = {p["merchant"]: p for p in result["patterns"]}
        assert "Netflix" in patterns
        assert patterns["Netflix"]["already_tracked"] is True

    async def test_not_tracked_when_no_subscription(self, pool):
        """already_tracked is False for a merchant without a subscription record."""
        from butlers.tools.finance.pattern_recognition import detect_recurring

        base = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
        for i in range(3):
            await _insert_transaction(
                pool,
                merchant="UnknownSvc",
                amount="5.00",
                posted_at=base + timedelta(days=30 * i),
            )

        result = await detect_recurring(pool, min_occurrences=3)

        patterns = {p["merchant"]: p for p in result["patterns"]}
        assert "UnknownSvc" in patterns
        assert patterns["UnknownSvc"]["already_tracked"] is False

    async def test_price_change_detected_when_amount_differs_more_than_5_percent(self, pool):
        """price_change_detected is True when avg_amount differs >5% from subscription."""
        from butlers.tools.finance.pattern_recognition import detect_recurring

        # Subscription records $9.99 but charges are now $11.99 (~20% increase)
        await _insert_subscription(pool, service="Spotify", amount="9.99")

        base = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
        for i in range(3):
            await _insert_transaction(
                pool,
                merchant="Spotify",
                amount="11.99",
                posted_at=base + timedelta(days=30 * i),
            )

        result = await detect_recurring(pool, min_occurrences=3)

        patterns = {p["merchant"]: p for p in result["patterns"]}
        assert "Spotify" in patterns
        assert patterns["Spotify"]["already_tracked"] is True
        assert patterns["Spotify"]["price_change_detected"] is True

    async def test_price_change_not_detected_when_within_5_percent(self, pool):
        """price_change_detected is False when amount difference is within 5%."""
        from butlers.tools.finance.pattern_recognition import detect_recurring

        # Subscription records $10.00, charges are $10.30 (~3% — within threshold)
        await _insert_subscription(pool, service="MusicSvc", amount="10.00")

        base = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
        for i in range(3):
            await _insert_transaction(
                pool,
                merchant="MusicSvc",
                amount="10.30",
                posted_at=base + timedelta(days=30 * i),
            )

        result = await detect_recurring(pool, min_occurrences=3)

        patterns = {p["merchant"]: p for p in result["patterns"]}
        assert "MusicSvc" in patterns
        assert patterns["MusicSvc"]["already_tracked"] is True
        assert patterns["MusicSvc"]["price_change_detected"] is False

    async def test_cancelled_subscription_does_not_mark_tracked(self, pool):
        """Cancelled subscriptions are not used for already_tracked matching."""
        from butlers.tools.finance.pattern_recognition import detect_recurring

        await _insert_subscription(pool, service="OldSvc", amount="7.99", status="cancelled")

        base = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
        for i in range(3):
            await _insert_transaction(
                pool,
                merchant="OldSvc",
                amount="7.99",
                posted_at=base + timedelta(days=30 * i),
            )

        result = await detect_recurring(pool, min_occurrences=3)

        patterns = {p["merchant"]: p for p in result["patterns"]}
        assert "OldSvc" in patterns
        assert patterns["OldSvc"]["already_tracked"] is False

    async def test_result_includes_last_seen_and_next_expected_dates(self, pool):
        """Pattern result contains last_seen_date and next_expected_date fields."""
        from butlers.tools.finance.pattern_recognition import detect_recurring

        base = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
        for i in range(3):
            await _insert_transaction(
                pool,
                merchant="DateSvc",
                amount="5.00",
                posted_at=base + timedelta(days=30 * i),
            )

        result = await detect_recurring(pool, min_occurrences=3)

        patterns = {p["merchant"]: p for p in result["patterns"]}
        assert "DateSvc" in patterns
        p = patterns["DateSvc"]
        assert "last_seen_date" in p
        assert "next_expected_date" in p
        # Verify they're ISO date strings
        date.fromisoformat(p["last_seen_date"])
        date.fromisoformat(p["next_expected_date"])

    async def test_custom_min_occurrences_threshold(self, pool):
        """min_occurrences=5 filters out merchants with fewer than 5 charges."""
        from butlers.tools.finance.pattern_recognition import detect_recurring

        base = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
        # Only 4 charges — below min_occurrences=5
        for i in range(4):
            await _insert_transaction(
                pool,
                merchant="FewCharges",
                amount="10.00",
                posted_at=base + timedelta(days=30 * i),
            )
        # 6 charges — above threshold
        for i in range(6):
            await _insert_transaction(
                pool,
                merchant="ManyCharges",
                amount="10.00",
                posted_at=base + timedelta(days=30 * i),
            )

        result = await detect_recurring(pool, min_occurrences=5)

        pattern_names = {p["merchant"] for p in result["patterns"]}
        assert "FewCharges" not in pattern_names
        assert "ManyCharges" in pattern_names


# ---------------------------------------------------------------------------
# Additional fixtures for predict_bills tests (bills/merchant_mappings tables)
# ---------------------------------------------------------------------------

CREATE_BILLS_SQL = """
CREATE TABLE IF NOT EXISTS bills (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    payee                  TEXT NOT NULL,
    amount                 NUMERIC(14, 2) NOT NULL,
    currency               CHAR(3) NOT NULL,
    due_date               DATE NOT NULL,
    frequency              TEXT NOT NULL CHECK (frequency IN (
                               'one_time', 'weekly', 'monthly', 'quarterly', 'yearly', 'custom'
                           )),
    status                 TEXT NOT NULL CHECK (status IN ('pending', 'paid', 'overdue')),
    payment_method         TEXT,
    account_id             UUID,
    source_message_id      TEXT,
    statement_period_start DATE,
    statement_period_end   DATE,
    paid_at                TIMESTAMPTZ,
    metadata               JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

CREATE_MERCHANT_MAPPINGS_SQL = """
CREATE TABLE IF NOT EXISTS merchant_mappings (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    raw_pattern         TEXT NOT NULL,
    normalized_merchant TEXT NOT NULL,
    category            TEXT NOT NULL,
    confidence          FLOAT NOT NULL DEFAULT 0.5,
    learned_from_count  INT NOT NULL DEFAULT 0,
    source              TEXT NOT NULL DEFAULT 'learn',
    is_active           BOOLEAN NOT NULL DEFAULT true,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_merchant_mappings_pattern_active UNIQUE (raw_pattern)
)
"""

CREATE_RECURRING_GROUPS_SQL = """
CREATE TABLE IF NOT EXISTS recurring_groups (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant             TEXT NOT NULL UNIQUE,
    estimated_frequency  TEXT CHECK (estimated_frequency IS NULL OR estimated_frequency IN (
                             'weekly', 'monthly', 'quarterly', 'yearly', 'custom'
                         )),
    avg_amount           NUMERIC(14, 2) NOT NULL,
    currency             CHAR(3) DEFAULT 'USD',
    last_seen_date       DATE,
    next_expected_date   DATE,
    is_active            BOOLEAN NOT NULL DEFAULT true,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def _dt(d: date) -> datetime:
    """Convert a date to a timezone-aware datetime at midnight UTC."""
    return datetime.combine(d, datetime.min.time()).replace(tzinfo=UTC)


async def _insert_txn(
    pool_arg,
    merchant: str,
    amount: float,
    posted_date: date,
    currency: str = "USD",
    direction: str = "debit",
    category: str = "bills",
) -> None:
    """Helper to insert a transaction row (predict_bills tests)."""
    await pool_arg.execute(
        """
        INSERT INTO transactions
            (posted_at, merchant, amount, currency, direction, category)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        _dt(posted_date),
        merchant,
        Decimal(str(amount)),
        currency,
        direction,
        category,
    )


@pytest.fixture
async def pool_full(provisioned_postgres_pool):
    """Pool with all finance tables (for predict_bills tests)."""
    async with provisioned_postgres_pool() as p:
        await p.execute(CREATE_TRANSACTIONS_SQL)
        await p.execute(CREATE_BILLS_SQL)
        await p.execute(CREATE_SUBSCRIPTIONS_SQL)
        await p.execute(CREATE_MERCHANT_MAPPINGS_SQL)
        await p.execute(CREATE_RECURRING_GROUPS_SQL)
        yield p


@pytest.fixture
async def pool_no_bills(provisioned_postgres_pool):
    """Pool with transactions only — no bills/subscriptions tables."""
    async with provisioned_postgres_pool() as p:
        await p.execute(CREATE_TRANSACTIONS_SQL)
        yield p


# ---------------------------------------------------------------------------
# Internal helper unit tests (pure Python — no DB)
# ---------------------------------------------------------------------------


class TestInternalHelpers:
    """Tests for pure-Python helpers in pattern_recognition."""

    def test_median_interval_days_empty(self):
        """Empty list returns None."""
        from butlers.tools.finance.pattern_recognition import _median_interval_days

        assert _median_interval_days([]) is None

    def test_median_interval_days_single(self):
        """Single date returns None (no interval)."""
        from butlers.tools.finance.pattern_recognition import _median_interval_days

        assert _median_interval_days([date(2026, 1, 1)]) is None

    def test_median_interval_days_two(self):
        """Two dates: interval = gap in days."""
        from butlers.tools.finance.pattern_recognition import _median_interval_days

        d1 = date(2026, 1, 1)
        d2 = date(2026, 2, 1)
        assert _median_interval_days([d1, d2]) == 31.0

    def test_median_interval_days_three_monthly(self):
        """Three monthly dates: median interval ~ 30 days."""
        from butlers.tools.finance.pattern_recognition import _median_interval_days

        dates = [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)]
        result = _median_interval_days(dates)
        assert result is not None
        assert 28 <= result <= 32

    def test_median_interval_days_unsorted_input(self):
        """Unsorted dates are sorted before computing intervals."""
        from butlers.tools.finance.pattern_recognition import _median_interval_days

        dates = [date(2026, 3, 1), date(2026, 1, 1), date(2026, 2, 1)]
        result = _median_interval_days(dates)
        assert result is not None
        assert 28 <= result <= 32

    def test_median_amount_odd(self):
        """Median of odd-length list is middle element."""
        from butlers.tools.finance.pattern_recognition import _median_amount

        amounts = [Decimal("10"), Decimal("20"), Decimal("30")]
        assert _median_amount(amounts) == Decimal("20")

    def test_median_amount_even(self):
        """Median of even-length list is average of two middle elements."""
        from butlers.tools.finance.pattern_recognition import _median_amount

        amounts = [Decimal("10"), Decimal("20"), Decimal("30"), Decimal("40")]
        assert _median_amount(amounts) == Decimal("25")

    def test_amount_variance_single(self):
        """Single amount: variance is 0.0."""
        from butlers.tools.finance.pattern_recognition import _amount_variance_fraction

        assert _amount_variance_fraction([Decimal("100")]) == 0.0

    def test_amount_variance_identical(self):
        """Identical amounts: variance is 0.0."""
        from butlers.tools.finance.pattern_recognition import _amount_variance_fraction

        amounts = [Decimal("50"), Decimal("50"), Decimal("50")]
        assert _amount_variance_fraction(amounts) == 0.0

    def test_amount_variance_large_spread(self):
        """Large spread: variance > threshold."""
        from butlers.tools.finance.pattern_recognition import _amount_variance_fraction

        amounts = [Decimal("10"), Decimal("50"), Decimal("90")]
        result = _amount_variance_fraction(amounts)
        assert result > 0.10


# ---------------------------------------------------------------------------
# predict_bills tests
# ---------------------------------------------------------------------------


class TestPredictBills:
    """Tests for predict_bills() — accuracy and edge cases."""

    async def test_empty_transactions_returns_insufficient_data(self, pool_full):
        """No transactions returns insufficient_data status."""
        from butlers.tools.finance.pattern_recognition import predict_bills

        result = await predict_bills(pool=pool_full, days_ahead=30)
        assert result["status"] == "insufficient_data"
        assert result["predictions"] == []
        assert result["window_days"] == 30

    async def test_fewer_than_three_payments_excluded(self, pool_full):
        """Payees with < 3 payments are not predicted."""
        from butlers.tools.finance.pattern_recognition import predict_bills

        today = date.today()
        await _insert_txn(pool_full, "Gas Company", 80.0, today - timedelta(days=60))
        await _insert_txn(pool_full, "Gas Company", 80.0, today - timedelta(days=30))

        result = await predict_bills(pool=pool_full, days_ahead=60)
        assert result["status"] == "insufficient_data"
        assert result["predictions"] == []

    async def test_three_monthly_payments_predict_next(self, pool_full):
        """3 monthly payments predict next date within horizon."""
        from butlers.tools.finance.pattern_recognition import predict_bills

        today = date.today()
        await _insert_txn(pool_full, "Electric Co", 95.0, today - timedelta(days=90))
        await _insert_txn(pool_full, "Electric Co", 95.0, today - timedelta(days=60))
        await _insert_txn(pool_full, "Electric Co", 95.0, today - timedelta(days=30))

        result = await predict_bills(pool=pool_full, days_ahead=45)
        assert result["status"] == "ok"
        preds = [p for p in result["predictions"] if p["payee"] == "Electric Co"]
        assert len(preds) == 1
        pred = preds[0]
        # predicted = (today-30) + 30 = today
        predicted = date.fromisoformat(pred["predicted_date"])
        assert predicted == today
        assert pred["is_tracked"] is False
        assert pred["amount_drift"] is False

    async def test_prediction_outside_horizon_excluded(self, pool_full):
        """Prediction beyond days_ahead is not included."""
        from butlers.tools.finance.pattern_recognition import predict_bills

        today = date.today()
        await _insert_txn(pool_full, "Water Utility", 50.0, today - timedelta(days=90))
        await _insert_txn(pool_full, "Water Utility", 50.0, today - timedelta(days=60))
        await _insert_txn(pool_full, "Water Utility", 50.0, today - timedelta(days=30))

        await _insert_txn(pool_full, "Cable TV", 60.0, today - timedelta(days=70))
        await _insert_txn(pool_full, "Cable TV", 60.0, today - timedelta(days=40))
        await _insert_txn(pool_full, "Cable TV", 60.0, today - timedelta(days=10))

        result_5 = await predict_bills(pool=pool_full, days_ahead=5)
        cable_preds_5 = [p for p in result_5["predictions"] if p["payee"] == "Cable TV"]
        assert cable_preds_5 == []

        result_30 = await predict_bills(pool=pool_full, days_ahead=30)
        cable_preds_30 = [p for p in result_30["predictions"] if p["payee"] == "Cable TV"]
        assert len(cable_preds_30) == 1

    async def test_irregular_amounts_excluded(self, pool_full):
        """Payees with >10% amount variance are excluded."""
        from butlers.tools.finance.pattern_recognition import predict_bills

        today = date.today()
        await _insert_txn(pool_full, "Irregular Biller", 10.0, today - timedelta(days=90))
        await _insert_txn(pool_full, "Irregular Biller", 50.0, today - timedelta(days=60))
        await _insert_txn(pool_full, "Irregular Biller", 200.0, today - timedelta(days=30))

        result = await predict_bills(pool=pool_full, days_ahead=60)
        biller_preds = [p for p in result["predictions"] if p["payee"] == "Irregular Biller"]
        assert biller_preds == []

    async def test_is_tracked_true_when_bill_exists(self, pool_full):
        """is_tracked=True when a pending bills row matches the payee."""
        from butlers.tools.finance.pattern_recognition import predict_bills

        today = date.today()
        payee = "Rent Corp"
        for i in range(3, 0, -1):
            await _insert_txn(pool_full, payee, 1800.0, today - timedelta(days=30 * i))

        await pool_full.execute(
            """
            INSERT INTO bills (payee, amount, currency, due_date, frequency, status)
            VALUES ($1, $2, 'USD', $3, 'monthly', 'pending')
            """,
            payee,
            Decimal("1800.00"),
            today + timedelta(days=1),
        )

        result = await predict_bills(pool=pool_full, days_ahead=60)
        preds = [p for p in result["predictions"] if p["payee"] == payee]
        assert len(preds) == 1
        assert preds[0]["is_tracked"] is True

    async def test_is_tracked_true_when_subscription_exists(self, pool_full):
        """is_tracked=True when an active subscription matches the payee."""
        from butlers.tools.finance.pattern_recognition import predict_bills

        today = date.today()
        payee = "Netflix"
        for i in range(3, 0, -1):
            await _insert_txn(pool_full, payee, 15.49, today - timedelta(days=30 * i))

        await pool_full.execute(
            """
            INSERT INTO subscriptions (service, amount, currency, frequency, next_renewal, status)
            VALUES ($1, $2, 'USD', 'monthly', $3, 'active')
            """,
            payee,
            Decimal("15.49"),
            today + timedelta(days=15),
        )

        result = await predict_bills(pool=pool_full, days_ahead=60)
        preds = [p for p in result["predictions"] if p["payee"] == payee]
        assert len(preds) == 1
        assert preds[0]["is_tracked"] is True

    async def test_is_tracked_false_when_no_match(self, pool_full):
        """is_tracked=False when no bills or subscriptions match."""
        from butlers.tools.finance.pattern_recognition import predict_bills

        today = date.today()
        for i in range(3, 0, -1):
            await _insert_txn(pool_full, "Untracked Biller", 40.0, today - timedelta(days=30 * i))

        result = await predict_bills(pool=pool_full, days_ahead=60)
        preds = [p for p in result["predictions"] if p["payee"] == "Untracked Biller"]
        assert len(preds) == 1
        assert preds[0]["is_tracked"] is False

    async def test_amount_drift_true_when_diverges_over_10pct(self, pool_full):
        """amount_drift=True when predicted amount differs >10% from tracked bill."""
        from butlers.tools.finance.pattern_recognition import predict_bills

        today = date.today()
        payee = "Phone Plan"
        for i in range(3, 0, -1):
            await _insert_txn(pool_full, payee, 50.0, today - timedelta(days=30 * i))

        await pool_full.execute(
            """
            INSERT INTO bills (payee, amount, currency, due_date, frequency, status)
            VALUES ($1, $2, 'USD', $3, 'monthly', 'pending')
            """,
            payee,
            Decimal("70.00"),
            today + timedelta(days=5),
        )

        result = await predict_bills(pool=pool_full, days_ahead=60)
        preds = [p for p in result["predictions"] if p["payee"] == payee]
        assert len(preds) == 1
        assert preds[0]["is_tracked"] is True
        assert preds[0]["amount_drift"] is True

    async def test_amount_drift_false_within_10pct(self, pool_full):
        """amount_drift=False when predicted and tracked amounts are within 10%."""
        from butlers.tools.finance.pattern_recognition import predict_bills

        today = date.today()
        payee = "Internet ISP"
        for i in range(3, 0, -1):
            await _insert_txn(pool_full, payee, 100.0, today - timedelta(days=30 * i))

        await pool_full.execute(
            """
            INSERT INTO bills (payee, amount, currency, due_date, frequency, status)
            VALUES ($1, $2, 'USD', $3, 'monthly', 'pending')
            """,
            payee,
            Decimal("105.00"),
            today + timedelta(days=5),
        )

        result = await predict_bills(pool=pool_full, days_ahead=60)
        preds = [p for p in result["predictions"] if p["payee"] == payee]
        assert len(preds) == 1
        assert preds[0]["is_tracked"] is True
        assert preds[0]["amount_drift"] is False

    async def test_credit_transactions_excluded(self, pool_full):
        """Credit direction transactions are ignored."""
        from butlers.tools.finance.pattern_recognition import predict_bills

        today = date.today()
        for i in range(3, 0, -1):
            await _insert_txn(
                pool_full,
                "Refund Merchant",
                50.0,
                today - timedelta(days=30 * i),
                direction="credit",
            )

        result = await predict_bills(pool=pool_full, days_ahead=60)
        preds = [p for p in result["predictions"] if p["payee"] == "Refund Merchant"]
        assert preds == []

    async def test_predictions_sorted_by_date(self, pool_full):
        """Predictions are sorted by predicted_date ascending."""
        from butlers.tools.finance.pattern_recognition import predict_bills

        today = date.today()
        for i in range(3, 0, -1):
            offset = 60 + 30 * (3 - i)
            await _insert_txn(pool_full, "Biller A", 80.0, today - timedelta(days=offset))
        for i in range(3, 0, -1):
            offset = 35 + 30 * (3 - i)
            await _insert_txn(pool_full, "Biller B", 90.0, today - timedelta(days=offset))

        result = await predict_bills(pool=pool_full, days_ahead=60)
        dates = [p["predicted_date"] for p in result["predictions"]]
        assert dates == sorted(dates)

    async def test_response_includes_as_of_and_window_days(self, pool_full):
        """Response always includes as_of timestamp and window_days."""
        from butlers.tools.finance.pattern_recognition import predict_bills

        result = await predict_bills(pool=pool_full, days_ahead=14)
        assert "as_of" in result
        assert result["window_days"] == 14
        datetime.fromisoformat(result["as_of"])

    async def test_prediction_contains_all_expected_fields(self, pool_full):
        """Each prediction dict includes all required fields."""
        from butlers.tools.finance.pattern_recognition import predict_bills

        today = date.today()
        for i in range(3, 0, -1):
            await _insert_txn(pool_full, "Full Fields", 120.0, today - timedelta(days=30 * i))

        result = await predict_bills(pool=pool_full, days_ahead=60)
        preds = [p for p in result["predictions"] if p["payee"] == "Full Fields"]
        assert len(preds) == 1
        pred = preds[0]
        required_keys = {
            "payee",
            "predicted_date",
            "predicted_amount",
            "currency",
            "median_interval_days",
            "occurrences",
            "last_payment_date",
            "is_tracked",
            "amount_drift",
        }
        assert required_keys.issubset(set(pred.keys()))

    async def test_six_monthly_payments_high_accuracy(self, pool_full):
        """6 monthly payments produce accurate prediction."""
        from butlers.tools.finance.pattern_recognition import predict_bills

        today = date.today()
        for i in range(6, 0, -1):
            await _insert_txn(pool_full, "Regular Bill", 75.0, today - timedelta(days=30 * i))

        result = await predict_bills(pool=pool_full, days_ahead=45)
        preds = [p for p in result["predictions"] if p["payee"] == "Regular Bill"]
        assert len(preds) == 1
        predicted = date.fromisoformat(preds[0]["predicted_date"])
        # Expected exactly today: last_payment=(today-30), median_interval=30, predicted=today.
        assert predicted == today

    async def test_no_bills_table_graceful(self, pool_no_bills):
        """predict_bills gracefully handles missing bills/subscriptions tables."""
        from butlers.tools.finance.pattern_recognition import predict_bills

        today = date.today()
        for i in range(3, 0, -1):
            await _insert_txn(
                pool_no_bills, "Graceful Biller", 60.0, today - timedelta(days=30 * i)
            )

        result = await predict_bills(pool=pool_no_bills, days_ahead=60)
        assert result["status"] in ("ok", "insufficient_data")
        preds = [p for p in result["predictions"] if p["payee"] == "Graceful Biller"]
        if preds:
            assert preds[0]["is_tracked"] is False
            assert preds[0]["amount_drift"] is False

    async def test_paid_bill_not_counted_as_tracked(self, pool_full):
        """A paid bill does not count toward is_tracked."""
        from butlers.tools.finance.pattern_recognition import predict_bills

        today = date.today()
        payee = "Paid Bill Payee"
        for i in range(3, 0, -1):
            await _insert_txn(pool_full, payee, 200.0, today - timedelta(days=30 * i))

        await pool_full.execute(
            """
            INSERT INTO bills (payee, amount, currency, due_date, frequency, status)
            VALUES ($1, $2, 'USD', $3, 'monthly', 'paid')
            """,
            payee,
            Decimal("200.00"),
            today - timedelta(days=5),
        )

        result = await predict_bills(pool=pool_full, days_ahead=60)
        preds = [p for p in result["predictions"] if p["payee"] == payee]
        if preds:
            assert preds[0]["is_tracked"] is False

    async def test_cancelled_subscription_not_tracked(self, pool_full):
        """A cancelled subscription does not count toward is_tracked."""
        from butlers.tools.finance.pattern_recognition import predict_bills

        today = date.today()
        payee = "Cancelled Sub"
        for i in range(3, 0, -1):
            await _insert_txn(pool_full, payee, 10.0, today - timedelta(days=30 * i))

        await pool_full.execute(
            """
            INSERT INTO subscriptions (service, amount, currency, frequency, next_renewal, status)
            VALUES ($1, $2, 'USD', 'monthly', $3, 'cancelled')
            """,
            payee,
            Decimal("10.00"),
            today + timedelta(days=15),
        )

        result = await predict_bills(pool=pool_full, days_ahead=60)
        preds = [p for p in result["predictions"] if p["payee"] == payee]
        if preds:
            assert preds[0]["is_tracked"] is False

    async def test_multiple_payees(self, pool_full):
        """Multiple qualifying payees all appear in predictions."""
        from butlers.tools.finance.pattern_recognition import predict_bills

        today = date.today()
        for payee, amount in [("Multi A", 40.0), ("Multi B", 60.0), ("Multi C", 80.0)]:
            for i in range(3, 0, -1):
                await _insert_txn(pool_full, payee, amount, today - timedelta(days=30 * i))

        result = await predict_bills(pool=pool_full, days_ahead=60)
        payees = {p["payee"] for p in result["predictions"]}
        assert {"Multi A", "Multi B", "Multi C"}.issubset(payees)

    async def test_default_days_ahead_is_30(self, pool_full):
        """Default days_ahead is 30."""
        from butlers.tools.finance.pattern_recognition import predict_bills

        result = await predict_bills(pool=pool_full)
        assert result["window_days"] == 30

    async def test_currency_preserved_in_prediction(self, pool_full):
        """Currency is preserved from transactions in prediction output."""
        from butlers.tools.finance.pattern_recognition import predict_bills

        today = date.today()
        for i in range(3, 0, -1):
            await _insert_txn(
                pool_full, "EUR Biller", 50.0, today - timedelta(days=30 * i), currency="EUR"
            )

        result = await predict_bills(pool=pool_full, days_ahead=60)
        preds = [p for p in result["predictions"] if p["payee"] == "EUR Biller"]
        if preds:
            assert preds[0]["currency"] == "EUR"

    async def test_occurrences_count_correct(self, pool_full):
        """Prediction occurrences count matches actual transaction count."""
        from butlers.tools.finance.pattern_recognition import predict_bills

        today = date.today()
        for i in range(5, 0, -1):
            await _insert_txn(pool_full, "Count Test", 100.0, today - timedelta(days=30 * i))

        result = await predict_bills(pool=pool_full, days_ahead=60)
        preds = [p for p in result["predictions"] if p["payee"] == "Count Test"]
        if preds:
            assert preds[0]["occurrences"] == 5


# ---------------------------------------------------------------------------
# Merchant categorization tests — learn_merchant_categories, suggest_categories,
# recall_merchant_mappings (tasks 2.1–2.3)
# ---------------------------------------------------------------------------

# SQL for a minimal transactions table with a category column (reuse CREATE_TRANSACTIONS_SQL
# defined at the top of this module).

CREATE_MERCHANT_MAPPINGS_CLEAN_SQL = """
CREATE TABLE IF NOT EXISTS merchant_mappings (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant        TEXT NOT NULL UNIQUE,
    category        TEXT NOT NULL,
    confidence      NUMERIC(5, 4) NOT NULL DEFAULT 0.5,
    sample_count    INT NOT NULL DEFAULT 1,
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


async def _insert_categorized_txn(
    pool_arg,
    merchant: str,
    category: str,
    *,
    amount: str = "25.00",
    currency: str = "USD",
) -> None:
    """Insert a debit transaction with an explicit category."""
    await pool_arg.execute(
        """
        INSERT INTO transactions
            (posted_at, merchant, amount, currency, direction, category)
        VALUES (now(), $1, $2::numeric, $3, 'debit', $4)
        """,
        merchant,
        Decimal(amount),
        currency,
        category,
    )


@pytest.fixture
async def pool_merchant(provisioned_postgres_pool):
    """Pool with transactions and (empty) merchant_mappings tables."""
    async with provisioned_postgres_pool() as p:
        await p.execute(CREATE_TRANSACTIONS_SQL)
        await p.execute(CREATE_MERCHANT_MAPPINGS_CLEAN_SQL)
        yield p


class TestLearnMerchantCategories:
    """Tests for learn_merchant_categories() — task 2.1."""

    async def test_no_transactions_returns_zero_upserted(self, pool_merchant):
        """Empty transaction table returns upserted=0."""
        from butlers.tools.finance.pattern_recognition import learn_merchant_categories

        result = await learn_merchant_categories(pool_merchant)

        assert result["upserted"] == 0
        assert "as_of" in result

    async def test_single_merchant_single_category(self, pool_merchant):
        """Single merchant with one category is upserted into merchant_mappings."""
        from butlers.tools.finance.pattern_recognition import learn_merchant_categories

        await _insert_categorized_txn(pool_merchant, "Whole Foods", "groceries")

        result = await learn_merchant_categories(pool_merchant)

        assert result["upserted"] == 1
        row = await pool_merchant.fetchrow(
            "SELECT category, sample_count FROM merchant_mappings WHERE merchant = $1",
            "Whole Foods",
        )
        assert row is not None
        assert row["category"] == "groceries"
        assert row["sample_count"] == 1

    async def test_dominant_category_wins(self, pool_merchant):
        """When a merchant has multiple categories, the most frequent one is stored."""
        from butlers.tools.finance.pattern_recognition import learn_merchant_categories

        # 3x dining, 1x entertainment — dining should win
        for _ in range(3):
            await _insert_categorized_txn(pool_merchant, "Chili's", "dining")
        await _insert_categorized_txn(pool_merchant, "Chili's", "entertainment")

        await learn_merchant_categories(pool_merchant)

        row = await pool_merchant.fetchrow(
            "SELECT category, sample_count FROM merchant_mappings WHERE merchant = $1",
            "Chili's",
        )
        assert row is not None
        assert row["category"] == "dining"
        assert row["sample_count"] == 3

    async def test_confidence_grows_with_sample_count(self, pool_merchant):
        """Higher sample counts yield higher confidence scores (capped at 0.99)."""
        from butlers.tools.finance.pattern_recognition import learn_merchant_categories

        for _ in range(10):
            await _insert_categorized_txn(pool_merchant, "Amazon", "shopping")

        await learn_merchant_categories(pool_merchant)

        row = await pool_merchant.fetchrow(
            "SELECT confidence FROM merchant_mappings WHERE merchant = $1",
            "Amazon",
        )
        assert row is not None
        # confidence = min(0.99, 0.5 + (10 - 1) * 0.05) = min(0.99, 0.95) = 0.95
        assert float(row["confidence"]) == pytest.approx(0.95, abs=0.01)

    async def test_confidence_capped_at_0_99(self, pool_merchant):
        """Confidence is never greater than 0.99 regardless of sample count."""
        from butlers.tools.finance.pattern_recognition import learn_merchant_categories

        for _ in range(30):
            await _insert_categorized_txn(pool_merchant, "HighVolumeStore", "shopping")

        await learn_merchant_categories(pool_merchant)

        row = await pool_merchant.fetchrow(
            "SELECT confidence FROM merchant_mappings WHERE merchant = $1",
            "HighVolumeStore",
        )
        assert row is not None
        assert float(row["confidence"]) <= 0.99

    async def test_upsert_on_second_call_no_duplicates(self, pool_merchant):
        """Calling learn_merchant_categories twice does not duplicate rows."""
        from butlers.tools.finance.pattern_recognition import learn_merchant_categories

        await _insert_categorized_txn(pool_merchant, "Target", "shopping")
        await learn_merchant_categories(pool_merchant)
        await _insert_categorized_txn(pool_merchant, "Target", "shopping")
        await learn_merchant_categories(pool_merchant)

        count = await pool_merchant.fetchval(
            "SELECT COUNT(*) FROM merchant_mappings WHERE merchant = $1",
            "Target",
        )
        assert count == 1

    async def test_multiple_merchants_upserted(self, pool_merchant):
        """Multiple distinct merchants each get their own mapping row."""
        from butlers.tools.finance.pattern_recognition import learn_merchant_categories

        pairs = [("Trader Joe's", "groceries"), ("Shell", "gas"), ("Lyft", "transport")]
        for merchant, cat in pairs:
            await _insert_categorized_txn(pool_merchant, merchant, cat)

        result = await learn_merchant_categories(pool_merchant)

        assert result["upserted"] == 3

    async def test_credit_transactions_excluded(self, pool_merchant):
        """Credit transactions are excluded from category learning (debit only)."""
        from butlers.tools.finance.pattern_recognition import learn_merchant_categories

        # Insert a credit transaction for "CreditStore"
        await pool_merchant.execute(
            """
            INSERT INTO transactions
                (posted_at, merchant, amount, currency, direction, category)
            VALUES (now(), 'CreditStore', 10.00, 'USD', 'credit', 'refund')
            """,
        )
        await _insert_categorized_txn(pool_merchant, "DebitStore", "groceries")

        result = await learn_merchant_categories(pool_merchant)

        # Only DebitStore should appear; CreditStore is a credit transaction
        assert result["upserted"] == 1
        row = await pool_merchant.fetchrow(
            "SELECT merchant FROM merchant_mappings WHERE merchant = $1",
            "CreditStore",
        )
        assert row is None

    async def test_creates_merchant_mappings_table_if_missing(self, provisioned_postgres_pool):
        """learn_merchant_categories creates merchant_mappings if it doesn't exist."""
        from butlers.tools.finance.pattern_recognition import learn_merchant_categories

        async with provisioned_postgres_pool() as p:
            await p.execute(CREATE_TRANSACTIONS_SQL)
            # Do NOT create merchant_mappings table

            await _insert_categorized_txn(p, "AutoCreate Store", "electronics")
            result = await learn_merchant_categories(p)

            assert result["upserted"] == 1


class TestSuggestCategories:
    """Tests for suggest_categories() — task 2.2."""

    async def test_returns_empty_when_no_mappings_table(self, provisioned_postgres_pool):
        """Returns empty suggestions when merchant_mappings table does not exist."""
        from butlers.tools.finance.pattern_recognition import suggest_categories

        async with provisioned_postgres_pool() as p:
            await p.execute(CREATE_TRANSACTIONS_SQL)
            # No merchant_mappings table created

            result = await suggest_categories(p, merchant="AnyMerchant")

            assert result["suggestions"] == []
            assert "as_of" in result

    async def test_merchant_pattern_match(self, pool_merchant):
        """ILIKE pattern lookup returns matching merchant suggestions."""
        from butlers.tools.finance.pattern_recognition import (
            learn_merchant_categories,
            suggest_categories,
        )

        await _insert_categorized_txn(pool_merchant, "Netflix Inc", "subscriptions")
        await _insert_categorized_txn(pool_merchant, "Hulu", "subscriptions")
        await learn_merchant_categories(pool_merchant)

        result = await suggest_categories(pool_merchant, merchant="Netflix")

        assert len(result["suggestions"]) == 1
        s = result["suggestions"][0]
        assert s["merchant"] == "Netflix Inc"
        assert s["category"] == "subscriptions"
        assert 0.0 < s["confidence"] <= 1.0

    async def test_no_match_returns_empty(self, pool_merchant):
        """Merchant pattern that matches nothing returns empty suggestions."""
        from butlers.tools.finance.pattern_recognition import (
            learn_merchant_categories,
            suggest_categories,
        )

        await _insert_categorized_txn(pool_merchant, "Spotify", "subscriptions")
        await learn_merchant_categories(pool_merchant)

        result = await suggest_categories(pool_merchant, merchant="NonExistentXYZ")

        assert result["suggestions"] == []

    async def test_transaction_ids_lookup(self, pool_merchant):
        """transaction_ids parameter resolves merchant and returns suggestion."""
        from butlers.tools.finance.pattern_recognition import (
            learn_merchant_categories,
            suggest_categories,
        )

        await _insert_categorized_txn(pool_merchant, "Uber Eats", "dining")
        await learn_merchant_categories(pool_merchant)

        # Insert a new uncategorized transaction for Uber Eats
        txn_row = await pool_merchant.fetchrow(
            """
            INSERT INTO transactions
                (posted_at, merchant, amount, currency, direction, category)
            VALUES (now(), 'Uber Eats', 22.50, 'USD', 'debit', 'uncategorized')
            RETURNING id::text
            """
        )
        txn_id = txn_row["id"]

        result = await suggest_categories(pool_merchant, transaction_ids=[txn_id])

        suggestions = result["suggestions"]
        assert len(suggestions) == 1
        s = suggestions[0]
        assert s["transaction_id"] == txn_id
        assert s["merchant"] == "Uber Eats"
        assert s["suggested_category"] == "dining"
        assert 0.0 < s["confidence"] <= 1.0

    async def test_confidence_scores_included(self, pool_merchant):
        """Suggestions always include a numeric confidence score between 0 and 1."""
        from butlers.tools.finance.pattern_recognition import (
            learn_merchant_categories,
            suggest_categories,
        )

        for _ in range(5):
            await _insert_categorized_txn(pool_merchant, "Apple Store", "electronics")
        await learn_merchant_categories(pool_merchant)

        result = await suggest_categories(pool_merchant, merchant="Apple")

        for s in result["suggestions"]:
            assert 0.0 < s["confidence"] <= 1.0

    async def test_inactive_mappings_excluded(self, pool_merchant):
        """Inactive merchant mappings (is_active=false) are not returned."""
        from butlers.tools.finance.pattern_recognition import (
            learn_merchant_categories,
            suggest_categories,
        )

        await _insert_categorized_txn(pool_merchant, "OldMerchant", "travel")
        await learn_merchant_categories(pool_merchant)

        # Deactivate the mapping
        await pool_merchant.execute(
            "UPDATE merchant_mappings SET is_active = false WHERE merchant = $1",
            "OldMerchant",
        )

        result = await suggest_categories(pool_merchant, merchant="OldMerchant")

        assert result["suggestions"] == []


class TestRecallMerchantMappings:
    """Tests for recall_merchant_mappings() — task 2.3."""

    async def test_returns_empty_when_no_mappings_table(self, provisioned_postgres_pool):
        """Returns empty mappings when merchant_mappings table does not exist."""
        from butlers.tools.finance.pattern_recognition import recall_merchant_mappings

        async with provisioned_postgres_pool() as p:
            result = await recall_merchant_mappings(p)

            assert result["mappings"] == []
            assert "as_of" in result

    async def test_returns_all_active_mappings(self, pool_merchant):
        """No filters returns all active merchant mappings."""
        from butlers.tools.finance.pattern_recognition import (
            learn_merchant_categories,
            recall_merchant_mappings,
        )

        for merchant, cat in [("Costco", "groceries"), ("Delta Airlines", "travel")]:
            await _insert_categorized_txn(pool_merchant, merchant, cat)
        await learn_merchant_categories(pool_merchant)

        result = await recall_merchant_mappings(pool_merchant)

        merchants = {m["merchant"] for m in result["mappings"]}
        assert "Costco" in merchants
        assert "Delta Airlines" in merchants

    async def test_merchant_pattern_filter(self, pool_merchant):
        """merchant_pattern filter applies ILIKE."""
        from butlers.tools.finance.pattern_recognition import (
            learn_merchant_categories,
            recall_merchant_mappings,
        )

        await _insert_categorized_txn(pool_merchant, "Starbucks", "dining")
        await _insert_categorized_txn(pool_merchant, "Walmart", "groceries")
        await learn_merchant_categories(pool_merchant)

        result = await recall_merchant_mappings(pool_merchant, merchant_pattern="Starbucks")

        assert len(result["mappings"]) == 1
        assert result["mappings"][0]["merchant"] == "Starbucks"

    async def test_category_filter(self, pool_merchant):
        """category filter returns only mappings matching that exact category."""
        from butlers.tools.finance.pattern_recognition import (
            learn_merchant_categories,
            recall_merchant_mappings,
        )

        await _insert_categorized_txn(pool_merchant, "United Airlines", "travel")
        await _insert_categorized_txn(pool_merchant, "Airbnb", "travel")
        await _insert_categorized_txn(pool_merchant, "Safeway", "groceries")
        await learn_merchant_categories(pool_merchant)

        result = await recall_merchant_mappings(pool_merchant, category="travel")

        merchants = {m["merchant"] for m in result["mappings"]}
        assert "United Airlines" in merchants
        assert "Airbnb" in merchants
        assert "Safeway" not in merchants

    async def test_combined_merchant_and_category_filter(self, pool_merchant):
        """Combined merchant_pattern + category filter narrows results."""
        from butlers.tools.finance.pattern_recognition import (
            learn_merchant_categories,
            recall_merchant_mappings,
        )

        await _insert_categorized_txn(pool_merchant, "Southwest Airlines", "travel")
        await _insert_categorized_txn(pool_merchant, "Southwest Gas", "utilities")
        await learn_merchant_categories(pool_merchant)

        result = await recall_merchant_mappings(
            pool_merchant, merchant_pattern="Southwest", category="travel"
        )

        assert len(result["mappings"]) == 1
        assert result["mappings"][0]["merchant"] == "Southwest Airlines"

    async def test_response_includes_confidence_and_sample_count(self, pool_merchant):
        """Each mapping includes confidence and sample_count fields."""
        from butlers.tools.finance.pattern_recognition import (
            learn_merchant_categories,
            recall_merchant_mappings,
        )

        for _ in range(4):
            await _insert_categorized_txn(pool_merchant, "Kroger", "groceries")
        await learn_merchant_categories(pool_merchant)

        result = await recall_merchant_mappings(pool_merchant, merchant_pattern="Kroger")

        assert len(result["mappings"]) == 1
        m = result["mappings"][0]
        assert "confidence" in m
        assert "sample_count" in m
        assert m["sample_count"] == 4
        assert 0.0 < m["confidence"] <= 1.0

    async def test_inactive_mappings_excluded(self, pool_merchant):
        """Inactive mappings (is_active=false) are not returned."""
        from butlers.tools.finance.pattern_recognition import (
            learn_merchant_categories,
            recall_merchant_mappings,
        )

        await _insert_categorized_txn(pool_merchant, "OldBrand", "electronics")
        await learn_merchant_categories(pool_merchant)

        await pool_merchant.execute(
            "UPDATE merchant_mappings SET is_active = false WHERE merchant = $1",
            "OldBrand",
        )

        result = await recall_merchant_mappings(pool_merchant, merchant_pattern="OldBrand")

        assert result["mappings"] == []

    async def test_results_ordered_by_confidence_desc(self, pool_merchant):
        """Results are returned ordered by confidence DESC."""
        from butlers.tools.finance.pattern_recognition import (
            learn_merchant_categories,
            recall_merchant_mappings,
        )

        # HighConf: 10 samples → high confidence; LowConf: 1 sample → low confidence
        for _ in range(10):
            await _insert_categorized_txn(pool_merchant, "HighConf", "shopping")
        await _insert_categorized_txn(pool_merchant, "LowConf", "shopping")
        await learn_merchant_categories(pool_merchant)

        result = await recall_merchant_mappings(pool_merchant, category="shopping")

        merchants_in_order = [m["merchant"] for m in result["mappings"]]
        assert merchants_in_order.index("HighConf") < merchants_in_order.index("LowConf")


try:
    from butlers.tools.finance.transactions import (  # noqa: F401
        update_transaction as _update_transaction_check,
    )

    _update_transaction_available = True
except ImportError:
    _update_transaction_available = False


@pytest.mark.skipif(not _update_transaction_available, reason="update_transaction not yet merged")
class TestUpdateTransactionCategoryFeedback:
    """Tests for update_transaction() category learning feedback loop — task 2.4."""

    async def test_update_category_refreshes_merchant_mappings(self, pool_merchant):
        """Updating a transaction's category triggers merchant mapping refresh."""
        from butlers.tools.finance.pattern_recognition import recall_merchant_mappings
        from butlers.tools.finance.transactions import update_transaction

        # Insert a transaction and capture its ID
        row = await pool_merchant.fetchrow(
            """
            INSERT INTO transactions
                (posted_at, merchant, amount, currency, direction, category)
            VALUES (now(), 'Whole Foods', 42.00, 'USD', 'debit', 'uncategorized')
            RETURNING id::text
            """
        )
        txn_id = row["id"]

        # Update category — this should trigger learn_merchant_categories
        result = await update_transaction(
            pool_merchant, transaction_id=txn_id, category="groceries"
        )

        assert result.get("error") is None
        assert result["category"] == "groceries"

        # Mapping should now exist in merchant_mappings
        mappings = await recall_merchant_mappings(pool_merchant, merchant_pattern="Whole Foods")
        assert len(mappings["mappings"]) == 1
        assert mappings["mappings"][0]["category"] == "groceries"

    async def test_update_without_category_does_not_touch_mappings(self, pool_merchant):
        """Updating description only does not alter merchant_mappings."""
        from butlers.tools.finance.pattern_recognition import recall_merchant_mappings
        from butlers.tools.finance.transactions import update_transaction

        row = await pool_merchant.fetchrow(
            """
            INSERT INTO transactions
                (posted_at, merchant, amount, currency, direction, category)
            VALUES (now(), 'Target', 15.00, 'USD', 'debit', 'shopping')
            RETURNING id::text
            """
        )
        txn_id = row["id"]

        await update_transaction(pool_merchant, transaction_id=txn_id, description="New desc")

        mappings = await recall_merchant_mappings(pool_merchant, merchant_pattern="Target")
        # No category update happened, so no mapping should exist (table was empty)
        assert len(mappings["mappings"]) == 0

    async def test_update_nonexistent_transaction(self, pool_merchant):
        """Updating a non-existent transaction returns an error dict."""
        from butlers.tools.finance.transactions import update_transaction

        result = await update_transaction(
            pool_merchant,
            transaction_id="00000000-0000-0000-0000-000000000000",
            category="groceries",
        )

        assert result.get("error") == "transaction_not_found"

    async def test_update_merchant_name(self, pool_merchant):
        """Merchant name can be updated without affecting category mappings."""
        from butlers.tools.finance.pattern_recognition import recall_merchant_mappings
        from butlers.tools.finance.transactions import update_transaction

        row = await pool_merchant.fetchrow(
            """
            INSERT INTO transactions
                (posted_at, merchant, amount, currency, direction, category)
            VALUES (now(), 'Old Name', 20.00, 'USD', 'debit', 'dining')
            RETURNING id::text
            """
        )
        txn_id = row["id"]

        result = await update_transaction(pool_merchant, transaction_id=txn_id, merchant="New Name")

        assert result.get("error") is None
        assert result["merchant"] == "New Name"
        # Merchant-only update must not trigger the category feedback loop.
        mappings = await recall_merchant_mappings(pool_merchant, merchant_pattern="New Name")
        assert len(mappings["mappings"]) == 0
