"""Tests for finance backfill script (bu-ofv8).

Covers:
- Correct JSONB extraction from SPO transaction facts
- Deduplication against existing finance.transactions rows (NOT EXISTS logic)
- Skipped row logging: malformed amounts, missing required fields
- Idempotency: running twice does not insert duplicate rows
- Provenance tag: backfilled rows include backfilled_from_fact_id in metadata
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

pytestmark = [
    pytest.mark.asyncio(loop_scope="session"),
]

# ---------------------------------------------------------------------------
# DDL helpers — minimal schema that covers both tables under test
# ---------------------------------------------------------------------------

_DDL_FACTS = """
CREATE TABLE IF NOT EXISTS facts (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject             TEXT NOT NULL DEFAULT 'owner',
    predicate           TEXT NOT NULL,
    content             TEXT NOT NULL DEFAULT '',
    embedding           TEXT,
    search_vector       TSVECTOR,
    importance          FLOAT NOT NULL DEFAULT 5.0,
    confidence          FLOAT NOT NULL DEFAULT 1.0,
    decay_rate          FLOAT NOT NULL DEFAULT 0.002,
    permanence          TEXT NOT NULL DEFAULT 'stable',
    source_butler       TEXT,
    source_episode_id   UUID,
    supersedes_id       UUID,
    validity            TEXT NOT NULL DEFAULT 'active',
    scope               TEXT NOT NULL DEFAULT 'global',
    reference_count     INTEGER NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_confirmed_at   TIMESTAMPTZ,
    tags                JSONB DEFAULT '[]'::jsonb,
    metadata            JSONB DEFAULT '{}'::jsonb,
    entity_id           UUID,
    object_entity_id    UUID,
    valid_at            TIMESTAMPTZ DEFAULT NULL,
    tenant_id           TEXT NOT NULL DEFAULT 'owner',
    request_id          TEXT,
    idempotency_key     TEXT,
    observed_at         TIMESTAMPTZ DEFAULT now(),
    invalid_at          TIMESTAMPTZ,
    retention_class     TEXT NOT NULL DEFAULT 'operational',
    sensitivity         TEXT NOT NULL DEFAULT 'normal'
)
"""

_DDL_ACCOUNTS = """
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
"""

_DDL_TRANSACTIONS = """
CREATE TABLE IF NOT EXISTS transactions (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id        UUID REFERENCES accounts(id) ON DELETE SET NULL,
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

_DDL_DEDUP_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS uq_transactions_dedupe
    ON transactions (source_message_id, merchant, amount, posted_at)
    WHERE source_message_id IS NOT NULL
"""


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _fact_meta(**kwargs: object) -> str:
    """Build a JSON-encoded metadata dict for a fact row."""
    return json.dumps(kwargs)


@pytest.fixture
async def pool(provisioned_postgres_pool):
    """Provision a fresh database with facts + transactions tables."""
    async with provisioned_postgres_pool() as p:
        await p.execute(_DDL_FACTS)
        await p.execute(_DDL_ACCOUNTS)
        await p.execute(_DDL_TRANSACTIONS)
        await p.execute(_DDL_DEDUP_INDEX)
        yield p


# ---------------------------------------------------------------------------
# Helpers to seed fact rows directly
# ---------------------------------------------------------------------------


async def _insert_fact(
    pool,
    *,
    predicate: str = "transaction_debit",
    scope: str = "finance",
    validity: str = "active",
    valid_at: datetime | None = None,
    metadata: dict | None = None,
) -> str:
    """Insert a raw fact row and return its UUID as a string."""
    if valid_at is None:
        valid_at = _utcnow()
    meta_json = json.dumps(metadata or {})
    row = await pool.fetchrow(
        """
        INSERT INTO facts (predicate, scope, validity, valid_at, metadata, content)
        VALUES ($1, $2, $3, $4, $5::jsonb, 'test')
        RETURNING id
        """,
        predicate,
        scope,
        validity,
        valid_at,
        meta_json,
    )
    return str(row["id"])


async def _transaction_count(pool) -> int:
    return await pool.fetchval("SELECT COUNT(*) FROM transactions")


# ---------------------------------------------------------------------------
# Tests: JSONB extraction — correct values
# ---------------------------------------------------------------------------


class TestJsonbExtraction:
    """Verify that field values are correctly extracted from fact metadata."""

    async def test_extracts_merchant(self, pool):
        from butlers.tools.finance.backfill import backfill_spo_transactions

        await _insert_fact(
            pool,
            metadata={
                "merchant": "Trader Joe's",
                "amount": "42.50",
                "currency": "USD",
                "category": "groceries",
                "direction": "debit",
            },
        )
        result = await backfill_spo_transactions(pool)
        assert result.inserted == 1
        row = await pool.fetchrow("SELECT * FROM transactions")
        assert row["merchant"] == "Trader Joe's"

    async def test_extracts_amount_as_decimal(self, pool):
        from butlers.tools.finance.backfill import backfill_spo_transactions

        await _insert_fact(
            pool,
            metadata={
                "merchant": "Netflix",
                "amount": "15.49",
                "currency": "USD",
                "category": "subscriptions",
            },
        )
        result = await backfill_spo_transactions(pool)
        assert result.inserted == 1
        row = await pool.fetchrow("SELECT * FROM transactions")
        assert Decimal(str(row["amount"])) == Decimal("15.49")

    async def test_extracts_currency_uppercased(self, pool):
        from butlers.tools.finance.backfill import backfill_spo_transactions

        await _insert_fact(
            pool,
            metadata={
                "merchant": "Amazon",
                "amount": "29.99",
                "currency": "usd",  # lowercase in metadata
                "category": "shopping",
            },
        )
        result = await backfill_spo_transactions(pool)
        assert result.inserted == 1
        row = await pool.fetchrow("SELECT * FROM transactions")
        assert row["currency"] == "USD"

    async def test_extracts_direction_from_metadata(self, pool):
        from butlers.tools.finance.backfill import backfill_spo_transactions

        await _insert_fact(
            pool,
            predicate="transaction_credit",
            metadata={
                "merchant": "PayPal Refund",
                "amount": "50.00",
                "currency": "USD",
                "category": "refunds",
                "direction": "credit",
            },
        )
        result = await backfill_spo_transactions(pool)
        assert result.inserted == 1
        row = await pool.fetchrow("SELECT * FROM transactions")
        assert row["direction"] == "credit"

    async def test_infers_direction_from_predicate_when_missing(self, pool):
        from butlers.tools.finance.backfill import backfill_spo_transactions

        # No 'direction' field in metadata — should infer from predicate
        await _insert_fact(
            pool,
            predicate="transaction_debit",
            metadata={
                "merchant": "Starbucks",
                "amount": "5.75",
                "currency": "USD",
                "category": "dining",
                # direction intentionally absent
            },
        )
        result = await backfill_spo_transactions(pool)
        assert result.inserted == 1
        row = await pool.fetchrow("SELECT * FROM transactions")
        assert row["direction"] == "debit"

    async def test_extracts_optional_fields(self, pool):
        from butlers.tools.finance.backfill import backfill_spo_transactions

        await _insert_fact(
            pool,
            metadata={
                "merchant": "Amazon",
                "amount": "9.99",
                "currency": "USD",
                "category": "shopping",
                "description": "Prime membership",
                "payment_method": "Amex",
                "receipt_url": "https://example.com/r/1",
                "external_ref": "ext-001",
                "source_message_id": "msg-abc-001",
            },
        )
        result = await backfill_spo_transactions(pool)
        assert result.inserted == 1
        row = await pool.fetchrow("SELECT * FROM transactions")
        assert row["description"] == "Prime membership"
        assert row["payment_method"] == "Amex"
        assert row["receipt_url"] == "https://example.com/r/1"
        assert row["external_ref"] == "ext-001"
        assert row["source_message_id"] == "msg-abc-001"

    async def test_uses_valid_at_as_posted_at(self, pool):
        from butlers.tools.finance.backfill import backfill_spo_transactions

        expected_dt = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
        await _insert_fact(
            pool,
            valid_at=expected_dt,
            metadata={
                "merchant": "IKEA",
                "amount": "249.99",
                "currency": "USD",
                "category": "home",
            },
        )
        result = await backfill_spo_transactions(pool)
        assert result.inserted == 1
        row = await pool.fetchrow("SELECT * FROM transactions")
        # posted_at should match valid_at (timezone-normalized)
        assert row["posted_at"].replace(tzinfo=UTC) == expected_dt

    async def test_amount_stored_as_absolute_value(self, pool):
        """Negative amounts in metadata should be stored as absolute values."""
        from butlers.tools.finance.backfill import backfill_spo_transactions

        # Some older facts may store amount as negative string
        await _insert_fact(
            pool,
            metadata={
                "merchant": "Whole Foods",
                "amount": "-35.00",
                "currency": "USD",
                "category": "groceries",
            },
        )
        result = await backfill_spo_transactions(pool)
        assert result.inserted == 1
        row = await pool.fetchrow("SELECT * FROM transactions")
        assert Decimal(str(row["amount"])) == Decimal("35.00")

    async def test_provenance_tag_in_metadata(self, pool):
        """Backfilled rows should have backfilled_from_fact_id in metadata."""
        from butlers.tools.finance.backfill import backfill_spo_transactions

        fact_id = await _insert_fact(
            pool,
            metadata={
                "merchant": "Costco",
                "amount": "120.00",
                "currency": "USD",
                "category": "groceries",
            },
        )
        await backfill_spo_transactions(pool)
        row = await pool.fetchrow("SELECT metadata FROM transactions")
        meta = row["metadata"] if isinstance(row["metadata"], dict) else json.loads(row["metadata"])
        assert meta.get("backfilled_from_fact_id") == fact_id


# ---------------------------------------------------------------------------
# Tests: deduplication against existing rows
# ---------------------------------------------------------------------------


class TestDeduplication:
    """Verify NOT EXISTS dedup prevents duplicate inserts."""

    async def test_dedup_by_source_message_id(self, pool):
        """Row with matching source_message_id+merchant+amount+posted_at is skipped."""
        from butlers.tools.finance.backfill import backfill_spo_transactions

        posted_at = _utcnow()
        # Pre-insert a matching row in finance.transactions
        await pool.execute(
            """
            INSERT INTO transactions
                (source_message_id, posted_at, merchant, amount, currency, direction, category)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            "msg-dup-001",
            posted_at,
            "Trader Joe's",
            Decimal("42.50"),
            "USD",
            "debit",
            "groceries",
        )

        # Insert a fact with the same dedup key
        await _insert_fact(
            pool,
            valid_at=posted_at,
            metadata={
                "merchant": "Trader Joe's",
                "amount": "42.50",
                "currency": "USD",
                "category": "groceries",
                "source_message_id": "msg-dup-001",
            },
        )
        result = await backfill_spo_transactions(pool)
        assert result.inserted == 0
        assert result.skipped == 1
        assert "duplicate" in result.skipped_rows[0].reason
        # Count should still be 1 (the pre-existing row)
        assert await _transaction_count(pool) == 1

    async def test_dedup_by_composite_fallback(self, pool):
        """Row without source_message_id deduped by posted_at+merchant+amount+currency."""
        from butlers.tools.finance.backfill import backfill_spo_transactions

        posted_at = datetime(2025, 3, 10, 10, 0, 0, tzinfo=UTC)
        # Pre-insert a matching row with no source_message_id or external_ref
        await pool.execute(
            """
            INSERT INTO transactions
                (posted_at, merchant, amount, currency, direction, category)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            posted_at,
            "Costco",
            Decimal("89.99"),
            "USD",
            "debit",
            "groceries",
        )

        # Insert a fact with the same composite key
        await _insert_fact(
            pool,
            valid_at=posted_at,
            metadata={
                "merchant": "Costco",
                "amount": "89.99",
                "currency": "USD",
                "category": "groceries",
                # no source_message_id — triggers composite fallback
            },
        )
        result = await backfill_spo_transactions(pool)
        assert result.inserted == 0
        assert result.skipped == 1
        # Still only 1 row
        assert await _transaction_count(pool) == 1

    async def test_no_dedup_for_distinct_transactions(self, pool):
        """Different amounts/merchants are inserted without dedup."""
        from butlers.tools.finance.backfill import backfill_spo_transactions

        posted_at = _utcnow()
        # Pre-insert a row for Amazon
        await pool.execute(
            """
            INSERT INTO transactions
                (posted_at, merchant, amount, currency, direction, category)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            posted_at,
            "Amazon",
            Decimal("29.99"),
            "USD",
            "debit",
            "shopping",
        )

        # Insert a fact for a different merchant
        await _insert_fact(
            pool,
            valid_at=posted_at,
            metadata={
                "merchant": "Netflix",
                "amount": "15.49",
                "currency": "USD",
                "category": "subscriptions",
            },
        )
        result = await backfill_spo_transactions(pool)
        assert result.inserted == 1
        assert result.skipped == 0
        assert await _transaction_count(pool) == 2

    async def test_idempotent_double_run(self, pool):
        """Running backfill twice does not insert duplicate rows."""
        from butlers.tools.finance.backfill import backfill_spo_transactions

        await _insert_fact(
            pool,
            metadata={
                "merchant": "Spotify",
                "amount": "9.99",
                "currency": "USD",
                "category": "subscriptions",
            },
        )
        result1 = await backfill_spo_transactions(pool)
        result2 = await backfill_spo_transactions(pool)

        assert result1.inserted == 1
        assert result2.inserted == 0
        assert result2.skipped == 1
        # Only one row in the table
        assert await _transaction_count(pool) == 1

    async def test_multiple_facts_some_new_some_duplicate(self, pool):
        """Mixed batch: new facts are inserted, duplicates are skipped."""
        from butlers.tools.finance.backfill import backfill_spo_transactions

        posted_at = datetime(2025, 5, 1, 9, 0, 0, tzinfo=UTC)

        # Pre-insert one existing transaction
        await pool.execute(
            """
            INSERT INTO transactions
                (source_message_id, posted_at, merchant, amount, currency, direction, category)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            "msg-exists-001",
            posted_at,
            "Trader Joe's",
            Decimal("55.00"),
            "USD",
            "debit",
            "groceries",
        )

        # Fact 1: duplicate of the existing row
        await _insert_fact(
            pool,
            valid_at=posted_at,
            metadata={
                "merchant": "Trader Joe's",
                "amount": "55.00",
                "currency": "USD",
                "category": "groceries",
                "source_message_id": "msg-exists-001",
            },
        )
        # Fact 2: brand new transaction
        await _insert_fact(
            pool,
            metadata={
                "merchant": "Whole Foods",
                "amount": "32.10",
                "currency": "USD",
                "category": "groceries",
            },
        )

        result = await backfill_spo_transactions(pool)
        assert result.inserted == 1
        assert result.skipped == 1
        assert await _transaction_count(pool) == 2


# ---------------------------------------------------------------------------
# Tests: skipped row logging
# ---------------------------------------------------------------------------


class TestSkippedRowLogging:
    """Verify that rows with malformed/missing fields are skipped with reasons."""

    async def test_skips_missing_merchant(self, pool):
        from butlers.tools.finance.backfill import backfill_spo_transactions

        await _insert_fact(
            pool,
            metadata={
                # merchant is missing
                "amount": "10.00",
                "currency": "USD",
                "category": "misc",
            },
        )
        result = await backfill_spo_transactions(pool)
        assert result.inserted == 0
        assert result.skipped == 1
        assert "merchant" in result.skipped_rows[0].reason
        assert await _transaction_count(pool) == 0

    async def test_skips_missing_amount(self, pool):
        from butlers.tools.finance.backfill import backfill_spo_transactions

        await _insert_fact(
            pool,
            metadata={
                "merchant": "Unknown Store",
                # amount is missing
                "currency": "USD",
                "category": "misc",
            },
        )
        result = await backfill_spo_transactions(pool)
        assert result.inserted == 0
        assert result.skipped == 1
        assert "amount" in result.skipped_rows[0].reason

    async def test_skips_malformed_amount_non_numeric(self, pool):
        from butlers.tools.finance.backfill import backfill_spo_transactions

        await _insert_fact(
            pool,
            metadata={
                "merchant": "Mystery Shop",
                "amount": "not-a-number",
                "currency": "USD",
                "category": "misc",
            },
        )
        result = await backfill_spo_transactions(pool)
        assert result.inserted == 0
        assert result.skipped == 1
        assert "amount" in result.skipped_rows[0].reason

    async def test_skips_malformed_amount_empty_string(self, pool):
        from butlers.tools.finance.backfill import backfill_spo_transactions

        await _insert_fact(
            pool,
            metadata={
                "merchant": "Mystery Shop",
                "amount": "",
                "currency": "USD",
                "category": "misc",
            },
        )
        result = await backfill_spo_transactions(pool)
        assert result.inserted == 0
        assert result.skipped == 1
        assert "amount" in result.skipped_rows[0].reason

    async def test_skips_missing_currency(self, pool):
        from butlers.tools.finance.backfill import backfill_spo_transactions

        await _insert_fact(
            pool,
            metadata={
                "merchant": "Acme Corp",
                "amount": "25.00",
                # currency is missing
                "category": "services",
            },
        )
        result = await backfill_spo_transactions(pool)
        assert result.inserted == 0
        assert result.skipped == 1
        assert "currency" in result.skipped_rows[0].reason

    async def test_skips_missing_category(self, pool):
        from butlers.tools.finance.backfill import backfill_spo_transactions

        await _insert_fact(
            pool,
            metadata={
                "merchant": "Acme Corp",
                "amount": "25.00",
                "currency": "USD",
                # category is missing
            },
        )
        result = await backfill_spo_transactions(pool)
        assert result.inserted == 0
        assert result.skipped == 1
        assert "category" in result.skipped_rows[0].reason

    async def test_skipped_row_includes_raw_metadata(self, pool):
        """SkippedRow stores the raw metadata for diagnostics."""
        from butlers.tools.finance.backfill import backfill_spo_transactions

        await _insert_fact(
            pool,
            metadata={
                "merchant": "Broken Store",
                "amount": "bad",
                "currency": "USD",
                "category": "misc",
            },
        )
        result = await backfill_spo_transactions(pool)
        assert result.skipped == 1
        skipped = result.skipped_rows[0]
        assert skipped.raw_metadata.get("merchant") == "Broken Store"
        assert skipped.raw_metadata.get("amount") == "bad"

    async def test_skipped_row_includes_fact_id(self, pool):
        """SkippedRow stores the source fact_id for traceability."""
        from butlers.tools.finance.backfill import backfill_spo_transactions

        fact_id = await _insert_fact(
            pool,
            metadata={
                "merchant": "Broken Store",
                "amount": "invalid",
                "currency": "USD",
                "category": "misc",
            },
        )
        result = await backfill_spo_transactions(pool)
        assert result.skipped == 1
        assert result.skipped_rows[0].fact_id == fact_id

    async def test_skips_inactive_facts(self, pool):
        """Facts with validity != 'active' are not picked up by the backfill."""
        from butlers.tools.finance.backfill import backfill_spo_transactions

        await _insert_fact(
            pool,
            validity="superseded",
            metadata={
                "merchant": "Old Store",
                "amount": "10.00",
                "currency": "USD",
                "category": "misc",
            },
        )
        result = await backfill_spo_transactions(pool)
        assert result.inserted == 0
        assert result.skipped == 0
        assert await _transaction_count(pool) == 0

    async def test_skips_non_finance_scope(self, pool):
        """Facts from a different scope are ignored."""
        from butlers.tools.finance.backfill import backfill_spo_transactions

        await _insert_fact(
            pool,
            scope="global",  # not 'finance'
            metadata={
                "merchant": "Other Store",
                "amount": "10.00",
                "currency": "USD",
                "category": "misc",
            },
        )
        result = await backfill_spo_transactions(pool)
        assert result.inserted == 0
        assert result.skipped == 0

    async def test_partial_bad_batch_continues(self, pool):
        """A bad row in a batch does not stop processing of subsequent rows."""
        from butlers.tools.finance.backfill import backfill_spo_transactions

        # Bad fact (missing currency)
        await _insert_fact(
            pool,
            metadata={
                "merchant": "Bad Store",
                "amount": "invalid",
                "category": "misc",
            },
        )
        # Good fact
        await _insert_fact(
            pool,
            metadata={
                "merchant": "Good Store",
                "amount": "20.00",
                "currency": "USD",
                "category": "shopping",
            },
        )
        result = await backfill_spo_transactions(pool)
        assert result.inserted == 1
        assert result.skipped == 1
        assert await _transaction_count(pool) == 1


# ---------------------------------------------------------------------------
# Tests: BackfillResult shape
# ---------------------------------------------------------------------------


class TestBackfillResult:
    """Verify the BackfillResult data model and to_dict() serialization."""

    async def test_empty_facts_table_returns_zero_counts(self, pool):
        from butlers.tools.finance.backfill import backfill_spo_transactions

        result = await backfill_spo_transactions(pool)
        assert result.inserted == 0
        assert result.skipped == 0
        assert result.skipped_rows == []

    async def test_to_dict_structure(self, pool):
        from butlers.tools.finance.backfill import backfill_spo_transactions

        await _insert_fact(
            pool,
            metadata={
                "merchant": "TestCo",
                "amount": "bad-amount",
                "currency": "USD",
                "category": "test",
            },
        )
        result = await backfill_spo_transactions(pool)
        d = result.to_dict()
        assert "inserted" in d
        assert "skipped" in d
        assert "skipped_rows" in d
        assert isinstance(d["skipped_rows"], list)
        assert d["skipped_rows"][0]["fact_id"] is not None
        assert d["skipped_rows"][0]["reason"] is not None
        assert isinstance(d["skipped_rows"][0]["raw_metadata"], dict)
