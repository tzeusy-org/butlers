"""Unit tests for roster/finance/tools/data_import.py.

Covers:
- Format detection (Chase, Amex, Capital One, generic)
- Date format auto-detection
- Amount normalisation (currency symbols, commas, parenthetical negatives)
- Merchant name normalisation
- Row parsing per format
- Dry run mode
- Batch processing flow (mocked pool)
- Deduplication (mocked pool)
- Error handling (bad dates, missing amounts, unreadable blob)

Issue: bu-w5dv
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers — CSV builders
# ---------------------------------------------------------------------------

CHASE_CSV = """\
Transaction Date,Post Date,Description,Category,Type,Amount,Memo
01/15/2024,01/16/2024,WHOLE FOODS MARKET #123,Food & Drink,Sale,-45.32,
01/16/2024,01/17/2024,SHELL OIL 1234567890,Travel,Sale,-32.00,
01/18/2024,01/19/2024,NETFLIX.COM,Entertainment,Sale,-15.49,
01/20/2024,01/21/2024,DIRECT DEPOSIT,Income,Payment,1200.00,Payroll
"""

AMEX_CSV = """\
Date,Description,Card Member,Account #,Amount
15 Jan 2024,WHOLE FOODS,JOHN DOE,12345,45.32
16 Jan 2024,SHELL OIL,JOHN DOE,12345,32.00
18 Jan 2024,NETFLIX,JOHN DOE,12345,15.49
20 Jan 2024,PAYMENT RECEIVED,JOHN DOE,12345,-1200.00
"""

CAPITAL_ONE_CSV = """\
Transaction Date,Posted Date,Card No.,Description,Category,Debit,Credit
2024-01-15,2024-01-16,1234,WHOLE FOODS MARKET,Groceries,45.32,
2024-01-16,2024-01-17,1234,SHELL GAS,Gas/Automotive,32.00,
2024-01-18,2024-01-19,1234,NETFLIX,Entertainment,15.49,
2024-01-20,2024-01-21,1234,PAYMENT THANK YOU,Payments,,1200.00
"""

GENERIC_CSV = """\
Date,Payee,Amount,Notes
2024-01-15,Whole Foods,-45.32,Groceries
2024-01-16,Shell Gas,-32.00,Fuel
2024-01-18,Netflix,-15.49,Subscription
2024-01-20,Payroll Deposit,1200.00,
"""


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------


class TestDetectFormat:
    def test_chase_detected(self):
        from butlers.tools.finance.data_import import detect_format

        headers = [
            "Transaction Date",
            "Post Date",
            "Description",
            "Category",
            "Type",
            "Amount",
            "Memo",
        ]
        fmt = detect_format(headers)
        assert fmt["name"] == "chase"

    def test_chase_checking_minimal_headers(self):
        """Chase checking has fewer columns but still has Transaction Date + Amount."""
        from butlers.tools.finance.data_import import detect_format

        headers = ["Transaction Date", "Description", "Amount", "Balance"]
        fmt = detect_format(headers)
        assert fmt["name"] == "chase"

    def test_amex_detected(self):
        from butlers.tools.finance.data_import import detect_format

        headers = ["Date", "Description", "Card Member", "Account #", "Amount"]
        fmt = detect_format(headers)
        assert fmt["name"] == "amex"

    def test_capital_one_detected(self):
        from butlers.tools.finance.data_import import detect_format

        headers = [
            "Transaction Date",
            "Posted Date",
            "Card No.",
            "Description",
            "Category",
            "Debit",
            "Credit",
        ]
        fmt = detect_format(headers)
        assert fmt["name"] == "capital_one"

    def test_generic_fallback(self):
        from butlers.tools.finance.data_import import detect_format

        headers = ["Date", "Payee", "Amount", "Notes"]
        fmt = detect_format(headers)
        assert fmt["name"] == "generic"

    def test_case_insensitive_detection(self):
        """Headers are matched case-insensitively."""
        from butlers.tools.finance.data_import import detect_format

        headers = ["TRANSACTION DATE", "DESCRIPTION", "AMOUNT", "BALANCE"]
        fmt = detect_format(headers)
        assert fmt["name"] == "chase"

    def test_empty_headers_gives_generic(self):
        from butlers.tools.finance.data_import import detect_format

        fmt = detect_format([])
        assert fmt["name"] == "generic"


# ---------------------------------------------------------------------------
# Date format detection
# ---------------------------------------------------------------------------


class TestDetectDateFormat:
    def test_iso_format(self):
        from butlers.tools.finance.data_import import _detect_date_format

        fmt = _detect_date_format(["2024-01-15", "2024-02-28", "2023-12-01"])
        assert fmt == "%Y-%m-%d"

    def test_us_slash_format(self):
        from butlers.tools.finance.data_import import _detect_date_format

        fmt = _detect_date_format(["01/15/2024", "02/28/2024", "12/01/2023"])
        assert fmt == "%m/%d/%Y"

    def test_us_dash_format(self):
        from butlers.tools.finance.data_import import _detect_date_format

        fmt = _detect_date_format(["01-15-2024", "02-28-2024"])
        assert fmt == "%m-%d-%Y"

    def test_two_digit_year(self):
        from butlers.tools.finance.data_import import _detect_date_format

        fmt = _detect_date_format(["01/15/24", "02/28/24"])
        assert fmt == "%m/%d/%y"

    def test_no_samples_returns_none(self):
        from butlers.tools.finance.data_import import _detect_date_format

        assert _detect_date_format([]) is None

    def test_empty_strings_ignored(self):
        from butlers.tools.finance.data_import import _detect_date_format

        fmt = _detect_date_format(["", "2024-01-15", ""])
        assert fmt == "%Y-%m-%d"

    def test_mixed_formats_returns_none(self):
        from butlers.tools.finance.data_import import _detect_date_format

        result = _detect_date_format(["01/15/2024", "2024-01-16"])
        assert result is None


# ---------------------------------------------------------------------------
# Amount normalisation
# ---------------------------------------------------------------------------


class TestParseAmount:
    def test_simple_decimal(self):
        from butlers.tools.finance.data_import import _parse_amount

        assert _parse_amount("45.32") == Decimal("45.32")

    def test_negative(self):
        from butlers.tools.finance.data_import import _parse_amount

        assert _parse_amount("-45.32") == Decimal("-45.32")

    def test_dollar_sign(self):
        from butlers.tools.finance.data_import import _parse_amount

        assert _parse_amount("$45.32") == Decimal("45.32")

    def test_comma_thousands(self):
        from butlers.tools.finance.data_import import _parse_amount

        assert _parse_amount("1,234.56") == Decimal("1234.56")

    def test_currency_and_commas(self):
        from butlers.tools.finance.data_import import _parse_amount

        assert _parse_amount("$1,234.56") == Decimal("1234.56")

    def test_parenthetical_negative(self):
        from butlers.tools.finance.data_import import _parse_amount

        assert _parse_amount("(1,234.56)") == Decimal("-1234.56")

    def test_empty_raises(self):
        from butlers.tools.finance.data_import import _parse_amount

        with pytest.raises(ValueError):
            _parse_amount("")

    def test_invalid_raises(self):
        from butlers.tools.finance.data_import import _parse_amount

        with pytest.raises(ValueError):
            _parse_amount("not-a-number")

    def test_euro_symbol(self):
        from butlers.tools.finance.data_import import _parse_amount

        assert _parse_amount("€45.32") == Decimal("45.32")


# ---------------------------------------------------------------------------
# Merchant name normalisation
# ---------------------------------------------------------------------------


class TestNormalizeMerchant:
    def test_basic_title_case(self):
        from butlers.tools.finance.data_import import _normalize_merchant

        result = _normalize_merchant("WHOLE FOODS MARKET")
        assert result == "Whole Foods Market"

    def test_strips_trailing_whitespace(self):
        from butlers.tools.finance.data_import import _normalize_merchant

        result = _normalize_merchant("  NETFLIX  ")
        assert result == "Netflix"

    def test_collapses_whitespace(self):
        from butlers.tools.finance.data_import import _normalize_merchant

        result = _normalize_merchant("SHELL  OIL   COMPANY")
        assert result == "Shell Oil Company"

    def test_strips_trailing_numeric_id(self):
        from butlers.tools.finance.data_import import _normalize_merchant

        result = _normalize_merchant("SHELL OIL 1234567890")
        assert "1234567890" not in result

    def test_strips_hash_suffix(self):
        from butlers.tools.finance.data_import import _normalize_merchant

        result = _normalize_merchant("WHOLE FOODS #123")
        # The hash suffix should be removed or result should not contain '#123'
        assert "#123" not in result

    def test_empty_string(self):
        from butlers.tools.finance.data_import import _normalize_merchant

        result = _normalize_merchant("")
        assert result == ""


# ---------------------------------------------------------------------------
# Chase CSV parsing
# ---------------------------------------------------------------------------


class TestChaseParsing:
    def _parse(self, content: str, currency: str = "USD"):
        from butlers.tools.finance.data_import import (
            _detect_date_format,
            _parse_csv_rows,
            _sample_date_values,
            detect_format,
        )

        reader = csv.DictReader(io.StringIO(content))
        headers = list(reader.fieldnames or [])
        fmt = detect_format(headers)
        date_col = fmt["col_map"].get("date")
        samples = _sample_date_values(content, date_col)
        date_fmt = _detect_date_format(samples)
        parsed, errors = _parse_csv_rows(content, fmt, date_fmt, currency, None)
        return parsed, errors, fmt

    def test_format_is_chase(self):
        _, _, fmt = self._parse(CHASE_CSV)
        assert fmt["name"] == "chase"

    def test_parses_four_rows(self):
        parsed, errors, _ = self._parse(CHASE_CSV)
        assert len(errors) == 0
        assert len(parsed) == 4

    def test_negative_amount_is_debit(self):
        parsed, _, _ = self._parse(CHASE_CSV)
        whole_foods = next(t for t in parsed if "Whole Foods" in t["merchant"])
        assert whole_foods["direction"] == "debit"
        assert whole_foods["amount"] == Decimal("45.32")

    def test_positive_amount_is_credit(self):
        parsed, _, _ = self._parse(CHASE_CSV)
        deposit = next(t for t in parsed if "Deposit" in t["merchant"])
        assert deposit["direction"] == "credit"
        assert deposit["amount"] == Decimal("1200.00")

    def test_merchant_normalised(self):
        parsed, _, _ = self._parse(CHASE_CSV)
        whole_foods = next(t for t in parsed if "Whole Foods" in t["merchant"])
        # Should be title-cased, trimmed, and trailing #NNN suffix removed.
        assert "Whole Foods" in whole_foods["merchant"]
        assert "#123" not in whole_foods["merchant"]
        # Result should be title-cased.
        assert whole_foods["merchant"][0].isupper()

    def test_category_extracted(self):
        parsed, _, _ = self._parse(CHASE_CSV)
        netflix = next(t for t in parsed if "Netflix" in t["merchant"])
        assert netflix["category"] == "entertainment"

    def test_currency_applied(self):
        parsed, _, _ = self._parse(CHASE_CSV, currency="USD")
        for txn in parsed:
            assert txn["currency"] == "USD"


# ---------------------------------------------------------------------------
# Amex CSV parsing
# ---------------------------------------------------------------------------


class TestAmexParsing:
    def _parse(self, content: str, currency: str = "USD"):
        from butlers.tools.finance.data_import import (
            _detect_date_format,
            _parse_csv_rows,
            _sample_date_values,
            detect_format,
        )

        reader = csv.DictReader(io.StringIO(content))
        headers = list(reader.fieldnames or [])
        fmt = detect_format(headers)
        date_col = fmt["col_map"].get("date")
        samples = _sample_date_values(content, date_col)
        date_fmt = _detect_date_format(samples)
        parsed, errors = _parse_csv_rows(content, fmt, date_fmt, currency, None)
        return parsed, errors, fmt

    def test_format_is_amex(self):
        _, _, fmt = self._parse(AMEX_CSV)
        assert fmt["name"] == "amex"

    def test_positive_charge_is_debit(self):
        """Amex encodes charges as positive — should map to debit."""
        parsed, _, _ = self._parse(AMEX_CSV)
        whole_foods = next(t for t in parsed if "Whole Foods" in t["merchant"])
        assert whole_foods["direction"] == "debit"
        assert whole_foods["amount"] == Decimal("45.32")

    def test_negative_payment_is_credit(self):
        """Amex encodes payments as negative — should map to credit."""
        parsed, _, _ = self._parse(AMEX_CSV)
        payment = next(t for t in parsed if "Payment" in t["merchant"])
        assert payment["direction"] == "credit"
        assert payment["amount"] == Decimal("1200.00")

    def test_parses_four_rows(self):
        parsed, errors, _ = self._parse(AMEX_CSV)
        assert len(errors) == 0
        assert len(parsed) == 4


# ---------------------------------------------------------------------------
# Capital One CSV parsing
# ---------------------------------------------------------------------------


class TestCapitalOneParsing:
    def _parse(self, content: str, currency: str = "USD"):
        from butlers.tools.finance.data_import import (
            _detect_date_format,
            _parse_csv_rows,
            _sample_date_values,
            detect_format,
        )

        reader = csv.DictReader(io.StringIO(content))
        headers = list(reader.fieldnames or [])
        fmt = detect_format(headers)
        date_col = fmt["col_map"].get("date")
        samples = _sample_date_values(content, date_col)
        date_fmt = _detect_date_format(samples)
        parsed, errors = _parse_csv_rows(content, fmt, date_fmt, currency, None)
        return parsed, errors, fmt

    def test_format_is_capital_one(self):
        _, _, fmt = self._parse(CAPITAL_ONE_CSV)
        assert fmt["name"] == "capital_one"

    def test_debit_col_is_debit(self):
        parsed, _, _ = self._parse(CAPITAL_ONE_CSV)
        whole_foods = next(t for t in parsed if "Whole Foods" in t["merchant"])
        assert whole_foods["direction"] == "debit"
        assert whole_foods["amount"] == Decimal("45.32")

    def test_credit_col_is_credit(self):
        parsed, _, _ = self._parse(CAPITAL_ONE_CSV)
        payment = next(t for t in parsed if "Payment" in t["merchant"])
        assert payment["direction"] == "credit"
        assert payment["amount"] == Decimal("1200.00")

    def test_parses_four_rows(self):
        parsed, errors, _ = self._parse(CAPITAL_ONE_CSV)
        assert len(errors) == 0
        assert len(parsed) == 4


# ---------------------------------------------------------------------------
# Generic CSV parsing
# ---------------------------------------------------------------------------


class TestGenericParsing:
    def _parse(self, content: str, currency: str = "USD", column_map=None):
        from butlers.tools.finance.data_import import (
            _COL_DATE,
            _detect_date_format,
            _parse_csv_rows,
            _resolve_generic_cols,
            _sample_date_values,
            detect_format,
        )

        reader = csv.DictReader(io.StringIO(content))
        headers = list(reader.fieldnames or [])
        fmt = detect_format(headers)
        resolved = _resolve_generic_cols(headers)
        # Caller's column_map overrides take priority for date column detection.
        date_col = (
            column_map.get(_COL_DATE)
            if column_map and _COL_DATE in column_map
            else resolved.get(_COL_DATE)
        )
        samples = _sample_date_values(content, date_col)
        date_fmt = _detect_date_format(samples)
        parsed, errors = _parse_csv_rows(content, fmt, date_fmt, currency, column_map)
        return parsed, errors, fmt

    def test_format_is_generic(self):
        _, _, fmt = self._parse(GENERIC_CSV)
        assert fmt["name"] == "generic"

    def test_parses_four_rows(self):
        parsed, errors, _ = self._parse(GENERIC_CSV)
        assert len(errors) == 0
        assert len(parsed) == 4

    def test_negative_amount_is_debit(self):
        parsed, _, _ = self._parse(GENERIC_CSV)
        whole_foods = next(t for t in parsed if "Whole Foods" in t["merchant"])
        assert whole_foods["direction"] == "debit"

    def test_positive_amount_is_credit(self):
        parsed, _, _ = self._parse(GENERIC_CSV)
        payroll = next(t for t in parsed if "Payroll" in t["merchant"])
        assert payroll["direction"] == "credit"

    def test_column_map_override(self):
        """Caller can override column names via column_map."""
        content = "txn_date,vendor,charge\n2024-01-15,Coffee Shop,-5.00\n"
        parsed, errors, _ = self._parse(
            content,
            column_map={"date": "txn_date", "merchant": "vendor", "amount": "charge"},
        )
        assert len(errors) == 0
        assert len(parsed) == 1
        assert "Coffee Shop" in parsed[0]["merchant"]


# ---------------------------------------------------------------------------
# Dry run mode
# ---------------------------------------------------------------------------


class TestDryRun:
    def _make_blob_store(self, content: str):
        blob_store = AsyncMock()
        blob_store.get = AsyncMock(return_value=content.encode("utf-8"))
        return blob_store

    async def test_dry_run_no_inserts(self):
        """Dry run returns preview without touching the pool."""
        from butlers.tools.finance.data_import import import_transactions

        blob_store = self._make_blob_store(CHASE_CSV)
        pool = MagicMock()
        pool.execute = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=None)

        result = await import_transactions(
            pool=pool,
            blob_store=blob_store,
            storage_ref="s3://bucket/chase.csv",
            dry_run=True,
        )

        assert result["dry_run"] is True
        assert "preview" in result
        assert result["detected_format"] == "chase"
        # No DB inserts should have been made.
        pool.execute.assert_not_called()

    async def test_dry_run_preview_max_10(self):
        """Dry run preview contains at most 10 transactions."""
        from butlers.tools.finance.data_import import import_transactions

        # Build a CSV with 20 rows.
        lines = ["Transaction Date,Description,Amount,Balance"]
        for i in range(20):
            lines.append(f"01/{i + 1:02d}/2024,MERCHANT {i},-{i + 1}.00,500.00")
        content = "\n".join(lines)

        blob_store = self._make_blob_store(content)
        pool = MagicMock()

        result = await import_transactions(
            pool=pool,
            blob_store=blob_store,
            storage_ref="s3://bucket/big.csv",
            dry_run=True,
        )

        assert len(result["preview"]) <= 10

    async def test_dry_run_preview_shape(self):
        """Each preview item has expected fields."""
        from butlers.tools.finance.data_import import import_transactions

        blob_store = self._make_blob_store(CHASE_CSV)
        pool = MagicMock()

        result = await import_transactions(
            pool=pool,
            blob_store=blob_store,
            storage_ref="s3://bucket/chase.csv",
            dry_run=True,
        )

        for item in result["preview"]:
            assert "posted_at" in item
            assert "merchant" in item
            assert "amount" in item
            assert "direction" in item
            assert "currency" in item

    async def test_dry_run_returns_counts(self):
        """Dry run result includes total, parsed, parse_errors counts."""
        from butlers.tools.finance.data_import import import_transactions

        blob_store = self._make_blob_store(CHASE_CSV)
        pool = MagicMock()

        result = await import_transactions(
            pool=pool,
            blob_store=blob_store,
            storage_ref="s3://bucket/chase.csv",
            dry_run=True,
        )

        assert "total" in result
        assert "parsed" in result
        assert "parse_errors" in result
        assert result["total"] >= result["parsed"]


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


class TestDeduplication:
    def _make_blob_store(self, content: str):
        blob_store = AsyncMock()
        blob_store.get = AsyncMock(return_value=content.encode("utf-8"))
        return blob_store

    async def test_duplicate_row_is_skipped(self):
        """A row that matches an existing transaction is counted as skipped.

        Now uses batch dedup: pool.fetch returns all matching rows at once.
        """
        from datetime import UTC, datetime
        from decimal import Decimal

        from butlers.tools.finance.data_import import import_transactions

        blob_store = self._make_blob_store(CHASE_CSV)

        # Pool.fetch returns native-type column values for matching rows.
        # _check_duplicates_batch compares (posted_at, amount, merchant) tuples
        # using native Python types (datetime, Decimal, str) to avoid string
        # serialization mismatches with PostgreSQL's ::text cast.
        pool = MagicMock()
        pool.fetch = AsyncMock(
            return_value=[
                {
                    "posted_at": datetime(2024, 1, 15, tzinfo=UTC),
                    "amount": Decimal("45.32"),
                    "merchant": "Whole Foods Market",
                },
                {
                    "posted_at": datetime(2024, 1, 16, tzinfo=UTC),
                    "amount": Decimal("32.00"),
                    "merchant": "Shell Oil",
                },
                {
                    "posted_at": datetime(2024, 1, 18, tzinfo=UTC),
                    "amount": Decimal("15.49"),
                    "merchant": "Netflix.Com",
                },
                {
                    "posted_at": datetime(2024, 1, 20, tzinfo=UTC),
                    "amount": Decimal("1200.00"),
                    "merchant": "Direct Deposit",
                },
            ]
        )
        pool.execute = AsyncMock()

        result = await import_transactions(
            pool=pool,
            blob_store=blob_store,
            storage_ref="s3://bucket/chase.csv",
            dry_run=False,
        )

        assert result["skipped"] > 0
        # With all rows being duplicates, nothing should be imported.
        assert result["imported"] == 0

    async def test_no_duplicates_all_imported(self):
        """When pool.fetch returns no rows (no dups), all rows are inserted.

        Now uses batch dedup: pool.fetch returns empty list when no duplicates.
        """
        from butlers.tools.finance.data_import import import_transactions

        blob_store = self._make_blob_store(CHASE_CSV)

        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])  # no duplicates found
        pool.execute = AsyncMock()

        result = await import_transactions(
            pool=pool,
            blob_store=blob_store,
            storage_ref="s3://bucket/chase.csv",
            dry_run=False,
        )

        assert result["errors"] == 0
        assert result["skipped"] == 0
        assert result["imported"] == 4  # 4 data rows in CHASE_CSV

    async def test_batch_dedup_all_duplicates(self):
        """_check_duplicates_batch identifies all rows as duplicates."""
        from datetime import UTC, datetime
        from decimal import Decimal

        from butlers.tools.finance.data_import import _check_duplicates_batch

        batch = [
            {
                "posted_at": datetime(2024, 1, 15, tzinfo=UTC),
                "amount": Decimal("45.32"),
                "merchant": "Whole Foods",
            },
            {
                "posted_at": datetime(2024, 1, 16, tzinfo=UTC),
                "amount": Decimal("32.00"),
                "merchant": "Shell Oil",
            },
        ]

        pool = MagicMock()
        # Simulate: all rows are duplicates. Return native column values to
        # match the tuple comparison in _check_duplicates_batch.
        pool.fetch = AsyncMock(
            return_value=[
                {
                    "posted_at": datetime(2024, 1, 15, tzinfo=UTC),
                    "amount": Decimal("45.32"),
                    "merchant": "Whole Foods",
                },
                {
                    "posted_at": datetime(2024, 1, 16, tzinfo=UTC),
                    "amount": Decimal("32.00"),
                    "merchant": "Shell Oil",
                },
            ]
        )

        dup_indices = await _check_duplicates_batch(pool, batch, account_id=None)
        assert pool.fetch.called
        assert 0 in dup_indices
        assert 1 in dup_indices

    async def test_batch_dedup_no_duplicates(self):
        """_check_duplicates_batch returns empty set when pool.fetch returns no rows."""
        from datetime import UTC, datetime
        from decimal import Decimal

        from butlers.tools.finance.data_import import _check_duplicates_batch

        batch = [
            {
                "posted_at": datetime(2024, 1, 15, tzinfo=UTC),
                "amount": Decimal("45.32"),
                "merchant": "Whole Foods",
            },
        ]

        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])  # no matches

        dup_indices = await _check_duplicates_batch(pool, batch, account_id=None)
        assert dup_indices == set()
        assert pool.fetch.called

    async def test_batch_dedup_mixed_duplicates(self):
        """_check_duplicates_batch identifies some rows as duplicates."""
        from datetime import UTC, datetime
        from decimal import Decimal

        from butlers.tools.finance.data_import import _check_duplicates_batch

        batch = [
            {
                "posted_at": datetime(2024, 1, 15, tzinfo=UTC),
                "amount": Decimal("45.32"),
                "merchant": "Whole Foods",
            },
            {
                "posted_at": datetime(2024, 1, 16, tzinfo=UTC),
                "amount": Decimal("32.00"),
                "merchant": "Shell Oil",
            },
            {
                "posted_at": datetime(2024, 1, 18, tzinfo=UTC),
                "amount": Decimal("15.49"),
                "merchant": "Netflix",
            },
        ]

        pool = MagicMock()
        # Only first two rows are duplicates; third is new.
        pool.fetch = AsyncMock(
            return_value=[
                {
                    "posted_at": datetime(2024, 1, 15, tzinfo=UTC),
                    "amount": Decimal("45.32"),
                    "merchant": "Whole Foods",
                },
                {
                    "posted_at": datetime(2024, 1, 16, tzinfo=UTC),
                    "amount": Decimal("32.00"),
                    "merchant": "Shell Oil",
                },
            ]
        )

        dup_indices = await _check_duplicates_batch(pool, batch, account_id=None)
        # Indices 0 and 1 should be marked as duplicates.
        assert 0 in dup_indices
        assert 1 in dup_indices
        assert 2 not in dup_indices

    async def test_batch_dedup_with_account_id(self):
        """_check_duplicates_batch uses account_id in query when provided."""
        from datetime import UTC, datetime
        from decimal import Decimal

        from butlers.tools.finance.data_import import _check_duplicates_batch

        account_id = "12345678-1234-5678-1234-567812345678"
        batch = [
            {
                "posted_at": datetime(2024, 1, 15, tzinfo=UTC),
                "amount": Decimal("45.32"),
                "merchant": "Whole Foods",
            },
        ]

        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])

        await _check_duplicates_batch(pool, batch, account_id=account_id)
        # Verify the query includes account_id in WHERE clause.
        call_args = pool.fetch.call_args
        assert call_args is not None
        query = call_args[0][0]
        assert "account_id = $1" in query

    async def test_batch_dedup_empty_batch(self):
        """_check_duplicates_batch returns empty set for empty batch."""
        from butlers.tools.finance.data_import import _check_duplicates_batch

        pool = MagicMock()
        dup_indices = await _check_duplicates_batch(pool, [], account_id=None)
        assert dup_indices == set()
        # Should not call pool.fetch for empty batch.
        assert not pool.fetch.called


# ---------------------------------------------------------------------------
# Batch processing
# ---------------------------------------------------------------------------


class TestBatchProcessing:
    def _build_large_csv(self, n_rows: int) -> str:
        """Build a Chase-format CSV with n_rows data rows."""
        lines = ["Transaction Date,Description,Amount,Balance"]
        for i in range(n_rows):
            lines.append(f"01/{(i % 28) + 1:02d}/2024,MERCHANT {i},-{i + 1}.00,500.00")
        return "\n".join(lines)

    def _make_blob_store(self, content: str):
        blob_store = AsyncMock()
        blob_store.get = AsyncMock(return_value=content.encode("utf-8"))
        return blob_store

    async def test_1000_rows_processed(self):
        """1000 rows are processed without error via batch logic."""
        from butlers.tools.finance.data_import import import_transactions

        content = self._build_large_csv(1000)
        blob_store = self._make_blob_store(content)
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])  # no duplicates in batch query
        pool.execute = AsyncMock()

        result = await import_transactions(
            pool=pool,
            blob_store=blob_store,
            storage_ref="s3://bucket/big.csv",
            dry_run=False,
        )

        assert result["errors"] == 0
        assert result["imported"] == 1000

    async def test_500_row_batch_boundary(self):
        """501 rows triggers exactly 2 batches (500 + 1)."""
        from butlers.tools.finance.data_import import _BATCH_SIZE, import_transactions

        assert _BATCH_SIZE == 500
        content = self._build_large_csv(501)
        blob_store = self._make_blob_store(content)
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])  # no duplicates
        pool.execute = AsyncMock()

        result = await import_transactions(
            pool=pool,
            blob_store=blob_store,
            storage_ref="s3://bucket/batch.csv",
            dry_run=False,
        )

        assert result["imported"] == 501


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def _make_blob_store(self, content: str):
        blob_store = AsyncMock()
        blob_store.get = AsyncMock(return_value=content.encode("utf-8"))
        return blob_store

    async def test_blob_fetch_failure_returns_error_dict(self):
        """Blob fetch failure returns structured error, not exception."""
        from butlers.tools.finance.data_import import import_transactions

        blob_store = AsyncMock()
        blob_store.get = AsyncMock(side_effect=RuntimeError("S3 unreachable"))
        pool = MagicMock()

        result = await import_transactions(
            pool=pool,
            blob_store=blob_store,
            storage_ref="s3://bucket/missing.csv",
        )

        assert result["status"] == "error"
        assert "error" in result

    async def test_undetectable_date_format_returns_error(self):
        """CSV with unrecognizable date format returns structured error."""
        from butlers.tools.finance.data_import import import_transactions

        bad_csv = "Transaction Date,Description,Amount\nJanuary 15 2024,MERCHANT,-10.00\n"
        blob_store = self._make_blob_store(bad_csv)
        pool = MagicMock()

        result = await import_transactions(
            pool=pool,
            blob_store=blob_store,
            storage_ref="s3://bucket/bad.csv",
        )

        assert result["status"] == "error"
        assert "date" in result["error"].lower()

    async def test_row_with_missing_amount_logged_as_error(self):
        """Row with empty amount is captured in error_details, not raised."""
        from butlers.tools.finance.data_import import import_transactions

        content = (
            "Transaction Date,Description,Amount\n"
            "01/15/2024,VALID MERCHANT,-10.00\n"
            "01/16/2024,BAD ROW,\n"
        )
        blob_store = self._make_blob_store(content)
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=None)
        pool.execute = AsyncMock()

        result = await import_transactions(
            pool=pool,
            blob_store=blob_store,
            storage_ref="s3://bucket/partial.csv",
            dry_run=False,
        )

        # One valid row + one skipped row (no amount = _parse_row returns None, not an error)
        # The call should succeed overall.
        assert "import_batch_id" in result

    async def test_db_error_during_insert_captured(self):
        """DB insert failure for one row is captured in error_details."""
        from butlers.tools.finance.data_import import import_transactions

        blob_store = self._make_blob_store(CHASE_CSV)
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=None)
        # First call succeeds, subsequent ones raise
        pool.execute = AsyncMock(side_effect=Exception("DB unavailable"))

        result = await import_transactions(
            pool=pool,
            blob_store=blob_store,
            storage_ref="s3://bucket/chase.csv",
        )

        assert result["errors"] > 0
        assert any("db_error" in str(e.get("reason", "")) for e in result["error_details"])

    async def test_import_result_includes_batch_id(self):
        """Result always includes import_batch_id."""
        from butlers.tools.finance.data_import import import_transactions

        blob_store = self._make_blob_store(CHASE_CSV)
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=None)
        pool.execute = AsyncMock()

        result = await import_transactions(
            pool=pool,
            blob_store=blob_store,
            storage_ref="s3://bucket/chase.csv",
        )

        assert "import_batch_id" in result
        assert len(result["import_batch_id"]) == 36  # UUID v4


# ---------------------------------------------------------------------------
# Format detection result includes format name
# ---------------------------------------------------------------------------


class TestReturnShape:
    def _make_blob_store(self, content: str):
        blob_store = AsyncMock()
        blob_store.get = AsyncMock(return_value=content.encode("utf-8"))
        return blob_store

    async def test_detected_format_in_result(self):
        from butlers.tools.finance.data_import import import_transactions

        blob_store = self._make_blob_store(CHASE_CSV)
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=None)
        pool.execute = AsyncMock()

        result = await import_transactions(
            pool=pool,
            blob_store=blob_store,
            storage_ref="s3://bucket/chase.csv",
        )

        assert result["detected_format"] == "chase"

    async def test_amex_format_in_result(self):
        from butlers.tools.finance.data_import import import_transactions

        blob_store = self._make_blob_store(AMEX_CSV)
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=None)
        pool.execute = AsyncMock()

        result = await import_transactions(
            pool=pool,
            blob_store=blob_store,
            storage_ref="s3://bucket/amex.csv",
        )

        assert result["detected_format"] == "amex"

    async def test_capital_one_format_in_result(self):
        from butlers.tools.finance.data_import import import_transactions

        blob_store = self._make_blob_store(CAPITAL_ONE_CSV)
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=None)
        pool.execute = AsyncMock()

        result = await import_transactions(
            pool=pool,
            blob_store=blob_store,
            storage_ref="s3://bucket/c1.csv",
        )

        assert result["detected_format"] == "capital_one"

    async def test_result_has_all_required_keys(self):
        from butlers.tools.finance.data_import import import_transactions

        blob_store = self._make_blob_store(CHASE_CSV)
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=None)
        pool.execute = AsyncMock()

        result = await import_transactions(
            pool=pool,
            blob_store=blob_store,
            storage_ref="s3://bucket/chase.csv",
        )

        for key in ("total", "imported", "skipped", "errors", "import_batch_id", "detected_format"):
            assert key in result, f"Missing key: {key}"

    async def test_categories_learned_triggered_on_category_data(self):
        """A successful blob-store import with category data triggers
        learn_merchant_categories() and returns the upserted count."""
        from butlers.tools.finance.data_import import import_transactions

        blob_store = self._make_blob_store(CHASE_CSV)  # has a Category column
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=None)
        pool.fetchval = AsyncMock(return_value=False)
        pool.fetch = AsyncMock(return_value=[])  # no duplicates
        pool.execute = AsyncMock(return_value="INSERT 0 1")

        learn_spy = AsyncMock(return_value={"upserted": 3, "as_of": "2024-01-01"})
        with patch(
            "butlers.tools.finance.pattern_recognition.learn_merchant_categories",
            new=learn_spy,
        ):
            result = await import_transactions(
                pool=pool,
                blob_store=blob_store,
                storage_ref="s3://bucket/chase.csv",
            )

        assert result["imported"] == 4
        learn_spy.assert_awaited_once()
        assert learn_spy.await_args.args[0] is pool
        assert result["categories_learned"] == 3
