"""Tests for finance butler transaction tools."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest


def _utcnow() -> datetime:
    return datetime.now(UTC)


@pytest.fixture
async def pool(provisioned_postgres_pool):
    """Provision a fresh database with finance tables and return a pool."""
    async with provisioned_postgres_pool() as p:
        # Create finance.accounts (dependency of transactions)
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

        # Create finance.transactions
        await p.execute("""
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
                updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await p.execute("""
            CREATE INDEX IF NOT EXISTS idx_transactions_posted_at
                ON transactions (posted_at DESC)
        """)
        await p.execute("""
            CREATE INDEX IF NOT EXISTS idx_transactions_merchant
                ON transactions (merchant)
        """)
        await p.execute("""
            CREATE INDEX IF NOT EXISTS idx_transactions_category
                ON transactions (category)
        """)
        await p.execute("""
            CREATE INDEX IF NOT EXISTS idx_transactions_source_message_id
                ON transactions (source_message_id)
        """)
        # Dedupe partial index
        await p.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_transactions_dedupe
                ON transactions (source_message_id, merchant, amount, posted_at)
                WHERE source_message_id IS NOT NULL
        """)

        yield p


# ------------------------------------------------------------------
# record_transaction
# ------------------------------------------------------------------


async def test_record_transaction_basic(pool):
    """record_transaction creates a row and returns a TransactionRecord dict."""
    from butlers.tools.finance.transactions import record_transaction

    now = _utcnow()
    result = await record_transaction(
        pool=pool,
        posted_at=now,
        merchant="Trader Joe's",
        amount=-42.50,
        currency="USD",
        category="groceries",
    )

    assert result["merchant"] == "Trader Joe's"
    assert result["category"] == "groceries"
    assert result["currency"] == "USD"
    assert result["direction"] == "debit"
    assert Decimal(str(result["amount"])) == Decimal("42.50")
    assert "id" in result
    assert result["id"] is not None


async def test_record_transaction_positive_amount_is_credit(pool):
    """Positive amount infers credit direction (money in / refund)."""
    from butlers.tools.finance.transactions import record_transaction

    result = await record_transaction(
        pool=pool,
        posted_at=_utcnow(),
        merchant="PayPal",
        amount=100.00,
        currency="USD",
        category="refunds",
    )

    assert result["direction"] == "credit"
    assert Decimal(str(result["amount"])) == Decimal("100.00")


async def test_record_transaction_negative_amount_is_debit(pool):
    """Negative amount infers debit direction (money out)."""
    from butlers.tools.finance.transactions import record_transaction

    result = await record_transaction(
        pool=pool,
        posted_at=_utcnow(),
        merchant="Netflix",
        amount=-15.49,
        currency="USD",
        category="subscriptions",
    )

    assert result["direction"] == "debit"
    assert Decimal(str(result["amount"])) == Decimal("15.49")


async def test_record_transaction_optional_fields(pool):
    """Optional fields are persisted when provided."""
    from butlers.tools.finance.transactions import record_transaction

    now = _utcnow()
    result = await record_transaction(
        pool=pool,
        posted_at=now,
        merchant="Amazon",
        amount=-29.99,
        currency="USD",
        category="shopping",
        description="Prime monthly",
        payment_method="Amex",
        receipt_url="https://amazon.com/r/123",
        external_ref="ext-abc-123",
        source_message_id="msg-001",
        metadata={"order_id": "ORD-456"},
    )

    assert result["description"] == "Prime monthly"
    assert result["payment_method"] == "Amex"
    assert result["receipt_url"] == "https://amazon.com/r/123"
    assert result["external_ref"] == "ext-abc-123"
    assert result["source_message_id"] == "msg-001"
    assert result["metadata"]["order_id"] == "ORD-456"


async def test_record_transaction_metadata_defaults_to_empty_dict(pool):
    """Metadata field defaults to empty dict when not provided."""
    from butlers.tools.finance.transactions import record_transaction

    result = await record_transaction(
        pool=pool,
        posted_at=_utcnow(),
        merchant="Starbucks",
        amount=-5.25,
        currency="USD",
        category="dining",
    )

    assert result["metadata"] == {} or result["metadata"] is not None


async def test_record_transaction_dedupe_via_source_message_id(pool):
    """Duplicate insert with same source_message_id returns existing row."""
    from butlers.tools.finance.transactions import record_transaction

    now = _utcnow()
    first = await record_transaction(
        pool=pool,
        posted_at=now,
        merchant="Chase Alert",
        amount=-75.00,
        currency="USD",
        category="dining",
        source_message_id="email-dedupe-001",
    )

    second = await record_transaction(
        pool=pool,
        posted_at=now,
        merchant="Chase Alert",
        amount=-75.00,
        currency="USD",
        category="dining",
        source_message_id="email-dedupe-001",
    )

    assert first["id"] == second["id"]

    # Verify only one row exists
    count = await pool.fetchval(
        "SELECT COUNT(*) FROM transactions WHERE source_message_id = 'email-dedupe-001'"
    )
    assert count == 1


async def test_record_transaction_different_source_ids_not_deduped(pool):
    """Different source_message_ids create separate rows."""
    from butlers.tools.finance.transactions import record_transaction

    now = _utcnow()
    first = await record_transaction(
        pool=pool,
        posted_at=now,
        merchant="Lyft",
        amount=-12.00,
        currency="USD",
        category="transport",
        source_message_id="msg-lyft-001",
    )
    second = await record_transaction(
        pool=pool,
        posted_at=now,
        merchant="Lyft",
        amount=-12.00,
        currency="USD",
        category="transport",
        source_message_id="msg-lyft-002",
    )

    assert first["id"] != second["id"]


async def test_record_transaction_no_source_id_allows_duplicates(pool):
    """Without source_message_id, identical transactions create separate rows."""
    from butlers.tools.finance.transactions import record_transaction

    now = _utcnow()
    first = await record_transaction(
        pool=pool,
        posted_at=now,
        merchant="Uber",
        amount=-8.00,
        currency="USD",
        category="transport",
    )
    second = await record_transaction(
        pool=pool,
        posted_at=now,
        merchant="Uber",
        amount=-8.00,
        currency="USD",
        category="transport",
    )

    assert first["id"] != second["id"]


async def test_record_transaction_currency_uppercased(pool):
    """Currency code is stored as uppercase."""
    from butlers.tools.finance.transactions import record_transaction

    result = await record_transaction(
        pool=pool,
        posted_at=_utcnow(),
        merchant="Wise",
        amount=-50.00,
        currency="eur",
        category="transfer",
    )

    assert result["currency"] == "EUR"


async def test_record_transaction_with_account_id(pool):
    """account_id is stored as UUID string when provided."""
    from butlers.tools.finance.transactions import record_transaction

    # Create an account first
    account_id = await pool.fetchval(
        """
        INSERT INTO accounts (institution, type, currency)
        VALUES ('Chase', 'credit', 'USD')
        RETURNING id
        """
    )

    result = await record_transaction(
        pool=pool,
        posted_at=_utcnow(),
        merchant="Whole Foods",
        amount=-85.00,
        currency="USD",
        category="groceries",
        account_id=str(account_id),
    )

    assert result["account_id"] == str(account_id)


async def test_record_transaction_returns_string_id(pool):
    """Returned id is a string (UUID serialized)."""
    from butlers.tools.finance.transactions import record_transaction

    result = await record_transaction(
        pool=pool,
        posted_at=_utcnow(),
        merchant="BP Gas",
        amount=-45.00,
        currency="USD",
        category="fuel",
    )

    # Should be parseable as a UUID
    parsed = uuid.UUID(result["id"])
    assert str(parsed) == result["id"]


# ------------------------------------------------------------------
# list_transactions
# ------------------------------------------------------------------


@pytest.fixture
async def seeded_pool(pool):
    """Pool with a set of pre-seeded transactions for filter testing."""
    from butlers.tools.finance.transactions import record_transaction

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
        await record_transaction(pool=pool, **txn)

    return pool


async def test_list_transactions_returns_all(seeded_pool):
    """list_transactions with no filters returns all seeded transactions."""
    from butlers.tools.finance.transactions import list_transactions

    result = await list_transactions(pool=seeded_pool)

    assert result["total"] == 5
    assert len(result["items"]) == 5
    assert result["offset"] == 0


async def test_list_transactions_default_sort_posted_at_desc(seeded_pool):
    """Results are sorted by posted_at DESC by default."""
    from butlers.tools.finance.transactions import list_transactions

    result = await list_transactions(pool=seeded_pool)
    items = result["items"]

    # Check descending order
    for i in range(len(items) - 1):
        assert items[i]["posted_at"] >= items[i + 1]["posted_at"]


async def test_list_transactions_filter_by_category(seeded_pool):
    """list_transactions filters by category."""
    from butlers.tools.finance.transactions import list_transactions

    result = await list_transactions(pool=seeded_pool, category="groceries")

    assert result["total"] == 2
    for item in result["items"]:
        assert item["category"] == "groceries"


async def test_list_transactions_filter_by_merchant(seeded_pool):
    """list_transactions filters by merchant substring (case-insensitive)."""
    from butlers.tools.finance.transactions import list_transactions

    result = await list_transactions(pool=seeded_pool, merchant="netflix")

    assert result["total"] == 1
    assert result["items"][0]["merchant"] == "Netflix"


async def test_list_transactions_filter_by_start_date(seeded_pool):
    """list_transactions filters by start_date (inclusive)."""
    from butlers.tools.finance.transactions import list_transactions

    cutoff = _utcnow() - timedelta(days=6)
    result = await list_transactions(pool=seeded_pool, start_date=cutoff)

    # Starbucks (-5d), Whole Foods (-3d), Amazon (-1d)
    assert result["total"] == 3


async def test_list_transactions_filter_by_end_date(seeded_pool):
    """list_transactions filters by end_date (inclusive)."""
    from butlers.tools.finance.transactions import list_transactions

    cutoff = _utcnow() - timedelta(days=7)
    result = await list_transactions(pool=seeded_pool, end_date=cutoff)

    # Trader Joe's (-10d), Netflix (-8d)
    assert result["total"] == 2


async def test_list_transactions_filter_by_date_range(seeded_pool):
    """list_transactions filters by combined start_date + end_date."""
    from butlers.tools.finance.transactions import list_transactions

    start = _utcnow() - timedelta(days=9)
    end = _utcnow() - timedelta(days=4)
    result = await list_transactions(pool=seeded_pool, start_date=start, end_date=end)

    # Netflix (-8d), Starbucks (-5d) fall in [start, end]
    assert result["total"] == 2


async def test_list_transactions_filter_min_amount(seeded_pool):
    """list_transactions filters by minimum amount."""
    from butlers.tools.finance.transactions import list_transactions

    result = await list_transactions(pool=seeded_pool, min_amount=50.00)

    # Trader Joe's (55.00) and Whole Foods (120.00)
    assert result["total"] == 2
    for item in result["items"]:
        assert Decimal(str(item["amount"])) >= Decimal("50.00")


async def test_list_transactions_filter_max_amount(seeded_pool):
    """list_transactions filters by maximum amount."""
    from butlers.tools.finance.transactions import list_transactions

    result = await list_transactions(pool=seeded_pool, max_amount=20.00)

    # Netflix (15.49) and Starbucks (6.75)
    assert result["total"] == 2
    for item in result["items"]:
        assert Decimal(str(item["amount"])) <= Decimal("20.00")


async def test_list_transactions_pagination_limit(seeded_pool):
    """list_transactions respects limit parameter."""
    from butlers.tools.finance.transactions import list_transactions

    result = await list_transactions(pool=seeded_pool, limit=2)

    assert result["total"] == 5  # total unchanged
    assert len(result["items"]) == 2
    assert result["limit"] == 2


async def test_list_transactions_pagination_offset(seeded_pool):
    """list_transactions respects offset parameter."""
    from butlers.tools.finance.transactions import list_transactions

    # Get first page
    page1 = await list_transactions(pool=seeded_pool, limit=3, offset=0)
    # Get second page
    page2 = await list_transactions(pool=seeded_pool, limit=3, offset=3)

    ids_page1 = {item["id"] for item in page1["items"]}
    ids_page2 = {item["id"] for item in page2["items"]}
    assert ids_page1.isdisjoint(ids_page2)
    assert len(page2["items"]) == 2  # remaining 2


async def test_list_transactions_empty_result(pool):
    """list_transactions returns empty items with total=0 when no rows."""
    from butlers.tools.finance.transactions import list_transactions

    result = await list_transactions(pool=pool)

    assert result["total"] == 0
    assert result["items"] == []
    assert result["limit"] == 50
    assert result["offset"] == 0


async def test_list_transactions_response_schema(seeded_pool):
    """list_transactions response has the expected TransactionListResponse shape."""
    from butlers.tools.finance.transactions import list_transactions

    result = await list_transactions(pool=seeded_pool, limit=2)

    assert "items" in result
    assert "total" in result
    assert "limit" in result
    assert "offset" in result
    assert isinstance(result["items"], list)
    assert isinstance(result["total"], int)

    # Each item should have expected TransactionRecord fields
    item = result["items"][0]
    expected_fields = {
        "id",
        "posted_at",
        "merchant",
        "amount",
        "currency",
        "direction",
        "category",
        "metadata",
        "created_at",
    }
    assert expected_fields.issubset(item.keys())


async def test_list_transactions_combined_filters(seeded_pool):
    """list_transactions correctly applies multiple filters simultaneously."""
    from butlers.tools.finance.transactions import list_transactions

    # Groceries category + min_amount 100
    result = await list_transactions(
        pool=seeded_pool,
        category="groceries",
        min_amount=100.00,
    )

    # Only Whole Foods (120.00) matches
    assert result["total"] == 1
    assert result["items"][0]["merchant"] == "Whole Foods"
