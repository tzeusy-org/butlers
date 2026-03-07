"""Tests for finance butler SPO fact-layer tools (bu-ddb.4).

Covers:
- record_transaction_fact: temporal fact creation, direction inference,
  source_message_id-based deduplication, amount string encoding.
- list_transaction_facts: filter by date range, category, merchant, amount,
  direction, account_id; pagination.
- track_account_fact: property fact creation (content-differentiated accounts).
- track_subscription_fact: property fact creation, validation of status/frequency.
- track_bill_fact: property fact creation, validation of status/frequency.
- spending_summary_facts: JSONB aggregation over transaction facts, group_by modes.
"""

from __future__ import annotations

import shutil
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

_docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not _docker_available, reason="Docker not available"),
]

# ---------------------------------------------------------------------------
# Minimal schema DDL — TEXT embedding avoids pgvector dependency in tests
# ---------------------------------------------------------------------------

_DDL_SHARED_SCHEMA = "CREATE SCHEMA IF NOT EXISTS shared"
_DDL_SHARED_ENTITIES = """
CREATE TABLE IF NOT EXISTS shared.entities (
    id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name    TEXT NOT NULL DEFAULT '',
    roles   TEXT[] NOT NULL DEFAULT '{}'
)
"""
_DDL_PREDICATE_REGISTRY = """
CREATE TABLE IF NOT EXISTS predicate_registry (
    name                 TEXT PRIMARY KEY,
    expected_subject_type TEXT,
    is_temporal          BOOLEAN NOT NULL DEFAULT false,
    description          TEXT
)
"""
_DDL_FACTS = """
CREATE TABLE IF NOT EXISTS facts (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject             TEXT NOT NULL,
    predicate           TEXT NOT NULL,
    content             TEXT NOT NULL,
    embedding           TEXT,
    search_vector       TSVECTOR,
    importance          FLOAT NOT NULL DEFAULT 5.0,
    confidence          FLOAT NOT NULL DEFAULT 1.0,
    decay_rate          FLOAT NOT NULL DEFAULT 0.002,
    permanence          TEXT NOT NULL DEFAULT 'stable',
    source_butler       TEXT,
    source_episode_id   UUID,
    supersedes_id       UUID REFERENCES facts(id) ON DELETE SET NULL,
    validity            TEXT NOT NULL DEFAULT 'active',
    scope               TEXT NOT NULL DEFAULT 'global',
    reference_count     INTEGER NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_confirmed_at   TIMESTAMPTZ,
    tags                JSONB DEFAULT '[]'::jsonb,
    metadata            JSONB DEFAULT '{}'::jsonb,
    entity_id           UUID REFERENCES shared.entities(id),
    object_entity_id    UUID REFERENCES shared.entities(id),
    valid_at            TIMESTAMPTZ DEFAULT NULL
)
"""
_DDL_MEMORY_LINKS = """
CREATE TABLE IF NOT EXISTS memory_links (
    id          BIGSERIAL PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_id   UUID NOT NULL,
    target_type TEXT NOT NULL,
    target_id   UUID NOT NULL,
    relation    TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_type, source_id, target_type, target_id)
)
"""

_DDL_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_facts_predicate ON facts (predicate)",
    "CREATE INDEX IF NOT EXISTS idx_facts_validity ON facts (validity)",
    "CREATE INDEX IF NOT EXISTS idx_facts_scope ON facts (scope)",
    "CREATE INDEX IF NOT EXISTS idx_facts_entity_id ON facts (entity_id)",
    "CREATE INDEX IF NOT EXISTS idx_facts_valid_at ON facts (valid_at DESC)",
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
async def pool(provisioned_postgres_pool):
    """Provision a fresh database with memory/facts infrastructure."""
    async with provisioned_postgres_pool() as p:
        await p.execute(_DDL_SHARED_SCHEMA)
        await p.execute(_DDL_SHARED_ENTITIES)
        await p.execute(_DDL_PREDICATE_REGISTRY)
        await p.execute(_DDL_FACTS)
        await p.execute(_DDL_MEMORY_LINKS)
        for ddl in _DDL_INDEXES:
            await p.execute(ddl)
        await p.execute(_DDL_FINANCE_PREDICATES)
        yield p


@pytest.fixture
async def pool_with_owner(pool):
    """Pool pre-seeded with an owner entity in shared.entities."""
    await pool.execute(
        "INSERT INTO shared.entities (name, roles) VALUES ($1, $2)",
        "Owner",
        ["owner"],
    )
    yield pool


# ---------------------------------------------------------------------------
# Patch helper: suppress embedding engine loading in tests
# ---------------------------------------------------------------------------


def _mock_embedding_engine():
    engine = MagicMock()
    engine.embed.return_value = [0.1] * 384
    return engine


def _utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# record_transaction_fact
# ---------------------------------------------------------------------------


class TestRecordTransactionFact:
    """Tests for record_transaction_fact — temporal fact creation."""

    async def test_debit_direction_inferred_from_negative_amount(self, pool_with_owner):
        with patch(
            "butlers.tools.finance.facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            from butlers.tools.finance.facts import record_transaction_fact

            result = await record_transaction_fact(
                pool=pool_with_owner,
                posted_at=_utcnow(),
                merchant="Trader Joe's",
                amount=-55.00,
                currency="USD",
                category="groceries",
            )

        assert result["direction"] == "debit"
        assert Decimal(result["amount"]) == Decimal("55.00")
        assert result["merchant"] == "Trader Joe's"
        assert result["currency"] == "USD"
        assert result["category"] == "groceries"
        assert result["id"] is not None

    async def test_credit_direction_inferred_from_positive_amount(self, pool_with_owner):
        with patch(
            "butlers.tools.finance.facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            from butlers.tools.finance.facts import record_transaction_fact

            result = await record_transaction_fact(
                pool=pool_with_owner,
                posted_at=_utcnow(),
                merchant="PayPal",
                amount=100.00,
                currency="USD",
                category="refunds",
            )

        assert result["direction"] == "credit"
        assert Decimal(result["amount"]) == Decimal("100.00")

    async def test_amount_stored_as_string_for_precision(self, pool_with_owner):
        with patch(
            "butlers.tools.finance.facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            from butlers.tools.finance.facts import record_transaction_fact

            result = await record_transaction_fact(
                pool=pool_with_owner,
                posted_at=_utcnow(),
                merchant="Test",
                amount=-15.49,
                currency="USD",
                category="subscriptions",
            )

        # Amount must be a string-encoded decimal, not a float
        assert isinstance(result["amount"], str)
        assert Decimal(result["amount"]) == Decimal("15.49")

    async def test_dedup_via_source_message_id(self, pool_with_owner):
        with patch(
            "butlers.tools.finance.facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            from butlers.tools.finance.facts import record_transaction_fact

            now = _utcnow()
            first = await record_transaction_fact(
                pool=pool_with_owner,
                posted_at=now,
                merchant="Chase Alert",
                amount=-75.00,
                currency="USD",
                category="dining",
                source_message_id="email-dedupe-001",
            )
            second = await record_transaction_fact(
                pool=pool_with_owner,
                posted_at=now,
                merchant="Chase Alert",
                amount=-75.00,
                currency="USD",
                category="dining",
                source_message_id="email-dedupe-001",
            )

        assert first["id"] == second["id"]

        # Only one active fact in the DB
        count = await pool_with_owner.fetchval(
            "SELECT COUNT(*) FROM facts WHERE predicate = 'transaction_debit'"
            " AND metadata->>'source_message_id' = 'email-dedupe-001'"
        )
        assert count == 1

    async def test_different_source_ids_not_deduped(self, pool_with_owner):
        with patch(
            "butlers.tools.finance.facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            from butlers.tools.finance.facts import record_transaction_fact

            now = _utcnow()
            first = await record_transaction_fact(
                pool=pool_with_owner,
                posted_at=now,
                merchant="Lyft",
                amount=-12.00,
                currency="USD",
                category="transport",
                source_message_id="msg-lyft-001",
            )
            second = await record_transaction_fact(
                pool=pool_with_owner,
                posted_at=now + timedelta(seconds=1),
                merchant="Lyft",
                amount=-12.00,
                currency="USD",
                category="transport",
                source_message_id="msg-lyft-002",
            )

        assert first["id"] != second["id"]

    async def test_optional_fields_stored_in_metadata(self, pool_with_owner):
        with patch(
            "butlers.tools.finance.facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            from butlers.tools.finance.facts import record_transaction_fact

            result = await record_transaction_fact(
                pool=pool_with_owner,
                posted_at=_utcnow(),
                merchant="Amazon",
                amount=-29.99,
                currency="USD",
                category="shopping",
                description="Prime monthly",
                payment_method="Amex",
                receipt_url="https://amazon.com/r/123",
                external_ref="ext-abc-123",
                source_message_id="msg-001",
            )

        assert result["description"] == "Prime monthly"
        assert result["payment_method"] == "Amex"
        assert result["receipt_url"] == "https://amazon.com/r/123"
        assert result["external_ref"] == "ext-abc-123"
        assert result["source_message_id"] == "msg-001"

    async def test_currency_uppercased(self, pool_with_owner):
        with patch(
            "butlers.tools.finance.facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            from butlers.tools.finance.facts import record_transaction_fact

            result = await record_transaction_fact(
                pool=pool_with_owner,
                posted_at=_utcnow(),
                merchant="Wise",
                amount=-50.00,
                currency="eur",
                category="transfer",
            )

        assert result["currency"] == "EUR"

    async def test_fact_is_temporal_with_valid_at_set(self, pool_with_owner):
        """Transaction facts are temporal: valid_at = posted_at."""
        with patch(
            "butlers.tools.finance.facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            from butlers.tools.finance.facts import record_transaction_fact

            posted = _utcnow()
            await record_transaction_fact(
                pool=pool_with_owner,
                posted_at=posted,
                merchant="Starbucks",
                amount=-5.25,
                currency="USD",
                category="dining",
            )

        row = await pool_with_owner.fetchrow(
            "SELECT valid_at FROM facts WHERE predicate = 'transaction_debit' LIMIT 1"
        )
        assert row is not None
        assert row["valid_at"] is not None  # temporal — valid_at set

    async def test_works_without_owner_entity(self, pool):
        """When no owner entity exists, facts are still created (entity_id=NULL)."""
        with patch(
            "butlers.tools.finance.facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            from butlers.tools.finance.facts import record_transaction_fact

            result = await record_transaction_fact(
                pool=pool,
                posted_at=_utcnow(),
                merchant="Fallback",
                amount=-10.00,
                currency="USD",
                category="misc",
            )

        assert result["id"] is not None
        assert result["direction"] == "debit"


# ---------------------------------------------------------------------------
# list_transaction_facts
# ---------------------------------------------------------------------------


@pytest.fixture
async def seeded_facts_pool(pool_with_owner):
    """Pool with pre-seeded transaction facts for filter testing."""
    with patch(
        "butlers.tools.finance.facts._get_embedding_engine",
        return_value=_mock_embedding_engine(),
    ):
        from butlers.tools.finance.facts import record_transaction_fact

        base = _utcnow()
        txns = [
            {
                "posted_at": base - timedelta(days=10),
                "merchant": "Trader Joe's",
                "amount": -55.00,
                "currency": "USD",
                "category": "groceries",
            },
            {
                "posted_at": base - timedelta(days=8),
                "merchant": "Netflix",
                "amount": -15.49,
                "currency": "USD",
                "category": "subscriptions",
            },
            {
                "posted_at": base - timedelta(days=5),
                "merchant": "Starbucks",
                "amount": -6.75,
                "currency": "USD",
                "category": "dining",
            },
            {
                "posted_at": base - timedelta(days=3),
                "merchant": "Whole Foods",
                "amount": -120.00,
                "currency": "USD",
                "category": "groceries",
            },
            {
                "posted_at": base - timedelta(days=1),
                "merchant": "Amazon",
                "amount": -39.99,
                "currency": "USD",
                "category": "shopping",
            },
        ]
        for txn in txns:
            await record_transaction_fact(pool=pool_with_owner, **txn)

    return pool_with_owner


class TestListTransactionFacts:
    """Tests for list_transaction_facts — filtered, paginated fact queries."""

    async def test_returns_all_seeded(self, seeded_facts_pool):
        from butlers.tools.finance.facts import list_transaction_facts

        result = await list_transaction_facts(pool=seeded_facts_pool)
        assert result["total"] == 5
        assert len(result["items"]) == 5

    async def test_filter_by_category(self, seeded_facts_pool):
        from butlers.tools.finance.facts import list_transaction_facts

        result = await list_transaction_facts(pool=seeded_facts_pool, category="groceries")
        assert result["total"] == 2
        for item in result["items"]:
            assert item["category"] == "groceries"

    async def test_filter_by_merchant(self, seeded_facts_pool):
        from butlers.tools.finance.facts import list_transaction_facts

        result = await list_transaction_facts(pool=seeded_facts_pool, merchant="netflix")
        assert result["total"] == 1
        assert result["items"][0]["merchant"] == "Netflix"

    async def test_filter_by_start_date(self, seeded_facts_pool):
        from butlers.tools.finance.facts import list_transaction_facts

        cutoff = _utcnow() - timedelta(days=6)
        result = await list_transaction_facts(pool=seeded_facts_pool, start_date=cutoff)
        # Starbucks (-5d), Whole Foods (-3d), Amazon (-1d)
        assert result["total"] == 3

    async def test_filter_by_end_date(self, seeded_facts_pool):
        from butlers.tools.finance.facts import list_transaction_facts

        cutoff = _utcnow() - timedelta(days=7)
        result = await list_transaction_facts(pool=seeded_facts_pool, end_date=cutoff)
        # Trader Joe's (-10d), Netflix (-8d)
        assert result["total"] == 2

    async def test_filter_min_amount(self, seeded_facts_pool):
        from butlers.tools.finance.facts import list_transaction_facts

        result = await list_transaction_facts(pool=seeded_facts_pool, min_amount=50.00)
        assert result["total"] == 2
        for item in result["items"]:
            assert Decimal(item["amount"]) >= Decimal("50.00")

    async def test_filter_max_amount(self, seeded_facts_pool):
        from butlers.tools.finance.facts import list_transaction_facts

        result = await list_transaction_facts(pool=seeded_facts_pool, max_amount=20.00)
        assert result["total"] == 2
        for item in result["items"]:
            assert Decimal(item["amount"]) <= Decimal("20.00")

    async def test_pagination_limit(self, seeded_facts_pool):
        from butlers.tools.finance.facts import list_transaction_facts

        result = await list_transaction_facts(pool=seeded_facts_pool, limit=2)
        assert result["total"] == 5
        assert len(result["items"]) == 2
        assert result["limit"] == 2

    async def test_pagination_offset(self, seeded_facts_pool):
        from butlers.tools.finance.facts import list_transaction_facts

        page1 = await list_transaction_facts(pool=seeded_facts_pool, limit=3, offset=0)
        page2 = await list_transaction_facts(pool=seeded_facts_pool, limit=3, offset=3)
        ids1 = {i["id"] for i in page1["items"]}
        ids2 = {i["id"] for i in page2["items"]}
        assert ids1.isdisjoint(ids2)
        assert len(page2["items"]) == 2

    async def test_empty_result(self, pool_with_owner):
        from butlers.tools.finance.facts import list_transaction_facts

        result = await list_transaction_facts(pool=pool_with_owner)
        assert result["total"] == 0
        assert result["items"] == []

    async def test_sorted_by_valid_at_desc(self, seeded_facts_pool):
        from butlers.tools.finance.facts import list_transaction_facts

        result = await list_transaction_facts(pool=seeded_facts_pool)
        posted_ats = [item["posted_at"] for item in result["items"]]
        assert posted_ats == sorted(posted_ats, reverse=True)

    async def test_response_schema(self, seeded_facts_pool):
        from butlers.tools.finance.facts import list_transaction_facts

        result = await list_transaction_facts(pool=seeded_facts_pool, limit=1)
        assert "items" in result
        assert "total" in result
        assert "limit" in result
        assert "offset" in result
        item = result["items"][0]
        for field in ("id", "direction", "merchant", "amount", "currency", "category", "posted_at"):
            assert field in item


# ---------------------------------------------------------------------------
# track_account_fact
# ---------------------------------------------------------------------------


class TestTrackAccountFact:
    """Tests for track_account_fact — property fact creation."""

    async def test_creates_account_fact(self, pool_with_owner):
        with patch(
            "butlers.tools.finance.facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            from butlers.tools.finance.facts import track_account_fact

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
            "butlers.tools.finance.facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            from butlers.tools.finance.facts import track_account_fact

            result = await track_account_fact(
                pool=pool_with_owner,
                institution="Wells Fargo",
                type="savings",
                last_four="5678",
            )

        assert result["content"] == "Wells Fargo savings ****5678"

    async def test_content_without_last_four(self, pool_with_owner):
        with patch(
            "butlers.tools.finance.facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            from butlers.tools.finance.facts import track_account_fact

            result = await track_account_fact(
                pool=pool_with_owner,
                institution="Amex",
                type="credit",
            )

        assert result["content"] == "Amex credit"

    async def test_is_property_fact_valid_at_null(self, pool_with_owner):
        """Account facts are property facts: valid_at IS NULL."""
        with patch(
            "butlers.tools.finance.facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            from butlers.tools.finance.facts import track_account_fact

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
            "butlers.tools.finance.facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            from butlers.tools.finance.facts import track_account_fact

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
            "butlers.tools.finance.facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            from butlers.tools.finance.facts import track_account_fact

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


# ---------------------------------------------------------------------------
# track_subscription_fact
# ---------------------------------------------------------------------------


class TestTrackSubscriptionFact:
    """Tests for track_subscription_fact — property fact creation."""

    async def test_creates_subscription_fact(self, pool_with_owner):
        with patch(
            "butlers.tools.finance.facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            from butlers.tools.finance.facts import track_subscription_fact

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
            "butlers.tools.finance.facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            from butlers.tools.finance.facts import track_subscription_fact

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
            "butlers.tools.finance.facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            from butlers.tools.finance.facts import track_subscription_fact

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
            "butlers.tools.finance.facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            from butlers.tools.finance.facts import track_subscription_fact

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
            "butlers.tools.finance.facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            from butlers.tools.finance.facts import track_subscription_fact

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
            "butlers.tools.finance.facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            from butlers.tools.finance.facts import track_subscription_fact

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


# ---------------------------------------------------------------------------
# track_bill_fact
# ---------------------------------------------------------------------------


class TestTrackBillFact:
    """Tests for track_bill_fact — property fact creation."""

    async def test_creates_bill_fact(self, pool_with_owner):
        with patch(
            "butlers.tools.finance.facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            from butlers.tools.finance.facts import track_bill_fact

            due = date.today() + timedelta(days=7)
            result = await track_bill_fact(
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
            "butlers.tools.finance.facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            from butlers.tools.finance.facts import track_bill_fact

            with pytest.raises(ValueError, match="Invalid status"):
                await track_bill_fact(
                    pool=pool_with_owner,
                    payee="Phone",
                    amount=50.00,
                    currency="USD",
                    due_date=date.today() + timedelta(days=3),
                    status="unpaid",
                )

    async def test_invalid_frequency_raises(self, pool_with_owner):
        with patch(
            "butlers.tools.finance.facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            from butlers.tools.finance.facts import track_bill_fact

            with pytest.raises(ValueError, match="Invalid frequency"):
                await track_bill_fact(
                    pool=pool_with_owner,
                    payee="Phone",
                    amount=50.00,
                    currency="USD",
                    due_date=date.today() + timedelta(days=3),
                    frequency="biweekly",
                )

    async def test_is_property_fact_valid_at_null(self, pool_with_owner):
        with patch(
            "butlers.tools.finance.facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            from butlers.tools.finance.facts import track_bill_fact

            await track_bill_fact(
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
            "butlers.tools.finance.facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            from butlers.tools.finance.facts import track_bill_fact

            due_str = (date.today() + timedelta(days=7)).isoformat()
            result = await track_bill_fact(
                pool=pool_with_owner,
                payee="Credit Card",
                amount=300.00,
                currency="USD",
                due_date=due_str,
            )
        assert result["due_date"] == due_str

    async def test_paid_status_with_paid_at(self, pool_with_owner):
        with patch(
            "butlers.tools.finance.facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            from butlers.tools.finance.facts import track_bill_fact

            paid_time = _utcnow()
            result = await track_bill_fact(
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
            "butlers.tools.finance.facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            from butlers.tools.finance.facts import track_bill_fact

            due = date.today() + timedelta(days=10)
            await track_bill_fact(
                pool=pool_with_owner,
                payee="Rent",
                amount=1800.00,
                currency="USD",
                due_date=due,
                status="pending",
            )
            await track_bill_fact(
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


# ---------------------------------------------------------------------------
# spending_summary_facts
# ---------------------------------------------------------------------------


class TestSpendingSummaryFacts:
    """Tests for spending_summary_facts — JSONB aggregation over transaction facts."""

    @pytest.fixture
    async def seeded(self, pool_with_owner):
        """Pool with debit transaction facts for the current month."""
        with patch(
            "butlers.tools.finance.facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            from butlers.tools.finance.facts import record_transaction_fact

            now = _utcnow()
            entries = [
                ("Whole Foods", -80.00, "groceries"),
                ("Blue Bottle", -20.00, "dining"),
                ("Safeway", -50.00, "groceries"),
                ("Netflix", -15.49, "subscriptions"),
            ]
            for merchant, amount, category in entries:
                await record_transaction_fact(
                    pool=pool_with_owner,
                    posted_at=now,
                    merchant=merchant,
                    amount=amount,
                    currency="USD",
                    category=category,
                )
            # Add a credit that should NOT be counted
            await record_transaction_fact(
                pool=pool_with_owner,
                posted_at=now,
                merchant="PayPal Refund",
                amount=100.00,
                currency="USD",
                category="refunds",
            )
        return pool_with_owner

    async def test_excludes_credit_direction(self, seeded):
        from butlers.tools.finance.facts import spending_summary_facts

        result = await spending_summary_facts(pool=seeded)
        # Only debits: 80+20+50+15.49 = 165.49
        assert Decimal(result["total_spend"]) == Decimal("165.49")

    async def test_empty_returns_zero(self, pool_with_owner):
        from butlers.tools.finance.facts import spending_summary_facts

        result = await spending_summary_facts(pool=pool_with_owner)
        assert Decimal(result["total_spend"]) == Decimal("0")

    async def test_group_by_category(self, seeded):
        from butlers.tools.finance.facts import spending_summary_facts

        result = await spending_summary_facts(pool=seeded, group_by="category")
        keys = {g["key"] for g in result["groups"]}
        assert "groceries" in keys
        assert "dining" in keys

        grocery = next(g for g in result["groups"] if g["key"] == "groceries")
        assert Decimal(grocery["amount"]) == Decimal("130.00")
        assert grocery["count"] == 2

    async def test_group_by_merchant(self, seeded):
        from butlers.tools.finance.facts import spending_summary_facts

        result = await spending_summary_facts(pool=seeded, group_by="merchant")
        keys = {g["key"] for g in result["groups"]}
        assert "Whole Foods" in keys
        assert "Netflix" in keys

    async def test_category_filter(self, seeded):
        from butlers.tools.finance.facts import spending_summary_facts

        result = await spending_summary_facts(pool=seeded, category_filter="groceries")
        assert Decimal(result["total_spend"]) == Decimal("130.00")

    async def test_invalid_group_by_raises(self, pool_with_owner):
        from butlers.tools.finance.facts import spending_summary_facts

        with pytest.raises(ValueError, match="Unsupported group_by"):
            await spending_summary_facts(pool=pool_with_owner, group_by="invalid_mode")

    async def test_return_shape(self, seeded):
        from butlers.tools.finance.facts import spending_summary_facts

        result = await spending_summary_facts(pool=seeded, group_by="category")
        assert set(result.keys()) == {"start_date", "end_date", "currency", "total_spend", "groups"}
        assert isinstance(result["groups"], list)

    async def test_group_by_week(self, pool_with_owner):
        from butlers.tools.finance.facts import spending_summary_facts

        with patch(
            "butlers.tools.finance.facts._get_embedding_engine",
            return_value=_mock_embedding_engine(),
        ):
            from butlers.tools.finance.facts import record_transaction_fact

            today = _utcnow()
            week1 = today - timedelta(days=14)
            week2 = today - timedelta(days=7)

            await record_transaction_fact(
                pool=pool_with_owner,
                posted_at=week1,
                merchant="A",
                amount=-30.00,
                currency="USD",
                category="misc",
            )
            await record_transaction_fact(
                pool=pool_with_owner,
                posted_at=week2,
                merchant="B",
                amount=-50.00,
                currency="USD",
                category="misc",
            )

        start = (today - timedelta(days=21)).date()
        end = today.date()
        result = await spending_summary_facts(
            pool=pool_with_owner, start_date=start, end_date=end, group_by="week"
        )
        assert len(result["groups"]) >= 2
        for g in result["groups"]:
            assert "W" in g["key"]

    async def test_string_dates_accepted(self, seeded):
        from butlers.tools.finance.facts import spending_summary_facts

        today = _utcnow().date()
        result = await spending_summary_facts(
            pool=seeded,
            start_date=today.replace(day=1).isoformat(),
            end_date=today.isoformat(),
        )
        # Should not raise; total should include the seeded debit transactions
        assert Decimal(result["total_spend"]) >= Decimal("0")
