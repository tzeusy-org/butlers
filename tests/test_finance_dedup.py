"""Tests for the finance butler _deduplicate() function.

Covers:
- Priority 1 match: external_id + account_id
- Priority 2 match: source_message_id
- Priority 3 fallback: composite (account_id + posted_at + amount + merchant)
- No-match: returns None for genuinely new transactions
- NULL / missing fields at each priority level — graceful skip, not error
- Partial key availability — correct tier selection
- Amount normalisation (sign-insensitive comparison)

All tests use unittest.mock.AsyncMock — no live database required.

Issue: bu-i112
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

import butlers.tools.finance.transactions as _txn_module
from butlers.tools.finance.transactions import _deduplicate

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def clear_column_cache():
    """Clear the _has_column module-level cache before each test.

    Prevents cross-test contamination: without this, a test that caches
    column existence as False would cause later tests to skip P1 silently.
    """
    _txn_module._column_existence_cache.clear()
    yield
    _txn_module._column_existence_cache.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ACCOUNT_ID = str(uuid4())
_TXN_ID = str(uuid4())
_NOW = datetime(2024, 3, 15, 10, 30, 0, tzinfo=UTC)


def _mock_pool(*, fetchrow_return=None, fetchval_return=None) -> AsyncMock:
    """Build a minimal asyncpg Pool mock.

    fetchval is used by _has_column; fetchrow is used for SELECT queries.
    """
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=fetchval_return)
    pool.fetchrow = AsyncMock(return_value=fetchrow_return)
    return pool


def _make_row(txn_id: str) -> MagicMock:
    """Build a mock asyncpg Record with a single 'id' key."""
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda k: txn_id if k == "id" else None)
    return row


# ---------------------------------------------------------------------------
# Import smoke-test
# ---------------------------------------------------------------------------


def test_deduplicate_importable():
    """_deduplicate must be importable from the finance transactions module."""
    assert callable(_deduplicate)


# ---------------------------------------------------------------------------
# Priority 1: external_id + account_id
# ---------------------------------------------------------------------------


class TestPriority1ExternalId:
    """Priority 1 dedup: (account_id, external_id)."""

    async def test_returns_existing_id_when_match(self):
        """Returns existing transaction ID when external_id + account_id match."""
        pool = _mock_pool(
            fetchval_return=1,  # _has_column → column exists
            fetchrow_return=_make_row(_TXN_ID),
        )

        txn = {
            "external_id": "ext-abc-123",
            "account_id": _ACCOUNT_ID,
            "source_message_id": None,
        }
        result = await _deduplicate(pool, txn)

        assert result == _TXN_ID

    async def test_no_match_returns_none(self):
        """Returns None when external_id + account_id combination is not found."""
        pool = _mock_pool(
            fetchval_return=1,  # column exists
            fetchrow_return=None,  # no match
        )

        txn = {
            "external_id": "ext-new-999",
            "account_id": _ACCOUNT_ID,
        }
        result = await _deduplicate(pool, txn)

        assert result is None

    async def test_skips_p1_when_external_id_column_absent(self):
        """Falls through to lower tiers when external_id column doesn't exist in schema."""
        # _has_column returns 0 (column absent); source_message_id also absent
        # → composite fallback also absent → None
        pool = _mock_pool(
            fetchval_return=0,  # external_id column absent
            fetchrow_return=None,
        )

        txn = {
            "external_id": "ext-abc-123",
            "account_id": _ACCOUNT_ID,
            "source_message_id": None,
            # No composite fallback keys either
        }
        result = await _deduplicate(pool, txn)

        # fetchrow should NOT have been called because the column is absent
        # and no fallback keys are present
        pool.fetchrow.assert_not_called()
        assert result is None

    async def test_skips_p1_when_external_id_is_none(self):
        """Priority 1 is not attempted when external_id is None."""
        pool = _mock_pool(fetchval_return=1, fetchrow_return=None)

        txn = {
            "external_id": None,
            "account_id": _ACCOUNT_ID,
            "source_message_id": None,
        }
        result = await _deduplicate(pool, txn)

        # _has_column should not be called — we never reach P1 logic
        pool.fetchval.assert_not_called()
        assert result is None

    async def test_skips_p1_when_account_id_is_none(self):
        """Priority 1 is not attempted when account_id is None."""
        pool = _mock_pool(fetchval_return=1, fetchrow_return=None)

        txn = {
            "external_id": "ext-abc-123",
            "account_id": None,
            "source_message_id": None,
        }
        result = await _deduplicate(pool, txn)

        pool.fetchval.assert_not_called()
        assert result is None

    async def test_p1_does_not_fall_through_when_matched(self):
        """Once P1 returns a match, P2 and P3 are NOT queried."""
        pool = _mock_pool(
            fetchval_return=1,
            fetchrow_return=_make_row(_TXN_ID),
        )

        txn = {
            "external_id": "ext-abc-123",
            "account_id": _ACCOUNT_ID,
            "source_message_id": "msg-111",  # would trigger P2 if reached
            "posted_at": _NOW,
            "amount": Decimal("42.00"),
            "merchant": "Acme",
        }
        result = await _deduplicate(pool, txn)

        assert result == _TXN_ID
        # fetchrow called exactly once — for P1 SELECT
        assert pool.fetchrow.call_count == 1


# ---------------------------------------------------------------------------
# Priority 2: source_message_id
# ---------------------------------------------------------------------------


class TestPriority2SourceMessageId:
    """Priority 2 dedup: source_message_id."""

    async def test_returns_existing_id_when_match(self):
        """Returns existing transaction ID when source_message_id matches."""
        pool = _mock_pool(fetchrow_return=_make_row(_TXN_ID))

        txn = {
            "external_id": None,
            "account_id": None,
            "source_message_id": "email-abc@example.com",
        }
        result = await _deduplicate(pool, txn)

        assert result == _TXN_ID

    async def test_no_match_returns_none(self):
        """Returns None when source_message_id is not found."""
        pool = _mock_pool(fetchrow_return=None)

        txn = {
            "external_id": None,
            "account_id": None,
            "source_message_id": "email-new@example.com",
        }
        result = await _deduplicate(pool, txn)

        assert result is None

    async def test_skips_p2_when_source_message_id_is_none(self):
        """Priority 2 is skipped when source_message_id is None."""
        pool = _mock_pool(fetchrow_return=None)

        txn = {
            "external_id": None,
            "account_id": None,
            "source_message_id": None,
        }
        result = await _deduplicate(pool, txn)

        pool.fetchrow.assert_not_called()
        assert result is None

    async def test_p2_does_not_fall_through_when_matched(self):
        """Once P2 returns a match, P3 is NOT queried."""
        pool = _mock_pool(fetchrow_return=_make_row(_TXN_ID))

        txn = {
            "external_id": None,
            "account_id": _ACCOUNT_ID,
            "source_message_id": "email-abc@example.com",
            "posted_at": _NOW,
            "amount": Decimal("99.00"),
            "merchant": "Netflix",
        }
        result = await _deduplicate(pool, txn)

        assert result == _TXN_ID
        # fetchrow called exactly once — for P2 SELECT
        assert pool.fetchrow.call_count == 1

    async def test_p2_no_match_does_not_fall_through_to_p3(self):
        """When P2 finds no match, P3 is NOT attempted if source_message_id was present."""
        p3_txn_id = str(uuid4())

        # P2 returns None on first call, P3 returns a match on second call
        pool = AsyncMock()
        pool.fetchval = AsyncMock(return_value=0)  # not used but safe default
        pool.fetchrow = AsyncMock(side_effect=[None, _make_row(p3_txn_id)])

        txn = {
            "external_id": None,
            "account_id": _ACCOUNT_ID,
            "source_message_id": "email-no-match@example.com",
            "posted_at": _NOW,
            "amount": Decimal("15.00"),
            "merchant": "Spotify",
        }
        result = await _deduplicate(pool, txn)

        # P3 is NOT attempted when source_message_id is present (even with no match)
        # — P3 fallback requires BOTH external_id AND source_message_id to be absent.
        assert result is None
        assert pool.fetchrow.call_count == 1


# ---------------------------------------------------------------------------
# Priority 3: composite fallback
# ---------------------------------------------------------------------------


class TestPriority3CompositeFallback:
    """Priority 3 dedup: account_id + posted_at + amount + merchant."""

    async def test_returns_existing_id_when_match(self):
        """Returns existing transaction ID on composite key match."""
        pool = _mock_pool(fetchrow_return=_make_row(_TXN_ID))

        txn = {
            "external_id": None,
            "account_id": _ACCOUNT_ID,
            "source_message_id": None,
            "posted_at": _NOW,
            "amount": Decimal("55.75"),
            "merchant": "Whole Foods",
        }
        result = await _deduplicate(pool, txn)

        assert result == _TXN_ID

    async def test_no_match_returns_none(self):
        """Returns None when no composite match found."""
        pool = _mock_pool(fetchrow_return=None)

        txn = {
            "external_id": None,
            "account_id": _ACCOUNT_ID,
            "source_message_id": None,
            "posted_at": _NOW,
            "amount": Decimal("99.99"),
            "merchant": "NewMerchant",
        }
        result = await _deduplicate(pool, txn)

        assert result is None

    async def test_skips_p3_when_account_id_missing(self):
        """Priority 3 is skipped when account_id is None."""
        pool = _mock_pool(fetchrow_return=None)

        txn = {
            "external_id": None,
            "source_message_id": None,
            "account_id": None,
            "posted_at": _NOW,
            "amount": Decimal("10.00"),
            "merchant": "Coffee Shop",
        }
        result = await _deduplicate(pool, txn)

        pool.fetchrow.assert_not_called()
        assert result is None

    async def test_skips_p3_when_posted_at_missing(self):
        """Priority 3 is skipped when posted_at is None."""
        pool = _mock_pool(fetchrow_return=None)

        txn = {
            "external_id": None,
            "source_message_id": None,
            "account_id": _ACCOUNT_ID,
            "posted_at": None,
            "amount": Decimal("10.00"),
            "merchant": "Coffee Shop",
        }
        result = await _deduplicate(pool, txn)

        pool.fetchrow.assert_not_called()
        assert result is None

    async def test_skips_p3_when_merchant_missing(self):
        """Priority 3 is skipped when merchant is None."""
        pool = _mock_pool(fetchrow_return=None)

        txn = {
            "external_id": None,
            "source_message_id": None,
            "account_id": _ACCOUNT_ID,
            "posted_at": _NOW,
            "amount": Decimal("10.00"),
            "merchant": None,
        }
        result = await _deduplicate(pool, txn)

        pool.fetchrow.assert_not_called()
        assert result is None

    async def test_skips_p3_when_amount_missing(self):
        """Priority 3 is skipped when amount is None."""
        pool = _mock_pool(fetchrow_return=None)

        txn = {
            "external_id": None,
            "source_message_id": None,
            "account_id": _ACCOUNT_ID,
            "posted_at": _NOW,
            "amount": None,
            "merchant": "Coffee Shop",
        }
        result = await _deduplicate(pool, txn)

        pool.fetchrow.assert_not_called()
        assert result is None

    async def test_skips_p3_when_source_message_id_present(self):
        """Priority 3 is not attempted when source_message_id is non-None (even if P2 missed)."""
        # P2 returns None (no source_message_id match)
        pool = _mock_pool(fetchrow_return=None)

        txn = {
            "external_id": None,
            "account_id": _ACCOUNT_ID,
            "source_message_id": "some-msg-id",  # P2 attempted, not P3
            "posted_at": _NOW,
            "amount": Decimal("10.00"),
            "merchant": "Coffee Shop",
        }
        result = await _deduplicate(pool, txn)

        # Only one fetchrow call (P2); P3 must not add a second call
        assert pool.fetchrow.call_count == 1
        assert result is None

    async def test_normalizes_negative_amount(self):
        """Negative amounts are stored as absolute values; dedup must compare correctly."""
        pool = _mock_pool(fetchrow_return=_make_row(_TXN_ID))

        txn = {
            "external_id": None,
            "source_message_id": None,
            "account_id": _ACCOUNT_ID,
            "posted_at": _NOW,
            "amount": Decimal("-42.50"),  # debit: negative sign
            "merchant": "Merchant",
        }
        result = await _deduplicate(pool, txn)

        assert result == _TXN_ID
        # Verify the amount passed to DB is the absolute value
        call_args = pool.fetchrow.call_args
        assert Decimal("42.50") in call_args.args or Decimal("42.50") in call_args[0]


# ---------------------------------------------------------------------------
# No-match (new transaction)
# ---------------------------------------------------------------------------


class TestNoMatch:
    """All tiers return no match — transaction is new."""

    async def test_all_none_fields_returns_none(self):
        """Returns None when all dedup fields are None (no tier attempted)."""
        pool = _mock_pool(fetchrow_return=None)

        txn = {}  # empty dict — all keys missing
        result = await _deduplicate(pool, txn)

        pool.fetchrow.assert_not_called()
        assert result is None

    async def test_only_merchant_and_amount_no_ids(self):
        """Returns None when only merchant/amount present (no account_id, no source_msg)."""
        pool = _mock_pool(fetchrow_return=None)

        txn = {
            "merchant": "Amazon",
            "amount": Decimal("29.99"),
            "posted_at": _NOW,
            # no account_id, external_id, or source_message_id
        }
        result = await _deduplicate(pool, txn)

        pool.fetchrow.assert_not_called()
        assert result is None


# ---------------------------------------------------------------------------
# NULL field edge cases
# ---------------------------------------------------------------------------


class TestNullFieldEdgeCases:
    """NULL/None values at specific priority levels produce graceful skip."""

    async def test_p1_both_fields_null_no_db_call(self):
        """No DB call when both external_id and account_id are None."""
        pool = _mock_pool(fetchrow_return=None)

        txn = {"external_id": None, "account_id": None, "source_message_id": None}
        await _deduplicate(pool, txn)

        pool.fetchval.assert_not_called()

    async def test_p1_only_external_id_null(self):
        """P1 skipped when external_id is None even if account_id is present."""
        pool = _mock_pool(fetchrow_return=None)

        txn = {
            "external_id": None,
            "account_id": _ACCOUNT_ID,
            "source_message_id": None,
        }
        await _deduplicate(pool, txn)

        pool.fetchval.assert_not_called()

    async def test_p1_only_account_id_null(self):
        """P1 skipped when account_id is None even if external_id is present."""
        pool = _mock_pool(fetchrow_return=None)

        txn = {
            "external_id": "ext-123",
            "account_id": None,
            "source_message_id": None,
        }
        await _deduplicate(pool, txn)

        pool.fetchval.assert_not_called()

    async def test_p2_null_source_message_id_no_db_call(self):
        """No P2 DB call when source_message_id is None."""
        pool = _mock_pool(fetchrow_return=None)

        txn = {"external_id": None, "account_id": None, "source_message_id": None}
        await _deduplicate(pool, txn)

        pool.fetchrow.assert_not_called()

    async def test_p3_partial_composite_key_missing_posted_at(self):
        """P3 skipped when posted_at is absent from the txn dict."""
        pool = _mock_pool(fetchrow_return=None)

        txn = {
            "external_id": None,
            "source_message_id": None,
            "account_id": _ACCOUNT_ID,
            # posted_at absent (not None, just missing)
            "amount": Decimal("10.00"),
            "merchant": "Shop",
        }
        await _deduplicate(pool, txn)

        pool.fetchrow.assert_not_called()

    async def test_p3_partial_composite_key_missing_merchant(self):
        """P3 skipped when merchant is absent from the txn dict."""
        pool = _mock_pool(fetchrow_return=None)

        txn = {
            "external_id": None,
            "source_message_id": None,
            "account_id": _ACCOUNT_ID,
            "posted_at": _NOW,
            "amount": Decimal("10.00"),
            # merchant absent
        }
        await _deduplicate(pool, txn)

        pool.fetchrow.assert_not_called()


# ---------------------------------------------------------------------------
# Partial key availability — correct tier selection
# ---------------------------------------------------------------------------


class TestPartialKeyAvailability:
    """Correct tier is selected based on which keys are available."""

    async def test_only_source_message_id_uses_p2(self):
        """When only source_message_id is available, P2 is used (not P3)."""
        pool = _mock_pool(fetchrow_return=_make_row(_TXN_ID))

        txn = {
            "source_message_id": "msg-only",
            # no external_id, no account_id, no composite fields
        }
        result = await _deduplicate(pool, txn)

        assert result == _TXN_ID
        assert pool.fetchrow.call_count == 1

    async def test_external_id_and_account_id_uses_p1_ignores_source_msg(self):
        """P1 is used when external_id + account_id present, even if source_msg also present."""
        pool = _mock_pool(fetchval_return=1, fetchrow_return=_make_row(_TXN_ID))

        txn = {
            "external_id": "ext-999",
            "account_id": _ACCOUNT_ID,
            "source_message_id": "msg-also-present",
            "posted_at": _NOW,
            "amount": Decimal("5.00"),
            "merchant": "Shop",
        }
        result = await _deduplicate(pool, txn)

        assert result == _TXN_ID
        # fetchrow called once: P1 matched → stop
        assert pool.fetchrow.call_count == 1

    async def test_all_p3_keys_no_higher_priority_uses_p3(self):
        """P3 used when all composite keys are available and P1/P2 fields are absent."""
        pool = _mock_pool(fetchrow_return=_make_row(_TXN_ID))

        txn = {
            "external_id": None,
            "source_message_id": None,
            "account_id": _ACCOUNT_ID,
            "posted_at": _NOW,
            "amount": Decimal("20.00"),
            "merchant": "Target",
        }
        result = await _deduplicate(pool, txn)

        assert result == _TXN_ID
        assert pool.fetchrow.call_count == 1

    async def test_float_amount_normalised_for_p3(self):
        """Float amounts are normalised to Decimal for the P3 composite query."""
        pool = _mock_pool(fetchrow_return=_make_row(_TXN_ID))

        txn = {
            "external_id": None,
            "source_message_id": None,
            "account_id": _ACCOUNT_ID,
            "posted_at": _NOW,
            "amount": 12.34,  # float
            "merchant": "Floaty",
        }
        result = await _deduplicate(pool, txn)

        assert result == _TXN_ID
        call_args = pool.fetchrow.call_args
        # The 3rd positional arg (index 2, 0-indexed after SQL) is the amount
        positional = call_args.args
        # SQL is index 0, account_id is 1, posted_at is 2, amount is 3, merchant is 4
        assert isinstance(positional[3], Decimal)
        assert positional[3] == Decimal("12.34")

    async def test_integer_amount_normalised_for_p3(self):
        """Integer amounts are also normalised to Decimal."""
        pool = _mock_pool(fetchrow_return=_make_row(_TXN_ID))

        txn = {
            "external_id": None,
            "source_message_id": None,
            "account_id": _ACCOUNT_ID,
            "posted_at": _NOW,
            "amount": 50,  # int
            "merchant": "Merchant",
        }
        result = await _deduplicate(pool, txn)

        assert result == _TXN_ID
        call_args = pool.fetchrow.call_args
        positional = call_args.args
        assert isinstance(positional[3], Decimal)
        assert positional[3] == Decimal("50")
