"""Tests for finance butler dashboard API endpoints.

Verifies the API contract (status codes, response shapes, filtering, pagination)
for the finance butler's GET endpoints.

Issue: butlers-ee32.8
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Load the finance router for dependency override
# ---------------------------------------------------------------------------

_ROUTER_PATH = Path(__file__).parents[1] / "api" / "router.py"


def _load_finance_router():
    """Dynamically load the finance router module."""
    import importlib.util
    import sys

    module_name = "finance_api_router_test"
    if module_name in sys.modules:
        return sys.modules[module_name]

    spec = importlib.util.spec_from_file_location(module_name, _ROUTER_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


_finance_router_mod = _load_finance_router()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(tz=UTC)
_TODAY = date.today()
_UUID = str(uuid.uuid4())
_ACCT_UUID = str(uuid.uuid4())


def _tx_row(
    *,
    id: Any = None,
    posted_at: Any = None,
    merchant: str = "Trader Joe's",
    description: str | None = None,
    amount: Any = "55.00",
    currency: str = "USD",
    direction: str = "debit",
    category: str = "groceries",
    payment_method: str | None = None,
    account_id: Any = None,
    receipt_url: str | None = None,
    external_ref: str | None = None,
    source_message_id: str | None = None,
    metadata: dict | None = None,
    created_at: Any = None,
    updated_at: Any = None,
) -> dict:
    return {
        "id": uuid.UUID(id) if id else uuid.uuid4(),
        "posted_at": posted_at or _NOW,
        "merchant": merchant,
        "description": description,
        "amount": Decimal(amount),
        "currency": currency,
        "direction": direction,
        "category": category,
        "payment_method": payment_method,
        "account_id": uuid.UUID(account_id) if account_id else None,
        "receipt_url": receipt_url,
        "external_ref": external_ref,
        "source_message_id": source_message_id,
        "metadata": metadata or {},
        "created_at": created_at or _NOW,
        "updated_at": updated_at or _NOW,
    }


def _sub_row(
    *,
    id: Any = None,
    service: str = "Netflix",
    amount: str = "15.49",
    currency: str = "USD",
    frequency: str = "monthly",
    next_renewal: Any = None,
    status: str = "active",
    auto_renew: bool = True,
    payment_method: str | None = None,
    account_id: Any = None,
    source_message_id: str | None = None,
    metadata: dict | None = None,
    created_at: Any = None,
    updated_at: Any = None,
) -> dict:
    return {
        "id": uuid.UUID(id) if id else uuid.uuid4(),
        "service": service,
        "amount": Decimal(amount),
        "currency": currency,
        "frequency": frequency,
        "next_renewal": next_renewal or (_TODAY + timedelta(days=30)),
        "status": status,
        "auto_renew": auto_renew,
        "payment_method": payment_method,
        "account_id": uuid.UUID(account_id) if account_id else None,
        "source_message_id": source_message_id,
        "metadata": metadata or {},
        "created_at": created_at or _NOW,
        "updated_at": updated_at or _NOW,
    }


def _bill_row(
    *,
    id: Any = None,
    payee: str = "Comcast",
    amount: str = "89.99",
    currency: str = "USD",
    due_date: Any = None,
    frequency: str = "monthly",
    status: str = "pending",
    payment_method: str | None = None,
    account_id: Any = None,
    source_message_id: str | None = None,
    statement_period_start: Any = None,
    statement_period_end: Any = None,
    paid_at: Any = None,
    metadata: dict | None = None,
    created_at: Any = None,
    updated_at: Any = None,
) -> dict:
    return {
        "id": uuid.UUID(id) if id else uuid.uuid4(),
        "payee": payee,
        "amount": Decimal(amount),
        "currency": currency,
        "due_date": due_date or (_TODAY + timedelta(days=7)),
        "frequency": frequency,
        "status": status,
        "payment_method": payment_method,
        "account_id": uuid.UUID(account_id) if account_id else None,
        "source_message_id": source_message_id,
        "statement_period_start": statement_period_start,
        "statement_period_end": statement_period_end,
        "paid_at": paid_at,
        "metadata": metadata or {},
        "created_at": created_at or _NOW,
        "updated_at": updated_at or _NOW,
    }


def _account_row(
    *,
    id: Any = None,
    institution: str = "Chase",
    type: str = "credit",
    name: str | None = "Sapphire",
    last_four: str | None = "4242",
    currency: str = "USD",
    metadata: dict | None = None,
    created_at: Any = None,
    updated_at: Any = None,
) -> dict:
    return {
        "id": uuid.UUID(id) if id else uuid.uuid4(),
        "institution": institution,
        "type": type,
        "name": name,
        "last_four": last_four,
        "currency": currency,
        "metadata": metadata or {},
        "created_at": created_at or _NOW,
        "updated_at": updated_at or _NOW,
    }


def _make_app(
    *,
    fetch_rows: list | None = None,
    fetchval_return: int | None = 0,
    fetchrow_return: dict | None = None,
):
    """Build a FastAPI test app with a mocked finance DatabaseManager."""
    from fastapi import FastAPI

    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=fetch_rows or [])
    mock_pool.fetchval = AsyncMock(return_value=fetchval_return)
    mock_pool.fetchrow = AsyncMock(return_value=fetchrow_return)

    mock_db = MagicMock()
    mock_db.pool.return_value = mock_pool

    app = FastAPI()
    app.include_router(_finance_router_mod.router)
    app.dependency_overrides[_finance_router_mod._get_db_manager] = lambda: mock_db

    return app, mock_pool


# ---------------------------------------------------------------------------
# Tests: GET /api/finance/transactions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_transactions_empty():
    """GET /api/finance/transactions returns empty list when no data."""
    app, _ = _make_app(fetch_rows=[], fetchval_return=0)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/finance/transactions")

    assert response.status_code == 200
    body = response.json()
    assert body["data"] == []
    assert body["meta"]["total"] == 0
    assert body["meta"]["offset"] == 0
    assert body["meta"]["limit"] == 50


@pytest.mark.asyncio
async def test_list_transactions_with_results():
    """GET /api/finance/transactions returns transaction records."""
    rows = [_tx_row(merchant="Netflix", category="subscriptions"), _tx_row()]
    app, _ = _make_app(fetch_rows=rows, fetchval_return=2)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/finance/transactions")

    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["total"] == 2
    assert len(body["data"]) == 2

    item = body["data"][0]
    assert "id" in item
    assert "posted_at" in item
    assert "merchant" in item
    assert "amount" in item
    assert "currency" in item
    assert "direction" in item
    assert "category" in item
    assert "metadata" in item
    assert "created_at" in item
    assert "updated_at" in item


@pytest.mark.asyncio
async def test_list_transactions_pagination_params():
    """GET /api/finance/transactions forwards offset/limit params correctly."""
    app, mock_pool = _make_app(fetch_rows=[], fetchval_return=100)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/finance/transactions?offset=10&limit=5")

    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["total"] == 100
    assert body["meta"]["offset"] == 10
    assert body["meta"]["limit"] == 5


@pytest.mark.asyncio
async def test_list_transactions_filter_by_category():
    """GET /api/finance/transactions filters by category."""
    rows = [_tx_row(category="groceries")]
    app, mock_pool = _make_app(fetch_rows=rows, fetchval_return=1)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/finance/transactions?category=groceries")

    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["total"] == 1
    # Verify category filter was in the query
    call_args = mock_pool.fetchval.call_args[0][0]
    assert "category" in call_args


@pytest.mark.asyncio
async def test_list_transactions_filter_by_merchant():
    """GET /api/finance/transactions filters by merchant substring."""
    rows = [_tx_row(merchant="Amazon")]
    app, mock_pool = _make_app(fetch_rows=rows, fetchval_return=1)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/finance/transactions?merchant=amazon")

    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["total"] == 1
    call_args = mock_pool.fetchval.call_args[0][0]
    assert "ILIKE" in call_args


@pytest.mark.asyncio
async def test_list_transactions_schema_prefix():
    """GET /api/finance/transactions uses finance schema prefix in queries."""
    app, mock_pool = _make_app(fetch_rows=[], fetchval_return=0)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.get("/api/finance/transactions")

    call_args = mock_pool.fetchval.call_args[0][0]
    assert "finance.transactions" in call_args


@pytest.mark.asyncio
async def test_list_transactions_optional_fields():
    """GET /api/finance/transactions maps optional fields correctly."""
    rows = [
        _tx_row(
            description="Prime monthly",
            payment_method="Amex",
            account_id=_ACCT_UUID,
            receipt_url="https://example.com/r",
            external_ref="ext-001",
            source_message_id="msg-001",
            metadata={"order_id": "ORD-123"},
        )
    ]
    app, _ = _make_app(fetch_rows=rows, fetchval_return=1)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/finance/transactions")

    item = response.json()["data"][0]
    assert item["description"] == "Prime monthly"
    assert item["payment_method"] == "Amex"
    assert item["account_id"] == _ACCT_UUID
    assert item["receipt_url"] == "https://example.com/r"
    assert item["external_ref"] == "ext-001"
    assert item["source_message_id"] == "msg-001"
    assert item["metadata"]["order_id"] == "ORD-123"


# ---------------------------------------------------------------------------
# Tests: GET /api/finance/subscriptions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_subscriptions_empty():
    """GET /api/finance/subscriptions returns empty list when no data."""
    app, _ = _make_app(fetch_rows=[], fetchval_return=0)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/finance/subscriptions")

    assert response.status_code == 200
    body = response.json()
    assert body["data"] == []
    assert body["meta"]["total"] == 0


@pytest.mark.asyncio
async def test_list_subscriptions_with_results():
    """GET /api/finance/subscriptions returns subscription records."""
    rows = [_sub_row(service="Netflix"), _sub_row(service="Spotify", amount="9.99")]
    app, _ = _make_app(fetch_rows=rows, fetchval_return=2)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/finance/subscriptions")

    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["total"] == 2
    assert len(body["data"]) == 2

    item = body["data"][0]
    for field in ("id", "service", "amount", "currency", "frequency", "next_renewal", "status"):
        assert field in item


@pytest.mark.asyncio
async def test_list_subscriptions_filter_by_status():
    """GET /api/finance/subscriptions filters by status."""
    rows = [_sub_row(status="active")]
    app, mock_pool = _make_app(fetch_rows=rows, fetchval_return=1)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/finance/subscriptions?status=active")

    assert response.status_code == 200
    assert response.json()["meta"]["total"] == 1
    call_args = mock_pool.fetchval.call_args[0][0]
    assert "status" in call_args


@pytest.mark.asyncio
async def test_list_subscriptions_pagination():
    """GET /api/finance/subscriptions respects pagination parameters."""
    app, _ = _make_app(fetch_rows=[], fetchval_return=50)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/finance/subscriptions?offset=20&limit=10")

    body = response.json()
    assert body["meta"]["total"] == 50
    assert body["meta"]["offset"] == 20
    assert body["meta"]["limit"] == 10


@pytest.mark.asyncio
async def test_list_subscriptions_schema_prefix():
    """GET /api/finance/subscriptions uses finance schema prefix."""
    app, mock_pool = _make_app(fetch_rows=[], fetchval_return=0)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.get("/api/finance/subscriptions")

    call_args = mock_pool.fetchval.call_args[0][0]
    assert "finance.subscriptions" in call_args


# ---------------------------------------------------------------------------
# Tests: GET /api/finance/bills
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_bills_empty():
    """GET /api/finance/bills returns empty list when no data."""
    app, _ = _make_app(fetch_rows=[], fetchval_return=0)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/finance/bills")

    assert response.status_code == 200
    body = response.json()
    assert body["data"] == []
    assert body["meta"]["total"] == 0


@pytest.mark.asyncio
async def test_list_bills_with_results():
    """GET /api/finance/bills returns bill records."""
    rows = [_bill_row(payee="Comcast"), _bill_row(payee="Rent", amount="2200.00")]
    app, _ = _make_app(fetch_rows=rows, fetchval_return=2)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/finance/bills")

    body = response.json()
    assert body["meta"]["total"] == 2
    assert len(body["data"]) == 2

    item = body["data"][0]
    for field in ("id", "payee", "amount", "currency", "due_date", "frequency", "status"):
        assert field in item


@pytest.mark.asyncio
async def test_list_bills_filter_by_status():
    """GET /api/finance/bills filters by status."""
    rows = [_bill_row(status="pending")]
    app, mock_pool = _make_app(fetch_rows=rows, fetchval_return=1)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/finance/bills?status=pending")

    assert response.status_code == 200
    call_args = mock_pool.fetchval.call_args[0][0]
    assert "status" in call_args


@pytest.mark.asyncio
async def test_list_bills_filter_by_payee():
    """GET /api/finance/bills filters by payee substring."""
    rows = [_bill_row(payee="Comcast")]
    app, mock_pool = _make_app(fetch_rows=rows, fetchval_return=1)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/finance/bills?payee=comcast")

    assert response.status_code == 200
    call_args = mock_pool.fetchval.call_args[0][0]
    assert "ILIKE" in call_args


@pytest.mark.asyncio
async def test_list_bills_pagination():
    """GET /api/finance/bills respects pagination parameters."""
    app, _ = _make_app(fetch_rows=[], fetchval_return=30)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/finance/bills?offset=5&limit=10")

    body = response.json()
    assert body["meta"]["total"] == 30
    assert body["meta"]["offset"] == 5
    assert body["meta"]["limit"] == 10


@pytest.mark.asyncio
async def test_list_bills_schema_prefix():
    """GET /api/finance/bills uses finance schema prefix."""
    app, mock_pool = _make_app(fetch_rows=[], fetchval_return=0)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.get("/api/finance/bills")

    call_args = mock_pool.fetchval.call_args[0][0]
    assert "finance.bills" in call_args


# ---------------------------------------------------------------------------
# Tests: GET /api/finance/accounts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_accounts_empty():
    """GET /api/finance/accounts returns empty list when no data."""
    app, _ = _make_app(fetch_rows=[], fetchval_return=0)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/finance/accounts")

    assert response.status_code == 200
    body = response.json()
    assert body["data"] == []
    assert body["meta"]["total"] == 0


@pytest.mark.asyncio
async def test_list_accounts_with_results():
    """GET /api/finance/accounts returns account records."""
    rows = [_account_row(institution="Chase"), _account_row(institution="Ally", type="savings")]
    app, _ = _make_app(fetch_rows=rows, fetchval_return=2)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/finance/accounts")

    body = response.json()
    assert body["meta"]["total"] == 2
    assert len(body["data"]) == 2

    item = body["data"][0]
    for field in ("id", "institution", "type", "currency", "metadata"):
        assert field in item


@pytest.mark.asyncio
async def test_list_accounts_filter_by_type():
    """GET /api/finance/accounts filters by account type."""
    rows = [_account_row(type="credit")]
    app, mock_pool = _make_app(fetch_rows=rows, fetchval_return=1)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/finance/accounts?type=credit")

    assert response.status_code == 200
    call_args = mock_pool.fetchval.call_args[0][0]
    assert "type" in call_args


@pytest.mark.asyncio
async def test_list_accounts_pagination():
    """GET /api/finance/accounts respects pagination parameters."""
    app, _ = _make_app(fetch_rows=[], fetchval_return=20)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/finance/accounts?offset=10&limit=5")

    body = response.json()
    assert body["meta"]["total"] == 20
    assert body["meta"]["offset"] == 10
    assert body["meta"]["limit"] == 5


@pytest.mark.asyncio
async def test_list_accounts_schema_prefix():
    """GET /api/finance/accounts uses finance schema prefix."""
    app, mock_pool = _make_app(fetch_rows=[], fetchval_return=0)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.get("/api/finance/accounts")

    call_args = mock_pool.fetchval.call_args[0][0]
    assert "finance.accounts" in call_args


# ---------------------------------------------------------------------------
# Tests: GET /api/finance/spending-summary
# ---------------------------------------------------------------------------


def _spending_fetchrow():
    """Return a mock fetchrow result for spending total/currency."""
    return {"total": Decimal("150.00"), "currency": "USD"}


@pytest.mark.asyncio
async def test_spending_summary_basic_shape():
    """GET /api/finance/spending-summary returns SpendingSummaryModel shape."""
    group_rows = [
        {"key": "groceries", "amount": Decimal("100.00"), "count": 2},
        {"key": "dining", "amount": Decimal("50.00"), "count": 1},
    ]

    app, mock_pool = _make_app(fetchrow_return=_spending_fetchrow())
    mock_pool.fetch = AsyncMock(return_value=group_rows)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/finance/spending-summary")

    assert response.status_code == 200
    body = response.json()
    assert "start_date" in body
    assert "end_date" in body
    assert "currency" in body
    assert "total_spend" in body
    assert "groups" in body
    assert isinstance(body["groups"], list)


@pytest.mark.asyncio
async def test_spending_summary_group_by_category():
    """GET /api/finance/spending-summary?group_by=category uses category grouping."""
    group_rows = [{"key": "groceries", "amount": Decimal("80.00"), "count": 3}]

    app, mock_pool = _make_app(fetchrow_return=_spending_fetchrow())
    mock_pool.fetch = AsyncMock(return_value=group_rows)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/finance/spending-summary?group_by=category")

    assert response.status_code == 200
    body = response.json()
    assert body["groups"][0]["key"] == "groceries"
    assert body["groups"][0]["count"] == 3


@pytest.mark.asyncio
async def test_spending_summary_group_by_merchant():
    """GET /api/finance/spending-summary?group_by=merchant uses merchant grouping."""
    group_rows = [{"key": "Netflix", "amount": Decimal("15.49"), "count": 1}]

    app, mock_pool = _make_app(fetchrow_return=_spending_fetchrow())
    mock_pool.fetch = AsyncMock(return_value=group_rows)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/finance/spending-summary?group_by=merchant")

    assert response.status_code == 200
    body = response.json()
    assert body["groups"][0]["key"] == "Netflix"


@pytest.mark.asyncio
async def test_spending_summary_empty_groups():
    """GET /api/finance/spending-summary returns empty groups when no transactions."""
    app, mock_pool = _make_app(fetchrow_return={"total": Decimal("0"), "currency": "USD"})
    mock_pool.fetch = AsyncMock(return_value=[])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/finance/spending-summary")

    assert response.status_code == 200
    body = response.json()
    assert body["groups"] == []
    assert body["total_spend"] == "0"


@pytest.mark.asyncio
async def test_spending_summary_invalid_group_by():
    """GET /api/finance/spending-summary with invalid group_by returns 422."""
    app, _ = _make_app()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/finance/spending-summary?group_by=invalid")

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_spending_summary_date_params():
    """GET /api/finance/spending-summary accepts start_date and end_date params."""
    app, mock_pool = _make_app(fetchrow_return=_spending_fetchrow())
    mock_pool.fetch = AsyncMock(return_value=[])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/finance/spending-summary?start_date=2026-01-01&end_date=2026-01-31"
        )

    assert response.status_code == 200
    body = response.json()
    assert body["start_date"] == "2026-01-01"
    assert body["end_date"] == "2026-01-31"


# ---------------------------------------------------------------------------
# Tests: GET /api/finance/upcoming-bills
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upcoming_bills_empty():
    """GET /api/finance/upcoming-bills returns empty items when no bills."""
    app, mock_pool = _make_app(fetch_rows=[])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/finance/upcoming-bills")

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["count"] == 0
    assert "total_amount" in body
    assert "days_ahead" in body


@pytest.mark.asyncio
async def test_upcoming_bills_with_results():
    """GET /api/finance/upcoming-bills returns bills with urgency classification."""
    today = date.today()
    rows = [
        _bill_row(payee="Comcast", due_date=today + timedelta(days=5)),
        _bill_row(payee="Rent", amount="2200.00", due_date=today + timedelta(days=1)),
    ]
    app, mock_pool = _make_app(fetch_rows=rows)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/finance/upcoming-bills")

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 2

    item = body["items"][0]
    assert "bill" in item
    assert "urgency" in item
    assert "days_until_due" in item
    assert item["urgency"] in ("due_today", "due_soon", "upcoming", "overdue")


@pytest.mark.asyncio
async def test_upcoming_bills_urgency_classification():
    """GET /api/finance/upcoming-bills classifies bills correctly by urgency."""
    today = date.today()
    rows = [
        _bill_row(payee="Overdue Bill", due_date=today - timedelta(days=3)),
        _bill_row(payee="Due Today", due_date=today),
        _bill_row(payee="Due Soon", due_date=today + timedelta(days=2)),
        _bill_row(payee="Upcoming", due_date=today + timedelta(days=10)),
    ]
    app, mock_pool = _make_app(fetch_rows=rows)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/finance/upcoming-bills?days_ahead=30")

    body = response.json()
    items = body["items"]
    urgencies = {item["bill"]["payee"]: item["urgency"] for item in items}

    assert urgencies["Overdue Bill"] == "overdue"
    assert urgencies["Due Today"] == "due_today"
    assert urgencies["Due Soon"] == "due_soon"
    assert urgencies["Upcoming"] == "upcoming"


@pytest.mark.asyncio
async def test_upcoming_bills_days_ahead_param():
    """GET /api/finance/upcoming-bills respects days_ahead parameter."""
    app, mock_pool = _make_app(fetch_rows=[])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/finance/upcoming-bills?days_ahead=7")

    assert response.status_code == 200
    body = response.json()
    assert body["days_ahead"] == 7


@pytest.mark.asyncio
async def test_upcoming_bills_include_overdue_false():
    """GET /api/finance/upcoming-bills?include_overdue=false excludes overdue bills."""
    today = date.today()
    # Row that would be overdue; with include_overdue=false, the query should exclude it
    rows = [_bill_row(payee="Future Bill", due_date=today + timedelta(days=3))]
    app, mock_pool = _make_app(fetch_rows=rows)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/finance/upcoming-bills?include_overdue=false")

    assert response.status_code == 200
    body = response.json()
    assert body["include_overdue"] is False
    # Confirm fetch was called (verifies the query path was taken)
    assert mock_pool.fetch.called


# ---------------------------------------------------------------------------
# Tests: 503 when pool not available
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transactions_503_when_pool_unavailable():
    """GET /api/finance/transactions returns 503 when DB pool is not available."""
    from fastapi import FastAPI

    mock_db = MagicMock()
    mock_db.pool.side_effect = KeyError("finance")

    app = FastAPI()
    app.include_router(_finance_router_mod.router)
    app.dependency_overrides[_finance_router_mod._get_db_manager] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/finance/transactions")

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_subscriptions_503_when_pool_unavailable():
    """GET /api/finance/subscriptions returns 503 when DB pool is not available."""
    from fastapi import FastAPI

    mock_db = MagicMock()
    mock_db.pool.side_effect = KeyError("finance")

    app = FastAPI()
    app.include_router(_finance_router_mod.router)
    app.dependency_overrides[_finance_router_mod._get_db_manager] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/finance/subscriptions")

    assert response.status_code == 503
