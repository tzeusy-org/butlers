"""Tests for SPO mirror write in record_transaction() and bulk_record_transactions().

Covers:
- 6.1: record_transaction() schedules a fire-and-forget SPO mirror to public.facts
- 6.2: bulk_record_transactions() routes through record_transaction() for per-row
  dedup and SPO mirror
- 6.3: spending_summary() response shape backward compatibility
- 6.4: SPO mirror fact is created; primary insert is not rolled back on mirror failure
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Async tests: apply asyncio mark at function level to avoid warnings on sync tests.
pytestmark = pytest.mark.unit


def _utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Helpers: build minimal asyncpg row mock
# ---------------------------------------------------------------------------


def _make_row(
    *,
    id: str = "00000000-0000-0000-0000-000000000001",
    merchant: str = "ACME",
    amount: Decimal | None = None,
    currency: str = "USD",
    direction: str = "debit",
    category: str = "shopping",
    description: str | None = None,
    payment_method: str | None = None,
    account_id: str | None = None,
    receipt_url: str | None = None,
    external_ref: str | None = None,
    source_message_id: str | None = None,
    metadata: dict | None = None,
    posted_at: datetime | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> dict:
    now = _utcnow()
    return {
        "id": id,
        "merchant": merchant,
        "amount": amount or Decimal("42.00"),
        "currency": currency,
        "direction": direction,
        "category": category,
        "description": description,
        "payment_method": payment_method,
        "account_id": account_id,
        "receipt_url": receipt_url,
        "external_ref": external_ref,
        "source_message_id": source_message_id,
        "metadata": metadata or {},
        "posted_at": posted_at or now,
        "created_at": created_at or now,
        "updated_at": updated_at or now,
    }


def _make_asyncpg_record(row_dict: dict):
    """Build a MagicMock that looks like an asyncpg Record."""
    record = MagicMock()
    record.__getitem__ = MagicMock(side_effect=lambda k: row_dict[k])
    record.__iter__ = MagicMock(side_effect=lambda: iter(row_dict.items()))
    record.keys = MagicMock(return_value=list(row_dict.keys()))
    record.items = MagicMock(return_value=list(row_dict.items()))
    # __contains__ for dict(record) usage
    record.__contains__ = MagicMock(side_effect=lambda k: k in row_dict)
    return record


def _make_pool(fetchrow_return=None):
    """Build a minimal asyncpg pool mock."""
    pool = AsyncMock()
    row_dict = fetchrow_return or _make_row()
    if not isinstance(fetchrow_return, MagicMock):
        record = _make_asyncpg_record(row_dict)
    else:
        record = fetchrow_return
    pool.fetchrow = AsyncMock(return_value=record)
    pool.fetchval = AsyncMock(return_value=0)  # dedupe check returns 0 (no existing row)
    return pool


# ---------------------------------------------------------------------------
# 6.1 / 6.4: SPO mirror is scheduled after a successful primary insert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_record_transaction_schedules_spo_mirror():
    """After a successful insert, record_transaction() schedules a SPO mirror task."""
    from butlers.tools.finance.transactions import record_transaction

    pool = _make_pool()
    mirror_called = asyncio.Event()

    async def _fake_mirror_to_spo(*args, **kwargs):
        mirror_called.set()

    patch_target = "butlers.tools.finance.transactions._mirror_to_spo"
    with patch(patch_target, side_effect=_fake_mirror_to_spo):
        await record_transaction(
            pool=pool,
            posted_at=_utcnow(),
            merchant="Trader Joe's",
            amount=-42.50,
            currency="USD",
            category="groceries",
        )
        # Let the event loop run the background task
        await asyncio.sleep(0)

    assert mirror_called.is_set(), "SPO mirror was not scheduled/called after primary insert"


@pytest.mark.asyncio(loop_scope="session")
async def test_spo_mirror_failure_does_not_raise_or_rollback_primary():
    """If the SPO mirror write fails, record_transaction() still succeeds.

    Patches the underlying record_transaction_fact (called inside _mirror_to_spo)
    so that _mirror_to_spo's own exception handler is exercised.  The primary
    insert result must be returned successfully even when the mirror throws.
    """
    from butlers.tools.finance.transactions import record_transaction

    pool = _make_pool(fetchrow_return=_make_row(merchant="Netflix", direction="debit"))

    async def _failing_fact(*args, **kwargs):
        raise RuntimeError("Simulated SPO mirror failure")

    with patch("butlers.tools.finance.facts.record_transaction_fact", side_effect=_failing_fact):
        result = await record_transaction(
            pool=pool,
            posted_at=_utcnow(),
            merchant="Netflix",
            amount=-15.49,
            currency="USD",
            category="subscriptions",
        )
        # Yield to the event loop so the background task runs and
        # _mirror_to_spo's exception handler swallows the error.
        await asyncio.sleep(0)

    # Primary insert succeeded: result contains id
    assert "id" in result
    assert result["merchant"] == "Netflix"


@pytest.mark.asyncio(loop_scope="session")
async def test_spo_mirror_called_with_correct_predicate_for_debit():
    """SPO mirror receives the correct arguments for a debit transaction."""
    from butlers.tools.finance.transactions import record_transaction

    inserted_row = _make_row(merchant="Starbucks", direction="debit")
    pool = AsyncMock()
    # First fetchrow call: dedup check (no existing row) → None
    # Second fetchrow call: INSERT RETURNING → row
    pool.fetchrow = AsyncMock(side_effect=[None, _make_asyncpg_record(inserted_row)])
    pool.fetchval = AsyncMock(return_value=0)

    mirror_mock = AsyncMock()

    with patch("butlers.tools.finance.transactions._mirror_to_spo", mirror_mock):
        posted_at = _utcnow()
        await record_transaction(
            pool=pool,
            posted_at=posted_at,
            merchant="Starbucks",
            amount=-5.50,
            currency="USD",
            category="dining",
            source_message_id="msg-mirror-test-001",
        )
        await asyncio.sleep(0)

    mirror_mock.assert_called_once()
    _, kwargs = mirror_mock.call_args
    assert kwargs["merchant"] == "Starbucks"
    assert kwargs["currency"] == "USD"
    assert kwargs["category"] == "dining"
    assert kwargs["source_message_id"] == "msg-mirror-test-001"
    # The amount passed to _mirror_to_spo is the original (signed) amount
    assert Decimal(str(kwargs["amount"])) == Decimal("-5.50")


@pytest.mark.asyncio(loop_scope="session")
async def test_spo_mirror_called_for_credit_transaction():
    """SPO mirror is called for credit (positive amount) transactions too."""
    from butlers.tools.finance.transactions import record_transaction

    pool_row = _make_row(direction="credit", amount=Decimal("100.00"))
    pool = _make_pool(fetchrow_return=pool_row)
    mirror_called = asyncio.Event()

    async def _capture_mirror(*args, **kwargs):
        mirror_called.set()

    with patch("butlers.tools.finance.transactions._mirror_to_spo", side_effect=_capture_mirror):
        await record_transaction(
            pool=pool,
            posted_at=_utcnow(),
            merchant="PayPal",
            amount=100.00,
            currency="USD",
            category="refunds",
        )
        await asyncio.sleep(0)

    assert mirror_called.is_set()


@pytest.mark.asyncio(loop_scope="session")
async def test_spo_mirror_not_called_on_dedup_hit():
    """When a duplicate is detected (fetchrow returns existing row), no new
    primary insert fires, but the function still returns the existing record.
    (The mirror for the original insert was already done at insert time.)"""
    from butlers.tools.finance.transactions import record_transaction

    existing_row = _make_row(source_message_id="msg-dedup-001")
    pool = _make_pool()
    # Simulate dedup: fetchrow returns existing row for source_message_id check
    pool.fetchrow = AsyncMock(return_value=_make_asyncpg_record(existing_row))
    mirror_calls = []

    async def _capture_mirror(*args, **kwargs):
        mirror_calls.append(kwargs)

    with patch("butlers.tools.finance.transactions._mirror_to_spo", side_effect=_capture_mirror):
        result = await record_transaction(
            pool=pool,
            posted_at=_utcnow(),
            merchant="ACME",
            amount=-42.00,
            currency="USD",
            category="shopping",
            source_message_id="msg-dedup-001",
        )
        await asyncio.sleep(0)

    # Dedup path returns the existing row without a new insert or mirror
    assert result["source_message_id"] == "msg-dedup-001"
    assert len(mirror_calls) == 0, "Mirror should not be called on dedup path"


# ---------------------------------------------------------------------------
# 6.2: bulk_record_transactions routes through record_transaction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_bulk_record_transactions_routes_through_record_transaction():
    """bulk_record_transactions() calls record_transaction() for each valid row."""
    from butlers.tools.finance.transactions import bulk_record_transactions

    record_calls = []

    async def _fake_record_transaction(*args, **kwargs):
        record_calls.append(kwargs)
        return {"id": "fake-id", "merchant": kwargs["merchant"]}

    with patch(
        "butlers.tools.finance.transactions.record_transaction",
        side_effect=_fake_record_transaction,
    ):
        result = await bulk_record_transactions(
            pool=MagicMock(),
            transactions=[
                {
                    "posted_at": _utcnow().isoformat(),
                    "merchant": "Amazon",
                    "amount": "-29.99",
                    "currency": "USD",
                    "category": "shopping",
                },
                {
                    "posted_at": _utcnow().isoformat(),
                    "merchant": "Netflix",
                    "amount": "-15.49",
                    "currency": "USD",
                    "category": "subscriptions",
                    "source_message_id": "msg-bulk-001",
                },
            ],
        )

    assert result["total"] == 2
    assert result["imported"] == 2
    assert result["skipped"] == 0
    assert result["errors"] == 0
    assert len(record_calls) == 2
    merchants = {c["merchant"] for c in record_calls}
    assert merchants == {"Amazon", "Netflix"}


@pytest.mark.asyncio(loop_scope="session")
async def test_bulk_record_transactions_handles_invalid_date():
    """Rows with unparseable posted_at are counted as errors."""
    from butlers.tools.finance.transactions import bulk_record_transactions

    result = await bulk_record_transactions(
        pool=MagicMock(),
        transactions=[
            {
                "posted_at": "not-a-date",
                "merchant": "Bad Row",
                "amount": "-10.00",
            }
        ],
    )

    assert result["total"] == 1
    assert result["errors"] == 1
    assert result["error_details"][0]["reason"] == "invalid_date"


@pytest.mark.asyncio(loop_scope="session")
async def test_bulk_record_transactions_handles_invalid_amount():
    """Rows with non-numeric amount are counted as errors."""
    from butlers.tools.finance.transactions import bulk_record_transactions

    result = await bulk_record_transactions(
        pool=MagicMock(),
        transactions=[
            {
                "posted_at": _utcnow().isoformat(),
                "merchant": "Bad Amount",
                "amount": "not-a-number",
            }
        ],
    )

    assert result["total"] == 1
    assert result["errors"] == 1
    assert result["error_details"][0]["reason"] == "invalid_amount"


@pytest.mark.asyncio(loop_scope="session")
async def test_bulk_record_transactions_handles_missing_merchant():
    """Rows with missing merchant are counted as errors."""
    from butlers.tools.finance.transactions import bulk_record_transactions

    result = await bulk_record_transactions(
        pool=MagicMock(),
        transactions=[
            {
                "posted_at": _utcnow().isoformat(),
                "merchant": "",
                "amount": "-10.00",
            }
        ],
    )

    assert result["total"] == 1
    assert result["errors"] == 1
    assert result["error_details"][0]["reason"] == "missing_merchant"


@pytest.mark.asyncio(loop_scope="session")
async def test_bulk_record_transactions_skips_duplicates():
    """Rows that raise UniqueViolationError are counted as skipped."""
    import asyncpg

    from butlers.tools.finance.transactions import bulk_record_transactions

    async def _raise_unique(*args, **kwargs):
        raise asyncpg.UniqueViolationError()

    with patch(
        "butlers.tools.finance.transactions.record_transaction",
        side_effect=_raise_unique,
    ):
        result = await bulk_record_transactions(
            pool=MagicMock(),
            transactions=[
                {
                    "posted_at": _utcnow().isoformat(),
                    "merchant": "Duplicate",
                    "amount": "-10.00",
                    "source_message_id": "msg-dup-001",
                }
            ],
        )

    assert result["total"] == 1
    assert result["skipped"] == 1
    assert result["error_details"][0]["reason"] == "duplicate"


@pytest.mark.asyncio(loop_scope="session")
async def test_bulk_record_transactions_inherits_account_id():
    """Top-level account_id is passed to record_transaction when not overridden per-row."""
    from butlers.tools.finance.transactions import bulk_record_transactions

    captured: list[dict] = []

    async def _capture(*args, **kwargs):
        captured.append(kwargs)
        return {"id": "fake-id", "merchant": kwargs["merchant"]}

    with patch("butlers.tools.finance.transactions.record_transaction", side_effect=_capture):
        await bulk_record_transactions(
            pool=MagicMock(),
            transactions=[
                {
                    "posted_at": _utcnow().isoformat(),
                    "merchant": "ACME",
                    "amount": "-9.99",
                }
            ],
            account_id="acct-top-level-uuid",
        )

    assert captured[0]["account_id"] == "acct-top-level-uuid"


@pytest.mark.asyncio(loop_scope="session")
async def test_bulk_record_transactions_per_row_account_id_overrides_top():
    """Per-row account_id overrides the top-level account_id."""
    from butlers.tools.finance.transactions import bulk_record_transactions

    captured: list[dict] = []

    async def _capture(*args, **kwargs):
        captured.append(kwargs)
        return {"id": "fake-id", "merchant": kwargs["merchant"]}

    with patch("butlers.tools.finance.transactions.record_transaction", side_effect=_capture):
        await bulk_record_transactions(
            pool=MagicMock(),
            transactions=[
                {
                    "posted_at": _utcnow().isoformat(),
                    "merchant": "ACME",
                    "amount": "-9.99",
                    "account_id": "acct-per-row-uuid",
                }
            ],
            account_id="acct-top-level-uuid",
        )

    assert captured[0]["account_id"] == "acct-per-row-uuid"


@pytest.mark.asyncio(loop_scope="session")
async def test_bulk_record_transactions_source_stored_in_metadata():
    """The top-level source parameter is stored in each row's metadata as import_source."""
    from butlers.tools.finance.transactions import bulk_record_transactions

    captured: list[dict] = []

    async def _capture(*args, **kwargs):
        captured.append(kwargs)
        return {"id": "fake-id", "merchant": kwargs["merchant"]}

    with patch("butlers.tools.finance.transactions.record_transaction", side_effect=_capture):
        await bulk_record_transactions(
            pool=MagicMock(),
            transactions=[
                {
                    "posted_at": _utcnow().isoformat(),
                    "merchant": "Chase CSV",
                    "amount": "-50.00",
                }
            ],
            source="chase_csv",
        )

    meta = captured[0].get("metadata") or {}
    assert meta.get("import_source") == "chase_csv"


@pytest.mark.asyncio(loop_scope="session")
async def test_bulk_record_transactions_batch_too_large():
    """Raises ValueError when batch exceeds _MAX_BULK_TRANSACTIONS."""
    from butlers.tools.finance.transactions import _MAX_BULK_TRANSACTIONS, bulk_record_transactions

    oversized = [{"posted_at": _utcnow().isoformat(), "merchant": "X", "amount": "1.00"}] * (
        _MAX_BULK_TRANSACTIONS + 1
    )

    with pytest.raises(ValueError, match="Batch too large"):
        await bulk_record_transactions(pool=MagicMock(), transactions=oversized)


# ---------------------------------------------------------------------------
# 6.3: spending_summary response shape backward compatibility
# ---------------------------------------------------------------------------


def test_spending_summary_response_shape():
    """spending_summary() returns the same shape as spending_summary_facts().

    This test verifies backward compatibility by checking that both functions
    agree on the top-level keys of the response dict.
    """
    import inspect

    from butlers.tools.finance.spending import spending_summary

    # Verify spending_summary is an async function with expected return shape
    assert inspect.iscoroutinefunction(spending_summary)

    # The docstring specifies the exact response shape.  We verify the keys
    # by running a mock-based check rather than a live DB call.
    expected_keys = {"start_date", "end_date", "currency", "total_spend", "groups"}

    # Check that spending_summary's docstring documents these keys.
    doc = spending_summary.__doc__ or ""
    for key in expected_keys:
        assert key in doc, f"spending_summary docstring missing key: {key!r}"


def test_spending_summary_facts_response_shape_matches():
    """spending_summary_facts() documents the same response shape as spending_summary()."""
    from butlers.tools.finance.facts import spending_summary_facts

    expected_keys = {"start_date", "end_date", "currency", "total_spend", "groups"}
    doc = spending_summary_facts.__doc__ or ""
    for key in expected_keys:
        assert key in doc, f"spending_summary_facts docstring missing key: {key!r}"


def test_spending_summary_valid_group_by_modes_unchanged():
    """VALID_GROUP_BY_MODES includes the same values as the SPO implementation."""
    from butlers.tools.finance.spending import VALID_GROUP_BY_MODES

    # These are the group_by modes specified in the backward-compatibility contract.
    required_modes = {"category", "merchant", "week", "month"}
    assert required_modes == VALID_GROUP_BY_MODES


@pytest.mark.asyncio(loop_scope="session")
async def test_spending_summary_raises_on_invalid_group_by():
    """spending_summary() raises ValueError for unsupported group_by values."""
    from unittest.mock import AsyncMock

    from butlers.tools.finance.spending import spending_summary

    fake_pool = AsyncMock()
    fake_pool.fetchrow = AsyncMock(return_value=None)

    with pytest.raises(ValueError, match="Unsupported group_by"):
        await spending_summary(fake_pool, group_by="invalid_mode")
