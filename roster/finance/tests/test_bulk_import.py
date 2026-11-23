"""Real-PG behavior of finance bulk import (bu-ylnw9q).

Both bulk paths must land rows in ``finance.transactions`` (what GET /transactions
reads) and report exact imported / skipped counts when duplicates are present.
"""

from __future__ import annotations

import asyncio
import importlib.util
import shutil
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import httpx
import pytest

from butlers.api.deps import get_db
from butlers.tools.finance.transactions import (  # pyright: ignore[reportMissingImports]
    bulk_record_transactions,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_ROUTER_PATH = Path(__file__).parents[1] / "api" / "router.py"


def _load_router():
    name = "_finance_api_router_bulk_import_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _ROUTER_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _row(merchant: str, msg_id: str, amount: str = "-12.50") -> dict:
    return {
        "posted_at": "2026-01-15T10:00:00Z",
        "merchant": merchant,
        "amount": amount,
        "currency": "USD",
        "category": "groceries",
        "source_message_id": msg_id,
    }


async def _as_finance_rw(conn: asyncpg.Connection) -> None:
    await conn.execute("SET ROLE butler_finance_rw")


@pytest.fixture
async def finance_pool(db):
    # The memory chain provides ``facts``: the SPO mirror writes it and the transaction list
    # joins its correction overlay.
    return await db(
        "core",
        "memory",
        "finance",
        schemas={"memory": "finance", "finance": "finance"},
        schema="finance",
        init=_as_finance_rw,
        max_size=2,
    )


async def test_mcp_bulk_counts_existing_duplicate_as_skipped(finance_pool):
    first = await bulk_record_transactions(finance_pool, [_row("Existing Co", "m-1")])
    assert (first["imported"], first["skipped"]) == (1, 0)

    result = await bulk_record_transactions(
        finance_pool, [_row("Existing Co", "m-1"), _row("Fresh Co", "m-2")]
    )

    assert result["imported"] == 1
    assert result["skipped"] == 1
    assert result["errors"] == 0
    assert result["error_details"] == [{"index": 0, "reason": "duplicate"}]


async def test_blob_csv_overrides_preview_import_and_retry_in_finance_role(finance_pool):
    from butlers.tools.finance.data_import import import_transactions

    blob = AsyncMock(scheme="s3", bucket="bucket")
    blob.get.return_value = (
        b"Date;Payee;Amount\n17/01/2026;Bookshop;12.345\n18/01/2026;Refund;-2.00\n"
    )
    options = {
        "origin_butler": "finance",
        "delimiter": ";",
        "date_format": "%d/%m/%Y",
        "amount_sign": "positive_debit",
    }
    ref = "s3://bucket/finance/statement.csv"
    await finance_pool.execute(
        """
        INSERT INTO merchant_mappings (raw_pattern, normalized_merchant, category)
        VALUES ('book%', 'Bookshop', 'shopping')
        """
    )

    preview = await import_transactions(finance_pool, blob, ref, dry_run=True, **options)
    assert [(row["direction"], row["is_duplicate"]) for row in preview["preview"]] == [
        ("debit", False),
        ("credit", False),
    ]
    assert preview["merchant_mappings_applied"] == 1
    assert preview["preview"][0]["category"] == "shopping"
    assert preview["preview"][0]["amount"] == "12.35"
    assert await finance_pool.fetchval("SELECT count(*) FROM transactions") == 0
    assert await finance_pool.fetchval("SELECT count(*) FROM spending_summaries") == 0

    inserted = await import_transactions(finance_pool, blob, ref, **options)
    assert (inserted["imported"], inserted["skipped"], inserted["errors"]) == (2, 0, 0)
    assert inserted["mv_refreshed"] is True
    assert await finance_pool.fetchval(
        "SELECT total_amount FROM spending_summaries WHERE category = 'shopping'"
    ) == Decimal("12.35")
    rows = await finance_pool.fetch(
        "SELECT posted_at, merchant, amount, direction FROM transactions ORDER BY posted_at"
    )
    assert [tuple(row) for row in rows] == [
        (datetime(2026, 1, 17, tzinfo=UTC), "Bookshop", Decimal("12.35"), "debit"),
        (datetime(2026, 1, 18, tzinfo=UTC), "Refund", Decimal("2.00"), "credit"),
    ]
    retried = await import_transactions(finance_pool, blob, ref, **options)
    assert (retried["imported"], retried["skipped"], retried["errors"]) == (0, 2, 0)
    assert await finance_pool.fetchval("SELECT count(*) FROM transactions") == 2


async def test_blob_csv_deduplicates_shared_scope_batches_and_concurrent_replays(finance_pool):
    from butlers.tools.finance.data_import import import_transactions

    statement = (
        b"Date,Merchant,Amount\n2026-01-15,Books,-5\n2026-01-15,Books,-5\n"
        b"2026-01-16,Overflow,-9999999999999\n2026-01-16,Music,-3\n"
    )
    blob = AsyncMock(scheme="s3", bucket="bucket")
    blob.get.return_value = statement
    ref = "s3://bucket/finance/statement.csv"
    preview = await import_transactions(
        finance_pool, blob, ref, origin_butler="finance", dry_run=True
    )
    assert [item["is_duplicate"] for item in preview["preview"]] == [False, True, False, False]
    assert await finance_pool.fetchval("SELECT count(*) FROM transactions") == 0

    ready = asyncio.Barrier(2)

    async def read_statement(*args):
        await ready.wait()
        return statement

    blob.get.side_effect = read_statement
    async with asyncio.timeout(10):
        first, second = await asyncio.gather(
            import_transactions(finance_pool, blob, ref, origin_butler="finance"),
            import_transactions(finance_pool, blob, ref, origin_butler="finance"),
        )
    assert sorted((r["imported"], r["skipped"], r["errors"]) for r in (first, second)) == [
        (0, 3, 1),
        (2, 1, 1),
    ]
    for result in (first, second):
        [error] = result["error_details"]
        assert error["row_index"] == 2
        assert "numeric field overflow" in error["reason"]
    assert await finance_pool.fetchval("SELECT count(*) FROM transactions") == 2

    account_id = await finance_pool.fetchval(
        "INSERT INTO accounts (institution, type, name, currency) "
        "VALUES ('Bank', 'checking', 'Checking', 'USD') RETURNING id"
    )
    blob.get.side_effect = None
    blob.get.return_value = b"Date,Merchant,Amount\n2026-01-15,Books,-5\n"
    separate_account = await import_transactions(
        finance_pool, blob, ref, origin_butler="finance", account_id=str(account_id)
    )
    assert (
        separate_account["imported"],
        separate_account["skipped"],
        separate_account["errors"],
    ) == (
        1,
        0,
        0,
    )
    assert await finance_pool.fetchval("SELECT count(*) FROM transactions") == 3


async def test_rest_bulk_rows_list_and_counts_are_exact(finance_pool):
    from fastapi import FastAPI

    router_mod = _load_router()
    db = MagicMock()
    db.pool.return_value = finance_pool
    app = FastAPI()
    app.include_router(router_mod.router)
    app.dependency_overrides[get_db] = lambda: db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/finance/transactions/bulk",
            json={
                "transactions": [
                    _row("Alpha", "r-1"),
                    _row("Alpha", "r-1"),  # in-batch duplicate
                    _row("Beta", "r-2"),
                ],
                "source": "csv_import",
            },
        )
        listed = await client.get("/api/finance/transactions")

    assert resp.status_code == 200
    body = resp.json()
    assert (body["total"], body["imported"], body["skipped"], body["errors"]) == (3, 2, 1, 0)
    assert body["error_details"] == [{"index": 1, "reason": "duplicate"}]
    merchants = sorted(t["merchant"] for t in listed.json()["data"])
    assert merchants == ["Alpha", "Beta"]
