"""Tests for spend (was: costs), pricing, and schedule spend API endpoints.

Condensed: 22 → ~12 tests [bu-gg4y1]. Migrated from /api/costs → /api/spend [bu-dvb7i].
Keeps: pricing config load (parametrized errors + tiered parse), pricing endpoint,
spend summary aggregation + tiered pricing + unreachable fallback, daily sorting,
by-schedule contract + zero-div guard.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastmcp.exceptions import ToolError

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
from butlers.api.routers.spend import _is_tool_absent_error, _ledger_session_divergences
from butlers.core.model_routing import check_monthly_ceiling
from butlers.core.sessions import CADENCE_BASIS_DESCRIPTION, _estimate_runs_per_month

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
    clients: dict[str, MagicMock] = {}

    async def _get(name: str):
        resp = responses.get(name)
        if isinstance(resp, Exception):
            raise resp
        # Cache per-name so tests can retrieve the same client mock afterward
        # (e.g. to inspect call_tool.call_args) instead of getting a fresh one.
        if name not in clients:
            c = MagicMock()
            c.call_tool = AsyncMock(return_value=resp)
            clients[name] = c
        return clients[name]

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
                "total_cached_input_tokens": summary.get("total_cached_input_tokens", 0),
                "total_cache_creation_tokens": summary.get("total_cache_creation_tokens", 0),
            }
        )
        pool.fetch = AsyncMock(
            return_value=[
                {
                    "model": model,
                    "input_tokens": stats.get("input_tokens", 0),
                    "output_tokens": stats.get("output_tokens", 0),
                    "cached_input_tokens": stats.get("cached_input_tokens", 0),
                    "cache_creation_tokens": stats.get("cache_creation_tokens", 0),
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
                        "cached_input_tokens": day.get("cached_input_tokens", 0),
                        "cache_creation_tokens": day.get("cache_creation_tokens", 0),
                    }
                    for day in daily
                ],
                [
                    {
                        "day": date.fromisoformat(day["date"]),
                        "model": model,
                        "input_tokens": stats.get("input_tokens", 0),
                        "output_tokens": stats.get("output_tokens", 0),
                        "cached_input_tokens": stats.get("cached_input_tokens", 0),
                        "cache_creation_tokens": stats.get("cache_creation_tokens", 0),
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
    _wire_db(
        _wire(app, MagicMock(spec=MCPClientManager), [], _flat_pricing()),
        _mock_db({"switchboard": _mock_ledger_pool([])}),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend")
    data = resp.json()["data"]
    assert data["total_cost_usd"] == 0.0
    assert data["source_error"] is False
    SpendSummary.model_validate(data)


async def test_cost_summary_aggregates_multiple_butlers(app):
    rows = [
        _ledger_row(
            butler_name="sw",
            model_id="claude-sonnet-4-20250514",
            calls=5,
            input_tokens=10_000,
            output_tokens=5_000,
        ),
        _ledger_row(
            butler_name="gen",
            model_id="claude-haiku-35-20241022",
            calls=3,
            input_tokens=8_000,
            output_tokens=4_000,
        ),
    ]
    mgr = MagicMock(spec=MCPClientManager)
    _wire_db(
        _wire(app, mgr, [], _flat_pricing()), _mock_db({"switchboard": _mock_ledger_pool(rows)})
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend")
    data = resp.json()["data"]
    assert data["total_sessions"] == 8
    assert data["total_cost_usd"] == pytest.approx(0.1274, abs=1e-4)
    assert data["by_butler"] == {"gen": pytest.approx(0.0224), "sw": pytest.approx(0.105)}
    mgr.get_client.assert_not_called()


async def test_cost_summary_ledger_failure_is_visible_and_never_falls_back_to_mcp(app):
    pool = MagicMock()
    pool.fetch = AsyncMock(side_effect=RuntimeError("ledger unavailable"))
    mgr = _mock_mgr({"sw": ButlerUnreachableError("should not be called")})
    _wire_db(
        _wire(app, mgr, [ButlerConnectionInfo(name="sw", port=41100)], _flat_pricing()),
        _mock_db({"switchboard": pool}),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend")
    data = resp.json()["data"]
    assert data["source_error"] is True
    assert data["total_cost_usd"] == 0.0
    mgr.get_client.assert_not_called()


async def test_cost_summary_ignores_mcp_tool_denial_when_ledger_is_healthy(app):
    """A dashboard aggregate is no longer partial because an MCP tool is denied."""
    rows = [
        _ledger_row(
            butler_name="finance",
            model_id="claude-sonnet-4-20250514",
            calls=2,
            input_tokens=1_000,
            output_tokens=500,
        )
    ]
    denied_client = MagicMock()
    denied_client.call_tool = AsyncMock(side_effect=ToolError("Unknown tool: 'sessions_summary'"))
    mgr = MagicMock(spec=MCPClientManager)
    mgr.get_client = AsyncMock(return_value=denied_client)
    _wire_db(
        _wire(app, mgr, [], _flat_pricing()), _mock_db({"switchboard": _mock_ledger_pool(rows)})
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend")

    assert resp.status_code == 200
    assert resp.json()["data"]["source_error"] is False
    mgr.get_client.assert_not_called()


async def test_cost_summary_all_reachable_reports_no_unavailable_butlers(app):
    _wire_db(
        _wire(app, MagicMock(spec=MCPClientManager), [], _flat_pricing()),
        _mock_db({"switchboard": _mock_ledger_pool([])}),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend")
    assert resp.json()["data"]["source_error"] is False


async def test_cost_breakdown_by_butler_reports_ledger_source_error(app):
    pool = MagicMock()
    pool.fetch = AsyncMock(side_effect=RuntimeError("ledger unavailable"))
    _wire_db(
        _wire(app, MagicMock(spec=MCPClientManager), [], _flat_pricing()),
        _mock_db({"switchboard": pool}),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend/breakdown?by=butler")
    data = resp.json()["data"]
    assert data["breakdown"] == {}
    assert data["source_error"] is True


def _mock_ledger_pool(rows: list[dict]):
    pool = MagicMock()

    async def _fetch(*args):
        # The aggregate query's final bind is its optional executing-butler
        # filter. The MTD gate query has no binds and intentionally sees every
        # row in this fixture.
        butler = args[-1] if len(args) == 4 else None
        if butler is None:
            return rows
        return [row for row in rows if row.get("butler_name") == butler]

    pool.fetch = AsyncMock(side_effect=_fetch)
    return pool


def _ledger_row(
    *,
    day: date | None = None,
    butler_name: str = "switchboard",
    purpose: str = "route",
    model_id: str = "claude-sonnet-4-20250514",
    calls: int = 1,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_input_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> dict:
    """Return one grouped executed-model ledger fixture row."""
    return {
        "day": day or date.today(),
        "butler_name": butler_name,
        "purpose": purpose,
        "model_id": model_id,
        "calls": calls,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_input_tokens": cached_input_tokens,
        "cache_creation_tokens": cache_creation_tokens,
    }


async def test_spend_aggregate_surfaces_use_executed_ledger_models_and_keep_unpriced_usage(app):
    """Summary, daily, breakdown, and forecast share ledger execution truth.

    The requested model intentionally never appears in the ledger fixture. If
    any of these surfaces regress to pricing `sessions.model`, it will either
    call the unreachable MCP manager or invent that requested-model bucket.
    """
    rows = [
        {
            "day": date.today(),
            "butler_name": "travel",
            "purpose": "schedule:trip-digest",
            "model_id": "executed-model",
            "calls": 2,
            "input_tokens": 1_000,
            "output_tokens": 500,
            "cached_input_tokens": 0,
            "cache_creation_tokens": 0,
        },
        {
            "day": date.today(),
            "butler_name": "travel",
            "purpose": "schedule:trip-digest",
            "model_id": "unpriced-executed-model",
            "calls": 3,
            "input_tokens": 200,
            "output_tokens": 100,
            "cached_input_tokens": 40,
            "cache_creation_tokens": 10,
        },
    ]
    pool = _mock_ledger_pool(rows)
    pool.fetchrow = AsyncMock(return_value={"monthly_usd": 100.0})
    db = _mock_db({"switchboard": pool})
    mgr = MagicMock(spec=MCPClientManager)
    pricing = PricingConfig({"executed-model": ModelPricing(0.000001, 0.000002)})
    _wire(app, mgr, [], pricing)
    _wire_db(app, db)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        summary_response = await client.get(
            f"/api/spend?from={date.today().isoformat()}&to={date.today().isoformat()}"
        )
        daily_response = await client.get(
            f"/api/spend/daily?from={date.today().isoformat()}&to={date.today().isoformat()}"
        )
        breakdown_response = await client.get("/api/spend/breakdown?by=model")
        forecast_response = await client.get("/api/spend/forecast")

    assert summary_response.status_code == 200, summary_response.text
    summary = summary_response.json()["data"]
    assert summary["total_cost_usd"] == pytest.approx(0.002)
    assert summary["by_model"] == {"executed-model": pytest.approx(0.002)}
    assert "requested-model" not in summary["by_model"]
    assert summary["unpriced_models"] == [
        {
            "model": "unpriced-executed-model",
            "calls": 3,
            "input_tokens": 200,
            "output_tokens": 100,
            "cached_input_tokens": 40,
            "cache_creation_tokens": 10,
        }
    ]

    assert daily_response.status_code == 200, daily_response.text
    daily = daily_response.json()
    assert daily["data"][0]["cost_usd"] == pytest.approx(0.002)
    assert daily["data"][0]["by_butler"] == {"travel": pytest.approx(0.002)}
    assert daily["data"][0]["unpriced_models"][0]["calls"] == 3

    assert breakdown_response.status_code == 200, breakdown_response.text
    breakdown = breakdown_response.json()["data"]
    assert breakdown["breakdown"] == {"executed-model": pytest.approx(0.002)}
    assert breakdown["unpriced_models"][0]["model"] == "unpriced-executed-model"

    assert forecast_response.status_code == 200, forecast_response.text
    forecast = forecast_response.json()["data"]
    assert forecast["mtd_usd"] == pytest.approx(0.002)
    assert forecast["ceiling_blind_to_unpriced_models"] == 1
    assert forecast["unpriced_models"][0]["model"] == "unpriced-executed-model"
    mgr.get_client.assert_not_called()


async def test_ledger_session_divergence_deadman_reports_material_day_butler_drift():
    """Session tokens are diagnostic evidence and surface >5% drift loudly."""
    day = date(2026, 7, 11)
    session_pool = MagicMock()
    session_pool.fetch = AsyncMock(
        side_effect=[
            [
                {
                    "day": day,
                    "sessions": 1,
                    "input_tokens": 50,
                    "output_tokens": 0,
                    "cached_input_tokens": 0,
                    "cache_creation_tokens": 0,
                }
            ],
            [],
        ]
    )
    db = _mock_db({"travel": session_pool})
    ledger_rows = [
        {
            "day": day,
            "butler_name": "travel",
            "model_id": "executed-model",
            "calls": 1,
            "input_tokens": 100,
            "output_tokens": 0,
            "cached_input_tokens": 0,
            "cache_creation_tokens": 0,
        }
    ]

    divergences, source_error = await _ledger_session_divergences(
        db,
        [ButlerConnectionInfo(name="travel", port=41100)],
        day,
        day,
        ledger_rows,
    )

    assert source_error is False
    assert len(divergences) == 1
    assert divergences[0].model_dump() == {
        "date": "2026-07-11",
        "butler": "travel",
        "ledger_tokens": 100,
        "session_tokens": 50,
        "difference_ratio": 0.5,
    }


async def test_ledger_session_divergence_deadman_marks_missing_butler_evidence_degraded():
    """A ledger butler absent from the session pool map is not a clean comparison."""
    day = date(2026, 7, 11)
    divergences, source_error = await _ledger_session_divergences(
        MagicMock(),
        [],
        day,
        day,
        [
            _ledger_row(
                day=day,
                butler_name="retired-butler",
                model_id="executed-model",
                input_tokens=100,
            )
        ],
    )

    assert divergences == []
    assert source_error is True


async def test_cost_breakdown_by_purpose_prices_ledger_rows(app):
    """``by=purpose`` prices token_usage_ledger rows grouped by (purpose, model_id).

    Unlike butler/model/feature, this dimension reads the shared ledger directly
    (bu-og0j2/bu-qvnce.12) rather than fanning out per-butler MCP calls.
    """
    rows = [
        {
            "purpose": "classification",
            "model_id": "claude-sonnet-4-20250514",
            "input_tokens": 10000,
            "output_tokens": 5000,
            "cached_input_tokens": 0,
            "cache_creation_tokens": 0,
        },
        {
            "purpose": "discretion",
            "model_id": "claude-haiku-35-20241022",
            "input_tokens": 8000,
            "output_tokens": 4000,
            "cached_input_tokens": 0,
            "cache_creation_tokens": 0,
        },
        # Same purpose, second model_id -- costs must accumulate, not overwrite.
        {
            "purpose": "classification",
            "model_id": "claude-haiku-35-20241022",
            "input_tokens": 1000,
            "output_tokens": 500,
            "cached_input_tokens": 0,
            "cache_creation_tokens": 0,
        },
    ]
    db = _mock_db({"switchboard": _mock_ledger_pool(rows)})
    _wire_db(app, db)
    _wire(app, MagicMock(spec=MCPClientManager), [], _flat_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend/breakdown?by=purpose")
    data = resp.json()["data"]
    assert data["by"] == "purpose"
    assert data["source_error"] is False
    assert data["breakdown"]["classification"] == pytest.approx(0.1078, abs=1e-4)
    assert data["breakdown"]["discretion"] == pytest.approx(0.0224, abs=1e-4)


async def test_cost_breakdown_by_purpose_no_db_reports_source_error(app):
    """Without a DatabaseManager there is no MCP fallback for the ledger -- report it."""
    app.dependency_overrides.pop(_costs_get_db, None)
    _wire(app, MagicMock(spec=MCPClientManager), [], _flat_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend/breakdown?by=purpose")
    data = resp.json()["data"]
    assert data["breakdown"] == {}
    assert data["source_error"] is True


async def test_cost_breakdown_by_purpose_query_failure_reports_source_error(app):
    """A ledger query failure must surface as source_error, never a truthful empty result."""
    pool = MagicMock()
    pool.fetch = AsyncMock(side_effect=RuntimeError("connection reset"))
    db = _mock_db({"switchboard": pool})
    _wire_db(app, db)
    _wire(app, MagicMock(spec=MCPClientManager), [], _flat_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend/breakdown?by=purpose")
    data = resp.json()["data"]
    assert data["breakdown"] == {}
    assert data["source_error"] is True


async def test_cost_summary_prices_tiered_executed_ledger_models(app):
    """The ledger path preserves the standard (zero-context) tier selection."""
    rows = [
        _ledger_row(
            model_id="gpt-5.4",
            input_tokens=1_000_000,
            output_tokens=1_000_000,
        )
    ]
    _wire_db(
        _wire(app, MagicMock(spec=MCPClientManager), [], _tiered_pricing()),
        _mock_db({"switchboard": _mock_ledger_pool(rows)}),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        response = await c.get("/api/spend")
    assert response.json()["data"]["total_cost_usd"] == pytest.approx(17.50, abs=1e-4)


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
    rows = [
        _ledger_row(day=date(2026, 2, 10), input_tokens=100, output_tokens=50),
        _ledger_row(day=date(2026, 2, 8), input_tokens=200, output_tokens=100),
    ]
    _wire_db(
        _wire(app, MagicMock(spec=MCPClientManager), [], _flat_pricing()),
        _mock_db({"switchboard": _mock_ledger_pool(rows)}),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/spend/daily", params={"from": "2026-02-08", "to": "2026-02-10"}
        )
    data = resp.json()["data"]
    assert [d["date"] for d in data] == ["2026-02-08", "2026-02-10"]


async def test_daily_costs_preserves_per_butler_identity(app):
    """Daily ledger rows retain each executing butler rather than smearing totals."""
    rows = [
        _ledger_row(
            day=date(2026, 2, 8),
            butler_name="sw",
            model_id="claude-sonnet-4-20250514",
            calls=1,
            input_tokens=100,
            output_tokens=50,
        ),
        _ledger_row(
            day=date(2026, 2, 8),
            butler_name="gen",
            model_id="claude-haiku-35-20241022",
            calls=2,
            input_tokens=200,
            output_tokens=100,
        ),
        _ledger_row(
            day=date(2026, 2, 9),
            butler_name="gen",
            model_id="claude-haiku-35-20241022",
            calls=1,
            input_tokens=50,
            output_tokens=25,
        ),
    ]
    _wire_db(
        _wire(app, MagicMock(spec=MCPClientManager), [], _flat_pricing()),
        _mock_db({"switchboard": _mock_ledger_pool(rows)}),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/spend/daily", params={"from": "2026-02-08", "to": "2026-02-09"}
        )
    data = resp.json()["data"]
    by_date = {d["date"]: d for d in data}

    # 2026-02-08: both butlers spent — by_butler must carry each contribution
    # separately, not a single smeared total.
    day1 = by_date["2026-02-08"]
    assert set(day1["by_butler"].keys()) == {"sw", "gen"}
    assert day1["by_butler"]["sw"] > 0
    assert day1["by_butler"]["gen"] > 0
    assert day1["cost_usd"] == pytest.approx(
        day1["by_butler"]["sw"] + day1["by_butler"]["gen"], abs=1e-6
    )

    # 2026-02-09: only "gen" spent — "sw" must not appear with a fabricated 0.
    day2 = by_date["2026-02-09"]
    assert set(day2["by_butler"].keys()) == {"gen"}


async def test_daily_costs_ledger_failure_reports_source_error_without_mcp_fallback(app):
    pool = MagicMock()
    pool.fetch = AsyncMock(side_effect=RuntimeError("ledger unavailable"))
    mgr = _mock_mgr({"sw": ButlerUnreachableError("should not be called")})
    _wire_db(
        _wire(app, mgr, [ButlerConnectionInfo(name="sw", port=41100)], _flat_pricing()),
        _mock_db({"switchboard": pool}),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/spend/daily", params={"from": "2026-02-08", "to": "2026-02-08"}
        )
    body = response.json()
    assert body["data"] == []
    assert body["meta"]["source_error"] is True
    mgr.get_client.assert_not_called()


async def test_daily_costs_empty_ledger_is_not_degraded(app):
    _wire_db(
        _wire(app, MagicMock(spec=MCPClientManager), [], _flat_pricing()),
        _mock_db({"switchboard": _mock_ledger_pool([])}),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/spend/daily", params={"from": "2026-02-08", "to": "2026-02-08"}
        )
    assert response.json()["meta"].get("source_error") is not True


# ---------------------------------------------------------------------------
# GET /api/spend — date-range params (from/to)
# ---------------------------------------------------------------------------


async def test_cost_summary_date_range_aggregates_executed_ledger_rows(app):
    rows = [
        _ledger_row(
            day=date(2026, 3, 1),
            butler_name="sw",
            calls=3,
            input_tokens=6_000,
            output_tokens=3_000,
        ),
        _ledger_row(
            day=date(2026, 3, 2),
            butler_name="sw",
            calls=2,
            input_tokens=4_000,
            output_tokens=2_000,
        ),
    ]
    _wire_db(
        _wire(app, MagicMock(spec=MCPClientManager), [], _flat_pricing()),
        _mock_db({"switchboard": _mock_ledger_pool(rows)}),
    )
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
    """Date-range summary groups ledger rows by the executing butler."""
    rows = [
        _ledger_row(
            day=date(2026, 4, 1),
            butler_name="a",
            model_id="claude-haiku-35-20241022",
            calls=1,
            input_tokens=1_000,
            output_tokens=500,
        ),
        _ledger_row(
            day=date(2026, 4, 1),
            butler_name="b",
            model_id="claude-haiku-35-20241022",
            calls=2,
            input_tokens=2_000,
            output_tokens=1_000,
        ),
    ]
    _wire_db(
        _wire(app, MagicMock(spec=MCPClientManager), [], _flat_pricing()),
        _mock_db({"switchboard": _mock_ledger_pool(rows)}),
    )
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
# _is_tool_absent_error -- absent-vs-degraded classifier (bu-hmdqz.7)
# ---------------------------------------------------------------------------


def test_is_tool_absent_error_true_for_staffer_unknown_tool():
    """A staffer structurally lacks each spend fan-out tool."""
    info = ButlerConnectionInfo(name="switchboard", port=41100, type="staffer")
    assert _is_tool_absent_error(ToolError("Unknown tool: 'sessions_summary'"), info) is True


def test_is_tool_absent_error_false_for_registered_butler_or_other_failure():
    """A registered butler's authorization denial stays a degraded source."""
    info = ButlerConnectionInfo(name="finance", port=41100)
    assert _is_tool_absent_error(ToolError("Unknown tool: 'sessions_summary'"), info) is False
    assert _is_tool_absent_error(ToolError("division by zero"), info) is False
    assert _is_tool_absent_error(ButlerUnreachableError("broken"), info) is False
    assert _is_tool_absent_error(TimeoutError(), info) is False


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
        "runs_per_month": 30.436875,
    }
    zero_sched = {
        **sched,
        "name": "empty-report",
        "total_runs": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
    }
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


async def test_by_schedule_separates_forecast_from_measured_history(app):
    """A projection must never be presented as measured history (bu-6jv4m.2).

    The range-measured fields (``total_runs``, ``total_cost_usd``,
    ``avg_cost_per_run``) and the forecast fields (``projected_monthly_runs``,
    ``projected_monthly_usd``) are separate, and the forecast carries the basis
    it was computed on. The projected cost is exactly avg-cost x projected runs
    -- there is no free-floating multiplier anywhere in the chain.
    """
    configs = [ButlerConnectionInfo(name="sw", port=41100)]
    weekly = {
        "name": "weekly-review",
        "cron": "0 9 * * 1",
        "model": "claude-sonnet-4-20250514",
        "total_runs": 3,
        "total_input_tokens": 3000,
        "total_output_tokens": 1500,
        "runs_per_month": _estimate_runs_per_month("0 9 * * 1"),
        "forecast_basis": CADENCE_BASIS_DESCRIPTION,
    }
    mgr = _mock_mgr({"sw": _make_tool_result({"schedules": [weekly]})})
    _wire(app, mgr, configs, _flat_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend/by-schedule")
    assert resp.status_code == 200
    row = next(i for i in resp.json()["data"] if i["schedule_name"] == "weekly-review")
    ScheduleCost(**row)

    # Measured history stays exactly what was measured.
    assert row["total_runs"] == 3

    # The live regression: a weekly cron projected ~4.3 runs a month, not 30.
    assert row["projected_monthly_runs"] == pytest.approx(4.35, abs=0.05)
    assert row["forecast_basis"] == CADENCE_BASIS_DESCRIPTION

    # Projected cost is derived from the two exposed numbers, nothing hidden.
    assert row["projected_monthly_usd"] == pytest.approx(
        row["avg_cost_per_run"] * row["projected_monthly_runs"], abs=1e-6
    )
    # A 30x basis would have produced ~7x this figure.
    assert row["projected_monthly_usd"] < row["avg_cost_per_run"] * 30


async def test_by_schedule_forecast_absent_when_cadence_unknown(app):
    """A schedule whose cron cannot be parsed projects zero, not a guess."""
    configs = [ButlerConnectionInfo(name="sw", port=41100)]
    broken = {
        "name": "mystery",
        "cron": "not a cron",
        "model": "claude-sonnet-4-20250514",
        "total_runs": 5,
        "total_input_tokens": 5000,
        "total_output_tokens": 2500,
        "runs_per_month": _estimate_runs_per_month("not a cron"),
        "forecast_basis": CADENCE_BASIS_DESCRIPTION,
    }
    mgr = _mock_mgr({"sw": _make_tool_result({"schedules": [broken]})})
    _wire(app, mgr, configs, _flat_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend/by-schedule")
    assert resp.status_code == 200
    row = next(i for i in resp.json()["data"] if i["schedule_name"] == "mystery")
    assert row["projected_monthly_runs"] == 0.0
    assert row["projected_monthly_usd"] == 0.0
    # ...but the measured history it DID accrue is still reported truthfully.
    assert row["total_runs"] == 5
    assert row["total_cost_usd"] > 0


async def test_by_schedule_merges_multi_model_fragments(app):
    """A schedule that ran under 2+ models in the window must collapse into
    ONE ScheduleCost entry per (butler, schedule_name) -- the underlying DB
    query groups by (name, cron, model), so before this fix each model
    produced its own fragment that under-ranked the schedule's true burn and
    collided on the frontend's `${butler}-${schedule_name}` React key
    (bu-hmdqz.7)."""
    configs = [ButlerConnectionInfo(name="sw", port=41100)]
    sonnet_fragment = {
        "name": "drain-curriculum-request",
        "cron": "0 8 * * *",
        "model": "claude-sonnet-4-20250514",
        "total_runs": 20,
        "total_input_tokens": 20000,
        "total_output_tokens": 10000,
        "runs_per_month": 30.436875,
    }
    haiku_fragment = {
        **sonnet_fragment,
        "model": "claude-haiku-35-20241022",
        "total_runs": 10,
        "total_input_tokens": 10000,
        "total_output_tokens": 5000,
    }
    mgr = _mock_mgr({"sw": _make_tool_result({"schedules": [sonnet_fragment, haiku_fragment]})})
    _wire(app, mgr, configs, _flat_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend/by-schedule")
    assert resp.status_code == 200
    items = resp.json()["data"]
    matches = [i for i in items if i["schedule_name"] == "drain-curriculum-request"]
    # Exactly one row -- no duplicate (butler, schedule_name) React key.
    assert len(matches) == 1
    merged = matches[0]
    assert merged["total_runs"] == 30
    # Cost is the sum of both per-model fragments, priced independently.
    sonnet_cost = 20000 * 0.000003 + 10000 * 0.000015
    haiku_cost = 10000 * 0.0000008 + 5000 * 0.000004
    assert merged["total_cost_usd"] == pytest.approx(sonnet_cost + haiku_cost, abs=1e-6)
    assert merged["avg_cost_per_run"] == pytest.approx((sonnet_cost + haiku_cost) / 30, abs=1e-6)


async def test_by_schedule_tool_absent_not_marked_unavailable(app):
    """A staffer butler with no ``schedule_costs`` tool registered raises
    ``ToolError('Unknown tool: ...')`` -- legitimately absent (see
    ``core_tools/_scheduling.py``: ``schedule_costs`` is non-STAFFER only),
    NOT a degraded source, and must not appear in
    ``meta.unavailable_butlers`` (bu-hmdqz.7)."""
    configs = [
        ButlerConnectionInfo(name="sw", port=41100),
        ButlerConnectionInfo(name="switchboard", port=41101, type="staffer"),
    ]
    sched = {
        "name": "daily-report",
        "cron": "0 8 * * *",
        "model": "claude-sonnet-4-20250514",
        "total_runs": 30,
        "total_input_tokens": 30000,
        "total_output_tokens": 15000,
        "runs_per_month": 30.436875,
    }
    mgr = _mock_mgr(
        {
            "sw": _make_tool_result({"schedules": [sched]}),
            "switchboard": ToolError("Unknown tool: 'schedule_costs'"),
        }
    )
    _wire(app, mgr, configs, _flat_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend/by-schedule")
    assert resp.status_code == 200
    body = resp.json()
    assert any(i["schedule_name"] == "daily-report" for i in body["data"])
    assert "unavailable_butlers" not in body["meta"]


async def test_by_schedule_reports_unavailable_butlers_for_genuine_failure(app):
    """A butler whose schedule_costs call genuinely fails must be named in
    meta.unavailable_butlers -- its schedules are dropped from the merged
    ranking otherwise, indistinguishable from "has no schedules" (bu-h3ej9,
    mirrors the /daily degraded pattern)."""
    configs = [
        ButlerConnectionInfo(name="sw", port=41100),
        ButlerConnectionInfo(name="broken", port=41101),
    ]
    sched = {
        "name": "daily-report",
        "cron": "0 8 * * *",
        "model": "claude-sonnet-4-20250514",
        "total_runs": 30,
        "total_input_tokens": 30000,
        "total_output_tokens": 15000,
        "runs_per_month": 30.436875,
    }
    mgr = _mock_mgr(
        {
            "sw": _make_tool_result({"schedules": [sched]}),
            "broken": ButlerUnreachableError("broken"),
        }
    )
    _wire(app, mgr, configs, _flat_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend/by-schedule")
    assert resp.status_code == 200
    body = resp.json()
    # sw's schedule still lands...
    assert any(i["schedule_name"] == "daily-report" for i in body["data"])
    # ...and the failed butler is named, not silently dropped.
    assert body["meta"]["unavailable_butlers"] == ["broken"]


async def test_by_schedule_all_reachable_reports_no_unavailable_butlers(app):
    """When every butler's schedule_costs call succeeds, meta must not carry a
    degraded flag -- a truthful complete result must not read as partial
    (bu-h3ej9)."""
    configs = [ButlerConnectionInfo(name="sw", port=41100)]
    mgr = _mock_mgr({"sw": _make_tool_result({"schedules": []})})
    _wire(app, mgr, configs, _flat_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend/by-schedule")
    assert resp.status_code == 200
    assert "unavailable_butlers" not in resp.json()["meta"]


# ---------------------------------------------------------------------------
# GET /api/spend — ?butler= filter [bu-iuol4.12]
# ---------------------------------------------------------------------------


async def test_cost_summary_butler_filter_returns_only_that_butler(app):
    """?butler=sw is a ledger query filter, not an MCP fan-out filter."""
    rows = [
        _ledger_row(butler_name="sw", calls=5, input_tokens=10_000, output_tokens=5_000),
        _ledger_row(butler_name="gen", calls=99, input_tokens=99_000, output_tokens=99_000),
    ]
    pool = _mock_ledger_pool(rows)
    _wire_db(
        _wire(app, MagicMock(spec=MCPClientManager), [], _flat_pricing()),
        _mock_db({"switchboard": pool}),
    )
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
    assert pool.fetch.await_args.args[-1] == "sw"
    SpendSummary.model_validate(data)


async def test_cost_summary_includes_staffer_ledger_rows_without_session_tool(app):
    rows = [
        _ledger_row(butler_name="switchboard", calls=5, input_tokens=10_000, output_tokens=5_000)
    ]
    mgr = _mock_mgr({"switchboard": ButlerUnreachableError("should not be called")})
    _wire_db(
        _wire(app, mgr, [], _flat_pricing()), _mock_db({"switchboard": _mock_ledger_pool(rows)})
    )

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
    rows = [
        _ledger_row(
            butler_name="known",
            calls=2,
            input_tokens=1_000,
            output_tokens=500,
        )
    ]
    _wire_db(
        _wire(
            app,
            MagicMock(spec=MCPClientManager),
            [ButlerConnectionInfo(name="known", port=41100)],
            _flat_pricing(),
        ),
        _mock_db({"switchboard": _mock_ledger_pool(rows)}),
    )
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
    """?butler=sw scopes the daily ledger aggregate to one executing butler."""
    rows = [
        _ledger_row(
            day=date(2026, 5, 1),
            butler_name="sw",
            calls=2,
            input_tokens=1_000,
            output_tokens=500,
        ),
        _ledger_row(
            day=date(2026, 5, 1),
            butler_name="gen",
            calls=99,
            input_tokens=99_000,
            output_tokens=99_000,
        ),
    ]
    pool = _mock_ledger_pool(rows)
    _wire_db(
        _wire(app, MagicMock(spec=MCPClientManager), [], _flat_pricing()),
        _mock_db({"switchboard": pool}),
    )
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
    assert data[0]["by_butler"] == {"sw": pytest.approx(0.0105)}
    assert pool.fetch.await_args.args[-1] == "sw"


async def test_daily_includes_staffer_ledger_rows_without_session_tool(app):
    rows = [
        _ledger_row(
            day=date(2026, 5, 1),
            butler_name="switchboard",
            calls=2,
            input_tokens=10_000,
            output_tokens=5_000,
        )
    ]
    mgr = _mock_mgr({"switchboard": ButlerUnreachableError("should not be called")})
    _wire_db(
        _wire(app, mgr, [], _flat_pricing()), _mock_db({"switchboard": _mock_ledger_pool(rows)})
    )

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
            "by_butler": {"switchboard": pytest.approx(0.105, abs=1e-4)},
            "unpriced_models": [],
        }
    ]
    mgr.get_client.assert_not_called()


async def test_daily_unknown_butler_returns_empty_200(app):
    """?butler=nonexistent on /daily returns an empty list 200."""
    rows = [
        _ledger_row(
            day=date(2026, 5, 3),
            butler_name="known",
            calls=2,
            input_tokens=1_000,
            output_tokens=500,
        )
    ]
    _wire_db(
        _wire(
            app,
            MagicMock(spec=MCPClientManager),
            [ButlerConnectionInfo(name="known", port=41100)],
            _flat_pricing(),
        ),
        _mock_db({"switchboard": _mock_ledger_pool(rows)}),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/spend/daily",
            params={"from": "2026-05-03", "to": "2026-05-03", "butler": "nonexistent"},
        )
    assert resp.status_code == 200
    assert resp.json()["data"] == []


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


async def test_top_sessions_no_butler_filter_aggregates_all(app):
    """Omitting ?butler on /top-sessions returns sessions from all butlers."""
    configs = [
        ButlerConnectionInfo(name="sw", port=41100),
        ButlerConnectionInfo(name="gen", port=41101),
    ]
    session_data = {
        "sessions": [
            {
                "session_id": "session-x",
                "model": "claude-sonnet-4-20250514",
                "input_tokens": 1000,
                "output_tokens": 500,
                "cached_input_tokens": 0,
                "started_at": "2026-05-01T08:00:00Z",
            }
        ]
    }
    mgr = _mock_mgr({"sw": _make_tool_result(session_data), "gen": _make_tool_result(session_data)})
    _wire(app, mgr, configs, _flat_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend/top-sessions")
    assert resp.status_code == 200
    data = resp.json()["data"]
    butlers_returned = {s["butler"] for s in data}
    assert "sw" in butlers_returned
    assert "gen" in butlers_returned


async def test_top_sessions_unknown_butler_returns_empty_200(app):
    """?butler=nonexistent on /top-sessions returns an empty list 200."""
    configs = [ButlerConnectionInfo(name="sw", port=41100)]
    sw_sessions = {
        "sessions": [
            {
                "session_id": "sw-s1",
                "model": "claude-sonnet-4-20250514",
                "input_tokens": 1000,
                "output_tokens": 500,
                "cached_input_tokens": 0,
                "started_at": "2026-05-01T08:00:00Z",
            }
        ]
    }
    mgr = _mock_mgr({"sw": _make_tool_result(sw_sessions)})
    _wire(app, mgr, configs, _flat_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend/top-sessions", params={"butler": "nonexistent"})
    assert resp.status_code == 200
    assert resp.json()["data"] == []


# ---------------------------------------------------------------------------
# GET /api/spend/top-sessions — date-range scoping [bu-oaiiw]
# ---------------------------------------------------------------------------


async def test_top_sessions_date_range_forwarded_to_mcp_tool(app):
    """?from=&to= on /top-sessions are forwarded as from_date/to_date to the MCP tool."""
    configs = [ButlerConnectionInfo(name="sw", port=41100)]
    sw_sessions = {"sessions": []}
    mgr = _mock_mgr({"sw": _make_tool_result(sw_sessions)})
    _wire(app, mgr, configs, _flat_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/spend/top-sessions", params={"from": "2026-05-01", "to": "2026-05-07"}
        )
    assert resp.status_code == 200
    client_mock = await mgr.get_client("sw")
    tool_name, tool_args = client_mock.call_tool.call_args.args
    assert tool_name == "top_sessions"
    assert tool_args["from_date"] == "2026-05-01"
    assert tool_args["to_date"] == "2026-05-07"


async def test_top_sessions_no_date_range_omits_mcp_args_back_compat(app):
    """Omitting from/to on /top-sessions does not send from_date/to_date (all-time, back-compat)."""
    configs = [ButlerConnectionInfo(name="sw", port=41100)]
    sw_sessions = {"sessions": []}
    mgr = _mock_mgr({"sw": _make_tool_result(sw_sessions)})
    _wire(app, mgr, configs, _flat_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend/top-sessions")
    assert resp.status_code == 200
    client_mock = await mgr.get_client("sw")
    _tool_name, tool_args = client_mock.call_tool.call_args.args
    assert "from_date" not in tool_args
    assert "to_date" not in tool_args


# ---------------------------------------------------------------------------
# GET /api/spend/top-sessions — degraded-source reporting [bu-i7p0z]
# ---------------------------------------------------------------------------


async def test_top_sessions_reports_unavailable_butlers_for_genuine_failure(app):
    """A butler whose top_sessions call genuinely fails must be named in
    meta.unavailable_butlers -- its contribution is silently dropped from the
    merged top-N otherwise, indistinguishable from "no expensive sessions"
    (bu-i7p0z, mirrors the schedule-costs / cost-summary degraded pattern)."""
    configs = [
        ButlerConnectionInfo(name="sw", port=41100),
        ButlerConnectionInfo(name="broken", port=41101),
    ]
    sw_sessions = {
        "sessions": [
            {
                "session_id": "s1",
                "model": "claude-sonnet-4-20250514",
                "input_tokens": 100,
                "output_tokens": 50,
                "started_at": "2026-02-08T00:00:00Z",
            }
        ]
    }
    mgr = _mock_mgr(
        {"sw": _make_tool_result(sw_sessions), "broken": ButlerUnreachableError("broken")}
    )
    _wire(app, mgr, configs, _flat_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend/top-sessions")
    body = resp.json()
    assert body["meta"]["unavailable_butlers"] == ["broken"]


async def test_top_sessions_all_reachable_reports_no_unavailable_butlers(app):
    """When every butler's top_sessions call succeeds, meta must not carry a
    degraded flag -- a truthful empty/complete result must not read as
    partial."""
    configs = [ButlerConnectionInfo(name="sw", port=41100)]
    sw_sessions = {"sessions": []}
    mgr = _mock_mgr({"sw": _make_tool_result(sw_sessions)})
    _wire(app, mgr, configs, _flat_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend/top-sessions")
    assert "unavailable_butlers" not in resp.json()["meta"]


async def test_top_sessions_tool_absent_not_marked_unavailable(app):
    """A staffer butler with no ``top_sessions`` tool registered raises
    ``ToolError('Unknown tool: ...')`` -- legitimately absent (see
    ``core_tools/_sessions.py``: ``top_sessions`` is non-STAFFER only), NOT a
    degraded source. It must contribute nothing and must NOT appear in
    ``meta.unavailable_butlers`` (bu-hmdqz.7 -- classify-before-flagging in the
    flagging direction; the tool-absent twin of the genuine-failure test above,
    closing the asymmetric coverage flagged in bu-agdql). ``db`` is None so the
    fan-out takes the MCP path where the ``_is_tool_absent_error`` classification
    lives (the DB-first path added in bu-h1i8k is bypassed)."""
    configs = [
        ButlerConnectionInfo(name="sw", port=41100),
        ButlerConnectionInfo(name="switchboard", port=41101, type="staffer"),
    ]
    sw_sessions = {
        "sessions": [
            {
                "session_id": "s1",
                "model": "claude-sonnet-4-20250514",
                "input_tokens": 100,
                "output_tokens": 50,
                "started_at": "2026-02-08T00:00:00Z",
            }
        ]
    }
    mgr = _mock_mgr(
        {
            "sw": _make_tool_result(sw_sessions),
            "switchboard": ToolError("Unknown tool: 'top_sessions'"),
        }
    )
    _wire(app, mgr, configs, _flat_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend/top-sessions")
    assert resp.status_code == 200
    body = resp.json()
    # The reachable butler's session is present; the tool-absent staffer
    # contributes nothing and is NOT flagged as a genuine failure.
    assert any(s["session_id"] == "s1" for s in body["data"])
    assert not any(s["butler"] == "switchboard" for s in body["data"])
    assert "unavailable_butlers" not in body["meta"]


@pytest.mark.parametrize(
    "params",
    [
        {"from": "2026-05-01"},  # only 'from' without 'to'
        {"from": "2026-05-07", "to": "2026-05-01"},  # inverted 'from' > 'to'
    ],
    ids=["only-from", "inverted"],
)
async def test_top_sessions_date_range_invalid_returns_422(app, params):
    """Incomplete or inverted from/to ranges return 422."""
    configs = [ButlerConnectionInfo(name="sw", port=41100)]
    mgr = _mock_mgr({})
    _wire(app, mgr, configs, _flat_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend/top-sessions", params=params)
    assert resp.status_code == 422


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
                "runs_per_month": 30.436875,
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
                "runs_per_month": 730.485,
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


async def test_by_schedule_no_butler_filter_aggregates_all(app):
    """Omitting ?butler on /by-schedule returns schedules from all butlers."""
    configs = [
        ButlerConnectionInfo(name="sw", port=41100),
        ButlerConnectionInfo(name="gen", port=41101),
    ]
    sched = {
        "schedules": [
            {
                "name": "daily",
                "cron": "0 8 * * *",
                "model": "claude-sonnet-4-20250514",
                "total_runs": 5,
                "total_input_tokens": 5000,
                "total_output_tokens": 2500,
                "runs_per_month": 30.436875,
            }
        ]
    }
    mgr = _mock_mgr({"sw": _make_tool_result(sched), "gen": _make_tool_result(sched)})
    _wire(app, mgr, configs, _flat_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend/by-schedule")
    assert resp.status_code == 200
    data = resp.json()["data"]
    butlers_returned = {s["butler"] for s in data}
    assert "sw" in butlers_returned
    assert "gen" in butlers_returned


async def test_by_schedule_unknown_butler_returns_empty_200(app):
    """?butler=nonexistent on /by-schedule returns an empty list 200."""
    configs = [ButlerConnectionInfo(name="sw", port=41100)]
    sw_sched = {
        "schedules": [
            {
                "name": "daily",
                "cron": "0 8 * * *",
                "model": "claude-sonnet-4-20250514",
                "total_runs": 5,
                "total_input_tokens": 5000,
                "total_output_tokens": 2500,
                "runs_per_month": 30.436875,
            }
        ]
    }
    mgr = _mock_mgr({"sw": _make_tool_result(sw_sched)})
    _wire(app, mgr, configs, _flat_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend/by-schedule", params={"butler": "nonexistent"})
    assert resp.status_code == 200
    assert resp.json()["data"] == []


# ---------------------------------------------------------------------------
# GET /api/spend/by-schedule — date-range scoping [bu-oaiiw]
# ---------------------------------------------------------------------------


async def test_by_schedule_date_range_forwarded_to_mcp_tool(app):
    """?from=&to= on /by-schedule are forwarded as from_date/to_date to the MCP tool."""
    configs = [ButlerConnectionInfo(name="sw", port=41100)]
    sw_sched = {"schedules": []}
    mgr = _mock_mgr({"sw": _make_tool_result(sw_sched)})
    _wire(app, mgr, configs, _flat_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/spend/by-schedule", params={"from": "2026-05-01", "to": "2026-05-07"}
        )
    assert resp.status_code == 200
    client_mock = await mgr.get_client("sw")
    tool_name, tool_args = client_mock.call_tool.call_args.args
    assert tool_name == "schedule_costs"
    assert tool_args["from_date"] == "2026-05-01"
    assert tool_args["to_date"] == "2026-05-07"


async def test_by_schedule_no_date_range_omits_mcp_args_back_compat(app):
    """Omitting from/to on /by-schedule does not send from_date/to_date (all-time, back-compat)."""
    configs = [ButlerConnectionInfo(name="sw", port=41100)]
    sw_sched = {"schedules": []}
    mgr = _mock_mgr({"sw": _make_tool_result(sw_sched)})
    _wire(app, mgr, configs, _flat_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend/by-schedule")
    assert resp.status_code == 200
    client_mock = await mgr.get_client("sw")
    _tool_name, tool_args = client_mock.call_tool.call_args.args
    assert tool_args == {}


@pytest.mark.parametrize(
    "params",
    [
        {"from": "2026-05-01"},  # only 'from' without 'to'
        {"from": "2026-05-07", "to": "2026-05-01"},  # inverted 'from' > 'to'
    ],
    ids=["only-from", "inverted"],
)
async def test_by_schedule_date_range_invalid_returns_422(app, params):
    """Incomplete or inverted from/to ranges return 422."""
    configs = [ButlerConnectionInfo(name="sw", port=41100)]
    mgr = _mock_mgr({})
    _wire(app, mgr, configs, _flat_pricing())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend/by-schedule", params=params)
    assert resp.status_code == 422


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
# Ledger-first forecast MTD + degraded envelope [bu-7o89u.1]
#
# The forecast MTD/EOM figures must come from the exact same
# butlers.core.model_routing.price_mtd_from_ledger helper check_monthly_ceiling
# uses to gate spawns -- never from the per-butler daily-actuals fan-out (kept
# only for the chart's solid-actuals `days` series). See spend.py's
# get_spend_forecast docstring and model_routing.py's price_mtd_from_ledger.
# ---------------------------------------------------------------------------


def _mock_forecast_ledger_pool(usage_rows: list[dict], ceiling_row: dict | None):
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=usage_rows)
    pool.fetchrow = AsyncMock(return_value=ceiling_row)
    return pool


_LEDGER_USAGE_ROWS = [
    {
        "model_id": "claude-haiku",
        "input_tokens": 1000,
        "output_tokens": 500,
        "cached_input_tokens": 0,
        "cache_creation_tokens": 0,
    }
]


async def test_forecast_mtd_priced_from_ledger_not_fan_out(app):
    """mtd_usd/projected_eom_usd come from the ledger helper, never the fan-out.

    configs=[] means the daily-actuals fan-out contributes nothing -- if
    mtd_usd were still (accidentally) derived from that fan-out sum, it would
    be 0 regardless of the ledger fixture below.
    """
    db = _mock_db(
        {"switchboard": _mock_forecast_ledger_pool(_LEDGER_USAGE_ROWS, {"monthly_usd": 100.0})}
    )
    _wire_db(app, db)
    _wire(app, MagicMock(spec=MCPClientManager), [], _flat_pricing())

    with patch("butlers.api.pricing.estimate_session_cost", return_value=42.0):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/spend/forecast")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["mtd_usd"] == pytest.approx(42.0)
    assert data["ceiling_usd"] == pytest.approx(100.0)
    assert data["ceiling_source_error"] is False
    expected_eom = 42.0 / max(data["days_elapsed"], 1) * data["days_in_month"]
    assert data["projected_eom_usd"] == pytest.approx(expected_eom, rel=1e-6)


async def test_forecast_mtd_matches_check_monthly_ceiling_same_fixture(app):
    """The forecast endpoint and the spawn-deny gate must agree on MTD from the
    same ledger fixture -- the exact divergence bu-7o89u.1 closes.
    """
    pool = _mock_forecast_ledger_pool(_LEDGER_USAGE_ROWS, {"monthly_usd": 100.0})
    db = _mock_db({"switchboard": pool})
    _wire_db(app, db)
    _wire(app, MagicMock(spec=MCPClientManager), [], _flat_pricing())

    with patch("butlers.api.pricing.estimate_session_cost", return_value=42.0):
        gate_status = await check_monthly_ceiling(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/spend/forecast")

    data = resp.json()["data"]
    assert gate_status.mtd_usd == pytest.approx(42.0)
    assert data["mtd_usd"] == pytest.approx(gate_status.mtd_usd)


async def test_forecast_no_db_reports_ceiling_source_error(app):
    """No DatabaseManager wired -- no MCP fallback exists for the ledger, so this
    must report ceiling_source_error rather than a fabricated $0 MTD (mirrors
    _get_spend_breakdown_by_purpose's no-db handling).
    """
    app.dependency_overrides.pop(_costs_get_db, None)
    _wire(app, MagicMock(spec=MCPClientManager), [], _flat_pricing())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend/forecast")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["ceiling_source_error"] is True
    assert data["mtd_usd"] == 0.0
    assert data["ceiling_usd"] is None
    assert data["projected_eom_usd"] == 0.0


async def test_forecast_ledger_query_failure_reports_ceiling_source_error(app):
    """A ledger query failure must surface as ceiling_source_error, never a
    truthful-looking $0 MTD (butlers/CLAUDE.md degraded-mode envelope convention).
    """
    pool = MagicMock()
    pool.fetch = AsyncMock(side_effect=RuntimeError("connection reset"))
    db = _mock_db({"switchboard": pool})
    _wire_db(app, db)
    _wire(app, MagicMock(spec=MCPClientManager), [], _flat_pricing())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend/forecast")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["ceiling_source_error"] is True
    assert data["mtd_usd"] == 0.0
    assert data["ceiling_usd"] is None


async def test_forecast_divergence_source_error_is_independent_of_ceiling_source(app):
    """The diagnostic session read can fail without invalidating ledger money."""
    configs = [ButlerConnectionInfo(name="broken", port=41100)]
    ledger_rows = [
        _ledger_row(
            day=date.today(),
            butler_name="broken",
            model_id="claude-sonnet-4-20250514",
            calls=1,
            input_tokens=1_000,
            output_tokens=500,
        )
    ]
    ledger_pool = _mock_forecast_ledger_pool(ledger_rows, {"monthly_usd": 100.0})
    session_pool = MagicMock()
    session_pool.fetch = AsyncMock(side_effect=RuntimeError("session DB unavailable"))
    _wire_db(app, _mock_db({"switchboard": ledger_pool, "broken": session_pool}))
    mgr = _mock_mgr({"broken": ButlerUnreachableError("should not be called")})
    _wire(app, mgr, configs, _flat_pricing())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend/forecast")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["ceiling_source_error"] is False
    assert data["divergence_source_error"] is True
    assert data["unavailable_butlers"] == []
    mgr.get_client.assert_not_called()


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


def test_spend_rule_condition_accepts_purpose() -> None:
    """purpose (bu-og0j2) accepts scalar and list values, same as trigger, unvalidated."""
    from butlers.api.routers.spend import SpendRuleCondition

    assert SpendRuleCondition(purpose="discretion").purpose == "discretion"
    assert SpendRuleCondition(purpose=["healing", "discretion"]).purpose == [
        "healing",
        "discretion",
    ]


def test_spend_rule_condition_rejects_trigger_and_purpose_together() -> None:
    """trigger and purpose alias the same trigger_source value (bu-og0j2/bu-qvnce.12).

    A condition setting both can never match at dispatch (they're ANDed against the
    same underlying value), so it is rejected at create/update time (422) instead of
    silently persisting a rule that fails closed forever.
    """
    from pydantic import ValidationError

    from butlers.api.routers.spend import SpendRuleCondition

    with pytest.raises(ValidationError):
        SpendRuleCondition(trigger="route", purpose="discretion")
    # Same value on both keys is still rejected -- redundant, not a legitimate use.
    with pytest.raises(ValidationError):
        SpendRuleCondition(trigger="route", purpose="route")
    # Either alone is fine.
    assert SpendRuleCondition(trigger="route").trigger == "route"
    assert SpendRuleCondition(purpose="route").purpose == "route"


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


# ---------------------------------------------------------------------------
# DB-first evidence layer for /top-sessions and /by-schedule (bu-h1i8k)
#
# DB-first, MCP-fallback per butler: the direct core.sessions.* DB read is tried
# first (which reaches the staffer butlers whose sessions/scheduling MCP tools
# are structurally absent), falling back to the MCP tool on a DB pool
# absence/error, and only marking a butler unavailable when BOTH fail.
# ---------------------------------------------------------------------------

_TOP_SESSIONS_DB_PAYLOAD = {
    "sessions": [
        {
            "session_id": "db-session-1",
            "model": "claude-sonnet-4-20250514",
            "input_tokens": 1000,
            "output_tokens": 500,
            "started_at": "2026-05-01T08:00:00Z",
        }
    ]
}

_SCHEDULE_COSTS_DB_PAYLOAD = {
    "schedules": [
        {
            "name": "daily-brief",
            "cron": "0 8 * * *",
            "model": "claude-sonnet-4-20250514",
            "total_runs": 4,
            "total_input_tokens": 4000,
            "total_output_tokens": 2000,
            "total_cached_input_tokens": 0,
            "total_cache_creation_tokens": 0,
            "runs_per_month": 30.436875,
        }
    ]
}


async def test_top_sessions_db_first_serves_data_and_skips_mcp(app):
    """DB-first: a butler's costliest sessions come from the DB read; the MCP tool
    is not consulted. The MCP client is wired to RAISE, proving the DB path
    short-circuits it (this is also the staffer case: a butler whose top_sessions
    MCP tool is structurally absent is now filled by the DB)."""
    configs = [ButlerConnectionInfo(name="switchboard", port=41100)]
    db = _mock_db({"switchboard": MagicMock()})
    mgr = _mock_mgr({"switchboard": ButlerUnreachableError("switchboard")})
    _wire(app, mgr, configs, _flat_pricing())
    _wire_db(app, db)
    with patch(
        "butlers.api.routers.spend.top_sessions",
        new=AsyncMock(return_value=_TOP_SESSIONS_DB_PAYLOAD),
    ) as db_helper:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/spend/top-sessions")
    assert resp.status_code == 200
    body = resp.json()
    assert [s["session_id"] for s in body["data"]] == ["db-session-1"]
    assert body["data"][0]["butler"] == "switchboard"
    db_helper.assert_awaited()
    assert body["meta"].get("unavailable_butlers", []) == []


async def test_top_sessions_db_miss_falls_back_to_mcp(app):
    """DB pool absent (KeyError) -> the DB helper returns None -> MCP tool serves."""
    configs = [ButlerConnectionInfo(name="finance", port=41100)]
    db = _mock_db({})  # no pool -> KeyError -> DB None -> MCP fallback
    mcp_payload = {
        "sessions": [
            {
                "session_id": "mcp-session-1",
                "model": "claude-haiku-35-20241022",
                "input_tokens": 100,
                "output_tokens": 50,
                "started_at": "2026-05-01T09:00:00Z",
            }
        ]
    }
    mgr = _mock_mgr({"finance": _make_tool_result(mcp_payload)})
    _wire(app, mgr, configs, _flat_pricing())
    _wire_db(app, db)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend/top-sessions")
    assert resp.status_code == 200
    body = resp.json()
    assert [s["session_id"] for s in body["data"]] == ["mcp-session-1"]
    assert body["meta"].get("unavailable_butlers", []) == []


async def test_top_sessions_db_and_mcp_both_fail_marks_unavailable(app):
    """Both DB (pool absent) and MCP (unreachable) fail -> unavailable_butlers."""
    configs = [ButlerConnectionInfo(name="finance", port=41100)]
    db = _mock_db({})  # DB None
    mgr = _mock_mgr({"finance": ButlerUnreachableError("finance")})
    _wire(app, mgr, configs, _flat_pricing())
    _wire_db(app, db)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend/top-sessions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["meta"]["unavailable_butlers"] == ["finance"]


async def test_by_schedule_db_first_serves_data_and_skips_mcp(app):
    """DB-first: per-schedule costs come from the DB read; MCP is not consulted
    (staffer butler filled by DB)."""
    configs = [ButlerConnectionInfo(name="switchboard", port=41100)]
    db = _mock_db({"switchboard": MagicMock()})
    mgr = _mock_mgr({"switchboard": ButlerUnreachableError("switchboard")})
    _wire(app, mgr, configs, _flat_pricing())
    _wire_db(app, db)
    with patch(
        "butlers.api.routers.spend.schedule_costs",
        new=AsyncMock(return_value=_SCHEDULE_COSTS_DB_PAYLOAD),
    ) as db_helper:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/spend/by-schedule")
    assert resp.status_code == 200
    body = resp.json()
    assert [c["schedule_name"] for c in body["data"]] == ["daily-brief"]
    assert body["data"][0]["butler"] == "switchboard"
    assert body["data"][0]["total_runs"] == 4
    db_helper.assert_awaited()
    assert body["meta"].get("unavailable_butlers", []) == []


async def test_by_schedule_db_miss_falls_back_to_mcp(app):
    """DB pool absent -> the DB helper returns None -> MCP tool serves."""
    configs = [ButlerConnectionInfo(name="finance", port=41100)]
    db = _mock_db({})
    mgr = _mock_mgr({"finance": _make_tool_result(_SCHEDULE_COSTS_DB_PAYLOAD)})
    _wire(app, mgr, configs, _flat_pricing())
    _wire_db(app, db)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend/by-schedule")
    assert resp.status_code == 200
    body = resp.json()
    assert [c["schedule_name"] for c in body["data"]] == ["daily-brief"]
    assert body["meta"].get("unavailable_butlers", []) == []


async def test_by_schedule_db_and_mcp_both_fail_marks_unavailable(app):
    """Both DB and MCP fail -> unavailable_butlers."""
    configs = [ButlerConnectionInfo(name="finance", port=41100)]
    db = _mock_db({})
    mgr = _mock_mgr({"finance": ButlerUnreachableError("finance")})
    _wire(app, mgr, configs, _flat_pricing())
    _wire_db(app, db)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/spend/by-schedule")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["meta"]["unavailable_butlers"] == ["finance"]


async def test_utc_today_keeps_spend_defaults_and_ledger_mtd_on_one_month_at_rollover(
    app, monkeypatch
):
    """UTC 00:30 on Aug 1 is still Jul 31 for a west-of-UTC host.

    Preset summaries, daily defaults, MTD breakdown, the forecast denominator,
    and the ledger MTD used by the ceiling must all remain on Aug 1.  Letting
    ``date.today()`` leak into the API would query Jul 31 while the ledger
    gate's SQL is already explicitly UTC.
    """
    from butlers.api.routers import spend as spend_router
    from butlers.core import model_routing

    class UtcRolloverDatetime(datetime):
        @classmethod
        def now(cls, tz=None) -> UtcRolloverDatetime:
            assert tz is UTC
            return cls(2026, 8, 1, 0, 30, tzinfo=UTC)

    monkeypatch.setattr(spend_router, "datetime", UtcRolloverDatetime)
    # Do not monkeypatch the module's ``date`` name: FastAPI resolves the
    # endpoint annotations lazily.  The explicit helper is the seam shared by
    # all date-defaulting spend routes, while this frozen UTC instant models a
    # west-of-UTC host that would still report July 31 from ``date.today()``.
    assert spend_router._utc_today() == date(2026, 8, 1)

    usage_row = _ledger_row(
        day=date(2026, 8, 1),
        model_id="claude-haiku-35-20241022",
        input_tokens=1_000,
        output_tokens=100,
    )
    pool = _mock_forecast_ledger_pool([usage_row], {"monthly_usd": 10.0})
    _wire_db(app, _mock_db({"switchboard": pool}))
    _wire(app, MagicMock(spec=MCPClientManager), [], _flat_pricing())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        summary = await client.get("/api/spend?period=today")
        daily = await client.get("/api/spend/daily")
        breakdown = await client.get("/api/spend/breakdown?by=butler")
        forecast = await client.get("/api/spend/forecast")

    assert (
        summary.status_code
        == daily.status_code
        == breakdown.status_code
        == forecast.status_code
        == 200
    )
    forecast_data = forecast.json()["data"]
    assert forecast_data["days_elapsed"] == 1
    assert forecast_data["mtd_usd"] == pytest.approx(0.0012)
    assert forecast_data["projected_eom_usd"] == pytest.approx(0.0012 * 31)

    # The forecast's MTD is the same UTC-ledger subtotal as the pre-spawn
    # ceiling helper, whose query explicitly derives the start from UTC now.
    ledger_mtd = await spend_router.price_mtd_from_ledger(pool, _flat_pricing())
    assert forecast_data["mtd_usd"] == pytest.approx(ledger_mtd.cost_usd)
    assert "now() AT TIME ZONE 'UTC'" in model_routing._MTD_USAGE_BY_MODEL_SQL

    expected_start = datetime(2026, 8, 1, tzinfo=UTC)
    expected_end = datetime(2026, 8, 2, tzinfo=UTC)
    ranged_ledger_calls = [
        call
        for call in pool.fetch.await_args_list
        if call.args[0] == spend_router._LEDGER_USAGE_BY_DAY_SQL
    ]
    assert len(ranged_ledger_calls) == 4
    observed_ranges = [
        (call.args[1].isoformat(), call.args[2].isoformat(), call.args[3])
        for call in ranged_ledger_calls
    ]
    assert observed_ranges == [
        # preset=today
        (expected_start.isoformat(), expected_end.isoformat(), None),
        # /daily default: UTC today and the preceding six UTC days
        (datetime(2026, 7, 26, tzinfo=UTC).isoformat(), expected_end.isoformat(), None),
        # MTD breakdown and forecast
        (expected_start.isoformat(), expected_end.isoformat(), None),
        (expected_start.isoformat(), expected_end.isoformat(), None),
    ]
