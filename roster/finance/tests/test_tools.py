"""Tests for butlers.tools.finance — subscription and bill tracking tools."""

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


@pytest.fixture
async def pool(provisioned_postgres_pool):
    """Provision a fresh database with finance tables and return a pool."""
    async with provisioned_postgres_pool() as p:
        # Create finance.accounts (required FK target)
        await p.execute("""
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
        """)
        # Create finance.subscriptions
        await p.execute("""
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
        """)
        # Create finance.bills
        await p.execute("""
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
        """)
        yield p


# ---------------------------------------------------------------------------
# track_subscription tests
# ---------------------------------------------------------------------------


class TestTrackSubscription:
    """Tests for the track_subscription tool."""

    async def test_create_new_subscription(self, pool):
        """Creating a subscription returns a SubscriptionRecord dict."""
        from butlers.tools.finance import track_subscription

        renewal = date.today() + timedelta(days=30)
        result = await track_subscription(
            pool=pool,
            service="Netflix",
            amount=15.49,
            currency="USD",
            frequency="monthly",
            next_renewal=renewal,
        )

        assert result["service"] == "Netflix"
        assert float(result["amount"]) == 15.49
        assert result["currency"] == "USD"
        assert result["frequency"] == "monthly"
        assert result["status"] == "active"
        assert result["auto_renew"] is True
        assert result["id"] is not None

    async def test_create_with_all_optional_fields(self, pool):
        """Creating subscription with all optional fields persists them."""
        from butlers.tools.finance import track_subscription

        renewal = date.today() + timedelta(days=7)
        result = await track_subscription(
            pool=pool,
            service="Spotify",
            amount=9.99,
            currency="USD",
            frequency="monthly",
            next_renewal=renewal,
            status="active",
            auto_renew=False,
            payment_method="Visa ending in 4242",
            source_message_id="email-msg-001",
            metadata={"plan": "premium"},
        )

        assert result["auto_renew"] is False
        assert result["payment_method"] == "Visa ending in 4242"
        assert result["source_message_id"] == "email-msg-001"
        assert result["metadata"]["plan"] == "premium"

    async def test_upsert_updates_existing_on_service_frequency_match(self, pool):
        """Calling track_subscription twice with same service+frequency updates in place."""
        from butlers.tools.finance import track_subscription

        renewal_1 = date.today() + timedelta(days=30)
        first = await track_subscription(
            pool=pool,
            service="Adobe Creative Cloud",
            amount=54.99,
            currency="USD",
            frequency="monthly",
            next_renewal=renewal_1,
        )

        renewal_2 = date.today() + timedelta(days=31)
        second = await track_subscription(
            pool=pool,
            service="Adobe Creative Cloud",
            amount=59.99,
            currency="USD",
            frequency="monthly",
            next_renewal=renewal_2,
            status="active",
        )

        # Same record — same id
        assert first["id"] == second["id"]
        # Amount updated
        assert float(second["amount"]) == 59.99
        # Renewal date updated
        assert second["next_renewal"] == renewal_2

    async def test_different_frequency_creates_new_record(self, pool):
        """Same service but different frequency creates a separate record."""
        from butlers.tools.finance import track_subscription

        renewal = date.today() + timedelta(days=365)
        await track_subscription(
            pool=pool,
            service="Adobe Creative Cloud",
            amount=54.99,
            currency="USD",
            frequency="monthly",
            next_renewal=date.today() + timedelta(days=30),
        )
        yearly = await track_subscription(
            pool=pool,
            service="Adobe Creative Cloud",
            amount=599.99,
            currency="USD",
            frequency="yearly",
            next_renewal=renewal,
        )

        # Verify two distinct records exist in the database
        count = await pool.fetchval(
            "SELECT COUNT(*) FROM subscriptions WHERE service = $1",
            "Adobe Creative Cloud",
        )
        assert count == 2
        assert yearly["frequency"] == "yearly"

    async def test_status_cancelled(self, pool):
        """Cancelled status is accepted and persisted."""
        from butlers.tools.finance import track_subscription

        result = await track_subscription(
            pool=pool,
            service="Hulu",
            amount=7.99,
            currency="USD",
            frequency="monthly",
            next_renewal=date.today() + timedelta(days=15),
            status="cancelled",
        )
        assert result["status"] == "cancelled"

    async def test_status_paused(self, pool):
        """Paused status is accepted and persisted."""
        from butlers.tools.finance import track_subscription

        result = await track_subscription(
            pool=pool,
            service="Disney+",
            amount=10.99,
            currency="USD",
            frequency="monthly",
            next_renewal=date.today() + timedelta(days=20),
            status="paused",
        )
        assert result["status"] == "paused"

    async def test_invalid_status_raises(self, pool):
        """Invalid status raises ValueError."""
        from butlers.tools.finance import track_subscription

        with pytest.raises(ValueError, match="Invalid status"):
            await track_subscription(
                pool=pool,
                service="Bad Service",
                amount=5.00,
                currency="USD",
                frequency="monthly",
                next_renewal=date.today() + timedelta(days=30),
                status="expired",  # invalid
            )

    async def test_invalid_frequency_raises(self, pool):
        """Invalid frequency raises ValueError."""
        from butlers.tools.finance import track_subscription

        with pytest.raises(ValueError, match="Invalid frequency"):
            await track_subscription(
                pool=pool,
                service="Bad Service",
                amount=5.00,
                currency="USD",
                frequency="biweekly",  # invalid
                next_renewal=date.today() + timedelta(days=14),
            )

    async def test_renewal_date_string_normalized(self, pool):
        """ISO string renewal date is normalized to a date object."""
        from butlers.tools.finance import track_subscription

        renewal_str = (date.today() + timedelta(days=30)).isoformat()
        result = await track_subscription(
            pool=pool,
            service="Dropbox",
            amount=11.99,
            currency="USD",
            frequency="monthly",
            next_renewal=renewal_str,
        )
        assert result["next_renewal"] == date.fromisoformat(renewal_str)

    async def test_account_id_linkage(self, pool):
        """Account ID can be linked to a subscription."""
        from butlers.tools.finance import track_subscription

        acct = await pool.fetchrow("""
            INSERT INTO accounts (institution, type, currency)
            VALUES ('Chase', 'credit', 'USD')
            RETURNING id
        """)
        account_id = acct["id"]

        result = await track_subscription(
            pool=pool,
            service="Amazon Prime",
            amount=14.99,
            currency="USD",
            frequency="monthly",
            next_renewal=date.today() + timedelta(days=30),
            account_id=account_id,
        )
        assert result["account_id"] == account_id

    async def test_metadata_merged_on_update(self, pool):
        """On upsert, metadata is merged (not replaced)."""
        from butlers.tools.finance import track_subscription

        renewal = date.today() + timedelta(days=30)
        await track_subscription(
            pool=pool,
            service="Notion",
            amount=8.00,
            currency="USD",
            frequency="monthly",
            next_renewal=renewal,
            metadata={"plan": "personal"},
        )
        updated = await track_subscription(
            pool=pool,
            service="Notion",
            amount=8.00,
            currency="USD",
            frequency="monthly",
            next_renewal=renewal,
            metadata={"seats": 1},
        )
        # Both keys should be present after merge
        assert "plan" in updated["metadata"]
        assert "seats" in updated["metadata"]


# ---------------------------------------------------------------------------
# track_bill tests
# ---------------------------------------------------------------------------


class TestTrackBill:
    """Tests for the track_bill tool."""

    async def test_create_new_bill(self, pool):
        """Creating a bill returns a BillRecord dict."""
        from butlers.tools.finance import track_bill

        due = date.today() + timedelta(days=7)
        result = await track_bill(
            pool=pool,
            payee="PG&E",
            amount=84.00,
            currency="USD",
            due_date=due,
        )

        assert result["payee"] == "PG&E"
        assert float(result["amount"]) == 84.00
        assert result["currency"] == "USD"
        assert result["due_date"] == due
        assert result["status"] == "pending"
        assert result["frequency"] == "one_time"
        assert result["id"] is not None

    async def test_create_with_all_optional_fields(self, pool):
        """Creating bill with all optional fields persists them."""
        from butlers.tools.finance import track_bill

        due = date.today() + timedelta(days=5)
        period_start = date.today().replace(day=1)
        period_end = date.today()
        paid_time = datetime.now(UTC)

        result = await track_bill(
            pool=pool,
            payee="Comcast",
            amount=89.99,
            currency="USD",
            due_date=due,
            frequency="monthly",
            status="paid",
            payment_method="Auto-pay Checking",
            statement_period_start=period_start,
            statement_period_end=period_end,
            paid_at=paid_time,
            source_message_id="email-bill-001",
            metadata={"account_number": "****1234"},
        )

        assert result["frequency"] == "monthly"
        assert result["status"] == "paid"
        assert result["payment_method"] == "Auto-pay Checking"
        assert result["statement_period_start"] == period_start
        assert result["statement_period_end"] == period_end
        assert result["source_message_id"] == "email-bill-001"
        assert result["metadata"]["account_number"] == "****1234"

    async def test_upsert_updates_existing_on_payee_due_date_match(self, pool):
        """Calling track_bill twice with same payee+due_date updates in place."""
        from butlers.tools.finance import track_bill

        due = date.today() + timedelta(days=10)
        first = await track_bill(
            pool=pool,
            payee="Rent",
            amount=1800.00,
            currency="USD",
            due_date=due,
            status="pending",
        )
        second = await track_bill(
            pool=pool,
            payee="Rent",
            amount=1800.00,
            currency="USD",
            due_date=due,
            status="paid",
            paid_at=datetime.now(UTC),
        )

        assert first["id"] == second["id"]
        assert second["status"] == "paid"

    async def test_different_due_date_creates_new_record(self, pool):
        """Same payee but different due_date creates a separate record."""
        from butlers.tools.finance import track_bill

        due_1 = date.today() + timedelta(days=5)
        due_2 = date.today() + timedelta(days=35)
        await track_bill(pool=pool, payee="Internet", amount=60.00, currency="USD", due_date=due_1)
        await track_bill(pool=pool, payee="Internet", amount=60.00, currency="USD", due_date=due_2)

        count = await pool.fetchval("SELECT COUNT(*) FROM bills WHERE payee = $1", "Internet")
        assert count == 2

    async def test_status_paid(self, pool):
        """Paid status is accepted and persisted."""
        from butlers.tools.finance import track_bill

        result = await track_bill(
            pool=pool,
            payee="Water Bill",
            amount=40.00,
            currency="USD",
            due_date=date.today() - timedelta(days=2),
            status="paid",
        )
        assert result["status"] == "paid"

    async def test_status_overdue(self, pool):
        """Overdue status is accepted and persisted."""
        from butlers.tools.finance import track_bill

        result = await track_bill(
            pool=pool,
            payee="Gas Bill",
            amount=50.00,
            currency="USD",
            due_date=date.today() - timedelta(days=5),
            status="overdue",
        )
        assert result["status"] == "overdue"

    async def test_invalid_status_raises(self, pool):
        """Invalid status raises ValueError."""
        from butlers.tools.finance import track_bill

        with pytest.raises(ValueError, match="Invalid status"):
            await track_bill(
                pool=pool,
                payee="Phone",
                amount=50.00,
                currency="USD",
                due_date=date.today() + timedelta(days=3),
                status="unpaid",  # invalid
            )

    async def test_invalid_frequency_raises(self, pool):
        """Invalid frequency raises ValueError."""
        from butlers.tools.finance import track_bill

        with pytest.raises(ValueError, match="Invalid frequency"):
            await track_bill(
                pool=pool,
                payee="Phone",
                amount=50.00,
                currency="USD",
                due_date=date.today() + timedelta(days=3),
                frequency="biweekly",  # invalid
            )

    async def test_due_date_string_accepted(self, pool):
        """ISO string due_date is normalized to a date object."""
        from butlers.tools.finance import track_bill

        due_str = (date.today() + timedelta(days=7)).isoformat()
        result = await track_bill(
            pool=pool,
            payee="Credit Card",
            amount=300.00,
            currency="USD",
            due_date=due_str,
        )
        assert result["due_date"] == date.fromisoformat(due_str)

    async def test_paid_at_string_accepted(self, pool):
        """ISO string paid_at is normalized to a datetime."""
        from butlers.tools.finance import track_bill

        paid_str = datetime.now(UTC).isoformat()
        result = await track_bill(
            pool=pool,
            payee="Electric",
            amount=70.00,
            currency="USD",
            due_date=date.today() + timedelta(days=1),
            status="paid",
            paid_at=paid_str,
        )
        assert result["paid_at"] is not None


# ---------------------------------------------------------------------------
# upcoming_bills tests
# ---------------------------------------------------------------------------


class TestUpcomingBills:
    """Tests for the upcoming_bills tool."""

    async def test_empty_returns_empty_items(self, pool):
        """No bills returns empty items list with zero totals."""
        from butlers.tools.finance import upcoming_bills

        result = await upcoming_bills(pool=pool)

        assert result["items"] == []
        assert result["totals"]["due_soon"] == 0
        assert result["totals"]["overdue"] == 0
        assert result["window_days"] == 14

    async def test_bill_due_within_horizon_included(self, pool):
        """Bill with due_date within days_ahead is included."""
        from butlers.tools.finance import track_bill, upcoming_bills

        due = date.today() + timedelta(days=5)
        await track_bill(pool=pool, payee="Rent", amount=1800.00, currency="USD", due_date=due)

        result = await upcoming_bills(pool=pool, days_ahead=14)
        assert len(result["items"]) == 1
        assert result["items"][0]["bill"]["payee"] == "Rent"

    async def test_bill_beyond_horizon_excluded(self, pool):
        """Bill with due_date beyond horizon is not included."""
        from butlers.tools.finance import track_bill, upcoming_bills

        due = date.today() + timedelta(days=20)
        await track_bill(
            pool=pool, payee="Distant Bill", amount=100.00, currency="USD", due_date=due
        )

        result = await upcoming_bills(pool=pool, days_ahead=14)
        assert len(result["items"]) == 0

    async def test_urgency_due_today(self, pool):
        """Bill due today gets urgency=due_today."""
        from butlers.tools.finance import track_bill, upcoming_bills

        await track_bill(
            pool=pool,
            payee="Phone",
            amount=50.00,
            currency="USD",
            due_date=date.today(),
        )

        result = await upcoming_bills(pool=pool, days_ahead=14)
        assert len(result["items"]) == 1
        assert result["items"][0]["urgency"] == "due_today"
        assert result["items"][0]["days_until_due"] == 0

    async def test_urgency_due_soon(self, pool):
        """Bill due within horizon but not today gets urgency=due_soon."""
        from butlers.tools.finance import track_bill, upcoming_bills

        due = date.today() + timedelta(days=7)
        await track_bill(pool=pool, payee="Internet", amount=60.00, currency="USD", due_date=due)

        result = await upcoming_bills(pool=pool, days_ahead=14)
        assert len(result["items"]) == 1
        assert result["items"][0]["urgency"] == "due_soon"
        assert result["items"][0]["days_until_due"] == 7

    async def test_urgency_overdue_by_status(self, pool):
        """Bill with status=overdue gets urgency=overdue."""
        from butlers.tools.finance import track_bill, upcoming_bills

        past_due = date.today() - timedelta(days=3)
        await track_bill(
            pool=pool,
            payee="Gas",
            amount=45.00,
            currency="USD",
            due_date=past_due,
            status="overdue",
        )

        result = await upcoming_bills(pool=pool, days_ahead=14, include_overdue=True)
        overdue_items = [i for i in result["items"] if i["urgency"] == "overdue"]
        assert len(overdue_items) == 1
        assert overdue_items[0]["days_until_due"] < 0

    async def test_urgency_overdue_by_past_pending(self, pool):
        """Bill past due_date with status=pending is classified as overdue."""
        from butlers.tools.finance import track_bill, upcoming_bills

        past_due = date.today() - timedelta(days=2)
        await track_bill(
            pool=pool,
            payee="Water",
            amount=30.00,
            currency="USD",
            due_date=past_due,
            status="pending",
        )

        result = await upcoming_bills(pool=pool, days_ahead=14, include_overdue=True)
        overdue_items = [i for i in result["items"] if i["urgency"] == "overdue"]
        assert len(overdue_items) == 1

    async def test_include_overdue_false_excludes_past_bills(self, pool):
        """include_overdue=False excludes bills past the due date."""
        from butlers.tools.finance import track_bill, upcoming_bills

        past_due = date.today() - timedelta(days=2)
        await track_bill(
            pool=pool,
            payee="Old Bill",
            amount=30.00,
            currency="USD",
            due_date=past_due,
            status="overdue",
        )

        result = await upcoming_bills(pool=pool, days_ahead=14, include_overdue=False)
        assert len(result["items"]) == 0

    async def test_include_overdue_true_includes_past_bills(self, pool):
        """include_overdue=True includes bills that are past due."""
        from butlers.tools.finance import track_bill, upcoming_bills

        past_due = date.today() - timedelta(days=5)
        await track_bill(
            pool=pool,
            payee="Old Overdue",
            amount=75.00,
            currency="USD",
            due_date=past_due,
            status="overdue",
        )

        result = await upcoming_bills(pool=pool, days_ahead=14, include_overdue=True)
        assert len(result["items"]) == 1

    async def test_paid_bills_excluded(self, pool):
        """Paid bills are not included in upcoming_bills."""
        from butlers.tools.finance import track_bill, upcoming_bills

        due = date.today() + timedelta(days=3)
        await track_bill(
            pool=pool,
            payee="Already Paid",
            amount=100.00,
            currency="USD",
            due_date=due,
            status="paid",
        )

        result = await upcoming_bills(pool=pool, days_ahead=14)
        assert len(result["items"]) == 0

    async def test_totals_due_soon_count(self, pool):
        """totals.due_soon counts non-overdue items."""
        from butlers.tools.finance import track_bill, upcoming_bills

        await track_bill(
            pool=pool,
            payee="Bill A",
            amount=50.00,
            currency="USD",
            due_date=date.today() + timedelta(days=3),
        )
        await track_bill(
            pool=pool,
            payee="Bill B",
            amount=100.00,
            currency="USD",
            due_date=date.today() + timedelta(days=10),
        )

        result = await upcoming_bills(pool=pool, days_ahead=14)
        assert result["totals"]["due_soon"] == 2
        assert result["totals"]["overdue"] == 0

    async def test_totals_overdue_count(self, pool):
        """totals.overdue counts overdue items."""
        from butlers.tools.finance import track_bill, upcoming_bills

        past = date.today() - timedelta(days=3)
        await track_bill(
            pool=pool,
            payee="Overdue A",
            amount=50.00,
            currency="USD",
            due_date=past,
            status="overdue",
        )
        await track_bill(
            pool=pool,
            payee="Due Soon B",
            amount=80.00,
            currency="USD",
            due_date=date.today() + timedelta(days=5),
        )

        result = await upcoming_bills(pool=pool, days_ahead=14, include_overdue=True)
        assert result["totals"]["overdue"] == 1
        assert result["totals"]["due_soon"] == 1

    async def test_totals_amount_due(self, pool):
        """totals.amount_due sums all included bill amounts."""
        from butlers.tools.finance import track_bill, upcoming_bills

        await track_bill(
            pool=pool,
            payee="Rent",
            amount=1800.00,
            currency="USD",
            due_date=date.today() + timedelta(days=5),
        )
        await track_bill(
            pool=pool,
            payee="Internet",
            amount=60.00,
            currency="USD",
            due_date=date.today() + timedelta(days=8),
        )

        result = await upcoming_bills(pool=pool, days_ahead=14)
        assert Decimal(result["totals"]["amount_due"]) == pytest.approx(Decimal("1860.00"))

    async def test_response_has_as_of_and_window_days(self, pool):
        """Response includes as_of timestamp and window_days."""
        from butlers.tools.finance import upcoming_bills

        result = await upcoming_bills(pool=pool, days_ahead=7)
        assert "as_of" in result
        assert result["window_days"] == 7
        # as_of should be parseable
        datetime.fromisoformat(result["as_of"])

    async def test_custom_days_ahead(self, pool):
        """Custom days_ahead changes the query horizon."""
        from butlers.tools.finance import track_bill, upcoming_bills

        # Bill due in 30 days — out of default 14-day window but in 60-day window
        due_30 = date.today() + timedelta(days=30)
        await track_bill(
            pool=pool,
            payee="Quarterly Bill",
            amount=200.00,
            currency="USD",
            due_date=due_30,
            frequency="quarterly",
        )

        result_14 = await upcoming_bills(pool=pool, days_ahead=14)
        result_60 = await upcoming_bills(pool=pool, days_ahead=60)

        assert len(result_14["items"]) == 0
        assert len(result_60["items"]) == 1

    async def test_items_sorted_by_due_date(self, pool):
        """Items are returned sorted by due_date ascending."""
        from butlers.tools.finance import track_bill, upcoming_bills

        due_a = date.today() + timedelta(days=8)
        due_b = date.today() + timedelta(days=3)
        due_c = date.today() + timedelta(days=12)
        await track_bill(pool=pool, payee="Bill A", amount=10.00, currency="USD", due_date=due_a)
        await track_bill(pool=pool, payee="Bill B", amount=20.00, currency="USD", due_date=due_b)
        await track_bill(pool=pool, payee="Bill C", amount=30.00, currency="USD", due_date=due_c)

        result = await upcoming_bills(pool=pool, days_ahead=14)
        due_dates = [item["bill"]["due_date"] for item in result["items"]]
        assert due_dates == sorted(due_dates)
