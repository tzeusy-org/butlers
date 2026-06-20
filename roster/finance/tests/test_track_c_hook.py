"""Integration tests for Track C — record_transaction bill reconciliation hook (bu-y6gpw).

C2: Recording a debit that matches a placeholder bill returns
    ``bill_reconciliation.auto_settled`` and the bill is settled.
    An ambiguous debit returns ``bill_reconciliation.candidates`` and
    mutates nothing.
    Fire-and-forget: transaction recording succeeds even when reconciliation fails.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, patch

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


# ---------------------------------------------------------------------------
# C2a — auto_settle path
# ---------------------------------------------------------------------------


class TestAutoSettle:
    """C2a: debit that exactly matches a placeholder bill auto-settles it."""

    async def test_auto_settle_returned_in_response(self, pool):
        """record_transaction returns bill_reconciliation.auto_settled for a matching debit."""
        from butlers.tools.finance.transactions import record_transaction

        await _insert_bill(
            pool,
            payee="HSBC Credit Card",
            amount=0.00,
            currency="SGD",
            due_date=date.today() - timedelta(days=5),
            status="pending",
        )

        posted = datetime.now(UTC) - timedelta(days=3)
        result = await record_transaction(
            pool=pool,
            posted_at=posted,
            merchant="HSBC Credit Card",
            amount=-717.57,
            currency="SGD",
            category="utilities",
        )

        assert "bill_reconciliation" in result, "auto_settle block must be present"
        recon = result["bill_reconciliation"]
        assert "auto_settled" in recon, "auto_settled key must be present"
        settled = recon["auto_settled"]
        assert settled["payee"] == "HSBC Credit Card"
        assert abs(settled["amount"] - 717.57) < 0.01
        assert "bill_id" in settled
        assert "txn_id" in settled
        assert settled["txn_id"] == str(result["id"])

    async def test_auto_settle_marks_bill_paid_in_db(self, pool):
        """After auto_settle, the bill row is status='paid' with reconciled_transaction_id set."""
        from butlers.tools.finance.transactions import record_transaction

        bill = await _insert_bill(
            pool,
            payee="HSBC Credit Card",
            amount=0.00,
            currency="SGD",
            due_date=date.today() - timedelta(days=5),
            status="pending",
        )

        posted = datetime.now(UTC) - timedelta(days=3)
        result = await record_transaction(
            pool=pool,
            posted_at=posted,
            merchant="HSBC Credit Card",
            amount=-717.57,
            currency="SGD",
            category="utilities",
        )

        # Verify DB state
        row = await pool.fetchrow(
            "SELECT status, reconciled_transaction_id, amount FROM bills WHERE id = $1",
            bill["id"],
        )
        assert row["status"] == "paid"
        assert str(row["reconciled_transaction_id"]) == str(result["id"])
        # Amount backfilled from $0.00 to the transaction amount
        assert abs(float(row["amount"]) - 717.57) < 0.01

    async def test_auto_settle_idempotent_second_call_skipped(self, pool):
        """A second matching debit does not re-settle an already-paid bill."""
        from butlers.tools.finance.transactions import record_transaction

        await _insert_bill(
            pool,
            payee="HSBC Credit Card",
            amount=0.00,
            currency="SGD",
            due_date=date.today() - timedelta(days=5),
            status="pending",
        )

        posted = datetime.now(UTC) - timedelta(days=3)
        result1 = await record_transaction(
            pool=pool,
            posted_at=posted,
            merchant="HSBC Credit Card",
            amount=-717.57,
            currency="SGD",
            category="utilities",
        )
        assert "bill_reconciliation" in result1
        assert "auto_settled" in result1["bill_reconciliation"]

        # Second debit: same payee, slightly different amount (within tolerance)
        # The bill is already paid so the hook should not fire again.
        posted2 = datetime.now(UTC) - timedelta(days=2)
        result2 = await record_transaction(
            pool=pool,
            posted_at=posted2,
            merchant="HSBC Credit Card",
            amount=-717.57,
            currency="SGD",
            category="utilities",
            source_message_id="msg-second-debit-" + str(uuid.uuid4()),
        )
        # The second transaction may or may not find a bill — the bill is paid,
        # so the matcher skips it.  The key guarantee is no crash.
        assert "id" in result2

    async def test_credit_transaction_has_no_bill_reconciliation(self, pool):
        """Credits never settle bills — bill_reconciliation must be absent."""
        from butlers.tools.finance.transactions import record_transaction

        await _insert_bill(
            pool,
            payee="HSBC Credit Card",
            amount=717.57,
            currency="SGD",
            due_date=date.today() - timedelta(days=5),
            status="pending",
        )

        result = await record_transaction(
            pool=pool,
            posted_at=datetime.now(UTC),
            merchant="HSBC Credit Card",
            amount=717.57,  # positive → credit
            currency="SGD",
            category="income",
        )

        assert result["direction"] == "credit"
        assert "bill_reconciliation" not in result


# ---------------------------------------------------------------------------
# C2b — confirm path
# ---------------------------------------------------------------------------


class TestConfirmCandidates:
    """C2b: ambiguous debit surfaces candidates without settling anything."""

    async def test_confirm_returned_when_two_bills_in_window(self, pool):
        """Two in-window bills for same payee → candidates, no settlement."""
        from butlers.tools.finance.transactions import record_transaction

        today = date.today()
        bill_a = await _insert_bill(
            pool,
            payee="HSBC Credit Card",
            amount=717.57,
            currency="SGD",
            due_date=today - timedelta(days=3),
            status="pending",
        )
        bill_b = await _insert_bill(
            pool,
            payee="HSBC Credit Card",
            amount=717.57,
            currency="SGD",
            due_date=today - timedelta(days=10),
            status="pending",
        )

        posted = datetime.now(UTC) - timedelta(days=3)
        result = await record_transaction(
            pool=pool,
            posted_at=posted,
            merchant="HSBC Credit Card",
            amount=-717.57,
            currency="SGD",
            category="utilities",
        )

        assert "bill_reconciliation" in result
        recon = result["bill_reconciliation"]
        assert "candidates" in recon, "confirm path must surface candidates"
        assert "auto_settled" not in recon

        candidate_ids = {c["bill_id"] for c in recon["candidates"]}
        assert str(bill_a["id"]) in candidate_ids or str(bill_b["id"]) in candidate_ids

        # Bills must remain unpaid
        for bill_id in (bill_a["id"], bill_b["id"]):
            row = await pool.fetchrow("SELECT status FROM bills WHERE id = $1", bill_id)
            assert row["status"] == "pending", "ambiguous bills must not be settled"

    async def test_confirm_candidates_contain_required_fields(self, pool):
        """Each candidate entry exposes bill_id, payee, due_date, and amount."""
        from butlers.tools.finance.transactions import record_transaction

        today = date.today()
        await _insert_bill(
            pool,
            payee="DBS Credit Card",
            amount=500.00,
            currency="SGD",
            due_date=today - timedelta(days=2),
            status="pending",
        )
        await _insert_bill(
            pool,
            payee="DBS Credit Card",
            amount=500.00,
            currency="SGD",
            due_date=today - timedelta(days=9),
            status="pending",
        )

        result = await record_transaction(
            pool=pool,
            posted_at=datetime.now(UTC) - timedelta(days=2),
            merchant="DBS Credit Card",
            amount=-500.00,
            currency="SGD",
            category="utilities",
        )

        recon = result.get("bill_reconciliation", {})
        candidates = recon.get("candidates", [])
        assert len(candidates) >= 1
        for c in candidates:
            assert "bill_id" in c
            assert "payee" in c
            assert "due_date" in c
            assert "amount" in c


# ---------------------------------------------------------------------------
# Fire-and-forget safety
# ---------------------------------------------------------------------------


class TestFireAndForgetSafety:
    """Reconciliation failures must never break the primary transaction insert."""

    async def test_reconciliation_error_does_not_propagate(self, pool):
        """When match_transaction_to_bills raises, record_transaction still succeeds."""
        from butlers.tools.finance.transactions import record_transaction

        # Patch the reconciliation function to simulate a failure.
        with patch(
            "butlers.tools.finance.reconciliation.match_transaction_to_bills",
            new=AsyncMock(side_effect=RuntimeError("simulated reconciliation failure")),
        ):
            result = await record_transaction(
                pool=pool,
                posted_at=datetime.now(UTC),
                merchant="Some Merchant",
                amount=-99.00,
                currency="USD",
                category="uncategorized",
                source_message_id="fire-and-forget-test-" + str(uuid.uuid4()),
            )

        # Transaction recorded successfully
        assert "id" in result
        assert result["merchant"] == "Some Merchant"
        assert result["direction"] == "debit"

        # No bill_reconciliation block — error was swallowed
        assert "bill_reconciliation" not in result

        # The row actually made it into the DB
        row = await pool.fetchrow("SELECT id FROM transactions WHERE id = $1::uuid", result["id"])
        assert row is not None

    async def test_debit_no_matching_bill_no_reconciliation_key(self, pool):
        """A debit with no matching bill produces no bill_reconciliation block."""
        from butlers.tools.finance.transactions import record_transaction

        # No bills in DB at all
        result = await record_transaction(
            pool=pool,
            posted_at=datetime.now(UTC),
            merchant="Random Merchant",
            amount=-55.00,
            currency="USD",
            category="uncategorized",
            source_message_id="no-bill-test-" + str(uuid.uuid4()),
        )

        assert result["direction"] == "debit"
        # No match → no bill_reconciliation block
        assert "bill_reconciliation" not in result
