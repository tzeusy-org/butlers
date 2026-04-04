"""Tests for cost, pricing, and schedule cost API endpoints.

Condensed from test_costs.py (44), test_cost_comprehensive.py (9),
test_cost_schedule.py (9), test_pricing_endpoint.py (6), test_pricing.py (48)
→ ~25 tests (bu-egmz6).

Keeps: cost aggregation, tiered pricing, model validation, graceful fallback,
schedule endpoint, pricing endpoint, pricing config loading/parsing.
Removes: duplicate period/filter-accepted tests, trivial model-construct round-trips.
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _flat_pricing() -> PricingConfig:
    return PricingConfig(
        models={
            "claude-sonnet-4-20250514": ModelPricing(0.000003, 0.000015),
            "claude-haiku-35-20241022": ModelPricing(0.0000008, 0.000004),
        }
    )


def _tiered_pricing() -> PricingConfig:
    return PricingConfig(
        models={
            "gpt-5.4": TieredModelPricing(
                tiers=(
                    PricingTier(0, 0.0000025, 0.000015, 0.00000025),
                    PricingTier(272_000, 0.000005, 0.0000225, 0.0000005),
                )
            ),
        }
    )


def _make_tool_result(data: dict) -> MagicMock:
    item = MagicMock()
    item.text = json.dumps(data)
    result = MagicMock()
    result.content = [item]
    return result


def _make_empty_tool_result() -> MagicMock:
    result = MagicMock()
    result.content = []
    return result


def _mock_mgr_with(responses: dict[str, MagicMock | Exception]) -> MCPClientManager:
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


class TestLoadPricing:
    def test_loads_flat_models(self, tmp_path):
        p = tmp_path / "pricing.toml"
        p.write_text(_FLAT_TOML)
        cfg = load_pricing(p)
        assert len(cfg.model_ids) == 2
        mp = cfg.get_model_pricing("claude-sonnet-4-5-20250929")
        assert mp.input_price_per_token == pytest.approx(0.000003)

    def test_loads_tiered_model(self, tmp_path):
        p = tmp_path / "pricing.toml"
        p.write_text(_TIERED_TOML)
        cfg = load_pricing(p)
        pricing = cfg.get_model_pricing("gpt-5.4")
        assert isinstance(pricing, TieredModelPricing)
        assert len(pricing.tiers) == 2
        assert pricing.tiers[0].context_threshold == 0
        assert pricing.tiers[1].context_threshold == 272_000

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(PricingError, match="not found"):
            load_pricing(tmp_path / "nonexistent.toml")

    def test_corrupt_toml_raises(self, tmp_path):
        p = tmp_path / "bad.toml"
        p.write_text("[models\ngarbage!!!")
        with pytest.raises(PricingError, match="Invalid TOML"):
            load_pricing(p)

    def test_missing_price_field_raises(self, tmp_path):
        p = tmp_path / "partial.toml"
        p.write_text('[models]\n[models."m1"]\ninput_price_per_token = 0.001\n')
        with pytest.raises(PricingError, match="Missing required field"):
            load_pricing(p)

    def test_unknown_model_returns_none(self, tmp_path):
        p = tmp_path / "pricing.toml"
        p.write_text(_FLAT_TOML)
        cfg = load_pricing(p)
        assert cfg.get_model_pricing("nonexistent-model") is None

    def test_empty_tiers_raises(self, tmp_path):
        p = tmp_path / "pricing.toml"
        p.write_text('[models]\n[models."m"]\ntiers = []\n')
        with pytest.raises(PricingError, match="non-empty array"):
            load_pricing(p)

    def test_tiered_cached_input_defaults_to_zero(self, tmp_path):
        p = tmp_path / "pricing.toml"
        p.write_text(
            '[models]\n[models."m"]\n'
            '[[models."m".tiers]]\n'
            "context_threshold = 0\n"
            "input_price_per_token = 0.001\n"
            "output_price_per_token = 0.002\n"
        )
        cfg = load_pricing(p)
        assert cfg.get_model_pricing("m").tiers[0].cached_input_price_per_token == 0.0


# ---------------------------------------------------------------------------
# GET /api/settings/pricing
# ---------------------------------------------------------------------------


class TestPricingEndpoint:
    async def test_flat_model_returns_per_million_prices(self, app):
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

    async def test_tiered_model_returns_base_tier(self, app):
        config = PricingConfig(
            {
                "gpt-5.4": TieredModelPricing(
                    tiers=(
                        PricingTier(0, 0.0000025, 0.000015),
                        PricingTier(272_000, 0.000005, 0.0000225),
                    )
                )
            }
        )
        app.dependency_overrides[get_pricing] = lambda: config
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/settings/pricing")
        assert resp.status_code == 200
        entry = resp.json()["data"]["gpt-5.4"]
        assert entry["input_per_million"] == pytest.approx(2.5)

    async def test_empty_pricing_config(self, app):
        app.dependency_overrides[get_pricing] = lambda: PricingConfig({})
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/settings/pricing")
        assert resp.status_code == 200
        assert resp.json()["data"] == {}


# ---------------------------------------------------------------------------
# GET /api/costs/summary
# ---------------------------------------------------------------------------


class TestCostSummary:
    async def test_zero_butlers_returns_zeroed_summary(self, app):
        mgr = MagicMock(spec=MCPClientManager)
        _wire(app, mgr, [], _flat_pricing())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/costs/summary")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total_cost_usd"] == 0.0
        assert data["total_sessions"] == 0
        CostSummary.model_validate(data)

    async def test_aggregates_multiple_butlers(self, app):
        configs = [
            ButlerConnectionInfo(name="sw", port=41100),
            ButlerConnectionInfo(name="gen", port=41101),
        ]
        sw_data = {
            "total_sessions": 5,
            "total_input_tokens": 10000,
            "total_output_tokens": 5000,
            "by_model": {
                "claude-sonnet-4-20250514": {"input_tokens": 10000, "output_tokens": 5000}
            },
        }
        gen_data = {
            "total_sessions": 3,
            "total_input_tokens": 8000,
            "total_output_tokens": 4000,
            "by_model": {"claude-haiku-35-20241022": {"input_tokens": 8000, "output_tokens": 4000}},
        }
        mgr = _mock_mgr_with({"sw": _make_tool_result(sw_data), "gen": _make_tool_result(gen_data)})
        _wire(app, mgr, configs, _flat_pricing())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/costs/summary")
        data = resp.json()["data"]
        assert data["total_sessions"] == 8
        assert data["total_input_tokens"] == 18000
        # sw: 10000*0.000003+5000*0.000015=0.105; gen: 8000*0.0000008+4000*0.000004=0.0224
        assert data["total_cost_usd"] == pytest.approx(0.1274, abs=1e-4)

    async def test_unreachable_butler_skipped(self, app):
        configs = [
            ButlerConnectionInfo(name="sw", port=41100),
            ButlerConnectionInfo(name="broken", port=41101),
        ]
        sw_data = {
            "total_sessions": 2,
            "total_input_tokens": 1000,
            "total_output_tokens": 500,
            "by_model": {"claude-sonnet-4-20250514": {"input_tokens": 1000, "output_tokens": 500}},
        }
        mgr = _mock_mgr_with(
            {"sw": _make_tool_result(sw_data), "broken": ButlerUnreachableError("broken")}
        )
        _wire(app, mgr, configs, _flat_pricing())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/costs/summary")
        data = resp.json()["data"]
        assert data["total_sessions"] == 2
        assert "broken" not in data["by_butler"]

    async def test_tiered_pricing_low_vs_high_tier(self, app):
        """Low tier applies when context < 272k; high tier when context >= 272k."""
        configs = [ButlerConnectionInfo(name="t", port=41100)]

        def _data(context_tokens: int) -> dict:
            return {
                "total_sessions": 1,
                "total_input_tokens": 1_000_000,
                "total_output_tokens": 1_000_000,
                "by_model": {
                    "gpt-5.4": {
                        "input_tokens": 1_000_000,
                        "output_tokens": 1_000_000,
                        "cached_input_tokens": 0,
                        "context_tokens": context_tokens,
                    }
                },
            }

        # Low tier: 1M*$2.5/1M + 1M*$15/1M = $17.50
        mgr = _mock_mgr_with({"t": _make_tool_result(_data(100_000))})
        _wire(app, mgr, configs, _tiered_pricing())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp_low = await client.get("/api/costs/summary")
        assert resp_low.json()["data"]["total_cost_usd"] == pytest.approx(17.50, abs=1e-4)

        # High tier: 1M*$5/1M + 1M*$22.5/1M = $27.50
        mgr2 = _mock_mgr_with({"t": _make_tool_result(_data(300_000))})
        _wire(app, mgr2, configs, _tiered_pricing())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp_high = await client.get("/api/costs/summary")
        assert resp_high.json()["data"]["total_cost_usd"] == pytest.approx(27.50, abs=1e-4)

    async def test_invalid_period_returns_422(self, app):
        mgr = MagicMock(spec=MCPClientManager)
        _wire(app, mgr, [], _flat_pricing())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/costs/summary?period=90d")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/costs/daily
# ---------------------------------------------------------------------------


class TestDailyCosts:
    async def test_empty_butlers_returns_empty_list(self, app):
        mgr = MagicMock(spec=MCPClientManager)
        _wire(app, mgr, [], _flat_pricing())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/costs/daily")
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    async def test_aggregates_and_sorts_by_date(self, app):
        configs = [ButlerConnectionInfo(name="sw", port=41100)]
        daily_data = {
            "days": [
                {
                    "date": "2026-02-10",
                    "sessions": 1,
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "by_model": {},
                },
                {
                    "date": "2026-02-08",
                    "sessions": 2,
                    "input_tokens": 200,
                    "output_tokens": 100,
                    "by_model": {},
                },
                {
                    "date": "2026-02-09",
                    "sessions": 3,
                    "input_tokens": 300,
                    "output_tokens": 150,
                    "by_model": {},
                },
            ]
        }
        mgr = _mock_mgr_with({"sw": _make_tool_result(daily_data)})
        _wire(app, mgr, configs, _flat_pricing())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/costs/daily", params={"from": "2026-02-08", "to": "2026-02-10"}
            )
        data = resp.json()["data"]
        assert [d["date"] for d in data] == ["2026-02-08", "2026-02-09", "2026-02-10"]


# ---------------------------------------------------------------------------
# GET /api/costs/by-schedule
# ---------------------------------------------------------------------------


class TestBySchedule:
    def _schedule_data(self, **kw) -> dict:
        defaults = {
            "name": "daily-report",
            "cron": "0 8 * * *",
            "model": "claude-sonnet-4-20250514",
            "total_runs": 30,
            "total_input_tokens": 30000,
            "total_output_tokens": 15000,
            "runs_per_day": 1.0,
        }
        defaults.update(kw)
        return {"schedules": [defaults]}

    async def test_returns_schedule_cost_fields(self, app):
        configs = [ButlerConnectionInfo(name="sw", port=41100)]
        mgr = _mock_mgr_with({"sw": _make_tool_result(self._schedule_data())})
        _wire(app, mgr, configs, _flat_pricing())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/costs/by-schedule")
        assert resp.status_code == 200
        item = resp.json()["data"][0]
        assert item["schedule_name"] == "daily-report"
        assert item["butler"] == "sw"
        assert item["total_cost_usd"] > 0
        ScheduleCost(**item)

    async def test_zero_runs_avoids_division_by_zero(self, app):
        configs = [ButlerConnectionInfo(name="sw", port=41100)]
        mgr = _mock_mgr_with(
            {
                "sw": _make_tool_result(
                    self._schedule_data(total_runs=0, total_input_tokens=0, total_output_tokens=0)
                )
            }
        )
        _wire(app, mgr, configs, _flat_pricing())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/costs/by-schedule")
        item = resp.json()["data"][0]
        assert item["avg_cost_per_run"] == 0.0
        assert item["projected_monthly_usd"] == 0.0

    async def test_sorted_by_projected_cost_descending(self, app):
        configs = [
            ButlerConnectionInfo(name="a", port=41100),
            ButlerConnectionInfo(name="b", port=41101),
        ]
        cheap = {
            "schedules": [
                {
                    "name": "cheap",
                    "cron": "0 8 * * *",
                    "model": "claude-sonnet-4-20250514",
                    "total_runs": 10,
                    "total_input_tokens": 1000,
                    "total_output_tokens": 500,
                    "runs_per_day": 0.5,
                }
            ]
        }
        expensive = {
            "schedules": [
                {
                    "name": "expensive",
                    "cron": "0 8 * * *",
                    "model": "claude-sonnet-4-20250514",
                    "total_runs": 100,
                    "total_input_tokens": 100000,
                    "total_output_tokens": 50000,
                    "runs_per_day": 5.0,
                }
            ]
        }

        async def _get(name: str):
            c = MagicMock()
            c.call_tool = AsyncMock(
                return_value=_make_tool_result(cheap if name == "a" else expensive)
            )
            return c

        mgr = MagicMock(spec=MCPClientManager)
        mgr.get_client = AsyncMock(side_effect=_get)
        _wire(app, mgr, configs, _flat_pricing())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/costs/by-schedule")
        data = resp.json()["data"]
        assert data[0]["schedule_name"] == "expensive"
        assert data[0]["projected_monthly_usd"] >= data[1]["projected_monthly_usd"]

    async def test_unreachable_butler_returns_empty(self, app):
        configs = [ButlerConnectionInfo(name="broken", port=41100)]
        mgr = _mock_mgr_with({"broken": ButlerUnreachableError("broken")})
        _wire(app, mgr, configs, _flat_pricing())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/costs/by-schedule")
        assert resp.status_code == 200
        assert resp.json()["data"] == []
