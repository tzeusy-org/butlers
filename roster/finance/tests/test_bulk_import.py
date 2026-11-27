"""Real-PG behavior of finance bulk import (bu-ylnw9q).

Daemon bulk and stored-CSV imports must land rows in ``finance.transactions`` (what GET /transactions
reads) and report exact imported / skipped counts when duplicates are present.
"""

from __future__ import annotations

import asyncio
import importlib.util
import shutil
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import httpx
import pytest

from butlers.api.deps import get_db
from butlers.roster_imports import roster_module
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
    # Include historical memory facts to prove ledger writes leave them unchanged.
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
    import_transactions = roster_module("finance", "tools/data_import").import_transactions

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
    import_transactions = roster_module("finance", "tools/data_import").import_transactions

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


async def test_mcp_bulk_rows_list_and_counts_are_exact(finance_pool):
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
        body = await bulk_record_transactions(
            finance_pool,
            transactions=[
                _row("Alpha", "r-1")
                | {
                    "metadata": {
                        "normalized_merchant": "Alpha display",
                        "inferred_category": "dining",
                        "order_id": "synthetic-order",
                    }
                },
                _row("Alpha", "r-1"),
                _row("Beta", "r-2") | {"posted_at": "2026-01-16T10:00:00Z"},
            ],
            source="csv_import",
        )
        listed = await client.get("/api/finance/transactions")

    assert listed.status_code == 200
    assert (body["total"], body["imported"], body["skipped"], body["errors"]) == (3, 2, 1, 0)
    assert body["error_details"] == [{"index": 1, "reason": "duplicate"}]
    merchants = sorted(t["merchant"] for t in listed.json()["data"])
    assert merchants == ["Alpha", "Beta"]
    assert [row["merchant"] for row in listed.json()["data"]] == ["Beta", "Alpha"]
    alpha = next(row for row in listed.json()["data"] if row["merchant"] == "Alpha")
    assert alpha["normalized_merchant"] == "Alpha display"
    assert alpha["inferred_category"] == "dining"
    assert alpha["metadata"]["order_id"] == "synthetic-order"
    assert "normalized_merchant" not in alpha["metadata"]
    assert "inferred_category" not in alpha["metadata"]
    assert all(t["metadata"]["import_source"] == "csv_import" for t in listed.json()["data"])


async def test_ledger_enrichment_api_and_exposed_writer_preserve_history(finance_pool):
    """MCP enrichment, REST edits, filters and analytics share transaction-ID fields."""
    from uuid import uuid4

    from fastapi import FastAPI

    from butlers.core.goal_lineage import VerifiedLineage, lineage_scope
    from butlers.testing.db_manager import db_manager

    spending_summary = roster_module("finance", "tools/spending").spending_summary
    bulk_update_transactions = roster_module(
        "finance", "tools/transactions"
    ).bulk_update_transactions

    history_id = await finance_pool.fetchval(
        """INSERT INTO facts(subject,predicate,scope,content,metadata,valid_at)
           VALUES ('owner','transaction_debit','finance','synthetic history',
                   '{"merchant":"History only","amount":"9.00","currency":"USD"}',now())
           RETURNING id"""
    )
    history = await finance_pool.fetchval("SELECT to_jsonb(f) FROM facts f WHERE id=$1", history_id)
    imported = await bulk_record_transactions(
        finance_pool, [_row("Alpha RAW", "e-1"), _row("Beta", "e-2")]
    )
    assert imported["imported"] == 2
    # A fresh write has a positive ledger witness and cannot add/modify transaction facts.
    assert await finance_pool.fetchval("SELECT count(*) FROM transactions") == 2
    assert (
        await finance_pool.fetchval(
            "SELECT count(*) FROM facts WHERE predicate LIKE 'transaction_%'"
        )
        == 1
    )

    goal, invocation = uuid4(), uuid4()
    lineage = VerifiedLineage(
        goal_id=goal,
        invocation_id=invocation,
        depth=0,
        origin="owner",
        exposed=True,
        capability="bulk_update_transactions",
        args_digest="test",
        call_id=uuid4(),
    )
    with lineage_scope(lineage):
        result = await bulk_update_transactions(
            finance_pool,
            [
                {
                    "match": {"merchant_pattern": "Alpha%"},
                    "set": {
                        "normalized_merchant": "Synthetic Coffee",
                        "inferred_category": "dining",
                    },
                }
            ],
        )
    assert result["updated_total"] == 1
    stored = await finance_pool.fetchrow("SELECT * FROM transactions WHERE merchant='Alpha RAW'")
    assert (stored["taint_goal_id"], stored["taint_invocation_id"]) == (goal, invocation)

    router_mod = _load_router()
    app = FastAPI()
    app.include_router(router_mod.router)
    app.dependency_overrides[get_db] = lambda: db_manager({"finance": finance_pool})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.patch(
            "/api/finance/transactions/bulk-metadata",
            json={
                "ops": [
                    {
                        "match": {"merchant_pattern": "Alpha%"},
                        "set": {"inferred_category": "shopping"},
                    }
                ]
            },
        )
        assert response.status_code == 200
        assert response.json()["updated_total"] == 1
        selected = await client.get(
            "/api/finance/transactions",
            params={
                "merchant": "Synthetic Coffee",
                "category": "shopping",
            },
        )
        assert selected.status_code == 200
        assert selected.json()["meta"]["total"] == 1
        row = selected.json()["data"][0]
        assert (row["merchant"], row["category"]) == ("Alpha RAW", "groceries")
        assert (row["normalized_merchant"], row["inferred_category"]) == (
            "Synthetic Coffee",
            "shopping",
        )
        summary = await client.get(
            "/api/finance/spending-summary",
            params={
                "start_date": "2026-01-01",
                "end_date": "2026-01-31",
                "group_by": "category",
            },
        )
        assert summary.status_code == 200
        assert any(group["key"] == "shopping" for group in summary.json()["groups"])
        distinct = await client.get("/api/finance/merchants/distinct")
        assert distinct.status_code == 200
        assert any(
            item["normalized_merchant"] == "Synthetic Coffee" for item in distinct.json()["data"]
        )
        removed = await client.post(
            "/api/finance/transactions/bulk", json={"transactions": [_row("Removed", "no")]}
        )
        assert removed.status_code == 404

    # Owner edits leave the previously exposed writer pointer in place.
    stored = await finance_pool.fetchrow("SELECT * FROM transactions WHERE id=$1", stored["id"])
    assert (stored["taint_goal_id"], stored["taint_invocation_id"]) == (goal, invocation)
    assert (stored["display_merchant"], stored["display_category"]) == (
        "Synthetic Coffee",
        "shopping",
    )
    assert (
        await finance_pool.fetchval("SELECT to_jsonb(f) FROM facts f WHERE id=$1", history_id)
        == history
    )
    assert await finance_pool.fetchval("SELECT count(*) FROM transactions") == 2
    spend = await spending_summary(finance_pool, "2026-01-01", "2026-01-31", group_by="merchant")
    assert any(group["key"] == "Synthetic Coffee" for group in spend["groups"])
    match_transaction_to_bills = roster_module(
        "finance", "tools/reconciliation"
    ).match_transaction_to_bills

    match = await match_transaction_to_bills(
        finance_pool,
        dict(stored),
        bills=[
            {
                "id": uuid4(),
                "payee": "Synthetic Coffee",
                "currency": "USD",
                "amount": Decimal("12.50"),
                "due_date": date(2026, 1, 20),
                "statement_period_end": None,
            }
        ],
    )
    assert match["tier"] == "auto_settle"
    prior = {
        **_row("Prior ledger", "e-prior", "-6.25"),
        "posted_at": "2025-12-15T10:00:00Z",
        "category": "transfer",
        "metadata": {"inferred_category": "shopping"},
    }
    with lineage_scope(lineage):
        assert (await bulk_record_transactions(finance_pool, [prior]))["imported"] == 1
    prior_row = await finance_pool.fetchrow(
        "SELECT * FROM transactions WHERE merchant='Prior ledger'"
    )
    assert (prior_row["taint_goal_id"], prior_row["taint_invocation_id"]) == (goal, invocation)
    trend = await roster_module("finance", "jobs/finance_jobs")._month_over_month_trend(
        finance_pool, last_month_start=date(2026, 1, 1), last_month_end=date(2026, 2, 1)
    )
    assert trend is not None
    assert "shopping +100%" in trend["notable"]
    split = await roster_module("finance", "tools/transactions").split_transaction(
        finance_pool,
        str(stored["id"]),
        [
            {"amount": "7.50", "category": "groceries"},
            {"amount": "5.00", "category": "dining"},
        ],
    )
    assert len(split["splits"]) == 2
    for child in split["splits"]:
        assert child["normalized_merchant"] == child["display_merchant"] == "Synthetic Coffee"
        assert child["inferred_category"] == child["display_category"] == "shopping"
        assert str(child["taint_goal_id"]) == str(goal)
        assert str(child["taint_invocation_id"]) == str(invocation)
    updated = await roster_module("finance", "tools/transactions").update_transaction(
        finance_pool,
        split["splits"][0]["id"],
        metadata={"normalized_merchant": "Owner display", "receipt": "kept"},
    )
    assert updated["normalized_merchant"] == updated["display_merchant"] == "Owner display"
    assert updated["metadata"]["receipt"] == "kept"
    assert updated["taint_goal_id"] == str(goal)
