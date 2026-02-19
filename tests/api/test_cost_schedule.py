"""Tests for GET /api/costs/by-schedule — per-schedule cost analysis endpoint.

Verifies fan-out to butlers, cost estimation, sorting, graceful fallback
on unreachable/timeout butlers, and edge cases like zero runs.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.deps import (
    ButlerConnectionInfo,
    ButlerUnreachableError,
    MCPClientManager,
    get_butler_configs,
    get_mcp_manager,
    get_pricing,
)
from butlers.api.models import ScheduleCost
from butlers.api.pricing import ModelPricing, PricingConfig

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_configs() -> list[ButlerConnectionInfo]:
    """Return a small set of butler configs for testing."""
    return [
        ButlerConnectionInfo(name="switchboard", port=40100, description="Routes messages"),
        ButlerConnectionInfo(name="general", port=40101, description="Catch-all assistant"),
    ]


def _make_pricing() -> PricingConfig:
    """Create a PricingConfig with known per-token prices."""
    return PricingConfig(
        models={
            "claude-sonnet-4-20250514": ModelPricing(
                input_price_per_token=0.000003,
                output_price_per_token=0.000015,
            ),
            "claude-haiku-35-20241022": ModelPricing(
                input_price_per_token=0.0000008,
                output_price_per_token=0.000004,
            ),
        }
    )


def _make_tool_result(data: dict) -> MagicMock:
    """Create a mock MCP tool result with JSON text content."""
    content_item = MagicMock()
    content_item.text = json.dumps(data)
    result = MagicMock()
    result.content = [content_item]
    return result


def _make_schedule_data(
    name: str = "daily-report",
    cron: str = "0 8 * * *",
    model: str = "claude-sonnet-4-20250514",
    total_runs: int = 30,
    total_input_tokens: int = 30000,
    total_output_tokens: int = 15000,
    runs_per_day: float = 1.0,
) -> dict:
    """Create a single schedule entry as returned by the ``schedule_costs`` MCP tool."""
    return {
        "name": name,
        "cron": cron,
        "model": model,
        "total_runs": total_runs,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "runs_per_day": runs_per_day,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_empty_butlers_returns_empty_list():
    """No butlers configured → empty list."""
    app = create_app()
    mock_mgr = MagicMock(spec=MCPClientManager)

    app.dependency_overrides[get_mcp_manager] = lambda: mock_mgr
    app.dependency_overrides[get_butler_configs] = lambda: []
    app.dependency_overrides[get_pricing] = _make_pricing

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/costs/by-schedule")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []


async def test_single_butler_returns_schedule_costs():
    """Single butler with schedule data returns ScheduleCost items."""
    app = create_app()
    mock_mgr = MagicMock(spec=MCPClientManager)

    schedule_data = {
        "schedules": [
            _make_schedule_data(
                name="daily-report",
                total_runs=30,
                total_input_tokens=30000,
                total_output_tokens=15000,
                runs_per_day=1.0,
            ),
        ]
    }

    mock_client = AsyncMock()
    mock_client.call_tool = AsyncMock(return_value=_make_tool_result(schedule_data))
    mock_mgr.get_client = AsyncMock(return_value=mock_client)

    configs = [ButlerConnectionInfo(name="switchboard", port=40100)]
    app.dependency_overrides[get_mcp_manager] = lambda: mock_mgr
    app.dependency_overrides[get_butler_configs] = lambda: configs
    app.dependency_overrides[get_pricing] = _make_pricing

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/costs/by-schedule")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) == 1

    item = body["data"][0]
    assert item["schedule_name"] == "daily-report"
    assert item["butler"] == "switchboard"
    assert item["cron"] == "0 8 * * *"
    assert item["total_runs"] == 30
    assert item["total_cost_usd"] > 0
    assert item["avg_cost_per_run"] > 0
    assert item["runs_per_day"] == 1.0
    assert item["projected_monthly_usd"] > 0


async def test_multiple_butlers_merged_and_sorted():
    """Multiple butlers' schedules are merged and sorted by projected cost descending."""
    app = create_app()
    mock_mgr = MagicMock(spec=MCPClientManager)

    # Butler A has a cheap schedule
    schedule_a = {
        "schedules": [
            _make_schedule_data(
                name="cheap-task",
                total_runs=10,
                total_input_tokens=1000,
                total_output_tokens=500,
                runs_per_day=0.5,
            ),
        ]
    }
    # Butler B has an expensive schedule
    schedule_b = {
        "schedules": [
            _make_schedule_data(
                name="expensive-task",
                total_runs=100,
                total_input_tokens=100000,
                total_output_tokens=50000,
                runs_per_day=5.0,
            ),
        ]
    }

    client_a = AsyncMock()
    client_a.call_tool = AsyncMock(return_value=_make_tool_result(schedule_a))
    client_b = AsyncMock()
    client_b.call_tool = AsyncMock(return_value=_make_tool_result(schedule_b))

    async def get_client(name: str):
        return client_a if name == "butler-a" else client_b

    mock_mgr.get_client = AsyncMock(side_effect=get_client)

    configs = [
        ButlerConnectionInfo(name="butler-a", port=40100),
        ButlerConnectionInfo(name="butler-b", port=40101),
    ]
    app.dependency_overrides[get_mcp_manager] = lambda: mock_mgr
    app.dependency_overrides[get_butler_configs] = lambda: configs
    app.dependency_overrides[get_pricing] = _make_pricing

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/costs/by-schedule")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) == 2
    # Sorted by projected_monthly_usd descending
    assert body["data"][0]["schedule_name"] == "expensive-task"
    assert body["data"][1]["schedule_name"] == "cheap-task"
    assert body["data"][0]["projected_monthly_usd"] >= body["data"][1]["projected_monthly_usd"]


async def test_butler_unreachable_returns_empty():
    """Unreachable butler is gracefully skipped, returning empty list."""
    app = create_app()
    mock_mgr = MagicMock(spec=MCPClientManager)
    mock_mgr.get_client = AsyncMock(side_effect=ButlerUnreachableError("switchboard"))

    configs = [ButlerConnectionInfo(name="switchboard", port=40100)]
    app.dependency_overrides[get_mcp_manager] = lambda: mock_mgr
    app.dependency_overrides[get_butler_configs] = lambda: configs
    app.dependency_overrides[get_pricing] = _make_pricing

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/costs/by-schedule")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []


async def test_timeout_returns_empty():
    """Butler timeout is gracefully handled."""
    app = create_app()
    mock_mgr = MagicMock(spec=MCPClientManager)
    mock_mgr.get_client = AsyncMock(side_effect=TimeoutError)

    configs = [ButlerConnectionInfo(name="switchboard", port=40100)]
    app.dependency_overrides[get_mcp_manager] = lambda: mock_mgr
    app.dependency_overrides[get_butler_configs] = lambda: configs
    app.dependency_overrides[get_pricing] = _make_pricing

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/costs/by-schedule")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []


async def test_zero_runs_avoids_division_by_zero():
    """A schedule with zero runs should have zero avg/projected cost."""
    app = create_app()
    mock_mgr = MagicMock(spec=MCPClientManager)

    schedule_data = {
        "schedules": [
            _make_schedule_data(
                name="new-task",
                total_runs=0,
                total_input_tokens=0,
                total_output_tokens=0,
                runs_per_day=1.0,
            ),
        ]
    }

    mock_client = AsyncMock()
    mock_client.call_tool = AsyncMock(return_value=_make_tool_result(schedule_data))
    mock_mgr.get_client = AsyncMock(return_value=mock_client)

    configs = [ButlerConnectionInfo(name="switchboard", port=40100)]
    app.dependency_overrides[get_mcp_manager] = lambda: mock_mgr
    app.dependency_overrides[get_butler_configs] = lambda: configs
    app.dependency_overrides[get_pricing] = _make_pricing

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/costs/by-schedule")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) == 1
    item = body["data"][0]
    assert item["avg_cost_per_run"] == 0.0
    assert item["projected_monthly_usd"] == 0.0
    assert item["total_cost_usd"] == 0.0


async def test_cost_estimation_uses_pricing_config():
    """Cost estimation correctly applies per-token pricing from the config."""
    app = create_app()
    mock_mgr = MagicMock(spec=MCPClientManager)

    # 1000 input tokens * 0.000003 + 500 output tokens * 0.000015 = 0.003 + 0.0075 = 0.0105
    schedule_data = {
        "schedules": [
            _make_schedule_data(
                name="priced-task",
                model="claude-sonnet-4-20250514",
                total_runs=1,
                total_input_tokens=1000,
                total_output_tokens=500,
                runs_per_day=1.0,
            ),
        ]
    }

    mock_client = AsyncMock()
    mock_client.call_tool = AsyncMock(return_value=_make_tool_result(schedule_data))
    mock_mgr.get_client = AsyncMock(return_value=mock_client)

    configs = [ButlerConnectionInfo(name="switchboard", port=40100)]
    app.dependency_overrides[get_mcp_manager] = lambda: mock_mgr
    app.dependency_overrides[get_butler_configs] = lambda: configs
    app.dependency_overrides[get_pricing] = _make_pricing

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/costs/by-schedule")

    assert resp.status_code == 200
    body = resp.json()
    item = body["data"][0]
    # total_cost = 1000 * 0.000003 + 500 * 0.000015 = 0.003 + 0.0075 = 0.0105
    assert item["total_cost_usd"] == pytest.approx(0.0105, abs=1e-6)
    assert item["avg_cost_per_run"] == pytest.approx(0.0105, abs=1e-6)
    # projected = avg * runs_per_day * 30 = 0.0105 * 1.0 * 30 = 0.315
    assert item["projected_monthly_usd"] == pytest.approx(0.315, abs=1e-4)


async def test_response_model_validation():
    """Returned data matches the ScheduleCost schema."""
    app = create_app()
    mock_mgr = MagicMock(spec=MCPClientManager)

    schedule_data = {
        "schedules": [
            _make_schedule_data(
                name="valid-task",
                cron="*/10 * * * *",
                total_runs=5,
                total_input_tokens=5000,
                total_output_tokens=2500,
                runs_per_day=6.0,
            ),
        ]
    }

    mock_client = AsyncMock()
    mock_client.call_tool = AsyncMock(return_value=_make_tool_result(schedule_data))
    mock_mgr.get_client = AsyncMock(return_value=mock_client)

    configs = [ButlerConnectionInfo(name="general", port=40101)]
    app.dependency_overrides[get_mcp_manager] = lambda: mock_mgr
    app.dependency_overrides[get_butler_configs] = lambda: configs
    app.dependency_overrides[get_pricing] = _make_pricing

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/costs/by-schedule")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) == 1
    item = body["data"][0]

    # Validate all expected fields exist and have correct types
    sc = ScheduleCost(**item)
    assert sc.schedule_name == "valid-task"
    assert sc.butler == "general"
    assert sc.cron == "*/10 * * * *"
    assert sc.total_runs == 5
    assert isinstance(sc.total_cost_usd, float)
    assert isinstance(sc.avg_cost_per_run, float)
    assert sc.runs_per_day == 6.0
    assert isinstance(sc.projected_monthly_usd, float)

    # Verify meta is present in response
    assert "meta" in body


async def test_unknown_model_costs_zero():
    """Schedule with an unknown model ID yields zero cost."""
    app = create_app()
    mock_mgr = MagicMock(spec=MCPClientManager)

    schedule_data = {
        "schedules": [
            _make_schedule_data(
                name="unknown-model-task",
                model="gpt-999-turbo",
                total_runs=10,
                total_input_tokens=10000,
                total_output_tokens=5000,
                runs_per_day=2.0,
            ),
        ]
    }

    mock_client = AsyncMock()
    mock_client.call_tool = AsyncMock(return_value=_make_tool_result(schedule_data))
    mock_mgr.get_client = AsyncMock(return_value=mock_client)

    configs = [ButlerConnectionInfo(name="switchboard", port=40100)]
    app.dependency_overrides[get_mcp_manager] = lambda: mock_mgr
    app.dependency_overrides[get_butler_configs] = lambda: configs
    app.dependency_overrides[get_pricing] = _make_pricing

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/costs/by-schedule")

    assert resp.status_code == 200
    body = resp.json()
    item = body["data"][0]
    assert item["total_cost_usd"] == 0.0
    assert item["avg_cost_per_run"] == 0.0
    assert item["projected_monthly_usd"] == 0.0
