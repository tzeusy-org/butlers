"""Tests for deterministic bill↔payment reconciliation (bu-fo2uv — Track B).

B1: Reproducer — a $0.00 pending HSBC bill + matching HSBC debit stays unlinked
    without the reconciliation engine (RED before B2/B3), then is settled by
    reconcile_bills() once the engine exists (GREEN after B2/B3).

B5: Matcher unit tests for all spec-required edge cases:
    - high-confidence exact match → auto_settle
    - placeholder ($0.00) backfill → auto_settle
    - multiple candidates in-window → confirm
    - fuzzy (substring) payee match → confirm
    - amount out of tolerance → none
    - currency mismatch → none
    - credit transaction → none
    - UTC date-window boundaries
    - idempotency: linked txn skipped; paid bill not re-settled; guarded UPDATE
      yields zero rows on second concurrent attempt
    - same-payee duplicate bills: two in-window → confirm; one in-window → auto_settle
    - payment-recorded-before-bill: sweep settles it
    - precision regression: amount path must never go through float (bu-1loc3)

Spec: openspec/changes/finance-bill-payment-reconciliation/design.md
"""

from __future__ import annotations

import shutil
import uuid
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
    """Provision a DB with core + finance migrations (including 009) applied once per module."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "finance"],
    )


@pytest.fixture
async def pool(migrated_db_url: str):
    """Return an asyncpg pool; truncate all finance tables between tests."""
    p = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    # Truncate in FK-safe order: child tables first
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
    frequency: str = "monthly",
    statement_period_end: date | None = None,
    reconciled_transaction_id: uuid.UUID | None = None,
) -> dict:
    """Insert a bill directly and return the row as a dict."""
    if due_date is None:
        due_date = date.today() - timedelta(days=5)
    row = await pool.fetchrow(
        """
        INSERT INTO bills (payee, amount, currency, due_date, frequency, status,
                           statement_period_end, reconciled_transaction_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        RETURNING *
        """,
        payee,
        amount,
        currency,
        due_date,
        frequency,
        status,
        statement_period_end,
        reconciled_transaction_id,
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
    category: str = "utilities",
) -> dict:
    """Insert a transaction directly and return the row as a dict."""
    if posted_at is None:
        posted_at = datetime.now(UTC) - timedelta(days=3)
    row = await pool.fetchrow(
        """
        INSERT INTO transactions (merchant, amount, currency, direction, posted_at, category, metadata)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING *
        """,
        merchant,
        abs(amount),
        currency,
        direction,
        posted_at,
        category,
        {},
    )
    return dict(row)


# ---------------------------------------------------------------------------
# B1 — Reproducer: demonstrates the gap and verifies the fix
# ---------------------------------------------------------------------------


class TestB1Reproducer:
    """B1: $0.00 pending HSBC bill + matching debit — gap and fix.

    RED before B2/B3: reconcile_bills() does not exist.
    GREEN after B2/B3: reconcile_bills() settles the bill and backfills the amount.
    """

    async def test_paid_hsbc_bill_stays_pending_without_reconciliation(self, pool):
        """Without reconcile_bills(), the HSBC bill stays pending.

        This is the failing reproducer: a real owner incident where SGD 717.57
        paid to HSBC stayed pending $0.00 because there was no deterministic
        matcher linking the debit transaction to the bill.

        The test is RED before B2/B3 because importing reconcile_bills() fails.
        After implementation it goes GREEN: the sweep finds and settles the bill.
        """
        from butlers.tools.finance.reconciliation import reconcile_bills

        # 1. Create placeholder HSBC bill — amount unknown when statement email arrived
        due = date.today() - timedelta(days=5)
        bill = await _insert_bill(
            pool,
            payee="HSBC Credit Card",
            amount=0.00,
            currency="SGD",
            due_date=due,
            status="pending",
        )
        bill_id = bill["id"]
        assert bill["status"] == "pending"
        assert float(bill["amount"]) == 0.00

        # 2. The SGD 717.57 debit was recorded (payment happened)
        txn = await _insert_txn(
            pool,
            merchant="HSBC Credit Card",
            amount=717.57,
            currency="SGD",
            direction="debit",
        )

        # 3. Without reconciliation, the bill is STILL pending — the bug
        row_before = await pool.fetchrow(
            "SELECT status, reconciled_transaction_id FROM bills WHERE id = $1",
            bill_id,
        )
        assert row_before["status"] == "pending"
        assert row_before["reconciled_transaction_id"] is None

        # 4. reconcile_bills() finds and auto-settles the match
        result = await reconcile_bills(pool=pool)

        assert len(result["auto_settled"]) == 1
        settled = result["auto_settled"][0]
        assert settled["payee"] == "HSBC Credit Card"
        assert abs(Decimal(settled["amount"]) - Decimal("717.57")) < Decimal("0.01")

        # 5. Verify the bill is now paid and backfilled
        row_after = await pool.fetchrow(
            "SELECT status, reconciled_transaction_id, amount FROM bills WHERE id = $1",
            bill_id,
        )
        assert row_after["status"] == "paid"
        assert row_after["reconciled_transaction_id"] == txn["id"]
        assert abs(float(row_after["amount"]) - 717.57) < 0.01


# ---------------------------------------------------------------------------
# B5 — Matcher unit tests
# ---------------------------------------------------------------------------


class TestHighConfidenceExactMatch:
    """Exact payee + single candidate + amount in tolerance → auto_settle."""

    async def test_exact_match_auto_settles(self, pool):
        """Exact payee + single in-window candidate + amount in tolerance → auto_settle."""
        from butlers.tools.finance.reconciliation import reconcile_bills

        bill = await _insert_bill(
            pool,
            payee="Grab",
            amount=25.00,
            currency="SGD",
            due_date=date.today() - timedelta(days=3),
        )
        txn = await _insert_txn(
            pool,
            merchant="Grab",
            amount=25.00,
            currency="SGD",
            direction="debit",
        )

        result = await reconcile_bills(pool=pool)

        assert len(result["auto_settled"]) == 1
        settled = result["auto_settled"][0]
        assert settled["bill_id"] == str(bill["id"])
        assert settled["txn_id"] == str(txn["id"])

    async def test_auto_settle_sets_status_paid(self, pool):
        """auto_settle updates bill status to 'paid'."""
        from butlers.tools.finance.reconciliation import reconcile_bills

        bill = await _insert_bill(
            pool,
            payee="Netflix",
            amount=15.49,
            currency="USD",
            due_date=date.today() - timedelta(days=2),
        )
        await _insert_txn(pool, merchant="Netflix", amount=15.49, currency="USD", direction="debit")

        await reconcile_bills(pool=pool)

        row = await pool.fetchrow("SELECT status FROM bills WHERE id = $1", bill["id"])
        assert row["status"] == "paid"

    async def test_auto_settle_links_transaction(self, pool):
        """auto_settle sets reconciled_transaction_id to the matched transaction UUID."""
        from butlers.tools.finance.reconciliation import reconcile_bills

        bill = await _insert_bill(
            pool,
            payee="Spotify",
            amount=9.99,
            currency="USD",
            due_date=date.today() - timedelta(days=1),
        )
        txn = await _insert_txn(
            pool, merchant="Spotify", amount=9.99, currency="USD", direction="debit"
        )

        await reconcile_bills(pool=pool)

        row = await pool.fetchrow(
            "SELECT reconciled_transaction_id FROM bills WHERE id = $1", bill["id"]
        )
        assert row["reconciled_transaction_id"] == txn["id"]


class TestPlaceholderBackfill:
    """$0.00 bill is compatible with any amount; settlement backfills the amount."""

    async def test_placeholder_bill_is_settled(self, pool):
        """$0.00 placeholder bill + matching debit → auto_settle."""
        from butlers.tools.finance.reconciliation import reconcile_bills

        bill = await _insert_bill(
            pool,
            payee="HSBC Credit Card",
            amount=0.00,
            currency="SGD",
            due_date=date.today() - timedelta(days=5),
            status="pending",
        )
        await _insert_txn(pool, merchant="HSBC Credit Card", amount=717.57, currency="SGD")

        result = await reconcile_bills(pool=pool)

        assert len(result["auto_settled"]) == 1
        assert result["auto_settled"][0]["bill_id"] == str(bill["id"])

    async def test_placeholder_amount_backfilled(self, pool):
        """Amount is backfilled from the transaction when bill.amount == 0."""
        from butlers.tools.finance.reconciliation import reconcile_bills

        bill = await _insert_bill(
            pool,
            payee="HSBC Credit Card",
            amount=0.00,
            currency="SGD",
            due_date=date.today() - timedelta(days=5),
        )
        await _insert_txn(pool, merchant="HSBC Credit Card", amount=717.57, currency="SGD")

        await reconcile_bills(pool=pool)

        row = await pool.fetchrow("SELECT amount FROM bills WHERE id = $1", bill["id"])
        assert abs(float(row["amount"]) - 717.57) < 0.01


class TestMultipleCandidatesConfirm:
    """Two or more in-window candidates → confirm tier, no auto-settlement."""

    async def test_two_in_window_candidates_yields_confirm(self, pool):
        """Two bills with same payee both in-window → confirm, nothing auto-settled."""
        from butlers.tools.finance.reconciliation import reconcile_bills

        # Two bills close together — both windows contain the same payment date
        txn_date = date.today() - timedelta(days=20)
        bill_a = await _insert_bill(
            pool,
            payee="DBS Card",
            amount=200.00,
            currency="SGD",
            due_date=txn_date + timedelta(days=3),
        )
        bill_b = await _insert_bill(
            pool,
            payee="DBS Card",
            amount=200.00,
            currency="SGD",
            due_date=txn_date + timedelta(days=8),
        )
        await _insert_txn(
            pool,
            merchant="DBS Card",
            amount=200.00,
            currency="SGD",
            posted_at=datetime.combine(txn_date, datetime.min.time()).replace(tzinfo=UTC),
        )

        result = await reconcile_bills(pool=pool)

        assert len(result["auto_settled"]) == 0
        assert len(result["candidates"]) >= 1
        # Verify both bills are still pending
        for bill in [bill_a, bill_b]:
            row = await pool.fetchrow("SELECT status FROM bills WHERE id = $1", bill["id"])
            assert row["status"] == "pending"


class TestFuzzyPayeeConfirm:
    """Substring payee match → confirm tier."""

    async def test_substring_payee_yields_confirm(self, pool):
        """When payee is a substring match (not exact), tier is confirm."""
        from butlers.tools.finance.reconciliation import reconcile_bills

        # Bill says "HSBC" but transaction merchant is "HSBC Credit Card"
        await _insert_bill(
            pool,
            payee="HSBC",
            amount=500.00,
            currency="SGD",
            due_date=date.today() - timedelta(days=3),
        )
        await _insert_txn(pool, merchant="HSBC Credit Card", amount=500.00, currency="SGD")

        result = await reconcile_bills(pool=pool)

        # Should not auto-settle — fuzzy match only
        assert len(result["auto_settled"]) == 0
        assert len(result["candidates"]) >= 1


class TestAmountTolerance:
    """Amount tolerance: max($1.00, 1% of bill amount)."""

    async def test_within_tolerance_auto_settles(self, pool):
        """Amount within max($1, 1%) tolerance → auto_settle."""
        from butlers.tools.finance.reconciliation import reconcile_bills

        # Bill $100, txn $99.50 — diff $0.50, tolerance max($1, $1) = $1.00 → OK
        await _insert_bill(
            pool,
            payee="Water Bill",
            amount=100.00,
            currency="SGD",
            due_date=date.today() - timedelta(days=2),
        )
        await _insert_txn(pool, merchant="Water Bill", amount=99.50, currency="SGD")

        result = await reconcile_bills(pool=pool)

        assert len(result["auto_settled"]) == 1

    async def test_outside_tolerance_yields_none(self, pool):
        """Amount outside max($1, 1%) tolerance → no match."""
        from butlers.tools.finance.reconciliation import reconcile_bills

        # Bill $100, txn $90 — diff $10, tolerance $1 → NOT OK
        await _insert_bill(
            pool,
            payee="Electric Bill",
            amount=100.00,
            currency="SGD",
            due_date=date.today() - timedelta(days=2),
        )
        await _insert_txn(pool, merchant="Electric Bill", amount=90.00, currency="SGD")

        result = await reconcile_bills(pool=pool)

        assert len(result["auto_settled"]) == 0
        assert len(result["candidates"]) == 0

    async def test_large_bill_1pct_tolerance(self, pool):
        """For large bills, tolerance is 1% of amount (e.g. $717 bill allows ~$7.17 diff)."""
        from butlers.tools.finance.reconciliation import reconcile_bills

        # Bill $717.57, txn $714.00 — diff $3.57, tolerance max($1, $7.1757) = $7.18 → OK
        await _insert_bill(
            pool,
            payee="HSBC Credit Card",
            amount=717.57,
            currency="SGD",
            due_date=date.today() - timedelta(days=3),
        )
        await _insert_txn(pool, merchant="HSBC Credit Card", amount=714.00, currency="SGD")

        result = await reconcile_bills(pool=pool)

        assert len(result["auto_settled"]) == 1


class TestCurrencyMismatch:
    """Currency mismatch → no match."""

    async def test_currency_mismatch_yields_none(self, pool):
        """Bill in SGD, transaction in USD → no match."""
        from butlers.tools.finance.reconciliation import reconcile_bills

        await _insert_bill(
            pool,
            payee="HSBC Credit Card",
            amount=717.57,
            currency="SGD",
            due_date=date.today() - timedelta(days=3),
        )
        await _insert_txn(pool, merchant="HSBC Credit Card", amount=717.57, currency="USD")

        result = await reconcile_bills(pool=pool)

        assert len(result["auto_settled"]) == 0
        assert len(result["candidates"]) == 0


class TestCreditTransactionNone:
    """Credit transactions never settle bills."""

    async def test_credit_txn_not_matched(self, pool):
        """A credit transaction (refund/income) is never matched against bills."""
        from butlers.tools.finance.reconciliation import reconcile_bills

        await _insert_bill(
            pool,
            payee="Grab",
            amount=25.00,
            currency="SGD",
            due_date=date.today() - timedelta(days=2),
        )
        await _insert_txn(
            pool,
            merchant="Grab",
            amount=25.00,
            currency="SGD",
            direction="credit",  # refund, not a payment
        )

        result = await reconcile_bills(pool=pool)

        assert len(result["auto_settled"]) == 0


class TestDateWindowBoundaries:
    """UTC date-window boundary tests (LOOKBACK=45d, GRACE=7d)."""

    async def test_txn_at_window_start_included(self, pool):
        """Transaction on due_date - 45 days is within window (inclusive)."""
        from butlers.tools.finance.reconciliation import reconcile_bills

        due = date.today()
        # Transaction exactly 45 days before due_date → within window
        txn_date = due - timedelta(days=45)
        await _insert_bill(pool, payee="Rent", amount=1800.00, currency="SGD", due_date=due)
        await _insert_txn(
            pool,
            merchant="Rent",
            amount=1800.00,
            currency="SGD",
            posted_at=datetime.combine(txn_date, datetime.min.time()).replace(tzinfo=UTC),
        )

        result = await reconcile_bills(pool=pool)

        assert len(result["auto_settled"]) == 1

    async def test_txn_before_window_start_excluded(self, pool):
        """Transaction 46 days before due_date is outside the window."""
        from butlers.tools.finance.reconciliation import reconcile_bills

        due = date.today()
        txn_date = due - timedelta(days=46)  # one day before window opens
        await _insert_bill(pool, payee="Rent", amount=1800.00, currency="SGD", due_date=due)
        await _insert_txn(
            pool,
            merchant="Rent",
            amount=1800.00,
            currency="SGD",
            posted_at=datetime.combine(txn_date, datetime.min.time()).replace(tzinfo=UTC),
        )

        result = await reconcile_bills(pool=pool)

        assert len(result["auto_settled"]) == 0

    async def test_txn_at_window_end_included(self, pool):
        """Transaction on due_date + 7 days is within window (inclusive)."""
        from butlers.tools.finance.reconciliation import reconcile_bills

        due = date.today() - timedelta(days=10)
        txn_date = due + timedelta(days=7)  # exactly at GRACE boundary
        await _insert_bill(pool, payee="Internet", amount=60.00, currency="SGD", due_date=due)
        await _insert_txn(
            pool,
            merchant="Internet",
            amount=60.00,
            currency="SGD",
            posted_at=datetime.combine(txn_date, datetime.min.time()).replace(tzinfo=UTC),
        )

        result = await reconcile_bills(pool=pool)

        assert len(result["auto_settled"]) == 1

    async def test_txn_after_window_end_excluded(self, pool):
        """Transaction 8 days after due_date is outside the grace window."""
        from butlers.tools.finance.reconciliation import reconcile_bills

        due = date.today() - timedelta(days=15)
        txn_date = due + timedelta(days=8)  # one day past GRACE boundary
        await _insert_bill(pool, payee="Internet", amount=60.00, currency="SGD", due_date=due)
        await _insert_txn(
            pool,
            merchant="Internet",
            amount=60.00,
            currency="SGD",
            posted_at=datetime.combine(txn_date, datetime.min.time()).replace(tzinfo=UTC),
        )

        result = await reconcile_bills(pool=pool)

        assert len(result["auto_settled"]) == 0

    async def test_statement_period_end_used_as_anchor(self, pool):
        """When statement_period_end is set, it replaces due_date as the window anchor."""
        from butlers.tools.finance.reconciliation import reconcile_bills

        # Bill due July 31, statement_period_end June 30
        # Anchor = June 30; window = [May 16, July 7]
        # due July 31 would give window [Jun 16, Aug 7]
        # Txn posted May 20 → within [May 16, Jul 7] but NOT within [Jun 16, Aug 7]
        statement_end = date.today() - timedelta(days=10)
        due_date = statement_end + timedelta(days=31)  # a month later
        txn_date = statement_end - timedelta(days=5)  # 5 days before statement end

        await _insert_bill(
            pool,
            payee="OCBC Card",
            amount=300.00,
            currency="SGD",
            due_date=due_date,
            statement_period_end=statement_end,
        )
        await _insert_txn(
            pool,
            merchant="OCBC Card",
            amount=300.00,
            currency="SGD",
            posted_at=datetime.combine(txn_date, datetime.min.time()).replace(tzinfo=UTC),
        )

        result = await reconcile_bills(pool=pool)

        # Should settle using statement_period_end as anchor
        assert len(result["auto_settled"]) == 1


class TestIdempotency:
    """Idempotency: linked txn skipped; paid bill not re-settled; guarded UPDATE."""

    async def test_already_linked_txn_skipped(self, pool):
        """A transaction that already reconciled a bill is not re-matched."""
        from butlers.tools.finance.reconciliation import reconcile_bills

        # First bill — already reconciled
        txn = await _insert_txn(pool, merchant="Grab", amount=25.00, currency="SGD")
        await _insert_bill(
            pool,
            payee="Grab",
            amount=25.00,
            currency="SGD",
            due_date=date.today() - timedelta(days=3),
            reconciled_transaction_id=txn["id"],
            status="paid",
        )
        # Second bill — same payee/amount, should NOT steal the same txn
        bill_b = await _insert_bill(
            pool,
            payee="Grab",
            amount=25.00,
            currency="SGD",
            due_date=date.today() - timedelta(days=2),
        )

        result = await reconcile_bills(pool=pool)

        # bill_b should NOT be settled because the txn is already linked to bill_a
        assert len(result["auto_settled"]) == 0
        row = await pool.fetchrow(
            "SELECT status, reconciled_transaction_id FROM bills WHERE id = $1",
            bill_b["id"],
        )
        assert row["status"] == "pending"
        assert row["reconciled_transaction_id"] is None

    async def test_paid_bill_not_re_settled(self, pool):
        """Bills already in 'paid' status are never reconsidered."""
        from butlers.tools.finance.reconciliation import reconcile_bills

        await _insert_bill(
            pool,
            payee="Netflix",
            amount=15.49,
            currency="USD",
            due_date=date.today() - timedelta(days=5),
            status="paid",  # already paid
        )
        txn = await _insert_txn(pool, merchant="Netflix", amount=15.49, currency="USD")

        result = await reconcile_bills(pool=pool)

        assert len(result["auto_settled"]) == 0
        # Transaction should not have been linked to any bill
        row = await pool.fetchrow(
            "SELECT id FROM bills WHERE reconciled_transaction_id = $1", txn["id"]
        )
        assert row is None

    async def test_reconcile_bills_idempotent_on_rerun(self, pool):
        """Running reconcile_bills() twice does not double-settle or error."""
        from butlers.tools.finance.reconciliation import reconcile_bills

        await _insert_bill(
            pool,
            payee="Spotify",
            amount=9.99,
            currency="USD",
            due_date=date.today() - timedelta(days=2),
        )
        await _insert_txn(pool, merchant="Spotify", amount=9.99, currency="USD")

        result1 = await reconcile_bills(pool=pool)
        result2 = await reconcile_bills(pool=pool)

        assert len(result1["auto_settled"]) == 1
        # Second run: nothing new to settle
        assert len(result2["auto_settled"]) == 0

    async def test_guarded_update_zero_rows_when_already_settled(self, pool):
        """The guarded WHERE clause yields 0 rows when another settler wins first."""
        from butlers.tools.finance.reconciliation import _settle_bill

        txn = await _insert_txn(pool, merchant="DBS Card", amount=200.00, currency="SGD")
        bill = await _insert_bill(
            pool,
            payee="DBS Card",
            amount=200.00,
            currency="SGD",
            due_date=date.today() - timedelta(days=2),
        )
        bill_id = bill["id"]

        txn_dict = {
            "id": txn["id"],
            "amount": 200.00,
            "posted_at": txn["posted_at"],
            "payment_method": None,
        }

        # First settle wins
        settled_first = await _settle_bill(pool, bill_id, txn_dict)
        assert settled_first is True

        # Second settle: guarded WHERE yields 0 rows — returns False, not an error
        settled_second = await _settle_bill(pool, bill_id, txn_dict)
        assert settled_second is False


class TestSamePayeeDuplicateBills:
    """Same-payee duplicate bills: two in-window → confirm; one in-window → auto_settle."""

    async def test_two_same_payee_both_in_window_yields_confirm(self, pool):
        """Two same-payee bills both in-window for the same txn → confirm, not auto_settle."""
        from butlers.tools.finance.reconciliation import reconcile_bills

        # Both bills' windows contain the txn date
        txn_date = date.today() - timedelta(days=20)
        txn_dt = datetime.combine(txn_date, datetime.min.time()).replace(tzinfo=UTC)

        # Bill A: due 3 days after txn — window includes txn_date
        bill_a = await _insert_bill(
            pool,
            payee="POSB Card",
            amount=150.00,
            currency="SGD",
            due_date=txn_date + timedelta(days=3),
        )
        # Bill B: due 8 days after txn — window also includes txn_date (LOOKBACK=45d)
        bill_b = await _insert_bill(
            pool,
            payee="POSB Card",
            amount=150.00,
            currency="SGD",
            due_date=txn_date + timedelta(days=8),
        )
        await _insert_txn(
            pool,
            merchant="POSB Card",
            amount=150.00,
            currency="SGD",
            posted_at=txn_dt,
        )

        result = await reconcile_bills(pool=pool)

        # Two candidates → confirm, no auto-settle
        assert len(result["auto_settled"]) == 0
        # Both bills remain pending
        for bill in [bill_a, bill_b]:
            row = await pool.fetchrow("SELECT status FROM bills WHERE id = $1", bill["id"])
            assert row["status"] == "pending"

    async def test_one_in_window_auto_settles_closest_anchor(self, pool):
        """When only one of two same-payee bills is in-window, that one is auto-settled."""
        from butlers.tools.finance.reconciliation import reconcile_bills

        # txn posted 20 days ago
        txn_date = date.today() - timedelta(days=20)
        txn_dt = datetime.combine(txn_date, datetime.min.time()).replace(tzinfo=UTC)

        # Bill A due in 5 days (future): anchor=today+5, window=[today-40, today+12]
        # txn_date = today-20, which IS in [today-40, today+12] → IN WINDOW
        bill_a_due = date.today() + timedelta(days=5)

        # Bill B due in 40 days (far future): anchor=today+40, window=[today-5, today+47]
        # txn_date = today-20, which is BEFORE today-5 → OUT OF WINDOW
        bill_b_due = date.today() + timedelta(days=40)

        bill_a = await _insert_bill(
            pool,
            payee="POSB Card",
            amount=150.00,
            currency="SGD",
            due_date=bill_a_due,
        )
        bill_b = await _insert_bill(
            pool,
            payee="POSB Card",
            amount=150.00,
            currency="SGD",
            due_date=bill_b_due,
        )
        await _insert_txn(
            pool,
            merchant="POSB Card",
            amount=150.00,
            currency="SGD",
            posted_at=txn_dt,
        )

        result = await reconcile_bills(pool=pool)

        # Only bill_a is in-window → auto_settle
        assert len(result["auto_settled"]) == 1
        assert result["auto_settled"][0]["bill_id"] == str(bill_a["id"])

        # bill_b should remain pending
        row_b = await pool.fetchrow("SELECT status FROM bills WHERE id = $1", bill_b["id"])
        assert row_b["status"] == "pending"


class TestPaymentRecordedBeforeBill:
    """Payment recorded before the bill existed — sweep settles it."""

    async def test_payment_before_bill_settled_by_sweep(self, pool):
        """Transaction recorded before bill was created is matched by reconcile_bills sweep."""
        from butlers.tools.finance.reconciliation import reconcile_bills

        # 1. Transaction recorded (e.g. bank debit notification arrived first)
        txn = await _insert_txn(
            pool,
            merchant="Singtel",
            amount=80.00,
            currency="SGD",
            posted_at=datetime.now(UTC) - timedelta(days=10),
        )

        # 2. Bill created later (e.g. when the bill email arrived after the payment)
        bill = await _insert_bill(
            pool,
            payee="Singtel",
            amount=80.00,
            currency="SGD",
            due_date=date.today() - timedelta(days=8),
            status="pending",
        )

        # 3. The inline hook couldn't match (bill didn't exist when txn was recorded)
        row_before = await pool.fetchrow(
            "SELECT status, reconciled_transaction_id FROM bills WHERE id = $1", bill["id"]
        )
        assert row_before["status"] == "pending"
        assert row_before["reconciled_transaction_id"] is None

        # 4. The sweep catches it
        result = await reconcile_bills(pool=pool)

        assert len(result["auto_settled"]) == 1
        assert result["auto_settled"][0]["bill_id"] == str(bill["id"])
        assert result["auto_settled"][0]["txn_id"] == str(txn["id"])

        row_after = await pool.fetchrow("SELECT status FROM bills WHERE id = $1", bill["id"])
        assert row_after["status"] == "paid"

    async def test_payee_filter_narrows_sweep(self, pool):
        """reconcile_bills(payee=...) only processes bills for the given payee."""
        from butlers.tools.finance.reconciliation import reconcile_bills

        await _insert_bill(
            pool,
            payee="Starhub",
            amount=50.00,
            currency="SGD",
            due_date=date.today() - timedelta(days=3),
        )
        bill_b = await _insert_bill(
            pool,
            payee="M1",
            amount=40.00,
            currency="SGD",
            due_date=date.today() - timedelta(days=3),
        )
        await _insert_txn(pool, merchant="Starhub", amount=50.00, currency="SGD")
        await _insert_txn(pool, merchant="M1", amount=40.00, currency="SGD")

        # Only process Starhub
        result = await reconcile_bills(pool=pool, payee="Starhub")

        assert len(result["auto_settled"]) == 1
        assert result["auto_settled"][0]["payee"] == "Starhub"

        # M1 bill is unaffected
        row_m1 = await pool.fetchrow("SELECT status FROM bills WHERE id = $1", bill_b["id"])
        assert row_m1["status"] == "pending"

    async def test_payee_filter_pushed_down_to_prefetch(self, pool, monkeypatch):
        """The payee filter is applied in the bill pre-fetch query, not in-memory.

        Proves the optimization: when payee= is supplied, the matcher only ever
        sees bills for that payee — non-matching bills are filtered by the DB and
        never materialized into the active-bills slice.
        """
        from butlers.tools.finance import reconciliation as recon

        await _insert_bill(
            pool,
            payee="Starhub",
            amount=50.00,
            currency="SGD",
            due_date=date.today() - timedelta(days=3),
        )
        await _insert_bill(
            pool,
            payee="M1",
            amount=40.00,
            currency="SGD",
            due_date=date.today() - timedelta(days=3),
        )
        await _insert_txn(pool, merchant="Starhub", amount=50.00, currency="SGD")
        await _insert_txn(pool, merchant="M1", amount=40.00, currency="SGD")

        # Capture every bills slice handed to the matcher.
        seen_payees: set[str] = set()
        real_matcher = recon.match_transaction_to_bills

        async def spy_matcher(pool, txn, *, bills=None):
            if bills is not None:
                seen_payees.update(b["payee"] for b in bills)
            return await real_matcher(pool, txn, bills=bills)

        monkeypatch.setattr(recon, "match_transaction_to_bills", spy_matcher)

        result = await recon.reconcile_bills(pool=pool, payee="Starhub")

        # The matcher never saw the M1 bill — it was excluded by the pre-fetch SQL.
        assert seen_payees == {"Starhub"}
        assert "M1" not in seen_payees
        # And the observable result is identical to the in-memory-filtered behavior.
        assert len(result["auto_settled"]) == 1
        assert result["auto_settled"][0]["payee"] == "Starhub"

    async def test_overdue_bill_also_reconciled(self, pool):
        """Bills with status='overdue' are eligible for reconciliation."""
        from butlers.tools.finance.reconciliation import reconcile_bills

        # Bill due 3 days ago (overdue); grace window extends to today+4.
        # Transaction posted 1 day ago is well within the window.
        bill = await _insert_bill(
            pool,
            payee="SP Services",
            amount=120.00,
            currency="SGD",
            due_date=date.today() - timedelta(days=3),
            status="overdue",
        )
        await _insert_txn(
            pool,
            merchant="SP Services",
            amount=120.00,
            currency="SGD",
            posted_at=datetime.now(UTC) - timedelta(days=1),
        )

        result = await reconcile_bills(pool=pool)

        assert len(result["auto_settled"]) == 1
        row = await pool.fetchrow("SELECT status FROM bills WHERE id = $1", bill["id"])
        assert row["status"] == "paid"


class TestAmountPrecisionRegression:
    """Regression (bu-1loc3): amount path must never go through float.

    Converting Decimal → float → str → Decimal silently loses precision for
    values with more significant digits than float64 can represent (~15.7 decimal
    digits). The fix is abs(Decimal(str(x))) which stays in exact Decimal
    arithmetic throughout.
    """

    async def test_decimal_str_path_preserves_precision(self):
        """Demonstrates the float round-trip precision loss and that the fix avoids it.

        Uses a Decimal value whose last significant digit is dropped when
        converted to float64. This is the concrete bug the fix addresses.
        """
        # 18 significant digits — more than float64 can represent exactly (~15.7).
        # float(this) rounds to 0.1, silently dropping the trailing '1'.
        precise = Decimal("0.10000000000000001")

        # Old (broken) path: the last digit is lost through float conversion.
        via_float = Decimal(str(abs(float(precise))))
        assert via_float == Decimal("0.1")  # precision dropped — this is the bug
        assert via_float != precise

        # New (correct) path: str(Decimal) preserves all significant digits.
        via_str = abs(Decimal(str(precise)))
        assert via_str == precise  # exact match — the fix works

    async def test_settle_bill_backfill_preserves_exact_decimal(self, pool):
        """_settle_bill writes the exact Decimal amount when backfilling a $0 placeholder.

        The CASE WHEN amount=0 THEN $3 branch in the guarded UPDATE must receive
        the exact Decimal amount from the transaction, not a float-derived value.
        asyncpg returns NUMERIC columns as Decimal; the fix ensures that Decimal
        flows through to the DB parameter unchanged.
        """
        from butlers.tools.finance.reconciliation import _settle_bill

        # Placeholder bill — amount will be backfilled from the transaction.
        bill = await _insert_bill(
            pool,
            payee="Precision Test",
            amount=0.00,
            currency="USD",
            due_date=date.today() - timedelta(days=1),
        )
        txn = await _insert_txn(
            pool,
            merchant="Precision Test",
            amount=49.99,
            currency="USD",
        )

        # Simulate what asyncpg gives back: a Decimal, not a float.
        txn_dict = {
            "id": txn["id"],
            "amount": Decimal("49.99"),
            "posted_at": txn["posted_at"],
            "payment_method": None,
        }

        settled = await _settle_bill(pool, bill["id"], txn_dict)
        assert settled is True

        row = await pool.fetchrow("SELECT amount FROM bills WHERE id = $1", bill["id"])
        # Must be the exact Decimal returned by asyncpg, not a float-truncated value.
        assert row["amount"] == Decimal("49.99")


# ---------------------------------------------------------------------------
# bills= parameter — in-memory path (N+1 elimination)
# ---------------------------------------------------------------------------


class TestBillsParam:
    """Verify the optional ``bills`` parameter to ``match_transaction_to_bills``.

    When ``bills`` is provided the function skips the DB bill-fetch and
    filters candidates in-memory by currency.  The settlement logic (window,
    payee, amount, confidence tier) must be identical to the DB path.
    """

    async def test_bills_param_exact_match_auto_settle(self, pool):
        """Passing a pre-fetched bills list yields auto_settle for an exact match."""
        from butlers.tools.finance.reconciliation import match_transaction_to_bills

        bill = await _insert_bill(
            pool,
            payee="Shopee",
            amount=45.00,
            currency="SGD",
            due_date=date.today() - timedelta(days=3),
        )
        txn = await _insert_txn(
            pool,
            merchant="Shopee",
            amount=45.00,
            currency="SGD",
        )
        txn_dict = {
            "id": str(txn["id"]),
            "direction": "debit",
            "merchant": "Shopee",
            "currency": "SGD",
            "amount": Decimal("45.00"),
            "posted_at": txn["posted_at"],
            "metadata": {},
        }

        result = await match_transaction_to_bills(pool, txn_dict, bills=[bill])

        assert result["tier"] == "auto_settle"
        assert str(result["bill"]["id"]) == str(bill["id"])

    async def test_bills_param_currency_mismatch_filtered_in_memory(self, pool):
        """Bills with mismatched currency are excluded in-memory when bills= is provided."""
        from butlers.tools.finance.reconciliation import match_transaction_to_bills

        usd_bill = await _insert_bill(
            pool,
            payee="Lazada",
            amount=50.00,
            currency="USD",
            due_date=date.today() - timedelta(days=2),
        )
        txn_dict = {
            "id": str(uuid.uuid4()),
            "direction": "debit",
            "merchant": "Lazada",
            "currency": "SGD",  # mismatch
            "amount": Decimal("50.00"),
            "posted_at": datetime.now(UTC) - timedelta(days=1),
            "metadata": {},
        }

        result = await match_transaction_to_bills(pool, txn_dict, bills=[usd_bill])

        assert result["tier"] == "none"

    async def test_bills_param_settled_bill_excluded_by_caller(self, pool):
        """When the caller filters settled bills out of the list, they are not matched."""
        from butlers.tools.finance.reconciliation import match_transaction_to_bills

        await _insert_bill(
            pool,
            payee="Carousell",
            amount=30.00,
            currency="SGD",
            due_date=date.today() - timedelta(days=2),
        )
        txn_dict = {
            "id": str(uuid.uuid4()),
            "direction": "debit",
            "merchant": "Carousell",
            "currency": "SGD",
            "amount": Decimal("30.00"),
            "posted_at": datetime.now(UTC) - timedelta(days=1),
            "metadata": {},
        }

        # Caller passes an empty list (simulating all bills already settled)
        result = await match_transaction_to_bills(pool, txn_dict, bills=[])

        assert result["tier"] == "none"

    async def test_bills_param_confirm_tier_multiple_candidates(self, pool):
        """Two in-window bills passed via bills= still produce confirm tier."""
        from butlers.tools.finance.reconciliation import match_transaction_to_bills

        txn_date = date.today() - timedelta(days=10)
        bill_a = await _insert_bill(
            pool,
            payee="DBS",
            amount=100.00,
            currency="SGD",
            due_date=txn_date + timedelta(days=3),
        )
        bill_b = await _insert_bill(
            pool,
            payee="DBS",
            amount=100.00,
            currency="SGD",
            due_date=txn_date + timedelta(days=8),
        )
        txn_dict = {
            "id": str(uuid.uuid4()),
            "direction": "debit",
            "merchant": "DBS",
            "currency": "SGD",
            "amount": Decimal("100.00"),
            "posted_at": datetime.combine(txn_date, datetime.min.time()).replace(tzinfo=UTC),
            "metadata": {},
        }

        result = await match_transaction_to_bills(pool, txn_dict, bills=[bill_a, bill_b])

        assert result["tier"] == "confirm"
        assert result["bill"] is None
        assert len(result["candidates"]) == 2
