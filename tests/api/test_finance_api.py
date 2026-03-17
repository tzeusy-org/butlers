"""Tests for the finance butler API endpoints.

Covers:
- GET /api/finance/transactions — overlay fields (normalized_merchant, inferred_category)
  surfaced from the metadata JSONB column.
- GET /api/finance/spending-summary — COALESCE group-by dimensions for category and
  merchant, matching the spending_summary_facts() behaviour.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(row: dict) -> MagicMock:
    """Build a MagicMock that behaves like an asyncpg Record for the given dict."""
    m = MagicMock()
    m.__getitem__ = MagicMock(side_effect=lambda key: row[key])
    return m


def _make_transaction_row(
    *,
    id=None,
    posted_at=None,
    merchant="ACME Corp",
    description=None,
    amount="42.00",
    currency="USD",
    direction="debit",
    category="shopping",
    payment_method=None,
    account_id=None,
    receipt_url=None,
    external_ref=None,
    source_message_id=None,
    metadata=None,
    created_at=None,
    updated_at=None,
) -> dict:
    """Build a dict mimicking an asyncpg Record for the finance.transactions table."""
    now = datetime.now(tz=UTC)
    return {
        "id": id or uuid4(),
        "posted_at": posted_at or now,
        "merchant": merchant,
        "description": description,
        "amount": Decimal(amount),
        "currency": currency,
        "direction": direction,
        "category": category,
        "payment_method": payment_method,
        "account_id": account_id,
        "receipt_url": receipt_url,
        "external_ref": external_ref,
        "source_message_id": source_message_id,
        "metadata": metadata if metadata is not None else {},
        "created_at": created_at or now,
        "updated_at": updated_at or now,
    }


def _build_finance_app(rows: list[dict], *, total: int | None = None):
    """Wire a FastAPI app with a mocked pool for the finance transactions endpoint.

    Returns (app, mock_pool, mock_db).
    """
    if total is None:
        total = len(rows)

    mock_pool = AsyncMock()
    mock_pool.fetchval = AsyncMock(return_value=total)
    mock_pool.fetch = AsyncMock(return_value=[_make_record(r) for r in rows])

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    # Import the finance router module's _get_db_manager stub so we can override it.
    import importlib.util
    import sys
    from pathlib import Path

    router_path = Path(__file__).parents[2] / "roster" / "finance" / "api" / "router.py"
    module_name = "finance_api_router"
    if module_name not in sys.modules:
        spec = importlib.util.spec_from_file_location(module_name, router_path)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod
        spec.loader.exec_module(mod)
    finance_router_mod = sys.modules[module_name]

    app = create_app()
    app.dependency_overrides[finance_router_mod._get_db_manager] = lambda: mock_db

    return app, mock_pool, mock_db


def _build_finance_app_with_spending(
    total_row: dict, group_rows: list[dict], *, currency: str = "USD"
):
    """Wire a FastAPI app with a mocked pool for the spending-summary endpoint.

    The mock handles fetchrow (for total + currency) and fetch (for group rows).
    Returns (app, mock_pool, mock_db).
    """
    import importlib.util
    import sys
    from pathlib import Path

    router_path = Path(__file__).parents[2] / "roster" / "finance" / "api" / "router.py"
    module_name = "finance_api_router"
    if module_name not in sys.modules:
        spec = importlib.util.spec_from_file_location(module_name, router_path)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod
        spec.loader.exec_module(mod)
    finance_router_mod = sys.modules[module_name]

    mock_pool = AsyncMock()

    async def _fetchrow(sql, *args):
        if "MAX(currency)" in sql or "COALESCE(SUM" in sql:
            return _make_record(total_row)
        # currency representative query
        return _make_record({"currency": currency, "cnt": 10})

    mock_pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    mock_pool.fetch = AsyncMock(return_value=[_make_record(r) for r in group_rows])

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    app.dependency_overrides[finance_router_mod._get_db_manager] = lambda: mock_db

    return app, mock_pool, mock_db


# ---------------------------------------------------------------------------
# Tests: GET /api/finance/transactions — overlay fields
# ---------------------------------------------------------------------------


class TestTransactionsOverlayFields:
    """Verify normalized_merchant and inferred_category are surfaced from metadata."""

    async def test_overlay_fields_absent_when_metadata_empty(self):
        """Without overlay metadata, both fields should be None."""
        rows = [_make_transaction_row(metadata={})]
        app, _, _ = _build_finance_app(rows)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/finance/transactions")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["normalized_merchant"] is None
        assert data[0]["inferred_category"] is None

    async def test_normalized_merchant_surfaced_from_metadata(self):
        """normalized_merchant from metadata JSONB is surfaced in the response."""
        rows = [
            _make_transaction_row(
                merchant="AMZN Mktp US*1A2B3C",
                metadata={"normalized_merchant": "Amazon"},
            )
        ]
        app, _, _ = _build_finance_app(rows)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/finance/transactions")

        assert resp.status_code == 200
        item = resp.json()["data"][0]
        assert item["merchant"] == "AMZN Mktp US*1A2B3C"
        assert item["normalized_merchant"] == "Amazon"

    async def test_inferred_category_surfaced_from_metadata(self):
        """inferred_category from metadata JSONB is surfaced in the response."""
        rows = [
            _make_transaction_row(
                category="shopping",
                metadata={"inferred_category": "electronics"},
            )
        ]
        app, _, _ = _build_finance_app(rows)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/finance/transactions")

        assert resp.status_code == 200
        item = resp.json()["data"][0]
        assert item["category"] == "shopping"
        assert item["inferred_category"] == "electronics"

    async def test_both_overlay_fields_surfaced_together(self):
        """Both normalized_merchant and inferred_category can be present at once."""
        rows = [
            _make_transaction_row(
                merchant="SQ *COFFEE SHOP",
                category="dining",
                metadata={
                    "normalized_merchant": "Blue Bottle Coffee",
                    "inferred_category": "coffee",
                    "extra_key": "preserved",
                },
            )
        ]
        app, _, _ = _build_finance_app(rows)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/finance/transactions")

        assert resp.status_code == 200
        item = resp.json()["data"][0]
        assert item["merchant"] == "SQ *COFFEE SHOP"
        assert item["normalized_merchant"] == "Blue Bottle Coffee"
        assert item["category"] == "dining"
        assert item["inferred_category"] == "coffee"
        # Raw metadata still present
        assert item["metadata"]["extra_key"] == "preserved"

    async def test_overlay_fields_absent_when_metadata_is_none(self):
        """When metadata is None (DB NULL), overlay fields default to None gracefully."""
        rows = [_make_transaction_row(metadata=None)]
        app, _, _ = _build_finance_app(rows)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/finance/transactions")

        assert resp.status_code == 200
        item = resp.json()["data"][0]
        assert item["normalized_merchant"] is None
        assert item["inferred_category"] is None
        assert item["metadata"] == {}

    async def test_raw_merchant_and_category_preserved_alongside_overlays(self):
        """The raw merchant and category columns are preserved even when overlays exist."""
        rows = [
            _make_transaction_row(
                merchant="RAW MERCHANT NAME",
                category="raw_category",
                metadata={
                    "normalized_merchant": "Clean Name",
                    "inferred_category": "clean_category",
                },
            )
        ]
        app, _, _ = _build_finance_app(rows)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/finance/transactions")

        assert resp.status_code == 200
        item = resp.json()["data"][0]
        # Original columns unchanged
        assert item["merchant"] == "RAW MERCHANT NAME"
        assert item["category"] == "raw_category"
        # Overlay columns added
        assert item["normalized_merchant"] == "Clean Name"
        assert item["inferred_category"] == "clean_category"

    async def test_pagination_metadata_present(self):
        """Response includes pagination metadata."""
        rows = [_make_transaction_row() for _ in range(3)]
        app, _, _ = _build_finance_app(rows, total=10)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/finance/transactions")

        assert resp.status_code == 200
        meta = resp.json()["meta"]
        assert meta["total"] == 10
        assert meta["offset"] == 0
        assert meta["limit"] == 50


# ---------------------------------------------------------------------------
# Tests: GET /api/finance/transactions — overlay filter behaviour
# ---------------------------------------------------------------------------


class TestTransactionsOverlayFilters:
    """Verify that merchant and category filters use COALESCE overlay fields in SQL."""

    async def test_merchant_filter_uses_coalesce_in_sql(self):
        """merchant= filter SQL must use COALESCE(metadata->>'normalized_merchant', merchant)."""
        rows = [
            _make_transaction_row(
                merchant="AMZN Mktp US*1A2B3C",
                metadata={"normalized_merchant": "Amazon"},
            )
        ]
        app, mock_pool, _ = _build_finance_app(rows)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/finance/transactions", params={"merchant": "Amazon"})

        assert resp.status_code == 200
        # Verify the SQL sent to the pool uses COALESCE for the merchant filter
        fetch_call_args = mock_pool.fetch.call_args[0]
        sql = fetch_call_args[0]
        assert "COALESCE(metadata->>'normalized_merchant', merchant)" in sql
        assert "ILIKE" in sql

    async def test_category_filter_uses_coalesce_in_sql(self):
        """category= filter SQL must use COALESCE(metadata->>'inferred_category', category)."""
        rows = [
            _make_transaction_row(
                category="shopping",
                metadata={"inferred_category": "electronics"},
            )
        ]
        app, mock_pool, _ = _build_finance_app(rows)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/finance/transactions", params={"category": "electronics"})

        assert resp.status_code == 200
        # Verify the SQL sent to the pool uses COALESCE for the category filter
        fetch_call_args = mock_pool.fetch.call_args[0]
        sql = fetch_call_args[0]
        assert "COALESCE(metadata->>'inferred_category', category)" in sql

    async def test_filtering_by_normalized_merchant_returns_matching_transactions(self):
        """Filtering by a normalized merchant name returns transactions with that overlay value."""
        rows = [
            _make_transaction_row(
                merchant="AMZN Mktp US*1A2B3C",
                metadata={"normalized_merchant": "Amazon"},
            )
        ]
        app, mock_pool, _ = _build_finance_app(rows)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/finance/transactions", params={"merchant": "Amazon"})

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["merchant"] == "AMZN Mktp US*1A2B3C"
        assert data[0]["normalized_merchant"] == "Amazon"

    async def test_filtering_by_inferred_category_returns_matching_transactions(self):
        """Filtering by an inferred category returns transactions with that overlay value."""
        rows = [
            _make_transaction_row(
                category="shopping",
                metadata={"inferred_category": "electronics"},
            )
        ]
        app, mock_pool, _ = _build_finance_app(rows)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/finance/transactions", params={"category": "electronics"})

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["category"] == "shopping"
        assert data[0]["inferred_category"] == "electronics"

    async def test_no_filter_does_not_add_coalesce_to_sql(self):
        """When no merchant or category filter is provided, no COALESCE clause is injected."""
        rows = [_make_transaction_row()]
        app, mock_pool, _ = _build_finance_app(rows)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/finance/transactions")

        assert resp.status_code == 200
        fetch_call_args = mock_pool.fetch.call_args[0]
        sql = fetch_call_args[0]
        # No WHERE clause injected when no filters are provided
        assert "WHERE" not in sql
        assert "COALESCE" not in sql

    async def test_combined_merchant_and_category_filters_use_coalesce(self):
        """Both merchant and category COALESCE filters can be combined in one request."""
        rows = [
            _make_transaction_row(
                merchant="SQ *BLUE BOTTLE",
                category="food",
                metadata={
                    "normalized_merchant": "Blue Bottle Coffee",
                    "inferred_category": "coffee",
                },
            )
        ]
        app, mock_pool, _ = _build_finance_app(rows)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/finance/transactions",
                params={"merchant": "Blue Bottle Coffee", "category": "coffee"},
            )

        assert resp.status_code == 200
        fetch_call_args = mock_pool.fetch.call_args[0]
        sql = fetch_call_args[0]
        assert "COALESCE(metadata->>'normalized_merchant', merchant)" in sql
        assert "COALESCE(metadata->>'inferred_category', category)" in sql


# ---------------------------------------------------------------------------
# Tests: GET /api/finance/spending-summary — COALESCE group-by
# ---------------------------------------------------------------------------


class TestSpendingSummaryOverlay:
    """Verify spending-summary uses COALESCE overlay dimensions in SQL."""

    async def test_category_group_uses_coalesce_in_sql(self):
        """SQL for group_by=category must use COALESCE(metadata->>'inferred_category', category)."""
        total_row = {"total": Decimal("100.00"), "currency": "USD"}
        group_rows = [
            {"key": "electronics", "amount": Decimal("60.00"), "count": 2},
            {"key": "dining", "amount": Decimal("40.00"), "count": 3},
        ]
        app, mock_pool, _ = _build_finance_app_with_spending(total_row, group_rows)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/finance/spending-summary",
                params={
                    "group_by": "category",
                    "start_date": "2026-01-01",
                    "end_date": "2026-01-31",
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["groups"]) == 2
        # Verify the SQL sent to the pool contains COALESCE for the category dimension
        fetch_call_args = mock_pool.fetch.call_args[0]
        sql = fetch_call_args[0]
        assert "COALESCE" in sql
        assert "inferred_category" in sql
        assert "category" in sql

    async def test_merchant_group_uses_coalesce_in_sql(self):
        """SQL for group_by=merchant must COALESCE(metadata->>'normalized_merchant', merchant)."""
        total_row = {"total": Decimal("200.00"), "currency": "USD"}
        group_rows = [
            {"key": "Amazon", "amount": Decimal("120.00"), "count": 5},
            {"key": "Blue Bottle Coffee", "amount": Decimal("80.00"), "count": 4},
        ]
        app, mock_pool, _ = _build_finance_app_with_spending(total_row, group_rows)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/finance/spending-summary",
                params={
                    "group_by": "merchant",
                    "start_date": "2026-01-01",
                    "end_date": "2026-01-31",
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["groups"]) == 2
        assert body["groups"][0]["key"] == "Amazon"

        # Verify the SQL sent to the pool contains COALESCE for the merchant dimension
        fetch_call_args = mock_pool.fetch.call_args[0]
        sql = fetch_call_args[0]
        assert "COALESCE" in sql
        assert "normalized_merchant" in sql
        assert "merchant" in sql

    async def test_spending_summary_response_shape(self):
        """spending-summary returns expected JSON shape with start_date, end_date, groups."""
        total_row = {"total": Decimal("55.00"), "currency": "EUR"}
        group_rows = [
            {"key": "groceries", "amount": Decimal("55.00"), "count": 3},
        ]
        app, _, _ = _build_finance_app_with_spending(total_row, group_rows, currency="EUR")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/finance/spending-summary",
                params={
                    "group_by": "category",
                    "start_date": "2026-02-01",
                    "end_date": "2026-02-28",
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["start_date"] == "2026-02-01"
        assert body["end_date"] == "2026-02-28"
        assert body["currency"] == "EUR"
        assert body["total_spend"] == "55.00"
        assert len(body["groups"]) == 1
        assert body["groups"][0]["key"] == "groceries"
        assert body["groups"][0]["count"] == 3
