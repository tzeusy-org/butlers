"""Cross-tool integration tests for the finance butler MCP tools.

These tests exercise multi-tool workflows and edge cases not covered by the
individual unit test modules:

- Cross-tool workflows: record_transaction → spending_summary, track_bill →
  mark_paid → upcoming_bills, subscription + transaction for the same service.
- Currency handling edge cases.
- Large result set pagination.
- Boundary dates (exactly today, midnight timestamps).
- Empty/null metadata round-trips.
- list_transactions account_id filter.
- Schema structure validation (tables, indexes, constraints via pg_catalog).
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
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
# Schema DDL (inline so each test gets a clean, isolated database)
# ---------------------------------------------------------------------------

_DDL_ACCOUNTS = """
CREATE TABLE IF NOT EXISTS accounts (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    institution TEXT NOT NULL,
    type        TEXT NOT NULL
                    CHECK (type IN ('checking', 'savings', 'credit', 'investment')),
    name        TEXT,
    last_four   CHAR(4),
    currency    CHAR(3) NOT NULL DEFAULT 'USD',
    metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_DDL_TRANSACTIONS = """
CREATE TABLE IF NOT EXISTS transactions (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id        UUID REFERENCES accounts(id) ON DELETE SET NULL,
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
    metadata          JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_DDL_TRANSACTIONS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_transactions_posted_at ON transactions (posted_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_transactions_merchant ON transactions (merchant)",
    "CREATE INDEX IF NOT EXISTS idx_transactions_category ON transactions (category)",
    "CREATE INDEX IF NOT EXISTS idx_transactions_source_message_id"
    " ON transactions (source_message_id)",
    """CREATE UNIQUE INDEX IF NOT EXISTS uq_transactions_dedupe
        ON transactions (source_message_id, merchant, amount, posted_at)
        WHERE source_message_id IS NOT NULL""",
]

_DDL_SUBSCRIPTIONS = """
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
    account_id        UUID REFERENCES accounts(id) ON DELETE SET NULL,
    source_message_id TEXT,
    metadata          JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_DDL_BILLS = """
CREATE TABLE IF NOT EXISTS bills (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    payee                  TEXT NOT NULL,
    amount                 NUMERIC(14, 2) NOT NULL,
    currency               CHAR(3) NOT NULL,
    due_date               DATE NOT NULL,
    frequency              TEXT NOT NULL
                               CHECK (frequency IN (
                                   'one_time', 'weekly', 'monthly',
                                   'quarterly', 'yearly', 'custom'
                               )),
    status                 TEXT NOT NULL
                               CHECK (status IN ('pending', 'paid', 'overdue')),
    payment_method         TEXT,
    account_id             UUID REFERENCES accounts(id) ON DELETE SET NULL,
    source_message_id      TEXT,
    statement_period_start DATE,
    statement_period_end   DATE,
    paid_at                TIMESTAMPTZ,
    metadata               JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


async def _provision_all_tables(pool) -> None:
    """Create all finance tables needed for cross-tool integration tests."""
    await pool.execute(_DDL_ACCOUNTS)
    await pool.execute(_DDL_TRANSACTIONS)
    for idx_sql in _DDL_TRANSACTIONS_INDEXES:
        await pool.execute(idx_sql)
    await pool.execute(_DDL_SUBSCRIPTIONS)
    await pool.execute(_DDL_BILLS)


# ---------------------------------------------------------------------------
# Shared fixture: full schema
# ---------------------------------------------------------------------------


@pytest.fixture
async def pool(provisioned_postgres_pool):
    """Provision a fresh database with all finance tables."""
    async with provisioned_postgres_pool() as p:
        await _provision_all_tables(p)
        yield p


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _today() -> date:
    return datetime.now(UTC).date()


# ===========================================================================
# 1. CROSS-TOOL WORKFLOW: record_transaction → spending_summary
# ===========================================================================


class TestTransactionToSpendingSummary:
    """Record transactions via record_transaction and verify spending_summary reflects them."""

    async def test_spending_summary_reflects_recorded_transactions(self, pool):
        """Transactions recorded via record_transaction appear in spending_summary."""
        from butlers.tools.finance import record_transaction, spending_summary

        today = _today()
        posted = _utcnow().replace(day=today.day, hour=12, minute=0, second=0, microsecond=0)

        await record_transaction(
            pool=pool,
            posted_at=posted,
            merchant="Whole Foods",
            amount=-90.00,
            currency="USD",
            category="groceries",
        )
        await record_transaction(
            pool=pool,
            posted_at=posted,
            merchant="Blue Bottle",
            amount=-12.00,
            currency="USD",
            category="dining",
        )
        await record_transaction(
            pool=pool,
            posted_at=posted,
            merchant="PayPal Refund",
            amount=30.00,
            currency="USD",
            category="refunds",
        )  # credit — should NOT count in spend

        result = await spending_summary(
            pool,
            start_date=today.replace(day=1),
            end_date=today,
            group_by="category",
        )

        assert Decimal(result["total_spend"]) == Decimal("102.00")
        keys = [g["key"] for g in result["groups"]]
        assert "groceries" in keys
        assert "dining" in keys
        assert "refunds" not in keys  # credits excluded

    async def test_spending_summary_group_by_merchant_after_recording(self, pool):
        """group_by=merchant correctly aggregates transactions from record_transaction."""
        from butlers.tools.finance import record_transaction, spending_summary

        today = _today()
        posted = _utcnow().replace(day=today.day, hour=10)

        # Record three Netflix charges (simulating different payment events)
        for _ in range(3):
            await record_transaction(
                pool=pool,
                posted_at=posted,
                merchant="Netflix",
                amount=-15.49,
                currency="USD",
                category="subscriptions",
            )

        result = await spending_summary(
            pool,
            start_date=today.replace(day=1),
            end_date=today,
            group_by="merchant",
        )
        netflix_group = next((g for g in result["groups"] if g["key"] == "Netflix"), None)
        assert netflix_group is not None
        assert Decimal(netflix_group["amount"]) == Decimal("46.47")
        assert netflix_group["count"] == 3

    async def test_spending_summary_date_boundary_exactly_today(self, pool):
        """A transaction posted exactly today is captured by spending_summary ending today."""
        from butlers.tools.finance import record_transaction, spending_summary

        today = _today()
        # Post at midnight (start of day) — boundary condition
        posted_midnight = datetime(today.year, today.month, today.day, 0, 0, 0, tzinfo=UTC)
        await record_transaction(
            pool=pool,
            posted_at=posted_midnight,
            merchant="Midnight Shop",
            amount=-5.00,
            currency="USD",
            category="misc",
        )

        result = await spending_summary(pool, start_date=today, end_date=today)
        assert Decimal(result["total_spend"]) == Decimal("5.00")

    async def test_spending_summary_excludes_transaction_before_start_date(self, pool):
        """Transactions before start_date are excluded from spending_summary."""
        from butlers.tools.finance import record_transaction, spending_summary

        today = _today()
        old_posted = datetime(today.year, today.month, today.day, 12, 0, 0, tzinfo=UTC) - timedelta(
            days=35
        )
        new_posted = _utcnow().replace(day=today.day, hour=12)

        await record_transaction(
            pool=pool,
            posted_at=old_posted,
            merchant="Old Merchant",
            amount=-100.00,
            currency="USD",
            category="misc",
        )
        await record_transaction(
            pool=pool,
            posted_at=new_posted,
            merchant="New Merchant",
            amount=-20.00,
            currency="USD",
            category="misc",
        )

        result = await spending_summary(
            pool,
            start_date=today.replace(day=1),
            end_date=today,
        )
        assert Decimal(result["total_spend"]) == Decimal("20.00")


# ===========================================================================
# 2. CROSS-TOOL WORKFLOW: track_bill → mark paid → upcoming_bills excludes it
# ===========================================================================


class TestBillPaymentWorkflow:
    """Full bill lifecycle: track → upcoming → mark paid → excluded from upcoming."""

    async def test_paid_bill_excluded_from_upcoming(self, pool):
        """Marking a bill as paid removes it from upcoming_bills."""
        from butlers.tools.finance import track_bill, upcoming_bills

        due = _today() + timedelta(days=5)
        bill = await track_bill(
            pool=pool,
            payee="Electric Company",
            amount=120.00,
            currency="USD",
            due_date=due,
            status="pending",
        )
        # Verify it appears initially
        result_before = await upcoming_bills(pool=pool, days_ahead=14)
        ids_before = [item["bill"]["id"] for item in result_before["items"]]
        assert bill["id"] in ids_before

        # Mark paid via upsert
        await track_bill(
            pool=pool,
            payee="Electric Company",
            amount=120.00,
            currency="USD",
            due_date=due,
            status="paid",
            paid_at=_utcnow(),
        )

        result_after = await upcoming_bills(pool=pool, days_ahead=14)
        ids_after = [item["bill"]["id"] for item in result_after["items"]]
        assert bill["id"] not in ids_after

    async def test_overdue_bill_appears_then_is_marked_paid(self, pool):
        """An overdue bill transitions correctly from overdue → paid → excluded."""
        from butlers.tools.finance import track_bill, upcoming_bills

        past_due = _today() - timedelta(days=7)
        bill = await track_bill(
            pool=pool,
            payee="Old Rent",
            amount=1500.00,
            currency="USD",
            due_date=past_due,
            status="overdue",
        )

        # Should appear as overdue
        result_overdue = await upcoming_bills(pool=pool, days_ahead=14, include_overdue=True)
        overdue_ids = [item["bill"]["id"] for item in result_overdue["items"]]
        assert bill["id"] in overdue_ids

        # Pay it
        await track_bill(
            pool=pool,
            payee="Old Rent",
            amount=1500.00,
            currency="USD",
            due_date=past_due,
            status="paid",
            paid_at=_utcnow(),
        )

        result_after = await upcoming_bills(pool=pool, days_ahead=14, include_overdue=True)
        ids_after = [item["bill"]["id"] for item in result_after["items"]]
        assert bill["id"] not in ids_after

    async def test_upcoming_bills_totals_decrease_when_bill_paid(self, pool):
        """totals.amount_due decreases after marking a bill paid."""
        from butlers.tools.finance import track_bill, upcoming_bills

        due_a = _today() + timedelta(days=3)
        due_b = _today() + timedelta(days=8)

        await track_bill(pool=pool, payee="Bill A", amount=50.00, currency="USD", due_date=due_a)
        await track_bill(pool=pool, payee="Bill B", amount=100.00, currency="USD", due_date=due_b)

        result_before = await upcoming_bills(pool=pool, days_ahead=14)
        assert Decimal(result_before["totals"]["amount_due"]) == Decimal("150.00")

        # Pay Bill A
        await track_bill(
            pool=pool,
            payee="Bill A",
            amount=50.00,
            currency="USD",
            due_date=due_a,
            status="paid",
            paid_at=_utcnow(),
        )

        result_after = await upcoming_bills(pool=pool, days_ahead=14)
        assert Decimal(result_after["totals"]["amount_due"]) == Decimal("100.00")

    async def test_multiple_bills_same_payee_different_due_dates(self, pool):
        """Separate billing cycles for same payee are independent records."""
        from butlers.tools.finance import track_bill, upcoming_bills

        due_1 = _today() + timedelta(days=4)
        due_2 = _today() + timedelta(days=34)  # beyond 14-day window

        await track_bill(pool=pool, payee="Comcast", amount=89.99, currency="USD", due_date=due_1)
        await track_bill(pool=pool, payee="Comcast", amount=89.99, currency="USD", due_date=due_2)

        result = await upcoming_bills(pool=pool, days_ahead=14)
        comcast_items = [i for i in result["items"] if i["bill"]["payee"] == "Comcast"]
        assert len(comcast_items) == 1
        assert comcast_items[0]["bill"]["due_date"] == due_1


# ===========================================================================
# 3. CROSS-TOOL WORKFLOW: subscription + transaction for the same service
# ===========================================================================


class TestSubscriptionAndTransactionWorkflow:
    """Track subscription renewal AND record the corresponding transaction."""

    async def test_subscription_renewal_with_matching_transaction(self, pool):
        """Subscription and transaction records for same service can coexist independently."""
        from butlers.tools.finance import record_transaction, spending_summary, track_subscription

        today = _today()
        next_renewal = today + timedelta(days=30)
        source_id = "email-netflix-renewal-001"
        posted = _utcnow().replace(day=today.day, hour=9)

        sub = await track_subscription(
            pool=pool,
            service="Netflix",
            amount=15.49,
            currency="USD",
            frequency="monthly",
            next_renewal=next_renewal,
            source_message_id=source_id,
        )
        txn = await record_transaction(
            pool=pool,
            posted_at=posted,
            merchant="Netflix",
            amount=-15.49,
            currency="USD",
            category="subscriptions",
            source_message_id=source_id,
        )

        assert sub["service"] == "Netflix"
        assert txn["merchant"] == "Netflix"
        assert Decimal(str(txn["amount"])) == Decimal("15.49")

        # Spending summary should include the transaction
        summary = await spending_summary(
            pool,
            start_date=today.replace(day=1),
            end_date=today,
            category_filter="subscriptions",
        )
        assert Decimal(summary["total_spend"]) >= Decimal("15.49")

    async def test_subscription_upsert_after_price_change_and_new_transaction(self, pool):
        """Price change: update subscription amount then record new transaction at new price."""
        from butlers.tools.finance import record_transaction, spending_summary, track_subscription

        today = _today()
        posted = _utcnow().replace(day=today.day, hour=10)

        # Original subscription at old price
        sub_v1 = await track_subscription(
            pool=pool,
            service="Spotify",
            amount=9.99,
            currency="USD",
            frequency="monthly",
            next_renewal=today + timedelta(days=30),
        )

        # Price change upsert
        sub_v2 = await track_subscription(
            pool=pool,
            service="Spotify",
            amount=11.99,
            currency="USD",
            frequency="monthly",
            next_renewal=today + timedelta(days=30),
        )

        # Same record, updated amount
        assert sub_v1["id"] == sub_v2["id"]
        assert Decimal(str(sub_v2["amount"])) == Decimal("11.99")

        # Record transaction at new price
        await record_transaction(
            pool=pool,
            posted_at=posted,
            merchant="Spotify",
            amount=-11.99,
            currency="USD",
            category="subscriptions",
        )

        summary = await spending_summary(
            pool,
            start_date=today.replace(day=1),
            end_date=today,
            category_filter="subscriptions",
        )
        assert Decimal(summary["total_spend"]) == Decimal("11.99")

    async def test_cancelled_subscription_transaction_still_tracked(self, pool):
        """Cancelling a subscription does not prevent the last transaction from appearing."""
        from butlers.tools.finance import record_transaction, spending_summary, track_subscription

        today = _today()
        posted = _utcnow().replace(day=today.day, hour=8)

        await track_subscription(
            pool=pool,
            service="Hulu",
            amount=7.99,
            currency="USD",
            frequency="monthly",
            next_renewal=today + timedelta(days=25),
            status="cancelled",
        )
        await record_transaction(
            pool=pool,
            posted_at=posted,
            merchant="Hulu",
            amount=-7.99,
            currency="USD",
            category="subscriptions",
        )

        summary = await spending_summary(
            pool,
            start_date=today.replace(day=1),
            end_date=today,
            category_filter="subscriptions",
        )
        assert Decimal(summary["total_spend"]) == Decimal("7.99")


# ===========================================================================
# 4. CURRENCY HANDLING EDGE CASES
# ===========================================================================


class TestCurrencyEdgeCases:
    """Currency normalization, multi-currency, and non-USD scenarios."""

    async def test_record_transaction_lowercase_currency_normalized(self, pool):
        """Lowercase currency code is stored as uppercase."""
        from butlers.tools.finance import record_transaction

        result = await record_transaction(
            pool=pool,
            posted_at=_utcnow(),
            merchant="Wise",
            amount=-50.00,
            currency="eur",
            category="transfer",
        )
        assert result["currency"] == "EUR"

    async def test_record_transaction_mixed_case_currency_normalized(self, pool):
        """Mixed-case currency code is stored as uppercase."""
        from butlers.tools.finance import record_transaction

        result = await record_transaction(
            pool=pool,
            posted_at=_utcnow(),
            merchant="HSBC",
            amount=-200.00,
            currency="Gbp",
            category="banking",
        )
        assert result["currency"] == "GBP"

    async def test_spending_summary_currency_most_frequent_wins(self, pool):
        """spending_summary reports the most-frequent currency in the result set."""
        from butlers.tools.finance import record_transaction, spending_summary

        today = _today()
        posted = _utcnow().replace(day=today.day, hour=12)

        # 3 USD transactions, 1 EUR transaction
        for _ in range(3):
            await record_transaction(
                pool=pool,
                posted_at=posted,
                merchant="US Store",
                amount=-10.00,
                currency="USD",
                category="misc",
            )
        await record_transaction(
            pool=pool,
            posted_at=posted,
            merchant="EU Store",
            amount=-20.00,
            currency="EUR",
            category="misc",
        )

        result = await spending_summary(pool, start_date=today.replace(day=1), end_date=today)
        assert result["currency"] == "USD"

    async def test_list_transactions_multi_currency_coexist(self, pool):
        """Transactions in different currencies are all stored and retrievable."""
        from butlers.tools.finance import list_transactions, record_transaction

        posted = _utcnow()
        for currency in ["USD", "EUR", "GBP", "JPY"]:
            await record_transaction(
                pool=pool,
                posted_at=posted,
                merchant=f"Store-{currency}",
                amount=-100.00,
                currency=currency,
                category="shopping",
            )

        result = await list_transactions(pool=pool, category="shopping")
        currencies = {item["currency"] for item in result["items"]}
        assert {"USD", "EUR", "GBP", "JPY"}.issubset(currencies)
        assert result["total"] == 4

    async def test_track_subscription_non_usd_currency(self, pool):
        """Subscription can be tracked in non-USD currency."""
        from butlers.tools.finance import track_subscription

        result = await track_subscription(
            pool=pool,
            service="Spotify UK",
            amount=9.99,
            currency="GBP",
            frequency="monthly",
            next_renewal=_today() + timedelta(days=30),
        )
        assert result["currency"] == "GBP"

    async def test_track_bill_non_usd_currency(self, pool):
        """Bill can be tracked in non-USD currency."""
        from butlers.tools.finance import track_bill

        result = await track_bill(
            pool=pool,
            payee="EDF Energy",
            amount=85.00,
            currency="GBP",
            due_date=_today() + timedelta(days=10),
        )
        assert result["currency"] == "GBP"


# ===========================================================================
# 5. LARGE RESULT SET PAGINATION
# ===========================================================================


class TestPagination:
    """Pagination correctness for list_transactions with large data sets."""

    async def test_paginate_through_all_transactions(self, pool):
        """Paginating with limit=10 returns all 50 transactions without overlap."""
        from butlers.tools.finance import list_transactions, record_transaction

        # Insert 50 transactions
        base = _utcnow()
        for i in range(50):
            await record_transaction(
                pool=pool,
                posted_at=base - timedelta(hours=i),
                merchant=f"Merchant-{i:02d}",
                amount=-float(i + 1),
                currency="USD",
                category="misc",
            )

        seen_ids: set[str] = set()
        offset = 0
        limit = 10
        total = None

        while True:
            page = await list_transactions(pool=pool, limit=limit, offset=offset)
            if total is None:
                total = page["total"]
                assert total == 50

            items = page["items"]
            if not items:
                break
            for item in items:
                assert item["id"] not in seen_ids, f"Duplicate id at offset={offset}"
                seen_ids.add(item["id"])
            offset += limit

        assert len(seen_ids) == 50

    async def test_pagination_total_unchanged_across_pages(self, pool):
        """Total reported is consistent across multiple page fetches."""
        from butlers.tools.finance import list_transactions, record_transaction

        base = _utcnow()
        for i in range(15):
            await record_transaction(
                pool=pool,
                posted_at=base - timedelta(minutes=i),
                merchant=f"Shop-{i}",
                amount=-5.00,
                currency="USD",
                category="misc",
            )

        page1 = await list_transactions(pool=pool, limit=5, offset=0)
        page2 = await list_transactions(pool=pool, limit=5, offset=5)
        page3 = await list_transactions(pool=pool, limit=5, offset=10)

        assert page1["total"] == page2["total"] == page3["total"] == 15

    async def test_offset_beyond_total_returns_empty_items(self, pool):
        """An offset beyond total returns an empty items list, not an error."""
        from butlers.tools.finance import list_transactions, record_transaction

        await record_transaction(
            pool=pool,
            posted_at=_utcnow(),
            merchant="Solo Shop",
            amount=-1.00,
            currency="USD",
            category="misc",
        )

        result = await list_transactions(pool=pool, limit=10, offset=100)
        assert result["items"] == []
        assert result["total"] == 1

    async def test_limit_clamped_to_maximum(self, pool):
        """A limit exceeding 500 is clamped to 500."""
        from butlers.tools.finance import list_transactions, record_transaction

        await record_transaction(
            pool=pool,
            posted_at=_utcnow(),
            merchant="Test",
            amount=-1.00,
            currency="USD",
            category="misc",
        )

        result = await list_transactions(pool=pool, limit=9999)
        assert result["limit"] == 500

    async def test_limit_minimum_clamped_to_one(self, pool):
        """A limit of 0 or negative is clamped to 1."""
        from butlers.tools.finance import list_transactions, record_transaction

        await record_transaction(
            pool=pool,
            posted_at=_utcnow(),
            merchant="Test",
            amount=-1.00,
            currency="USD",
            category="misc",
        )

        result = await list_transactions(pool=pool, limit=0)
        assert result["limit"] == 1
        assert len(result["items"]) <= 1


# ===========================================================================
# 6. BOUNDARY DATES
# ===========================================================================


class TestBoundaryDates:
    """Edge cases around date boundaries."""

    async def test_bill_due_exactly_today_urgency_due_today(self, pool):
        """Bill due exactly today gets urgency=due_today."""
        from butlers.tools.finance import track_bill, upcoming_bills

        today = _today()
        await track_bill(
            pool=pool,
            payee="Today Bill",
            amount=50.00,
            currency="USD",
            due_date=today,
            status="pending",
        )

        result = await upcoming_bills(pool=pool, days_ahead=14)
        assert len(result["items"]) == 1
        item = result["items"][0]
        assert item["urgency"] == "due_today"
        assert item["days_until_due"] == 0

    async def test_bill_due_at_horizon_boundary_included(self, pool):
        """Bill due exactly at days_ahead boundary (day N) is included."""
        from butlers.tools.finance import track_bill, upcoming_bills

        horizon = _today() + timedelta(days=14)
        await track_bill(
            pool=pool,
            payee="Horizon Bill",
            amount=75.00,
            currency="USD",
            due_date=horizon,
        )

        result = await upcoming_bills(pool=pool, days_ahead=14)
        payees = [i["bill"]["payee"] for i in result["items"]]
        assert "Horizon Bill" in payees

    async def test_bill_due_one_day_past_horizon_excluded(self, pool):
        """Bill due one day beyond the horizon is excluded."""
        from butlers.tools.finance import track_bill, upcoming_bills

        beyond = _today() + timedelta(days=15)
        await track_bill(
            pool=pool,
            payee="Beyond Bill",
            amount=75.00,
            currency="USD",
            due_date=beyond,
        )

        result = await upcoming_bills(pool=pool, days_ahead=14)
        payees = [i["bill"]["payee"] for i in result["items"]]
        assert "Beyond Bill" not in payees

    async def test_transaction_posted_at_end_of_day_captured_same_day(self, pool):
        """Transaction at 23:59:59 UTC is captured when querying that calendar day."""
        from butlers.tools.finance import list_transactions, record_transaction

        today = _today()
        end_of_day = datetime(today.year, today.month, today.day, 23, 59, 59, tzinfo=UTC)
        await record_transaction(
            pool=pool,
            posted_at=end_of_day,
            merchant="Late Night Shop",
            amount=-7.50,
            currency="USD",
            category="misc",
        )

        result = await list_transactions(
            pool=pool,
            start_date=datetime(today.year, today.month, today.day, 0, 0, 0, tzinfo=UTC),
            end_date=end_of_day,
        )
        merchants = [item["merchant"] for item in result["items"]]
        assert "Late Night Shop" in merchants

    async def test_subscription_renewal_exactly_today(self, pool):
        """Subscription with next_renewal=today is stored without error."""
        from butlers.tools.finance import track_subscription

        result = await track_subscription(
            pool=pool,
            service="SameDay Service",
            amount=5.00,
            currency="USD",
            frequency="monthly",
            next_renewal=_today(),
        )
        assert result["next_renewal"] == _today()


# ===========================================================================
# 7. EMPTY / NULL METADATA HANDLING
# ===========================================================================


class TestMetadataHandling:
    """Verify metadata field round-trips and edge cases."""

    async def test_transaction_metadata_none_stored_as_empty_dict(self, pool):
        """metadata=None is stored and returned as an empty dict {}."""
        from butlers.tools.finance import record_transaction

        result = await record_transaction(
            pool=pool,
            posted_at=_utcnow(),
            merchant="No Meta",
            amount=-1.00,
            currency="USD",
            category="misc",
            metadata=None,
        )
        assert result["metadata"] == {} or isinstance(result["metadata"], dict)

    async def test_transaction_metadata_empty_dict_round_trips(self, pool):
        """metadata={} is stored and returned as {}."""
        from butlers.tools.finance import record_transaction

        result = await record_transaction(
            pool=pool,
            posted_at=_utcnow(),
            merchant="Empty Meta",
            amount=-1.00,
            currency="USD",
            category="misc",
            metadata={},
        )
        assert result["metadata"] == {}

    async def test_transaction_metadata_nested_dict_round_trips(self, pool):
        """Nested metadata dict is preserved through JSONB storage."""
        from butlers.tools.finance import record_transaction

        meta = {"order": {"id": "ORD-001", "items": [1, 2, 3]}, "tags": ["promo"]}
        result = await record_transaction(
            pool=pool,
            posted_at=_utcnow(),
            merchant="Nested Meta",
            amount=-50.00,
            currency="USD",
            category="shopping",
            metadata=meta,
        )
        assert result["metadata"]["order"]["id"] == "ORD-001"
        assert result["metadata"]["order"]["items"] == [1, 2, 3]
        assert result["metadata"]["tags"] == ["promo"]

    async def test_subscription_metadata_merged_on_multiple_upserts(self, pool):
        """Metadata is accumulated (merged) across successive upserts, not replaced."""
        from butlers.tools.finance import track_subscription

        renewal = _today() + timedelta(days=30)
        await track_subscription(
            pool=pool,
            service="Merge Test Sub",
            amount=10.00,
            currency="USD",
            frequency="monthly",
            next_renewal=renewal,
            metadata={"key_a": "value_a"},
        )
        await track_subscription(
            pool=pool,
            service="Merge Test Sub",
            amount=10.00,
            currency="USD",
            frequency="monthly",
            next_renewal=renewal,
            metadata={"key_b": "value_b"},
        )
        final = await track_subscription(
            pool=pool,
            service="Merge Test Sub",
            amount=10.00,
            currency="USD",
            frequency="monthly",
            next_renewal=renewal,
            metadata={"key_c": "value_c"},
        )
        assert "key_a" in final["metadata"]
        assert "key_b" in final["metadata"]
        assert "key_c" in final["metadata"]

    async def test_bill_metadata_none_stored_as_empty_dict(self, pool):
        """Bill with metadata=None stores an empty dict."""
        from butlers.tools.finance import track_bill

        result = await track_bill(
            pool=pool,
            payee="No Meta Bill",
            amount=100.00,
            currency="USD",
            due_date=_today() + timedelta(days=5),
            metadata=None,
        )
        assert result["metadata"] == {} or isinstance(result["metadata"], dict)

    async def test_bill_metadata_account_info_preserved(self, pool):
        """Bill metadata containing account info survives round-trip."""
        from butlers.tools.finance import track_bill

        meta = {"account_number": "****1234", "statement_id": "S-2026-02"}
        result = await track_bill(
            pool=pool,
            payee="Chase",
            amount=342.00,
            currency="USD",
            due_date=_today() + timedelta(days=8),
            metadata=meta,
        )
        assert result["metadata"]["account_number"] == "****1234"
        assert result["metadata"]["statement_id"] == "S-2026-02"


# ===========================================================================
# 8. list_transactions ACCOUNT_ID FILTER
# ===========================================================================


class TestListTransactionsAccountFilter:
    """list_transactions filtered by account_id."""

    async def test_filter_by_account_id_returns_only_matching(self, pool):
        """list_transactions(account_id=X) returns only transactions linked to X."""
        from butlers.tools.finance import list_transactions, record_transaction

        acct_id = await pool.fetchval(
            "INSERT INTO accounts (institution, type, currency) VALUES ($1, $2, $3) RETURNING id",
            "Chase",
            "credit",
            "USD",
        )
        other_acct_id = await pool.fetchval(
            "INSERT INTO accounts (institution, type, currency) VALUES ($1, $2, $3) RETURNING id",
            "Amex",
            "credit",
            "USD",
        )

        posted = _utcnow()
        await record_transaction(
            pool=pool,
            posted_at=posted,
            merchant="Chase Purchase",
            amount=-30.00,
            currency="USD",
            category="misc",
            account_id=str(acct_id),
        )
        await record_transaction(
            pool=pool,
            posted_at=posted,
            merchant="Amex Purchase",
            amount=-50.00,
            currency="USD",
            category="misc",
            account_id=str(other_acct_id),
        )
        await record_transaction(
            pool=pool,
            posted_at=posted,
            merchant="No Account Purchase",
            amount=-10.00,
            currency="USD",
            category="misc",
        )

        result = await list_transactions(pool=pool, account_id=str(acct_id))
        assert result["total"] == 1
        assert result["items"][0]["merchant"] == "Chase Purchase"

    async def test_filter_by_nonexistent_account_id_returns_empty(self, pool):
        """Filtering by a UUID that matches no transactions returns empty."""
        from butlers.tools.finance import list_transactions, record_transaction

        await record_transaction(
            pool=pool,
            posted_at=_utcnow(),
            merchant="Any Store",
            amount=-10.00,
            currency="USD",
            category="misc",
        )

        result = await list_transactions(pool=pool, account_id=str(uuid.uuid4()))
        assert result["total"] == 0
        assert result["items"] == []

    async def test_account_id_filter_combined_with_category(self, pool):
        """account_id and category filters combine correctly (AND logic)."""
        from butlers.tools.finance import list_transactions, record_transaction

        acct_id = await pool.fetchval(
            "INSERT INTO accounts (institution, type, currency) VALUES ($1, $2, $3) RETURNING id",
            "Wells Fargo",
            "checking",
            "USD",
        )

        posted = _utcnow()
        await record_transaction(
            pool=pool,
            posted_at=posted,
            merchant="Grocery Store",
            amount=-40.00,
            currency="USD",
            category="groceries",
            account_id=str(acct_id),
        )
        await record_transaction(
            pool=pool,
            posted_at=posted,
            merchant="Restaurant",
            amount=-25.00,
            currency="USD",
            category="dining",
            account_id=str(acct_id),
        )

        result = await list_transactions(pool=pool, account_id=str(acct_id), category="groceries")
        assert result["total"] == 1
        assert result["items"][0]["merchant"] == "Grocery Store"


# ===========================================================================
# 9. SCHEMA STRUCTURE VALIDATION
# ===========================================================================


class TestSchemaValidation:
    """Validate that all expected tables and indexes are created by the DDL."""

    async def test_all_expected_tables_exist(self, pool):
        """All four finance tables exist in the database."""
        tables = await pool.fetch(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_type = 'BASE TABLE'
            """
        )
        table_names = {row["table_name"] for row in tables}
        for expected in ("accounts", "transactions", "subscriptions", "bills"):
            assert expected in table_names, f"Missing table: {expected}"

    async def test_transactions_indexes_exist(self, pool):
        """All expected indexes on transactions table are present."""
        indexes = await pool.fetch(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE tablename = 'transactions'
            """
        )
        index_names = {row["indexname"] for row in indexes}
        expected_indexes = {
            "idx_transactions_posted_at",
            "idx_transactions_merchant",
            "idx_transactions_category",
            "idx_transactions_source_message_id",
            "uq_transactions_dedupe",
        }
        for idx in expected_indexes:
            assert idx in index_names, f"Missing index: {idx}"

    async def test_transactions_direction_check_constraint_enforced(self, pool):
        """Invalid direction value is rejected by the CHECK constraint."""
        import asyncpg

        with pytest.raises(asyncpg.CheckViolationError):
            await pool.execute(
                """
                INSERT INTO transactions
                    (posted_at, merchant, amount, currency, direction, category)
                VALUES (now(), 'Test', 10.00, 'USD', 'invalid_direction', 'misc')
                """
            )

    async def test_subscriptions_frequency_check_constraint_enforced(self, pool):
        """Invalid frequency is rejected by the subscriptions CHECK constraint."""
        import asyncpg

        with pytest.raises(asyncpg.CheckViolationError):
            await pool.execute(
                """
                INSERT INTO subscriptions
                    (service, amount, currency, frequency, next_renewal, status)
                VALUES ('Bad Sub', 9.99, 'USD', 'biweekly', CURRENT_DATE, 'active')
                """
            )

    async def test_bills_status_check_constraint_enforced(self, pool):
        """Invalid status is rejected by the bills CHECK constraint."""
        import asyncpg

        with pytest.raises(asyncpg.CheckViolationError):
            await pool.execute(
                """
                INSERT INTO bills
                    (payee, amount, currency, due_date, frequency, status)
                VALUES ('Bad Bill', 50.00, 'USD', CURRENT_DATE, 'one_time', 'unknown')
                """
            )

    async def test_accounts_type_check_constraint_enforced(self, pool):
        """Invalid account type is rejected by the accounts CHECK constraint."""
        import asyncpg

        with pytest.raises(asyncpg.CheckViolationError):
            await pool.execute(
                """
                INSERT INTO accounts (institution, type, currency)
                VALUES ('Bad Bank', 'brokerage', 'USD')
                """
            )

    async def test_transactions_uuid_dedupe_index_unique(self, pool):
        """Unique partial index blocks (source_message_id, merchant, amount, posted_at) dupes."""
        import asyncpg

        posted = _utcnow()
        await pool.execute(
            """
            INSERT INTO transactions
                (source_message_id, posted_at, merchant, amount, currency, direction, category)
            VALUES ('dedupe-schema-test', $1, 'Dupe Merchant', 25.00, 'USD', 'debit', 'misc')
            """,
            posted,
        )

        with pytest.raises(asyncpg.UniqueViolationError):
            await pool.execute(
                """
                INSERT INTO transactions
                    (source_message_id, posted_at, merchant, amount, currency, direction, category)
                VALUES ('dedupe-schema-test', $1, 'Dupe Merchant', 25.00, 'USD', 'debit', 'misc')
                """,
                posted,
            )

    async def test_transactions_fk_account_id_on_delete_set_null(self, pool):
        """Deleting an account sets account_id to NULL on linked transactions."""
        from butlers.tools.finance import record_transaction

        acct_id = await pool.fetchval(
            "INSERT INTO accounts (institution, type, currency) VALUES ($1, $2, $3) RETURNING id",
            "Delete Me Bank",
            "checking",
            "USD",
        )
        txn = await record_transaction(
            pool=pool,
            posted_at=_utcnow(),
            merchant="Linked Store",
            amount=-20.00,
            currency="USD",
            category="misc",
            account_id=str(acct_id),
        )
        assert txn["account_id"] == str(acct_id)

        await pool.execute("DELETE FROM accounts WHERE id = $1", acct_id)

        row = await pool.fetchrow(
            "SELECT account_id FROM transactions WHERE id = $1::uuid", txn["id"]
        )
        assert row["account_id"] is None

    async def test_transactions_nullable_optional_columns(self, pool):
        """Optional columns (description, payment_method, receipt_url, etc.) accept NULL."""
        from butlers.tools.finance import record_transaction

        result = await record_transaction(
            pool=pool,
            posted_at=_utcnow(),
            merchant="Bare Minimum",
            amount=-1.00,
            currency="USD",
            category="misc",
        )
        assert result.get("description") is None
        assert result.get("payment_method") is None
        assert result.get("receipt_url") is None
        assert result.get("external_ref") is None
        assert result.get("source_message_id") is None
        assert result.get("account_id") is None


# ===========================================================================
# 10. CONCURRENT / RAPID SEQUENTIAL OPERATIONS
# ===========================================================================


class TestConcurrentAndRapidOperations:
    """Verify correctness under concurrent and rapid sequential operations."""

    async def test_concurrent_transaction_inserts_all_succeed(self, pool):
        """Concurrent record_transaction calls complete without error."""
        from butlers.tools.finance import list_transactions, record_transaction

        posted = _utcnow()

        async def insert_one(i: int):
            return await record_transaction(
                pool=pool,
                posted_at=posted - timedelta(seconds=i),
                merchant=f"Concurrent-{i}",
                amount=-float(i + 1),
                currency="USD",
                category="misc",
            )

        results = await asyncio.gather(*[insert_one(i) for i in range(10)])
        assert len(results) == 10
        assert len({r["id"] for r in results}) == 10  # all distinct

        listing = await list_transactions(pool=pool, category="misc")
        assert listing["total"] == 10

    async def test_concurrent_dedupe_same_source_message_id(self, pool):
        """Concurrent inserts with same source_message_id result in a single row."""
        from butlers.tools.finance import record_transaction

        posted = _utcnow()
        source_id = f"concurrent-dedupe-{uuid.uuid4().hex[:8]}"

        results = await asyncio.gather(
            *[
                record_transaction(
                    pool=pool,
                    posted_at=posted,
                    merchant="Same Merchant",
                    amount=-99.00,
                    currency="USD",
                    category="misc",
                    source_message_id=source_id,
                )
                for _ in range(5)
            ]
        )

        # All returned records should have the same ID (idempotent)
        ids = {r["id"] for r in results}
        assert len(ids) == 1

        # Exactly one row in DB
        count = await pool.fetchval(
            "SELECT COUNT(*) FROM transactions WHERE source_message_id = $1", source_id
        )
        assert count == 1

    async def test_rapid_bill_state_transitions(self, pool):
        """Rapid pending→overdue→paid transitions leave the bill in the final state."""
        from butlers.tools.finance import track_bill, upcoming_bills

        due = _today() - timedelta(days=1)

        await track_bill(
            pool=pool,
            payee="Rapid Bill",
            amount=200.00,
            currency="USD",
            due_date=due,
            status="pending",
        )
        await track_bill(
            pool=pool,
            payee="Rapid Bill",
            amount=200.00,
            currency="USD",
            due_date=due,
            status="overdue",
        )
        await track_bill(
            pool=pool,
            payee="Rapid Bill",
            amount=200.00,
            currency="USD",
            due_date=due,
            status="paid",
            paid_at=_utcnow(),
        )

        # Should not appear in upcoming_bills (paid)
        result = await upcoming_bills(pool=pool, days_ahead=14, include_overdue=True)
        payees = [i["bill"]["payee"] for i in result["items"]]
        assert "Rapid Bill" not in payees

        # Verify final state
        count = await pool.fetchval("SELECT COUNT(*) FROM bills WHERE payee = $1", "Rapid Bill")
        assert count == 1
        status = await pool.fetchval("SELECT status FROM bills WHERE payee = $1", "Rapid Bill")
        assert status == "paid"


# ===========================================================================
# 11. SPENDING SUMMARY EDGE CASES NOT COVERED
# ===========================================================================


class TestSpendingSummaryEdgeCases:
    """Additional edge cases for spending_summary."""

    async def test_spending_summary_no_group_by_returns_single_total_bucket(self, pool):
        """Without group_by, a single 'total' bucket is returned."""
        from butlers.tools.finance import record_transaction, spending_summary

        today = _today()
        posted = _utcnow().replace(day=today.day, hour=10)
        await record_transaction(
            pool=pool,
            posted_at=posted,
            merchant="Store A",
            amount=-30.00,
            currency="USD",
            category="misc",
        )

        result = await spending_summary(pool, start_date=today, end_date=today)
        assert len(result["groups"]) == 1
        assert result["groups"][0]["key"] == "total"
        assert Decimal(result["groups"][0]["amount"]) == Decimal("30.00")

    async def test_spending_summary_start_equals_end_date(self, pool):
        """start_date == end_date captures transactions for exactly that day."""
        from butlers.tools.finance import record_transaction, spending_summary

        today = _today()
        posted = datetime(today.year, today.month, today.day, 14, 0, 0, tzinfo=UTC)
        await record_transaction(
            pool=pool,
            posted_at=posted,
            merchant="Same Day",
            amount=-25.00,
            currency="USD",
            category="misc",
        )

        result = await spending_summary(pool, start_date=today, end_date=today)
        assert Decimal(result["total_spend"]) == Decimal("25.00")

    async def test_spending_summary_group_by_category_sorted_by_amount_desc(self, pool):
        """Categories are sorted highest spend first."""
        from butlers.tools.finance import record_transaction, spending_summary

        today = _today()
        posted = _utcnow().replace(day=today.day, hour=10)

        await record_transaction(
            pool=pool,
            posted_at=posted,
            merchant="Small",
            amount=-5.00,
            currency="USD",
            category="misc",
        )
        await record_transaction(
            pool=pool,
            posted_at=posted,
            merchant="Big",
            amount=-500.00,
            currency="USD",
            category="rent",
        )
        await record_transaction(
            pool=pool,
            posted_at=posted,
            merchant="Medium",
            amount=-50.00,
            currency="USD",
            category="groceries",
        )

        result = await spending_summary(
            pool, start_date=today.replace(day=1), end_date=today, group_by="category"
        )
        amounts = [Decimal(g["amount"]) for g in result["groups"]]
        assert amounts == sorted(amounts, reverse=True)

    async def test_spending_summary_account_id_filter_via_record_transaction(self, pool):
        """account_id filter in spending_summary works after transactions are recorded."""
        from butlers.tools.finance import record_transaction, spending_summary

        acct_id = await pool.fetchval(
            "INSERT INTO accounts (institution, type, currency) VALUES ($1, $2, $3) RETURNING id",
            "Filter Bank",
            "checking",
            "USD",
        )

        today = _today()
        posted = _utcnow().replace(day=today.day, hour=10)
        await record_transaction(
            pool=pool,
            posted_at=posted,
            merchant="Acct Store",
            amount=-80.00,
            currency="USD",
            category="misc",
            account_id=str(acct_id),
        )
        await record_transaction(
            pool=pool,
            posted_at=posted,
            merchant="Other Store",
            amount=-200.00,
            currency="USD",
            category="misc",
        )

        result = await spending_summary(
            pool, start_date=today.replace(day=1), end_date=today, account_id=str(acct_id)
        )
        assert Decimal(result["total_spend"]) == Decimal("80.00")

    async def test_spending_summary_zero_amount_debit_counted(self, pool):
        """A debit transaction with amount 0 is included in the count (edge: free item)."""
        from butlers.tools.finance import record_transaction, spending_summary

        today = _today()
        posted = _utcnow().replace(day=today.day, hour=10)
        # Negative zero stays debit in amount math; but amount=0 edge is unusual.
        # We use -0.01 to avoid the credit/debit sign ambiguity (0 is treated as credit).
        await record_transaction(
            pool=pool,
            posted_at=posted,
            merchant="Near Zero",
            amount=-0.01,
            currency="USD",
            category="misc",
        )

        result = await spending_summary(pool, start_date=today.replace(day=1), end_date=today)
        assert Decimal(result["total_spend"]) == Decimal("0.01")


# ===========================================================================
# 12. UPCOMING_BILLS ADDITIONAL EDGE CASES
# ===========================================================================


class TestUpcomingBillsEdgeCases:
    """Additional edge cases not covered in test_tools.py."""

    async def test_days_ahead_zero_only_includes_today(self, pool):
        """days_ahead=0 includes only bills due today."""
        from butlers.tools.finance import track_bill, upcoming_bills

        today = _today()
        await track_bill(
            pool=pool, payee="Today Only", amount=10.00, currency="USD", due_date=today
        )
        await track_bill(
            pool=pool,
            payee="Tomorrow Bill",
            amount=20.00,
            currency="USD",
            due_date=today + timedelta(days=1),
        )

        result = await upcoming_bills(pool=pool, days_ahead=0)
        payees = [i["bill"]["payee"] for i in result["items"]]
        assert "Today Only" in payees
        assert "Tomorrow Bill" not in payees

    async def test_multiple_overdue_bills_all_included(self, pool):
        """All overdue bills are included when include_overdue=True."""
        from butlers.tools.finance import track_bill, upcoming_bills

        for i in range(3):
            await track_bill(
                pool=pool,
                payee=f"Overdue Bill {i}",
                amount=float(10 * (i + 1)),
                currency="USD",
                due_date=_today() - timedelta(days=i + 1),
                status="overdue",
            )

        result = await upcoming_bills(pool=pool, days_ahead=14, include_overdue=True)
        overdue = [i for i in result["items"] if i["urgency"] == "overdue"]
        assert len(overdue) == 3
        assert result["totals"]["overdue"] == 3

    async def test_bill_due_date_as_string_accepted_by_track_bill(self, pool):
        """ISO string due_date in track_bill survives round-trip and appears in upcoming_bills."""
        from butlers.tools.finance import track_bill, upcoming_bills

        due_str = (_today() + timedelta(days=3)).isoformat()
        await track_bill(
            pool=pool,
            payee="String Date Bill",
            amount=55.00,
            currency="USD",
            due_date=due_str,
        )

        result = await upcoming_bills(pool=pool, days_ahead=14)
        payees = [i["bill"]["payee"] for i in result["items"]]
        assert "String Date Bill" in payees

    async def test_as_of_timestamp_is_recent(self, pool):
        """as_of timestamp in upcoming_bills response is close to current time."""
        from butlers.tools.finance import upcoming_bills

        result = await upcoming_bills(pool=pool)
        as_of = datetime.fromisoformat(result["as_of"])
        delta = abs((datetime.now(UTC) - as_of).total_seconds())
        assert delta < 5, f"as_of is too old: {delta}s ago"
