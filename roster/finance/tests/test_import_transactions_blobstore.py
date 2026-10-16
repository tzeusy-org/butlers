"""Unit tests for import_transactions tool — storage_ref / BlobStore API.

Verifies that:
- The import_transactions MCP tool accepts `storage_ref` (not `file_path`).
- The tool returns a structured error when blob_store is not configured.
- The tool passes `blob_store` and `storage_ref` to the underlying
  `data_import.import_transactions` implementation when both are present.
- The FinanceModule stores the blob_store provided at on_startup.

Issue: bu-44my
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# FinanceModule lifecycle tests
# ---------------------------------------------------------------------------


class TestFinanceModuleLifecycle:
    """Tests for FinanceModule blob_store wiring."""

    async def test_blob_store_none_by_default(self):
        """FinanceModule.blob_store is None before on_startup is called."""
        from butlers.modules._roster_finance import FinanceModule

        mod = FinanceModule()
        assert mod.blob_store is None

    async def test_blob_store_stored_in_on_startup(self):
        """on_startup stores the provided blob_store on the module."""
        from butlers.modules._roster_finance import FinanceModule

        mod = FinanceModule()
        fake_blob_store = MagicMock()
        fake_db = MagicMock()
        fake_db.pool = MagicMock()

        await mod.on_startup(config=None, db=fake_db, blob_store=fake_blob_store)

        assert mod.blob_store is fake_blob_store

    async def test_blob_store_none_when_not_provided(self):
        """on_startup without blob_store leaves blob_store as None."""
        from butlers.modules._roster_finance import FinanceModule

        mod = FinanceModule()
        fake_db = MagicMock()
        fake_db.pool = MagicMock()

        await mod.on_startup(config=None, db=fake_db)

        assert mod.blob_store is None

    async def test_blob_store_cleared_on_shutdown(self):
        """on_shutdown clears the blob_store reference."""
        from butlers.modules._roster_finance import FinanceModule

        mod = FinanceModule()
        fake_blob_store = MagicMock()
        fake_db = MagicMock()
        fake_db.pool = MagicMock()

        await mod.on_startup(config=None, db=fake_db, blob_store=fake_blob_store)
        assert mod.blob_store is not None

        await mod.on_shutdown()
        assert mod.blob_store is None

    async def test_on_startup_accepts_blob_store_kwarg(self):
        """on_startup signature accepts blob_store as a keyword argument."""
        from butlers.modules._roster_finance import FinanceModule

        sig = inspect.signature(FinanceModule.on_startup)
        assert "blob_store" in sig.parameters
        param = sig.parameters["blob_store"]
        assert param.default is None


# ---------------------------------------------------------------------------
# import_transactions MCP tool registration tests
# ---------------------------------------------------------------------------


class _FakeMCP:
    """Minimal MCP mock that collects registered tool callables."""

    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self):
        """Return a decorator that registers the wrapped function."""

        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


class _FakeModule:
    """Minimal FinanceModule stand-in for tools.register_tools."""

    def __init__(self, *, pool=None, blob_store=None) -> None:
        self._pool = pool
        self.blob_store = blob_store
        self.notify_fn = None  # matches FinanceModule.notify_fn

    def _get_pool(self):
        return self._pool


def _make_fake_data_import(import_fn=None) -> MagicMock:
    """Return a mock data_import module with an import_transactions callable."""
    mod = MagicMock()
    mod.import_transactions = import_fn or AsyncMock(return_value={"imported": 0})
    return mod


class TestImportTransactionsToolRegistration:
    """Tests for the import_transactions MCP tool registration."""

    def _register_with_data_import(self, blob_store=None, import_fn=None):
        """Helper: register tools with a fake data_import module injected into sys.modules."""
        import sys

        from butlers.modules._roster_finance.tools import register_tools

        mcp = _FakeMCP()
        module = _FakeModule(blob_store=blob_store)
        fake_data_import = _make_fake_data_import(import_fn)

        # Inject the fake data_import into sys.modules so _try_import finds it.
        data_import_key = "butlers.tools.finance.data_import"
        original = sys.modules.get(data_import_key, None)
        sys.modules[data_import_key] = fake_data_import
        try:
            register_tools(mcp, module)
        finally:
            if original is None:
                sys.modules.pop(data_import_key, None)
            else:
                sys.modules[data_import_key] = original

        return mcp, module, fake_data_import

    def test_import_transactions_tool_uses_storage_ref_not_file_path(self):
        """import_transactions MCP tool parameter is storage_ref, not file_path."""
        mcp, _, _ = self._register_with_data_import()

        assert "import_transactions" in mcp.tools, (
            "import_transactions tool was not registered — "
            "check that the fake data_import module is injected correctly"
        )

        assert "import_transactions_from_file" not in mcp.tools
        sig = inspect.signature(mcp.tools["import_transactions"])
        params = list(sig.parameters.keys())

        assert "storage_ref" in params, f"Expected 'storage_ref' parameter, got: {params}"
        assert "file_path" not in params, (
            f"Old 'file_path' parameter should be removed, found it in: {params}"
        )

    def test_import_transactions_storage_ref_is_required(self):
        """storage_ref is a required (no default) parameter."""
        mcp, _, _ = self._register_with_data_import()

        sig = inspect.signature(mcp.tools["import_transactions"])
        param = sig.parameters["storage_ref"]
        assert param.default is inspect.Parameter.empty

    async def test_import_transactions_returns_error_when_blob_store_none(self):
        """Returns a structured error dict when blob_store is not configured."""
        mcp, _, _ = self._register_with_data_import(blob_store=None)

        result = await mcp.tools["import_transactions"](storage_ref="s3://bucket/file.csv")

        assert result["status"] == "blob_store_not_configured"
        assert "error" in result
        assert "Blob storage is not configured" in result["error"]

    async def test_import_transactions_passes_blob_store_to_impl(self):
        """When blob_store is configured, passes it to data_import.import_transactions."""
        fake_blob = MagicMock()
        import_fn = AsyncMock(return_value={"total": 5, "imported": 5, "skipped": 0})
        mcp, _, fake_data_import = self._register_with_data_import(
            blob_store=fake_blob, import_fn=import_fn
        )

        await mcp.tools["import_transactions"](storage_ref="s3://bucket/my-export.csv")

        fake_data_import.import_transactions.assert_awaited_once()
        call_kwargs = fake_data_import.import_transactions.call_args.kwargs
        assert call_kwargs.get("blob_store") is fake_blob
        assert call_kwargs.get("storage_ref") == "s3://bucket/my-export.csv"

    async def test_import_transactions_passes_optional_params_to_impl(self):
        """Optional parameters (account_id, currency, dry_run) are forwarded."""
        fake_blob = MagicMock()
        import_fn = AsyncMock(return_value={"total": 3, "imported": 3})
        mcp, _, fake_data_import = self._register_with_data_import(
            blob_store=fake_blob, import_fn=import_fn
        )

        await mcp.tools["import_transactions"](
            storage_ref="s3://bucket/chase-export.csv",
            account_id="acct-123",
            currency="EUR",
            dry_run=True,
            delimiter=";",
            date_format="%d/%m/%Y",
            amount_sign="positive_debit",
        )

        call_kwargs = fake_data_import.import_transactions.call_args.kwargs
        assert call_kwargs.get("account_id") == "acct-123"
        assert call_kwargs.get("currency") == "EUR"
        assert call_kwargs.get("dry_run") is True
        assert call_kwargs.get("delimiter") == ";"
        assert call_kwargs.get("date_format") == "%d/%m/%Y"
        assert call_kwargs.get("amount_sign") == "positive_debit"

    async def test_import_transactions_column_map_parsed_from_json(self):
        """column_map JSON string is parsed to dict before forwarding."""
        import json

        fake_blob = MagicMock()
        import_fn = AsyncMock(return_value={"total": 1, "imported": 1})
        mcp, _, fake_data_import = self._register_with_data_import(
            blob_store=fake_blob, import_fn=import_fn
        )

        col_map = json.dumps({"date": "Date", "merchant": "Description", "amount": "Amount"})
        await mcp.tools["import_transactions"](
            storage_ref="s3://bucket/generic.csv",
            column_map=col_map,
        )

        call_kwargs = fake_data_import.import_transactions.call_args.kwargs
        assert isinstance(call_kwargs.get("column_map"), dict)
        assert call_kwargs["column_map"]["date"] == "Date"

    def test_import_transactions_registered_when_data_import_module_present(self):
        """import_transactions IS registered when data_import module is importable.

        The data_import module has been implemented (bu-w5dv); this test verifies
        that the registration path works end-to-end when the module is present.
        """
        mcp, _, _ = self._register_with_data_import(blob_store=MagicMock())
        assert "import_transactions" in mcp.tools


# ---------------------------------------------------------------------------
# Module base class signature test
# ---------------------------------------------------------------------------


class TestModuleBaseSignature:
    """Verify the base Module.on_startup signature includes blob_store."""

    def test_base_on_startup_has_blob_store_param(self):
        """Module.on_startup abstract method signature includes blob_store."""
        from butlers.modules.base import Module

        sig = inspect.signature(Module.on_startup)
        assert "blob_store" in sig.parameters
        param = sig.parameters["blob_store"]
        assert param.default is None


class TestImportParsingOptions:
    async def test_semicolon_day_first_positive_debit_import(self):
        from datetime import UTC, datetime
        from decimal import Decimal

        from butlers.tools.finance.data_import import import_transactions

        blob = MagicMock()
        blob.get = AsyncMock(
            return_value=(
                "\ufeffDate;Merchant;Amount\n04/03/2024;Café;12.50\n05/03/2024;Refund;-2.00\n".encode(
                    "utf-8"
                )
            )
        )
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        pool.fetchval = AsyncMock(return_value=False)
        pool.execute = AsyncMock()
        result = await import_transactions(
            pool,
            blob,
            "s3://bucket/statement.csv",
            currency="SGD",
            delimiter=";",
            date_format="%d/%m/%Y",
            amount_sign="positive_debit",
        )
        assert (result["imported"], result["errors"]) == (2, 0)
        calls = pool.execute.call_args_list
        assert calls[0].args[2] == datetime(2024, 3, 4, tzinfo=UTC)
        assert calls[0].args[3] == "Café"
        assert calls[0].args[5:8] == (Decimal("12.50"), "SGD", "debit")
        assert calls[1].args[5:8] == (Decimal("2.00"), "SGD", "credit")

    async def test_invalid_options_rejected_before_blob_read(self):
        from butlers.tools.finance.data_import import import_transactions

        blob = MagicMock()
        blob.get = AsyncMock()
        for options in (
            {"delimiter": "ab"},
            {"delimiter": ""},
            {"delimiter": 1},
            {"amount_sign": "unknown"},
            {"date_format": "%Q"},
            {"date_format": "%Y/%Y"},
            {"date_format": ""},
            {"date_format": 1},
            {"amount_sign": "split_cols", "column_map": {"debit": "Paid"}},
        ):
            pool = MagicMock()
            result = await import_transactions(pool, blob, "ref", **options)
            assert pool.mock_calls == []
            assert result["status"] == "validation_error", options
            assert result["error"]
        blob.get.assert_not_awaited()

    async def test_split_column_overrides_through_blob_import(self):
        from butlers.tools.finance.data_import import import_transactions

        blob = MagicMock()
        blob.get = AsyncMock(
            return_value=(b"When|Payee|Paid|Received\n2024-01-15|Cafe|5|\n2024-01-16|Refund||2\n")
        )
        pool = MagicMock()
        pool.fetchval = AsyncMock(return_value=False)
        result = await import_transactions(
            pool,
            blob,
            "ref",
            dry_run=True,
            delimiter="|",
            amount_sign="split_cols",
            column_map={"date": "When", "merchant": "Payee", "debit": "Paid", "credit": "Received"},
        )
        assert result["parsed"] == 2
        assert [row["merchant"] for row in result["preview"]] == ["Cafe", "Refund"]
        assert [row["direction"] for row in result["preview"]] == ["debit", "credit"]
        assert [row["amount"] for row in result["preview"]] == ["5", "2"]

        # A signed override must restore Amount even when split headers coexist.
        blob.get = AsyncMock(
            return_value=(b"Date,Merchant,Amount,Debit,Credit\n2024-01-15,Cafe,5,5,\n")
        )
        for convention, direction in (("positive_debit", "debit"), ("negative_debit", "credit")):
            result = await import_transactions(
                pool,
                blob,
                "ref",
                dry_run=True,
                amount_sign=convention,
            )
            assert (result["total"], result["parsed"], result["parse_errors"]) == (1, 1, 0)
            assert result["preview"][0]["direction"] == direction
            assert result["preview"][0]["amount"] == "5"
        # When the override has no signed column, expose the mapping problem.
        blob.get = AsyncMock(return_value=b"Date,Merchant,Debit,Credit\n2024-01-15,Cafe,5,\n")
        result = await import_transactions(
            pool, blob, "ref", dry_run=True, amount_sign="positive_debit"
        )
        assert result["parse_errors"] == 1
        assert "column_map" in result["error_details"][0]["reason"]


class TestImportOutputBounds:
    async def test_error_samples_and_blob_failures_have_finite_output(self):
        import json

        from butlers.tools.finance.data_import import import_transactions

        # Reviewer's 200KB single-row counterexample, plus repeated errors and
        # characters that expand under JSON escaping, all at the public boundary.
        cell = '"\\\u0001é' * 2500
        headers = ["Date", "Merchant", "Amount", *("Extra" + str(i) + cell for i in range(20))]
        import csv
        import io

        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(headers)
        for _ in range(60):
            writer.writerow(["not-a-date" + cell, "Cafe", "5", *([cell] * 20)])
        blob = MagicMock()
        blob.get = AsyncMock(return_value=stream.getvalue().encode())
        pool = MagicMock()
        pool.fetchval = AsyncMock(return_value=False)
        for dry_run, date_format in ((True, "%Y-%m-%d"), (False, "%Y-%m-%d"), (True, None)):
            result = await import_transactions(
                pool,
                blob,
                "ref",
                dry_run=dry_run,
                date_format=date_format,
            )
            assert len(json.dumps(result).encode()) <= 32768
            assert result["output_truncated"] is True
            assert result["truncation"]["strings_truncated"] > 0
            if date_format:
                assert result.get("parse_errors", result.get("errors")) == 60
                assert result["truncation"]["error_details_omitted"] > 0
                assert result["error_details"]
                assert len(json.dumps(result["error_details"]).encode()) <= 8192
                for error in result["error_details"]:
                    assert len(json.dumps(error).encode()) <= 2048
                    assert len(error.get("raw", {})) <= 8
                    assert all(len(key) <= 64 for key in error.get("raw", {}))
                    assert len(error["reason"]) <= 256
                    assert error["truncated"] is True
            else:
                assert result["status"] == "error"
                assert result["date_samples"]
                assert all(len(sample) <= 256 for sample in result["date_samples"])
        blob.get = AsyncMock(side_effect=RuntimeError(cell))
        result = await import_transactions(pool, blob, cell)
        assert len(json.dumps(result).encode()) <= 32768
        assert len(result["error"]) <= 256
        assert len(result["storage_ref"]) <= 256
        assert result["output_truncated"] is True

    async def test_preview_and_summary_limits_do_not_change_imported_cells(self):
        import csv
        import io
        import json

        from butlers.tools.finance.data_import import import_transactions

        merchant = "merchant" + 'é"\\' * 1000
        category = "category" + 'é"\\' * 500
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(["Date", "Merchant", "Amount", "Category", "Description"])
        for i in range(60):
            writer.writerow(["2024-01-15", merchant, "5", category + str(i), merchant + " detail"])
        blob = MagicMock()
        blob.get = AsyncMock(return_value=stream.getvalue().encode())
        pool = MagicMock()
        pool.fetchval = AsyncMock(return_value=False)
        pool.fetch = AsyncMock(return_value=[])
        pool.execute = AsyncMock()
        for dry_run in (True, False):
            result = await import_transactions(
                pool,
                blob,
                "ref",
                dry_run=dry_run,
                column_map={
                    "merchant": "Merchant",
                    "category": "Category",
                    "description": "Description",
                },
            )
            assert len(json.dumps(result).encode()) <= 32768
            assert result["output_truncated"] is True
            assert result["truncation"]["strings_truncated"] > 0
            if dry_run:
                assert result["parsed"] == 60
                assert 0 < len(result["preview"]) <= 10
                assert len(json.dumps(result["preview"]).encode()) <= 8192
                assert all(len(json.dumps(row).encode()) <= 2048 for row in result["preview"])
                assert result["truncation"]["preview_rows_omitted"] >= 50
                assert all(len(row["merchant"]) <= 256 for row in result["preview"])
                assert all(row["truncated"] for row in result["preview"])
                assert all(row["is_duplicate"] is False for row in result["preview"])
                assert all(
                    set(("posted_at", "amount", "currency", "direction", "category")) <= row.keys()
                    for row in result["preview"]
                )
                pool.execute.assert_not_awaited()
            else:
                assert result["imported"] == 60
                assert len(result["categories_used"]) <= 50
                assert result["truncation"]["categories_used_omitted"] > 0
        insert_calls = [
            call
            for call in pool.execute.call_args_list
            if "INSERT INTO transactions" in call.args[0]
        ]
        assert len(insert_calls) == 60
        assert all(call.args[3] == merchant.title() for call in insert_calls)
        assert all(call.args[4] == merchant + " detail" for call in insert_calls)
        assert all(call.args[9]["raw_merchant"] == merchant for call in insert_calls)
        assert all(call.args[8].startswith(category) for call in insert_calls)
