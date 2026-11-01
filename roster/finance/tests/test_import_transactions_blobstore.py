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
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp import FastMCP

from butlers.testing.mcp import registered_tools

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

    async def _register_with_data_import(self, blob_store=None, import_fn=None):
        """Helper: register tools with a fake data_import module injected into sys.modules."""
        import sys

        from butlers.modules._roster_finance.tools import register_tools

        mcp = FastMCP()
        module = _FakeModule(blob_store=blob_store)
        fake_data_import = _make_fake_data_import(import_fn)

        # register_tools does ``from butlers.tools.finance import data_import``, which reads the
        # package attribute, so the fake goes on the package as well as into sys.modules.
        import butlers.tools.finance as finance_tools

        data_import_key = "butlers.tools.finance.data_import"
        original = sys.modules.get(data_import_key, None)
        original_attr = finance_tools.data_import
        sys.modules[data_import_key] = fake_data_import
        finance_tools.data_import = fake_data_import
        try:
            register_tools(mcp, module)
        finally:
            finance_tools.data_import = original_attr
            if original is None:
                sys.modules.pop(data_import_key, None)
            else:
                sys.modules[data_import_key] = original

        return await registered_tools(mcp), module, fake_data_import

    async def test_import_transactions_tool_uses_storage_ref_not_file_path(self):
        """import_transactions MCP tool parameter is storage_ref, not file_path."""
        tools, _, _ = await self._register_with_data_import()

        assert "import_transactions" in tools, (
            "import_transactions tool was not registered — "
            "check that the fake data_import module is injected correctly"
        )

        sig = inspect.signature(tools["import_transactions"])
        params = list(sig.parameters.keys())

        assert "storage_ref" in params, f"Expected 'storage_ref' parameter, got: {params}"
        assert "file_path" not in params, (
            f"Old 'file_path' parameter should be removed, found it in: {params}"
        )

    async def test_import_transactions_storage_ref_is_required(self):
        """storage_ref is a required (no default) parameter."""
        tools, _, _ = await self._register_with_data_import()

        sig = inspect.signature(tools["import_transactions"])
        param = sig.parameters["storage_ref"]
        assert param.default is inspect.Parameter.empty

    async def test_import_transactions_returns_error_when_blob_store_none(self):
        """Returns a structured error dict when blob_store is not configured."""
        tools, _, _ = await self._register_with_data_import(blob_store=None)

        result = await tools["import_transactions"](storage_ref="s3://bucket/file.csv")

        assert result["status"] == "blob_store_not_configured"
        assert "error" in result
        assert "Blob storage is not configured" in result["error"]

    async def test_import_transactions_passes_blob_store_to_impl(self):
        """When blob_store is configured, passes it to data_import.import_transactions."""
        fake_blob = MagicMock()
        import_fn = AsyncMock(return_value={"total": 5, "imported": 5, "skipped": 0})
        tools, _, fake_data_import = await self._register_with_data_import(
            blob_store=fake_blob, import_fn=import_fn
        )

        await tools["import_transactions"](storage_ref="s3://bucket/my-export.csv")

        fake_data_import.import_transactions.assert_awaited_once()
        call_kwargs = fake_data_import.import_transactions.call_args.kwargs
        assert call_kwargs.get("blob_store") is fake_blob
        assert call_kwargs.get("storage_ref") == "s3://bucket/my-export.csv"

    async def test_import_transactions_passes_optional_params_to_impl(self):
        """Optional parameters (account_id, currency, dry_run) are forwarded."""
        fake_blob = MagicMock()
        import_fn = AsyncMock(return_value={"total": 3, "imported": 3})
        tools, _, fake_data_import = await self._register_with_data_import(
            blob_store=fake_blob, import_fn=import_fn
        )

        await tools["import_transactions"](
            storage_ref="s3://bucket/chase-export.csv",
            account_id="acct-123",
            currency="EUR",
            dry_run=True,
        )

        call_kwargs = fake_data_import.import_transactions.call_args.kwargs
        assert call_kwargs.get("account_id") == "acct-123"
        assert call_kwargs.get("currency") == "EUR"
        assert call_kwargs.get("dry_run") is True

    async def test_import_transactions_column_map_parsed_from_json(self):
        """column_map JSON string is parsed to dict before forwarding."""
        import json

        fake_blob = MagicMock()
        import_fn = AsyncMock(return_value={"total": 1, "imported": 1})
        tools, _, fake_data_import = await self._register_with_data_import(
            blob_store=fake_blob, import_fn=import_fn
        )

        col_map = json.dumps({"date": "Date", "merchant": "Description", "amount": "Amount"})
        await tools["import_transactions"](
            storage_ref="s3://bucket/generic.csv",
            column_map=col_map,
        )

        call_kwargs = fake_data_import.import_transactions.call_args.kwargs
        assert isinstance(call_kwargs.get("column_map"), dict)
        assert call_kwargs["column_map"]["date"] == "Date"

    async def test_import_transactions_registered_when_data_import_module_present(self):
        """import_transactions IS registered when data_import module is importable.

        The data_import module has been implemented (bu-w5dv); this test verifies
        that the registration path works end-to-end when the module is present.
        """
        tools, _, _ = await self._register_with_data_import(blob_store=MagicMock())
        assert "import_transactions" in tools


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
