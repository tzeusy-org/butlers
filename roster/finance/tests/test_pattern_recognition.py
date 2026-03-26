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
