"""Tests for butlers.tools.finance.spending — spending_summary aggregation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

CREATE_TRANSACTIONS_SQL = """
CREATE TABLE IF NOT EXISTS transactions (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id        UUID,
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
"""


@pytest.fixture
async def pool(provisioned_postgres_pool):
    """Provision a fresh database with the finance transactions table."""
    async with provisioned_postgres_pool() as p:
        await p.execute(CREATE_TRANSACTIONS_SQL)
        yield p


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _insert_tx(
    pool,
    *,
    merchant: str = "ACME Corp",
    amount: str = "50.00",
    currency: str = "USD",
    direction: str = "debit",
    category: str = "general",
    posted_at: datetime | None = None,
    account_id: str | None = None,
    source_message_id: str | None = None,
) -> None:
    if posted_at is None:
        posted_at = datetime.now(UTC)
    await pool.execute(
        """
        INSERT INTO transactions
            (merchant, amount, currency, direction, category, posted_at, account_id,
             source_message_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7::uuid, $8)
        """,
        merchant,
        Decimal(amount),
        currency,
        direction,
        category,
        posted_at,
        account_id,
        source_message_id,
    )


def _this_month_mid() -> datetime:
    """Return a datetime that is definitely within the current calendar month."""
    today = datetime.now(UTC)
    return today.replace(day=max(1, today.day - 1), hour=12, minute=0, second=0, microsecond=0)


# ---------------------------------------------------------------------------
# Basic / default date range
# ---------------------------------------------------------------------------


async def test_spending_summary_empty(pool):
    """Returns zero total and empty groups when no transactions exist."""
    from butlers.tools.finance.spending import spending_summary

    result = await spending_summary(pool)

    assert result["total_spend"] == "0"
    assert result["start_date"] <= result["end_date"]
    # groups may have a single 'total' bucket with count 0 or be empty
    for g in result["groups"]:
        assert int(g["count"]) == 0


async def test_spending_summary_excludes_credit_direction(pool):
    """Credit-direction entries must NOT count toward the spend total."""
    from butlers.tools.finance.spending import spending_summary

    posted = _this_month_mid()
    await _insert_tx(
        pool, merchant="Refund Co", amount="100.00", direction="credit", posted_at=posted
    )
    await _insert_tx(
        pool, merchant="Coffee Shop", amount="5.00", direction="debit", posted_at=posted
    )

    result = await spending_summary(pool)

    assert Decimal(result["total_spend"]) == Decimal("5.00")


async def test_spending_summary_explicit_date_range(pool):
    """Transactions outside the specified date range are excluded."""
    from butlers.tools.finance.spending import spending_summary

    today = datetime.now(UTC)
    in_range = today - timedelta(days=5)
    out_of_range = today - timedelta(days=40)

    await _insert_tx(pool, amount="100.00", direction="debit", posted_at=in_range)
    await _insert_tx(pool, amount="999.00", direction="debit", posted_at=out_of_range)

    start = (today - timedelta(days=10)).date()
    end = today.date()
    result = await spending_summary(pool, start_date=start, end_date=end)

    assert Decimal(result["total_spend"]) == Decimal("100.00")


# ---------------------------------------------------------------------------
# group_by=category
# ---------------------------------------------------------------------------


async def test_spending_summary_group_by_category(pool):
    """group_by=category returns one bucket per category, sorted by amount desc."""
    from butlers.tools.finance.spending import spending_summary

    posted = _this_month_mid()
    await _insert_tx(
        pool, merchant="Whole Foods", amount="80.00", category="groceries", posted_at=posted
    )
    await _insert_tx(
        pool, merchant="Blue Bottle", amount="20.00", category="dining", posted_at=posted
    )
    await _insert_tx(
        pool, merchant="Safeway", amount="50.00", category="groceries", posted_at=posted
    )

    result = await spending_summary(pool, group_by="category")

    assert Decimal(result["total_spend"]) == Decimal("150.00")
    keys = [g["key"] for g in result["groups"]]
    assert "groceries" in keys
    assert "dining" in keys

    grocery_group = next(g for g in result["groups"] if g["key"] == "groceries")
    assert Decimal(grocery_group["amount"]) == Decimal("130.00")
    assert grocery_group["count"] == 2

    dining_group = next(g for g in result["groups"] if g["key"] == "dining")
    assert Decimal(dining_group["amount"]) == Decimal("20.00")
    assert dining_group["count"] == 1

    # Should be sorted by amount DESC — groceries first
    assert result["groups"][0]["key"] == "groceries"


# ---------------------------------------------------------------------------
# group_by=merchant
# ---------------------------------------------------------------------------


async def test_spending_summary_group_by_merchant(pool):
    """group_by=merchant aggregates by merchant name."""
    from butlers.tools.finance.spending import spending_summary

    posted = _this_month_mid()
    await _insert_tx(pool, merchant="Netflix", amount="15.00", posted_at=posted)
    await _insert_tx(pool, merchant="Netflix", amount="15.00", posted_at=posted)
    await _insert_tx(pool, merchant="Spotify", amount="10.00", posted_at=posted)

    result = await spending_summary(pool, group_by="merchant")

    netflix = next(g for g in result["groups"] if g["key"] == "Netflix")
    assert Decimal(netflix["amount"]) == Decimal("30.00")
    assert netflix["count"] == 2

    spotify = next(g for g in result["groups"] if g["key"] == "Spotify")
    assert Decimal(spotify["amount"]) == Decimal("10.00")


# ---------------------------------------------------------------------------
# group_by=week
# ---------------------------------------------------------------------------


async def test_spending_summary_group_by_week(pool):
    """group_by=week returns ISO week buckets (YYYY-Www format)."""
    from butlers.tools.finance.spending import spending_summary

    today = datetime.now(UTC)
    # Two transactions one week apart
    week1 = today - timedelta(days=14)
    week2 = today - timedelta(days=7)

    start = (today - timedelta(days=21)).date()
    end = today.date()

    await _insert_tx(pool, amount="30.00", posted_at=week1)
    await _insert_tx(pool, amount="50.00", posted_at=week2)

    result = await spending_summary(pool, start_date=start, end_date=end, group_by="week")

    assert len(result["groups"]) >= 2
    # Keys should look like "2026-W07"
    for g in result["groups"]:
        assert "W" in g["key"], f"Expected ISO week key, got: {g['key']}"


# ---------------------------------------------------------------------------
# group_by=month
# ---------------------------------------------------------------------------


async def test_spending_summary_group_by_month(pool):
    """group_by=month returns YYYY-MM buckets."""
    from butlers.tools.finance.spending import spending_summary

    today = datetime.now(UTC)
    # Transactions in two separate months
    this_month = today.replace(day=1, hour=12)
    last_month_dt = (today.replace(day=1) - timedelta(days=1)).replace(hour=12)

    start = last_month_dt.date().replace(day=1)
    end = today.date()

    await _insert_tx(pool, amount="100.00", posted_at=this_month)
    await _insert_tx(pool, amount="200.00", posted_at=last_month_dt)

    result = await spending_summary(pool, start_date=start, end_date=end, group_by="month")

    assert len(result["groups"]) >= 2
    for g in result["groups"]:
        # Keys should be YYYY-MM format
        parts = g["key"].split("-")
        assert len(parts) == 2 and len(parts[0]) == 4 and len(parts[1]) == 2


# ---------------------------------------------------------------------------
# category_filter
# ---------------------------------------------------------------------------


async def test_spending_summary_category_filter(pool):
    """category_filter restricts results to the named category."""
    from butlers.tools.finance.spending import spending_summary

    posted = _this_month_mid()
    await _insert_tx(pool, amount="100.00", category="groceries", posted_at=posted)
    await _insert_tx(pool, amount="50.00", category="dining", posted_at=posted)

    result = await spending_summary(pool, category_filter="groceries")

    assert Decimal(result["total_spend"]) == Decimal("100.00")


# ---------------------------------------------------------------------------
# account_id filter
# ---------------------------------------------------------------------------


async def test_spending_summary_account_id_filter(pool):
    """account_id filter restricts results to a specific account."""
    import uuid

    from butlers.tools.finance.spending import spending_summary

    acct = str(uuid.uuid4())
    other_acct = str(uuid.uuid4())
    posted = _this_month_mid()

    await _insert_tx(pool, amount="75.00", account_id=acct, posted_at=posted)
    await _insert_tx(pool, amount="25.00", account_id=other_acct, posted_at=posted)
    await _insert_tx(pool, amount="10.00", account_id=None, posted_at=posted)

    result = await spending_summary(pool, account_id=acct)

    assert Decimal(result["total_spend"]) == Decimal("75.00")


# ---------------------------------------------------------------------------
# Invalid group_by
# ---------------------------------------------------------------------------


async def test_spending_summary_invalid_group_by(pool):
    """Invalid group_by value raises ValueError."""
    from butlers.tools.finance.spending import spending_summary

    with pytest.raises(ValueError, match="Unsupported group_by"):
        await spending_summary(pool, group_by="invalid_mode")


# ---------------------------------------------------------------------------
# String date input
# ---------------------------------------------------------------------------


async def test_spending_summary_accepts_string_dates(pool):
    """start_date and end_date can be ISO-8601 strings."""
    from butlers.tools.finance.spending import spending_summary

    posted = _this_month_mid()
    await _insert_tx(pool, amount="42.00", posted_at=posted)

    today = datetime.now(UTC).date()
    result = await spending_summary(
        pool,
        start_date=today.replace(day=1).isoformat(),
        end_date=today.isoformat(),
    )

    # Should not raise; total should reflect the inserted transaction
    assert Decimal(result["total_spend"]) >= Decimal("42.00")


# ---------------------------------------------------------------------------
# Return type shape
# ---------------------------------------------------------------------------


async def test_spending_summary_return_shape(pool):
    """spending_summary always returns a dict with the expected top-level keys."""
    from butlers.tools.finance.spending import spending_summary

    result = await spending_summary(pool, group_by="category")

    assert set(result.keys()) == {"start_date", "end_date", "currency", "total_spend", "groups"}
    assert isinstance(result["groups"], list)
    for g in result["groups"]:
        assert "key" in g
        assert "amount" in g
        assert "count" in g


# ---------------------------------------------------------------------------
# __init__.py re-export
# ---------------------------------------------------------------------------


def test_spending_summary_importable_from_package():
    """spending_summary is importable from the finance tools package."""
    from butlers.tools.finance import spending_summary  # noqa: F401

    assert callable(spending_summary)


def test_valid_group_by_modes_importable():
    """VALID_GROUP_BY_MODES is re-exported from the finance tools package."""
    from butlers.tools.finance import VALID_GROUP_BY_MODES

    assert "category" in VALID_GROUP_BY_MODES
    assert "merchant" in VALID_GROUP_BY_MODES
    assert "week" in VALID_GROUP_BY_MODES
    assert "month" in VALID_GROUP_BY_MODES
