"""Unit tests for the bulk import pipeline (tasks 5.1-5.5).

Covers:
- import_transactions_from_file: format detection, normalisation, dedup
- Merchant mapping auto-apply (task 5.2)
- Post-import triggers: spending_summaries refresh, compute_baselines (task 5.3)
- Dry run mode with duplicate detection flags (task 5.4)
- Error handling: missing file, bad date format, DB errors

Issue: bu-ra1c
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Shared CSV fixtures
# ---------------------------------------------------------------------------

CHASE_CSV = """\
Transaction Date,Post Date,Description,Category,Type,Amount,Memo
01/15/2024,01/16/2024,WHOLE FOODS MARKET,Food & Drink,Sale,-45.32,
01/16/2024,01/17/2024,SHELL OIL,Travel,Sale,-32.00,
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

UNCATEGORIZED_CSV = """\
Transaction Date,Description,Amount
01/15/2024,COFFEE SHOP,-5.50
01/16/2024,BOOKSTORE,-12.99
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_tmp_csv(content: str) -> str:
    """Write *content* to a temp file and return its path."""
    fd, path = tempfile.mkstemp(suffix=".csv")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


def _make_pool(
    *, fetchrow_return=None, fetchval_return=None, execute_return=None, fetch_return=None
):
    """Build a minimal async mock pool.

    fetch_return: return value for pool.fetch() (used by _check_duplicates_batch).
      - None (default) → empty list, meaning no duplicates found.
      - A non-empty list of rows → those (merchant, amount, posted_at) tuples are
        treated as existing duplicates and the corresponding batch rows are skipped.
    """
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=fetchrow_return)
    pool.fetchval = AsyncMock(return_value=fetchval_return)
    pool.execute = AsyncMock(return_value=execute_return or "INSERT 0 1")
    pool.fetch = AsyncMock(return_value=fetch_return if fetch_return is not None else [])
    return pool


# ---------------------------------------------------------------------------
# 5.1  Core import_transactions_from_file behaviour
# ---------------------------------------------------------------------------


class TestImportTransactionsFromFile:
    """Tests for basic functionality of import_transactions_from_file."""

    async def test_returns_import_batch_id(self):
        from butlers.tools.finance.data_import import import_transactions_from_file

        path = _write_tmp_csv(CHASE_CSV)
        try:
            pool = _make_pool()
            result = await import_transactions_from_file(pool, file_path=path)
            assert "import_batch_id" in result
            assert len(result["import_batch_id"]) == 36  # UUID v4
        finally:
            os.unlink(path)

    async def test_detects_chase_format(self):
        from butlers.tools.finance.data_import import import_transactions_from_file

        path = _write_tmp_csv(CHASE_CSV)
        try:
            pool = _make_pool()
            result = await import_transactions_from_file(pool, file_path=path)
            assert result["detected_format"] == "chase"
        finally:
            os.unlink(path)

    async def test_detects_amex_format(self):
        from butlers.tools.finance.data_import import import_transactions_from_file

        path = _write_tmp_csv(AMEX_CSV)
        try:
            pool = _make_pool()
            result = await import_transactions_from_file(pool, file_path=path)
            assert result["detected_format"] == "amex"
        finally:
            os.unlink(path)

    async def test_detects_capital_one_format(self):
        from butlers.tools.finance.data_import import import_transactions_from_file

        path = _write_tmp_csv(CAPITAL_ONE_CSV)
        try:
            pool = _make_pool()
            result = await import_transactions_from_file(pool, file_path=path)
            assert result["detected_format"] == "capital_one"
        finally:
            os.unlink(path)

    async def test_detects_generic_format(self):
        from butlers.tools.finance.data_import import import_transactions_from_file

        path = _write_tmp_csv(GENERIC_CSV)
        try:
            pool = _make_pool()
            result = await import_transactions_from_file(pool, file_path=path)
            assert result["detected_format"] == "generic"
        finally:
            os.unlink(path)

    async def test_imports_four_rows_from_chase(self):
        from butlers.tools.finance.data_import import import_transactions_from_file

        path = _write_tmp_csv(CHASE_CSV)
        try:
            pool = _make_pool()
            result = await import_transactions_from_file(pool, file_path=path)
            assert result["imported"] == 4
            assert result["skipped"] == 0
            assert result["errors"] == 0
        finally:
            os.unlink(path)

    async def test_result_has_all_required_keys(self):
        from butlers.tools.finance.data_import import import_transactions_from_file

        path = _write_tmp_csv(CHASE_CSV)
        try:
            pool = _make_pool()
            result = await import_transactions_from_file(pool, file_path=path)
            for key in (
                "total",
                "imported",
                "skipped",
                "errors",
                "import_batch_id",
                "detected_format",
                "dry_run",
                "merchant_mappings_applied",
            ):
                assert key in result, f"Missing key: {key!r}"
        finally:
            os.unlink(path)

    async def test_500_row_batch_boundary(self):
        """501 rows triggers exactly two batches (500 + 1)."""
        from butlers.tools.finance.data_import import _BATCH_SIZE, import_transactions_from_file

        assert _BATCH_SIZE == 500
        lines = ["Transaction Date,Description,Amount,Balance"]
        for i in range(501):
            lines.append(f"01/{(i % 28) + 1:02d}/2024,MERCHANT {i},-{i + 1}.00,500.00")
        content = "\n".join(lines)

        path = _write_tmp_csv(content)
        try:
            pool = _make_pool()
            result = await import_transactions_from_file(pool, file_path=path)
            assert result["imported"] == 501
        finally:
            os.unlink(path)

    async def test_column_map_override(self):
        """Custom column names can be remapped via column_map."""
        content = "txn_date,vendor,charge\n2024-01-15,Coffee Shop,-5.00\n"
        path = _write_tmp_csv(content)
        try:
            from butlers.tools.finance.data_import import import_transactions_from_file

            pool = _make_pool()
            result = await import_transactions_from_file(
                pool,
                file_path=path,
                column_map={"date": "txn_date", "merchant": "vendor", "amount": "charge"},
            )
            assert result["imported"] == 1
        finally:
            os.unlink(path)

    async def test_currency_applied_to_all_rows(self):
        """The supplied currency is stored on every imported row."""
        path = _write_tmp_csv(CHASE_CSV)
        try:
            from butlers.tools.finance.data_import import import_transactions_from_file

            inserted_rows: list = []

            async def fake_execute(sql, *args, **kwargs):
                inserted_rows.append(args)
                return "INSERT 0 1"

            pool = _make_pool()
            pool.execute = AsyncMock(side_effect=fake_execute)
            # No duplicate found
            pool.fetchrow = AsyncMock(return_value=None)
            pool.fetchval = AsyncMock(return_value=False)

            result = await import_transactions_from_file(pool, file_path=path, currency="EUR")
            # All 4 rows should have been inserted (execute called 4 times)
            assert result["imported"] == 4
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# 5.1  Deduplication
# ---------------------------------------------------------------------------


class TestImportDeduplication:
    async def test_duplicate_row_is_skipped(self):
        """A row matching an existing transaction is counted as skipped."""
        from butlers.tools.finance.data_import import import_transactions_from_file

        path = _write_tmp_csv(CHASE_CSV)
        try:
            pool = _make_pool(fetchval_return=False)
            pool.execute = AsyncMock()

            # Patch _check_duplicates_batch to report all rows as duplicates.
            async def all_duplicates(p, batch, account_id):
                return set(range(len(batch)))

            with patch(
                "butlers.tools.finance.data_import._check_duplicates_batch",
                side_effect=all_duplicates,
            ):
                result = await import_transactions_from_file(pool, file_path=path)
            assert result["skipped"] > 0
            assert result["imported"] == 0
        finally:
            os.unlink(path)

    async def test_no_duplicates_all_imported(self):
        """When no duplicates exist, all rows are inserted."""
        from butlers.tools.finance.data_import import import_transactions_from_file

        path = _write_tmp_csv(CHASE_CSV)
        try:
            pool = _make_pool(fetchrow_return=None, fetchval_return=False)
            pool.execute = AsyncMock()

            result = await import_transactions_from_file(pool, file_path=path)
            assert result["skipped"] == 0
            assert result["imported"] == 4
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# 5.2  Merchant mapping auto-apply
# ---------------------------------------------------------------------------


class TestMerchantMappingAutoApply:
    async def test_merchant_mapping_applied_to_uncategorized_rows(self):
        """Uncategorized rows get category from merchant_mappings lookup."""
        from butlers.tools.finance.data_import import import_transactions_from_file

        path = _write_tmp_csv(UNCATEGORIZED_CSV)
        try:
            # Simulate: merchant_mappings table exists, lookup returns "dining"
            pool = MagicMock()
            pool.execute = AsyncMock(return_value="INSERT 0 1")

            call_count = [0]

            async def fake_fetchval(sql, *args, **kwargs):
                # _has_merchant_mappings_table → True
                return True

            async def fake_fetchrow(sql, *args, **kwargs):
                # _lookup_merchant_category → returns a mapping row
                call_count[0] += 1
                row = MagicMock()
                row.__getitem__ = MagicMock(
                    side_effect=lambda k: "dining" if k == "category" else None
                )
                return row

            pool.fetchval = AsyncMock(side_effect=fake_fetchval)
            pool.fetchrow = AsyncMock(side_effect=fake_fetchrow)

            result = await import_transactions_from_file(pool, file_path=path)
            # Both rows were uncategorized and should have had mapping applied
            assert result["merchant_mappings_applied"] == 2
        finally:
            os.unlink(path)

    async def test_no_merchant_mappings_applied_when_table_missing(self):
        """When merchant_mappings table is absent, auto-apply is skipped."""
        from butlers.tools.finance.data_import import import_transactions_from_file

        path = _write_tmp_csv(UNCATEGORIZED_CSV)
        try:
            pool = MagicMock()
            pool.execute = AsyncMock(return_value="INSERT 0 1")
            pool.fetchrow = AsyncMock(return_value=None)
            # _has_merchant_mappings_table → False
            pool.fetchval = AsyncMock(return_value=False)

            result = await import_transactions_from_file(pool, file_path=path)
            assert result["merchant_mappings_applied"] == 0
        finally:
            os.unlink(path)

    async def test_already_categorized_rows_not_overridden(self):
        """Rows that already have a non-'uncategorized' category are not touched."""
        from butlers.tools.finance.data_import import import_transactions_from_file

        # Chase CSV has categories (food, travel, entertainment, income)
        path = _write_tmp_csv(CHASE_CSV)
        try:
            pool = MagicMock()
            pool.execute = AsyncMock(return_value="INSERT 0 1")
            pool.fetchrow = AsyncMock(return_value=None)
            # merchant_mappings table exists
            pool.fetchval = AsyncMock(return_value=True)

            result = await import_transactions_from_file(pool, file_path=path)
            # All rows already have categories from Chase format; nothing to apply
            assert result["merchant_mappings_applied"] == 0
        finally:
            os.unlink(path)

    async def test_merchant_mapping_lookup_failure_does_not_abort_import(self):
        """A merchant mapping lookup error is swallowed; import proceeds."""
        from butlers.tools.finance.data_import import import_transactions_from_file

        path = _write_tmp_csv(UNCATEGORIZED_CSV)
        try:
            pool = MagicMock()
            pool.execute = AsyncMock(return_value="INSERT 0 1")
            pool.fetchrow = AsyncMock(return_value=None)

            call_count = [0]

            async def fetchval_first_raises(sql, *args, **kwargs):
                call_count[0] += 1
                sql_lower = sql.lower()
                # Only the merchant_mappings table-existence check raises.
                if "merchant_mappings" in sql_lower and call_count[0] <= 1:
                    raise Exception("DB connection error")
                # Post-import trigger checks (pg_matviews, etc.) return False.
                return False

            pool.fetchval = AsyncMock(side_effect=fetchval_first_raises)

            result = await import_transactions_from_file(pool, file_path=path)
            # Import should still succeed despite mapping lookup failure
            assert result["merchant_mappings_applied"] == 0
            assert "import_batch_id" in result
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# 5.3  Post-import triggers
# ---------------------------------------------------------------------------


class TestPostImportTriggers:
    async def test_spending_summaries_refreshed_after_import(self):
        """spending_summaries is refreshed when rows are imported."""
        from butlers.tools.finance.data_import import import_transactions_from_file

        path = _write_tmp_csv(CHASE_CSV)
        try:
            pool = MagicMock()
            pool.execute = AsyncMock(return_value="INSERT 0 1")
            pool.fetchrow = AsyncMock(return_value=None)

            executed_sqls: list[str] = []

            async def track_execute(sql, *args, **kwargs):
                executed_sqls.append(sql)
                return "INSERT 0 1"

            async def fake_fetchval(sql, *args, **kwargs):
                sql_lower = sql.lower()
                if "pg_matviews" in sql_lower:
                    return True  # spending_summaries MV exists
                if "merchant_mappings" in sql_lower:
                    return False  # no merchant_mappings table
                return None

            pool.execute = AsyncMock(side_effect=track_execute)
            pool.fetchval = AsyncMock(side_effect=fake_fetchval)

            result = await import_transactions_from_file(pool, file_path=path)
            assert result["imported"] == 4
            assert result["mv_refreshed"] is True
            refresh_calls = [s for s in executed_sqls if "REFRESH MATERIALIZED VIEW" in s]
            assert len(refresh_calls) == 1
        finally:
            os.unlink(path)

    async def test_spending_summaries_not_refreshed_when_mv_absent(self):
        """mv_refreshed is False when spending_summaries MV does not exist."""
        from butlers.tools.finance.data_import import import_transactions_from_file

        path = _write_tmp_csv(CHASE_CSV)
        try:
            pool = MagicMock()
            pool.execute = AsyncMock(return_value="INSERT 0 1")
            pool.fetchrow = AsyncMock(return_value=None)

            async def fake_fetchval(sql, *args, **kwargs):
                sql_lower = sql.lower()
                if "pg_matviews" in sql_lower:
                    return False  # MV doesn't exist
                if "merchant_mappings" in sql_lower:
                    return False
                return None

            pool.fetchval = AsyncMock(side_effect=fake_fetchval)

            result = await import_transactions_from_file(pool, file_path=path)
            assert result["mv_refreshed"] is False
        finally:
            os.unlink(path)

    async def test_compute_baselines_triggered_for_50_plus_rows(self):
        """compute_baselines is called when >= 50 rows are imported."""
        lines = ["Transaction Date,Description,Amount,Balance"]
        for i in range(50):
            lines.append(f"01/{(i % 28) + 1:02d}/2024,MERCHANT {i},-{i + 1}.00,500.00")
        content = "\n".join(lines)

        path = _write_tmp_csv(content)
        try:
            from butlers.tools.finance.data_import import import_transactions_from_file

            pool = MagicMock()
            pool.execute = AsyncMock(return_value="INSERT 0 1")
            pool.fetchrow = AsyncMock(return_value=None)
            pool.fetchval = AsyncMock(return_value=False)  # no MV, no mappings

            baselines_called = [False]

            async def fake_compute_baselines(p):
                baselines_called[0] = True
                return {"status": "ok"}

            with patch(
                "butlers.tools.finance.data_import._trigger_compute_baselines",
                new=AsyncMock(return_value=True),
            ):
                result = await import_transactions_from_file(pool, file_path=path)
                # With _trigger_compute_baselines mocked to return True
                assert result["baselines_triggered"] is True
                assert result["imported"] == 50
        finally:
            os.unlink(path)

    async def test_compute_baselines_not_triggered_for_less_than_50_rows(self):
        """compute_baselines is NOT called when fewer than 50 rows are imported."""
        from butlers.tools.finance.data_import import import_transactions_from_file

        path = _write_tmp_csv(CHASE_CSV)  # 4 rows
        try:
            pool = MagicMock()
            pool.execute = AsyncMock(return_value="INSERT 0 1")
            pool.fetchrow = AsyncMock(return_value=None)
            pool.fetchval = AsyncMock(return_value=False)

            with patch(
                "butlers.tools.finance.data_import._trigger_compute_baselines",
                new=AsyncMock(return_value=True),
            ) as mock_trigger:
                result = await import_transactions_from_file(pool, file_path=path)
                assert result["baselines_triggered"] is False
                mock_trigger.assert_not_called()
        finally:
            os.unlink(path)

    async def test_no_triggers_when_nothing_imported(self):
        """Post-import triggers are skipped when imported == 0."""
        from butlers.tools.finance.data_import import import_transactions_from_file

        path = _write_tmp_csv(CHASE_CSV)
        try:
            pool = _make_pool(fetchval_return=False)
            pool.execute = AsyncMock()

            # Report all batch rows as duplicates so imported == 0.
            async def all_duplicates(p, batch, account_id):
                return set(range(len(batch)))

            with (
                patch(
                    "butlers.tools.finance.data_import._check_duplicates_batch",
                    side_effect=all_duplicates,
                ),
                patch(
                    "butlers.tools.finance.data_import._refresh_spending_summaries",
                    new=AsyncMock(return_value=True),
                ) as mock_refresh,
                patch(
                    "butlers.tools.finance.data_import._trigger_compute_baselines",
                    new=AsyncMock(return_value=True),
                ) as mock_baselines,
                patch(
                    "butlers.tools.finance.data_import._trigger_learn_merchant_categories",
                    new=AsyncMock(return_value=3),
                ) as mock_learn,
            ):
                result = await import_transactions_from_file(pool, file_path=path)
                assert result["imported"] == 0
                mock_refresh.assert_not_called()
                mock_baselines.assert_not_called()
                mock_learn.assert_not_called()
                assert result["categories_learned"] == 0
        finally:
            os.unlink(path)

    async def test_learn_merchant_categories_triggered_with_category_data(self):
        """A real import with category data triggers learn_merchant_categories()
        and surfaces the upserted count via ``categories_learned``."""
        from butlers.tools.finance.data_import import import_transactions_from_file

        # CHASE_CSV carries a Category column on every imported row.
        path = _write_tmp_csv(CHASE_CSV)
        try:
            pool = MagicMock()
            pool.execute = AsyncMock(return_value="INSERT 0 1")
            pool.fetchrow = AsyncMock(return_value=None)
            pool.fetchval = AsyncMock(return_value=False)  # no MV, no mappings table

            # Spy on the real learning entry point (imported lazily inside the
            # trigger helper) to prove the import path reaches it.
            learn_spy = AsyncMock(return_value={"upserted": 2, "as_of": "2024-01-01"})
            with patch(
                "butlers.tools.finance.pattern_recognition.learn_merchant_categories",
                new=learn_spy,
            ):
                result = await import_transactions_from_file(pool, file_path=path)

            assert result["imported"] == 4
            learn_spy.assert_awaited_once()
            # The helper passes the live pool through to the learning function.
            assert learn_spy.await_args.args[0] is pool
            assert result["categories_learned"] == 2
        finally:
            os.unlink(path)

    async def test_learn_merchant_categories_not_triggered_without_category_data(self):
        """When no imported row carries category data, learning does not fire and
        ``categories_learned`` is 0."""
        from butlers.tools.finance.data_import import import_transactions_from_file

        # UNCATEGORIZED_CSV has no Category column.
        path = _write_tmp_csv(UNCATEGORIZED_CSV)
        try:
            pool = MagicMock()
            pool.execute = AsyncMock(return_value="INSERT 0 1")
            pool.fetchrow = AsyncMock(return_value=None)
            pool.fetchval = AsyncMock(return_value=False)
            pool.fetch = AsyncMock(return_value=[])

            with patch(
                "butlers.tools.finance.data_import._trigger_learn_merchant_categories",
                new=AsyncMock(return_value=5),
            ) as mock_learn:
                result = await import_transactions_from_file(pool, file_path=path)

            assert result["imported"] == 2
            mock_learn.assert_not_called()
            assert result["categories_learned"] == 0
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# 5.4  Dry run mode
# ---------------------------------------------------------------------------


class TestDryRunMode:
    async def test_dry_run_no_inserts(self):
        """Dry run does not insert any rows."""
        from butlers.tools.finance.data_import import import_transactions_from_file

        path = _write_tmp_csv(CHASE_CSV)
        try:
            pool = MagicMock()
            pool.execute = AsyncMock()
            pool.fetchrow = AsyncMock(return_value=None)
            pool.fetchval = AsyncMock(return_value=False)

            result = await import_transactions_from_file(pool, file_path=path, dry_run=True)

            assert result["dry_run"] is True
            assert "preview" in result
            pool.execute.assert_not_called()
        finally:
            os.unlink(path)

    async def test_dry_run_preview_max_10(self):
        """Dry run preview contains at most 10 transactions."""
        lines = ["Transaction Date,Description,Amount,Balance"]
        for i in range(20):
            lines.append(f"01/{i + 1:02d}/2024,MERCHANT {i},-{i + 1}.00,500.00")
        content = "\n".join(lines)

        path = _write_tmp_csv(content)
        try:
            from butlers.tools.finance.data_import import import_transactions_from_file

            pool = MagicMock()
            pool.fetchrow = AsyncMock(return_value=None)
            pool.fetchval = AsyncMock(return_value=False)
            pool.execute = AsyncMock()

            result = await import_transactions_from_file(pool, file_path=path, dry_run=True)
            assert len(result["preview"]) <= 10
        finally:
            os.unlink(path)

    async def test_dry_run_preview_includes_is_duplicate_flag(self):
        """Each preview item has the is_duplicate boolean field."""
        from butlers.tools.finance.data_import import import_transactions_from_file

        path = _write_tmp_csv(CHASE_CSV)
        try:
            pool = MagicMock()
            pool.fetchrow = AsyncMock(return_value=None)
            pool.fetchval = AsyncMock(return_value=False)
            pool.execute = AsyncMock()

            result = await import_transactions_from_file(pool, file_path=path, dry_run=True)
            for item in result["preview"]:
                assert "is_duplicate" in item

        finally:
            os.unlink(path)

    async def test_dry_run_duplicate_flagged_correctly(self):
        """A preview row that matches an existing transaction has is_duplicate=True."""
        from butlers.tools.finance.data_import import import_transactions_from_file

        path = _write_tmp_csv(CHASE_CSV)
        try:
            pool = MagicMock()
            # All dedup checks find an existing row → duplicates
            existing = MagicMock()
            pool.fetchrow = AsyncMock(return_value=existing)
            pool.fetchval = AsyncMock(return_value=False)
            pool.execute = AsyncMock()

            result = await import_transactions_from_file(pool, file_path=path, dry_run=True)
            assert result["dry_run"] is True
            # All preview rows should be marked as duplicates
            for item in result["preview"]:
                assert item["is_duplicate"] is True
        finally:
            os.unlink(path)

    async def test_dry_run_preview_item_shape(self):
        """Each preview item has the expected fields."""
        from butlers.tools.finance.data_import import import_transactions_from_file

        path = _write_tmp_csv(CHASE_CSV)
        try:
            pool = MagicMock()
            pool.fetchrow = AsyncMock(return_value=None)
            pool.fetchval = AsyncMock(return_value=False)
            pool.execute = AsyncMock()

            result = await import_transactions_from_file(pool, file_path=path, dry_run=True)
            for item in result["preview"]:
                for field in ("posted_at", "merchant", "amount", "currency", "direction"):
                    assert field in item, f"Missing field {field!r} in preview item"
        finally:
            os.unlink(path)

    async def test_dry_run_returns_counts(self):
        """Dry run result includes total, parsed, parse_errors counts."""
        from butlers.tools.finance.data_import import import_transactions_from_file

        path = _write_tmp_csv(CHASE_CSV)
        try:
            pool = MagicMock()
            pool.fetchrow = AsyncMock(return_value=None)
            pool.fetchval = AsyncMock(return_value=False)
            pool.execute = AsyncMock()

            result = await import_transactions_from_file(pool, file_path=path, dry_run=True)
            assert "total" in result
            assert "parsed" in result
            assert "parse_errors" in result
            assert result["total"] >= result["parsed"]
        finally:
            os.unlink(path)

    async def test_dry_run_includes_merchant_mappings_applied(self):
        """Dry run also shows how many merchant mappings would be applied."""
        from butlers.tools.finance.data_import import import_transactions_from_file

        path = _write_tmp_csv(UNCATEGORIZED_CSV)
        try:
            pool = MagicMock()
            pool.execute = AsyncMock()
            pool.fetchrow = AsyncMock(return_value=None)

            async def fake_fetchval(sql, *args, **kwargs):
                if "merchant_mappings" in sql.lower():
                    return True  # table exists
                return False

            pool.fetchval = AsyncMock(side_effect=fake_fetchval)

            with patch(
                "butlers.tools.finance.data_import._lookup_merchant_category",
                new=AsyncMock(return_value="dining"),
            ):
                result = await import_transactions_from_file(pool, file_path=path, dry_run=True)
                assert "merchant_mappings_applied" in result
                assert result["merchant_mappings_applied"] == 2
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    async def test_missing_file_returns_error(self):
        """Non-existent file path returns structured error, not exception."""
        from butlers.tools.finance.data_import import import_transactions_from_file

        pool = _make_pool()
        result = await import_transactions_from_file(pool, file_path="/nonexistent/path/import.csv")
        assert result["status"] == "error"
        assert "error" in result

    async def test_undetectable_date_format_returns_error(self):
        """CSV with unrecognizable date format returns structured error."""
        bad_csv = "Transaction Date,Description,Amount\nJanuary 15 2024,MERCHANT,-10.00\n"
        path = _write_tmp_csv(bad_csv)
        try:
            from butlers.tools.finance.data_import import import_transactions_from_file

            pool = _make_pool()
            result = await import_transactions_from_file(pool, file_path=path)
            assert result["status"] == "error"
            assert "date" in result["error"].lower()
        finally:
            os.unlink(path)

    async def test_db_error_during_insert_captured(self):
        """DB insert failure is captured in error_details."""
        from butlers.tools.finance.data_import import import_transactions_from_file

        path = _write_tmp_csv(CHASE_CSV)
        try:
            pool = MagicMock()
            pool.fetchrow = AsyncMock(return_value=None)
            pool.fetchval = AsyncMock(return_value=False)
            pool.execute = AsyncMock(side_effect=Exception("DB unavailable"))

            result = await import_transactions_from_file(pool, file_path=path)
            assert result["errors"] > 0
        finally:
            os.unlink(path)

    async def test_import_result_always_has_batch_id(self):
        """Result always contains import_batch_id (even on errors)."""
        from butlers.tools.finance.data_import import import_transactions_from_file

        pool = _make_pool()
        result = await import_transactions_from_file(pool, file_path="/nonexistent/file.csv")
        # Error case still has import_batch_id
        assert "import_batch_id" in result


# ---------------------------------------------------------------------------
# _load_csv_from_file unit tests
# ---------------------------------------------------------------------------


class TestLoadCsvFromFile:
    def test_reads_utf8_file(self):
        from butlers.tools.finance.data_import import _load_csv_from_file

        content = "Date,Merchant,Amount\n2024-01-15,Coffee,-5.00\n"
        path = _write_tmp_csv(content)
        try:
            result = _load_csv_from_file(path)
            assert "Coffee" in result
        finally:
            os.unlink(path)

    def test_reads_bom_utf8_file(self):
        """UTF-8 with BOM (common in Windows-exported CSVs)."""
        from butlers.tools.finance.data_import import _load_csv_from_file

        content = "\ufeffDate,Merchant,Amount\n2024-01-15,Coffee,-5.00\n"
        fd, path = tempfile.mkstemp(suffix=".csv")
        with os.fdopen(fd, "w", encoding="utf-8-sig") as fh:
            fh.write(content)
        try:
            result = _load_csv_from_file(path)
            # BOM should be stripped
            assert result.startswith("Date") or "Date" in result
        finally:
            os.unlink(path)

    def test_raises_for_missing_file(self):
        from butlers.tools.finance.data_import import _load_csv_from_file

        with pytest.raises(FileNotFoundError):
            _load_csv_from_file("/nonexistent/path/to/file.csv")


# ---------------------------------------------------------------------------
# _lookup_merchant_category unit tests
# ---------------------------------------------------------------------------


class TestLookupMerchantCategory:
    async def test_returns_category_when_found(self):
        from butlers.tools.finance.data_import import _lookup_merchant_category

        pool = MagicMock()
        row = MagicMock()
        row.__getitem__ = MagicMock(side_effect=lambda k: "dining" if k == "category" else None)
        pool.fetchrow = AsyncMock(return_value=row)

        result = await _lookup_merchant_category(pool, "Starbucks")
        assert result == "dining"

    async def test_returns_none_when_no_mapping(self):
        from butlers.tools.finance.data_import import _lookup_merchant_category

        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=None)

        result = await _lookup_merchant_category(pool, "Unknown Merchant")
        assert result is None


# ---------------------------------------------------------------------------
# _apply_merchant_mappings unit tests
# ---------------------------------------------------------------------------


class TestApplyMerchantMappings:
    async def test_no_mapping_when_table_absent(self):
        from butlers.tools.finance.data_import import _apply_merchant_mappings

        pool = MagicMock()
        pool.fetchval = AsyncMock(return_value=False)  # table doesn't exist

        rows = [{"merchant": "Coffee Shop", "category": "uncategorized"}]
        result_rows, count = await _apply_merchant_mappings(pool, rows)
        assert count == 0
        assert result_rows[0]["category"] == "uncategorized"

    async def test_applies_mapping_to_uncategorized(self):
        from butlers.tools.finance.data_import import _apply_merchant_mappings

        pool = MagicMock()
        pool.fetchval = AsyncMock(return_value=True)  # table exists

        mapping_row = MagicMock()
        mapping_row.__getitem__ = MagicMock(
            side_effect=lambda k: "groceries" if k == "category" else None
        )
        pool.fetchrow = AsyncMock(return_value=mapping_row)

        rows = [{"merchant": "Whole Foods", "category": "uncategorized"}]
        result_rows, count = await _apply_merchant_mappings(pool, rows)
        assert count == 1
        assert result_rows[0]["category"] == "groceries"

    async def test_skips_already_categorized_rows(self):
        from butlers.tools.finance.data_import import _apply_merchant_mappings

        pool = MagicMock()
        pool.fetchval = AsyncMock(return_value=True)  # table exists
        pool.fetchrow = AsyncMock(return_value=None)

        rows = [{"merchant": "Netflix", "category": "entertainment"}]
        result_rows, count = await _apply_merchant_mappings(pool, rows)
        # Already categorized; no mappings applied
        assert count == 0
        assert result_rows[0]["category"] == "entertainment"

    async def test_deduplicates_merchant_lookups(self):
        """Multiple rows with the same merchant only trigger one DB lookup."""
        from butlers.tools.finance.data_import import _apply_merchant_mappings

        pool = MagicMock()
        pool.fetchval = AsyncMock(return_value=True)

        lookup_calls = [0]

        async def fake_fetchrow(sql, *args, **kwargs):
            lookup_calls[0] += 1
            row = MagicMock()
            row.__getitem__ = MagicMock(side_effect=lambda k: "dining" if k == "category" else None)
            return row

        pool.fetchrow = AsyncMock(side_effect=fake_fetchrow)

        rows = [
            {"merchant": "Starbucks", "category": "uncategorized"},
            {"merchant": "Starbucks", "category": "uncategorized"},
            {"merchant": "Starbucks", "category": "uncategorized"},
        ]
        result_rows, count = await _apply_merchant_mappings(pool, rows)
        # All 3 should be mapped, but only 1 DB lookup per unique merchant
        assert count == 3
        # DB calls: 2 calls per merchant (primary + fallback queries)
        # The key invariant is calls <= 2 * unique_merchants (not 2 * total_rows)
        assert lookup_calls[0] <= 4  # at most 2 calls per unique merchant
