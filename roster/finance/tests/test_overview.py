"""Tests for butlers.tools.finance.overview — financial overview tools.

Covers:
- net_worth_snapshot: upsert, date defaults, credit/asset distinction
- net_worth_history: carry-forward logic, multi-account, empty data
- cash_flow: income vs. expenses, savings_rate, optional breakdown, periods
- subscription_audit: tracked + detected entries, annual cost, empty tables
- flag_tax_deductible: category mapping, custom categories, year filter, summary
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


async def _insert_transaction(
    pool,
    *,
    merchant: str = "ACME Corp",
    amount: str = "50.00",
    currency: str = "USD",
    direction: str = "debit",
    category: str = "general",
    posted_at: datetime | None = None,
    deleted_at: datetime | None = None,
) -> None:
    if posted_at is None:
        posted_at = datetime.now(UTC)
    await pool.execute(
        """
        INSERT INTO transactions
            (merchant, amount, currency, direction, category, posted_at, deleted_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        merchant,
        Decimal(amount),
        currency,
        direction,
        category,
        posted_at,
        deleted_at,
    )


async def _insert_account(
    pool,
    *,
    institution: str = "Chase",
    account_type: str = "checking",
    name: str = "Checking",
    currency: str = "USD",
) -> str:
    row = await pool.fetchrow(
        """
        INSERT INTO accounts (institution, type, name, currency)
        VALUES ($1, $2, $3, $4)
        RETURNING id
        """,
        institution,
        account_type,
        name,
        currency,
    )
    return str(row["id"])


async def _insert_snapshot(
    pool,
    *,
    account_id: str,
    balance: str,
    currency: str = "USD",
    as_of_date: date,
) -> None:
    await pool.execute(
        """
        INSERT INTO balance_snapshots (account_id, balance, currency, as_of_date, source)
        VALUES ($1::uuid, $2, $3, $4, 'manual')
        ON CONFLICT (account_id, as_of_date)
        DO UPDATE SET balance = EXCLUDED.balance
        """,
        account_id,
        Decimal(balance),
        currency,
        as_of_date,
    )


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def finance_pool(db):
    """A core+finance clone; seeds the non-system 'general' category used below."""
    p = await db("core", "finance")
    await p.execute(
        "INSERT INTO categories (name, display_name) VALUES ('general', 'General') "
        "ON CONFLICT (name) DO NOTHING"
    )
    return p


# ---------------------------------------------------------------------------
# net_worth_snapshot
# ---------------------------------------------------------------------------


class TestNetWorthSnapshot:
    async def test_create_new_snapshot(self, finance_pool):
        """net_worth_snapshot inserts a row and returns the snapshot dict."""
        from butlers.tools.finance.overview import net_worth_snapshot

        result = await net_worth_snapshot(
            finance_pool,
            account="Checking",
            institution="Chase",
            balance=5000.00,
        )

        assert result["account"] == "Checking"
        assert result["institution"] == "Chase"
        assert Decimal(result["balance"]) == Decimal("5000.00")
        assert result["currency"] == "USD"
        assert result["id"] is not None

    async def test_default_date_is_today(self, finance_pool):
        """When as_of_date is omitted, the snapshot is dated today."""
        from butlers.tools.finance.overview import net_worth_snapshot

        result = await net_worth_snapshot(
            finance_pool,
            account="Savings",
            institution="BofA",
            balance=10000.00,
        )

        today = date.today()
        row = await finance_pool.fetchrow(
            "SELECT as_of_date FROM balance_snapshots WHERE id = $1::uuid",
            result["id"],
        )
        assert row["as_of_date"] == today

    async def test_explicit_date(self, finance_pool):
        """as_of_date parameter is respected."""
        from butlers.tools.finance.overview import net_worth_snapshot

        target = date(2025, 6, 15)
        result = await net_worth_snapshot(
            finance_pool,
            account="Checking",
            institution="Chase",
            balance=3000.00,
            as_of_date=target,
        )

        row = await finance_pool.fetchrow(
            "SELECT as_of_date FROM balance_snapshots WHERE id = $1::uuid",
            result["id"],
        )
        assert row["as_of_date"] == target

    async def test_upsert_updates_balance_for_same_account_date(self, finance_pool):
        """Calling net_worth_snapshot twice for same account+date updates the balance."""
        from butlers.tools.finance.overview import net_worth_snapshot

        target = date(2025, 6, 15)
        await net_worth_snapshot(
            finance_pool,
            account="Checking",
            institution="Chase",
            balance=3000.00,
            as_of_date=target,
        )
        result = await net_worth_snapshot(
            finance_pool,
            account="Checking",
            institution="Chase",
            balance=3500.00,
            as_of_date=target,
        )

        count = await finance_pool.fetchval("SELECT COUNT(*) FROM balance_snapshots")
        assert count == 1
        assert Decimal(result["balance"]) == Decimal("3500.00")

    async def test_upsert_conflicts_on_real_unique_index_and_stores_new_balance(self, finance_pool):
        """bu-7hfqqr: the real schema has a unique index, not a named constraint.

        A second manual snapshot for the same account and date must update the
        stored row in place (same id) rather than error or insert a duplicate.
        """
        from butlers.tools.finance.overview import net_worth_snapshot

        target = date(2025, 7, 1)
        first = await net_worth_snapshot(
            finance_pool,
            account="Brokerage",
            institution="Fidelity",
            balance=100.00,
            as_of_date=target,
        )
        second = await net_worth_snapshot(
            finance_pool,
            account="Brokerage",
            institution="Fidelity",
            balance=250.00,
            currency="eur",
            as_of_date=target,
        )

        assert second["id"] == first["id"]
        rows = await finance_pool.fetch(
            "SELECT id, balance, currency FROM balance_snapshots "
            "WHERE account_id = $1::uuid AND as_of_date = $2",
            first["account_id"],
            target,
        )
        assert len(rows) == 1
        assert str(rows[0]["id"]) == str(first["id"])
        assert rows[0]["balance"] == Decimal("250.00")
        assert rows[0]["currency"] == "EUR"

    async def test_negative_balance_stored_correctly(self, finance_pool):
        """Negative balance (liability) is stored as-is."""
        from butlers.tools.finance.overview import net_worth_snapshot

        result = await net_worth_snapshot(
            finance_pool,
            account="Credit Card",
            institution="Chase",
            balance=-2500.00,
        )
        assert Decimal(result["balance"]) == Decimal("-2500.00")

    async def test_iso_string_date_accepted(self, finance_pool):
        """ISO string as_of_date is accepted."""
        from butlers.tools.finance.overview import net_worth_snapshot

        result = await net_worth_snapshot(
            finance_pool,
            account="Checking",
            institution="Chase",
            balance=1000.00,
            as_of_date="2025-01-15",
        )
        row = await finance_pool.fetchrow(
            "SELECT as_of_date FROM balance_snapshots WHERE id = $1::uuid",
            result["id"],
        )
        assert row["as_of_date"] == date(2025, 1, 15)

    async def test_multiple_accounts_tracked_separately(self, finance_pool):
        """Different account+institution combinations create separate snapshots."""
        from butlers.tools.finance.overview import net_worth_snapshot

        d = date(2025, 6, 1)
        await net_worth_snapshot(
            finance_pool, account="Checking", institution="Chase", balance=5000.00, as_of_date=d
        )
        await net_worth_snapshot(
            finance_pool, account="Savings", institution="BofA", balance=20000.00, as_of_date=d
        )

        count = await finance_pool.fetchval("SELECT COUNT(*) FROM balance_snapshots")
        assert count == 2


# ---------------------------------------------------------------------------
# net_worth_history
# ---------------------------------------------------------------------------


class TestNetWorthHistory:
    async def test_empty_returns_snapshot_list(self, finance_pool):
        """With no data, returns empty snapshots list."""
        from butlers.tools.finance.overview import net_worth_history

        result = await net_worth_history(finance_pool, months=3)
        assert "snapshots" in result
        assert "as_of" in result

    async def test_single_snapshot_appears_in_correct_month(self, finance_pool):
        """A snapshot on a specific date appears in the correct period."""
        from butlers.tools.finance.overview import net_worth_history, net_worth_snapshot

        today = date.today()
        month_label = f"{today.year:04d}-{today.month:02d}"

        await net_worth_snapshot(
            finance_pool, account="Checking", institution="Chase", balance=5000.00
        )

        result = await net_worth_history(finance_pool, months=1)
        assert len(result["snapshots"]) == 1
        period = result["snapshots"][0]
        assert period["period"] == month_label
        assert Decimal(period["total_assets"]) == Decimal("5000.00")
        assert Decimal(period["total_liabilities"]) == Decimal("0")
        assert Decimal(period["net_worth"]) == Decimal("5000.00")

    async def test_liabilities_split_correctly(self, finance_pool):
        """Positive balances are assets; negative are liabilities."""
        from butlers.tools.finance.overview import net_worth_history, net_worth_snapshot

        await net_worth_snapshot(
            finance_pool, account="Checking", institution="Chase", balance=10000.00
        )
        await net_worth_snapshot(
            finance_pool, account="Credit Card", institution="Chase", balance=-3000.00
        )

        result = await net_worth_history(finance_pool, months=1)
        period = result["snapshots"][0]
        assert Decimal(period["total_assets"]) == Decimal("10000.00")
        assert Decimal(period["total_liabilities"]) == Decimal("3000.00")
        assert Decimal(period["net_worth"]) == Decimal("7000.00")

    async def test_mixed_currency_net_worth_is_bucketed_and_legacy_is_degraded(self, finance_pool):
        from butlers.tools.finance.overview import net_worth_history, net_worth_snapshot

        await net_worth_snapshot(
            finance_pool, account="Checking", institution="Chase", balance=1000, currency="USD"
        )
        await net_worth_snapshot(
            finance_pool, account="Current", institution="Wise", balance=800, currency="EUR"
        )

        period = (await net_worth_history(finance_pool, months=1))["snapshots"][0]

        assert period["currency"] is None
        assert period["legacy_aggregate_degraded"] is True
        assert [row["currency"] for row in period["by_currency"]] == ["EUR", "USD"]
        assert Decimal(period["by_currency"][0]["net_worth"]) == Decimal("800")
        assert Decimal(period["by_currency"][1]["net_worth"]) == Decimal("1000")

    async def test_carry_forward_for_missing_month(self, finance_pool):
        """When a month has no snapshot for an account, carry forward from prior month."""
        from butlers.tools.finance.overview import net_worth_history

        today = date.today()
        account_id = await _insert_account(finance_pool, institution="Chase", name="Checking")

        # Insert snapshot 2 months ago; the month in between and current month have no snapshot.
        two_months_ago = today.replace(day=1) - timedelta(days=1)
        two_months_ago = two_months_ago.replace(day=1)  # First of 2 months ago

        await _insert_snapshot(
            finance_pool, account_id=account_id, balance="8000.00", as_of_date=two_months_ago
        )

        result = await net_worth_history(finance_pool, months=3)
        snapshots = result["snapshots"]
        # Find months after the snapshot month — they should carry forward.
        snapshot_period = f"{two_months_ago.year:04d}-{two_months_ago.month:02d}"
        carried = [s for s in snapshots if s["period"] > snapshot_period]
        for s in carried:
            if s["accounts"]:
                acct = next((a for a in s["accounts"] if a["account"] == "Checking"), None)
                if acct:
                    assert acct["carried_forward"] is True
                    assert Decimal(acct["balance"]) == Decimal("8000.00")

    async def test_later_snapshot_overwrites_carry_forward(self, finance_pool):
        """When a newer snapshot exists for a month, it takes precedence over carry-forward."""
        from butlers.tools.finance.overview import net_worth_history

        today = date.today()
        account_id = await _insert_account(finance_pool, institution="Chase", name="Checking")

        # Snapshot last month.
        last_month = today.replace(day=1) - timedelta(days=1)
        last_month_first = last_month.replace(day=1)
        await _insert_snapshot(
            finance_pool, account_id=account_id, balance="6000.00", as_of_date=last_month_first
        )
        # Snapshot this month (more recent).
        await _insert_snapshot(
            finance_pool, account_id=account_id, balance="6500.00", as_of_date=today
        )

        result = await net_worth_history(finance_pool, months=2)
        current_month = f"{today.year:04d}-{today.month:02d}"
        current_period = next(s for s in result["snapshots"] if s["period"] == current_month)
        acct = current_period["accounts"][0]
        assert Decimal(acct["balance"]) == Decimal("6500.00")
        assert acct["carried_forward"] is False

    async def test_months_parameter_caps_at_120(self, finance_pool):
        """months is capped at 120."""
        from butlers.tools.finance.overview import net_worth_history

        result = await net_worth_history(finance_pool, months=999)
        assert len(result["snapshots"]) <= 120

    async def test_return_shape(self, finance_pool):
        """Response always includes snapshots list and as_of."""
        from butlers.tools.finance.overview import net_worth_history

        result = await net_worth_history(finance_pool, months=2)
        assert "snapshots" in result
        assert "as_of" in result
        for s in result["snapshots"]:
            assert "period" in s
            assert "accounts" in s
            assert "total_assets" in s
            assert "total_liabilities" in s
            assert "net_worth" in s


# ---------------------------------------------------------------------------
# cash_flow
# ---------------------------------------------------------------------------


class TestCashFlow:
    async def test_empty_returns_empty_periods(self, finance_pool):
        """With no transactions, returns empty periods list."""
        from butlers.tools.finance.overview import cash_flow

        result = await cash_flow(finance_pool, period="monthly", months=3)
        assert result["periods"] == []
        assert "avg_net" in result
        assert "as_of" in result

    async def test_basic_income_and_expense(self, finance_pool):
        """Credits are income, debits are expenses."""
        from butlers.tools.finance.overview import cash_flow

        today = datetime.now(UTC)
        await _insert_transaction(
            finance_pool, amount="1000.00", direction="credit", posted_at=today
        )
        await _insert_transaction(finance_pool, amount="400.00", direction="debit", posted_at=today)

        result = await cash_flow(finance_pool, period="monthly", months=1)
        assert len(result["periods"]) == 1
        period = result["periods"][0]
        assert Decimal(period["income"]) == Decimal("1000.00")
        assert Decimal(period["expenses"]) == Decimal("400.00")
        assert Decimal(period["net"]) == Decimal("600.00")

    async def test_savings_rate_computed(self, finance_pool):
        """savings_rate = net / income * 100."""
        from butlers.tools.finance.overview import cash_flow

        today = datetime.now(UTC)
        await _insert_transaction(
            finance_pool, amount="2000.00", direction="credit", posted_at=today
        )
        await _insert_transaction(
            finance_pool, amount="1000.00", direction="debit", posted_at=today
        )

        result = await cash_flow(finance_pool, period="monthly", months=1)
        period = result["periods"][0]
        # net = 1000, income = 2000 → savings_rate = 50%
        assert Decimal(period["savings_rate"]) == Decimal("50.00")

    async def test_cash_flow_excludes_transfers_and_separates_currencies(self, finance_pool):
        from butlers.tools.finance.overview import cash_flow

        posted = datetime.now(UTC)
        await _insert_transaction(
            finance_pool, amount="2000", direction="credit", category="income", posted_at=posted
        )
        await _insert_transaction(
            finance_pool, amount="1000", direction="debit", category="housing", posted_at=posted
        )
        await _insert_transaction(
            finance_pool, amount="5000", direction="credit", category="transfer", posted_at=posted
        )
        await _insert_transaction(
            finance_pool, amount="5000", direction="debit", category="transfer", posted_at=posted
        )
        await _insert_transaction(
            finance_pool,
            amount="400",
            currency="EUR",
            direction="credit",
            category="income",
            posted_at=posted,
        )

        result = await cash_flow(finance_pool, period="monthly", months=1)
        period = result["periods"][0]

        assert Decimal(period["income"]) == Decimal("2400")
        assert Decimal(period["expenses"]) == Decimal("1000")
        assert period["legacy_aggregate_degraded"] is True
        assert period["currency"] is None
        assert [row["currency"] for row in period["by_currency"]] == ["EUR", "USD"]
        usd = next(row for row in period["by_currency"] if row["currency"] == "USD")
        assert Decimal(usd["income"]) == Decimal("2000")
        assert Decimal(usd["expenses"]) == Decimal("1000")
        assert usd["savings_rate"] == "50.00"

    async def test_savings_rate_null_when_no_income(self, finance_pool):
        """savings_rate is None when income is zero."""
        from butlers.tools.finance.overview import cash_flow

        today = datetime.now(UTC)
        await _insert_transaction(finance_pool, amount="500.00", direction="debit", posted_at=today)

        result = await cash_flow(finance_pool, period="monthly", months=1)
        period = result["periods"][0]
        assert period["savings_rate"] is None

    async def test_deleted_transactions_excluded(self, finance_pool):
        """Soft-deleted transactions (deleted_at IS NOT NULL) are excluded."""
        from butlers.tools.finance.overview import cash_flow

        today = datetime.now(UTC)
        await _insert_transaction(finance_pool, amount="500.00", direction="debit", posted_at=today)
        # Deleted transaction — should not count.
        await _insert_transaction(
            finance_pool,
            amount="999.00",
            direction="debit",
            posted_at=today,
            deleted_at=today,
        )

        result = await cash_flow(finance_pool, period="monthly", months=1)
        period = result["periods"][0]
        assert Decimal(period["expenses"]) == Decimal("500.00")

    async def test_category_breakdown(self, finance_pool):
        """breakdown=True adds categories list per period."""
        from butlers.tools.finance.overview import cash_flow

        today = datetime.now(UTC)
        await _insert_transaction(
            finance_pool, amount="300.00", direction="debit", category="groceries", posted_at=today
        )
        await _insert_transaction(
            finance_pool, amount="100.00", direction="debit", category="dining", posted_at=today
        )

        result = await cash_flow(finance_pool, period="monthly", months=1, breakdown=True)
        period = result["periods"][0]
        assert "categories" in period
        cats = {c["category"]: c for c in period["categories"]}
        assert "groceries" in cats
        assert "dining" in cats
        assert Decimal(cats["groceries"]["expenses"]) == Decimal("300.00")

    async def test_no_breakdown_by_default(self, finance_pool):
        """By default, categories key is absent from each period."""
        from butlers.tools.finance.overview import cash_flow

        today = datetime.now(UTC)
        await _insert_transaction(finance_pool, amount="100.00", direction="debit", posted_at=today)

        result = await cash_flow(finance_pool, period="monthly", months=1)
        period = result["periods"][0]
        assert "categories" not in period

    async def test_weekly_period(self, finance_pool):
        """period='weekly' groups by ISO week."""
        from butlers.tools.finance.overview import cash_flow

        today = datetime.now(UTC)
        await _insert_transaction(finance_pool, amount="100.00", direction="debit", posted_at=today)

        result = await cash_flow(finance_pool, period="weekly", months=1)
        assert len(result["periods"]) >= 1
        # Keys should look like "2026-W12"
        for p in result["periods"]:
            assert "W" in p["period"], f"Expected ISO week, got: {p['period']}"

    async def test_yearly_period(self, finance_pool):
        """period='yearly' groups by year."""
        from butlers.tools.finance.overview import cash_flow

        today = datetime.now(UTC)
        await _insert_transaction(finance_pool, amount="200.00", direction="debit", posted_at=today)

        result = await cash_flow(finance_pool, period="yearly", months=12)
        for p in result["periods"]:
            assert len(p["period"]) == 4, f"Expected YYYY year key, got: {p['period']}"

    async def test_invalid_period_raises(self, finance_pool):
        """Invalid period raises ValueError."""
        from butlers.tools.finance.overview import cash_flow

        with pytest.raises(ValueError, match="Unsupported period"):
            await cash_flow(finance_pool, period="bimonthly")

    async def test_avg_net_computed(self, finance_pool):
        """avg_net is the mean of per-period net values."""
        from butlers.tools.finance.overview import cash_flow

        today = datetime.now(UTC)
        await _insert_transaction(
            finance_pool, amount="1000.00", direction="credit", posted_at=today
        )
        await _insert_transaction(finance_pool, amount="700.00", direction="debit", posted_at=today)

        result = await cash_flow(finance_pool, period="monthly", months=1)
        assert Decimal(result["avg_net"]) == Decimal("300.00")

    async def test_return_shape(self, finance_pool):
        """Response always includes periods, avg_net, avg_savings_rate, as_of."""
        from butlers.tools.finance.overview import cash_flow

        result = await cash_flow(finance_pool, period="monthly", months=2)
        assert "periods" in result
        assert "avg_net" in result
        assert "avg_savings_rate" in result
        assert "as_of" in result


# ---------------------------------------------------------------------------
# subscription_audit
# ---------------------------------------------------------------------------


class TestSubscriptionAudit:
    async def test_empty_returns_empty_entries(self, finance_pool):
        """With no subscriptions or recurring groups, returns empty entries."""
        from butlers.tools.finance.overview import subscription_audit

        result = await subscription_audit(finance_pool)
        assert result["entries"] == []
        assert Decimal(result["total_annual_cost"]) == Decimal("0")

    async def test_tracked_subscription_included(self, finance_pool):
        """Active subscriptions from finance.subscriptions appear in entries."""
        from butlers.tools.finance.overview import subscription_audit

        renewal = date.today() + timedelta(days=30)
        await finance_pool.execute(
            """
            INSERT INTO subscriptions (service, amount, currency, frequency, next_renewal, status)
            VALUES ('Netflix', 15.49, 'USD', 'monthly', $1, 'active')
            """,
            renewal,
        )

        result = await subscription_audit(finance_pool)
        assert len(result["entries"]) == 1
        entry = result["entries"][0]
        assert entry["service"] == "Netflix"
        assert entry["status"] == "tracked_active"
        assert Decimal(entry["amount"]) == Decimal("15.49")
        # monthly * 12 = 185.88
        assert Decimal(entry["annual_cost"]) == Decimal("185.88")

    async def test_paused_subscription_included_as_paused(self, finance_pool):
        """Paused subscriptions appear with status=tracked_paused."""
        from butlers.tools.finance.overview import subscription_audit

        renewal = date.today() + timedelta(days=15)
        await finance_pool.execute(
            """
            INSERT INTO subscriptions (service, amount, currency, frequency, next_renewal, status)
            VALUES ('Spotify', 9.99, 'USD', 'monthly', $1, 'paused')
            """,
            renewal,
        )

        result = await subscription_audit(finance_pool)
        assert len(result["entries"]) == 1
        assert result["entries"][0]["status"] == "tracked_paused"

    async def test_cancelled_subscription_excluded(self, finance_pool):
        """Cancelled subscriptions are NOT included in the audit."""
        from butlers.tools.finance.overview import subscription_audit

        renewal = date.today() - timedelta(days=30)
        await finance_pool.execute(
            """
            INSERT INTO subscriptions (service, amount, currency, frequency, next_renewal, status)
            VALUES ('Hulu', 7.99, 'USD', 'monthly', $1, 'cancelled')
            """,
            renewal,
        )

        result = await subscription_audit(finance_pool)
        assert len(result["entries"]) == 0

    async def test_detected_untracked_recurring_included(self, finance_pool):
        """Patterns from recurring_groups not in subscriptions appear as detected_untracked."""
        from butlers.tools.finance.overview import subscription_audit

        await finance_pool.execute(
            """
            INSERT INTO recurring_groups
                (merchant, estimated_frequency, avg_amount, currency, last_seen_date, is_active)
            VALUES ('Adobe', 'monthly', 54.99, 'USD', $1, true)
            """,
            date.today() - timedelta(days=5),
        )

        result = await subscription_audit(finance_pool)
        assert len(result["entries"]) == 1
        entry = result["entries"][0]
        assert entry["service"] == "Adobe"
        assert entry["status"] == "detected_untracked"

    async def test_already_tracked_not_duplicated(self, finance_pool):
        """A merchant that is already in subscriptions is not duplicated from recurring_groups."""
        from butlers.tools.finance.overview import subscription_audit

        renewal = date.today() + timedelta(days=30)
        await finance_pool.execute(
            """
            INSERT INTO subscriptions (service, amount, currency, frequency, next_renewal, status)
            VALUES ('Netflix', 15.49, 'USD', 'monthly', $1, 'active')
            """,
            renewal,
        )
        await finance_pool.execute(
            """
            INSERT INTO recurring_groups
                (merchant, estimated_frequency, avg_amount, currency, is_active)
            VALUES ('Netflix', 'monthly', 15.49, 'USD', true)
            """
        )

        result = await subscription_audit(finance_pool)
        netflix_entries = [e for e in result["entries"] if e["service"] == "Netflix"]
        assert len(netflix_entries) == 1

    async def test_annual_cost_projections(self, finance_pool):
        """Annual cost is correctly computed for different frequencies."""
        from butlers.tools.finance.overview import subscription_audit

        renewal = date.today() + timedelta(days=30)
        await finance_pool.execute(
            """
            INSERT INTO subscriptions
                (service, amount, currency, frequency, next_renewal, status)
            VALUES
                ('Weekly News',  10.00, 'USD', 'weekly',    $1, 'active'),
                ('Quarterly App', 30.00, 'USD', 'quarterly', $1, 'active'),
                ('Annual Plan',  120.00, 'USD', 'yearly',    $1, 'active')
            """,
            renewal,
        )

        result = await subscription_audit(finance_pool)
        entries = {e["service"]: e for e in result["entries"]}
        assert Decimal(entries["Weekly News"]["annual_cost"]) == Decimal("520.00")  # 10*52
        assert Decimal(entries["Quarterly App"]["annual_cost"]) == Decimal("120.00")  # 30*4
        assert Decimal(entries["Annual Plan"]["annual_cost"]) == Decimal("120.00")  # 120*1

    async def test_total_annual_cost_sums_active(self, finance_pool):
        """total_annual_cost sums active subscriptions and detected entries."""
        from butlers.tools.finance.overview import subscription_audit

        renewal = date.today() + timedelta(days=30)
        await finance_pool.execute(
            """
            INSERT INTO subscriptions (service, amount, currency, frequency, next_renewal, status)
            VALUES ('Netflix', 15.00, 'USD', 'monthly', $1, 'active')
            """,
            renewal,
        )

        result = await subscription_audit(finance_pool)
        # 15 * 12 = 180
        assert Decimal(result["total_annual_cost"]) == Decimal("180.00")

    async def test_total_annual_cost_separates_currencies(self, finance_pool):
        from butlers.tools.finance.overview import subscription_audit

        renewal = date.today() + timedelta(days=30)
        await finance_pool.execute(
            """
            INSERT INTO subscriptions (service, amount, currency, frequency, next_renewal, status)
            VALUES ('USD plan', 10, 'USD', 'monthly', $1, 'active'),
                   ('EUR plan', 8, 'EUR', 'monthly', $1, 'active')
            """,
            renewal,
        )

        result = await subscription_audit(finance_pool)
        assert result["currency"] is None
        assert result["legacy_aggregate_degraded"] is True
        assert result["by_currency"] == [
            {"currency": "EUR", "total_annual_cost": "96.00"},
            {"currency": "USD", "total_annual_cost": "120.00"},
        ]

    async def test_return_shape(self, finance_pool):
        """Response has required top-level keys."""
        from butlers.tools.finance.overview import subscription_audit

        result = await subscription_audit(finance_pool)
        assert "entries" in result
        assert "total_annual_cost" in result
        assert "changes_since_last_audit" in result
        assert "last_audit_date" in result
        assert "as_of" in result

    async def test_last_charge_date_populated_via_batch_query(self, finance_pool):
        """last_charge_date is correctly fetched via batch JOIN (not N+1 queries)."""
        from butlers.tools.finance.overview import subscription_audit

        renewal = date.today() + timedelta(days=30)

        # Insert multiple subscriptions to ensure batch query handles multiple rows.
        await finance_pool.execute(
            """
            INSERT INTO subscriptions
                (service, amount, currency, frequency, next_renewal, status)
            VALUES
                ('Netflix', 15.49, 'USD', 'monthly', $1, 'active'),
                ('Spotify', 9.99, 'USD', 'monthly', $1, 'active')
            """,
            renewal,
        )

        # Insert transactions for both subscriptions, with different dates.
        t1 = datetime.now(UTC) - timedelta(days=60)
        t2 = datetime.now(UTC) - timedelta(days=30)
        t3 = datetime.now(UTC) - timedelta(days=5)
        await finance_pool.execute(
            """
            INSERT INTO transactions
                (merchant, amount, currency, direction, category, posted_at)
            VALUES
                ('Netflix', 15.49, 'USD', 'debit', 'entertainment', $1),
                ('Spotify', 9.99, 'USD', 'debit', 'entertainment', $2),
                ('Netflix', 15.49, 'USD', 'debit', 'entertainment', $3)
            """,
            t1,
            t2,
            t3,
        )

        result = await subscription_audit(finance_pool)
        entries = {e["service"]: e for e in result["entries"]}

        # Netflix should have the most recent charge date (5 days ago).
        netflix_entry = entries["Netflix"]
        assert netflix_entry["last_charge_date"] is not None
        assert netflix_entry["last_charge_date"] == t3.isoformat()

        # Spotify should have its charge date (30 days ago).
        spotify_entry = entries["Spotify"]
        assert spotify_entry["last_charge_date"] is not None
        assert spotify_entry["last_charge_date"] == t2.isoformat()

    async def test_merchant_match_is_case_insensitive(self, finance_pool):
        """Merchant matching is case-insensitive (e.g. 'netflix' matches 'NETFLIX INC')."""
        from butlers.tools.finance.overview import subscription_audit

        renewal = date.today() + timedelta(days=30)
        await finance_pool.execute(
            """
            INSERT INTO subscriptions (service, amount, currency, frequency, next_renewal, status)
            VALUES ('Netflix', 15.49, 'USD', 'monthly', $1, 'active')
            """,
            renewal,
        )

        t1 = datetime.now(UTC) - timedelta(days=10)
        # Merchant name uses different capitalisation.
        await finance_pool.execute(
            """
            INSERT INTO transactions
                (merchant, amount, currency, direction, category, posted_at)
            VALUES ('NETFLIX INC', 15.49, 'USD', 'debit', 'entertainment', $1)
            """,
            t1,
        )

        result = await subscription_audit(finance_pool)
        entry = result["entries"][0]
        assert entry["last_charge_date"] is not None
        assert entry["last_charge_date"] == t1.isoformat()

    async def test_short_service_name_not_matched_against_transactions(self, finance_pool):
        """Service names shorter than _MIN_MERCHANT_MATCH_LEN are not matched.

        Prevents false positives from very short names like 'TV' or 'Go'
        matching unrelated merchant names.
        """
        from butlers.tools.finance.overview import _MIN_MERCHANT_MATCH_LEN, subscription_audit

        # This test assumes _MIN_MERCHANT_MATCH_LEN > 2 (currently 3).
        assert _MIN_MERCHANT_MATCH_LEN > 2, "constant changed; update test accordingly"

        renewal = date.today() + timedelta(days=30)
        # Service name is shorter than _MIN_MERCHANT_MATCH_LEN (e.g., "TV" = 2 chars).
        short_name = "TV"
        assert len(short_name) < _MIN_MERCHANT_MATCH_LEN
        await finance_pool.execute(
            """
            INSERT INTO subscriptions (service, amount, currency, frequency, next_renewal, status)
            VALUES ($1, 9.99, 'USD', 'monthly', $2, 'active')
            """,
            short_name,
            renewal,
        )

        # Insert a transaction whose merchant contains "tv" — should NOT match.
        t1 = datetime.now(UTC) - timedelta(days=5)
        await finance_pool.execute(
            """
            INSERT INTO transactions
                (merchant, amount, currency, direction, category, posted_at)
            VALUES ('DIRECTV SPORTS', 9.99, 'USD', 'debit', 'entertainment', $1)
            """,
            t1,
        )

        result = await subscription_audit(finance_pool)
        entry = result["entries"][0]
        # last_charge_date must be None: "TV" is too short to match.
        assert entry["last_charge_date"] is None

    async def test_unrelated_transaction_not_matched(self, finance_pool):
        """A transaction for a different merchant is not matched to a subscription.

        Ensures merchant matching uses substring containment (service in merchant),
        not the reverse, so 'Netflix' does not match 'Spotify Annual'.
        """
        from butlers.tools.finance.overview import subscription_audit

        renewal = date.today() + timedelta(days=30)
        await finance_pool.execute(
            """
            INSERT INTO subscriptions (service, amount, currency, frequency, next_renewal, status)
            VALUES ('Netflix', 15.49, 'USD', 'monthly', $1, 'active')
            """,
            renewal,
        )

        # Insert a transaction that does NOT contain "netflix".
        t1 = datetime.now(UTC) - timedelta(days=10)
        await finance_pool.execute(
            """
            INSERT INTO transactions
                (merchant, amount, currency, direction, category, posted_at)
            VALUES ('Spotify Premium', 9.99, 'USD', 'debit', 'entertainment', $1)
            """,
            t1,
        )

        result = await subscription_audit(finance_pool)
        entry = result["entries"][0]
        assert entry["last_charge_date"] is None


# ---------------------------------------------------------------------------
# flag_tax_deductible
# ---------------------------------------------------------------------------


class TestFlagTaxDeductible:
    @pytest.fixture(autouse=True)
    async def _no_tax_relevant_categories(self, finance_pool):
        """Clear the seeded tax flags so the built-in default mapping applies."""
        await finance_pool.execute(
            "UPDATE categories SET is_tax_relevant = false, tax_category = NULL "
            "WHERE is_tax_relevant OR tax_category IS NOT NULL"
        )

    async def test_summary_separates_currencies(self, finance_pool):
        from butlers.tools.finance.overview import flag_tax_deductible

        now = datetime.now(UTC)
        await _insert_transaction(
            finance_pool,
            merchant="US clinic",
            amount="100",
            currency="USD",
            direction="debit",
            category="medical",
            posted_at=now,
        )
        await _insert_transaction(
            finance_pool,
            merchant="EU clinic",
            amount="80",
            currency="EUR",
            direction="debit",
            category="medical",
            posted_at=now,
        )

        summary = (await flag_tax_deductible(finance_pool, year=now.year))["summary"]
        assert summary["currency"] is None
        assert summary["legacy_aggregate_degraded"] is True
        assert [row["currency"] for row in summary["by_currency"]] == ["EUR", "USD"]

    async def test_empty_returns_no_transactions(self, finance_pool):
        """With no transactions, returns empty result."""
        from butlers.tools.finance.overview import flag_tax_deductible

        result = await flag_tax_deductible(finance_pool, year=2025)
        assert result["transactions"] == []
        assert result["summary"]["flagged_count"] == 0
        assert result["year"] == 2025
        assert result["disclaimer"]

    async def test_default_year_is_current(self, finance_pool):
        """When year is omitted, defaults to the current year."""
        from butlers.tools.finance.overview import flag_tax_deductible

        result = await flag_tax_deductible(finance_pool)
        assert result["year"] == datetime.now(UTC).year

    async def test_medical_category_flagged_by_default(self, finance_pool):
        """Transactions with default tax-relevant category 'medical' are flagged."""
        from butlers.tools.finance.overview import flag_tax_deductible

        today = datetime.now(UTC)
        await _insert_transaction(
            finance_pool,
            merchant="Dr. Smith",
            amount="200.00",
            direction="debit",
            category="medical",
            posted_at=today,
        )

        result = await flag_tax_deductible(finance_pool, year=today.year)
        assert len(result["transactions"]) == 1
        tx = result["transactions"][0]
        assert tx["merchant"] == "Dr. Smith"
        assert tx["tax_category"] == "medical_expense"
        assert Decimal(tx["amount"]) == Decimal("200.00")

    async def test_charitable_donation_flagged(self, finance_pool):
        """Charitable donations are flagged by default."""
        from butlers.tools.finance.overview import flag_tax_deductible

        today = datetime.now(UTC)
        await _insert_transaction(
            finance_pool,
            merchant="Red Cross",
            amount="100.00",
            direction="debit",
            category="charitable",
            posted_at=today,
        )

        result = await flag_tax_deductible(finance_pool, year=today.year)
        assert len(result["transactions"]) == 1
        assert result["transactions"][0]["tax_category"] == "charitable_donation"

    async def test_non_tax_category_not_flagged(self, finance_pool):
        """Transactions with non-tax-relevant categories are not included."""
        from butlers.tools.finance.overview import flag_tax_deductible

        today = datetime.now(UTC)
        await _insert_transaction(
            finance_pool,
            merchant="Netflix",
            amount="15.49",
            direction="debit",
            category="subscriptions",
            posted_at=today,
        )

        result = await flag_tax_deductible(finance_pool, year=today.year)
        assert len(result["transactions"]) == 0

    async def test_credit_transactions_excluded(self, finance_pool):
        """Credit (income) transactions are not flagged even if category matches."""
        from butlers.tools.finance.overview import flag_tax_deductible

        today = datetime.now(UTC)
        await _insert_transaction(
            finance_pool,
            merchant="Medical Refund",
            amount="50.00",
            direction="credit",
            category="medical",
            posted_at=today,
        )

        result = await flag_tax_deductible(finance_pool, year=today.year)
        assert len(result["transactions"]) == 0

    async def test_year_filter_excludes_other_years(self, finance_pool):
        """Transactions from other years are excluded."""
        from butlers.tools.finance.overview import flag_tax_deductible

        prev_year_dt = datetime(2024, 6, 1, tzinfo=UTC)
        await _insert_transaction(
            finance_pool,
            merchant="Dr. Jones",
            amount="300.00",
            direction="debit",
            category="medical",
            posted_at=prev_year_dt,
        )

        result = await flag_tax_deductible(finance_pool, year=2025)
        assert len(result["transactions"]) == 0

    async def test_deleted_transactions_excluded(self, finance_pool):
        """Soft-deleted transactions are excluded from tax flagging."""
        from butlers.tools.finance.overview import flag_tax_deductible

        today = datetime.now(UTC)
        await _insert_transaction(
            finance_pool,
            merchant="Dentist",
            amount="250.00",
            direction="debit",
            category="medical",
            posted_at=today,
            deleted_at=today,
        )

        result = await flag_tax_deductible(finance_pool, year=today.year)
        assert len(result["transactions"]) == 0

    async def test_custom_category_from_categories_table(self, finance_pool):
        """Categories marked is_tax_relevant=true in finance.categories are used."""
        from butlers.tools.finance.overview import flag_tax_deductible

        # Insert a custom category.
        await finance_pool.execute(
            """
            INSERT INTO categories (name, display_name, is_tax_relevant, tax_category)
            VALUES ('home_improvement', 'Home improvement', true, 'home_office_deduction')
            """
        )

        today = datetime.now(UTC)
        await _insert_transaction(
            finance_pool,
            merchant="Home Depot",
            amount="500.00",
            direction="debit",
            category="home_improvement",
            posted_at=today,
        )

        result = await flag_tax_deductible(finance_pool, year=today.year)
        assert len(result["transactions"]) == 1
        assert result["transactions"][0]["tax_category"] == "home_office_deduction"

    async def test_summary_totals(self, finance_pool):
        """Summary aggregates total_flagged_amount and by_tax_category correctly."""
        from butlers.tools.finance.overview import flag_tax_deductible

        today = datetime.now(UTC)
        await _insert_transaction(
            finance_pool,
            merchant="Dr. A",
            amount="200.00",
            direction="debit",
            category="medical",
            posted_at=today,
        )
        await _insert_transaction(
            finance_pool,
            merchant="Dr. B",
            amount="150.00",
            direction="debit",
            category="medical",
            posted_at=today,
        )
        await _insert_transaction(
            finance_pool,
            merchant="Charity",
            amount="100.00",
            direction="debit",
            category="charitable",
            posted_at=today,
        )

        result = await flag_tax_deductible(finance_pool, year=today.year)
        summary = result["summary"]
        assert Decimal(summary["total_flagged_amount"]) == Decimal("450.00")
        assert summary["flagged_count"] == 3
        assert Decimal(summary["by_tax_category"]["medical_expense"]) == Decimal("350.00")
        assert Decimal(summary["by_tax_category"]["charitable_donation"]) == Decimal("100.00")

    async def test_disclaimer_always_present(self, finance_pool):
        """Response always includes a disclaimer."""
        from butlers.tools.finance.overview import flag_tax_deductible

        result = await flag_tax_deductible(finance_pool)
        assert result["disclaimer"]
        assert len(result["disclaimer"]) > 20

    async def test_return_shape(self, finance_pool):
        """Response has required top-level keys."""
        from butlers.tools.finance.overview import flag_tax_deductible

        result = await flag_tax_deductible(finance_pool)
        assert "transactions" in result
        assert "summary" in result
        assert "year" in result
        assert "disclaimer" in result
        assert "total_flagged_amount" in result["summary"]
        assert "flagged_count" in result["summary"]
        assert "by_tax_category" in result["summary"]


# ---------------------------------------------------------------------------
# Import sanity checks (no DB needed)
# ---------------------------------------------------------------------------


def test_overview_importable_from_package():
    """All 5 overview functions are importable from the finance tools package."""
    from butlers.tools.finance import (  # noqa: F401
        cash_flow,
        flag_tax_deductible,
        net_worth_history,
        net_worth_snapshot,
        subscription_audit,
    )

    assert callable(net_worth_snapshot)
    assert callable(net_worth_history)
    assert callable(cash_flow)
    assert callable(subscription_audit)
    assert callable(flag_tax_deductible)
