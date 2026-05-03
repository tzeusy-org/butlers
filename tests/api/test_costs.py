"""Tests for cost, pricing, and schedule cost API endpoints.

Condensed: 22 → ~12 tests [bu-gg4y1].
Keeps: pricing config load (parametrized errors + tiered parse), pricing endpoint,
cost summary aggregation + tiered pricing + unreachable fallback, daily sorting,
by-schedule contract + zero-div guard.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.deps import (
    ButlerConnectionInfo,
    ButlerUnreachableError,
    MCPClientManager,
    get_butler_configs,
    get_mcp_manager,
    get_pricing,
)
from butlers.api.models import CostSummary, ScheduleCost
from butlers.api.pricing import (
    ModelPricing,
    PricingConfig,
    PricingError,
    PricingTier,
    TieredModelPricing,
    load_pricing,
)

pytestmark = pytest.mark.unit

_FLAT_TOML = """\
[models]
[models."claude-sonnet-4-5-20250929"]
input_price_per_token = 0.000003
output_price_per_token = 0.000015
[models."claude-haiku-35-20241022"]
input_price_per_token = 0.0000008
output_price_per_token = 0.000004
"""

_TIERED_TOML = """\
[models]
[models."flat-model"]
input_price_per_token = 0.000001
output_price_per_token = 0.000002
[models."gpt-5.4"]
[[models."gpt-5.4".tiers]]
context_threshold = 0
input_price_per_token = 0.0000025
cached_input_price_per_token = 0.00000025
output_price_per_token = 0.000015
[[models."gpt-5.4".tiers]]
context_threshold = 272000
input_price_per_token = 0.000005
cached_input_price_per_token = 0.0000005
output_price_per_token = 0.0000225
"""


def _flat_pricing():
    return PricingConfig(models={
        "claude-sonnet-4-20250514": ModelPricing(0.000003, 0.000015),
        "claude-haiku-35-20241022": ModelPricing(0.0000008, 0.000004),
    })


def _tiered_pricing():
    return PricingConfig(models={
        "gpt-5.4": TieredModelPricing(tiers=(
            PricingTier(0, 0.0000025, 0.000015, 0.00000025),
            PricingTier(272_000, 0.000005, 0.0000225, 0.0000005),
        )),
    })


def _make_tool_result(data: dict) -> MagicMock:
    item = MagicMock()
    item.text = json.dumps(data)
    result = MagicMock()
    result.content = [item]
    return result


def _mock_mgr(responses: dict) -> MCPClientManager:
    mgr = MagicMock(spec=MCPClientManager)

    async def _get(name: str):
        resp = responses.get(name)
        if isinstance(resp, Exception):
            raise resp
        c = MagicMock()
        c.call_tool = AsyncMock(return_value=resp)
        return c

    mgr.get_client = AsyncMock(side_effect=_get)
    return mgr


def _wire(app, mgr, configs, pricing):
    app.dependency_overrides[get_mcp_manager] = lambda: mgr
    app.dependency_overrides[get_butler_configs] = lambda: configs
    app.dependency_overrides[get_pricing] = lambda: pricing
    return app


# ---------------------------------------------------------------------------
# Pricing config loading
# ---------------------------------------------------------------------------


def test_load_pricing_flat_and_tiered(tmp_path):
    p = tmp_path / "pricing.toml"
    p.write_text(_FLAT_TOML)
    cfg = load_pricing(p)
    assert len(cfg.model_ids) == 2
    mp = cfg.get_model_pricing("claude-sonnet-4-5-20250929")
    assert mp.input_price_per_token == pytest.approx(0.000003)

    p2 = tmp_path / "tiered.toml"
    p2.write_text(_TIERED_TOML)
    cfg2 = load_pricing(p2)
    pricing = cfg2.get_model_pricing("gpt-5.4")
    assert isinstance(pricing, TieredModelPricing)
    assert len(pricing.tiers) == 2
    assert pricing.tiers[1].context_threshold == 272_000
    assert cfg2.get_model_pricing("nonexistent-model") is None


def test_load_pricing_missing_file_raises(tmp_path):
    with pytest.raises(PricingError, match="not found"):
        load_pricing(tmp_path / "nonexistent.toml")


@pytest.mark.parametrize("content,match", [
    ("[models\ngarbage!!!", "Invalid TOML"),
    ('[models]\n[models."m1"]\ninput_price_per_token = 0.001\n', "Missing required field"),
    ('[models]\n[models."m"]\ntiers = []\n', "non-empty array"),
])
def test_load_pricing_malformed_content_raises(tmp_path, content, match):
    p = tmp_path / "bad.toml"
    p.write_text(content)
    with pytest.raises(PricingError, match=match):
        load_pricing(p)


# ---------------------------------------------------------------------------
# GET /api/settings/pricing
# ---------------------------------------------------------------------------


async def test_pricing_endpoint_flat_and_tiered(app):
    config = PricingConfig({"claude-sonnet": ModelPricing(0.000003, 0.000015)})
    app.dependency_overrides[get_pricing] = lambda: config
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/settings/pricing")
    assert resp.status_code == 200
    entry = resp.json()["data"]["claude-sonnet"]
    assert entry["input_per_million"] == pytest.approx(3.0)
    assert entry["output_per_million"] == pytest.approx(15.0)


# ---------------------------------------------------------------------------
# GET /api/costs/summary
# ---------------------------------------------------------------------------


async def test_cost_summary_zero_butlers(app):
    _wire(app, MagicMock(spec=MCPClientManager), [], _flat_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/costs/summary")
    data = resp.json()["data"]
    assert data["total_cost_usd"] == 0.0
    CostSummary.model_validate(data)


async def test_cost_summary_aggregates_multiple_butlers(app):
    configs = [ButlerConnectionInfo(name="sw", port=41100), ButlerConnectionInfo(name="gen", port=41101)]
    sw_data = {"total_sessions": 5, "total_input_tokens": 10000, "total_output_tokens": 5000,
               "by_model": {"claude-sonnet-4-20250514": {"input_tokens": 10000, "output_tokens": 5000}}}
    gen_data = {"total_sessions": 3, "total_input_tokens": 8000, "total_output_tokens": 4000,
                "by_model": {"claude-haiku-35-20241022": {"input_tokens": 8000, "output_tokens": 4000}}}
    mgr = _mock_mgr({"sw": _make_tool_result(sw_data), "gen": _make_tool_result(gen_data)})
    _wire(app, mgr, configs, _flat_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/costs/summary")
    data = resp.json()["data"]
    assert data["total_sessions"] == 8
    assert data["total_cost_usd"] == pytest.approx(0.1274, abs=1e-4)


async def test_cost_summary_unreachable_butler_skipped(app):
    configs = [ButlerConnectionInfo(name="sw", port=41100), ButlerConnectionInfo(name="broken", port=41101)]
    sw_data = {"total_sessions": 2, "total_input_tokens": 1000, "total_output_tokens": 500,
               "by_model": {"claude-sonnet-4-20250514": {"input_tokens": 1000, "output_tokens": 500}}}
    mgr = _mock_mgr({"sw": _make_tool_result(sw_data), "broken": ButlerUnreachableError("broken")})
    _wire(app, mgr, configs, _flat_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/costs/summary")
    data = resp.json()["data"]
    assert data["total_sessions"] == 2
    assert "broken" not in data["by_butler"]


async def test_cost_summary_tiered_pricing(app):
    configs = [ButlerConnectionInfo(name="t", port=41100)]

    def _data(context: int):
        return {"total_sessions": 1, "total_input_tokens": 1_000_000, "total_output_tokens": 1_000_000,
                "by_model": {"gpt-5.4": {"input_tokens": 1_000_000, "output_tokens": 1_000_000,
                                         "cached_input_tokens": 0, "context_tokens": context}}}

    mgr = _mock_mgr({"t": _make_tool_result(_data(100_000))})
    _wire(app, mgr, configs, _tiered_pricing())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        resp_low = await c.get("/api/costs/summary")
    assert resp_low.json()["data"]["total_cost_usd"] == pytest.approx(17.50, abs=1e-4)

    mgr2 = _mock_mgr({"t": _make_tool_result(_data(300_000))})
    _wire(app, mgr2, configs, _tiered_pricing())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        resp_high = await c.get("/api/costs/summary")
    assert resp_high.json()["data"]["total_cost_usd"] == pytest.approx(27.50, abs=1e-4)


async def test_cost_summary_invalid_period_422(app):
    _wire(app, MagicMock(spec=MCPClientManager), [], _flat_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/costs/summary?period=90d")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/costs/daily
# ---------------------------------------------------------------------------


async def test_daily_costs_sorts_by_date(app):
    configs = [ButlerConnectionInfo(name="sw", port=41100)]
    daily_data = {"days": [
        {"date": "2026-02-10", "sessions": 1, "input_tokens": 100, "output_tokens": 50, "by_model": {}},
        {"date": "2026-02-08", "sessions": 2, "input_tokens": 200, "output_tokens": 100, "by_model": {}},
    ]}
    mgr = _mock_mgr({"sw": _make_tool_result(daily_data)})
    _wire(app, mgr, configs, _flat_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/costs/daily", params={"from": "2026-02-08", "to": "2026-02-10"})
    data = resp.json()["data"]
    assert [d["date"] for d in data] == ["2026-02-08", "2026-02-10"]


# ---------------------------------------------------------------------------
# GET /api/costs/by-schedule
# ---------------------------------------------------------------------------


async def test_by_schedule_contract_and_zero_division(app):
    configs = [ButlerConnectionInfo(name="sw", port=41100)]
    sched = {"name": "daily-report", "cron": "0 8 * * *", "model": "claude-sonnet-4-20250514",
             "total_runs": 30, "total_input_tokens": 30000, "total_output_tokens": 15000, "runs_per_day": 1.0}
    zero_sched = {**sched, "total_runs": 0, "total_input_tokens": 0, "total_output_tokens": 0}
    mgr = _mock_mgr({"sw": _make_tool_result({"schedules": [sched, zero_sched]})})
    _wire(app, mgr, configs, _flat_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/costs/by-schedule")
    assert resp.status_code == 200
    items = resp.json()["data"]
    real = next(i for i in items if i["schedule_name"] == "daily-report")
    zero = next(i for i in items if i["total_cost_usd"] == 0.0)
    assert real["total_cost_usd"] > 0
    ScheduleCost(**real)
    assert zero["avg_cost_per_run"] == 0.0
    assert zero["projected_monthly_usd"] == 0.0
