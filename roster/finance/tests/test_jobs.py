"""Tests for Finance butler scheduled job handlers."""

from __future__ import annotations

import json
import shutil
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

# _fj_module() reads this module from sys.modules; importing it here means a
# test selected alone does not depend on an earlier test having imported it.
import butlers.jobs._roster.finance_jobs  # noqa: F401
from butlers.core.owner_conditions import create_owner_conditions_table, get_active_condition
from butlers.tools.switchboard.insight.broker import create_insight_tables

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _today() -> date:
    """The UTC calendar date.

    Every fixture in this module stamps its rows as explicit UTC instants
    (``tzinfo=UTC``) and the code under test resolves its windows in the owner's
    configured timezone, which is UTC for these unconfigured test pools.  A
    LOCAL date here would put the fixture in a third frame, so these tests would
    pass or fail according to the developer's UTC offset -- which is exactly how
    a real production timezone skew stayed invisible to a UTC CI (bu-4zd9h).
    """
    return datetime.now(UTC).date()


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
    cancellation_url    TEXT,
    notice_period_days  INTEGER,
    cancel_by           DATE,
    cancellation_url_source TEXT NOT NULL DEFAULT 'extracted'
                            CHECK (cancellation_url_source IN ('owner', 'extracted')),
    status_source       TEXT NOT NULL DEFAULT 'extracted'
                            CHECK (status_source IN ('owner', 'extracted')),
    field_claims JSONB NOT NULL DEFAULT '[]'::jsonb,
    date_epoch BIGINT NOT NULL DEFAULT 0,
    renewal_source TEXT NOT NULL DEFAULT 'extracted',
    price_source TEXT NOT NULL DEFAULT 'extracted',
    metadata          JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

CREATE_OBLIGATION_LEDGER_SQL = """
CREATE TABLE IF NOT EXISTS finance.obligation_ledger (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subscription_id         UUID NOT NULL REFERENCES finance.subscriptions(id) ON DELETE CASCADE,
    period                  DATE NOT NULL,
    warn_by                 DATE,
    unknown_door            BOOLEAN NOT NULL DEFAULT false,
    price_change_amount     NUMERIC(14, 2),
    price_change_direction  TEXT
                                 CHECK (price_change_direction IS NULL
                                     OR price_change_direction IN ('increase', 'decrease')),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_obligation_ledger_subscription_period UNIQUE (subscription_id, period)
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
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at        TIMESTAMPTZ
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
# Schema additions for insight scan tests
# ---------------------------------------------------------------------------

CREATE_BUDGETS_SQL = """
CREATE TABLE IF NOT EXISTS finance.budgets (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category         TEXT NOT NULL,
    period           TEXT NOT NULL CHECK (period IN ('daily', 'weekly', 'monthly', 'quarterly', 'yearly')),
    amount           NUMERIC(14, 2) NOT NULL,
    currency         CHAR(3) NOT NULL DEFAULT 'USD',
    warn_threshold   NUMERIC(5, 4) NOT NULL DEFAULT 0.8000,
    alert_threshold  NUMERIC(5, 4) NOT NULL DEFAULT 1.0000,
    is_active        BOOLEAN NOT NULL DEFAULT true,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


async def _setup_insight_schema(pool) -> None:
    """Create finance schema + insight tables for insight-scan tests."""
    await pool.execute(CREATE_FINANCE_SCHEMA)
    await pool.execute(CREATE_BILLS_SQL)
    await pool.execute(CREATE_SUBSCRIPTIONS_SQL)
    await pool.execute(CREATE_TRANSACTIONS_SQL)
    await pool.execute(CREATE_BUDGETS_SQL)
    # Reuse the canonical shared helper instead of a hand-copied inline DDL, so
    # the insight_cooldowns schema (dedup_key TEXT PRIMARY KEY, mirroring
    # alembic core_010) can never drift here into the synthetic-id divergence
    # that masked the bu-tdd4k.1 production crash (bu-jgrn8).
    await create_insight_tables(pool)
    # bu-ep4ks.6: run_insight_scan also reconciles into the owner condition
    # ledger alongside insight-candidate submission -- provision it here too
    # so that side effect is exercised (not silently no-op'd by a missing
    # table) rather than added to a separate, divergent test setup helper.
    await create_owner_conditions_table(pool)
    # bu-8cdl1.10 slice 2: run_insight_scan also registers a forward
    # obligation ledger row per active subscription -- same rationale as the
    # owner_conditions table above.
    await pool.execute(CREATE_OBLIGATION_LEDGER_SQL)
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
    cancellation_url: str | None = None,
    notice_period_days: int | None = None,
    cancel_by: date | None = None,
    metadata: dict | None = None,
    cancellation_url_source: str = "extracted",
    status_source: str = "extracted",
) -> str:
    if next_renewal is None:
        next_renewal = _today() + timedelta(days=7)
    row_id = await pool.fetchval(
        """
        INSERT INTO finance.subscriptions
            (service, amount, currency, frequency, next_renewal, status,
             cancellation_url, notice_period_days, cancel_by, metadata,
             cancellation_url_source, status_source)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11, $12)
        RETURNING id
        """,
        service,
        amount,
        currency,
        frequency,
        next_renewal,
        status,
        cancellation_url,
        notice_period_days,
        cancel_by,
        json.dumps(metadata or {}),
        cancellation_url_source,
        status_source,
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
        "SELECT priority, category, dedup_key, message, cooldown_days, status, premise "
        "FROM insight_candidates ORDER BY created_at"
    )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Tests: run_insight_scan
# ---------------------------------------------------------------------------


async def test_insight_scan_empty_db_no_candidates(provisioned_postgres_pool):
    """No-op: empty finance tables produce no insight candidates."""
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        result = await run_insight_scan(pool)

        assert result["submitted"] == 0
        assert result["accepted"] == 0
        assert result["early_exit"] is False
        assert await _count_candidates(pool) == 0


async def test_insight_scan_bill_due_within_1_day_priority_92(provisioned_postgres_pool):
    """Bill due tomorrow gets priority 92 (time-critical)."""
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        await _insert_bill_returning_id(
            pool, payee="Rent", amount="1200.00", due_date=_today() + timedelta(days=1)
        )

        result = await run_insight_scan(pool)

        assert result["submitted"] >= 1
        assert result["accepted"] >= 1
        candidates = await _fetch_candidates(pool)
        bill_candidates = [c for c in candidates if c["category"] == "bill-due"]
        assert len(bill_candidates) == 1
        assert bill_candidates[0]["priority"] == 92


async def test_insight_scan_bill_due_within_3_days_priority_75(provisioned_postgres_pool):
    """Bill due in 3 days gets priority 75."""
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        await _insert_bill_returning_id(
            pool, payee="Internet", amount="89.00", due_date=_today() + timedelta(days=3)
        )

        await run_insight_scan(pool)

        candidates = await _fetch_candidates(pool)
        bill_candidates = [c for c in candidates if c["category"] == "bill-due"]
        assert len(bill_candidates) == 1
        assert bill_candidates[0]["priority"] == 75


async def test_insight_scan_bill_due_beyond_3_days_excluded(provisioned_postgres_pool):
    """Bills due more than 3 days away do not generate insight candidates."""
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        await _insert_bill_returning_id(
            pool, payee="Insurance", amount="200.00", due_date=_today() + timedelta(days=5)
        )

        result = await run_insight_scan(pool)

        assert result["submitted"] == 0
        assert await _count_candidates(pool) == 0


async def test_insight_scan_bill_paid_excluded(provisioned_postgres_pool):
    """Paid bills do not generate insight candidates."""
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        await _insert_bill_returning_id(
            pool,
            payee="Water",
            amount="50.00",
            due_date=_today() + timedelta(days=1),
            status="paid",
        )

        result = await run_insight_scan(pool)

        assert result["submitted"] == 0


async def test_insight_scan_overdue_bill_opens_owner_condition(provisioned_postgres_pool):
    """bu-ep4ks.6: a bill past due_date and still pending opens a standing
    owner condition -- a signal the pre-existing bill-due insight candidate
    never tracked (that category only ever fires while a bill is upcoming)."""
    from butlers.jobs._roster.finance_jobs import (
        _OWNER_CONDITION_BILL_OVERDUE_SOURCE,
        owner_condition_fingerprint,
        run_insight_scan,
    )

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        bill_id = await _insert_bill_returning_id(
            pool, payee="Electric Co", amount="75.00", due_date=_today() - timedelta(days=2)
        )

        await run_insight_scan(pool)

        fp = owner_condition_fingerprint(
            _OWNER_CONDITION_BILL_OVERDUE_SOURCE, 1, {"bill_id": bill_id}
        )
        active = await get_active_condition(
            pool, source=_OWNER_CONDITION_BILL_OVERDUE_SOURCE, fingerprint=fp
        )
        assert active is not None
        assert active["state"] == "open"
        assert "Electric Co" in active["summary"]


async def test_insight_scan_overdue_bill_condition_resolves_once_paid(provisioned_postgres_pool):
    """bu-ep4ks.6: paying an overdue bill resolves its owner condition on the
    next scan -- the ledger tracks "still true and unactioned", not a
    one-shot edge."""
    from butlers.jobs._roster.finance_jobs import (
        _OWNER_CONDITION_BILL_OVERDUE_SOURCE,
        owner_condition_fingerprint,
        run_insight_scan,
    )

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        bill_id = await _insert_bill_returning_id(
            pool, payee="Water Co", amount="40.00", due_date=_today() - timedelta(days=1)
        )
        await run_insight_scan(pool)

        fp = owner_condition_fingerprint(
            _OWNER_CONDITION_BILL_OVERDUE_SOURCE, 1, {"bill_id": bill_id}
        )
        assert (
            await get_active_condition(
                pool, source=_OWNER_CONDITION_BILL_OVERDUE_SOURCE, fingerprint=fp
            )
        ) is not None

        await pool.execute("UPDATE finance.bills SET status = 'paid' WHERE id = $1::uuid", bill_id)
        await run_insight_scan(pool)

        assert (
            await get_active_condition(
                pool, source=_OWNER_CONDITION_BILL_OVERDUE_SOURCE, fingerprint=fp
            )
        ) is None


async def test_insight_scan_upcoming_bill_does_not_open_overdue_condition(
    provisioned_postgres_pool,
):
    """A bill due in the future (not yet overdue) must not open the
    bill-overdue owner condition -- only the existing bill-due insight
    candidate covers "upcoming"."""
    from butlers.core.owner_conditions import list_conditions
    from butlers.jobs._roster.finance_jobs import (
        _OWNER_CONDITION_BILL_OVERDUE_SOURCE,
        run_insight_scan,
    )

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        await _insert_bill_returning_id(
            pool, payee="Internet", amount="89.00", due_date=_today() + timedelta(days=1)
        )

        await run_insight_scan(pool)

        total, _rows = await list_conditions(pool, source=_OWNER_CONDITION_BILL_OVERDUE_SOURCE)
        assert total == 0


async def test_insight_scan_missing_owner_conditions_table_does_not_break_scan(
    provisioned_postgres_pool,
):
    """bu-ep4ks.6: owner_conditions reconciliation is best-effort -- an
    unmigrated pool (owner_conditions table absent) must not break insight
    candidate submission."""
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        # Deliberately skip create_owner_conditions_table here.
        await pool.execute(CREATE_FINANCE_SCHEMA)
        await pool.execute(CREATE_BILLS_SQL)
        await pool.execute(CREATE_SUBSCRIPTIONS_SQL)
        await pool.execute(CREATE_TRANSACTIONS_SQL)
        await pool.execute(CREATE_BUDGETS_SQL)
        await create_insight_tables(pool)
        await pool.execute(
            "INSERT INTO insight_settings (id, verbosity) "
            "VALUES (1, 'normal') ON CONFLICT (id) DO NOTHING"
        )

        await _insert_bill_returning_id(
            pool, payee="Electric Co", amount="75.00", due_date=_today() - timedelta(days=2)
        )

        result = await run_insight_scan(pool)
        assert result["errors"] == 0


async def test_insight_scan_registers_obligation_ledger_row(provisioned_postgres_pool):
    """bu-8cdl1.10 slice 2: run_insight_scan registers a forward obligation
    ledger row (as a state side effect, not an insight candidate) for every
    active subscription's next renewal."""
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        sub_id = await _insert_subscription(
            pool, service="Netflix", next_renewal=_today() + timedelta(days=30)
        )

        await run_insight_scan(pool)

        row = await pool.fetchrow(
            "SELECT * FROM finance.obligation_ledger WHERE subscription_id = $1::uuid", sub_id
        )
        assert row is not None
        assert row["unknown_door"] is True  # no cancellation_url/notice_period_days/cancel_by set


async def test_insight_scan_missing_obligation_ledger_table_does_not_break_scan(
    provisioned_postgres_pool,
):
    """bu-8cdl1.10 slice 2: obligation ledger registration is best-effort -- an
    unmigrated pool (obligation_ledger table absent) must not break insight
    candidate submission."""
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        # Deliberately skip creating finance.obligation_ledger here.
        await pool.execute(CREATE_FINANCE_SCHEMA)
        await pool.execute(CREATE_BILLS_SQL)
        await pool.execute(CREATE_SUBSCRIPTIONS_SQL)
        await pool.execute(CREATE_TRANSACTIONS_SQL)
        await pool.execute(CREATE_BUDGETS_SQL)
        await create_insight_tables(pool)
        await create_owner_conditions_table(pool)
        await pool.execute(
            "INSERT INTO insight_settings (id, verbosity) "
            "VALUES (1, 'normal') ON CONFLICT (id) DO NOTHING"
        )

        await _insert_subscription(
            pool, service="Netflix", next_renewal=_today() + timedelta(days=30)
        )

        result = await run_insight_scan(pool)
        assert result["errors"] == 0


async def test_insight_scan_registers_obligation_ledger_despite_verbosity_off_early_exit(
    provisioned_postgres_pool,
):
    """bu-8cdl1.10 slice 2: the ledger write must run even when verbosity=off
    trips the very first candidate submission -- it is a STATE side effect
    alongside candidate delivery, not gated by whether any candidate survives
    the verbosity filter."""
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)
        await pool.execute("UPDATE insight_settings SET verbosity = 'off' WHERE id = 1")

        sub_id = await _insert_subscription(
            pool, service="Netflix", next_renewal=_today() + timedelta(days=30)
        )
        # A bill due soon is an unconditional bill-due submission (section 2),
        # so the very first _submit() call reliably trips the verbosity-off
        # early exit regardless of anomaly/budget history.
        await _insert_bill_returning_id(
            pool, payee="Electric Co", due_date=_today() + timedelta(days=1)
        )

        result = await run_insight_scan(pool)
        assert result["early_exit"] is True

        row = await pool.fetchrow(
            "SELECT * FROM finance.obligation_ledger WHERE subscription_id = $1::uuid", sub_id
        )
        assert row is not None


async def test_insight_scan_spending_anomaly_opens_owner_condition(provisioned_postgres_pool):
    """bu-ep4ks.6: an anomalous category also opens a standing owner
    condition, scoped to (category, month)."""
    from butlers.jobs._roster.finance_jobs import (
        _OWNER_CONDITION_SPENDING_ANOMALY_SOURCE,
        owner_condition_fingerprint,
        run_insight_scan,
    )

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
                category="groceries",
                posted_at=tx_date,
            )
        current_day = min(today.day, 28)
        current_tx_date = datetime(today.year, today.month, current_day, 12, 0, 0, tzinfo=UTC)
        await _insert_transaction(
            pool,
            merchant="Whole Foods",
            amount="220.00",
            direction="debit",
            category="groceries",
            posted_at=current_tx_date,
        )

        await run_insight_scan(pool)

        year_month = today.strftime("%Y-%m")
        fp = owner_condition_fingerprint(
            _OWNER_CONDITION_SPENDING_ANOMALY_SOURCE,
            1,
            {"category": "groceries", "month": year_month},
        )
        active = await get_active_condition(
            pool, source=_OWNER_CONDITION_SPENDING_ANOMALY_SOURCE, fingerprint=fp
        )
        assert active is not None
        assert active["state"] == "open"


async def test_insight_scan_bill_dedup_key_format(provisioned_postgres_pool):
    """Bill insight dedup_key matches finance:bill-due:{bill_id}:{due_date}."""
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        due = _today() + timedelta(days=2)
        bill_id = await _insert_bill_returning_id(pool, due_date=due)

        await run_insight_scan(pool)

        candidates = await _fetch_candidates(pool)
        bill_cands = [c for c in candidates if c["category"] == "bill-due"]
        assert len(bill_cands) == 1
        expected_key = f"finance:bill-due:{bill_id}:{due.isoformat()}"
        assert bill_cands[0]["dedup_key"] == expected_key
        # bu-q7vx1q.5: the "due soon" claim names the fact it rests on, so the
        # broker can withdraw it unsent if the bill is paid before delivery.
        premise = bill_cands[0]["premise"]
        premise = json.loads(premise) if isinstance(premise, str) else premise
        assert premise == {
            "kind": "probe",
            "butler": "finance",
            "probe": "bill_still_pending",
            "args": {"bill_id": str(bill_id)},
        }


async def test_insight_scan_bill_cooldown_days_is_1(provisioned_postgres_pool):
    """Bill insight has cooldown_days=1."""
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        await _insert_bill_returning_id(pool, due_date=_today() + timedelta(days=1))

        await run_insight_scan(pool)

        candidates = await _fetch_candidates(pool)
        bill_cands = [c for c in candidates if c["category"] == "bill-due"]
        assert bill_cands[0]["cooldown_days"] == 1


async def test_insight_scan_budget_90pct_priority_70(provisioned_postgres_pool):
    """Budget at 90%+ utilisation (per its own configured alert_threshold) gets priority 70.

    bu-rvz2o: insight_scan now reads each budget's configured warn_threshold/
    alert_threshold instead of hardcoded 80%/90% — this budget explicitly
    configures alert_threshold=0.90 to preserve the original test scenario.
    """
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        # Budget: $500 for groceries this month, exceeded at 90% (not the 100% default)
        await _insert_budget(pool, category="groceries", amount="500.00", alert_threshold="0.9000")

        # Spend $460 (92%) this month
        today = _today()
        safe_day = min(today.day, 28)
        tx_date = datetime(today.year, today.month, safe_day, 12, 0, 0, tzinfo=UTC)
        await _insert_transaction(
            pool,
            merchant="Whole Foods",
            amount="460.00",
            direction="debit",
            category="groceries",
            posted_at=tx_date,
        )

        await run_insight_scan(pool)

        candidates = await _fetch_candidates(pool)
        budget_cands = [c for c in candidates if c["category"] == "budget-threshold"]
        assert len(budget_cands) == 1
        assert budget_cands[0]["priority"] == 70


async def test_insight_scan_budget_pressure_published_on_crossing(provisioned_postgres_pool):
    """bu-317s5 slice 3: a budget crossing its threshold best-effort publishes
    finance.budget_pressure as a TTL'd derived-advisory domain event, once per
    (category, window, severity) -- the same dedup identity as the owner
    notification."""
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    publish_once_mock = AsyncMock(return_value={"status": "ok", "event_id": "e1", "deliveries": []})

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)
        await _insert_budget(pool, category="groceries", amount="500.00", alert_threshold="0.9000")
        today = _today()
        safe_day = min(today.day, 28)
        tx_date = datetime(today.year, today.month, safe_day, 12, 0, 0, tzinfo=UTC)
        await _insert_transaction(
            pool,
            merchant="Whole Foods",
            amount="460.00",
            direction="debit",
            category="groceries",
            posted_at=tx_date,
        )

        with patch(
            "butlers.core_tools._domain_events.publish_domain_event_once",
            new=publish_once_mock,
        ):
            await run_insight_scan(pool)
            # A second run in the same window (condition still holds) must not
            # re-publish -- publish_domain_event_once itself is responsible
            # for the memoized dedup, so this only proves the caller passes a
            # STABLE dedup_key across runs, not a fresh one every time.
            await run_insight_scan(pool)

    assert publish_once_mock.await_count == 2
    first_call, second_call = publish_once_mock.await_args_list
    assert first_call.kwargs["event_type"] == "finance.budget_pressure"
    assert first_call.kwargs["source_butler"] == "finance"
    assert first_call.kwargs["dedup_namespace"] == "finance.budget_pressure:groceries"
    assert first_call.kwargs["dedup_key"] == second_call.kwargs["dedup_key"]
    assert first_call.kwargs["dedup_key"].startswith("finance:budget-threshold:groceries:")
    assert first_call.kwargs["dedup_key"].endswith("-exceeded")
    payload = first_call.kwargs["payload"]
    assert payload["category"] == "groceries"
    assert payload["status"] == "exceeded"
    assert payload["currency"] == "USD"
    assert "valid_until" in payload


async def test_insight_scan_budget_below_threshold_does_not_publish_pressure_event(
    provisioned_postgres_pool,
):
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    publish_once_mock = AsyncMock()

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)
        await _insert_budget(pool, category="groceries", amount="500.00")
        today = _today()
        safe_day = min(today.day, 28)
        tx_date = datetime(today.year, today.month, safe_day, 12, 0, 0, tzinfo=UTC)
        # 50% utilisation -- well below the default 80% warn_threshold.
        await _insert_transaction(
            pool,
            merchant="Whole Foods",
            amount="250.00",
            direction="debit",
            category="groceries",
            posted_at=tx_date,
        )

        with patch(
            "butlers.core_tools._domain_events.publish_domain_event_once",
            new=publish_once_mock,
        ):
            await run_insight_scan(pool)

    publish_once_mock.assert_not_awaited()


async def test_insight_scan_budget_80_to_90pct_priority_50(provisioned_postgres_pool):
    """Budget at 80–90% utilisation gets priority 50."""
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        await _insert_budget(pool, category="dining", amount="300.00")

        today = _today()
        safe_day = min(today.day, 28)
        tx_date = datetime(today.year, today.month, safe_day, 12, 0, 0, tzinfo=UTC)
        # Spend $255 (85%) this month
        await _insert_transaction(
            pool,
            merchant="Restaurant",
            amount="255.00",
            direction="debit",
            category="dining",
            posted_at=tx_date,
        )

        await run_insight_scan(pool)

        candidates = await _fetch_candidates(pool)
        budget_cands = [c for c in candidates if c["category"] == "budget-threshold"]
        assert len(budget_cands) == 1
        assert budget_cands[0]["priority"] == 50


async def test_insight_scan_budget_below_80pct_no_candidate(provisioned_postgres_pool):
    """Budget below 80% does not generate an insight candidate."""
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        await _insert_budget(pool, category="transport", amount="200.00")

        today = _today()
        safe_day = min(today.day, 28)
        tx_date = datetime(today.year, today.month, safe_day, 12, 0, 0, tzinfo=UTC)
        # Spend $100 (50%) — below threshold
        await _insert_transaction(
            pool,
            merchant="Uber",
            amount="100.00",
            direction="debit",
            category="transport",
            posted_at=tx_date,
        )

        await run_insight_scan(pool)

        candidates = await _fetch_candidates(pool)
        budget_cands = [c for c in candidates if c["category"] == "budget-threshold"]
        assert len(budget_cands) == 0


async def test_insight_scan_budget_default_thresholds_92pct_is_warning(
    provisioned_postgres_pool,
):
    """bu-rvz2o: with DEFAULT thresholds (warn=80%, alert=100%), 92% utilisation
    is 'warning' (priority 50), not 'exceeded' — the old hardcoded 90% breakpoint
    did not match the real default alert_threshold of 100%.
    """
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        await _insert_budget(pool, category="groceries", amount="500.00")

        today = _today()
        safe_day = min(today.day, 28)
        tx_date = datetime(today.year, today.month, safe_day, 12, 0, 0, tzinfo=UTC)
        await _insert_transaction(
            pool,
            merchant="Whole Foods",
            amount="460.00",
            direction="debit",
            category="groceries",
            posted_at=tx_date,
        )

        await run_insight_scan(pool)

        candidates = await _fetch_candidates(pool)
        budget_cands = [c for c in candidates if c["category"] == "budget-threshold"]
        assert len(budget_cands) == 1
        assert budget_cands[0]["priority"] == 50


async def test_insight_scan_budget_custom_warn_threshold_respected(provisioned_postgres_pool):
    """bu-rvz2o: a budget with a custom (lower) warn_threshold flags earlier
    than the 80% default — absorbing budget-status-check's per-category
    warn_threshold/alert_threshold semantics, not a hardcoded percentage.
    """
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        await _insert_budget(
            pool,
            category="entertainment",
            amount="200.00",
            warn_threshold="0.5000",
            alert_threshold="0.9000",
        )

        today = _today()
        safe_day = min(today.day, 28)
        tx_date = datetime(today.year, today.month, safe_day, 12, 0, 0, tzinfo=UTC)
        # $120 of $200 = 60% — above the custom 50% warn_threshold, but the
        # default (80%) would have missed it entirely.
        await _insert_transaction(
            pool,
            merchant="Cinema",
            amount="120.00",
            direction="debit",
            category="entertainment",
            posted_at=tx_date,
        )

        await run_insight_scan(pool)

        candidates = await _fetch_candidates(pool)
        budget_cands = [c for c in candidates if c["category"] == "budget-threshold"]
        assert len(budget_cands) == 1
        assert budget_cands[0]["priority"] == 50


async def test_insight_scan_budget_dedup_key_format(provisioned_postgres_pool):
    """Budget dedup_key = finance:budget-threshold:{category}:{year-month}-{status}.

    bu-qvs1o: severity is folded into the fourth (time-scope) segment so a
    warning and a later escalation-to-exceeded within the same window carry
    distinct keys.
    """
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        await _insert_budget(pool, category="subscriptions", amount="100.00")

        today = _today()
        safe_day = min(today.day, 28)
        tx_date = datetime(today.year, today.month, safe_day, 12, 0, 0, tzinfo=UTC)
        await _insert_transaction(
            pool,
            merchant="Netflix",
            amount="95.00",
            direction="debit",
            category="subscriptions",
            posted_at=tx_date,
        )

        await run_insight_scan(pool)

        candidates = await _fetch_candidates(pool)
        budget_cands = [c for c in candidates if c["category"] == "budget-threshold"]
        year_month = _today().strftime("%Y-%m")
        # $95 of $100 = 95% -> warning (>= 80% warn, < 100% alert).
        expected_key = f"finance:budget-threshold:subscriptions:{year_month}-warning"
        assert budget_cands[0]["dedup_key"] == expected_key


# ---------------------------------------------------------------------------
# Tests: budget-threshold across all periods + period-scoped dedup (bu-hovqz)
# ---------------------------------------------------------------------------


def _current_week_token() -> str:
    iso = _today().isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _current_quarter_token() -> str:
    t = _today()
    return f"{t.year}-Q{(t.month - 1) // 3 + 1}"


def test_budget_period_scope_token_per_period():
    """Each period yields a distinct, period-correct dedup time-scope token."""
    fj = _fj_module()
    tok = fj._budget_period_scope_token
    assert tok("daily", date(2026, 7, 6)) == "2026-07-06"
    assert tok("weekly", date(2026, 7, 6)) == "2026-W28"  # Monday of ISO week 28
    assert tok("monthly", date(2026, 7, 1)) == "2026-07"
    assert tok("quarterly", date(2026, 7, 1)) == "2026-Q3"
    assert tok("yearly", date(2026, 1, 1)) == "2026"
    # All five formats are mutually unambiguous for the same anchor, so budgets
    # of different periods for one category never share a dedup key.
    tokens = {
        tok("daily", date(2026, 7, 6)),
        tok("weekly", date(2026, 7, 6)),
        tok("monthly", date(2026, 7, 1)),
        tok("quarterly", date(2026, 7, 1)),
        tok("yearly", date(2026, 1, 1)),
    }
    assert len(tokens) == 5


def test_budget_period_scope_token_resets_across_windows():
    """The token changes at every period boundary (dedup resets across windows)."""
    fj = _fj_module()
    tok = fj._budget_period_scope_token
    assert tok("daily", date(2026, 7, 6)) != tok("daily", date(2026, 7, 7))
    assert tok("weekly", date(2026, 7, 6)) != tok("weekly", date(2026, 7, 13))
    assert tok("monthly", date(2026, 7, 1)) != tok("monthly", date(2026, 8, 1))
    # Quarter rollover Q1 -> Q2 at Apr 1.
    assert tok("quarterly", date(2026, 3, 1)) == "2026-Q1"
    assert tok("quarterly", date(2026, 4, 1)) == "2026-Q2"
    # Year rollover.
    assert tok("yearly", date(2026, 1, 1)) != tok("yearly", date(2027, 1, 1))


def test_budget_period_scope_token_iso_week_year_boundary():
    """ISO week-year is used, so year-boundary weeks stay coherent and distinct."""
    fj = _fj_module()
    tok = fj._budget_period_scope_token
    # 2020-12-28 (Mon) is ISO week 53 of ISO-year 2020; 2021-01-04 (Mon) is week 1
    # of ISO-year 2021 — distinct, no calendar-year confusion.
    assert tok("weekly", date(2020, 12, 28)) == "2020-W53"
    assert tok("weekly", date(2021, 1, 4)) == "2021-W01"


def test_budget_period_scope_token_rejects_unknown_period():
    """An unsupported period is a programming error, not a silent skip."""
    fj = _fj_module()
    with pytest.raises(ValueError):
        fj._budget_period_scope_token("biweekly", date(2026, 7, 1))


async def test_insight_scan_weekly_budget_fires_with_week_scoped_key(provisioned_postgres_pool):
    """bu-hovqz: a weekly budget over threshold fires with an ISO-week-scoped key,
    and its cooldown spans the remainder of the current week.
    """
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)
        await _insert_budget(pool, category="coffee", period="weekly", amount="50.00")

        today = _today()
        tx_date = datetime(today.year, today.month, today.day, 12, 0, 0, tzinfo=UTC)
        # $55 of a $50 weekly budget = 110% -> exceeded (>= default alert 100%).
        await _insert_transaction(
            pool,
            merchant="Cafe",
            amount="55.00",
            direction="debit",
            category="coffee",
            posted_at=tx_date,
        )

        await run_insight_scan(pool)

        cands = [c for c in await _fetch_candidates(pool) if c["category"] == "budget-threshold"]
        assert len(cands) == 1
        assert (
            cands[0]["dedup_key"]
            == f"finance:budget-threshold:coffee:{_current_week_token()}-exceeded"
        )
        assert cands[0]["priority"] == 70
        assert "weekly budget" in cands[0]["message"]
        # Cooldown spans the rest of the ISO week (Monday..Sunday).
        week_end = today + timedelta(days=7 - today.isoweekday())
        assert cands[0]["cooldown_days"] == max(1, (week_end - today).days + 1)


async def test_insight_scan_quarterly_budget_fires_with_quarter_scoped_key(
    provisioned_postgres_pool,
):
    """bu-hovqz: a quarterly budget over threshold fires with a quarter-scoped key."""
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)
        await _insert_budget(pool, category="travel", period="quarterly", amount="1000.00")

        today = _today()
        tx_date = datetime(today.year, today.month, today.day, 12, 0, 0, tzinfo=UTC)
        # $850 of $1000 = 85% -> warning (>= 80% warn, < 100% alert).
        await _insert_transaction(
            pool,
            merchant="Airline",
            amount="850.00",
            direction="debit",
            category="travel",
            posted_at=tx_date,
        )

        await run_insight_scan(pool)

        cands = [c for c in await _fetch_candidates(pool) if c["category"] == "budget-threshold"]
        assert len(cands) == 1
        assert (
            cands[0]["dedup_key"]
            == f"finance:budget-threshold:travel:{_current_quarter_token()}-warning"
        )
        assert cands[0]["priority"] == 50
        assert "quarterly budget" in cands[0]["message"]


async def test_insight_scan_yearly_budget_fires_with_year_scoped_key(provisioned_postgres_pool):
    """bu-hovqz: a yearly budget over threshold fires with a year-scoped key."""
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)
        await _insert_budget(pool, category="insurance", period="yearly", amount="1200.00")

        today = _today()
        tx_date = datetime(today.year, today.month, today.day, 12, 0, 0, tzinfo=UTC)
        # $1200 of $1200 = 100% -> exceeded.
        await _insert_transaction(
            pool,
            merchant="Insurer",
            amount="1200.00",
            direction="debit",
            category="insurance",
            posted_at=tx_date,
        )

        await run_insight_scan(pool)

        cands = [c for c in await _fetch_candidates(pool) if c["category"] == "budget-threshold"]
        assert len(cands) == 1
        assert cands[0]["dedup_key"] == f"finance:budget-threshold:insurance:{today.year}-exceeded"
        assert cands[0]["priority"] == 70
        assert "yearly budget" in cands[0]["message"]


async def test_insight_scan_monthly_and_yearly_same_category_no_collision(
    provisioned_postgres_pool,
):
    """bu-hovqz: a monthly and a yearly budget for the same category both crossing
    threshold produce two candidates with distinct, non-colliding dedup keys.
    """
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)
        await _insert_budget(pool, category="shopping", period="monthly", amount="200.00")
        await _insert_budget(pool, category="shopping", period="yearly", amount="2000.00")

        today = _today()
        tx_date = datetime(today.year, today.month, today.day, 12, 0, 0, tzinfo=UTC)
        # $1900: 950% of the monthly budget (exceeded) AND 95% of the yearly (warning).
        await _insert_transaction(
            pool,
            merchant="Mall",
            amount="1900.00",
            direction="debit",
            category="shopping",
            posted_at=tx_date,
        )

        await run_insight_scan(pool)

        cands = [c for c in await _fetch_candidates(pool) if c["category"] == "budget-threshold"]
        assert len(cands) == 2
        keys = sorted(c["dedup_key"] for c in cands)
        assert keys == sorted(
            [
                # $1900/$200 = 950% -> exceeded; $1900/$2000 = 95% -> warning.
                f"finance:budget-threshold:shopping:{today.strftime('%Y-%m')}-exceeded",
                f"finance:budget-threshold:shopping:{today.year}-warning",
            ]
        )


async def test_insight_scan_budget_dedup_key_stable_within_window(provisioned_postgres_pool):
    """bu-hovqz: repeated scans within the same period window emit the SAME dedup key,
    so the broker's dedup/cooldown collapses them (dedup holds within the window).
    """
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)
        await _insert_budget(pool, category="groceries", period="monthly", amount="500.00")

        today = _today()
        safe_day = min(today.day, 28)
        tx_date = datetime(today.year, today.month, safe_day, 12, 0, 0, tzinfo=UTC)
        await _insert_transaction(
            pool,
            merchant="Market",
            amount="480.00",
            direction="debit",
            category="groceries",
            posted_at=tx_date,
        )

        await run_insight_scan(pool)
        await run_insight_scan(pool)

        cands = [c for c in await _fetch_candidates(pool) if c["category"] == "budget-threshold"]
        assert len(cands) == 2
        assert cands[0]["dedup_key"] == cands[1]["dedup_key"]


@pytest.mark.parametrize(
    "period, budget_amount, warn_spend, exceed_spend, scope_fn",
    [
        # Short window (monthly) and a long one (yearly): under the OLD
        # severity-agnostic key the yearly exceeded state would stay unreported
        # for the rest of the year once the warning's cooldown was set.
        ("monthly", "500.00", "460.00", "80.00", lambda t: t.strftime("%Y-%m")),
        ("yearly", "1200.00", "1080.00", "200.00", lambda t: str(t.year)),
    ],
)
async def test_insight_scan_budget_warn_then_exceeded_escalates_distinct_keys(
    provisioned_postgres_pool, period, budget_amount, warn_spend, exceed_spend, scope_fn
):
    """bu-qvs1o: a budget that crosses warn, then later exceeds within the SAME
    window, yields two candidates with distinct severity-scoped dedup keys — so
    the warning's per-window cooldown does not silence the escalation to
    exceeded. Folding severity into the fourth segment is what makes the keys
    distinct; parametrized across a short (monthly) and a long (yearly) window.
    """
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)
        await _insert_budget(pool, category="dining", period=period, amount=budget_amount)

        today = _today()
        safe_day = min(today.day, 28)
        tx_date = datetime(today.year, today.month, safe_day, 12, 0, 0, tzinfo=UTC)

        # First scan: warn-level spend -> a single 'warning' candidate.
        await _insert_transaction(
            pool,
            merchant="Bistro",
            amount=warn_spend,
            direction="debit",
            category="dining",
            posted_at=tx_date,
        )
        await run_insight_scan(pool)

        # More spend on the same budget/window pushes it over the alert threshold.
        await _insert_transaction(
            pool,
            merchant="Bistro",
            amount=exceed_spend,
            direction="debit",
            category="dining",
            posted_at=tx_date,
        )
        await run_insight_scan(pool)

        cands = [c for c in await _fetch_candidates(pool) if c["category"] == "budget-threshold"]
        scope = scope_fn(today)
        warning_key = f"finance:budget-threshold:dining:{scope}-warning"
        exceeded_key = f"finance:budget-threshold:dining:{scope}-exceeded"
        by_key = {c["dedup_key"]: c for c in cands}

        # Both severities are present as distinct candidates (escalation surfaces).
        assert warning_key in by_key
        assert exceeded_key in by_key
        assert warning_key != exceeded_key
        assert by_key[warning_key]["priority"] == 50
        assert by_key[exceeded_key]["priority"] == 70


async def test_insight_scan_subscription_renewal_within_3_days_priority_75(
    provisioned_postgres_pool,
):
    """Annual subscription renewing within 3 days gets priority 75."""
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        await _insert_subscription(
            pool, service="Adobe", frequency="yearly", next_renewal=_today() + timedelta(days=2)
        )

        await run_insight_scan(pool)

        candidates = await _fetch_candidates(pool)
        sub_cands = [c for c in candidates if c["category"] == "subscription-renewal"]
        assert len(sub_cands) == 1
        assert sub_cands[0]["priority"] == 75


async def test_insight_scan_subscription_renewal_within_14_days_priority_55(
    provisioned_postgres_pool,
):
    """Annual subscription renewing in 4–14 days gets priority 55."""
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        await _insert_subscription(
            pool,
            service="1Password",
            frequency="yearly",
            next_renewal=_today() + timedelta(days=10),
        )

        await run_insight_scan(pool)

        candidates = await _fetch_candidates(pool)
        sub_cands = [c for c in candidates if c["category"] == "subscription-renewal"]
        assert len(sub_cands) == 1
        assert sub_cands[0]["priority"] == 55


async def test_insight_scan_monthly_subscription_excluded(provisioned_postgres_pool):
    """Monthly subscriptions do NOT generate insight candidates (only annual)."""
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        await _insert_subscription(
            pool,
            service="Netflix",
            frequency="monthly",
            next_renewal=_today() + timedelta(days=3),
        )

        await run_insight_scan(pool)

        candidates = await _fetch_candidates(pool)
        sub_cands = [c for c in candidates if c["category"] == "subscription-renewal"]
        assert len(sub_cands) == 0


async def test_insight_scan_subscription_beyond_14_days_excluded(provisioned_postgres_pool):
    """Annual subscriptions renewing beyond 14 days are excluded."""
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        await _insert_subscription(
            pool, service="Dropbox", frequency="yearly", next_renewal=_today() + timedelta(days=20)
        )

        await run_insight_scan(pool)

        assert await _count_candidates(pool) == 0


async def test_insight_scan_subscription_dedup_key_format(provisioned_postgres_pool):
    """Subscription insight dedup_key matches finance:subscription-renewal:{id}:{date}."""
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        renewal_date = _today() + timedelta(days=5)
        sub_id = await _insert_subscription(
            pool, service="Backblaze", frequency="yearly", next_renewal=renewal_date
        )

        await run_insight_scan(pool)

        candidates = await _fetch_candidates(pool)
        sub_cands = [c for c in candidates if c["category"] == "subscription-renewal"]
        expected_key = f"finance:subscription-renewal:{sub_id}:{renewal_date.isoformat()}"
        assert sub_cands[0]["dedup_key"] == expected_key


# ---------------------------------------------------------------------------
# Tests: run_insight_scan — renewal insight door fields (bu-8cdl1.10 slice 3)
# ---------------------------------------------------------------------------


async def test_insight_scan_renewal_with_known_door_warns_with_days_remaining(
    provisioned_postgres_pool,
):
    """A subscription with a complete cancellation door (notice period + URL +
    cancel-by) carries the door fields and days-remaining-to-act on its
    renewal insight, not just the amount."""
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        renewal_date = _today() + timedelta(days=10)
        cancel_by = _today() + timedelta(days=3)
        sub_id = await _insert_subscription(
            pool,
            service="Adobe",
            next_renewal=renewal_date,
            cancellation_url="https://adobe.com/cancel",
            notice_period_days=7,
            cancel_by=cancel_by,
            cancellation_url_source="owner",
        )

        await run_insight_scan(pool)

        cand = await pool.fetchrow(
            "SELECT message, metadata FROM insight_candidates WHERE category = 'subscription-renewal'"
        )
        assert cand["metadata"]["subscription_id"] == sub_id
        assert cand["metadata"]["unknown_door"] is False
        assert cand["metadata"]["door_verified"] is True
        assert cand["metadata"]["cancellation_url"] == "https://adobe.com/cancel"
        assert cand["metadata"]["notice_period_days"] == 7
        assert cand["metadata"]["cancel_by"] == cancel_by.isoformat()
        assert cand["metadata"]["warn_by"] == (cancel_by - timedelta(days=7)).isoformat()
        assert cand["metadata"]["days_remaining_to_act"] == 3
        assert "No cancellation door on file" not in cand["message"]
        assert "Cancel by" in cand["message"]


async def test_insight_scan_extracted_door_is_inert_and_unverified(
    provisioned_postgres_pool,
):
    """An email-derived cancellation link reaches the owner as its registrable
    domain in plain text -- never as a URL in the message or the metadata."""
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        cancel_by = _today() + timedelta(days=3)
        await _insert_subscription(
            pool,
            next_renewal=_today() + timedelta(days=10),
            cancellation_url="https://login.example-phish.test/cancel?token=abc",
            notice_period_days=7,
            cancel_by=cancel_by,
            cancellation_url_source="extracted",
            metadata={
                "expected_signal_source": {
                    "producer": "connector:gmail",
                    "producer_endpoint_identity": "gmail:user@example.test",
                }
            },
        )

        await run_insight_scan(pool)

        cand = await pool.fetchrow(
            "SELECT message, metadata FROM insight_candidates WHERE category = 'subscription-renewal'"
        )
        assert "example-phish[.]test (link from an email, not verified)" in cand["message"]
        assert "http" not in cand["message"]
        assert "/cancel" not in cand["message"]
        assert cand["metadata"]["door_verified"] is False
        assert cand["metadata"]["cancellation_url"] is None
        assert cand["metadata"]["cancellation_domain"] == "example-phish[.]test"
        assert "token=abc" not in json.dumps(cand["metadata"])


async def test_insight_scan_extracted_cancelled_still_warns_and_asks_to_confirm(
    provisioned_postgres_pool,
):
    """An email that reports a cancellation cannot silence the renewal warning."""
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        await _insert_subscription(
            pool,
            next_renewal=_today() + timedelta(days=10),
            status="cancelled",
            status_source="extracted",
            metadata={
                "expected_signal_source": {
                    "producer": "connector:gmail",
                    "producer_endpoint_identity": "gmail:user@example.test",
                }
            },
        )

        await run_insight_scan(pool)

        cand = await pool.fetchrow(
            "SELECT message, metadata FROM insight_candidates WHERE category = 'subscription-renewal'"
        )
        assert cand is not None
        assert (
            "An email reported this as cancelled; confirm the subscription status with Butlers."
            in cand["message"]
        )
        assert cand["metadata"]["reported_status"] == "cancelled"


async def test_insight_scan_extracted_cancelled_without_email_source_says_unverified(
    provisioned_postgres_pool,
):
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        await _insert_subscription(
            pool,
            next_renewal=_today() + timedelta(days=10),
            status="paused",
            status_source="extracted",
        )

        await run_insight_scan(pool)

        cand = await pool.fetchrow(
            "SELECT message FROM insight_candidates WHERE category = 'subscription-renewal'"
        )
        assert "An unverified source reported this as paused;" in cand["message"]


async def test_insight_scan_owner_set_cancelled_suppresses_the_renewal_warning(
    provisioned_postgres_pool,
):
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        await _insert_subscription(
            pool,
            next_renewal=_today() + timedelta(days=10),
            status="cancelled",
            status_source="owner",
        )

        await run_insight_scan(pool)

        count = await pool.fetchval(
            "SELECT count(*) FROM insight_candidates WHERE category = 'subscription-renewal'"
        )
        assert count == 0


async def test_insight_scan_renewal_missing_door_renders_enrichment_prompt(
    provisioned_postgres_pool,
):
    """A subscription with no cancellation door on file gets an explicit
    enrichment prompt on its renewal insight, not a silent omission of the
    door status."""
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        renewal_date = _today() + timedelta(days=10)
        await _insert_subscription(pool, service="Dropbox", next_renewal=renewal_date)

        await run_insight_scan(pool)

        cand = await pool.fetchrow(
            "SELECT message, metadata FROM insight_candidates WHERE category = 'subscription-renewal'"
        )
        assert cand["metadata"]["unknown_door"] is True
        assert cand["metadata"]["cancel_by"] is None
        assert cand["metadata"]["warn_by"] is None
        assert "No cancellation door on file" in cand["message"]
        assert "add its cancellation URL" in cand["message"]


async def test_insight_scan_renewal_with_pre_charge_price_change_flag(
    provisioned_postgres_pool,
):
    """A pre-charge price-change flag on the obligation ledger row surfaces on
    the renewal insight alongside the door fields."""
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        renewal_date = _today() + timedelta(days=10)
        cancel_by = _today() + timedelta(days=3)
        await _insert_subscription(
            pool,
            service="Adobe",
            amount="599.00",
            next_renewal=renewal_date,
            cancellation_url="https://adobe.com/cancel",
            notice_period_days=7,
            cancel_by=cancel_by,
            metadata={"next_amount": "699.00"},
        )

        await run_insight_scan(pool)

        cand = await pool.fetchrow(
            "SELECT message, metadata FROM insight_candidates WHERE category = 'subscription-renewal'"
        )
        assert cand["metadata"]["price_change_amount"] == "699.00"
        assert cand["metadata"]["price_change_direction"] == "increase"
        assert "increase to USD 699.00" in cand["message"]


async def test_insight_scan_deadline_candidates_include_event_date_metadata(
    provisioned_postgres_pool,
):
    """Bills and renewals expose their source deadline for broker correlation."""
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)
        bill_due = _today() + timedelta(days=2)
        renewal_date = _today() + timedelta(days=5)
        await _insert_bill_returning_id(pool, due_date=bill_due)
        await _insert_subscription(pool, next_renewal=renewal_date)

        await run_insight_scan(pool)

        rows = await pool.fetch(
            "SELECT category, metadata FROM insight_candidates WHERE category = ANY($1::text[])",
            ["bill-due", "subscription-renewal"],
        )
        metadata_by_category = {row["category"]: row["metadata"] for row in rows}

        assert metadata_by_category["bill-due"]["event_date"] == bill_due.isoformat()
        assert (
            metadata_by_category["subscription-renewal"]["event_date"] == renewal_date.isoformat()
        )


async def test_insight_scan_spending_anomaly_over_30pct_generates_candidate(
    provisioned_postgres_pool,
):
    """Category spending >30% above 3-month average generates an insight."""
    from butlers.jobs._roster.finance_jobs import run_insight_scan

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
        current_day = min(today.day, 28)
        current_tx_date = datetime(today.year, today.month, current_day, 12, 0, 0, tzinfo=UTC)
        await _insert_transaction(
            pool,
            merchant="Whole Foods",
            amount="220.00",
            direction="debit",
            category="groceries",
            posted_at=current_tx_date,
        )

        await run_insight_scan(pool)

        candidates = await _fetch_candidates(pool)
        anomaly_cands = [c for c in candidates if c["category"] == "spending-anomaly"]
        assert len(anomaly_cands) == 1
        # 120% above average (> 100%) → priority 80
        assert anomaly_cands[0]["priority"] == 80


async def test_insight_scan_spending_anomaly_30_50pct_priority_50(provisioned_postgres_pool):
    """Category 30–50% above average gets priority 50."""
    from butlers.jobs._roster.finance_jobs import run_insight_scan

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
        current_day = min(today.day, 28)
        current_tx_date = datetime(today.year, today.month, current_day, 12, 0, 0, tzinfo=UTC)
        await _insert_transaction(
            pool,
            merchant="Fancy Restaurant",
            amount="140.00",
            direction="debit",
            category="dining",
            posted_at=current_tx_date,
        )

        await run_insight_scan(pool)

        candidates = await _fetch_candidates(pool)
        anomaly_cands = [c for c in candidates if c["category"] == "spending-anomaly"]
        assert len(anomaly_cands) == 1
        assert anomaly_cands[0]["priority"] == 50


async def test_insight_scan_spending_anomaly_50_100pct_priority_65(provisioned_postgres_pool):
    """Category 50–100% above average gets priority 65."""
    from butlers.jobs._roster.finance_jobs import run_insight_scan

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
        current_day = min(today.day, 28)
        current_tx_date = datetime(today.year, today.month, current_day, 12, 0, 0, tzinfo=UTC)
        await _insert_transaction(
            pool,
            merchant="Cinema",
            amount="175.00",
            direction="debit",
            category="entertainment",
            posted_at=current_tx_date,
        )

        await run_insight_scan(pool)

        candidates = await _fetch_candidates(pool)
        anomaly_cands = [c for c in candidates if c["category"] == "spending-anomaly"]
        assert len(anomaly_cands) == 1
        assert anomaly_cands[0]["priority"] == 65


async def test_insight_scan_spending_anomaly_below_30pct_no_candidate(provisioned_postgres_pool):
    """Category within 30% of average does NOT generate an insight."""
    from butlers.jobs._roster.finance_jobs import run_insight_scan

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
        current_day = min(today.day, 28)
        current_tx_date = datetime(today.year, today.month, current_day, 12, 0, 0, tzinfo=UTC)
        await _insert_transaction(
            pool,
            merchant="Grocery",
            amount="120.00",
            direction="debit",
            category="groceries",
            posted_at=current_tx_date,
        )

        await run_insight_scan(pool)

        candidates = await _fetch_candidates(pool)
        anomaly_cands = [c for c in candidates if c["category"] == "spending-anomaly"]
        assert len(anomaly_cands) == 0


async def test_insight_scan_spending_anomaly_fewer_than_3_months_excluded(
    provisioned_postgres_pool,
):
    """Categories with fewer than 3 months of history are excluded from anomaly detection."""
    from butlers.jobs._roster.finance_jobs import run_insight_scan

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
        current_day = min(today.day, 28)
        current_tx_date = datetime(today.year, today.month, current_day, 12, 0, 0, tzinfo=UTC)
        await _insert_transaction(
            pool,
            merchant="NewMerchant",
            amount="500.00",
            direction="debit",
            category="newcat",
            posted_at=current_tx_date,
        )

        await run_insight_scan(pool)

        candidates = await _fetch_candidates(pool)
        anomaly_cands = [c for c in candidates if c["category"] == "spending-anomaly"]
        assert len(anomaly_cands) == 0


async def test_insight_scan_spending_anomaly_dedup_key_format(provisioned_postgres_pool):
    """Anomaly insight dedup_key matches finance:spending-anomaly:{category}:{year-month}."""
    from butlers.jobs._roster.finance_jobs import run_insight_scan

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

        current_day = min(today.day, 28)
        current_tx_date = datetime(today.year, today.month, current_day, 12, 0, 0, tzinfo=UTC)
        await _insert_transaction(
            pool,
            merchant="Shop",
            amount="300.00",
            direction="debit",
            category="shopping",
            posted_at=current_tx_date,
        )

        await run_insight_scan(pool)

        candidates = await _fetch_candidates(pool)
        anomaly_cands = [c for c in candidates if c["category"] == "spending-anomaly"]
        year_month = today.strftime("%Y-%m")
        assert anomaly_cands[0]["dedup_key"] == f"finance:spending-anomaly:shopping:{year_month}"


async def test_insight_scan_verbosity_off_early_exit(provisioned_postgres_pool):
    """When verbosity=off, the first submission is filtered and no more are submitted."""
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        # Set verbosity to 'off'
        await pool.execute("UPDATE insight_settings SET verbosity = 'off' WHERE id = 1")

        # Add two bills due within 3 days
        await _insert_bill_returning_id(pool, payee="Bill A", due_date=_today() + timedelta(days=1))
        await _insert_bill_returning_id(pool, payee="Bill B", due_date=_today() + timedelta(days=2))

        result = await run_insight_scan(pool)

        assert result["early_exit"] is True
        assert result["filtered"] >= 1
        # Only first candidate should have been submitted before early exit
        assert result["submitted"] == 1


async def test_insight_scan_result_has_expected_keys(provisioned_postgres_pool):
    """Result dict contains all expected keys."""
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        result = await run_insight_scan(pool)

        assert "submitted" in result
        assert "accepted" in result
        assert "filtered" in result
        assert "errors" in result
        assert "early_exit" in result


async def test_insight_scan_multiple_categories_all_submitted(provisioned_postgres_pool):
    """Multiple categories (bill + subscription) each get a candidate submitted."""
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        # A bill due tomorrow
        await _insert_bill_returning_id(pool, payee="Rent", due_date=_today() + timedelta(days=1))
        # An annual subscription renewing in 5 days
        await _insert_subscription(
            pool, service="Adobe", frequency="yearly", next_renewal=_today() + timedelta(days=5)
        )

        result = await run_insight_scan(pool)

        assert result["submitted"] == 2
        assert result["accepted"] == 2
        assert result["early_exit"] is False


# ---------------------------------------------------------------------------
# Tests: run_insight_scan — subscription price-change (bu-rvz2o)
# ---------------------------------------------------------------------------


async def test_insight_scan_price_change_large_increase_priority_75(provisioned_postgres_pool):
    """A >=20% price increase on a tracked subscription generates priority 75."""
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        await _insert_subscription(pool, service="Spotify", amount="10.00", frequency="monthly")
        await _insert_transaction(
            pool,
            merchant="Spotify Premium",
            amount="15.00",
            direction="debit",
            category="subscriptions",
            posted_at=_utcnow(),
        )

        await run_insight_scan(pool)

        candidates = await _fetch_candidates(pool)
        price_cands = [c for c in candidates if c["category"] == "subscription-price-change"]
        assert len(price_cands) == 1
        assert price_cands[0]["priority"] == 75


async def test_insight_scan_price_change_mid_increase_priority_60(provisioned_postgres_pool):
    """A 10-20% price change generates priority 60."""
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        await _insert_subscription(pool, service="Hulu", amount="10.00", frequency="monthly")
        await _insert_transaction(
            pool,
            merchant="Hulu",
            amount="11.50",
            direction="debit",
            category="subscriptions",
            posted_at=_utcnow(),
        )

        await run_insight_scan(pool)

        candidates = await _fetch_candidates(pool)
        price_cands = [c for c in candidates if c["category"] == "subscription-price-change"]
        assert len(price_cands) == 1
        assert price_cands[0]["priority"] == 60


async def test_insight_scan_price_change_below_5pct_no_candidate(provisioned_postgres_pool):
    """A <=5% change is below detect_price_changes' own floor — no candidate."""
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        await _insert_subscription(pool, service="Disney Plus", amount="10.00", frequency="monthly")
        await _insert_transaction(
            pool,
            merchant="Disney Plus",
            amount="10.20",
            direction="debit",
            category="subscriptions",
            posted_at=_utcnow(),
        )

        await run_insight_scan(pool)

        candidates = await _fetch_candidates(pool)
        price_cands = [c for c in candidates if c["category"] == "subscription-price-change"]
        assert len(price_cands) == 0


async def test_insight_scan_price_change_no_matching_transaction_no_candidate(
    provisioned_postgres_pool,
):
    """A subscription with no matching charge in the lookback window is a no-op."""
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        await _insert_subscription(pool, service="Paramount Plus", amount="10.00")

        await run_insight_scan(pool)

        candidates = await _fetch_candidates(pool)
        price_cands = [c for c in candidates if c["category"] == "subscription-price-change"]
        assert len(price_cands) == 0


async def test_insight_scan_price_change_dedup_key_format(provisioned_postgres_pool):
    """Price-change dedup_key matches finance:subscription-price-change:{slug}:{year-month}."""
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        await _insert_subscription(pool, service="YouTube Premium", amount="12.00")
        await _insert_transaction(
            pool,
            merchant="YouTube Premium",
            amount="18.00",
            direction="debit",
            category="subscriptions",
            posted_at=_utcnow(),
        )

        await run_insight_scan(pool)

        candidates = await _fetch_candidates(pool)
        price_cands = [c for c in candidates if c["category"] == "subscription-price-change"]
        year_month = _today().strftime("%Y-%m")
        assert (
            price_cands[0]["dedup_key"]
            == f"finance:subscription-price-change:youtube-premium:{year_month}"
        )


# ---------------------------------------------------------------------------
# Tests: run_bill_reconciliation_sweep (bu-rvz2o)
# ---------------------------------------------------------------------------


def _fj_module():
    import sys

    return sys.modules["butlers.jobs._roster.finance_jobs"]


async def test_bill_reconciliation_sweep_empty_no_candidates(
    provisioned_postgres_pool, monkeypatch
):
    """No auto-settled/candidate/predicted results -> no insight candidates."""
    fj = _fj_module()

    async def _fake_reconcile(conn, lookback_days=90):
        return {"auto_settled": [], "candidates": []}

    async def _fake_predict(conn, days_ahead=30):
        return {"predictions": []}

    monkeypatch.setattr(fj, "reconcile_bills", _fake_reconcile)
    monkeypatch.setattr(fj, "predict_bills", _fake_predict)

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        result = await fj.run_bill_reconciliation_sweep(pool)

        assert result["auto_settled_count"] == 0
        assert result["confirm_candidates_count"] == 0
        assert result["predicted_count"] == 0
        assert await _count_candidates(pool) == 0


async def test_bill_reconciliation_sweep_auto_settled_generates_candidate(
    provisioned_postgres_pool, monkeypatch
):
    """Auto-settled bills generate one low-priority informational candidate."""
    fj = _fj_module()

    async def _fake_reconcile(conn, lookback_days=90):
        return {
            "auto_settled": [
                {"bill_id": "b1", "payee": "Electric Co", "amount": "50.00", "txn_id": "t1"},
            ],
            "candidates": [],
        }

    async def _fake_predict(conn, days_ahead=30):
        return {"predictions": []}

    monkeypatch.setattr(fj, "reconcile_bills", _fake_reconcile)
    monkeypatch.setattr(fj, "predict_bills", _fake_predict)

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        result = await fj.run_bill_reconciliation_sweep(pool)

        assert result["auto_settled_count"] == 1
        candidates = await _fetch_candidates(pool)
        settled_cands = [c for c in candidates if c["category"] == "bill-reconciled"]
        assert len(settled_cands) == 1
        assert settled_cands[0]["priority"] == 35


async def test_bill_reconciliation_sweep_confirm_candidate_priority_55(
    provisioned_postgres_pool, monkeypatch
):
    """Ambiguous matches needing confirmation generate an actionable candidate."""
    fj = _fj_module()

    async def _fake_reconcile(conn, lookback_days=90):
        return {
            "auto_settled": [],
            "candidates": [
                {"bill_id": "b2", "payee": "Internet Co", "due_date": _today(), "amount": "80.00"},
            ],
        }

    async def _fake_predict(conn, days_ahead=30):
        return {"predictions": []}

    monkeypatch.setattr(fj, "reconcile_bills", _fake_reconcile)
    monkeypatch.setattr(fj, "predict_bills", _fake_predict)

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        result = await fj.run_bill_reconciliation_sweep(pool)

        assert result["confirm_candidates_count"] == 1
        candidates = await _fetch_candidates(pool)
        confirm_cands = [c for c in candidates if c["category"] == "bill-reconcile-candidate"]
        assert len(confirm_cands) == 1
        assert confirm_cands[0]["priority"] == 55


async def test_bill_reconciliation_sweep_predicted_untracked_only(
    provisioned_postgres_pool, monkeypatch
):
    """Only untracked (is_tracked=False) predictions become bill-predicted candidates."""
    fj = _fj_module()

    async def _fake_reconcile(conn, lookback_days=90):
        return {"auto_settled": [], "candidates": []}

    async def _fake_predict(conn, days_ahead=30):
        return {
            "predictions": [
                {"payee": "Gym Membership", "is_tracked": False},
                {"payee": "Already Tracked Bill", "is_tracked": True},
            ]
        }

    monkeypatch.setattr(fj, "reconcile_bills", _fake_reconcile)
    monkeypatch.setattr(fj, "predict_bills", _fake_predict)

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        result = await fj.run_bill_reconciliation_sweep(pool)

        assert result["predicted_count"] == 1
        candidates = await _fetch_candidates(pool)
        predicted_cands = [c for c in candidates if c["category"] == "bill-predicted"]
        assert len(predicted_cands) == 1
        assert predicted_cands[0]["priority"] == 30
        assert "Gym Membership" in predicted_cands[0]["message"]
        assert "Already Tracked Bill" not in predicted_cands[0]["message"]


# ---------------------------------------------------------------------------
# Tests: run_anomaly_insight_scan (bu-rvz2o)
# ---------------------------------------------------------------------------


async def test_anomaly_insight_scan_insufficient_data_no_candidates(
    provisioned_postgres_pool, monkeypatch
):
    """status='insufficient_data' is a clean no-op, not an error."""
    fj = _fj_module()

    async def _fake_anomaly_scan(conn, days_back=1, sensitivity="medium"):
        return {"anomalies": [], "total_flagged": 0, "status": "insufficient_data"}

    monkeypatch.setattr(fj, "anomaly_scan", _fake_anomaly_scan)

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        result = await fj.run_anomaly_insight_scan(pool)

        assert result["status"] == "insufficient_data"
        assert result["anomalies_found"] == 0
        assert await _count_candidates(pool) == 0


async def test_anomaly_insight_scan_severity_priority_mapping(
    provisioned_postgres_pool, monkeypatch
):
    """Each anomaly's severity maps to its own priority: high=75, medium=55, low=35."""
    fj = _fj_module()

    async def _fake_anomaly_scan(conn, days_back=1, sensitivity="medium"):
        return {
            "status": "ok",
            "anomalies": [
                {
                    "transaction_id": "t1",
                    "merchant": "Big Store",
                    "amount": "500.00",
                    "type": "amount_anomaly",
                    "severity": "high",
                    "explanation": "way above baseline",
                },
                {
                    "transaction_id": "t2",
                    "merchant": "Mid Store",
                    "amount": "80.00",
                    "type": "amount_anomaly",
                    "severity": "medium",
                    "explanation": "somewhat above baseline",
                },
                {
                    "transaction_id": "t3",
                    "merchant": "New Cafe",
                    "amount": "12.00",
                    "type": "new_merchant",
                    "severity": "low",
                    "explanation": "first time seeing this merchant",
                },
            ],
        }

    monkeypatch.setattr(fj, "anomaly_scan", _fake_anomaly_scan)

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        result = await fj.run_anomaly_insight_scan(pool)

        assert result["anomalies_found"] == 3
        candidates = await _fetch_candidates(pool)
        anomaly_cands = {
            c["dedup_key"]: c["priority"]
            for c in candidates
            if c["category"] == "spending-anomaly-transaction"
        }
        assert len(anomaly_cands) == 3
        priorities = sorted(anomaly_cands.values(), reverse=True)
        assert priorities == [75, 55, 35]


async def test_anomaly_insight_scan_caps_at_max_per_run(provisioned_postgres_pool, monkeypatch):
    """More than _MAX_ANOMALY_CANDIDATES_PER_RUN anomalies are truncated, not dropped silently."""
    fj = _fj_module()

    anomalies = [
        {
            "transaction_id": f"t{i}",
            "merchant": f"Store {i}",
            "amount": "10.00",
            "type": "amount_anomaly",
            "severity": "low",
            "explanation": "minor",
        }
        for i in range(fj._MAX_ANOMALY_CANDIDATES_PER_RUN + 5)
    ]

    async def _fake_anomaly_scan(conn, days_back=1, sensitivity="medium"):
        return {"status": "ok", "anomalies": anomalies}

    monkeypatch.setattr(fj, "anomaly_scan", _fake_anomaly_scan)

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        result = await fj.run_anomaly_insight_scan(pool)

        assert result["anomalies_found"] == len(anomalies)
        assert result["truncated"] == 5
        assert await _count_candidates(pool) == fj._MAX_ANOMALY_CANDIDATES_PER_RUN


# ---------------------------------------------------------------------------
# Tests: run_monthly_finance_digest (bu-rvz2o)
# ---------------------------------------------------------------------------


async def test_monthly_finance_digest_proposes_one_candidate(
    provisioned_postgres_pool, monkeypatch
):
    """One consolidated monthly-finance-digest candidate is always proposed."""
    fj = _fj_module()

    async def _fake_budget_status(conn):
        return {"items": [], "count": 0}

    async def _fake_subscription_audit(conn):
        return {"entries": [], "total_annual_cost": "0", "changes_since_last_audit": []}

    monkeypatch.setattr(fj, "budget_status", _fake_budget_status)
    monkeypatch.setattr(fj, "subscription_audit", _fake_subscription_audit)

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        result = await fj.run_monthly_finance_digest(pool)

        assert result["status"] == "accepted"
        candidates = await _fetch_candidates(pool)
        digest_cands = [c for c in candidates if c["category"] == "monthly-finance-digest"]
        assert len(digest_cands) == 1
        assert digest_cands[0]["priority"] == 55
        assert digest_cands[0]["dedup_key"] == f"finance:monthly-digest:{result['period']}"


async def test_monthly_finance_digest_includes_flagged_budgets_and_subscriptions(
    provisioned_postgres_pool, monkeypatch
):
    """The digest message surfaces flagged budget categories and subscription counts."""
    fj = _fj_module()
    now = datetime(2026, 7, 6, 12, tzinfo=UTC)
    actual_budget_status = fj.budget_status

    async def _pinned_budget_status(conn):
        return await actual_budget_status(conn, now=now)

    async def _fake_subscription_audit(conn):
        return {
            "entries": [
                {"service": "Netflix", "status": "tracked_active"},
                {"service": "Unknown Merchant", "status": "detected_untracked"},
            ],
            "total_annual_cost": "150.00",
            "currency": "USD",
            "by_currency": [{"currency": "USD", "total_annual_cost": "150.00"}],
            "changes_since_last_audit": [],
        }

    monkeypatch.setattr(fj, "budget_status", _pinned_budget_status)
    monkeypatch.setattr(fj, "subscription_audit", _fake_subscription_audit)

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)
        # The digest consumes real daily status instead of a canned result.
        await _insert_budget(pool, category="dining", period="daily", amount="100.00")
        await _insert_budget(pool, category="groceries", amount="100.00")
        await _insert_transaction(pool, category="dining", amount="105.00", posted_at=now)
        await _insert_transaction(pool, category="groceries", amount="40.00", posted_at=now)

        await fj.run_monthly_finance_digest(pool)

        candidates = await _fetch_candidates(pool)
        digest_cands = [c for c in candidates if c["category"] == "monthly-finance-digest"]
        assert len(digest_cands) == 1
        message = digest_cands[0]["message"]
        assert "dining exceeded" in message
        assert "1 active" in message
        assert "150.00" in message
        assert "1 untracked pattern" in message


# ---------------------------------------------------------------------------
# Tests: run_monthly_finance_digest month-over-month trend content (bu-7hogl)
# ---------------------------------------------------------------------------


def _digest_month_bounds() -> tuple[datetime, datetime]:
    """Return (mid-of-prior-month, mid-of-covered-month) datetimes for inserts.

    The digest covers the calendar month before ``datetime.now(UTC).date()`` and
    compares it against the month before that. Day 15 is valid in every month,
    so it is a safe posting day for both.
    """
    today = datetime.now(UTC).date()
    first_of_this_month = today.replace(day=1)
    covered_start = (first_of_this_month - timedelta(days=1)).replace(day=1)
    prior_start = (covered_start - timedelta(days=1)).replace(day=1)
    prior_mid = datetime(prior_start.year, prior_start.month, 15, tzinfo=UTC)
    covered_mid = datetime(covered_start.year, covered_start.month, 15, tzinfo=UTC)
    return prior_mid, covered_mid


async def test_monthly_finance_digest_includes_trend_when_prior_month_data_exists(
    provisioned_postgres_pool, monkeypatch
):
    """The digest surfaces month-over-month swings, new, and disappeared categories."""
    fj = _fj_module()

    async def _fake_budget_status(conn):
        return {"items": [], "count": 0}

    async def _fake_subscription_audit(conn):
        return {"entries": [], "total_annual_cost": "0", "changes_since_last_audit": []}

    monkeypatch.setattr(fj, "budget_status", _fake_budget_status)
    monkeypatch.setattr(fj, "subscription_audit", _fake_subscription_audit)

    prior_mid, covered_mid = _digest_month_bounds()

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        # Prior month: dining $100, travel $100 (travel disappears next month).
        await _insert_transaction(
            pool, merchant="Bistro", amount="100.00", category="dining", posted_at=prior_mid
        )
        await _insert_transaction(
            pool, merchant="Airline", amount="100.00", category="travel", posted_at=prior_mid
        )
        # Covered month: dining $200 (+100% swing), coffee $50 (new). Travel absent.
        await _insert_transaction(
            pool, merchant="Bistro", amount="200.00", category="dining", posted_at=covered_mid
        )
        await _insert_transaction(
            pool, merchant="Cafe", amount="50.00", category="coffee", posted_at=covered_mid
        )

        await fj.run_monthly_finance_digest(pool)

        candidates = await _fetch_candidates(pool)
        digest_cands = [c for c in candidates if c["category"] == "monthly-finance-digest"]
        assert len(digest_cands) == 1
        message = digest_cands[0]["message"]
        # Total spend rose $200 -> $250 = +25%.
        assert "Month-over-month: total spend up 25% vs" in message
        assert "notable changes:" in message
        assert "dining +100%" in message
        assert "coffee (new)" in message
        assert "travel (no spend)" in message


async def test_monthly_finance_digest_omits_trend_when_no_prior_month_data(
    provisioned_postgres_pool, monkeypatch
):
    """With no prior-month spend, the digest still sends but omits the trend bullet."""
    fj = _fj_module()

    async def _fake_budget_status(conn):
        return {"items": [], "count": 0}

    async def _fake_subscription_audit(conn):
        return {"entries": [], "total_annual_cost": "0", "changes_since_last_audit": []}

    monkeypatch.setattr(fj, "budget_status", _fake_budget_status)
    monkeypatch.setattr(fj, "subscription_audit", _fake_subscription_audit)

    _prior_mid, covered_mid = _digest_month_bounds()

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        # Only covered-month spend; the month before it has no transactions.
        await _insert_transaction(
            pool, merchant="Bistro", amount="80.00", category="dining", posted_at=covered_mid
        )

        result = await fj.run_monthly_finance_digest(pool)

        assert result["status"] == "accepted"
        candidates = await _fetch_candidates(pool)
        digest_cands = [c for c in candidates if c["category"] == "monthly-finance-digest"]
        assert len(digest_cands) == 1
        message = digest_cands[0]["message"]
        assert "Month-over-month" not in message
        assert "notable changes" not in message


async def test_monthly_finance_digest_degrades_gracefully_on_trend_failure(
    provisioned_postgres_pool, monkeypatch
):
    """A trend-computation error never blocks the digest — it just drops the bullet."""
    fj = _fj_module()

    async def _fake_budget_status(conn):
        return {"items": [], "count": 0}

    async def _fake_subscription_audit(conn):
        return {"entries": [], "total_annual_cost": "0", "changes_since_last_audit": []}

    async def _boom(*args, **kwargs):
        raise RuntimeError("trend query blew up")

    monkeypatch.setattr(fj, "budget_status", _fake_budget_status)
    monkeypatch.setattr(fj, "subscription_audit", _fake_subscription_audit)
    monkeypatch.setattr(fj, "_month_over_month_trend", _boom)

    prior_mid, covered_mid = _digest_month_bounds()

    async with provisioned_postgres_pool() as pool:
        await _setup_insight_schema(pool)

        await _insert_transaction(
            pool, merchant="Bistro", amount="100.00", category="dining", posted_at=prior_mid
        )
        await _insert_transaction(
            pool, merchant="Bistro", amount="200.00", category="dining", posted_at=covered_mid
        )

        result = await fj.run_monthly_finance_digest(pool)

        assert result["status"] == "accepted"
        candidates = await _fetch_candidates(pool)
        digest_cands = [c for c in candidates if c["category"] == "monthly-finance-digest"]
        assert len(digest_cands) == 1
        message = digest_cands[0]["message"]
        assert "Month-over-month" not in message


@pytest.fixture
def field_scan_db(postgres_container):
    from butlers.testing.migration import create_migrated_test_db, migration_db_name

    # Full real core/Finance shape before this test enters an async loop.
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "finance"],
        schemas={"finance": "finance"},
    )


@pytest.mark.parametrize(
    "baseline_days,claim_days,selected_days",
    [(40, [3], 3), (8, [3], 3), (3, [8], 3), (40, [-1, 4], 4)],
)
async def test_scan_filters_each_claim_before_selecting_date_and_binds_ledger_premise_expiry(
    field_scan_db, baseline_days, claim_days, selected_days
):
    import asyncpg

    from butlers.db import register_jsonb_codec
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with asyncpg.create_pool(
        field_scan_db,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
        server_settings={"search_path": "finance,public"},
    ) as pool:
        await pool.execute(
            "INSERT INTO insight_settings(id,verbosity) VALUES(1,'normal') "
            "ON CONFLICT(id) DO UPDATE SET verbosity='normal'"
        )
        await _insert_subscription(
            pool,
            service="Synthetic claims",
            frequency="yearly",
            next_renewal=_today() + timedelta(days=baseline_days),
        )
        row = await pool.fetchrow(
            "SELECT id FROM finance.subscriptions WHERE service='Synthetic claims'"
        )
        await pool.execute(
            "UPDATE finance.subscriptions SET field_claims=$2::jsonb WHERE id=$1",
            row["id"],
            [
                {
                    "field": "renewal",
                    "value": (_today() + timedelta(days=d)).isoformat(),
                    "resolved": False,
                    "epoch": 0,
                }
                for d in claim_days
            ],
        )
        await run_insight_scan(pool)
        candidates = [
            c
            for c in await pool.fetch("SELECT * FROM insight_candidates")
            if c["category"] == "subscription-renewal"
        ]
        assert len(candidates) == 1
        selected = _today() + timedelta(days=selected_days)
        candidate = candidates[0]
        assert candidate["dedup_key"].endswith(selected.isoformat())
        metadata = candidate["metadata"]
        assert metadata["event_date"] == selected.isoformat()
        assert candidate["expires_at"] == datetime.combine(
            selected + timedelta(days=1), datetime.min.time(), tzinfo=UTC
        )
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM finance.obligation_ledger WHERE subscription_id=$1 AND period=$2",
                row["id"],
                selected,
            )
            == 1
        )
        premise = candidate["premise"]
        if isinstance(premise, str):
            premise = json.loads(premise)
        assert premise["args"] == {
            "subject": str(row["id"]),
            "selected_date": selected.isoformat(),
            "epoch": 0,
        }


@pytest.fixture
def registered_field_pipeline_db(postgres_container):
    from butlers.testing.migration import create_migrated_test_db, migration_db_name

    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "finance", "switchboard"],
        schemas={"core": "finance", "finance": "finance", "switchboard": "switchboard"},
    )


async def _settle_pipeline_sse_watchers(baseline_tasks):
    """Join only this fixture's idle native SSE process-lifetime watcher.

    The in-process server has no Uvicorn shutdown signal. Native SSE starts
    a per-loop watcher outside its session task group, so the fixture must
    settle that exact new task after its clients and server have exited.
    """
    import asyncio

    import sse_starlette.sse as sse

    watchers = [
        task
        for task in asyncio.all_tasks() - baseline_tasks
        if not task.done()
        and getattr(task.get_coro(), "cr_code", None) is sse._shutdown_watcher.__code__
    ]
    if not watchers:
        return
    state = sse._get_shutdown_state()
    assert not state.events, "SSE streams remain active after pipeline server settlement"
    assert len(watchers) == 1, "Expected the native per-loop SSE shutdown watcher"
    watcher = watchers[0]
    watcher.cancel()
    async with asyncio.timeout(5):
        await asyncio.gather(watcher, return_exceptions=True)
    assert watcher.done()
    assert not state.watcher_started


@pytest.mark.parametrize("after_scan", ["current", "owner-confirmed", "unknown"])
async def test_registered_routed_fields_scan_and_durable_broker_pipeline(
    registered_field_pipeline_db, monkeypatch, after_scan
):
    """Real registered write + roles + migrated scan + stored broker/currentness consumer.

    Only the external HTTP carrier is in-process and notify is a no-send sink.
    No provenance attestor, owning writer, scan SQL, proposal or broker is patched.
    Unrelated fleet publications use the existing closed catalog fixture; the
    Finance publication is the actual registered Finance module catalog.
    """
    import asyncio
    import importlib
    from contextlib import AsyncExitStack
    from types import SimpleNamespace
    from uuid import UUID, uuid4

    import asyncpg
    import httpx
    from fastmcp import Client, FastMCP
    from fastmcp.client.transports import StreamableHttpTransport
    from starlette.applications import Starlette

    from butlers.config import load_config
    from butlers.core import fact_authority
    from butlers.core import finance_warning_control as control
    from butlers.core import tool_call_capture as capture
    from butlers.core.catalog_publication import CatalogPublisher
    from butlers.core.catalog_reader import DOMAINS
    from butlers.core.mcp_urls import runtime_mcp_url
    from butlers.daemon import ButlerDaemon
    from butlers.db import register_jsonb_codec
    from butlers.jobs._roster.finance_jobs import run_insight_scan
    from butlers.modules.registry import default_registry
    from butlers.tools.switchboard.insight import broker
    from butlers.tools.switchboard.registry.registry import (
        expected_route_target,
        record_probe,
        register_boot,
        register_butler,
        reserve_probe,
    )
    from tests.contracts.test_catalog_publication import registered_catalog

    route = importlib.import_module("butlers.tools.switchboard.routing.route")
    from pathlib import Path

    root = Path(__file__).parents[3]
    url = registered_field_pipeline_db
    baseline_tasks = set(asyncio.all_tasks())
    owner = receiver = None
    actual_http = httpx.AsyncClient
    # live-clock: the actual fixed MCP reader derives its calendar internally without a now input.
    now = datetime.now(UTC)
    today = now.date()
    selected = today + timedelta(days=3)
    baseline_date = today + timedelta(days=40)
    async with AsyncExitStack() as stack:
        # Registered first, settled last: no client/session or native pool may
        # still depend on the watcher when the fixture joins it.
        stack.push_async_callback(_settle_pipeline_sse_watchers, baseline_tasks)
        admin = await stack.enter_async_context(
            asyncpg.create_pool(
                url,
                min_size=1,
                max_size=3,
                init=register_jsonb_codec,
                server_settings={"search_path": "finance,public"},
            )
        )

        boot_fence_state = {"armed": False, "owning_reads": 0, "rotated": False}

        async def runtime(domain):
            async def initialize(conn):
                await register_jsonb_codec(conn)
                if domain == "finance":

                    def record(query):
                        if boot_fence_state["armed"] and query.query.startswith(
                            "SELECT next_renewal, date_epoch, field_claims, status, status_source "
                            "FROM subscriptions WHERE id = $1"
                        ):
                            boot_fence_state["owning_reads"] += 1

                    conn.add_query_logger(record)

            async def role(conn):
                await conn.execute(f'SET ROLE "butler_{domain}_rw"')

            return await stack.enter_async_context(
                asyncpg.create_pool(
                    url,
                    min_size=1,
                    max_size=3,
                    init=initialize,
                    setup=role,
                    server_settings={"search_path": f"{domain},public"},
                )
            )

        finance_pool, switchboard_pool = await runtime("finance"), await runtime("switchboard")
        async with finance_pool.acquire() as conn:
            assert await conn.fetchval("SELECT current_user") == "butler_finance_rw"
        # Explicit privileged fixture configuration; no runtime grant or authority bypass.
        await admin.execute(
            "ALTER TABLE public.capability_catalog_control DISABLE ROW LEVEL SECURITY"
        )
        try:
            await admin.execute("UPDATE public.capability_catalog_control SET enabled=true")
        finally:
            await admin.execute(
                "ALTER TABLE public.capability_catalog_control ENABLE ROW LEVEL SECURITY"
            )
        await admin.execute(
            "UPDATE public.approvals_policy SET quiet_start_hour=NULL, quiet_end_hour=NULL, "
            "timezone='UTC' WHERE id=1"
        )
        await admin.execute(
            "INSERT INTO public.insight_settings(id,verbosity) VALUES(1,'normal') "
            "ON CONFLICT(id) DO UPDATE SET verbosity='normal'"
        )
        entity = await admin.fetchval(
            "INSERT INTO public.entities(canonical_name,entity_type,roles) "
            "VALUES('Synthetic pipeline owner','person',ARRAY['owner']) RETURNING id"
        )
        finance = ButlerDaemon(root / "roster/finance", db=SimpleNamespace(pool=finance_pool))
        finance.config = load_config(finance.config_dir)
        finance.mcp = FastMCP("registered-finance-pipeline")
        finance._modules = [default_registry()._modules["finance"]()]
        finance._module_configs = finance._validate_module_configs()
        await finance._register_module_tools()
        await finance._finalize_tool_catalog()
        publishers = {}
        catalogs = {}
        for domain in DOMAINS:
            runtime_pool = (
                finance_pool
                if domain == "finance"
                else (switchboard_pool if domain == "switchboard" else await runtime(domain))
            )
            catalog = (
                finance._tool_catalog
                if domain == "finance"
                else (await registered_catalog(domain))[1]
            )
            publisher = CatalogPublisher(runtime_pool, domain)
            await publisher.publish(catalog)
            publishers[domain], catalogs[domain] = publisher, catalog
        finance._catalog_publication = publishers["finance"]
        source = ButlerDaemon(
            root / "roster/switchboard", db=SimpleNamespace(pool=switchboard_pool)
        )
        source.config = load_config(source.config_dir)
        source._tool_catalog = catalogs["switchboard"]
        source._catalog_publication = publishers["switchboard"]
        expected = expected_route_target("finance")
        assert expected is not None
        # The real registry row must exist before the role-bound boot registration.
        await register_butler(
            switchboard_pool, "finance", runtime_mcp_url(expected.port), modules=["finance"]
        )
        switchboard_target = expected_route_target("switchboard")
        assert switchboard_target is not None
        await register_butler(
            switchboard_pool,
            "switchboard",
            runtime_mcp_url(switchboard_target.port),
            modules=["switchboard"],
        )
        finance._boot_epoch = await register_boot(
            finance_pool, "finance", finance._boot_instance_id
        )
        assert finance._boot_epoch == 1
        source._boot_epoch = await register_boot(
            switchboard_pool, "switchboard", source._boot_instance_id
        )
        assert finance._boot_instance_id.version == source._boot_instance_id.version == 7
        assert finance._boot_instance_id != publishers["finance"].instance
        assert source._boot_instance_id != publishers["switchboard"].instance
        owner = control.FinanceWarningControlOwner(source, route_call=route.route)
        source._finance_warning_control_owner = owner
        receiver = control.FinanceWarningControlOwner(
            finance, verifier_url="http://controlled.invalid" + control.VERIFY_PATH
        )
        finance._finance_warning_control_owner = receiver
        epoch, sequence = await reserve_probe(switchboard_pool, "finance")
        assert epoch == finance._boot_epoch
        assert await record_probe(
            switchboard_pool,
            "finance",
            boot_epoch=epoch,
            probe_sequence=sequence,
            healthy=True,
            compatible=True,
            accepting=True,
        )
        app = ButlerDaemon._build_mcp_http_app(
            finance.mcp, butler_name="finance", finance_warning_control_owner=receiver
        )
        raw = app._app._app
        await stack.enter_async_context(raw.router.lifespan_context(raw))

        def http_client(**kw):
            return actual_http(transport=httpx.ASGITransport(app=app), **kw)

        def routing_client(transport, **kw):
            headers = getattr(transport, "headers", {})
            return Client(
                StreamableHttpTransport(
                    runtime_mcp_url(expected.port),
                    headers=headers,
                    httpx_client_factory=http_client,
                ),
                **kw,
            )

        monkeypatch.setattr(route, "MCPClient", routing_client)
        verifier_app = Starlette(routes=[owner.route()])
        monkeypatch.setattr(
            control.httpx,
            "AsyncClient",
            lambda **kw: actual_http(transport=httpx.ASGITransport(app=verifier_app), **kw),
        )

        async def routed_write(*, explicit_owner, renewal, amount, currency):
            session = str(uuid4())
            context = (
                {"source_entity_id": str(entity), "request_context": {}}
                if explicit_owner
                else {
                    "request_context": {
                        "source_channel": "email",
                        "source_endpoint_identity": "gmail:synthetic",
                    }
                }
            )
            capture.set_runtime_session_routing_context(session, context)
            invocation = await fact_authority.register_invocation(
                "finance", session, source_endpoint=None, routed=True
            )
            try:
                # The actual HTTP middleware resolves this server-issued binding;
                # a query/session locator or caller metadata is never substituted.
                async def wire(endpoint, name, args):
                    async with Client(
                        StreamableHttpTransport(
                            endpoint,
                            headers={fact_authority.INVOCATION_HEADER: invocation},
                            httpx_client_factory=http_client,
                        )
                    ) as peer:
                        return (await peer.call_tool(name, args)).data

                result = await route.route(
                    switchboard_pool,
                    "finance",
                    "track_subscription",
                    {
                        "service": "Synthetic wire subscription",
                        "amount": amount,
                        "currency": currency,
                        "frequency": "yearly",
                        "next_renewal": renewal.isoformat(),
                    },
                    call_fn=wire,
                )
                assert "error" not in result
                return result["result"]
            finally:
                fact_authority.settle_invocation(invocation)
                capture.clear_runtime_session_routing_context(session)

        try:
            initial = await routed_write(
                explicit_owner=True, renewal=baseline_date, amount=10, currency="USD"
            )
            # A real earlier expired extraction must not mask a later in-window date.
            await routed_write(
                explicit_owner=False, renewal=today - timedelta(days=1), amount=99, currency="EUR"
            )
            extracted = await routed_write(
                explicit_owner=False, renewal=selected, amount=99, currency="EUR"
            )
            await routed_write(explicit_owner=False, renewal=selected, amount=99, currency="EUR")
            assert extracted["id"] == initial["id"]
            subject = UUID(extracted["id"])
            # Independent acquisition observes committed owning fields, never a return DTO oracle.
            async with admin.acquire() as observer:
                row = await observer.fetchrow(
                    "SELECT * FROM finance.subscriptions WHERE id=$1", subject
                )
                assert row["value_revision"] >= 3 and len(row["field_claims"]) == 3
                try:
                    assert row["next_renewal"] == baseline_date
                    assert str(row["amount"]) == "10.00" and row["currency"] == "USD"
                except AssertionError as exc:
                    if _pipeline_mutation.get() == "field-protection":
                        raise _FieldProtectionViolation from exc
                    raise
                assert row["renewal_source"] == row["price_source"] == "owner"
                assert {
                    claim["field"] for claim in row["field_claims"] if not claim["resolved"]
                } == {"renewal", "price"}
                assert (
                    len(row["field_claims"]) == 3
                )  # two dates + one complete price; replay dedups
            scan = await run_insight_scan(finance_pool, now=now)
            assert scan["errors"] == 0
            async with admin.acquire() as observer:
                candidate = await observer.fetchrow(
                    "SELECT * FROM public.insight_candidates WHERE category='subscription-renewal'"
                )
                try:
                    assert candidate is not None and candidate["status"] == "pending"
                except AssertionError as exc:
                    if _pipeline_mutation.get() == "prefilter-before-min":
                        raise _RenewalSelectionViolation from exc
                    raise
                assert candidate["dedup_key"].endswith(selected.isoformat())
                assert candidate["metadata"]["event_date"] == selected.isoformat()
                assert candidate["expires_at"] == datetime.combine(
                    selected + timedelta(days=1), datetime.min.time(), tzinfo=UTC
                )
                assert (
                    await observer.fetchval(
                        "SELECT count(*) FROM finance.obligation_ledger WHERE subscription_id=$1 AND period=$2",
                        subject,
                        selected,
                    )
                    == 1
                )
                premise = candidate["premise"]
                assert premise["args"] == {
                    "subject": str(subject),
                    "selected_date": selected.isoformat(),
                    "epoch": 0,
                }
            assert await owner.read(premise["args"]) is True
            if after_scan == "owner-confirmed":
                await routed_write(explicit_owner=True, renewal=selected, amount=99, currency="EUR")
                async with admin.acquire() as observer:
                    confirmed = await observer.fetchrow(
                        "SELECT * FROM finance.subscriptions WHERE id=$1", subject
                    )
                    assert confirmed["next_renewal"] == selected and confirmed["date_epoch"] == 1
                    assert all(claim["resolved"] for claim in confirmed["field_claims"])
                assert await owner.read(premise["args"]) is False
            elif after_scan == "unknown":
                receiver.stop_admission()  # real closed receiver, not a fabricated false/SQL response
                assert await owner.read(premise["args"]) is None
            if _pipeline_boot_race.get():
                from butlers.core.utils import generate_uuid7_string

                original_verify = owner.verify_consume
                old_local = receiver.identity()
                old_publication = publishers["finance"].revision

                async def rotate_after_callback(body):
                    proof = await original_verify(body)
                    if proof is not None and not boot_fence_state["rotated"]:
                        successor = UUID(generate_uuid7_string())
                        successor_epoch = await register_boot(finance_pool, "finance", successor)
                        assert successor_epoch == finance._boot_epoch + 1
                        async with admin.acquire() as observer:
                            observed = await observer.fetchrow(
                                "SELECT boot_instance_id,boot_epoch FROM "
                                "switchboard.butler_registry_control_plane WHERE name='finance'"
                            )
                            assert tuple(observed) == (successor, successor_epoch)
                        assert receiver.identity() == old_local
                        assert publishers["finance"].revision == old_publication
                        boot_fence_state["rotated"] = boot_fence_state["armed"] = True
                    return proof

                monkeypatch.setattr(owner, "verify_consume", rotate_after_callback)
            notify = AsyncMock(return_value={"status": "sent"})
            result = await broker.delivery_cycle(
                switchboard_pool, notify_fn=notify, now=now, finance_warning_reader=owner.read
            )
            if _pipeline_boot_race.get():
                await asyncio.sleep(0)  # deliver native query-logger callbacks before the oracle
                assert boot_fence_state["rotated"]
                try:
                    assert boot_fence_state["owning_reads"] == 0
                    assert "unverified" in notify.call_args.args[0].lower()
                    assert "as of" in notify.call_args.args[0].lower()
                except AssertionError as exc:
                    raise _BootAdmissionViolation from exc
            async with admin.acquire() as observer:
                stored = await observer.fetchrow(
                    "SELECT status FROM public.insight_candidates WHERE id=$1", candidate["id"]
                )
                if after_scan == "owner-confirmed":
                    assert result["withdrawn"] == 1 and stored["status"] == "withdrawn"
                    notify.assert_not_awaited()
                else:
                    assert stored["status"] == "delivered"
                    assert result["delivered"] == [str(candidate["id"])]
                    notify.assert_awaited_once()
                    if after_scan == "unknown":
                        assert "as of" in notify.call_args.args[0].lower()
                        assert "unverified" in notify.call_args.args[0].lower()
            # The already consumed/withdrawn durable row cannot send again on restart/replay.
            await broker.delivery_cycle(
                switchboard_pool, notify_fn=notify, now=now, finance_warning_reader=owner.read
            )
            assert notify.await_count == (0 if after_scan == "owner-confirmed" else 1)
        finally:
            await route._reset_router_client_cache_for_tests()
            await owner.close(5)
            await receiver.close(5)
            await owner.settle()
            await receiver.settle()
    await asyncio.sleep(0)
    assert not [task for task in asyncio.all_tasks() - baseline_tasks if not task.done()]


class _FieldProtectionViolation(AssertionError):
    pass


class _RenewalSelectionViolation(AssertionError):
    pass


_pipeline_mutation = ContextVar("finance_pipeline_product_mutation", default=None)


@pytest.fixture
def registered_field_mutation_dbs(postgres_container):
    from butlers.testing.migration import create_migrated_test_db, migration_db_name

    return [
        create_migrated_test_db(
            postgres_container,
            migration_db_name(),
            chains=["core", "finance", "switchboard"],
            schemas={"core": "finance", "finance": "finance", "switchboard": "switchboard"},
        )
        for _ in range(3)
    ]


def _pipeline_product_mutant(module, name, mutation):
    """Compile an exact production AST mutant; no SQL/pool/authority oracle replacement."""
    import ast
    import inspect

    original = getattr(module, name)
    tree = ast.parse(inspect.getsource(original))
    changed = 0
    if mutation == "field-protection":
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Subscript)
                and isinstance(node.targets[0].value, ast.Name)
                and node.targets[0].value.id == "state"
                and isinstance(node.targets[0].slice, ast.Constant)
                and node.targets[0].slice.value in {"next_renewal", "amount", "currency"}
                and isinstance(node.value, ast.IfExp)
                and isinstance(node.value.test, ast.Name)
                and node.value.test.id == "owner"
            ):
                node.value = node.value.body
                changed += 1
        assert changed == 3
    else:
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                old = ") dates WHERE day >= $1 AND day <= $2"
                if old in node.value:
                    assert node.value.count(old) == 1
                    assert node.value.count("AND selected.warning_date IS NOT NULL") == 1
                    node.value = node.value.replace(old, ") dates").replace(
                        "AND selected.warning_date IS NOT NULL",
                        "AND selected.warning_date BETWEEN $1 AND $2",
                    )
                    changed += 1
        assert changed == 1
    namespace = dict(module.__dict__)
    exec(
        compile(ast.fix_missing_locations(tree), inspect.getsourcefile(original), "exec"), namespace
    )
    mutant = namespace[name]
    assert (
        mutant.__code__.co_code != original.__code__.co_code
        or mutant.__code__.co_consts != original.__code__.co_consts
    )
    return original, mutant


def _pipeline_diagnostic(mutation, phase, status, exception="NONE"):
    """Emit only closed technical labels, never exception values or task/row identities."""
    if mutation not in {"field-protection", "prefilter-before-min", "boot-currentness"}:
        raise ValueError("unsupported pipeline mutation label")
    if phase not in {
        "baseline",
        "mutant_compile",
        "counterfactual",
        "module_restore",
        "restored",
        "cleanup",
    }:
        raise ValueError("unsupported pipeline phase label")
    if status not in {"BEGIN", "END", "ERROR", "EXPECTED_VIOLATION"}:
        raise ValueError("unsupported pipeline status label")
    allowed_exceptions = {
        "NONE",
        "UNKNOWN",
        "AssertionError",
        "TypeError",
        "RuntimeError",
        "ValueError",
        "TimeoutError",
        "CancelledError",
        "UniqueViolationError",
        "UndefinedTableError",
        "UndefinedColumnError",
        "InsufficientPrivilegeError",
        "ConnectionDoesNotExistError",
        "_FieldProtectionViolation",
        "_RenewalSelectionViolation",
        "_BootAdmissionViolation",
    }
    if exception not in allowed_exceptions:
        exception = "UNKNOWN"
    print(
        f"FINANCE_PIPELINE_DIAGNOSTIC mutation={mutation} phase={phase} "
        f"status={status} exception={exception}",
        flush=True,
    )


@contextmanager
def _pipeline_phase(mutation, phase):
    _pipeline_diagnostic(mutation, phase, "BEGIN")
    try:
        yield
    except BaseException as exc:
        _pipeline_diagnostic(mutation, phase, "ERROR", type(exc).__name__)
        raise
    else:
        _pipeline_diagnostic(mutation, phase, "END")


@contextmanager
def _expect_dedicated_invariant(expected):
    """Accept only the dedicated violation from a completed counterfactual exit."""

    def only_expected(error):
        if isinstance(error, BaseExceptionGroup):
            return bool(error.exceptions) and all(
                only_expected(child) for child in error.exceptions
            )
        return type(error) is expected

    try:
        yield
    except BaseException as exc:
        if not only_expected(exc):
            raise
    else:
        pytest.fail("Counterfactual completed without the dedicated invariant")


@pytest.mark.parametrize("mutation", ["field-protection", "prefilter-before-min"])
async def test_registered_pipeline_real_sql_mutants_are_killed(
    registered_field_mutation_dbs, monkeypatch, mutation
):
    """Hosted real SQL causal carrier; arbitrary setup/transport/SQL exceptions fail this test."""
    import butlers.jobs._roster.finance_jobs as jobs
    from butlers.tools.finance import subscriptions

    baseline, counterfactual, restored = registered_field_mutation_dbs
    with _pipeline_phase(mutation, "baseline"):
        with monkeypatch.context() as baseline_patch:
            await test_registered_routed_fields_scan_and_durable_broker_pipeline(
                baseline, baseline_patch, "current"
            )
    module, name, expected = (
        (subscriptions, "merge_field_claims", _FieldProtectionViolation)
        if mutation == "field-protection"
        else (jobs, "run_insight_scan", _RenewalSelectionViolation)
    )
    with _pipeline_phase(mutation, "mutant_compile"):
        original, mutant = _pipeline_product_mutant(module, name, mutation)
    token = _pipeline_mutation.set(mutation)
    try:
        with _pipeline_phase(mutation, "counterfactual"):
            with monkeypatch.context() as patcher:
                patcher.setattr(module, name, mutant)
                with _expect_dedicated_invariant(expected):
                    await test_registered_routed_fields_scan_and_durable_broker_pipeline(
                        counterfactual, patcher, "current"
                    )
            _pipeline_diagnostic(mutation, "counterfactual", "EXPECTED_VIOLATION")
    finally:
        with _pipeline_phase(mutation, "cleanup"):
            _pipeline_mutation.reset(token)
    with _pipeline_phase(mutation, "module_restore"):
        assert getattr(module, name) is original
    with _pipeline_phase(mutation, "restored"):
        with monkeypatch.context() as restored_patch:
            await test_registered_routed_fields_scan_and_durable_broker_pipeline(
                restored, restored_patch, "current"
            )


class _BootAdmissionViolation(AssertionError):
    pass


_pipeline_boot_race = ContextVar("finance_pipeline_boot_race", default=False)


async def test_registered_pipeline_committed_boot_rotation_causal_carrier(
    registered_field_mutation_dbs, monkeypatch
):
    """Real callback->successor commit->owning SQL refusal; not an arbitrary error kill."""
    import ast
    import inspect
    import textwrap

    from butlers.core import finance_warning_control as control

    with _pipeline_phase("boot-currentness", "mutant_compile"):
        original = control.FinanceWarningControlOwner._receiver_publication_current
        tree = ast.parse(textwrap.dedent(inspect.getsource(original)))
        replaced = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.BoolOp):
                for index, value in enumerate(node.values):
                    if (
                        isinstance(value, ast.Compare)
                        and isinstance(value.left, ast.Subscript)
                        and isinstance(value.left.value, ast.Name)
                        and value.left.value.id == "row"
                        and isinstance(value.left.slice, ast.Constant)
                        and value.left.slice.value == "boot_current"
                    ):
                        node.values[index] = ast.Constant(value=True)
                        replaced += 1
        assert replaced == 1
        namespace = dict(control.__dict__)
        exec(
            compile(ast.fix_missing_locations(tree), inspect.getsourcefile(original), "exec"),
            namespace,
        )
        mutant = namespace[original.__name__]
    baseline, counterfactual, restored = registered_field_mutation_dbs
    token = _pipeline_boot_race.set(True)
    try:
        with _pipeline_phase("boot-currentness", "baseline"):
            with monkeypatch.context() as baseline_patch:
                await test_registered_routed_fields_scan_and_durable_broker_pipeline(
                    baseline, baseline_patch, "current"
                )
        with _pipeline_phase("boot-currentness", "counterfactual"):
            with monkeypatch.context() as mutant_patch:
                mutant_patch.setattr(control.FinanceWarningControlOwner, original.__name__, mutant)
                with _expect_dedicated_invariant(_BootAdmissionViolation):
                    await test_registered_routed_fields_scan_and_durable_broker_pipeline(
                        counterfactual, mutant_patch, "current"
                    )
            _pipeline_diagnostic("boot-currentness", "counterfactual", "EXPECTED_VIOLATION")
        with _pipeline_phase("boot-currentness", "module_restore"):
            assert control.FinanceWarningControlOwner._receiver_publication_current is original
        with _pipeline_phase("boot-currentness", "restored"):
            with monkeypatch.context() as restored_patch:
                await test_registered_routed_fields_scan_and_durable_broker_pipeline(
                    restored, restored_patch, "current"
                )
    finally:
        with _pipeline_phase("boot-currentness", "cleanup"):
            _pipeline_boot_race.reset(token)


@pytest.mark.parametrize(
    "case",
    [
        "field-direct",
        "field-grouped",
        "field-nested",
        "renewal-direct",
        "renewal-grouped",
        "renewal-nested",
        "boot-direct",
        "boot-grouped",
        "boot-nested",
        "normal",
        "distinct",
        "mixed",
        "wrong-family",
        "timeout",
        "cancel",
        "setup",
        "teardown",
        "assertion",
        "subclass",
    ],
)
def test_dedicated_invariant_gate_rejects_unrelated_or_incomplete_failures(case):
    """Software exception-shape controls; these never exercise or credit SQL mutations."""
    import asyncio

    family = case.split("-", 1)[0]
    expected = {
        "field": _FieldProtectionViolation,
        "renewal": _RenewalSelectionViolation,
        "boot": _BootAdmissionViolation,
    }.get(family, _FieldProtectionViolation)
    accepted = family in {"field", "renewal", "boot"}
    error = None
    if accepted:
        error = expected()
        if case.endswith("grouped"):
            error = ExceptionGroup("controlled", [error, expected()])
        elif case.endswith("nested"):
            error = ExceptionGroup("controlled", [ExceptionGroup("controlled", [error])])
    elif case == "distinct":
        error = ValueError()
    elif case == "mixed":
        error = ExceptionGroup("controlled", [expected(), ValueError()])
    elif case == "wrong-family":
        error = _RenewalSelectionViolation()
    elif case == "timeout":
        error = TimeoutError()
    elif case == "cancel":
        error = asyncio.CancelledError()
    elif case in {"setup", "teardown"}:
        error = RuntimeError()
    elif case == "assertion":
        error = AssertionError()
    elif case == "subclass":

        class OtherViolation(expected):
            pass

        error = OtherViolation()
    caught = None
    try:
        with _expect_dedicated_invariant(expected):
            try:
                if case == "setup":
                    raise error
                if case == "teardown":
                    raise expected()
                if error is not None:
                    raise error
            finally:
                if case == "teardown":
                    raise error
    except BaseException as exc:
        caught = exc
    if accepted:
        assert caught is None
    elif case == "normal":
        assert isinstance(caught, pytest.fail.Exception)
    else:
        assert caught is error
