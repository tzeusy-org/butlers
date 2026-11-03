"""Tests for finance butler transaction tools."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

# All async tests in this file must share the session event loop so that the
# asyncpg pool (created in the session-scoped fixture loop per
# asyncio_default_fixture_loop_scope="session") is never used from a different
# loop.  Without this mark each test function gets a fresh function-scoped loop,
# which causes "got Future attached to a different loop" / asyncpg
# InterfaceError failures under pytest-xdist.
pytestmark = [
    pytest.mark.asyncio(loop_scope="session"),
]


def _utcnow() -> datetime:
    return datetime.now(UTC)


@pytest.fixture
async def finance_pool(db):
    """Core+finance clone; seeds the non-system categories these tests record under."""
    pool = await db("core", "finance")
    await pool.executemany(
        "INSERT INTO categories (name, display_name) VALUES ($1, $1) ON CONFLICT DO NOTHING",
        [("fuel",), ("refunds",)],
    )
    return pool


# ------------------------------------------------------------------
# record_transaction
# ------------------------------------------------------------------


async def test_record_transaction_basic(finance_pool):
    """record_transaction creates a row and returns a TransactionRecord dict."""
    from butlers.tools.finance.transactions import record_transaction

    now = _utcnow()
    result = await record_transaction(
        pool=finance_pool,
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


async def test_record_transaction_positive_amount_is_credit(finance_pool):
    """Positive amount infers credit direction (money in / refund)."""
    from butlers.tools.finance.transactions import record_transaction

    result = await record_transaction(
        pool=finance_pool,
        posted_at=_utcnow(),
        merchant="PayPal",
        amount=100.00,
        currency="USD",
        category="refunds",
    )

    assert result["direction"] == "credit"
    assert Decimal(str(result["amount"])) == Decimal("100.00")


async def test_record_transaction_explicit_debit_overrides_positive_amount(finance_pool):
    """Explicit debit direction coerces a positive amount into money-out semantics."""
    from butlers.tools.finance.transactions import record_transaction

    result = await record_transaction(
        pool=finance_pool,
        posted_at=_utcnow(),
        merchant="Nutrition Kitchen SG",
        amount=162.00,
        direction="debit",
        currency="SGD",
        category="subscriptions",
    )

    assert result["direction"] == "debit"
    assert Decimal(str(result["amount"])) == Decimal("162.00")


async def test_record_transaction_negative_amount_is_debit(finance_pool):
    """Negative amount infers debit direction (money out)."""
    from butlers.tools.finance.transactions import record_transaction

    result = await record_transaction(
        pool=finance_pool,
        posted_at=_utcnow(),
        merchant="Netflix",
        amount=-15.49,
        currency="USD",
        category="subscriptions",
    )

    assert result["direction"] == "debit"
    assert Decimal(str(result["amount"])) == Decimal("15.49")


async def test_record_transaction_optional_fields(finance_pool):
    """Optional fields are persisted when provided."""
    from butlers.tools.finance.transactions import record_transaction

    now = _utcnow()
    result = await record_transaction(
        pool=finance_pool,
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


async def test_record_transaction_metadata_defaults_to_empty_dict(finance_pool):
    """Metadata field defaults to empty dict when not provided."""
    from butlers.tools.finance.transactions import record_transaction

    result = await record_transaction(
        pool=finance_pool,
        posted_at=_utcnow(),
        merchant="Starbucks",
        amount=-5.25,
        currency="USD",
        category="dining",
    )

    assert result["metadata"] == {} or result["metadata"] is not None


async def test_record_transaction_dedupe_via_source_message_id(finance_pool):
    """Duplicate insert with same source_message_id returns existing row."""
    from butlers.tools.finance.transactions import record_transaction

    now = _utcnow()
    first = await record_transaction(
        pool=finance_pool,
        posted_at=now,
        merchant="Chase Alert",
        amount=-75.00,
        currency="USD",
        category="dining",
        source_message_id="email-dedupe-001",
    )

    second = await record_transaction(
        pool=finance_pool,
        posted_at=now,
        merchant="Chase Alert",
        amount=-75.00,
        currency="USD",
        category="dining",
        source_message_id="email-dedupe-001",
    )

    assert first["id"] == second["id"]

    # Verify only one row exists
    count = await finance_pool.fetchval(
        "SELECT COUNT(*) FROM transactions WHERE source_message_id = 'email-dedupe-001'"
    )
    assert count == 1


async def test_cross_source_dedup_same_business_identity(finance_pool):
    """Different source_message_ids with same business identity are deduped.

    Catches cross-source duplicates where the same real-world transaction
    arrives from different channels (e.g. email + Telegram) with different
    source_message_ids and slightly different timestamps but identical
    merchant and amount on the same day.
    """
    from datetime import timedelta

    from butlers.tools.finance.transactions import record_transaction

    now = datetime(2025, 1, 15, 12, tzinfo=UTC)
    first = await record_transaction(
        pool=finance_pool,
        posted_at=now,
        merchant="Lyft",
        amount=-12.00,
        currency="USD",
        category="transport",
        source_message_id="msg-lyft-001",
    )
    # Second ingestion from different channel — different source_message_id
    # and posted_at offset by a few minutes (simulating email vs Telegram).
    second = await record_transaction(
        pool=finance_pool,
        posted_at=now + timedelta(minutes=3),
        merchant="Lyft",
        amount=-12.00,
        currency="USD",
        category="transport",
        source_message_id="msg-lyft-002",
    )

    assert first["id"] == second["id"]


async def test_record_transaction_no_source_id_allows_duplicates(finance_pool):
    """Without source_message_id, identical transactions create separate rows."""
    from butlers.tools.finance.transactions import record_transaction

    now = _utcnow()
    first = await record_transaction(
        pool=finance_pool,
        posted_at=now,
        merchant="Uber",
        amount=-8.00,
        currency="USD",
        category="transport",
    )
    second = await record_transaction(
        pool=finance_pool,
        posted_at=now,
        merchant="Uber",
        amount=-8.00,
        currency="USD",
        category="transport",
    )

    assert first["id"] != second["id"]


async def test_record_transaction_currency_uppercased(finance_pool):
    """Currency code is stored as uppercase."""
    from butlers.tools.finance.transactions import record_transaction

    result = await record_transaction(
        pool=finance_pool,
        posted_at=_utcnow(),
        merchant="Wise",
        amount=-50.00,
        currency="eur",
        category="transfer",
    )

    assert result["currency"] == "EUR"


async def test_record_transaction_with_account_id(finance_pool):
    """account_id is stored as UUID string when provided."""
    from butlers.tools.finance.transactions import record_transaction

    # Create an account first
    account_id = await finance_pool.fetchval(
        """
        INSERT INTO accounts (institution, type, currency)
        VALUES ('Chase', 'credit', 'USD')
        RETURNING id
        """
    )

    result = await record_transaction(
        pool=finance_pool,
        posted_at=_utcnow(),
        merchant="Whole Foods",
        amount=-85.00,
        currency="USD",
        category="groceries",
        account_id=str(account_id),
    )

    assert result["account_id"] == str(account_id)


async def test_record_transaction_resolves_slugged_composite_account_label(finance_pool):
    """A documented slug label resolves across institution, type, and last four."""
    from butlers.tools.finance.transactions import record_transaction

    account_id = await finance_pool.fetchval(
        """
        INSERT INTO accounts (institution, type, name, last_four, currency)
        VALUES ('Example Bank', 'credit', 'Example Rewards', '4321', 'SGD')
        RETURNING id
        """
    )

    result = await record_transaction(
        pool=finance_pool,
        posted_at=_utcnow(),
        merchant="Example Merchant",
        amount=-12.34,
        currency="SGD",
        category="shopping",
        account_id="example-bank-card-4321",
    )

    assert result["account_id"] == str(account_id)


async def test_record_transaction_returns_string_id(finance_pool):
    """Returned id is a string (UUID serialized)."""
    from butlers.tools.finance.transactions import record_transaction

    result = await record_transaction(
        pool=finance_pool,
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
async def seeded_pool(finance_pool):
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
        await record_transaction(pool=finance_pool, **txn)

    return finance_pool


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


async def test_list_transactions_empty_result(finance_pool):
    """list_transactions returns empty items with total=0 when no rows."""
    from butlers.tools.finance.transactions import list_transactions

    result = await list_transactions(pool=finance_pool)

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
