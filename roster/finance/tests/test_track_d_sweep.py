"""Integration tests for Track D — scheduled reconciliation sweep (bu-th0ph).

D1: The weekly upcoming-bills-check skill calls reconcile_bills() FIRST, then
    reports auto-settled bills, ambiguous candidates needing confirmation, and
    still-unpaid past-due bills in the notify() digest.

These tests verify the sweep behaviour in the context of the weekly bill check:

1. Auto-settled bills are removed from the upcoming/overdue list.
2. Ambiguous candidates remain in the pending list (not settled).
3. The reconcile_bills() result provides the data needed for the digest sections.
4. An empty sweep (no matching transactions) does not affect the upcoming bills.
5. The sweep is idempotent — running it twice produces no second-run settlements.
6. Payment recorded before the bill existed is caught and settled by the sweep.

Spec: openspec/changes/finance-bill-payment-reconciliation/design.md (section D)
"""

from __future__ import annotations

import shutil
from datetime import UTC, date, datetime, timedelta
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision a DB with core + finance migrations (incl. 009) applied once per module."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "finance"],
    )


@pytest.fixture
async def pool(migrated_db_url: str):
    """Return an asyncpg pool; truncate finance tables between tests."""
    p = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    await p.execute("TRUNCATE TABLE bills, transactions, subscriptions, accounts CASCADE")
    yield p
    await p.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _insert_bill(
    pool: asyncpg.Pool,
    payee: str = "HSBC Credit Card",
    amount: float = 0.00,
    currency: str = "SGD",
    due_date: date | None = None,
    status: str = "pending",
    statement_period_end: date | None = None,
) -> dict:
    """Insert a bill directly and return the row as a dict."""
    if due_date is None:
        due_date = date.today() - timedelta(days=5)
    row = await pool.fetchrow(
        """
        INSERT INTO bills (payee, amount, currency, due_date, frequency, status,
                           statement_period_end, reconciled_transaction_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7, NULL)
        RETURNING *
        """,
        payee,
        amount,
        currency,
        due_date,
        "monthly",
        status,
        statement_period_end,
    )
    return dict(row)


async def _insert_txn(
    pool: asyncpg.Pool,
    merchant: str = "HSBC Credit Card",
    amount: float = 717.57,
    currency: str = "SGD",
    direction: str = "debit",
    posted_at: datetime | None = None,
    payment_method: str | None = None,
) -> dict:
    """Insert a transaction directly and return the row as a dict."""
    if posted_at is None:
        posted_at = datetime.now(UTC) - timedelta(days=3)
    row = await pool.fetchrow(
        """
        INSERT INTO transactions
            (merchant, amount, currency, direction, posted_at, category, metadata)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING *
        """,
        merchant,
        abs(amount),
        currency,
        direction,
        posted_at,
        "utilities",
        {},
    )
    return dict(row)


# ---------------------------------------------------------------------------
# D — Sweep removes auto-settled bills from the upcoming list
# ---------------------------------------------------------------------------


class TestSweepAutoSettledNotInUpcoming:
    """D1: auto-settled bills are paid and must not appear as overdue/pending."""

    async def test_auto_settled_bill_removed_from_upcoming(self, pool):
        """A bill auto-settled by reconcile_bills() does not appear in upcoming_bills().

        Scenario: HSBC placeholder bill ($0, past due) + matching debit
        → sweep settles it → upcoming_bills(include_overdue=True) returns empty.
        """
        from butlers.tools.finance.bills import upcoming_bills
        from butlers.tools.finance.reconciliation import reconcile_bills

        await _insert_bill(
            pool,
            payee="HSBC Credit Card",
            amount=0.00,
            currency="SGD",
            due_date=date.today() - timedelta(days=5),
            status="pending",
        )
        await _insert_txn(
            pool,
            merchant="HSBC Credit Card",
            amount=717.57,
            currency="SGD",
            posted_at=datetime.now(UTC) - timedelta(days=3),
        )

        # Step 0: run sweep first
        sweep = await reconcile_bills(pool=pool, lookback_days=90)
        assert len(sweep["auto_settled"]) == 1
        assert sweep["auto_settled"][0]["payee"] == "HSBC Credit Card"

        # Step 1: fetch upcoming bills — settled bill is gone
        result = await upcoming_bills(pool=pool, days_ahead=14, include_overdue=True)
        assert result["items"] == [], "auto-settled bill must not appear as unpaid"
        assert result["totals"]["overdue"] == 0

    async def test_auto_settled_bill_appears_only_in_sweep_output(self, pool):
        """reconcile_bills() returns settled bill in auto_settled for digest reporting."""
        from butlers.tools.finance.reconciliation import reconcile_bills

        await _insert_bill(
            pool,
            payee="Singtel",
            amount=80.00,
            currency="SGD",
            due_date=date.today() - timedelta(days=3),
            status="pending",
        )
        await _insert_txn(
            pool,
            merchant="Singtel",
            amount=80.00,
            currency="SGD",
            posted_at=datetime.now(UTC) - timedelta(days=2),
        )

        sweep = await reconcile_bills(pool=pool)

        assert len(sweep["auto_settled"]) == 1
        settled = sweep["auto_settled"][0]

        # Fields required for the digest
        assert "payee" in settled
        assert "bill_id" in settled
        assert "amount" in settled
        assert "paid_at" in settled
        assert "txn_id" in settled

        assert settled["payee"] == "Singtel"
        assert abs(Decimal(settled["amount"]) - Decimal("80.00")) < Decimal("0.01")

    async def test_multiple_auto_settled_all_removed(self, pool):
        """Two auto-settled bills are both removed from the upcoming list."""
        from butlers.tools.finance.bills import upcoming_bills
        from butlers.tools.finance.reconciliation import reconcile_bills

        for payee, amount in [("Netflix", 15.49), ("Spotify", 9.99)]:
            await _insert_bill(
                pool,
                payee=payee,
                amount=amount,
                currency="USD",
                due_date=date.today() - timedelta(days=2),
                status="pending",
            )
            await _insert_txn(
                pool,
                merchant=payee,
                amount=amount,
                currency="USD",
                posted_at=datetime.now(UTC) - timedelta(days=1),
            )

        sweep = await reconcile_bills(pool=pool)
        assert len(sweep["auto_settled"]) == 2

        result = await upcoming_bills(pool=pool, days_ahead=14, include_overdue=True)
        assert result["items"] == [], "all auto-settled bills must be gone from upcoming"


# ---------------------------------------------------------------------------
# D — Ambiguous candidates remain pending and surface in sweep output
# ---------------------------------------------------------------------------


class TestSweepCandidatesRemainPending:
    """D1: confirm-tier candidates are not settled — they appear in upcoming list."""

    async def test_candidates_remain_in_upcoming_list(self, pool):
        """Bills in the candidates (confirm) tier are NOT settled and still appear as pending."""
        from butlers.tools.finance.bills import upcoming_bills
        from butlers.tools.finance.reconciliation import reconcile_bills

        today = date.today()
        # Two same-payee bills — both in-window → confirm tier
        await _insert_bill(
            pool,
            payee="DBS Credit Card",
            amount=500.00,
            currency="SGD",
            due_date=today - timedelta(days=3),
            status="pending",
        )
        await _insert_bill(
            pool,
            payee="DBS Credit Card",
            amount=500.00,
            currency="SGD",
            due_date=today - timedelta(days=8),
            status="pending",
        )
        await _insert_txn(
            pool,
            merchant="DBS Credit Card",
            amount=500.00,
            currency="SGD",
            posted_at=datetime.now(UTC) - timedelta(days=3),
        )

        sweep = await reconcile_bills(pool=pool)

        # Should surface candidates, not auto-settle
        assert len(sweep["auto_settled"]) == 0
        assert len(sweep["candidates"]) >= 1

        # Both bills still pending → appear in upcoming
        result = await upcoming_bills(pool=pool, days_ahead=14, include_overdue=True)
        assert len(result["items"]) == 2, "unresolved ambiguous bills must remain in upcoming list"

    async def test_candidates_carry_required_digest_fields(self, pool):
        """Each candidate entry has the fields needed for the digest confirm section."""
        from butlers.tools.finance.reconciliation import reconcile_bills

        today = date.today()
        await _insert_bill(
            pool,
            payee="OCBC",
            amount=350.00,
            currency="SGD",
            due_date=today - timedelta(days=2),
            status="pending",
        )
        await _insert_bill(
            pool,
            payee="OCBC",
            amount=350.00,
            currency="SGD",
            due_date=today - timedelta(days=7),
            status="pending",
        )
        await _insert_txn(
            pool,
            merchant="OCBC",
            amount=350.00,
            currency="SGD",
            posted_at=datetime.now(UTC) - timedelta(days=2),
        )

        sweep = await reconcile_bills(pool=pool)
        assert len(sweep["candidates"]) >= 1

        for candidate in sweep["candidates"]:
            # Required for digest "CONFIRM NEEDED" section
            assert "bill_id" in candidate
            assert "payee" in candidate
            assert "due_date" in candidate
            assert "amount" in candidate
            assert "candidates" in candidate  # transaction candidates list
            assert len(candidate["candidates"]) >= 1

            txn_candidate = candidate["candidates"][0]
            assert "txn_id" in txn_candidate
            assert "merchant" in txn_candidate
            assert "amount" in txn_candidate
            assert "posted_at" in txn_candidate


# ---------------------------------------------------------------------------
# D — Empty sweep does not affect upcoming bills
# ---------------------------------------------------------------------------


class TestEmptySweepNoEffect:
    """D1 no-op: when there are no matching transactions, the sweep is empty."""

    async def test_sweep_empty_when_no_transactions(self, pool):
        """reconcile_bills() returns empty lists when there are no transactions."""
        from butlers.tools.finance.reconciliation import reconcile_bills

        await _insert_bill(
            pool,
            payee="SP Group",
            amount=120.00,
            currency="SGD",
            due_date=date.today() + timedelta(days=3),
            status="pending",
        )

        sweep = await reconcile_bills(pool=pool)

        assert sweep["auto_settled"] == []
        assert sweep["candidates"] == []

    async def test_upcoming_bills_unaffected_by_empty_sweep(self, pool):
        """If sweep finds nothing, upcoming_bills() still returns the pending bills."""
        from butlers.tools.finance.bills import upcoming_bills
        from butlers.tools.finance.reconciliation import reconcile_bills

        await _insert_bill(
            pool,
            payee="Electric Company",
            amount=95.00,
            currency="USD",
            due_date=date.today() + timedelta(days=7),
            status="pending",
        )

        # No transactions → sweep is a no-op
        sweep = await reconcile_bills(pool=pool)
        assert sweep["auto_settled"] == []

        result = await upcoming_bills(pool=pool, days_ahead=14, include_overdue=True)
        assert len(result["items"]) == 1
        assert result["items"][0]["bill"]["payee"] == "Electric Company"

    async def test_no_bills_no_transactions_sweep_empty(self, pool):
        """Empty database: sweep returns empty lists, upcoming_bills returns empty items."""
        from butlers.tools.finance.bills import upcoming_bills
        from butlers.tools.finance.reconciliation import reconcile_bills

        sweep = await reconcile_bills(pool=pool)
        assert sweep["auto_settled"] == []
        assert sweep["candidates"] == []

        result = await upcoming_bills(pool=pool, days_ahead=14, include_overdue=True)
        assert result["items"] == []


# ---------------------------------------------------------------------------
# D — Idempotency in the weekly context
# ---------------------------------------------------------------------------


class TestSweepIdempotency:
    """D1 idempotency: running the sweep twice does not double-settle anything."""

    async def test_second_sweep_settles_nothing_new(self, pool):
        """Running reconcile_bills() twice: first settles, second finds nothing."""
        from butlers.tools.finance.reconciliation import reconcile_bills

        await _insert_bill(
            pool,
            payee="Spotify",
            amount=9.99,
            currency="USD",
            due_date=date.today() - timedelta(days=2),
            status="pending",
        )
        await _insert_txn(
            pool,
            merchant="Spotify",
            amount=9.99,
            currency="USD",
            posted_at=datetime.now(UTC) - timedelta(days=1),
        )

        sweep1 = await reconcile_bills(pool=pool)
        assert len(sweep1["auto_settled"]) == 1

        # Second sweep (as would happen the following week)
        sweep2 = await reconcile_bills(pool=pool)
        assert len(sweep2["auto_settled"]) == 0
        assert len(sweep2["candidates"]) == 0

    async def test_sweep_then_upcoming_then_sweep_stable(self, pool):
        """Calling sweep → upcoming_bills → sweep again yields stable results."""
        from butlers.tools.finance.bills import upcoming_bills
        from butlers.tools.finance.reconciliation import reconcile_bills

        # Two bills: one that matches (will be settled), one that doesn't
        await _insert_bill(
            pool,
            payee="Netflix",
            amount=15.49,
            currency="USD",
            due_date=date.today() - timedelta(days=3),
            status="pending",
        )
        await _insert_bill(
            pool,
            payee="Hulu",
            amount=17.99,
            currency="USD",
            due_date=date.today() + timedelta(days=10),
            status="pending",
        )
        await _insert_txn(
            pool,
            merchant="Netflix",
            amount=15.49,
            currency="USD",
            posted_at=datetime.now(UTC) - timedelta(days=2),
        )
        # No transaction for Hulu

        sweep1 = await reconcile_bills(pool=pool)
        assert len(sweep1["auto_settled"]) == 1  # Netflix settled

        bills = await upcoming_bills(pool=pool, days_ahead=14, include_overdue=True)
        payees = [item["bill"]["payee"] for item in bills["items"]]
        assert "Netflix" not in payees, "settled bill must not appear"
        assert "Hulu" in payees, "pending bill must still appear"

        sweep2 = await reconcile_bills(pool=pool)
        assert len(sweep2["auto_settled"]) == 0  # no new settlements


# ---------------------------------------------------------------------------
# D — Payment recorded before bill existed (the motivating backstop scenario)
# ---------------------------------------------------------------------------


class TestPaymentBeforeBillSwept:
    """D1 backstop: sweep catches the inline-hook miss when txn preceded the bill."""

    async def test_payment_before_bill_caught_by_sweep(self, pool):
        """Transaction recorded before bill existed is settled by the weekly sweep.

        This is the core reason for the backstop: the inline hook missed it because
        no bill row existed when the debit was recorded. The weekly sweep finds both.
        """
        from butlers.tools.finance.bills import upcoming_bills
        from butlers.tools.finance.reconciliation import reconcile_bills

        # 1. Transaction arrives first (e.g. bank SMS notification)
        await _insert_txn(
            pool,
            merchant="POSB",
            amount=250.00,
            currency="SGD",
            posted_at=datetime.now(UTC) - timedelta(days=10),
        )

        # 2. Bill created later (e.g. bill statement email arrived after payment)
        await _insert_bill(
            pool,
            payee="POSB",
            amount=250.00,
            currency="SGD",
            due_date=date.today() - timedelta(days=8),
            status="pending",
        )

        # 3. Without sweep, bill appears as overdue
        result_before = await upcoming_bills(pool=pool, days_ahead=14, include_overdue=True)
        assert len(result_before["items"]) == 1
        assert result_before["totals"]["overdue"] == 1

        # 4. Sweep catches and settles it
        sweep = await reconcile_bills(pool=pool, lookback_days=90)
        assert len(sweep["auto_settled"]) == 1
        assert sweep["auto_settled"][0]["payee"] == "POSB"

        # 5. Bill no longer appears as overdue
        result_after = await upcoming_bills(pool=pool, days_ahead=14, include_overdue=True)
        assert result_after["items"] == []

    async def test_sweep_return_structure_for_full_digest(self, pool):
        """reconcile_bills() returns both auto_settled and candidates with full digest data."""
        from butlers.tools.finance.reconciliation import reconcile_bills

        today = date.today()

        # One auto-settleable: exact match
        await _insert_bill(
            pool,
            payee="Grab",
            amount=45.00,
            currency="SGD",
            due_date=today - timedelta(days=5),
            status="pending",
        )
        await _insert_txn(
            pool,
            merchant="Grab",
            amount=45.00,
            currency="SGD",
            posted_at=datetime.now(UTC) - timedelta(days=4),
        )

        # Two ambiguous: same-payee bills both in-window
        await _insert_bill(
            pool,
            payee="StarHub",
            amount=100.00,
            currency="SGD",
            due_date=today - timedelta(days=3),
            status="pending",
        )
        await _insert_bill(
            pool,
            payee="StarHub",
            amount=100.00,
            currency="SGD",
            due_date=today - timedelta(days=10),
            status="pending",
        )
        await _insert_txn(
            pool,
            merchant="StarHub",
            amount=100.00,
            currency="SGD",
            posted_at=datetime.now(UTC) - timedelta(days=5),
        )

        sweep = await reconcile_bills(pool=pool)

        # Digest data: auto-settled section
        assert len(sweep["auto_settled"]) == 1
        assert sweep["auto_settled"][0]["payee"] == "Grab"
        assert abs(Decimal(sweep["auto_settled"][0]["amount"]) - Decimal("45.00")) < Decimal("0.01")

        # Digest data: confirm section
        assert len(sweep["candidates"]) >= 1
        for c in sweep["candidates"]:
            assert c["payee"] == "StarHub"
            assert Decimal(str(c["amount"])) == Decimal("100.00")

    async def test_overdue_bill_settled_by_sweep_clears_overdue_count(self, pool):
        """An overdue-status bill settled by the sweep disappears from the overdue count."""
        from butlers.tools.finance.bills import upcoming_bills
        from butlers.tools.finance.reconciliation import reconcile_bills

        await _insert_bill(
            pool,
            payee="SP Services",
            amount=130.00,
            currency="SGD",
            due_date=date.today() - timedelta(days=5),
            status="overdue",
        )
        await _insert_txn(
            pool,
            merchant="SP Services",
            amount=130.00,
            currency="SGD",
            posted_at=datetime.now(UTC) - timedelta(days=2),
        )

        before = await upcoming_bills(pool=pool, days_ahead=14, include_overdue=True)
        assert before["totals"]["overdue"] == 1

        sweep = await reconcile_bills(pool=pool)
        assert len(sweep["auto_settled"]) == 1

        after = await upcoming_bills(pool=pool, days_ahead=14, include_overdue=True)
        assert after["totals"]["overdue"] == 0
        assert after["items"] == []
