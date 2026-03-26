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


async def test_monthly_spending_summary_notable_changes_disappeared_category(
    provisioned_postgres_pool,
):
    """A category present two months ago but absent last month counts as notable."""
    from roster.finance.jobs.finance_jobs import run_monthly_spending_summary

    async with provisioned_postgres_pool() as pool:
        await _setup_finance_schema(pool)

        two_months_mid = _two_months_ago_mid()

        # Category only present two months ago — it disappeared last month
        await _insert_transaction(
            pool,
            merchant="Old Gym",
            amount="40.00",
            direction="debit",
            category="fitness",
            posted_at=two_months_mid,
        )

        result = await run_monthly_spending_summary(pool)

        # "fitness" disappeared from last month — should be flagged as notable
        assert result["notable_changes"] >= 1


# ---------------------------------------------------------------------------
# Schema additions for insight scan tests
# ---------------------------------------------------------------------------

CREATE_BUDGETS_SQL = """
CREATE TABLE IF NOT EXISTS finance.budgets (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category         TEXT NOT NULL,
    period           TEXT NOT NULL CHECK (period IN ('weekly', 'monthly', 'quarterly', 'yearly')),
    amount           NUMERIC(14, 2) NOT NULL,
    currency         CHAR(3) NOT NULL DEFAULT 'USD',
    warn_threshold   NUMERIC(5, 4) NOT NULL DEFAULT 0.8000,
    alert_threshold  NUMERIC(5, 4) NOT NULL DEFAULT 1.0000,
    is_active        BOOLEAN NOT NULL DEFAULT true,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

CREATE_INSIGHT_TABLES_SQL = [
    """
    CREATE TABLE IF NOT EXISTS insight_settings (
        id INTEGER PRIMARY KEY DEFAULT 1,
        verbosity TEXT NOT NULL DEFAULT 'minimal',
        custom_budget INTEGER,
        quiet_start INTEGER,
        quiet_end INTEGER,
        quiet_timezone TEXT,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS insight_candidates (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        origin_butler TEXT NOT NULL,
        priority INTEGER NOT NULL CHECK (priority >= 1 AND priority <= 100),
        category TEXT NOT NULL,
        dedup_key TEXT NOT NULL,
        cooldown_days INTEGER,
        expires_at TIMESTAMPTZ NOT NULL,
        message TEXT NOT NULL,
        channel TEXT,
        metadata JSONB,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        status TEXT NOT NULL DEFAULT 'pending',
        delivered_at TIMESTAMPTZ,
        delivery_attempt_count INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS insight_cooldowns (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        dedup_key TEXT NOT NULL,
        cooldown_until TIMESTAMPTZ NOT NULL,
        reason TEXT NOT NULL DEFAULT 'delivered',
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS insight_engagement (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        insight_id UUID NOT NULL,
        delivered_at TIMESTAMPTZ NOT NULL,
        engaged BOOLEAN NOT NULL DEFAULT FALSE
    )
    """,
]


async def _setup_insight_schema(pool) -> None:
    """Create finance schema + insight tables for insight-scan tests."""
    await pool.execute(CREATE_FINANCE_SCHEMA)
    await pool.execute(CREATE_BILLS_SQL)
    await pool.execute(CREATE_SUBSCRIPTIONS_SQL)
    await pool.execute(CREATE_TRANSACTIONS_SQL)
    await pool.execute(CREATE_BUDGETS_SQL)
    for ddl in CREATE_INSIGHT_TABLES_SQL:
        await pool.execute(ddl)
    # Seed insight_settings with default verbosity (not 'off')
    await pool.execute(
        "INSERT INTO insight_settings (id, verbosity) "
        "VALUES (1, 'normal') ON CONFLICT (id) DO NOTHING"
    )


async def _insert_budget(
    pool,
    *,
    category: str = "groceries",
    period: str = "monthly",
    amount: str = "500.00",
    currency: str = "USD",
    warn_threshold: str = "0.8000",
    alert_threshold: str = "1.0000",
    is_active: bool = True,
) -> None:
    await pool.execute(
        """
        INSERT INTO finance.budgets
            (category, period, amount, currency, warn_threshold, alert_threshold, is_active)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        category,
        period,
        amount,
        currency,
        warn_threshold,
        alert_threshold,
        is_active,
    )


async def _insert_subscription(
    pool,
    *,
    service: str = "Adobe",
    amount: str = "599.00",
    currency: str = "USD",
    frequency: str = "yearly",
    next_renewal: date | None = None,
    status: str = "active",
) -> str:
    if next_renewal is None:
        next_renewal = _today() + timedelta(days=7)
    row_id = await pool.fetchval(
        """
        INSERT INTO finance.subscriptions
            (service, amount, currency, frequency, next_renewal, status)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING id
        """,
        service,
        amount,
        currency,
        frequency,
        next_renewal,
        status,
    )
    return str(row_id)


async def _insert_bill_returning_id(
    pool,
    *,
    payee: str = "Electric Company",
    amount: str = "100.00",
    currency: str = "USD",
    due_date: date | None = None,
    frequency: str = "monthly",
    status: str = "pending",
) -> str:
    if due_date is None:
        due_date = _today() + timedelta(days=2)
    row_id = await pool.fetchval(
        """
        INSERT INTO finance.bills (payee, amount, currency, due_date, frequency, status)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING id
        """,
        payee,
        amount,
        currency,
        due_date,
        frequency,
        status,
    )
    return str(row_id)


async def _count_candidates(pool) -> int:
    return await pool.fetchval("SELECT COUNT(*) FROM insight_candidates")


async def _fetch_candidates(pool) -> list[dict]:
    rows = await pool.fetch(
        "SELECT priority, category, dedup_key, message, cooldown_days, status "
        "FROM insight_candidates ORDER BY created_at"
    )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Tests: run_finance_insight_scan
# ---------------------------------------------------------------------------


async def test_insight_scan_empty_db_no_candidates(provisioned_postgres_pool):
    """No-op: empty finance tables produce no insight candidates."""
    from roster.finance.jobs.finance_jobs import run_finance_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        result = await run_finance_insight_scan(pool)

        assert result["submitted"] == 0
        assert result["accepted"] == 0
        assert result["early_exit"] is False
        assert await _count_candidates(pool) == 0


async def test_insight_scan_bill_due_within_1_day_priority_92(provisioned_postgres_pool):
    """Bill due tomorrow gets priority 92 (time-critical)."""
    from roster.finance.jobs.finance_jobs import run_finance_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        await _insert_bill_returning_id(
            pool, payee="Rent", amount="1200.00", due_date=_today() + timedelta(days=1)
        )

        result = await run_finance_insight_scan(pool)

        assert result["submitted"] >= 1
        assert result["accepted"] >= 1
        candidates = await _fetch_candidates(pool)
        bill_candidates = [c for c in candidates if c["category"] == "bill-due"]
        assert len(bill_candidates) == 1
        assert bill_candidates[0]["priority"] == 92


async def test_insight_scan_bill_due_within_3_days_priority_75(provisioned_postgres_pool):
    """Bill due in 3 days gets priority 75."""
    from roster.finance.jobs.finance_jobs import run_finance_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        await _insert_bill_returning_id(
            pool, payee="Internet", amount="89.00", due_date=_today() + timedelta(days=3)
        )

        await run_finance_insight_scan(pool)

        candidates = await _fetch_candidates(pool)
        bill_candidates = [c for c in candidates if c["category"] == "bill-due"]
        assert len(bill_candidates) == 1
        assert bill_candidates[0]["priority"] == 75


async def test_insight_scan_bill_due_beyond_3_days_excluded(provisioned_postgres_pool):
    """Bills due more than 3 days away do not generate insight candidates."""
    from roster.finance.jobs.finance_jobs import run_finance_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        await _insert_bill_returning_id(
            pool, payee="Insurance", amount="200.00", due_date=_today() + timedelta(days=5)
        )

        result = await run_finance_insight_scan(pool)

        assert result["submitted"] == 0
        assert await _count_candidates(pool) == 0


async def test_insight_scan_bill_paid_excluded(provisioned_postgres_pool):
    """Paid bills do not generate insight candidates."""
    from roster.finance.jobs.finance_jobs import run_finance_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        await _insert_bill_returning_id(
            pool,
            payee="Water",
            amount="50.00",
            due_date=_today() + timedelta(days=1),
            status="paid",
        )

        result = await run_finance_insight_scan(pool)

        assert result["submitted"] == 0


async def test_insight_scan_bill_dedup_key_format(provisioned_postgres_pool):
    """Bill insight dedup_key matches finance:bill-due:{bill_id}:{due_date}."""
    from roster.finance.jobs.finance_jobs import run_finance_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        due = _today() + timedelta(days=2)
        bill_id = await _insert_bill_returning_id(pool, due_date=due)

        await run_finance_insight_scan(pool)

        candidates = await _fetch_candidates(pool)
        bill_cands = [c for c in candidates if c["category"] == "bill-due"]
        assert len(bill_cands) == 1
        expected_key = f"finance:bill-due:{bill_id}:{due.isoformat()}"
        assert bill_cands[0]["dedup_key"] == expected_key


async def test_insight_scan_bill_cooldown_days_is_1(provisioned_postgres_pool):
    """Bill insight has cooldown_days=1."""
    from roster.finance.jobs.finance_jobs import run_finance_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        await _insert_bill_returning_id(pool, due_date=_today() + timedelta(days=1))

        await run_finance_insight_scan(pool)

        candidates = await _fetch_candidates(pool)
        bill_cands = [c for c in candidates if c["category"] == "bill-due"]
        assert bill_cands[0]["cooldown_days"] == 1


async def test_insight_scan_budget_90pct_priority_70(provisioned_postgres_pool):
    """Budget at 90%+ utilisation gets priority 70."""
    from roster.finance.jobs.finance_jobs import run_finance_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        # Budget: $500 for groceries this month
        await _insert_budget(pool, category="groceries", amount="500.00")

        # Spend $460 (92%) this month
        month_start = _today().replace(day=1)
        tx_date = datetime(month_start.year, month_start.month, 15, 12, 0, 0, tzinfo=UTC)
        await _insert_transaction(
            pool,
            merchant="Whole Foods",
            amount="460.00",
            direction="debit",
            category="groceries",
            posted_at=tx_date,
        )

        await run_finance_insight_scan(pool)

        candidates = await _fetch_candidates(pool)
        budget_cands = [c for c in candidates if c["category"] == "budget-threshold"]
        assert len(budget_cands) == 1
        assert budget_cands[0]["priority"] == 70


async def test_insight_scan_budget_80_to_90pct_priority_50(provisioned_postgres_pool):
    """Budget at 80–90% utilisation gets priority 50."""
    from roster.finance.jobs.finance_jobs import run_finance_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        await _insert_budget(pool, category="dining", amount="300.00")

        month_start = _today().replace(day=1)
        tx_date = datetime(month_start.year, month_start.month, 15, 12, 0, 0, tzinfo=UTC)
        # Spend $255 (85%) this month
        await _insert_transaction(
            pool,
            merchant="Restaurant",
            amount="255.00",
            direction="debit",
            category="dining",
            posted_at=tx_date,
        )

        await run_finance_insight_scan(pool)

        candidates = await _fetch_candidates(pool)
        budget_cands = [c for c in candidates if c["category"] == "budget-threshold"]
        assert len(budget_cands) == 1
        assert budget_cands[0]["priority"] == 50


async def test_insight_scan_budget_below_80pct_no_candidate(provisioned_postgres_pool):
    """Budget below 80% does not generate an insight candidate."""
    from roster.finance.jobs.finance_jobs import run_finance_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        await _insert_budget(pool, category="transport", amount="200.00")

        month_start = _today().replace(day=1)
        tx_date = datetime(month_start.year, month_start.month, 15, 12, 0, 0, tzinfo=UTC)
        # Spend $100 (50%) — below threshold
        await _insert_transaction(
            pool,
            merchant="Uber",
            amount="100.00",
            direction="debit",
            category="transport",
            posted_at=tx_date,
        )

        await run_finance_insight_scan(pool)

        candidates = await _fetch_candidates(pool)
        budget_cands = [c for c in candidates if c["category"] == "budget-threshold"]
        assert len(budget_cands) == 0


async def test_insight_scan_budget_dedup_key_format(provisioned_postgres_pool):
    """Budget insight dedup_key matches finance:budget-threshold:{category}:{year-month}."""
    from roster.finance.jobs.finance_jobs import run_finance_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        await _insert_budget(pool, category="subscriptions", amount="100.00")

        month_start = _today().replace(day=1)
        tx_date = datetime(month_start.year, month_start.month, 15, 12, 0, 0, tzinfo=UTC)
        await _insert_transaction(
            pool,
            merchant="Netflix",
            amount="95.00",
            direction="debit",
            category="subscriptions",
            posted_at=tx_date,
        )

        await run_finance_insight_scan(pool)

        candidates = await _fetch_candidates(pool)
        budget_cands = [c for c in candidates if c["category"] == "budget-threshold"]
        year_month = _today().strftime("%Y-%m")
        expected_key = f"finance:budget-threshold:subscriptions:{year_month}"
        assert budget_cands[0]["dedup_key"] == expected_key


async def test_insight_scan_subscription_renewal_within_3_days_priority_75(
    provisioned_postgres_pool,
):
    """Annual subscription renewing within 3 days gets priority 75."""
    from roster.finance.jobs.finance_jobs import run_finance_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        await _insert_subscription(
            pool, service="Adobe", frequency="yearly", next_renewal=_today() + timedelta(days=2)
        )

        await run_finance_insight_scan(pool)

        candidates = await _fetch_candidates(pool)
        sub_cands = [c for c in candidates if c["category"] == "subscription-renewal"]
        assert len(sub_cands) == 1
        assert sub_cands[0]["priority"] == 75


async def test_insight_scan_subscription_renewal_within_14_days_priority_55(
    provisioned_postgres_pool,
):
    """Annual subscription renewing in 4–14 days gets priority 55."""
    from roster.finance.jobs.finance_jobs import run_finance_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        await _insert_subscription(
            pool,
            service="1Password",
            frequency="yearly",
            next_renewal=_today() + timedelta(days=10),
        )

        await run_finance_insight_scan(pool)

        candidates = await _fetch_candidates(pool)
        sub_cands = [c for c in candidates if c["category"] == "subscription-renewal"]
        assert len(sub_cands) == 1
        assert sub_cands[0]["priority"] == 55


async def test_insight_scan_monthly_subscription_excluded(provisioned_postgres_pool):
    """Monthly subscriptions do NOT generate insight candidates (only annual)."""
    from roster.finance.jobs.finance_jobs import run_finance_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        await _insert_subscription(
            pool,
            service="Netflix",
            frequency="monthly",
            next_renewal=_today() + timedelta(days=3),
        )

        await run_finance_insight_scan(pool)

        candidates = await _fetch_candidates(pool)
        sub_cands = [c for c in candidates if c["category"] == "subscription-renewal"]
        assert len(sub_cands) == 0


async def test_insight_scan_subscription_beyond_14_days_excluded(provisioned_postgres_pool):
    """Annual subscriptions renewing beyond 14 days are excluded."""
    from roster.finance.jobs.finance_jobs import run_finance_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        await _insert_subscription(
            pool, service="Dropbox", frequency="yearly", next_renewal=_today() + timedelta(days=20)
        )

        await run_finance_insight_scan(pool)

        assert await _count_candidates(pool) == 0


async def test_insight_scan_subscription_dedup_key_format(provisioned_postgres_pool):
    """Subscription insight dedup_key matches finance:subscription-renewal:{id}:{date}."""
    from roster.finance.jobs.finance_jobs import run_finance_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        renewal_date = _today() + timedelta(days=5)
        sub_id = await _insert_subscription(
            pool, service="Backblaze", frequency="yearly", next_renewal=renewal_date
        )

        await run_finance_insight_scan(pool)

        candidates = await _fetch_candidates(pool)
        sub_cands = [c for c in candidates if c["category"] == "subscription-renewal"]
        expected_key = f"finance:subscription-renewal:{sub_id}:{renewal_date.isoformat()}"
        assert sub_cands[0]["dedup_key"] == expected_key


async def test_insight_scan_spending_anomaly_over_30pct_generates_candidate(
    provisioned_postgres_pool,
):
    """Category spending >30% above 3-month average generates an insight."""
    from roster.finance.jobs.finance_jobs import run_finance_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        today = _today()
        month_start = today.replace(day=1)

        # Build 3 months of history: $100/month in groceries
        for months_back in range(1, 4):
            if month_start.month - months_back > 0:
                hist_month = month_start.replace(month=month_start.month - months_back)
            else:
                hist_month = month_start.replace(
                    year=month_start.year - 1, month=month_start.month - months_back + 12
                )
            tx_date = datetime(hist_month.year, hist_month.month, 15, 12, 0, 0, tzinfo=UTC)
            await _insert_transaction(
                pool,
                merchant="Supermarket",
                amount="100.00",
                direction="debit",
                category="groceries",
                posted_at=tx_date,
            )

        # Current month: $220 (120% above average of $100 — more than 100%)
        current_tx_date = datetime(month_start.year, month_start.month, 10, 12, 0, 0, tzinfo=UTC)
        await _insert_transaction(
            pool,
            merchant="Whole Foods",
            amount="220.00",
            direction="debit",
            category="groceries",
            posted_at=current_tx_date,
        )

        await run_finance_insight_scan(pool)

        candidates = await _fetch_candidates(pool)
        anomaly_cands = [c for c in candidates if c["category"] == "spending-anomaly"]
        assert len(anomaly_cands) == 1
        # 120% above average (> 100%) → priority 80
        assert anomaly_cands[0]["priority"] == 80


async def test_insight_scan_spending_anomaly_30_50pct_priority_50(provisioned_postgres_pool):
    """Category 30–50% above average gets priority 50."""
    from roster.finance.jobs.finance_jobs import run_finance_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        today = _today()
        month_start = today.replace(day=1)

        for months_back in range(1, 4):
            if month_start.month - months_back > 0:
                hist_month = month_start.replace(month=month_start.month - months_back)
            else:
                hist_month = month_start.replace(
                    year=month_start.year - 1, month=month_start.month - months_back + 12
                )
            tx_date = datetime(hist_month.year, hist_month.month, 15, 12, 0, 0, tzinfo=UTC)
            await _insert_transaction(
                pool,
                merchant="Restaurant",
                amount="100.00",
                direction="debit",
                category="dining",
                posted_at=tx_date,
            )

        # 40% above average: $140
        current_tx_date = datetime(month_start.year, month_start.month, 10, 12, 0, 0, tzinfo=UTC)
        await _insert_transaction(
            pool,
            merchant="Fancy Restaurant",
            amount="140.00",
            direction="debit",
            category="dining",
            posted_at=current_tx_date,
        )

        await run_finance_insight_scan(pool)

        candidates = await _fetch_candidates(pool)
        anomaly_cands = [c for c in candidates if c["category"] == "spending-anomaly"]
        assert len(anomaly_cands) == 1
        assert anomaly_cands[0]["priority"] == 50


async def test_insight_scan_spending_anomaly_50_100pct_priority_65(provisioned_postgres_pool):
    """Category 50–100% above average gets priority 65."""
    from roster.finance.jobs.finance_jobs import run_finance_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        today = _today()
        month_start = today.replace(day=1)

        for months_back in range(1, 4):
            if month_start.month - months_back > 0:
                hist_month = month_start.replace(month=month_start.month - months_back)
            else:
                hist_month = month_start.replace(
                    year=month_start.year - 1, month=month_start.month - months_back + 12
                )
            tx_date = datetime(hist_month.year, hist_month.month, 15, 12, 0, 0, tzinfo=UTC)
            await _insert_transaction(
                pool,
                merchant="Supermarket",
                amount="100.00",
                direction="debit",
                category="entertainment",
                posted_at=tx_date,
            )

        # 75% above average: $175
        current_tx_date = datetime(month_start.year, month_start.month, 10, 12, 0, 0, tzinfo=UTC)
        await _insert_transaction(
            pool,
            merchant="Cinema",
            amount="175.00",
            direction="debit",
            category="entertainment",
            posted_at=current_tx_date,
        )

        await run_finance_insight_scan(pool)

        candidates = await _fetch_candidates(pool)
        anomaly_cands = [c for c in candidates if c["category"] == "spending-anomaly"]
        assert len(anomaly_cands) == 1
        assert anomaly_cands[0]["priority"] == 65


async def test_insight_scan_spending_anomaly_below_30pct_no_candidate(provisioned_postgres_pool):
    """Category within 30% of average does NOT generate an insight."""
    from roster.finance.jobs.finance_jobs import run_finance_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        today = _today()
        month_start = today.replace(day=1)

        for months_back in range(1, 4):
            if month_start.month - months_back > 0:
                hist_month = month_start.replace(month=month_start.month - months_back)
            else:
                hist_month = month_start.replace(
                    year=month_start.year - 1, month=month_start.month - months_back + 12
                )
            tx_date = datetime(hist_month.year, hist_month.month, 15, 12, 0, 0, tzinfo=UTC)
            await _insert_transaction(
                pool,
                merchant="Grocery",
                amount="100.00",
                direction="debit",
                category="groceries",
                posted_at=tx_date,
            )

        # 20% above average — below threshold
        current_tx_date = datetime(month_start.year, month_start.month, 10, 12, 0, 0, tzinfo=UTC)
        await _insert_transaction(
            pool,
            merchant="Grocery",
            amount="120.00",
            direction="debit",
            category="groceries",
            posted_at=current_tx_date,
        )

        await run_finance_insight_scan(pool)

        candidates = await _fetch_candidates(pool)
        anomaly_cands = [c for c in candidates if c["category"] == "spending-anomaly"]
        assert len(anomaly_cands) == 0


async def test_insight_scan_spending_anomaly_fewer_than_3_months_excluded(
    provisioned_postgres_pool,
):
    """Categories with fewer than 3 months of history are excluded from anomaly detection."""
    from roster.finance.jobs.finance_jobs import run_finance_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        today = _today()
        month_start = today.replace(day=1)

        # Only 2 months of history
        for months_back in range(1, 3):
            if month_start.month - months_back > 0:
                hist_month = month_start.replace(month=month_start.month - months_back)
            else:
                hist_month = month_start.replace(
                    year=month_start.year - 1, month=month_start.month - months_back + 12
                )
            tx_date = datetime(hist_month.year, hist_month.month, 15, 12, 0, 0, tzinfo=UTC)
            await _insert_transaction(
                pool,
                merchant="NewMerchant",
                amount="100.00",
                direction="debit",
                category="newcat",
                posted_at=tx_date,
            )

        # Current month: $500 — would be anomalous if history were sufficient
        current_tx_date = datetime(month_start.year, month_start.month, 10, 12, 0, 0, tzinfo=UTC)
        await _insert_transaction(
            pool,
            merchant="NewMerchant",
            amount="500.00",
            direction="debit",
            category="newcat",
            posted_at=current_tx_date,
        )

        await run_finance_insight_scan(pool)

        candidates = await _fetch_candidates(pool)
        anomaly_cands = [c for c in candidates if c["category"] == "spending-anomaly"]
        assert len(anomaly_cands) == 0


async def test_insight_scan_spending_anomaly_dedup_key_format(provisioned_postgres_pool):
    """Anomaly insight dedup_key matches finance:spending-anomaly:{category}:{year-month}."""
    from roster.finance.jobs.finance_jobs import run_finance_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        today = _today()
        month_start = today.replace(day=1)

        for months_back in range(1, 4):
            if month_start.month - months_back > 0:
                hist_month = month_start.replace(month=month_start.month - months_back)
            else:
                hist_month = month_start.replace(
                    year=month_start.year - 1, month=month_start.month - months_back + 12
                )
            tx_date = datetime(hist_month.year, hist_month.month, 15, 12, 0, 0, tzinfo=UTC)
            await _insert_transaction(
                pool,
                merchant="Shop",
                amount="100.00",
                direction="debit",
                category="shopping",
                posted_at=tx_date,
            )

        current_tx_date = datetime(month_start.year, month_start.month, 10, 12, 0, 0, tzinfo=UTC)
        await _insert_transaction(
            pool,
            merchant="Shop",
            amount="300.00",
            direction="debit",
            category="shopping",
            posted_at=current_tx_date,
        )

        await run_finance_insight_scan(pool)

        candidates = await _fetch_candidates(pool)
        anomaly_cands = [c for c in candidates if c["category"] == "spending-anomaly"]
        year_month = today.strftime("%Y-%m")
        assert anomaly_cands[0]["dedup_key"] == f"finance:spending-anomaly:shopping:{year_month}"


async def test_insight_scan_verbosity_off_early_exit(provisioned_postgres_pool):
    """When verbosity=off, the first submission is filtered and no more are submitted."""
    from roster.finance.jobs.finance_jobs import run_finance_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        # Set verbosity to 'off'
        await pool.execute("UPDATE insight_settings SET verbosity = 'off' WHERE id = 1")

        # Add two bills due within 3 days
        await _insert_bill_returning_id(pool, payee="Bill A", due_date=_today() + timedelta(days=1))
        await _insert_bill_returning_id(pool, payee="Bill B", due_date=_today() + timedelta(days=2))

        result = await run_finance_insight_scan(pool)

        assert result["early_exit"] is True
        assert result["filtered"] >= 1
        # Only first candidate should have been submitted before early exit
        assert result["submitted"] == 1


async def test_insight_scan_result_has_expected_keys(provisioned_postgres_pool):
    """Result dict contains all expected keys."""
    from roster.finance.jobs.finance_jobs import run_finance_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        result = await run_finance_insight_scan(pool)

        assert "submitted" in result
        assert "accepted" in result
        assert "filtered" in result
        assert "errors" in result
        assert "early_exit" in result


async def test_insight_scan_multiple_categories_all_submitted(provisioned_postgres_pool):
    """Multiple categories (bill + subscription) each get a candidate submitted."""
    from roster.finance.jobs.finance_jobs import run_finance_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        # A bill due tomorrow
        await _insert_bill_returning_id(pool, payee="Rent", due_date=_today() + timedelta(days=1))
        # An annual subscription renewing in 5 days
        await _insert_subscription(
            pool, service="Adobe", frequency="yearly", next_renewal=_today() + timedelta(days=5)
        )

        result = await run_finance_insight_scan(pool)

        assert result["submitted"] == 2
        assert result["accepted"] == 2
        assert result["early_exit"] is False
