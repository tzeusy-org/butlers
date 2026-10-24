"""Held-authority passport inventory through real MCP registration."""

from types import SimpleNamespace

import pytest
from fastmcp import FastMCP

from butlers.modules.memory import MemoryModule

pytestmark = pytest.mark.unit


async def test_registered_inventory_missing_authority_never_reports_absence():
    module = MemoryModule()
    mcp = FastMCP("memory-inventory-test")
    await module.register_tools(mcp, {}, SimpleNamespace(pool=None, schema="travel"), "travel")
    result = (await mcp.call_tool("memory_fact_inventory", {})).structured_content
    assert result["status"] == "unavailable"
    assert result["complete"] is False
    assert result["authority_source"] == "held_runtime_config"


class _Pool:
    def __init__(self, rows=()):
        self.rows = rows
        self.query = None
        self.args = None
        self.closed = False
        self.transactions = []

    def acquire(self):
        return self

    def transaction(self, **kwargs):
        self.transactions.append(kwargs)
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
        assert "statement_timeout" in sql

    def cursor(self, query, *args, **kwargs):
        self.query, self.args = query, args

        async def records():
            for row in self.rows:
                yield row

        return records()


async def _registered(pool, *, ceiling="normal", accessor=True):
    from unittest.mock import AsyncMock

    held = SimpleNamespace(catalog_read_sensitivity=ceiling)
    get = AsyncMock(return_value=held)
    db = SimpleNamespace(pool=pool, schema="travel")
    if accessor:
        db.runtime_config_accessor = SimpleNamespace(get=get)
    module = MemoryModule()
    mcp = FastMCP("fact-inventory")
    await module.register_tools(mcp, {}, db, "travel")
    return mcp, get, module


def _fact(id="1", content="2030-01-01", **overrides):
    from datetime import UTC, datetime

    return {
        "id": id,
        "subject": "owner passport",
        "entity_id": None,
        "object_entity_id": None,
        "scope": "travel",
        "content": content,
        "source_butler": "travel",
        "observed_at": datetime(2026, 1, 1, tzinfo=UTC),
        "content_authority": "owner",
        "authority_entity_id": None,
        "identity_bounded": True,
        **overrides,
    }


async def test_held_normal_scope_filters_before_limit_without_cache_or_reference_bumps():
    pool = _Pool([_fact()])
    mcp, get, module = await _registered(pool)
    result = (await mcp.call_tool("memory_fact_inventory", {})).structured_content
    assert result["complete"] and result["items"][0]["expiry_date"] == "2030-01-01"
    assert result["coverage"] == "authorized_scope"
    assert result["as_of"] == pool.args[4].isoformat() == "2026-10-01T00:00:00+00:00"
    get.assert_awaited_once_with()
    assert pool.args[:4] == ("shared", "passport_expiry", ["travel", "global"], ["normal"])
    before_limit = pool.query.split("LIMIT")[0]
    assert all(
        term in before_limit
        for term in (
            "f.tenant_id = $1",
            "f.predicate = $2",
            "f.scope = ANY($3",
            "f.sensitivity = ANY($4",
            "f.valid_at",
            "f.invalid_at",
            "NOT EXISTS",
            "newer.supersedes_id",
            "f.observed_at",
        )
    )
    assert "UPDATE" not in pool.query and "reference_count" not in pool.query
    assert pool.transactions == [{"isolation": "repeatable_read", "readonly": True}]
    assert pool.closed
    assert module.tool_metadata()["memory_fact_inventory"].resource_params == ("predicate", "scope")


@pytest.mark.parametrize("ceiling", ["internal", "confidential"])
async def test_held_wider_ceiling_is_server_owned_not_caller_authority(ceiling):
    pool = _Pool()
    mcp, _, _ = await _registered(pool, ceiling=ceiling)
    result = (await mcp.call_tool("memory_fact_inventory", {"scope": "global"})).structured_content
    assert result["complete"] and pool.args[2] == ["global"]
    assert pool.args[3] == (
        ["normal", "pii"] if ceiling == "internal" else ["normal", "pii", "confidential"]
    )


async def test_unknown_or_failed_held_authority_and_scope_injection_never_query():
    pool = _Pool()
    mcp, get, _ = await _registered(pool, ceiling="unrecognized")
    result = (await mcp.call_tool("memory_fact_inventory", {})).structured_content
    assert not result["complete"] and pool.query is None
    get.side_effect = RuntimeError("private diagnostic must not escape")
    result = (await mcp.call_tool("memory_fact_inventory", {})).structured_content
    assert result["reason"] == "authority_unavailable" and pool.query is None
    for args in ({"scope": "other_tenant"}, {"predicate": "owner_secret"}):
        result = (await mcp.call_tool("memory_fact_inventory", args)).structured_content
        assert result["reason"] == "unsupported_scope" and pool.query is None
    assert "private diagnostic" not in str(result)


async def test_conflicting_and_noncanonical_dates_remain_unresolved():
    rows = [
        _fact("1"),
        _fact("2", "2031-01-01"),
        _fact("3", "next year", subject="other passport"),
        _fact("4", "2030-02-01", subject="valid passport"),
    ]
    mcp, _, _ = await _registered(_Pool(rows))
    result = (await mcp.call_tool("memory_fact_inventory", {})).structured_content
    assert not result["complete"] and result["unresolved_count"] == 3
    assert [item["fact_id"] for item in result["items"]] == ["4"]


async def test_cap_is_incomplete_even_with_no_usable_dates():
    pool = _Pool([_fact(str(i), "bad date", subject=f"p{i}") for i in range(1001)])
    mcp, _, _ = await _registered(pool)
    result = (await mcp.call_tool("memory_fact_inventory", {})).structured_content
    assert not result["complete"] and result["items"] == []
    assert result["unresolved_count"] == 1000 and pool.closed


async def test_new_missing_authority_refusal_preserves_legacy_normal_fallback():
    mcp, _, module = await _registered(_Pool(), accessor=False)
    legacy = await module._catalog_read_policy()
    assert legacy.authority == "normal" and not legacy.policy_defaulted
    result = (await mcp.call_tool("memory_fact_inventory", {})).structured_content
    assert result["status"] == "unavailable" and not result["complete"]
