"""Registered Travel document inventory: software SQL contract, not real PG proof."""

from types import SimpleNamespace

import pytest
from fastmcp import FastMCP

from butlers.modules.registry import default_registry

pytestmark = pytest.mark.unit


async def test_registered_inventory_unavailable_never_reports_empty_complete():
    module = default_registry()._modules["travel"]()
    mcp = FastMCP("travel-inventory-test")
    await module.register_tools(mcp, {}, SimpleNamespace(pool=None), "travel")
    result = (await mcp.call_tool("document_expiry_inventory", {})).structured_content
    assert result["status"] == "unavailable"
    assert result["complete"] is False
    assert result["items"] == []


class _Connection:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []
        self.closed = False

    def transaction(self, **kwargs):
        self.calls.append(("transaction", kwargs))
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True

    async def fetchval(self, sql):
        from datetime import UTC, datetime

        assert sql == "SELECT transaction_timestamp()"
        return datetime(2026, 10, 1, tzinfo=UTC)

    async def execute(self, sql):
        self.calls.append(("execute", sql))

    def cursor(self, sql, **kwargs):
        self.calls.append(("cursor", sql, kwargs))

        async def records():
            for row in self.rows:
                yield row

        return records()

    def acquire(self):
        return self


async def _inventory(rows):
    connection = _Connection(rows)
    module = default_registry()._modules["travel"]()
    mcp = FastMCP("travel-documents")
    await module.register_tools(mcp, {}, SimpleNamespace(pool=connection), "travel")
    result = (await mcp.call_tool("document_expiry_inventory", {})).structured_content
    return result, connection, module


async def test_all_stored_rows_cross_trip_pagination_and_keep_null_and_expired_dates():
    from datetime import date

    rows = [
        {
            "id": str(i),
            "trip_id": str(i),
            "type": "visa",
            "expiry_date": date(2020, 1, 1) if i % 2 else None,
        }
        for i in range(30)
    ]
    result, connection, module = await _inventory(rows)
    assert result["as_of"] == "2026-10-01T00:00:00+00:00"
    assert result["complete"] and len(result["items"]) == 30
    assert result["items"][1]["expiry_date"] == "2020-01-01"
    assert result["items"][0]["expiry_date"] is None
    sql = next(c[1] for c in connection.calls if c[0] == "cursor")
    assert "JOIN travel.trips" in sql and "ORDER BY d.id LIMIT 1001" in sql
    assert "status" not in sql and "blob_ref" not in sql and "metadata" not in sql
    assert connection.calls[0] == (
        "transaction",
        {
            "isolation": "repeatable_read",
            "readonly": True,
        },
    )
    assert connection.closed
    metadata = module.tool_metadata()["document_expiry_inventory"]
    assert metadata.effect_class == "read" and not metadata.owner_authority
    assert metadata.read_stores == ("travel.documents", "travel.trips")


async def test_unresolved_date_and_unknown_type_do_not_become_empty_success():
    result, _, _ = await _inventory(
        [
            {"id": "1", "trip_id": "a", "type": "visa", "expiry_date": "next year"},
            {"id": "2", "trip_id": "b", "type": "passport", "expiry_date": None},
        ]
    )
    assert not result["complete"] and result["reason"] == "unresolved"
    assert result["unresolved_count"] == 2 and result["items"] == []


async def test_inventory_cap_and_real_cancellation_release_snapshot():
    import asyncio

    rows = [
        {"id": str(i), "trip_id": "a", "type": "receipt", "expiry_date": None} for i in range(1001)
    ]
    result, connection, _ = await _inventory(rows)
    assert not result["complete"] and result["reason"] == "row_limit"
    assert len(result["items"]) == 1000 and connection.closed

    class Cancelled(_Connection):
        async def execute(self, sql):
            raise asyncio.CancelledError

    cancelled = Cancelled([])
    module = default_registry()._modules["travel"]()
    mcp = FastMCP("travel-cancel")
    await module.register_tools(mcp, {}, SimpleNamespace(pool=cancelled), "travel")
    with pytest.raises(asyncio.CancelledError):
        await mcp.call_tool("document_expiry_inventory", {})
    assert cancelled.closed


async def test_inventory_acquire_is_inside_five_second_aggregate_bound():
    import asyncio
    import time

    class Blocked(_Connection):
        async def __aenter__(self):
            await asyncio.Event().wait()

    pool = Blocked([])
    module = default_registry()._modules["travel"]()
    mcp = FastMCP("travel-timeout")
    await module.register_tools(mcp, {}, SimpleNamespace(pool=pool), "travel")
    started = time.monotonic()
    result = (await mcp.call_tool("document_expiry_inventory", {})).structured_content
    assert 4 <= time.monotonic() - started < 8
    assert result["reason"] == "timeout" and not result["complete"]
