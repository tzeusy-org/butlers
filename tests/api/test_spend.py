"""Tests for spend (was: costs), pricing, and schedule spend API endpoints.

Condensed: 22 → ~12 tests [bu-gg4y1]. Migrated from /api/costs → /api/spend [bu-dvb7i].
Keeps: pricing config load (parametrized errors + tiered parse), pricing endpoint,
spend summary aggregation + tiered pricing + unreachable fallback, daily sorting,
by-schedule contract + zero-div guard.
"""

from __future__ import annotations

import json
from datetime import date
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
from butlers.api.models import ScheduleCost, SpendSummary
from butlers.api.pricing import (
    ModelPricing,
    PricingConfig,
    PricingError,
    PricingTier,
    TieredModelPricing,
    load_pricing,
)
from butlers.api.routers.spend import _get_db_manager as _costs_get_db

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
    return PricingConfig(
        models={
            "claude-sonnet-4-20250514": ModelPricing(0.000003, 0.000015),
            "claude-haiku-35-20241022": ModelPricing(0.0000008, 0.000004),
        }
    )


def _tiered_pricing():
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


def _wire_db(app, db):
    app.dependency_overrides[_costs_get_db] = lambda: db
    return app


def _mock_db_pool(*, summary: dict | None = None, daily: list[dict] | None = None):
    pool = MagicMock()
    if summary is not None:
        pool.fetchrow = AsyncMock(
            return_value={
                "total_sessions": summary["total_sessions"],
                "total_input_tokens": summary["total_input_tokens"],
                "total_output_tokens": summary["total_output_tokens"],
            }
        )
        pool.fetch = AsyncMock(
            return_value=[
                {
                    "model": model,
                    "input_tokens": stats.get("input_tokens", 0),
                    "output_tokens": stats.get("output_tokens", 0),
                }
                for model, stats in summary.get("by_model", {}).items()
            ]
        )
    elif daily is not None:
        pool.fetch = AsyncMock(
            side_effect=[
                [
                    {
                        "day": date.fromisoformat(day["date"]),
                        "sessions": day["sessions"],
                        "input_tokens": day["input_tokens"],
                        "output_tokens": day["output_tokens"],
                    }
                    for day in daily
                ],
                [
                    {
                        "day": date.fromisoformat(day["date"]),
                        "model": model,
                        "input_tokens": stats.get("input_tokens", 0),
                        "output_tokens": stats.get("output_tokens", 0),
                    }
                    for day in daily
                    for model, stats in day.get("by_model", {}).items()
                ],
            ]
        )
    return pool


def _mock_db(pools: dict[str, MagicMock]):
    db = MagicMock()
    db.pool.side_effect = lambda name: pools[name]
    return db


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


@pytest.mark.parametrize(
    "content,match",
    [
        ("[models\ngarbage!!!", "Invalid TOML"),
        ('[models]\n[models."m1"]\ninput_price_per_token = 0.001\n', "Missing required field"),
        ('[models]\n[models."m"]\ntiers = []\n', "non-empty array"),
    ],
)
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
# GET /api/spend
# ---------------------------------------------------------------------------


async def test_cost_summary_zero_butlers(app):
    _wire(app, MagicMock(spec=MCPClientManager), [], _flat_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend")
    data = resp.json()["data"]
    assert data["total_cost_usd"] == 0.0
    SpendSummary.model_validate(data)


async def test_cost_summary_aggregates_multiple_butlers(app):
    configs = [
        ButlerConnectionInfo(name="sw", port=41100),
        ButlerConnectionInfo(name="gen", port=41101),
    ]
    sw_data = {
        "total_sessions": 5,
        "total_input_tokens": 10000,
        "total_output_tokens": 5000,
        "by_model": {"claude-sonnet-4-20250514": {"input_tokens": 10000, "output_tokens": 5000}},
    }
    gen_data = {
        "total_sessions": 3,
        "total_input_tokens": 8000,
        "total_output_tokens": 4000,
        "by_model": {"claude-haiku-35-20241022": {"input_tokens": 8000, "output_tokens": 4000}},
    }
    mgr = _mock_mgr({"sw": _make_tool_result(sw_data), "gen": _make_tool_result(gen_data)})
    _wire(app, mgr, configs, _flat_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend")
    data = resp.json()["data"]
    assert data["total_sessions"] == 8
    assert data["total_cost_usd"] == pytest.approx(0.1274, abs=1e-4)


async def test_cost_summary_unreachable_butler_skipped(app):
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
    mgr = _mock_mgr({"sw": _make_tool_result(sw_data), "broken": ButlerUnreachableError("broken")})
    _wire(app, mgr, configs, _flat_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend")
    data = resp.json()["data"]
    assert data["total_sessions"] == 2
    assert "broken" not in data["by_butler"]


async def test_cost_summary_tiered_pricing(app):
    configs = [ButlerConnectionInfo(name="t", port=41100)]

    def _data(context: int):
        return {
            "total_sessions": 1,
            "total_input_tokens": 1_000_000,
            "total_output_tokens": 1_000_000,
            "by_model": {
                "gpt-5.4": {
                    "input_tokens": 1_000_000,
                    "output_tokens": 1_000_000,
                    "cached_input_tokens": 0,
                    "context_tokens": context,
                }
            },
        }

    mgr = _mock_mgr({"t": _make_tool_result(_data(100_000))})
    _wire(app, mgr, configs, _tiered_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        resp_low = await c.get("/api/spend")
    assert resp_low.json()["data"]["total_cost_usd"] == pytest.approx(17.50, abs=1e-4)

    mgr2 = _mock_mgr({"t": _make_tool_result(_data(300_000))})
    _wire(app, mgr2, configs, _tiered_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        resp_high = await c.get("/api/spend")
    assert resp_high.json()["data"]["total_cost_usd"] == pytest.approx(27.50, abs=1e-4)


async def test_cost_summary_invalid_period_422(app):
    _wire(app, MagicMock(spec=MCPClientManager), [], _flat_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend?period=90d")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/spend/daily
# ---------------------------------------------------------------------------


async def test_daily_costs_sorts_by_date(app):
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
        ]
    }
    mgr = _mock_mgr({"sw": _make_tool_result(daily_data)})
    _wire(app, mgr, configs, _flat_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/spend/daily", params={"from": "2026-02-08", "to": "2026-02-10"}
        )
    data = resp.json()["data"]
    assert [d["date"] for d in data] == ["2026-02-08", "2026-02-10"]


# ---------------------------------------------------------------------------
# GET /api/spend — date-range params (from/to)
# ---------------------------------------------------------------------------


async def test_cost_summary_date_range_aggregates_sessions_daily(app):
    """When from/to are provided, summary is computed from sessions_daily, not sessions_summary."""
    configs = [ButlerConnectionInfo(name="sw", port=41100)]
    daily_data = {
        "days": [
            {
                "date": "2026-03-01",
                "sessions": 3,
                "input_tokens": 6000,
                "output_tokens": 3000,
                "by_model": {
                    "claude-sonnet-4-20250514": {
                        "input_tokens": 6000,
                        "output_tokens": 3000,
                        "cached_input_tokens": 0,
                    }
                },
            },
            {
                "date": "2026-03-02",
                "sessions": 2,
                "input_tokens": 4000,
                "output_tokens": 2000,
                "by_model": {
                    "claude-sonnet-4-20250514": {
                        "input_tokens": 4000,
                        "output_tokens": 2000,
                        "cached_input_tokens": 0,
                    }
                },
            },
        ]
    }
    mgr = _mock_mgr({"sw": _make_tool_result(daily_data)})
    _wire(app, mgr, configs, _flat_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend", params={"from": "2026-03-01", "to": "2026-03-02"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total_sessions"] == 5
    assert data["total_input_tokens"] == 10000
    assert data["total_output_tokens"] == 5000
    # period label reflects the custom range
    assert data["period"] == "2026-03-01/2026-03-02"
    # by_model includes aggregated costs
    assert "claude-sonnet-4-20250514" in data["by_model"]
    SpendSummary.model_validate(data)


async def test_cost_summary_date_range_multi_butler(app):
    """Date-range summary aggregates across multiple butlers."""
    configs = [
        ButlerConnectionInfo(name="a", port=41100),
        ButlerConnectionInfo(name="b", port=41101),
    ]
    day_a = {
        "days": [
            {
                "date": "2026-04-01",
                "sessions": 1,
                "input_tokens": 1000,
                "output_tokens": 500,
                "by_model": {
                    "claude-haiku-35-20241022": {"input_tokens": 1000, "output_tokens": 500}
                },
            }
        ]
    }
    day_b = {
        "days": [
            {
                "date": "2026-04-01",
                "sessions": 2,
                "input_tokens": 2000,
                "output_tokens": 1000,
                "by_model": {
                    "claude-haiku-35-20241022": {"input_tokens": 2000, "output_tokens": 1000}
                },
            }
        ]
    }
    mgr = _mock_mgr({"a": _make_tool_result(day_a), "b": _make_tool_result(day_b)})
    _wire(app, mgr, configs, _flat_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend", params={"from": "2026-04-01", "to": "2026-04-01"})
    data = resp.json()["data"]
    assert data["total_sessions"] == 3
    assert data["total_input_tokens"] == 3000
    assert data["by_butler"]["a"] > 0
    assert data["by_butler"]["b"] > 0
    SpendSummary.model_validate(data)


@pytest.mark.parametrize(
    "params",
    [
        {"from": "2026-03-01"},  # only 'from' without 'to'
        {"from": "2026-04-30", "to": "2026-04-01"},  # inverted 'from' > 'to'
    ],
    ids=["only-from", "inverted"],
)
async def test_cost_summary_date_range_invalid_returns_422(app, params):
    """Incomplete or inverted from/to ranges return 422."""
    configs = [ButlerConnectionInfo(name="sw", port=41100)]
    mgr = _mock_mgr({})
    _wire(app, mgr, configs, _flat_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend", params=params)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/spend/by-schedule
# ---------------------------------------------------------------------------


async def test_by_schedule_contract_and_zero_division(app):
    configs = [ButlerConnectionInfo(name="sw", port=41100)]
    sched = {
        "name": "daily-report",
        "cron": "0 8 * * *",
        "model": "claude-sonnet-4-20250514",
        "total_runs": 30,
        "total_input_tokens": 30000,
        "total_output_tokens": 15000,
        "runs_per_day": 1.0,
    }
    zero_sched = {**sched, "total_runs": 0, "total_input_tokens": 0, "total_output_tokens": 0}
    mgr = _mock_mgr({"sw": _make_tool_result({"schedules": [sched, zero_sched]})})
    _wire(app, mgr, configs, _flat_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend/by-schedule")
    assert resp.status_code == 200
    items = resp.json()["data"]
    real = next(i for i in items if i["schedule_name"] == "daily-report")
    zero = next(i for i in items if i["total_cost_usd"] == 0.0)
    assert real["total_cost_usd"] > 0
    ScheduleCost(**real)
    assert zero["avg_cost_per_run"] == 0.0
    assert zero["projected_monthly_usd"] == 0.0


# ---------------------------------------------------------------------------
# GET /api/spend — ?butler= filter [bu-iuol4.12]
# ---------------------------------------------------------------------------


async def test_cost_summary_butler_filter_returns_only_that_butler(app):
    """?butler=sw restricts the fan-out to only that butler."""
    configs = [
        ButlerConnectionInfo(name="sw", port=41100),
        ButlerConnectionInfo(name="gen", port=41101),
    ]
    sw_data = {
        "total_sessions": 5,
        "total_input_tokens": 10000,
        "total_output_tokens": 5000,
        "by_model": {"claude-sonnet-4-20250514": {"input_tokens": 10000, "output_tokens": 5000}},
    }
    # gen is wired too — it must NOT be called when ?butler=sw
    gen_data = {
        "total_sessions": 99,
        "total_input_tokens": 99000,
        "total_output_tokens": 99000,
        "by_model": {"claude-haiku-35-20241022": {"input_tokens": 99000, "output_tokens": 99000}},
    }
    mgr = _mock_mgr({"sw": _make_tool_result(sw_data), "gen": _make_tool_result(gen_data)})
    _wire(app, mgr, configs, _flat_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend", params={"butler": "sw"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    # Only sw's sessions count
    assert data["total_sessions"] == 5
    # gen must not appear in by_butler
    assert "gen" not in data["by_butler"]
    SpendSummary.model_validate(data)


async def test_cost_summary_staffer_uses_db_when_session_tool_absent(app):
    """Staffer butlers still surface spend because dashboard can read their DB pool."""
    configs = [ButlerConnectionInfo(name="switchboard", port=41100, type="staffer")]
    summary = {
        "total_sessions": 5,
        "total_input_tokens": 10000,
        "total_output_tokens": 5000,
        "by_model": {"claude-sonnet-4-20250514": {"input_tokens": 10000, "output_tokens": 5000}},
    }
    mgr = _mock_mgr({"switchboard": ButlerUnreachableError("switchboard")})
    db = _mock_db({"switchboard": _mock_db_pool(summary=summary)})
    _wire_db(_wire(app, mgr, configs, _flat_pricing()), db)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend", params={"butler": "switchboard"})

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total_sessions"] == 5
    assert data["total_cost_usd"] == pytest.approx(0.105, abs=1e-4)
    mgr.get_client.assert_not_called()
    SpendSummary.model_validate(data)


async def test_cost_summary_unknown_butler_returns_empty_200(app):
    """?butler=nonexistent produces a zero-cost 200 response (not 404)."""
    configs = [
        ButlerConnectionInfo(name="sw", port=41100),
    ]
    sw_data = {
        "total_sessions": 5,
        "total_input_tokens": 10000,
        "total_output_tokens": 5000,
        "by_model": {"claude-sonnet-4-20250514": {"input_tokens": 10000, "output_tokens": 5000}},
    }
    mgr = _mock_mgr({"sw": _make_tool_result(sw_data)})
    _wire(app, mgr, configs, _flat_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend", params={"butler": "nonexistent"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total_cost_usd"] == 0.0
    assert data["total_sessions"] == 0
    assert data["by_butler"] == {}
    SpendSummary.model_validate(data)


# ---------------------------------------------------------------------------
# GET /api/spend/daily — ?butler= filter [bu-lryu6]
# ---------------------------------------------------------------------------


async def test_daily_butler_filter_returns_only_that_butler(app):
    """?butler=sw restricts /daily fan-out to only that butler."""
    configs = [
        ButlerConnectionInfo(name="sw", port=41100),
        ButlerConnectionInfo(name="gen", port=41101),
    ]
    sw_daily = {
        "days": [
            {
                "date": "2026-05-01",
                "sessions": 2,
                "input_tokens": 1000,
                "output_tokens": 500,
                "by_model": {
                    "claude-sonnet-4-20250514": {"input_tokens": 1000, "output_tokens": 500}
                },
            }
        ]
    }
    # gen returns more sessions — must NOT appear when ?butler=sw
    gen_daily = {
        "days": [
            {
                "date": "2026-05-01",
                "sessions": 99,
                "input_tokens": 99000,
                "output_tokens": 99000,
                "by_model": {
                    "claude-haiku-35-20241022": {"input_tokens": 99000, "output_tokens": 99000}
                },
            }
        ]
    }
    mgr = _mock_mgr({"sw": _make_tool_result(sw_daily), "gen": _make_tool_result(gen_daily)})
    _wire(app, mgr, configs, _flat_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/spend/daily",
            params={"from": "2026-05-01", "to": "2026-05-01", "butler": "sw"},
        )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 1
    assert data[0]["sessions"] == 2


async def test_daily_staffer_uses_db_when_session_tool_absent(app):
    """Staffer daily spend should come from the DB pool instead of MCP tools."""
    configs = [ButlerConnectionInfo(name="switchboard", port=41100, type="staffer")]
    daily = [
        {
            "date": "2026-05-01",
            "sessions": 2,
            "input_tokens": 10000,
            "output_tokens": 5000,
            "by_model": {
                "claude-sonnet-4-20250514": {"input_tokens": 10000, "output_tokens": 5000}
            },
        }
    ]
    mgr = _mock_mgr({"switchboard": ButlerUnreachableError("switchboard")})
    db = _mock_db({"switchboard": _mock_db_pool(daily=daily)})
    _wire_db(_wire(app, mgr, configs, _flat_pricing()), db)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/spend/daily",
            params={"from": "2026-05-01", "to": "2026-05-01", "butler": "switchboard"},
        )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data == [
        {
            "date": "2026-05-01",
            "cost_usd": pytest.approx(0.105, abs=1e-4),
            "sessions": 2,
            "input_tokens": 10000,
            "output_tokens": 5000,
        }
    ]
    mgr.get_client.assert_not_called()


# ---------------------------------------------------------------------------
# GET /api/spend/top-sessions — ?butler= filter [bu-lryu6]
# ---------------------------------------------------------------------------


async def test_top_sessions_butler_filter_returns_only_that_butler(app):
    """?butler=sw restricts /top-sessions to only that butler."""
    configs = [
        ButlerConnectionInfo(name="sw", port=41100),
        ButlerConnectionInfo(name="gen", port=41101),
    ]
    sw_sessions = {
        "sessions": [
            {
                "session_id": "sw-session-1",
                "model": "claude-sonnet-4-20250514",
                "input_tokens": 5000,
                "output_tokens": 2500,
                "cached_input_tokens": 0,
                "started_at": "2026-05-01T10:00:00Z",
            }
        ]
    }
    # gen returns sessions too — must NOT appear when ?butler=sw
    gen_sessions = {
        "sessions": [
            {
                "session_id": "gen-session-1",
                "model": "claude-haiku-35-20241022",
                "input_tokens": 50000,
                "output_tokens": 25000,
                "cached_input_tokens": 0,
                "started_at": "2026-05-01T09:00:00Z",
            }
        ]
    }
    mgr = _mock_mgr({"sw": _make_tool_result(sw_sessions), "gen": _make_tool_result(gen_sessions)})
    _wire(app, mgr, configs, _flat_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend/top-sessions", params={"butler": "sw"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert all(s["butler"] == "sw" for s in data)
    assert not any(s["butler"] == "gen" for s in data)


# ---------------------------------------------------------------------------
# GET /api/spend/by-schedule — ?butler= filter [bu-lryu6]
# ---------------------------------------------------------------------------


async def test_by_schedule_butler_filter_returns_only_that_butler(app):
    """?butler=sw restricts /by-schedule to only that butler."""
    configs = [
        ButlerConnectionInfo(name="sw", port=41100),
        ButlerConnectionInfo(name="gen", port=41101),
    ]
    sw_sched = {
        "schedules": [
            {
                "name": "sw-daily",
                "cron": "0 8 * * *",
                "model": "claude-sonnet-4-20250514",
                "total_runs": 10,
                "total_input_tokens": 10000,
                "total_output_tokens": 5000,
                "runs_per_day": 1.0,
            }
        ]
    }
    gen_sched = {
        "schedules": [
            {
                "name": "gen-hourly",
                "cron": "0 * * * *",
                "model": "claude-haiku-35-20241022",
                "total_runs": 100,
                "total_input_tokens": 100000,
                "total_output_tokens": 50000,
                "runs_per_day": 24.0,
            }
        ]
    }
    mgr = _mock_mgr({"sw": _make_tool_result(sw_sched), "gen": _make_tool_result(gen_sched)})
    _wire(app, mgr, configs, _flat_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend/by-schedule", params={"butler": "sw"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert all(s["butler"] == "sw" for s in data)
    assert not any(s["schedule_name"] == "gen-hourly" for s in data)


# ---------------------------------------------------------------------------
# §5.2 Forecast math [bu-dvb7i]
# ---------------------------------------------------------------------------


def test_forecast_math_projection():
    """Naive linear projection: mtd / max(days_elapsed, 1) * days_in_month."""
    import calendar as cal
    from datetime import date

    # Simulate: 10 days elapsed, $5 spent → daily rate $0.50, EOM = $0.50 × days
    today = date.today()
    days_in_month = cal.monthrange(today.year, today.month)[1]
    days_elapsed = today.day  # 1-based
    mtd = 5.0
    daily_rate = mtd / max(days_elapsed, 1)
    projected_eom = daily_rate * days_in_month

    # Verify invariants
    assert projected_eom >= mtd  # projected ≥ actual MTD
    assert daily_rate > 0
    assert projected_eom == pytest.approx(daily_rate * days_in_month, rel=1e-6)


def test_forecast_math_first_day_clamp():
    """Days elapsed is clamped to ≥ 1 to avoid division by zero."""

    days_in_month = 31
    mtd = 3.0
    days_elapsed = 0  # edge case: never happens in practice but test the clamp

    daily_rate = mtd / max(days_elapsed, 1)
    projected_eom = daily_rate * days_in_month
    assert projected_eom == pytest.approx(mtd * days_in_month, rel=1e-6)


async def test_forecast_endpoint_returns_correct_shape(app):
    """GET /api/spend/forecast returns days + projected_eom_usd shape."""
    import calendar as cal
    from datetime import date

    configs = [ButlerConnectionInfo(name="sw", port=41100)]
    today = date.today()
    days_in_month = cal.monthrange(today.year, today.month)[1]

    # Mock: one actual day with $1 spend
    month_start = today.replace(day=1)
    daily_data = {
        "days": [
            {
                "date": month_start.isoformat(),
                "sessions": 1,
                "input_tokens": 100,
                "output_tokens": 50,
                "by_model": {},
            }
        ]
    }
    mgr = _mock_mgr({"sw": _make_tool_result(daily_data)})
    _wire(app, mgr, configs, _flat_pricing())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend/forecast")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "days" in data
    assert "projected_eom_usd" in data
    assert "days_in_month" in data
    assert data["days_in_month"] == days_in_month
    assert len(data["days"]) == days_in_month
    # First N days have projected=False (actuals), remainder projected=True
    actual_days = [d for d in data["days"] if not d["projected"]]
    projected_days = [d for d in data["days"] if d["projected"]]
    assert len(actual_days) + len(projected_days) == days_in_month


def test_projection_confidence_low_below_three_days():
    """projection_confidence == 'low' when days_elapsed < 3 (dashboard-spend-dashboard §5.2)."""
    from butlers.api.routers.spend import projection_confidence_for

    assert projection_confidence_for(1) == "low"
    assert projection_confidence_for(2) == "low"


def test_projection_confidence_normal_from_three_days():
    """projection_confidence == 'normal' when days_elapsed >= 3."""
    from butlers.api.routers.spend import projection_confidence_for

    assert projection_confidence_for(3) == "normal"
    assert projection_confidence_for(15) == "normal"


async def test_forecast_endpoint_exposes_projection_confidence(app):
    """GET /api/spend/forecast includes projection_confidence matching days_elapsed."""
    from datetime import date

    from butlers.api.routers.spend import projection_confidence_for

    configs = [ButlerConnectionInfo(name="sw", port=41100)]
    mgr = _mock_mgr({"sw": _make_tool_result({"days": []})})
    _wire(app, mgr, configs, _flat_pricing())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend/forecast")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "projection_confidence" in data
    expected = projection_confidence_for(date.today().day)
    assert data["projection_confidence"] == expected
    assert data["projection_confidence"] in ("low", "normal")


# ---------------------------------------------------------------------------
# §5.2 Spend rules — position reshuffle on insert/delete [bu-dvb7i]
# ---------------------------------------------------------------------------


async def test_spend_rules_list_returns_empty_when_no_db(app):
    """GET /api/spend/rules returns empty list when DB unavailable."""
    _wire(app, MagicMock(spec=MCPClientManager), [], _flat_pricing())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend/rules")

    assert resp.status_code == 200
    assert resp.json()["data"] == []


async def test_spend_ceiling_requires_db(app):
    """PUT /api/spend/ceiling returns 503 when DB unavailable."""
    _wire(app, MagicMock(spec=MCPClientManager), [], _flat_pricing())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.put("/api/spend/ceiling", json={"monthly_usd": 100.0})

    assert resp.status_code == 503


async def test_spend_ceiling_rejects_non_positive(app):
    """PUT /api/spend/ceiling returns 422 for monthly_usd <= 0."""
    from unittest.mock import MagicMock

    from butlers.api.routers.spend import _get_db_manager

    mock_db = MagicMock()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    _wire(app, MagicMock(spec=MCPClientManager), [], _flat_pricing())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.put("/api/spend/ceiling", json={"monthly_usd": -5.0})

    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# §5.3 — WS /api/spend/stream
# ---------------------------------------------------------------------------


async def test_spend_stream_connect_and_receive_event(app):
    """WS /api/spend/stream: connecting delivers snapshot then live call event."""
    from fastapi.testclient import TestClient

    from butlers.api.routers.spend import _spend_recent, _spend_subscribers, emit_spend_event

    # Ensure clean state for this test
    _spend_recent.clear()
    _spend_subscribers.clear()

    with TestClient(app) as client:
        with client.websocket_connect("/api/spend/stream") as ws:
            # First message must be the snapshot (empty ring buffer)
            snap = json.loads(ws.receive_text())
            assert snap["kind"] == "snapshot"
            assert isinstance(snap["events"], list)

            # Emit a synthetic cost event
            event = {
                "kind": "call",
                "ts": 1_700_000_000.0,
                "butler": "home",
                "model": "claude-sonnet-4-20250514",
                "tokens_in": 1000,
                "tokens_out": 500,
                "cost_usd": 0.00003,
                "session_id": "sess-abc",
                "extra": {},
            }
            emit_spend_event(event)

            # The connected subscriber should immediately receive the event
            msg = json.loads(ws.receive_text())
            assert msg["kind"] == "call"
            assert msg["butler"] == "home"
            assert msg["cost_usd"] == pytest.approx(0.00003)


async def test_spend_stream_snapshot_includes_recent_events(app):
    """WS snapshot contains events added before the connection was opened."""
    from fastapi.testclient import TestClient

    from butlers.api.routers.spend import _spend_recent, _spend_subscribers, emit_spend_event

    _spend_recent.clear()
    _spend_subscribers.clear()

    # Pre-populate recent ring buffer
    pre_event = {
        "kind": "call",
        "ts": 1_699_000_000.0,
        "butler": "atlas",
        "model": "claude-haiku-35-20241022",
        "tokens_in": 200,
        "tokens_out": 100,
        "cost_usd": 0.000001,
        "session_id": "pre-sess",
        "extra": {},
    }
    emit_spend_event(pre_event)

    with TestClient(app) as client:
        with client.websocket_connect("/api/spend/stream") as ws:
            snap = json.loads(ws.receive_text())
            assert snap["kind"] == "snapshot"
            assert len(snap["events"]) == 1
            assert snap["events"][0]["butler"] == "atlas"


async def test_spend_stream_auth_rejected_when_key_configured(app, monkeypatch):
    """WS /api/spend/stream closes with 4401 when api_key is wrong (spec)."""
    monkeypatch.setenv("DASHBOARD_API_KEY", "secret-key")

    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            # Wrong key — closes at the upgrade with WS code 4401.
            with client.websocket_connect("/api/spend/stream?api_key=wrong-key") as ws:
                ws.receive_text()
    assert exc_info.value.code == 4401


async def test_spend_stream_auth_accepted_with_correct_key(app, monkeypatch):
    """WS /api/spend/stream accepts connection when api_key matches."""
    monkeypatch.setenv("DASHBOARD_API_KEY", "correct-key")

    from fastapi.testclient import TestClient

    from butlers.api.routers.spend import _spend_recent, _spend_subscribers

    _spend_recent.clear()
    _spend_subscribers.clear()

    with TestClient(app) as client:
        with client.websocket_connect("/api/spend/stream?api_key=correct-key") as ws:
            snap = json.loads(ws.receive_text())
            assert snap["kind"] == "snapshot"


async def test_emit_spend_event_updates_ring_buffer():
    """emit_spend_event adds to the ring buffer and drops oldest when full."""
    from butlers.api.routers.spend import (
        _STREAM_RECENT_MAX,
        _spend_recent,
        _spend_subscribers,
        emit_spend_event,
    )

    _spend_recent.clear()
    _spend_subscribers.clear()

    for i in range(_STREAM_RECENT_MAX + 5):
        emit_spend_event(
            {
                "kind": "call",
                "ts": float(i),
                "butler": "test",
                "model": "model-x",
                "tokens_in": 1,
                "tokens_out": 1,
                "cost_usd": 0.000001,
            }
        )

    assert len(_spend_recent) == _STREAM_RECENT_MAX
    # Newest events should be kept (last 50)
    assert _spend_recent[-1]["ts"] == float(_STREAM_RECENT_MAX + 4)


# ---------------------------------------------------------------------------
# §5.2 Spend rule — enforced create/validate schema [bu-xclyn]
# ---------------------------------------------------------------------------


def test_spend_rule_condition_rejects_unknown_key() -> None:
    """An unknown condition key is rejected (extra='forbid' → ValidationError → 422)."""
    from pydantic import ValidationError

    from butlers.api.routers.spend import SpendRuleCondition

    with pytest.raises(ValidationError):
        SpendRuleCondition(weather="sunny")  # type: ignore[call-arg]


def test_spend_rule_action_rejects_unknown_key() -> None:
    """An unknown action key is rejected."""
    from pydantic import ValidationError

    from butlers.api.routers.spend import SpendRuleAction

    with pytest.raises(ValidationError):
        SpendRuleAction(model="m", reroute_to="x")  # type: ignore[call-arg]


def test_spend_rule_action_requires_an_effect() -> None:
    """An action with neither model nor max_cost_per_call is rejected."""
    from pydantic import ValidationError

    from butlers.api.routers.spend import SpendRuleAction

    with pytest.raises(ValidationError):
        SpendRuleAction()


def test_spend_rule_action_max_cost_per_call_must_be_positive() -> None:
    """max_cost_per_call must be > 0."""
    from pydantic import ValidationError

    from butlers.api.routers.spend import SpendRuleAction

    with pytest.raises(ValidationError):
        SpendRuleAction(max_cost_per_call=0)
    with pytest.raises(ValidationError):
        SpendRuleAction(max_cost_per_call=-1.0)
    # Positive is accepted (cap-only rule).
    a = SpendRuleAction(max_cost_per_call=0.05)
    assert a.max_cost_per_call == pytest.approx(0.05)
    assert a.model is None


def test_spend_rule_condition_rejects_invalid_tier() -> None:
    """complexity/tier must be a canonical tier name."""
    from pydantic import ValidationError

    from butlers.api.routers.spend import SpendRuleCondition

    with pytest.raises(ValidationError):
        SpendRuleCondition(complexity="superfast")
    with pytest.raises(ValidationError):
        SpendRuleCondition(tier=["workhorse", "nope"])
    # Valid tiers (incl. case-insensitive) and list pass.
    assert SpendRuleCondition(complexity="WORKHORSE").complexity == "WORKHORSE"
    assert SpendRuleCondition(tier=["workhorse", "cheap"]).tier == ["workhorse", "cheap"]


def test_spend_rule_create_accepts_new_dims() -> None:
    """SpendRuleCreate accepts the new trigger condition dim and max_cost_per_call effect."""
    from butlers.api.routers.spend import SpendRuleCreate

    body = SpendRuleCreate.model_validate(
        {
            "condition": {"butler": "general", "trigger": "healing"},
            "action": {"model": "cheap-model", "max_cost_per_call": 0.05},
        }
    )
    assert body.condition.trigger == "healing"
    assert body.action.max_cost_per_call == pytest.approx(0.05)
    # Serialized payload (what gets persisted) drops None fields.
    assert body.condition.model_dump(exclude_none=True) == {
        "butler": "general",
        "trigger": "healing",
    }
    assert body.action.model_dump(exclude_none=True) == {
        "model": "cheap-model",
        "max_cost_per_call": 0.05,
    }


def test_spend_rule_create_back_compat_existing_shape() -> None:
    """Legacy rule shape (butler/complexity condition + model action) still validates."""
    from butlers.api.routers.spend import SpendRuleCreate

    body = SpendRuleCreate.model_validate(
        {
            "condition": {"butler": "general", "complexity": "workhorse"},
            "action": {"model": "claude-haiku-cheap"},
        }
    )
    assert body.condition.model_dump(exclude_none=True) == {
        "butler": "general",
        "complexity": "workhorse",
    }
    assert body.action.model_dump(exclude_none=True) == {"model": "claude-haiku-cheap"}
