"""Non-transaction property facts and transaction-ledger enrichment contracts."""

from __future__ import annotations

import shutil
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from butlers.roster_imports import roster_module

_property_facts = roster_module("finance", "tools/property_facts")
_transactions = roster_module("finance", "tools/transactions")
_write_bill_fact = _property_facts._write_bill_fact
bulk_update_transactions = _transactions.bulk_update_transactions
list_distinct_merchants = _transactions.list_distinct_merchants
record_transaction = _transactions.record_transaction
track_account_fact = _property_facts.track_account_fact
track_subscription_fact = _property_facts.track_subscription_fact

_docker_available = shutil.which("docker") is not None


pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not _docker_available, reason="Docker not available"),
]


_DDL_FINANCE_PREDICATES = """
INSERT INTO predicate_registry (name, is_temporal) VALUES
    ('transaction_debit',   true),
    ('transaction_credit',  true),
    ('account',             false),
    ('subscription',        false),
    ('bill',                false)
ON CONFLICT (name) DO NOTHING
"""


@pytest.fixture
async def finance_pool(db):
    """A core+memory clone (the real facts store) with the finance predicates registered."""
    pool = await db("core", "memory")
    await pool.execute(_DDL_FINANCE_PREDICATES)
    return pool


@pytest.fixture
async def pool_with_owner(finance_pool):
    """Pool pre-seeded with an owner entity in public.entities."""
    await finance_pool.execute(
        "INSERT INTO public.entities (canonical_name, entity_type, roles) VALUES ($1, $2, $3)",
        "Owner",
        "person",
        ["owner"],
    )
    yield finance_pool


def _mock_embedding_engine():
    engine = MagicMock()
    engine.embed.return_value = [0.1] * 384
    engine.model_name = "test-model"
    return engine


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TestTrackAccountFact:
    """Tests for track_account_fact — property fact creation."""

    async def test_creates_account_fact(self, pool_with_owner):
        with patch(
            "butlers.tools.finance.property_facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            result = await track_account_fact(
                pool=pool_with_owner,
                institution="Chase",
                type="checking",
                currency="USD",
                name="Chase Checking",
                last_four="1234",
            )

        assert result["institution"] == "Chase"
        assert result["type"] == "checking"
        assert result["currency"] == "USD"
        assert result["last_four"] == "1234"
        assert result["id"] is not None
        assert "****1234" in result["content"]

    async def test_content_includes_institution_type_last_four(self, pool_with_owner):
        with patch(
            "butlers.tools.finance.property_facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            result = await track_account_fact(
                pool=pool_with_owner,
                institution="Wells Fargo",
                type="savings",
                last_four="5678",
            )

        assert result["content"] == "Wells Fargo savings ****5678"

    async def test_content_without_last_four(self, pool_with_owner):
        with patch(
            "butlers.tools.finance.property_facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            result = await track_account_fact(
                pool=pool_with_owner,
                institution="Amex",
                type="credit",
            )

        assert result["content"] == "Amex credit"

    async def test_is_property_fact_valid_at_null(self, pool_with_owner):
        """Account facts are property facts: valid_at IS NULL."""
        with patch(
            "butlers.tools.finance.property_facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            await track_account_fact(
                pool=pool_with_owner,
                institution="Chase",
                type="credit",
            )

        row = await pool_with_owner.fetchrow(
            "SELECT valid_at FROM facts WHERE predicate = 'account' LIMIT 1"
        )
        assert row is not None
        assert row["valid_at"] is None  # property fact — valid_at must be NULL

    async def test_different_accounts_coexist(self, pool_with_owner):
        """Two accounts with different last_four create independent active facts."""
        with patch(
            "butlers.tools.finance.property_facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            await track_account_fact(
                pool=pool_with_owner, institution="Chase", type="credit", last_four="1111"
            )
            await track_account_fact(
                pool=pool_with_owner, institution="Chase", type="credit", last_four="2222"
            )

        count = await pool_with_owner.fetchval(
            "SELECT COUNT(*) FROM facts WHERE predicate = 'account' AND validity = 'active'"
        )
        assert count == 2

    async def test_same_account_supersedes(self, pool_with_owner):
        """Same content (same account) supersedes the previous fact."""
        with patch(
            "butlers.tools.finance.property_facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            await track_account_fact(
                pool=pool_with_owner,
                institution="Chase",
                type="credit",
                last_four="1234",
                name="Original",
            )
            await track_account_fact(
                pool=pool_with_owner,
                institution="Chase",
                type="credit",
                last_four="1234",
                name="Updated",
            )

        active_count = await pool_with_owner.fetchval(
            "SELECT COUNT(*) FROM facts WHERE predicate = 'account' AND validity = 'active'"
        )
        assert active_count == 1
        superseded_count = await pool_with_owner.fetchval(
            "SELECT COUNT(*) FROM facts WHERE predicate = 'account' AND validity = 'superseded'"
        )
        assert superseded_count == 1


class TestTrackSubscriptionFact:
    """Tests for track_subscription_fact — property fact creation."""

    async def test_creates_subscription_fact(self, pool_with_owner):
        with patch(
            "butlers.tools.finance.property_facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            renewal = date.today() + timedelta(days=30)
            result = await track_subscription_fact(
                pool=pool_with_owner,
                service="Netflix",
                amount=15.49,
                currency="USD",
                frequency="monthly",
                next_renewal=renewal,
            )

        assert result["service"] == "Netflix"
        assert Decimal(result["amount"]) == Decimal("15.49")
        assert result["currency"] == "USD"
        assert result["status"] == "active"
        assert result["auto_renew"] is True
        assert result["id"] is not None

    async def test_invalid_status_raises(self, pool_with_owner):
        with patch(
            "butlers.tools.finance.property_facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            with pytest.raises(ValueError, match="Invalid status"):
                await track_subscription_fact(
                    pool=pool_with_owner,
                    service="Bad",
                    amount=5.00,
                    currency="USD",
                    frequency="monthly",
                    next_renewal=date.today() + timedelta(days=30),
                    status="expired",
                )

    async def test_invalid_frequency_raises(self, pool_with_owner):
        with patch(
            "butlers.tools.finance.property_facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            with pytest.raises(ValueError, match="Invalid frequency"):
                await track_subscription_fact(
                    pool=pool_with_owner,
                    service="Bad",
                    amount=5.00,
                    currency="USD",
                    frequency="biweekly",
                    next_renewal=date.today() + timedelta(days=14),
                )

    async def test_is_property_fact_valid_at_null(self, pool_with_owner):
        with patch(
            "butlers.tools.finance.property_facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            await track_subscription_fact(
                pool=pool_with_owner,
                service="Spotify",
                amount=9.99,
                currency="USD",
                frequency="monthly",
                next_renewal=date.today() + timedelta(days=30),
            )

        row = await pool_with_owner.fetchrow(
            "SELECT valid_at FROM facts WHERE predicate = 'subscription' LIMIT 1"
        )
        assert row is not None
        assert row["valid_at"] is None  # property fact

    async def test_next_renewal_string_accepted(self, pool_with_owner):
        with patch(
            "butlers.tools.finance.property_facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            renewal_str = (date.today() + timedelta(days=30)).isoformat()
            result = await track_subscription_fact(
                pool=pool_with_owner,
                service="Dropbox",
                amount=11.99,
                currency="USD",
                frequency="monthly",
                next_renewal=renewal_str,
            )
        assert result["next_renewal"] == renewal_str

    async def test_source_message_id_stored_in_metadata(self, pool_with_owner):
        with patch(
            "butlers.tools.finance.property_facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            result = await track_subscription_fact(
                pool=pool_with_owner,
                service="Adobe",
                amount=54.99,
                currency="USD",
                frequency="monthly",
                next_renewal=date.today() + timedelta(days=30),
                source_message_id="email-sub-001",
            )
        assert result["source_message_id"] == "email-sub-001"


class TestTrackBillFact:
    """Tests for _write_bill_fact — property fact creation."""

    async def test_creates_bill_fact(self, pool_with_owner):
        with patch(
            "butlers.tools.finance.property_facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            due = date.today() + timedelta(days=7)
            result = await _write_bill_fact(
                pool=pool_with_owner,
                payee="PG&E",
                amount=84.00,
                currency="USD",
                due_date=due,
            )

        assert result["payee"] == "PG&E"
        assert Decimal(result["amount"]) == Decimal("84.00")
        assert result["currency"] == "USD"
        assert result["due_date"] == due.isoformat()
        assert result["status"] == "pending"
        assert result["frequency"] == "one_time"
        assert result["id"] is not None

    async def test_invalid_status_raises(self, pool_with_owner):
        with patch(
            "butlers.tools.finance.property_facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            with pytest.raises(ValueError, match="Invalid status"):
                await _write_bill_fact(
                    pool=pool_with_owner,
                    payee="Phone",
                    amount=50.00,
                    currency="USD",
                    due_date=date.today() + timedelta(days=3),
                    status="unpaid",
                )

    async def test_invalid_frequency_raises(self, pool_with_owner):
        with patch(
            "butlers.tools.finance.property_facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            with pytest.raises(ValueError, match="Invalid frequency"):
                await _write_bill_fact(
                    pool=pool_with_owner,
                    payee="Phone",
                    amount=50.00,
                    currency="USD",
                    due_date=date.today() + timedelta(days=3),
                    frequency="biweekly",
                )

    async def test_is_property_fact_valid_at_null(self, pool_with_owner):
        with patch(
            "butlers.tools.finance.property_facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            await _write_bill_fact(
                pool=pool_with_owner,
                payee="PG&E",
                amount=84.00,
                currency="USD",
                due_date=date.today() + timedelta(days=7),
            )

        row = await pool_with_owner.fetchrow(
            "SELECT valid_at FROM facts WHERE predicate = 'bill' LIMIT 1"
        )
        assert row is not None
        assert row["valid_at"] is None  # property fact

    async def test_due_date_string_accepted(self, pool_with_owner):
        with patch(
            "butlers.tools.finance.property_facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            due_str = (date.today() + timedelta(days=7)).isoformat()
            result = await _write_bill_fact(
                pool=pool_with_owner,
                payee="Credit Card",
                amount=300.00,
                currency="USD",
                due_date=due_str,
            )
        assert result["due_date"] == due_str

    async def test_paid_status_with_paid_at(self, pool_with_owner):
        with patch(
            "butlers.tools.finance.property_facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            paid_time = _utcnow()
            result = await _write_bill_fact(
                pool=pool_with_owner,
                payee="Electric",
                amount=70.00,
                currency="USD",
                due_date=date.today() + timedelta(days=1),
                status="paid",
                paid_at=paid_time,
            )
        assert result["status"] == "paid"
        assert result["paid_at"] is not None

    async def test_same_bill_supersedes(self, pool_with_owner):
        """Same payee+due_date bill supersedes when updated."""
        with patch(
            "butlers.tools.finance.property_facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            due = date.today() + timedelta(days=10)
            await _write_bill_fact(
                pool=pool_with_owner,
                payee="Rent",
                amount=1800.00,
                currency="USD",
                due_date=due,
                status="pending",
            )
            await _write_bill_fact(
                pool=pool_with_owner,
                payee="Rent",
                amount=1800.00,
                currency="USD",
                due_date=due,
                status="paid",
                paid_at=_utcnow(),
            )

        active = await pool_with_owner.fetchval(
            "SELECT COUNT(*) FROM facts WHERE predicate = 'bill' AND validity = 'active'"
        )
        assert active == 1
        superseded = await pool_with_owner.fetchval(
            "SELECT COUNT(*) FROM facts WHERE predicate = 'bill' AND validity = 'superseded'"
        )
        assert superseded == 1


class TestListDistinctMerchants:
    """Tests for list_distinct_merchants — aggregate query with merchant grouping."""

    @pytest.fixture
    async def seeded_merchants(self, ledger_pool):
        """Pool with multiple transactions across a few merchants."""

        now = _utcnow()
        entries = [
            ("TRADER JOES #123", -55.0, "groceries", now),
            ("TRADER JOES #456", -40.0, "groceries", now),
            ("NETFLIX.COM", -15.49, "subscriptions", now),
            ("NETFLIX.COM", -15.49, "subscriptions", now + timedelta(seconds=1)),
            ("STARBUCKS STORE 001", -6.75, "dining", now),
        ]
        for merchant, amount, category, posted_at in entries:
            await record_transaction(
                pool=ledger_pool,
                posted_at=posted_at,
                merchant=merchant,
                amount=amount,
                currency="USD",
                category=category,
            )
        return ledger_pool

    async def test_returns_distinct_merchants(self, seeded_merchants):

        result = await list_distinct_merchants(pool=seeded_merchants)
        assert "items" in result
        assert "total" in result
        merchants = {item["merchant"] for item in result["items"]}
        assert "TRADER JOES #123" in merchants
        assert "NETFLIX.COM" in merchants

    async def test_count_and_total_amount(self, seeded_merchants):

        result = await list_distinct_merchants(pool=seeded_merchants)
        netflix_item = next(i for i in result["items"] if i["merchant"] == "NETFLIX.COM")
        assert netflix_item["count"] == 2
        assert Decimal(netflix_item["total_amount"]) == Decimal("30.98")

    async def test_min_count_filter(self, seeded_merchants):

        result = await list_distinct_merchants(pool=seeded_merchants, min_count=2)
        for item in result["items"]:
            assert item["count"] >= 2

    async def test_empty_pool(self, ledger_pool):

        result = await list_distinct_merchants(pool=ledger_pool)
        assert result["total"] == 0
        assert result["items"] == []

    async def test_pagination(self, seeded_merchants):

        result = await list_distinct_merchants(pool=seeded_merchants, limit=2, offset=0)
        assert len(result["items"]) == 2
        assert result["total"] >= 3

    async def test_limit_capped_at_max(self, seeded_merchants):

        result = await list_distinct_merchants(pool=seeded_merchants, limit=9999)
        assert result["limit"] == 1000

    async def test_unnormalized_only(self, seeded_merchants):
        """unnormalized_only=True returns only merchants without normalized_merchant."""

        await bulk_update_transactions(
            pool=seeded_merchants,
            ops=[
                {
                    "match": {"merchant_pattern": "NETFLIX%"},
                    "set": {"normalized_merchant": "Netflix"},
                }
            ],
        )
        result = await list_distinct_merchants(pool=seeded_merchants, unnormalized_only=True)
        merchant_names = {i["merchant"] for i in result["items"]}
        assert "NETFLIX.COM" not in merchant_names

    async def test_normalized_merchant_field_populated(self, seeded_merchants):
        """After normalization, normalized_merchant is present in results."""

        await bulk_update_transactions(
            pool=seeded_merchants,
            ops=[
                {
                    "match": {"merchant_pattern": "NETFLIX%"},
                    "set": {"normalized_merchant": "Netflix"},
                }
            ],
        )
        result = await list_distinct_merchants(pool=seeded_merchants)
        netflix_items = [i for i in result["items"] if "NETFLIX" in i["merchant"]]
        assert len(netflix_items) > 0
        assert netflix_items[0]["normalized_merchant"] == "Netflix"

    async def test_date_filter(self, ledger_pool):
        """Transactions outside the date range are excluded."""

        old = _utcnow() - timedelta(days=60)
        new = _utcnow() - timedelta(days=1)
        await record_transaction(
            pool=ledger_pool,
            posted_at=old,
            merchant="OldShop",
            amount=-10.0,
            currency="USD",
            category="misc",
        )
        await record_transaction(
            pool=ledger_pool,
            posted_at=new,
            merchant="NewShop",
            amount=-20.0,
            currency="USD",
            category="misc",
        )
        cutoff = (_utcnow() - timedelta(days=30)).date()
        result = await list_distinct_merchants(pool=ledger_pool, start_date=cutoff)
        merchants = {i["merchant"] for i in result["items"]}
        assert "NewShop" in merchants
        assert "OldShop" not in merchants


class TestBulkUpdateTransactions:
    """Tests for bulk_update_transactions — metadata overlay."""

    @pytest.fixture
    async def seeded_bulk(self, ledger_pool):
        """Pool with transactions for bulk update testing."""

        now = _utcnow()
        txns = [
            ("STARBUCKS STORE 001", -5.5, "dining"),
            ("STARBUCKS STORE 002", -6.0, "dining"),
            ("AMAZON MKTPL", -25.0, "shopping"),
        ]
        for merchant, amount, category in txns:
            await record_transaction(
                pool=ledger_pool,
                posted_at=now,
                merchant=merchant,
                amount=amount,
                currency="USD",
                category=category,
            )
        return ledger_pool

    async def test_normalized_merchant_overlay_applied(self, seeded_bulk):

        result = await bulk_update_transactions(
            pool=seeded_bulk,
            ops=[
                {
                    "match": {"merchant_pattern": "STARBUCKS%"},
                    "set": {"normalized_merchant": "Starbucks"},
                }
            ],
        )
        assert result["updated_total"] == 2
        assert result["results"][0]["updated"] == 2
        rows = await seeded_bulk.fetch(
            "SELECT normalized_merchant AS nm FROM transactions WHERE merchant LIKE 'STARBUCKS%'"
        )
        assert all(r["nm"] == "Starbucks" for r in rows)

    async def test_inferred_category_overlay_applied(self, seeded_bulk):

        await bulk_update_transactions(
            pool=seeded_bulk,
            ops=[
                {
                    "match": {"merchant_pattern": "AMAZON%"},
                    "set": {"inferred_category": "online_shopping"},
                }
            ],
        )
        rows = await seeded_bulk.fetch(
            "SELECT inferred_category AS ic FROM transactions WHERE merchant LIKE 'AMAZON%'"
        )
        assert all(r["ic"] == "online_shopping" for r in rows)

    async def test_original_merchant_not_modified(self, seeded_bulk):

        await bulk_update_transactions(
            pool=seeded_bulk,
            ops=[
                {
                    "match": {"merchant_pattern": "STARBUCKS%"},
                    "set": {"normalized_merchant": "Starbucks"},
                }
            ],
        )
        rows = await seeded_bulk.fetch(
            "SELECT merchant AS m FROM transactions WHERE normalized_merchant = 'Starbucks'"
        )
        for r in rows:
            assert "STARBUCKS" in r["m"]

    async def test_original_category_not_modified(self, seeded_bulk):

        await bulk_update_transactions(
            pool=seeded_bulk,
            ops=[
                {
                    "match": {"merchant_pattern": "STARBUCKS%"},
                    "set": {"inferred_category": "coffee"},
                }
            ],
        )
        rows = await seeded_bulk.fetch(
            "SELECT category AS c FROM transactions WHERE merchant LIKE 'STARBUCKS%'"
        )
        assert all(r["c"] == "dining" for r in rows)

    async def test_multiple_ops(self, seeded_bulk):

        result = await bulk_update_transactions(
            pool=seeded_bulk,
            ops=[
                {
                    "match": {"merchant_pattern": "STARBUCKS%"},
                    "set": {"normalized_merchant": "Starbucks"},
                },
                {
                    "match": {"merchant_pattern": "AMAZON%"},
                    "set": {"normalized_merchant": "Amazon"},
                },
            ],
        )
        assert len(result["results"]) == 2
        assert result["updated_total"] == 3

    async def test_no_match_returns_zero(self, seeded_bulk):

        result = await bulk_update_transactions(
            pool=seeded_bulk,
            ops=[
                {
                    "match": {"merchant_pattern": "NONEXISTENT%"},
                    "set": {"normalized_merchant": "NoOne"},
                }
            ],
        )
        assert result["updated_total"] == 0
        assert result["results"][0]["updated"] == 0

    async def test_too_many_ops_raises(self, seeded_bulk):

        ops = [
            {"match": {"merchant_pattern": f"MERCHANT{i}%"}, "set": {"normalized_merchant": "X"}}
            for i in range(201)
        ]
        with pytest.raises(ValueError, match="Too many ops"):
            await bulk_update_transactions(pool=seeded_bulk, ops=ops)

    async def test_disallowed_set_key_raises(self, seeded_bulk):

        with pytest.raises(ValueError, match="not allowed"):
            await bulk_update_transactions(
                pool=seeded_bulk,
                ops=[{"match": {"merchant_pattern": "STARBUCKS%"}, "set": {"merchant": "Hacked"}}],
            )

    async def test_missing_merchant_pattern_raises(self, seeded_bulk):

        with pytest.raises(ValueError, match="merchant_pattern"):
            await bulk_update_transactions(
                pool=seeded_bulk, ops=[{"match": {}, "set": {"normalized_merchant": "X"}}]
            )


_DDL_IDEMPOTENCY_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_temporal_idempotency
    ON facts (tenant_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL
"""


@pytest.fixture
async def ledger_pool(db):
    return await db("core", "finance", schemas={"finance": "finance"}, schema="finance")
