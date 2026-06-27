"""Tests for GET /api/settings/console.

Covers:
- 200 happy-path with mocked sub-system helpers returning stable values.
- Header counts reflect sub-system results.
- Attention items: red first (open approvals), then amber.
- Partial-failure: spend aggregation fails → amber attention item instead of 500.
- Open-approvals path → red attention item.
- Spend-near-ceiling path → amber attention item.
- DB unavailable (None) → zero counts, no crash.
- 10-second cache: second call within TTL returns cached payload without re-running helpers.
- Cache expires after TTL: second call after TTL re-runs helpers.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

import butlers.api.routers.settings_console as console_mod
from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.deps import (
    ButlerConnectionInfo,
    MCPClientManager,
    get_butler_configs,
    get_mcp_manager,
    get_pricing,
)
from butlers.api.pricing import ModelPricing, PricingConfig

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PRICING = PricingConfig(models={"claude-sonnet-4-6": ModelPricing(0.000003, 0.000015)})

_BUTLER_CONFIG = [
    ButlerConnectionInfo(name="general", port=41100),
]


def _make_app(
    *,
    db: DatabaseManager | None = None,
):
    """Create a minimal test app with mocked dependencies."""
    app = create_app(api_key="")

    mock_mgr = MagicMock(spec=MCPClientManager)
    mock_mgr.get_client = AsyncMock(side_effect=Exception("unreachable in tests"))

    app.dependency_overrides[get_butler_configs] = lambda: _BUTLER_CONFIG
    app.dependency_overrides[get_mcp_manager] = lambda: mock_mgr
    app.dependency_overrides[get_pricing] = lambda: _PRICING
    app.dependency_overrides[console_mod._get_db_manager] = lambda: db

    return app


def _mock_db_none() -> None:
    return None


# ---------------------------------------------------------------------------
# Helper to reset module-level cache between tests
# ---------------------------------------------------------------------------


def _reset_cache():
    console_mod._cache_ts = 0.0
    console_mod._cache_payload = None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_console_cache():
    """Reset the in-memory console cache before each test."""
    _reset_cache()
    yield
    _reset_cache()


@pytest.mark.asyncio
async def test_console_no_db_returns_zeros():
    """With no DB, all counts should be zero and no crash."""
    app = _make_app(db=None)

    with (
        patch.object(console_mod, "_count_active_butlers", new=AsyncMock(return_value=(0, None))),
        patch.object(console_mod, "_get_spend_mtd", new=AsyncMock(return_value=(0.0, None, None))),
        patch.object(console_mod, "_count_open_approvals", new=AsyncMock(return_value=(0, None))),
        patch.object(console_mod, "_count_models", new=AsyncMock(return_value=(0, 0, None))),
        patch.object(console_mod, "_check_cli_auth", new=AsyncMock(return_value=[])),
        patch.object(console_mod, "_check_model_errors", new=AsyncMock(return_value=[])),
        patch.object(console_mod, "_check_failed_webhooks", new=AsyncMock(return_value=[])),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/settings/console")

    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    hc = body["header_counts"]
    assert hc["active_butlers"] == 0
    assert hc["spend_mtd_usd"] == 0.0
    assert hc["open_approvals"] == 0
    assert hc["models_verified"] == 0
    assert hc["models_total"] == 0
    assert body["attention"] == []
    assert body["attention_truncated_count"] == 0


@pytest.mark.asyncio
async def test_console_open_approvals_generates_red_attention():
    """Open approvals should create a red attention item."""
    app = _make_app(db=None)

    with (
        patch.object(console_mod, "_count_active_butlers", new=AsyncMock(return_value=(2, None))),
        patch.object(console_mod, "_get_spend_mtd", new=AsyncMock(return_value=(5.0, None, None))),
        patch.object(console_mod, "_count_open_approvals", new=AsyncMock(return_value=(3, None))),
        patch.object(console_mod, "_count_models", new=AsyncMock(return_value=(4, 5, None))),
        patch.object(console_mod, "_check_cli_auth", new=AsyncMock(return_value=[])),
        patch.object(console_mod, "_check_model_errors", new=AsyncMock(return_value=[])),
        patch.object(console_mod, "_check_failed_webhooks", new=AsyncMock(return_value=[])),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/settings/console")

    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body["header_counts"]["open_approvals"] == 3
    assert body["header_counts"]["active_butlers"] == 2
    assert body["header_counts"]["models_verified"] == 4
    assert body["header_counts"]["models_total"] == 5

    attention = body["attention"]
    assert len(attention) >= 1
    red = [a for a in attention if a["tone"] == "red"]
    assert any("approval" in a["text"].lower() for a in red), f"Expected approval item in {red}"
    # Red items come before amber
    tones = [a["tone"] for a in attention]
    last_red = max((i for i, t in enumerate(tones) if t == "red"), default=-1)
    first_amber = min((i for i, t in enumerate(tones) if t == "amber"), default=len(tones))
    assert last_red < first_amber, "Red items must precede amber items"


@pytest.mark.asyncio
async def test_console_spend_near_ceiling_generates_amber():
    """Spend >= 90% of ceiling should create an amber attention item."""
    app = _make_app(db=None)

    with (
        patch.object(console_mod, "_count_active_butlers", new=AsyncMock(return_value=(1, None))),
        patch.object(
            console_mod,
            "_get_spend_mtd",
            new=AsyncMock(return_value=(95.0, 100.0, None)),  # 95% of $100
        ),
        # Force a normal-confidence projection so the gate stays open regardless
        # of the calendar day the test runs on.
        patch(
            "butlers.api.routers.spend.projection_confidence_for",
            new=lambda days_elapsed: "normal",
        ),
        patch.object(console_mod, "_count_open_approvals", new=AsyncMock(return_value=(0, None))),
        patch.object(console_mod, "_count_models", new=AsyncMock(return_value=(2, 2, None))),
        patch.object(console_mod, "_check_cli_auth", new=AsyncMock(return_value=[])),
        patch.object(console_mod, "_check_model_errors", new=AsyncMock(return_value=[])),
        patch.object(console_mod, "_check_failed_webhooks", new=AsyncMock(return_value=[])),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/settings/console")

    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    amber = [a for a in body["attention"] if a["tone"] == "amber"]
    ceiling_items = [a for a in amber if a["kind"] == "spend_ceiling"]
    assert len(ceiling_items) == 1, f"Expected one spend_ceiling item, got {ceiling_items}"
    assert "/settings/spend" in ceiling_items[0]["action_route"]


@pytest.mark.asyncio
async def test_console_near_ceiling_suppressed_when_projection_low_confidence():
    """A low-confidence projection (days_elapsed < 3) gates the near-ceiling item.

    dashboard-spend-dashboard §5.2: projection_confidence='low' signals the Console
    aggregator NOT to raise a "spend near ceiling" attention item, since the naive
    early-month projection swings wildly.
    """
    app = _make_app(db=None)

    with (
        patch.object(console_mod, "_count_active_butlers", new=AsyncMock(return_value=(1, None))),
        patch.object(
            console_mod,
            "_get_spend_mtd",
            new=AsyncMock(return_value=(95.0, 100.0, None)),  # 95% of $100 → would normally fire
        ),
        patch(
            "butlers.api.routers.spend.projection_confidence_for",
            new=lambda days_elapsed: "low",
        ),
        patch.object(console_mod, "_count_open_approvals", new=AsyncMock(return_value=(0, None))),
        patch.object(console_mod, "_count_models", new=AsyncMock(return_value=(2, 2, None))),
        patch.object(console_mod, "_check_cli_auth", new=AsyncMock(return_value=[])),
        patch.object(console_mod, "_check_model_errors", new=AsyncMock(return_value=[])),
        patch.object(console_mod, "_check_failed_webhooks", new=AsyncMock(return_value=[])),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/settings/console")

    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    ceiling_items = [a for a in body["attention"] if a["kind"] == "spend_ceiling"]
    assert ceiling_items == [], "Near-ceiling item must be suppressed on low-confidence projection"


@pytest.mark.asyncio
async def test_console_spend_below_ceiling_no_amber():
    """Spend < 90% of ceiling should NOT generate an attention item."""
    app = _make_app(db=None)

    with (
        patch.object(console_mod, "_count_active_butlers", new=AsyncMock(return_value=(1, None))),
        patch.object(
            console_mod,
            "_get_spend_mtd",
            new=AsyncMock(return_value=(50.0, 100.0, None)),  # 50% of $100
        ),
        patch.object(console_mod, "_count_open_approvals", new=AsyncMock(return_value=(0, None))),
        patch.object(console_mod, "_count_models", new=AsyncMock(return_value=(2, 2, None))),
        patch.object(console_mod, "_check_cli_auth", new=AsyncMock(return_value=[])),
        patch.object(console_mod, "_check_model_errors", new=AsyncMock(return_value=[])),
        patch.object(console_mod, "_check_failed_webhooks", new=AsyncMock(return_value=[])),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/settings/console")

    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    ceiling_items = [a for a in body["attention"] if a["kind"] == "spend_ceiling"]
    assert ceiling_items == [], "No ceiling alert expected below 90%"


@pytest.mark.asyncio
async def test_console_partial_failure_subsystem_surfaces_amber():
    """When spend aggregation fails, it returns an amber item; whole response still 200."""
    app = _make_app(db=None)

    spend_err_item = console_mod.AttentionItem(
        tone="amber",
        kind="subsystem_error",
        text="Could not fetch spend data — totals may be unavailable.",
        action_route="/settings/spend",
    )

    with (
        patch.object(console_mod, "_count_active_butlers", new=AsyncMock(return_value=(1, None))),
        patch.object(
            console_mod,
            "_get_spend_mtd",
            new=AsyncMock(return_value=(0.0, None, spend_err_item)),
        ),
        patch.object(console_mod, "_count_open_approvals", new=AsyncMock(return_value=(0, None))),
        patch.object(console_mod, "_count_models", new=AsyncMock(return_value=(1, 1, None))),
        patch.object(console_mod, "_check_cli_auth", new=AsyncMock(return_value=[])),
        patch.object(console_mod, "_check_model_errors", new=AsyncMock(return_value=[])),
        patch.object(console_mod, "_check_failed_webhooks", new=AsyncMock(return_value=[])),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/settings/console")

    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    subsys_items = [a for a in body["attention"] if a["kind"] == "subsystem_error"]
    assert len(subsys_items) >= 1


@pytest.mark.asyncio
async def test_console_attention_truncated_at_five():
    """When more than 5 attention items exist, truncated_count reflects overflow."""
    app = _make_app(db=None)

    from butlers.api.routers.settings_console import AttentionItem as AI

    many_cli_items = [
        AI(
            tone="red",
            kind="auth_renewal",
            text=f"Provider {i} needs auth.",
            action_route="/secrets",
        )
        for i in range(6)  # 6 items will hit the cap of 5
    ]

    with (
        patch.object(console_mod, "_count_active_butlers", new=AsyncMock(return_value=(1, None))),
        patch.object(console_mod, "_get_spend_mtd", new=AsyncMock(return_value=(0.0, None, None))),
        patch.object(console_mod, "_count_open_approvals", new=AsyncMock(return_value=(0, None))),
        patch.object(console_mod, "_count_models", new=AsyncMock(return_value=(1, 1, None))),
        patch.object(console_mod, "_check_cli_auth", new=AsyncMock(return_value=many_cli_items)),
        patch.object(console_mod, "_check_model_errors", new=AsyncMock(return_value=[])),
        patch.object(console_mod, "_check_failed_webhooks", new=AsyncMock(return_value=[])),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/settings/console")

    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert len(body["attention"]) == 5
    assert body["attention_truncated_count"] == 1


@pytest.mark.asyncio
async def test_console_cache_returns_same_payload_within_ttl():
    """Two requests within the 10s TTL must return the same payload without re-running helpers."""
    app = _make_app(db=None)

    call_count = 0

    async def _counted(*_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        return (1, None)

    with (
        patch.object(console_mod, "_count_active_butlers", new=AsyncMock(side_effect=_counted)),
        patch.object(console_mod, "_get_spend_mtd", new=AsyncMock(return_value=(0.0, None, None))),
        patch.object(console_mod, "_count_open_approvals", new=AsyncMock(return_value=(0, None))),
        patch.object(console_mod, "_count_models", new=AsyncMock(return_value=(1, 2, None))),
        patch.object(console_mod, "_check_cli_auth", new=AsyncMock(return_value=[])),
        patch.object(console_mod, "_check_model_errors", new=AsyncMock(return_value=[])),
        patch.object(console_mod, "_check_failed_webhooks", new=AsyncMock(return_value=[])),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r1 = await client.get("/api/settings/console")
            r2 = await client.get("/api/settings/console")

    assert r1.status_code == 200
    assert r2.status_code == 200
    # _count_active_butlers should have been called only once (cache hit on second request)
    assert call_count == 1, f"Expected 1 helper call (cache hit), got {call_count}"
    # Both responses should be identical
    assert r1.json() == r2.json()


@pytest.mark.asyncio
async def test_console_cache_expires_and_refetches():
    """After the cache TTL expires, the next request re-runs the helpers."""
    app = _make_app(db=None)

    call_count = 0

    async def _counted(*_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        return (call_count, None)  # returns different values each call

    with (
        patch.object(console_mod, "_count_active_butlers", new=AsyncMock(side_effect=_counted)),
        patch.object(console_mod, "_get_spend_mtd", new=AsyncMock(return_value=(0.0, None, None))),
        patch.object(console_mod, "_count_open_approvals", new=AsyncMock(return_value=(0, None))),
        patch.object(console_mod, "_count_models", new=AsyncMock(return_value=(1, 2, None))),
        patch.object(console_mod, "_check_cli_auth", new=AsyncMock(return_value=[])),
        patch.object(console_mod, "_check_model_errors", new=AsyncMock(return_value=[])),
        patch.object(console_mod, "_check_failed_webhooks", new=AsyncMock(return_value=[])),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r1 = await client.get("/api/settings/console")
            # Simulate cache expiry by backdating the timestamp
            console_mod._cache_ts -= console_mod._CACHE_TTL_S + 1
            r2 = await client.get("/api/settings/console")

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert call_count == 2, f"Expected 2 helper calls (cache expired), got {call_count}"
