"""Real-PG behavior of finance bulk import (bu-ylnw9q).

Both bulk paths must land rows in ``finance.transactions`` (what GET /transactions
reads) and report exact imported / skipped counts when duplicates are present.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock

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
