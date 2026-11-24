"""``metric_baseline_get`` core tool: registration and shape (bu-q7vx1q.77)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp import Client, FastMCP

from butlers.core_tools._base import ToolContext
from butlers.core_tools._dispatcher import register_all_core_tools

pytestmark = pytest.mark.unit


def _row(**overrides: Any) -> dict[str, Any]:
    row = {
        "metric_key": "resting_hr",
        "bucket_value": "all",
        "method_version": "mad-v1",
        "status": "ready",
        "measurability": "measurable",
        "unit": "bpm",
        "center": 56.0,
        "dispersion": 2.0,
        "k_mad": 2.6,
        "n_observed": 54,
        "n_expected": 60,
        "coverage": 0.9,
        "input_digest": "d",
        "computed_at": datetime.now(UTC) - timedelta(hours=3),
    }
    row.update(overrides)
    return row


def _ctx(pool: Any) -> ToolContext:
    return ToolContext(
        daemon=MagicMock(),
        pool=pool,
        spawner=None,
        butler_name="health",
        butler_type=None,
        is_switchboard=False,
        is_messenger=False,
        route_metrics=None,
    )


def _build(pool: Any) -> tuple[FastMCP, list[tuple[str, str]]]:
    mcp = FastMCP("t")
    registered: list[tuple[str, str]] = []

    def _core_tool(group: str, **kwargs: Any):
        def decorator(fn):
            registered.append((fn.__name__, group))
            return mcp.tool(**kwargs)(fn)

        return decorator

    register_all_core_tools(_ctx(pool), mcp, _core_tool)
    return mcp, registered


async def _call(mcp: FastMCP, **arguments: Any) -> dict[str, Any]:
    async with Client(mcp) as client:
        result = await client.call_tool("metric_baseline_get", arguments)
    return json.loads(result.content[0].text)


async def test_registers_one_read_only_infrastructure_tool() -> None:
    mcp, registered = _build(AsyncMock())
    assert [row for row in registered if row[0] == "metric_baseline_get"] == [
        ("metric_baseline_get", "infra")
    ]
    async with Client(mcp) as client:
        tool = next(t for t in await client.list_tools() if t.name == "metric_baseline_get")
    assert tool.annotations.readOnlyHint is True
    assert tool.annotations.destructiveHint is False


def test_the_dispatcher_registers_it_exactly_once() -> None:
    registered: list[str] = []

    def _core_tool(group: str, **kwargs: Any):
        def decorator(fn):
            registered.append(fn.__name__)
            return fn

        return decorator

    register_all_core_tools(_ctx(AsyncMock()), MagicMock(), _core_tool)
    assert registered.count("metric_baseline_get") == 1


async def test_ready_reading_has_the_documented_shape() -> None:
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[_row()])
    mcp, _ = _build(pool)
    body = await _call(mcp, metric_key="resting_hr")
    assert set(body) >= {
        "state",
        "band",
        "n_observed",
        "n_expected",
        "coverage",
        "computed_at",
        "stale",
    }
    assert body["state"] == "ready"
    assert body["band"] == {"low": pytest.approx(50.8), "high": pytest.approx(61.2)}
    assert (body["n_observed"], body["n_expected"], body["coverage"]) == (54, 60, 0.9)
    assert body["stale"] is False


async def test_unknown_key_is_a_typed_no_baseline_not_an_error() -> None:
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    mcp, _ = _build(pool)
    body = await _call(mcp, metric_key="nonexistent")
    assert body["state"] == "no_baseline"
    assert pool.fetch.await_args.args[1] == ["nonexistent"]
    assert body["metric_key"] == "nonexistent"
    assert body["band"] is None
    assert body["computed_at"] is None


async def test_stale_row_reads_stale_without_a_band() -> None:
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[_row(computed_at=datetime.now(UTC) - timedelta(days=4))])
    mcp, _ = _build(pool)
    body = await _call(mcp, metric_key="resting_hr")
    assert body["state"] == "stale" and body["stale"] is True
    assert body["band"] is None
    assert body["computed_at"] is not None


async def test_the_tool_only_reads() -> None:
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[_row()])
    mcp, _ = _build(pool)
    await _call(mcp, metric_key="resting_hr")
    pool.execute.assert_not_called()
    pool.fetchrow.assert_not_called()
    sql = pool.fetch.await_args.args[0].upper()
    assert "INSERT" not in sql and "UPDATE" not in sql and "DELETE" not in sql


async def test_missing_pool_is_a_tool_error() -> None:
    mcp, _ = _build(None)
    async with Client(mcp) as client:
        with pytest.raises(Exception, match="Database pool is not available"):
            await client.call_tool("metric_baseline_get", {"metric_key": "hrv"})


async def test_registered_reads_remain_bound_to_each_daemons_pool() -> None:
    first, second = AsyncMock(), AsyncMock()
    first.fetch.return_value = [_row(center=56.0)]
    second.fetch.return_value = [_row(center=96.0)]
    mcp_first, _ = _build(first)
    mcp_second, _ = _build(second)
    first_read = await _call(mcp_first, metric_key="resting_hr")
    second_read = await _call(mcp_second, metric_key="resting_hr")
    assert first_read["center"] == 56.0
    assert second_read["center"] == 96.0
    first.fetch.assert_awaited_once()
    second.fetch.assert_awaited_once()
