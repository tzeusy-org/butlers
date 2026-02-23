"""Tests for Finance butler scheduled job handlers."""

from __future__ import annotations

import shutil
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _today() -> date:
    return date.today()


# ---------------------------------------------------------------------------
# Schema setup helper
# ---------------------------------------------------------------------------

CREATE_FINANCE_SCHEMA = "CREATE SCHEMA IF NOT EXISTS finance"

CREATE_BILLS_SQL = """
CREATE TABLE IF NOT EXISTS finance.bills (
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

CREATE_SUBSCRIPTIONS_SQL = """
CREATE TABLE IF NOT EXISTS finance.subscriptions (
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

CREATE_TRANSACTIONS_SQL = """
CREATE TABLE IF NOT EXISTS finance.transactions (
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
    metadata          JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


async def _setup_finance_schema(pool) -> None:
    """Create the finance schema and all required tables."""
    await pool.execute(CREATE_FINANCE_SCHEMA)
    await pool.execute(CREATE_BILLS_SQL)
    await pool.execute(CREATE_SUBSCRIPTIONS_SQL)
    await pool.execute(CREATE_TRANSACTIONS_SQL)


# ---------------------------------------------------------------------------
# Helper insert functions
# ---------------------------------------------------------------------------


async def _insert_bill(
    pool,
    *,
    payee: str = "Electric Company",
    amount: str = "100.00",
    currency: str = "USD",
    due_date: date | None = None,
    frequency: str = "monthly",
    status: str = "pending",
) -> None:
    if due_date is None:
        due_date = _today() + timedelta(days=7)
    await pool.execute(
        """
        INSERT INTO finance.bills (payee, amount, currency, due_date, frequency, status)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        payee,
        amount,
        currency,
        due_date,
        frequency,
        status,
    )


async def _insert_subscription(
    pool,
    *,
    service: str = "Netflix",
    amount: str = "15.49",
    currency: str = "USD",
    frequency: str = "monthly",
    next_renewal: date | None = None,
    status: str = "active",
) -> None:
    if next_renewal is None:
        next_renewal = _today() + timedelta(days=5)
    await pool.execute(
        """
        INSERT INTO finance.subscriptions
            (service, amount, currency, frequency, next_renewal, status)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        service,
        amount,
        currency,
        frequency,
        next_renewal,
        status,
    )


async def _insert_transaction(
    pool,
    *,
    merchant: str = "ACME",
    amount: str = "50.00",
    currency: str = "USD",
    direction: str = "debit",
    category: str = "general",
    posted_at: datetime | None = None,
) -> None:
    if posted_at is None:
        posted_at = _utcnow()
    await pool.execute(
        """
        INSERT INTO finance.transactions
            (merchant, amount, currency, direction, category, posted_at)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        merchant,
        amount,
        currency,
        direction,
        category,
        posted_at,
    )


# ---------------------------------------------------------------------------
# Tests: run_upcoming_bills_check
# ---------------------------------------------------------------------------


async def test_upcoming_bills_check_no_bills(provisioned_postgres_pool):
    """No-op: returns zeros when bills table is empty."""
    from roster.finance.jobs.finance_jobs import run_upcoming_bills_check

    async with provisioned_postgres_pool() as pool:
        await _setup_finance_schema(pool)

        result = await run_upcoming_bills_check(pool)

        assert result["bills_found"] == 0
        assert result["overdue"] == 0
        assert result["due_today"] == 0
        assert result["due_soon"] == 0
        assert result["total_amount_due"] == "0.00"


async def test_upcoming_bills_check_due_soon(provisioned_postgres_pool):
    """Bills due within 14 days are found and classified as due_soon."""
    from roster.finance.jobs.finance_jobs import run_upcoming_bills_check

    async with provisioned_postgres_pool() as pool:
        await _setup_finance_schema(pool)

        await _insert_bill(
            pool, payee="Internet", amount="89.99", due_date=_today() + timedelta(days=5)
        )
        await _insert_bill(
            pool, payee="Water", amount="45.00", due_date=_today() + timedelta(days=10)
        )

        result = await run_upcoming_bills_check(pool)

        assert result["bills_found"] == 2
        assert result["due_soon"] == 2
        assert result["overdue"] == 0
        assert result["due_today"] == 0
        assert Decimal(result["total_amount_due"]) == Decimal("134.99")


async def test_upcoming_bills_check_due_today(provisioned_postgres_pool):
    """Bills due today are classified as due_today."""
    from roster.finance.jobs.finance_jobs import run_upcoming_bills_check

    async with provisioned_postgres_pool() as pool:
        await _setup_finance_schema(pool)
        await _insert_bill(pool, payee="Rent", amount="1800.00", due_date=_today())

        result = await run_upcoming_bills_check(pool)

        assert result["bills_found"] == 1
        assert result["due_today"] == 1
        assert result["overdue"] == 0
        assert result["due_soon"] == 0


async def test_upcoming_bills_check_overdue_status(provisioned_postgres_pool):
    """Bills with status=overdue are classified as overdue."""
    from roster.finance.jobs.finance_jobs import run_upcoming_bills_check

    async with provisioned_postgres_pool() as pool:
        await _setup_finance_schema(pool)
        await _insert_bill(
            pool,
            payee="Gas Bill",
            amount="75.00",
            due_date=_today() - timedelta(days=5),
            status="overdue",
        )

        result = await run_upcoming_bills_check(pool)

        assert result["bills_found"] == 1
        assert result["overdue"] == 1
        assert result["due_today"] == 0
        assert result["due_soon"] == 0


async def test_upcoming_bills_check_past_due_pending(provisioned_postgres_pool):
    """Bills with past due_date and status=pending are classified as overdue."""
    from roster.finance.jobs.finance_jobs import run_upcoming_bills_check

    async with provisioned_postgres_pool() as pool:
        await _setup_finance_schema(pool)
        await _insert_bill(
            pool,
            payee="Credit Card",
            amount="340.00",
            due_date=_today() - timedelta(days=3),
            status="pending",
        )

        result = await run_upcoming_bills_check(pool)

        assert result["bills_found"] == 1
        assert result["overdue"] == 1


async def test_upcoming_bills_check_excludes_paid(provisioned_postgres_pool):
    """Paid bills are excluded from upcoming bills check."""
    from roster.finance.jobs.finance_jobs import run_upcoming_bills_check

    async with provisioned_postgres_pool() as pool:
        await _setup_finance_schema(pool)
        await _insert_bill(
            pool,
            payee="Phone Bill",
            amount="60.00",
            due_date=_today() + timedelta(days=3),
            status="paid",
        )

        result = await run_upcoming_bills_check(pool)

        assert result["bills_found"] == 0


async def test_upcoming_bills_check_excludes_far_future(provisioned_postgres_pool):
    """Bills due beyond 14 days are not included."""
    from roster.finance.jobs.finance_jobs import run_upcoming_bills_check

    async with provisioned_postgres_pool() as pool:
        await _setup_finance_schema(pool)
        await _insert_bill(
            pool,
            payee="Insurance",
            amount="200.00",
            due_date=_today() + timedelta(days=30),
            status="pending",
        )

        result = await run_upcoming_bills_check(pool)

        assert result["bills_found"] == 0


async def test_upcoming_bills_check_mixed_urgency(provisioned_postgres_pool):
    """Mixed urgency bill set is classified correctly."""
    from roster.finance.jobs.finance_jobs import run_upcoming_bills_check

    async with provisioned_postgres_pool() as pool:
        await _setup_finance_schema(pool)

        await _insert_bill(
            pool,
            payee="Overdue Bill",
            amount="50.00",
            due_date=_today() - timedelta(days=2),
            status="overdue",
        )
        await _insert_bill(pool, payee="Today Bill", amount="100.00", due_date=_today())
        await _insert_bill(
            pool, payee="Soon Bill", amount="75.00", due_date=_today() + timedelta(days=7)
        )

        result = await run_upcoming_bills_check(pool)

        assert result["bills_found"] == 3
        assert result["overdue"] == 1
        assert result["due_today"] == 1
        assert result["due_soon"] == 1
        assert Decimal(result["total_amount_due"]) == Decimal("225.00")


# ---------------------------------------------------------------------------
# Tests: run_subscription_renewal_alerts
# ---------------------------------------------------------------------------


async def test_subscription_renewal_alerts_no_subscriptions(provisioned_postgres_pool):
    """No-op: returns zeros when subscriptions table is empty."""
    from roster.finance.jobs.finance_jobs import run_subscription_renewal_alerts

    async with provisioned_postgres_pool() as pool:
        await _setup_finance_schema(pool)

        result = await run_subscription_renewal_alerts(pool)

        assert result["renewals_found"] == 0
        assert result["total_renewal_amount"] == "0.00"


async def test_subscription_renewal_alerts_upcoming_renewal(provisioned_postgres_pool):
    """Active subscriptions renewing within 7 days are found."""
    from roster.finance.jobs.finance_jobs import run_subscription_renewal_alerts

    async with provisioned_postgres_pool() as pool:
        await _setup_finance_schema(pool)

        await _insert_subscription(
            pool, service="Netflix", amount="15.49", next_renewal=_today() + timedelta(days=3)
        )
        await _insert_subscription(
            pool, service="Spotify", amount="9.99", next_renewal=_today() + timedelta(days=6)
        )

        result = await run_subscription_renewal_alerts(pool)

        assert result["renewals_found"] == 2
        assert Decimal(result["total_renewal_amount"]) == Decimal("25.48")


async def test_subscription_renewal_alerts_renewing_today(provisioned_postgres_pool):
    """Subscriptions renewing today are included."""
    from roster.finance.jobs.finance_jobs import run_subscription_renewal_alerts

    async with provisioned_postgres_pool() as pool:
        await _setup_finance_schema(pool)
        await _insert_subscription(pool, service="Adobe", amount="54.99", next_renewal=_today())

        result = await run_subscription_renewal_alerts(pool)

        assert result["renewals_found"] == 1
        assert Decimal(result["total_renewal_amount"]) == Decimal("54.99")


async def test_subscription_renewal_alerts_excludes_cancelled(provisioned_postgres_pool):
    """Cancelled subscriptions are excluded."""
    from roster.finance.jobs.finance_jobs import run_subscription_renewal_alerts

    async with provisioned_postgres_pool() as pool:
        await _setup_finance_schema(pool)
        await _insert_subscription(
            pool,
            service="Gym",
            amount="30.00",
            next_renewal=_today() + timedelta(days=2),
            status="cancelled",
        )

        result = await run_subscription_renewal_alerts(pool)

        assert result["renewals_found"] == 0


async def test_subscription_renewal_alerts_excludes_paused(provisioned_postgres_pool):
    """Paused subscriptions are excluded."""
    from roster.finance.jobs.finance_jobs import run_subscription_renewal_alerts

    async with provisioned_postgres_pool() as pool:
        await _setup_finance_schema(pool)
        await _insert_subscription(
            pool,
            service="Hulu",
            amount="17.99",
            next_renewal=_today() + timedelta(days=4),
            status="paused",
        )

        result = await run_subscription_renewal_alerts(pool)

        assert result["renewals_found"] == 0


async def test_subscription_renewal_alerts_excludes_far_future(provisioned_postgres_pool):
    """Subscriptions renewing more than 7 days away are excluded."""
    from roster.finance.jobs.finance_jobs import run_subscription_renewal_alerts

    async with provisioned_postgres_pool() as pool:
        await _setup_finance_schema(pool)
        await _insert_subscription(
            pool, service="iCloud", amount="2.99", next_renewal=_today() + timedelta(days=8)
        )

        result = await run_subscription_renewal_alerts(pool)

        assert result["renewals_found"] == 0


async def test_subscription_renewal_alerts_excludes_past_renewals(provisioned_postgres_pool):
    """Subscriptions with next_renewal in the past are excluded."""
    from roster.finance.jobs.finance_jobs import run_subscription_renewal_alerts

    async with provisioned_postgres_pool() as pool:
        await _setup_finance_schema(pool)
        await _insert_subscription(
            pool,
            service="Old Service",
            amount="10.00",
            next_renewal=_today() - timedelta(days=1),
            status="active",
        )

        result = await run_subscription_renewal_alerts(pool)

        assert result["renewals_found"] == 0


# ---------------------------------------------------------------------------
# Tests: run_monthly_spending_summary
# ---------------------------------------------------------------------------


def _prior_month_mid() -> datetime:
    """Return a datetime in the middle of the prior calendar month (UTC)."""
    today = date.today()
    first_of_this_month = today.replace(day=1)
    last_month_start = (first_of_this_month - timedelta(days=1)).replace(day=1)
    return datetime(last_month_start.year, last_month_start.month, 10, 12, 0, 0, tzinfo=UTC)


def _two_months_ago_mid() -> datetime:
    """Return a datetime in the middle of two months ago (UTC)."""
    today = date.today()
    first_of_this_month = today.replace(day=1)
    last_month_start = (first_of_this_month - timedelta(days=1)).replace(day=1)
    two_months_start = (last_month_start - timedelta(days=1)).replace(day=1)
    return datetime(two_months_start.year, two_months_start.month, 10, 12, 0, 0, tzinfo=UTC)


async def test_monthly_spending_summary_no_transactions(provisioned_postgres_pool):
    """No-op: returns zeros when no transactions exist in the prior month."""
    from roster.finance.jobs.finance_jobs import run_monthly_spending_summary

    async with provisioned_postgres_pool() as pool:
        await _setup_finance_schema(pool)

        result = await run_monthly_spending_summary(pool)

        today = date.today()
        first_of_this_month = today.replace(day=1)
        last_month_start = (first_of_this_month - timedelta(days=1)).replace(day=1)
        expected_period = last_month_start.strftime("%Y-%m")

        assert result["period"] == expected_period
        assert result["total_spend"] == "0.00"
        assert result["categories"] == 0
        assert result["merchants"] == 0
        assert result["notable_changes"] == 0


async def test_monthly_spending_summary_basic(provisioned_postgres_pool):
    """Transactions in the prior month are aggregated correctly."""
    from roster.finance.jobs.finance_jobs import run_monthly_spending_summary

    async with provisioned_postgres_pool() as pool:
        await _setup_finance_schema(pool)

        mid_prior = _prior_month_mid()
        await _insert_transaction(
            pool,
            merchant="Trader Joe's",
            amount="55.00",
            direction="debit",
            category="groceries",
            posted_at=mid_prior,
        )
        await _insert_transaction(
            pool,
            merchant="Netflix",
            amount="15.49",
            direction="debit",
            category="subscriptions",
            posted_at=mid_prior,
        )
        await _insert_transaction(
            pool,
            merchant="Starbucks",
            amount="6.75",
            direction="debit",
            category="dining",
            posted_at=mid_prior,
        )

        result = await run_monthly_spending_summary(pool)

        assert Decimal(result["total_spend"]) == Decimal("77.24")
        assert result["categories"] == 3
        assert result["merchants"] == 3


async def test_monthly_spending_summary_excludes_credits(provisioned_postgres_pool):
    """Credit transactions are excluded from spending summary."""
    from roster.finance.jobs.finance_jobs import run_monthly_spending_summary

    async with provisioned_postgres_pool() as pool:
        await _setup_finance_schema(pool)

        mid_prior = _prior_month_mid()
        await _insert_transaction(
            pool,
            merchant="Amazon",
            amount="50.00",
            direction="credit",
            category="refunds",
            posted_at=mid_prior,
        )

        result = await run_monthly_spending_summary(pool)

        assert result["total_spend"] == "0.00"
        assert result["categories"] == 0


async def test_monthly_spending_summary_excludes_current_month(provisioned_postgres_pool):
    """Transactions from the current month are not included."""
    from roster.finance.jobs.finance_jobs import run_monthly_spending_summary

    async with provisioned_postgres_pool() as pool:
        await _setup_finance_schema(pool)

        current_month_tx = _utcnow()
        await _insert_transaction(
            pool,
            merchant="Gas Station",
            amount="45.00",
            direction="debit",
            category="fuel",
            posted_at=current_month_tx,
        )

        result = await run_monthly_spending_summary(pool)

        assert result["total_spend"] == "0.00"


async def test_monthly_spending_summary_merchant_top_10(provisioned_postgres_pool):
    """Only top 10 merchants by spend are counted."""
    from roster.finance.jobs.finance_jobs import run_monthly_spending_summary

    async with provisioned_postgres_pool() as pool:
        await _setup_finance_schema(pool)

        mid_prior = _prior_month_mid()
        merchants = [
            ("Merchant A", "100.00"),
            ("Merchant B", "90.00"),
            ("Merchant C", "80.00"),
            ("Merchant D", "70.00"),
            ("Merchant E", "60.00"),
            ("Merchant F", "50.00"),
            ("Merchant G", "40.00"),
            ("Merchant H", "30.00"),
            ("Merchant I", "20.00"),
            ("Merchant J", "15.00"),
            ("Merchant K", "10.00"),  # excluded from top 10
        ]
        for merchant, amount in merchants:
            await _insert_transaction(
                pool,
                merchant=merchant,
                amount=amount,
                direction="debit",
                category="general",
                posted_at=mid_prior,
            )

        result = await run_monthly_spending_summary(pool)

        # merchants reflects top 10 (capped by LIMIT 10 in SQL)
        assert result["merchants"] == 10
        # categories still reflect all
        assert result["categories"] == 1


async def test_monthly_spending_summary_notable_changes_new_category(provisioned_postgres_pool):
    """A brand-new category in prior month (not in 2-months-ago) counts as notable."""
    from roster.finance.jobs.finance_jobs import run_monthly_spending_summary

    async with provisioned_postgres_pool() as pool:
        await _setup_finance_schema(pool)

        mid_prior = _prior_month_mid()
        # Insert only in prior month — no 2-months-ago equivalent
        await _insert_transaction(
            pool,
            merchant="New Merchant",
            amount="200.00",
            direction="debit",
            category="newcat",
            posted_at=mid_prior,
        )

        result = await run_monthly_spending_summary(pool)

        # "newcat" is new, so it's notable
        assert result["notable_changes"] >= 1


async def test_monthly_spending_summary_notable_changes_large_increase(provisioned_postgres_pool):
    """Category with >20% spend increase over prior month is flagged as notable."""
    from roster.finance.jobs.finance_jobs import run_monthly_spending_summary

    async with provisioned_postgres_pool() as pool:
        await _setup_finance_schema(pool)

        two_months_mid = _two_months_ago_mid()
        mid_prior = _prior_month_mid()

        # Two months ago: $100 on groceries
        await _insert_transaction(
            pool,
            merchant="Safeway",
            amount="100.00",
            direction="debit",
            category="groceries",
            posted_at=two_months_mid,
        )
        # Prior month: $200 on groceries (100% increase > 20% threshold)
        await _insert_transaction(
            pool,
            merchant="Whole Foods",
            amount="200.00",
            direction="debit",
            category="groceries",
            posted_at=mid_prior,
        )

        result = await run_monthly_spending_summary(pool)

        # Groceries went from $100 to $200 — 100% increase, notable
        assert result["notable_changes"] >= 1


async def test_monthly_spending_summary_no_notable_change_small_swing(provisioned_postgres_pool):
    """Categories with <=20% swing are not flagged as notable."""
    from roster.finance.jobs.finance_jobs import run_monthly_spending_summary

    async with provisioned_postgres_pool() as pool:
        await _setup_finance_schema(pool)

        two_months_mid = _two_months_ago_mid()
        mid_prior = _prior_month_mid()

        # Two months ago: $100 on dining
        await _insert_transaction(
            pool,
            merchant="Restaurant A",
            amount="100.00",
            direction="debit",
            category="dining",
            posted_at=two_months_mid,
        )
        # Prior month: $110 on dining (10% increase, within threshold)
        await _insert_transaction(
            pool,
            merchant="Restaurant B",
            amount="110.00",
            direction="debit",
            category="dining",
            posted_at=mid_prior,
        )

        result = await run_monthly_spending_summary(pool)

        # dining has only a 10% swing — should not be notable
        assert result["notable_changes"] == 0


async def test_monthly_spending_summary_period_label(provisioned_postgres_pool):
    """Period label is formatted as YYYY-MM for the prior month."""
    from roster.finance.jobs.finance_jobs import run_monthly_spending_summary

    async with provisioned_postgres_pool() as pool:
        await _setup_finance_schema(pool)

        result = await run_monthly_spending_summary(pool)

        today = date.today()
        first_of_this_month = today.replace(day=1)
        last_month_start = (first_of_this_month - timedelta(days=1)).replace(day=1)
        expected = last_month_start.strftime("%Y-%m")

        assert result["period"] == expected
