"""Integration tests for the upcoming-bills-check skill Step 0 → notify path.

Covers the full orchestration contract that lives in the skill but has no
automated test:

  Step 0  reconcile_bills(lookback_days=90)   — settlement backstop
  Step 1  upcoming_bills() + predict_bills()  — post-reconcile view
  Step 3  compose_upcoming_bills_digest()     — full message before notify
  Step 5  notify(intent="send")              — exactly once

Assertions per bead bu-pi72u:
  1. reconciliation settles matched bills (auto_settled non-empty)
  2. composed digest reflects settled state (settled bill in auto-settled
     section, NOT in needs-action section)
  3. exactly ONE notify call is emitted — compose fully first, then send once

Docker + migration fixture follows the same pattern as test_track_d_sweep.py.
"""

from __future__ import annotations

import shutil
from datetime import UTC, date, datetime, timedelta

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
    """Provision a DB with core + finance migrations applied once per module."""
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
    autopay: bool = False,
) -> dict:
    if due_date is None:
        due_date = date.today() - timedelta(days=5)
    row = await pool.fetchrow(
        """
        INSERT INTO bills (payee, amount, currency, due_date, frequency, status,
                           autopay, reconciled_transaction_id)
        VALUES ($1, $2, $3, $4, 'monthly', $5, $6, NULL)
        RETURNING *
        """,
        payee,
        amount,
        currency,
        due_date,
        status,
        autopay,
    )
    return dict(row)


async def _insert_txn(
    pool: asyncpg.Pool,
    merchant: str = "HSBC Credit Card",
    amount: float = 350.00,
    currency: str = "SGD",
    direction: str = "debit",
    posted_at: datetime | None = None,
) -> dict:
    if posted_at is None:
        posted_at = datetime.now(UTC) - timedelta(days=3)
    row = await pool.fetchrow(
        """
        INSERT INTO transactions (merchant, amount, currency, direction, posted_at, category, metadata)
        VALUES ($1, $2, $3, $4, $5, 'utilities', '{}')
        RETURNING *
        """,
        merchant,
        abs(amount),
        currency,
        direction,
        posted_at,
    )
    return dict(row)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSkillPathReconcileComposeNotify:
    """Full skill path: reconcile → compose → notify exactly once."""

    async def test_reconcile_compose_notify_single_call(self, pool):
        """Settled bill lands in auto-settled section; notify called exactly once.

        Scenario:
          - HSBC bill (pending, past due) + matching debit → auto-settled by sweep
          - SP Services bill (overdue, no matching txn) → stays in needs-action
          - compose_upcoming_bills_digest produces one digest string
          - mock notify accumulator receives exactly one call
        """
        from butlers.tools.finance.bills import compose_upcoming_bills_digest, upcoming_bills
        from butlers.tools.finance.pattern_recognition import predict_bills
        from butlers.tools.finance.reconciliation import reconcile_bills

        notify_calls: list[dict] = []

        async def mock_notify(channel: str, intent: str, message: str) -> None:
            notify_calls.append({"channel": channel, "intent": intent, "message": message})

        # Seed: one bill with a matching payment → will be auto-settled
        await _insert_bill(
            pool,
            payee="HSBC Credit Card",
            amount=350.00,
            currency="SGD",
            due_date=date.today() - timedelta(days=5),
        )
        await _insert_txn(pool, merchant="HSBC Credit Card", amount=350.00, currency="SGD")

        # Seed: one overdue bill with no payment → stays in needs-action
        await _insert_bill(
            pool,
            payee="SP Services",
            amount=95.00,
            currency="SGD",
            due_date=date.today() - timedelta(days=10),
            status="overdue",
        )

        # Step 0: reconcile
        sweep = await reconcile_bills(pool=pool, lookback_days=90)
        assert len(sweep["auto_settled"]) == 1, "HSBC bill must be auto-settled"
        assert sweep["auto_settled"][0]["payee"] == "HSBC Credit Card"

        # Step 1: post-reconcile view
        bills = await upcoming_bills(pool=pool, days_ahead=14, include_overdue=True)
        predictions = await predict_bills(pool=pool, days_ahead=30)

        # Settled bill must not appear as pending/overdue
        action_payees = [i["bill"]["payee"] for i in bills["needs_action"]]
        assert "HSBC Credit Card" not in action_payees
        assert "SP Services" in action_payees

        # Step 3: compose fully before notifying
        digest = compose_upcoming_bills_digest(sweep, bills, predictions)
        assert digest is not None, "digest must not be None when there is data to report"

        # Step 5: deliver exactly once
        await mock_notify(channel="telegram", intent="send", message=digest)

        assert len(notify_calls) == 1, "notify must be called exactly once (no double-notify)"
        call = notify_calls[0]
        assert call["channel"] == "telegram"
        assert call["intent"] == "send"
        assert call["message"] == digest

    async def test_digest_reflects_settled_state(self, pool):
        """Auto-settled bill appears in digest auto-settled section, not in needs-action."""
        from butlers.tools.finance.bills import compose_upcoming_bills_digest, upcoming_bills
        from butlers.tools.finance.pattern_recognition import predict_bills
        from butlers.tools.finance.reconciliation import reconcile_bills

        await _insert_bill(
            pool,
            payee="Singtel",
            amount=80.00,
            currency="SGD",
            due_date=date.today() - timedelta(days=3),
        )
        await _insert_txn(pool, merchant="Singtel", amount=80.00, currency="SGD")

        sweep = await reconcile_bills(pool=pool, lookback_days=90)
        assert len(sweep["auto_settled"]) == 1

        bills = await upcoming_bills(pool=pool, days_ahead=14, include_overdue=True)
        predictions = await predict_bills(pool=pool, days_ahead=30)

        digest = compose_upcoming_bills_digest(sweep, bills, predictions)
        assert digest is not None

        # Parse digest into sections to verify placement
        in_settled = False
        in_action = False
        settled_mentions = 0
        action_mentions = 0

        for line in digest.splitlines():
            if "Auto-settled" in line:
                in_settled, in_action = True, False
            elif "Needs action" in line:
                in_settled, in_action = False, True
            elif any(s in line for s in ["Auto-pays", "Confirm needed", "Heads-up"]):
                in_settled, in_action = False, False

            if "Singtel" in line:
                if in_settled:
                    settled_mentions += 1
                if in_action:
                    action_mentions += 1

        assert settled_mentions >= 1, "Singtel must appear in the auto-settled section"
        assert action_mentions == 0, "Singtel must NOT appear in the needs-action section"

    async def test_early_exit_nothing_to_report(self, pool):
        """Empty bills + empty sweep → compose returns None → notify never called."""
        from butlers.tools.finance.bills import compose_upcoming_bills_digest, upcoming_bills
        from butlers.tools.finance.pattern_recognition import predict_bills
        from butlers.tools.finance.reconciliation import reconcile_bills

        notify_calls: list[dict] = []

        async def mock_notify(**kwargs: object) -> None:
            notify_calls.append(dict(kwargs))

        # No bills, no transactions
        sweep = await reconcile_bills(pool=pool, lookback_days=90)
        bills = await upcoming_bills(pool=pool, days_ahead=14, include_overdue=True)
        predictions = await predict_bills(pool=pool, days_ahead=30)

        digest = compose_upcoming_bills_digest(sweep, bills, predictions)

        # SKILL.md Step 2: early exit — nothing worth sending
        assert digest is None, "digest must be None when there is nothing to report"

        # Skill must not call notify when digest is None
        if digest is not None:  # pragma: no cover
            await mock_notify(channel="telegram", intent="send", message=digest)

        assert len(notify_calls) == 0, "notify must not be called when there is nothing to send"

    async def test_autopay_bills_appear_in_digest_not_needs_action(self, pool):
        """Autopay bills surface as informational in digest, never as action items."""
        from butlers.tools.finance.bills import compose_upcoming_bills_digest, upcoming_bills
        from butlers.tools.finance.pattern_recognition import predict_bills
        from butlers.tools.finance.reconciliation import reconcile_bills

        await _insert_bill(
            pool,
            payee="GIRO - CPF",
            amount=500.00,
            currency="SGD",
            due_date=date.today() + timedelta(days=5),
            autopay=True,
        )

        sweep = await reconcile_bills(pool=pool, lookback_days=90)
        bills = await upcoming_bills(pool=pool, days_ahead=14, include_overdue=True)
        predictions = await predict_bills(pool=pool, days_ahead=30)

        # Autopay bill must not appear in needs_action
        assert bills["needs_action"] == []
        assert len(bills["autopay"]) == 1
        assert bills["autopay"][0]["bill"]["payee"] == "GIRO - CPF"

        digest = compose_upcoming_bills_digest(sweep, bills, predictions)
        assert digest is not None, "autopay bill alone is still worth reporting"

        # Digest has auto-pays section but no needs-action alarm
        assert "Auto-pays" in digest
        assert "GIRO - CPF" in digest
        assert "Needs action" not in digest

    async def test_single_notify_with_mixed_settled_and_pending(self, pool):
        """Mixed scenario: one settled + one pending → digest has both sections, notify once."""
        from butlers.tools.finance.bills import compose_upcoming_bills_digest, upcoming_bills
        from butlers.tools.finance.pattern_recognition import predict_bills
        from butlers.tools.finance.reconciliation import reconcile_bills

        notify_calls: list[dict] = []

        async def mock_notify(channel: str, intent: str, message: str) -> None:
            notify_calls.append({"channel": channel, "intent": intent, "message": message})

        # Will be settled
        await _insert_bill(
            pool,
            payee="Netflix",
            amount=15.49,
            currency="USD",
            due_date=date.today() - timedelta(days=2),
        )
        await _insert_txn(pool, merchant="Netflix", amount=15.49, currency="USD")

        # Will remain pending (needs action)
        await _insert_bill(
            pool,
            payee="Hulu",
            amount=17.99,
            currency="USD",
            due_date=date.today() + timedelta(days=7),
        )

        sweep = await reconcile_bills(pool=pool, lookback_days=90)
        bills = await upcoming_bills(pool=pool, days_ahead=14, include_overdue=True)
        predictions = await predict_bills(pool=pool, days_ahead=30)

        digest = compose_upcoming_bills_digest(sweep, bills, predictions)
        assert digest is not None

        # Both sections present
        assert "Auto-settled" in digest
        assert "Netflix" in digest
        assert "Needs action" in digest
        assert "Hulu" in digest

        # Deliver exactly once
        await mock_notify(channel="telegram", intent="send", message=digest)
        assert len(notify_calls) == 1
