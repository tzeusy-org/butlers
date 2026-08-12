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

import asyncio
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
from butlers.core.model_routing import check_monthly_ceiling

pytestmark = pytest.mark.unit

# Real sleep captured before any test patches asyncio.sleep (mirrors
# tests/daemon/test_scheduler_loop.py's _fast_sleep pattern) -- patching
# "butlers.api.routers.settings_console.asyncio.sleep" patches the asyncio
# module's own `sleep` attribute (same module object everywhere), so a fast
# replacement must NOT call `asyncio.sleep` itself or it recurses into its
# own patch.
_real_sleep = asyncio.sleep

# Safety-net timeout for the delta-loop tests below. These tests are already
# event-based: each waits on an asyncio.Event that the loop sets on the
# observable state change, so a passing run returns the instant the event
# fires and never approaches this bound. The timeout exists ONLY to fail fast
# if the loop genuinely wedges (never reaches the awaited tick). It is
# therefore generous on purpose -- a tight wall-clock value (previously 2.0s)
# false-failed under runner load even though nothing was wrong (bu-lbtqc).
_LOOP_WEDGE_TIMEOUT_S = 30.0


async def _fast_sleep(_delay: float) -> None:
    """Yield control to the event loop without a real delay."""
    await _real_sleep(0)


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


def _mock_ledger_pool(usage_rows: list[dict], ceiling_row: dict | None):
    """Mock a ``switchboard`` pool serving ``price_mtd_from_ledger``'s
    ``pool.fetch`` (usage-by-model) and ``pool.fetchrow`` (ceiling row) calls.

    Mirrors ``tests/api/test_spend.py::_mock_forecast_ledger_pool``.
    """
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=usage_rows)
    pool.fetchrow = AsyncMock(return_value=ceiling_row)
    return pool


def _mock_db(pools: dict[str, MagicMock]):
    db = MagicMock(spec=DatabaseManager)
    db.pool.side_effect = lambda name: pools[name]
    return db


class _ApprovalPoolAcquire:
    """Async context manager used by the real approvals-table discovery path."""

    def __init__(self, connection: MagicMock):
        self._connection = connection

    async def __aenter__(self) -> MagicMock:
        return self._connection

    async def __aexit__(self, *_args) -> bool:
        return False


def _mock_approval_pool(pending_count: int) -> tuple[MagicMock, MagicMock]:
    """Return a pool whose real catalog probe and pending count both succeed."""
    probe_connection = MagicMock()
    probe_connection.fetchval = AsyncMock(return_value=True)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_ApprovalPoolAcquire(probe_connection))
    pool.fetchval = AsyncMock(return_value=pending_count)
    return pool, probe_connection


def _mock_approval_db(
    *pending_counts: int,
) -> tuple[MagicMock, dict[str, MagicMock], dict[str, MagicMock]]:
    """Build named approval pools for real ``_find_all_approvals_pools`` fan-out."""
    pools: dict[str, MagicMock] = {}
    probe_connections: dict[str, MagicMock] = {}
    for index, pending_count in enumerate(pending_counts):
        name = f"settings-console-approval-{index}"
        pool, probe_connection = _mock_approval_pool(pending_count)
        pools[name] = pool
        probe_connections[name] = probe_connection

    db = MagicMock(spec=DatabaseManager)
    db.butler_names = list(pools)
    db.pool = MagicMock(side_effect=pools.__getitem__)
    return db, pools, probe_connections


_LEDGER_USAGE_ROWS = [
    {
        "model_id": "claude-haiku",
        "input_tokens": 1000,
        "output_tokens": 500,
        "cached_input_tokens": 0,
        "cache_creation_tokens": 0,
    }
]


# ---------------------------------------------------------------------------
# Helper to reset module-level cache between tests
# ---------------------------------------------------------------------------


def _reset_cache():
    console_mod._cache_ts = 0.0
    console_mod._cache_payload = None


@pytest.fixture
def clear_approval_table_cache():
    """Keep real approvals-table discovery independent from other tests."""
    from butlers.api.routers.approvals import _clear_table_cache

    _clear_table_cache()
    yield
    _clear_table_cache()


@pytest.mark.asyncio
async def test_approval_and_model_headers_are_unavailable_without_db_manager():
    approvals, approval_err = await console_mod._count_open_approvals(None)
    verified, total, model_err = await console_mod._count_models(None)
    assert approvals is None and approval_err is not None
    assert verified is None and total is None and model_err is not None


@pytest.mark.asyncio
async def test_approval_pool_failure_never_silently_reduces_total():
    good = MagicMock()
    good.fetchval = AsyncMock(return_value=2)
    bad = MagicMock()
    bad.fetchval = AsyncMock(side_effect=RuntimeError("down"))
    db = MagicMock(spec=DatabaseManager)
    with patch(
        "butlers.api.routers.approvals._find_all_approvals_pools",
        new=AsyncMock(return_value=[good, bad]),
    ):
        total, err = await console_mod._count_open_approvals(db)
    assert total is None
    assert err is not None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_console_cache():
    """Reset the in-memory console cache before each test."""
    _reset_cache()
    yield
    _reset_cache()


# ---------------------------------------------------------------------------
# _get_spend_mtd: ledger-first MTD, no rolling-30d fan-out mislabel [bu-7o89u.2]
#
# Previously this summed a rolling-30d per-butler sessions_summary fan-out
# under an "MTD" label and could fire the near-ceiling alarm off that
# mismatched figure. It must now price from public.token_usage_ledger via the
# exact same butlers.core.model_routing.price_mtd_from_ledger helper
# check_monthly_ceiling (the spawn-deny gate) uses, so the console can never
# show a different MTD than the number that halts the fleet.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_spend_mtd_prices_from_ledger_not_fan_out():
    """_get_spend_mtd(db) returns the ledger-priced MTD and matching ceiling,
    without any MCP/butler-config fan-out involved."""
    pool = _mock_ledger_pool(_LEDGER_USAGE_ROWS, {"monthly_usd": 100.0})
    db = _mock_db({"switchboard": pool})

    with patch("butlers.api.pricing.estimate_session_cost", return_value=42.0):
        mtd, ceiling, err = await console_mod._get_spend_mtd(db)

    assert mtd == pytest.approx(42.0)
    assert ceiling == pytest.approx(100.0)
    assert err is None


@pytest.mark.asyncio
async def test_get_spend_mtd_matches_check_monthly_ceiling_same_fixture():
    """The console and the spawn-deny gate must agree on MTD from the same
    ledger fixture -- the exact divergence bu-7o89u.2 closes for the console
    (mirrors bu-7o89u.1's forecast-endpoint agreement test)."""
    pool = _mock_ledger_pool(_LEDGER_USAGE_ROWS, {"monthly_usd": 100.0})
    db = _mock_db({"switchboard": pool})

    with patch("butlers.api.pricing.estimate_session_cost", return_value=42.0):
        gate_status = await check_monthly_ceiling(pool)
        mtd, ceiling, err = await console_mod._get_spend_mtd(db)

    assert err is None
    assert gate_status.mtd_usd == pytest.approx(42.0)
    assert mtd == pytest.approx(gate_status.mtd_usd)
    assert ceiling == pytest.approx(gate_status.ceiling_usd)


@pytest.mark.asyncio
async def test_get_spend_mtd_no_db_returns_null_not_fabricated_zero():
    """No DatabaseManager wired -- there is no MCP fallback for ledger rows --
    must report (None, None, amber-item), never a fabricated $0."""
    mtd, ceiling, err = await console_mod._get_spend_mtd(None)

    assert mtd is None
    assert ceiling is None
    assert err is not None
    assert err.tone == "amber"
    assert err.kind == "subsystem_error"


@pytest.mark.asyncio
async def test_get_spend_mtd_ledger_failure_returns_null_not_fabricated_zero():
    """A ledger query failure must surface as (None, None, amber-item), never
    a truthful-looking $0 MTD (butlers/CLAUDE.md degraded-envelope convention)."""
    pool = MagicMock()
    pool.fetch = AsyncMock(side_effect=RuntimeError("connection reset"))
    pool.fetchrow = AsyncMock(return_value={"monthly_usd": 100.0})
    db = _mock_db({"switchboard": pool})

    mtd, ceiling, err = await console_mod._get_spend_mtd(db)

    assert mtd is None
    assert ceiling is None
    assert err is not None
    assert err.tone == "amber"


@pytest.mark.asyncio
async def test_console_endpoint_spend_mtd_uses_ledger_helper_end_to_end():
    """GET /api/settings/console's spend_mtd_usd matches price_mtd_from_ledger
    on a real (mocked-pool) ledger fixture, exercised through the full
    aggregator -- not a mocked _get_spend_mtd -- so a wiring regression that
    reintroduces the fan-out sum would be caught here."""
    pool = _mock_ledger_pool(_LEDGER_USAGE_ROWS, {"monthly_usd": 100.0})
    db = _mock_db({"switchboard": pool})
    app = _make_app(db=db)

    with (
        patch("butlers.api.pricing.estimate_session_cost", return_value=42.0),
        patch.object(console_mod, "_count_active_butlers", new=AsyncMock(return_value=(1, None))),
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
    assert body["header_counts"]["spend_mtd_usd"] == pytest.approx(42.0)
    # 42 / 100 = 42% -- well under the 90% near-ceiling threshold, so no false
    # alarm from this true MTD.
    ceiling_items = [a for a in body["attention"] if a["kind"] == "spend_ceiling"]
    assert ceiling_items == []


@pytest.mark.asyncio
async def test_console_endpoint_ceiling_alarm_fires_only_on_true_mtd_breach():
    """The near-ceiling alarm compares the ledger-priced true MTD against the
    same public.spend_ceiling row check_monthly_ceiling reads -- it fires when
    that ratio crosses 90%, not off any other figure."""
    pool = _mock_ledger_pool(_LEDGER_USAGE_ROWS, {"monthly_usd": 100.0})
    db = _mock_db({"switchboard": pool})
    app = _make_app(db=db)

    with (
        patch("butlers.api.pricing.estimate_session_cost", return_value=95.0),
        patch(
            "butlers.api.routers.spend.projection_confidence_for",
            new=lambda days_elapsed: "normal",
        ),
        patch.object(console_mod, "_count_active_butlers", new=AsyncMock(return_value=(1, None))),
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
    assert body["header_counts"]["spend_mtd_usd"] == pytest.approx(95.0)
    ceiling_items = [a for a in body["attention"] if a["kind"] == "spend_ceiling"]
    assert len(ceiling_items) == 1, f"Expected the true-MTD breach to fire, got {ceiling_items}"


@pytest.mark.asyncio
async def test_console_healthy_zero_results_serialize_without_attention():
    """Healthy subsystem zeros remain truthful and produce no attention."""
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
    assert body["attention_all"] == []
    assert body["attention_truncated_count"] == 0


@pytest.mark.asyncio
async def test_check_cli_auth_uses_provider_passport_focus_route_and_identity():
    """Each CLI renewal points at its own Passport runtime row.

    The ``c:cli-auth/<provider>`` focus grammar belongs to the Passport
    surface; Settings Console only constructs its truthful, provider-specific
    door and never starts an auth flow itself.
    """
    from butlers.cli_auth.health import AuthHealthResult, AuthHealthState
    from butlers.cli_auth.registry import PROVIDERS, CLIAuthProviderDef

    provider = CLIAuthProviderDef(
        name="acme/main",
        display_name="Acme Main",
        runtime="codex",
        binary_name="codex",
    )
    health = AuthHealthResult(
        provider=provider.name,
        state=AuthHealthState.not_authenticated,
    )

    with (
        patch.dict(PROVIDERS, {provider.name: provider}, clear=True),
        patch.object(CLIAuthProviderDef, "is_available", return_value=True),
        patch(
            "butlers.cli_auth.health.probe_all", new=AsyncMock(return_value={provider.name: health})
        ),
    ):
        items = await console_mod._check_cli_auth(None)

    assert [(item.id, item.action_route) for item in items] == [
        ("auth_renewal:acme/main", "/secrets?focus=c:cli-auth/acme%2Fmain")
    ]


@pytest.mark.asyncio
async def test_check_cli_auth_passes_only_explicit_global_authority_to_codex_probe():
    """REQ-core-credentials-001: console probes never infer Codex authority."""
    from butlers.cli_auth.health import AuthHealthResult, AuthHealthState
    from butlers.cli_auth.registry import PROVIDERS, CLIAuthProviderDef

    provider = CLIAuthProviderDef(
        name="codex",
        display_name="Codex",
        runtime="codex",
        binary_name="codex",
    )
    pool = MagicMock()
    db = MagicMock()
    db.credential_shared_pool.return_value = pool
    probe_all = AsyncMock(
        return_value={
            "codex": AuthHealthResult(
                provider="codex",
                state=AuthHealthState.authenticated,
            )
        }
    )

    with (
        patch.dict(PROVIDERS, {"codex": provider}, clear=True),
        patch.object(CLIAuthProviderDef, "is_available", return_value=True),
        patch("butlers.cli_auth.health.probe_all", new=probe_all),
    ):
        assert await console_mod._check_cli_auth(db) == []

    authority = probe_all.await_args.kwargs["codex_authority"]
    assert authority.pool is pool
    assert authority.has_system_global_authority is True
    assert "credential_store" not in probe_all.await_args.kwargs


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
        id="subsystem_error:spend",
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
async def test_console_partial_failure_nulls_only_the_failed_header_count():
    """A failed subsystem's header_counts field is None, not a confident 0 --
    healthy subsystems' fields keep their real value in the same response.
    """
    app = _make_app(db=None)

    spend_err_item = console_mod.AttentionItem(
        id="subsystem_error:spend",
        tone="amber",
        kind="subsystem_error",
        text="Could not fetch spend data — totals may be unavailable.",
        action_route="/settings/spend",
    )

    with (
        patch.object(console_mod, "_count_active_butlers", new=AsyncMock(return_value=(3, None))),
        patch.object(
            console_mod,
            "_get_spend_mtd",
            new=AsyncMock(return_value=(0.0, None, spend_err_item)),
        ),
        patch.object(console_mod, "_count_open_approvals", new=AsyncMock(return_value=(2, None))),
        patch.object(console_mod, "_count_models", new=AsyncMock(return_value=(1, 1, None))),
        patch.object(console_mod, "_check_cli_auth", new=AsyncMock(return_value=[])),
        patch.object(console_mod, "_check_model_errors", new=AsyncMock(return_value=[])),
        patch.object(console_mod, "_check_failed_webhooks", new=AsyncMock(return_value=[])),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/settings/console")

    assert resp.status_code == 200, resp.text
    counts = resp.json()["data"]["header_counts"]
    assert counts["spend_mtd_usd"] is None
    # Healthy subsystems keep their real values, never nulled out by a
    # sibling subsystem's failure.
    assert counts["active_butlers"] == 3
    assert counts["open_approvals"] == 2
    assert counts["models_verified"] == 1
    assert counts["models_total"] == 1


@pytest.mark.asyncio
async def test_console_attention_truncated_at_five():
    """The compatibility cap keeps the complete, uniquely identified list."""
    app = _make_app(db=None)

    from butlers.api.routers.settings_console import AttentionItem as AI

    many_cli_items = [
        AI(
            id=f"auth_renewal:provider-{i}",
            tone="red",
            kind="auth_renewal",
            text=f"Provider {i} needs auth.",
            action_route=f"/secrets?focus=c:cli-auth/provider-{i}",
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
    assert len(body["attention_all"]) == 6
    assert [item["id"] for item in body["attention_all"]] == [
        f"auth_renewal:provider-{i}" for i in range(6)
    ]
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


# ---------------------------------------------------------------------------
# _check_failed_webhooks: queries last_delivery_ok (production deliveries)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_failed_webhooks_returns_empty_when_no_failures():
    """_check_failed_webhooks returns [] when all production deliveries succeeded."""
    from butlers.api.routers.settings_console import _check_failed_webhooks

    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=0)
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool

    items = await _check_failed_webhooks(db)

    assert items == []
    # Verify the query targets last_delivery_ok, not last_test_ok.
    call_sql = pool.fetchval.call_args[0][0]
    assert "last_delivery_ok" in call_sql, "Should query last_delivery_ok not last_test_ok"
    assert "last_test_ok" not in call_sql, "Must not query test state for production alert"


@pytest.mark.asyncio
async def test_check_failed_webhooks_returns_attention_item_on_exhaustion():
    """_check_failed_webhooks returns an amber webhook_failure item when deliveries failed."""
    from butlers.api.routers.settings_console import _check_failed_webhooks

    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=2)
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool

    items = await _check_failed_webhooks(db)

    assert len(items) == 1
    item = items[0]
    assert item.kind == "webhook_failure"
    assert item.tone == "amber"
    assert "2" in item.text
    assert item.action_route == "/settings/permissions"


@pytest.mark.asyncio
async def test_check_failed_webhooks_returns_empty_when_no_db():
    """_check_failed_webhooks returns [] without querying when db is None."""
    from butlers.api.routers.settings_console import _check_failed_webhooks

    items = await _check_failed_webhooks(None)

    assert items == []


@pytest.mark.asyncio
async def test_check_failed_webhooks_swallows_db_errors():
    """_check_failed_webhooks returns [] on DB error rather than raising."""
    from butlers.api.routers.settings_console import _check_failed_webhooks

    pool = AsyncMock()
    pool.fetchval = AsyncMock(side_effect=RuntimeError("DB gone"))
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool

    items = await _check_failed_webhooks(db)

    assert items == []


# ---------------------------------------------------------------------------
# _compute_console_deltas -- pure diff helper shared by the legacy
# per-connection WS loop and the standalone bus-emitting background loop
# ---------------------------------------------------------------------------


def _console_payload(
    *,
    active_butlers: int | None = 1,
    attention: list[dict] | None = None,
    attention_all: list[dict] | None = None,
) -> dict:
    all_items = attention_all if attention_all is not None else (attention or [])
    visible_items = attention if attention is not None else all_items[:5]
    return {
        "header_counts": {
            "active_butlers": active_butlers,
            "spend_mtd_usd": 0.0,
            "open_approvals": 0,
            "models_verified": 0,
            "models_total": 0,
        },
        "attention": visible_items,
        "attention_all": all_items,
        "attention_truncated_count": max(0, len(all_items) - len(visible_items)),
    }


def test_compute_console_deltas_no_change_is_empty():
    payload = _console_payload(active_butlers=3)
    header_delta, added, removed = console_mod._compute_console_deltas(payload, payload)
    assert header_delta == {}
    assert added == []
    assert removed == []


def test_compute_console_deltas_header_change_only():
    prev = _console_payload(active_butlers=1)
    new = _console_payload(active_butlers=2)
    header_delta, added, removed = console_mod._compute_console_deltas(prev, new)
    assert header_delta == {"active_butlers": 2}
    assert added == []
    assert removed == []


def test_compute_console_deltas_header_count_degrades_to_none():
    prev = _console_payload(active_butlers=3)
    new = _console_payload(active_butlers=None)

    header_delta, added, removed = console_mod._compute_console_deltas(prev, new)

    assert "active_butlers" in header_delta
    assert header_delta["active_butlers"] is None
    assert added == []
    assert removed == []


def test_compute_console_deltas_attention_add():
    item = {
        "id": "open_approvals",
        "tone": "red",
        "kind": "open_approvals",
        "text": "x",
        "action_route": "/approvals",
    }
    prev = _console_payload(attention=[])
    new = _console_payload(attention=[item])
    header_delta, added, removed = console_mod._compute_console_deltas(prev, new)
    assert header_delta == {}
    assert added == [item]
    assert removed == []


def test_compute_console_deltas_attention_remove():
    item = {
        "id": "spend_ceiling",
        "tone": "amber",
        "kind": "spend_ceiling",
        "text": "x",
        "action_route": "/settings/spend",
    }
    prev = _console_payload(attention=[item])
    new = _console_payload(attention=[])
    header_delta, added, removed = console_mod._compute_console_deltas(prev, new)
    assert header_delta == {}
    assert added == []
    assert removed == ["spend_ceiling"]


def test_compute_console_deltas_keeps_same_kind_items_distinct_by_identity():
    codex = {
        "id": "auth_renewal:codex",
        "tone": "red",
        "kind": "auth_renewal",
        "text": "Codex needs auth.",
        "action_route": "/secrets?focus=c:cli-auth/codex",
    }
    opencode = {
        "id": "auth_renewal:opencode",
        "tone": "red",
        "kind": "auth_renewal",
        "text": "OpenCode needs auth.",
        "action_route": "/secrets?focus=c:cli-auth/opencode",
    }

    header_delta, added, removed = console_mod._compute_console_deltas(
        _console_payload(attention_all=[]),
        _console_payload(attention_all=[codex, opencode]),
    )

    assert header_delta == {}
    assert added == [codex, opencode]
    assert removed == []

    header_delta, added, removed = console_mod._compute_console_deltas(
        _console_payload(attention_all=[codex, opencode]),
        _console_payload(attention_all=[opencode]),
    )

    assert header_delta == {}
    assert added == []
    assert removed == ["auth_renewal:codex"]


# ---------------------------------------------------------------------------
# run_settings_console_delta_loop -- standalone bus-emitting background task
# (bu-3quv8, completes bu-qvnce.14 slice 2 on the backend)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_settings_console_delta_loop_emits_nothing_on_first_tick():
    """The first tick has no baseline to diff against -- no emit_event calls,
    the REST GET's own snapshot is the client's initial state."""
    payload = _console_payload(active_butlers=1)
    first_tick_done = asyncio.Event()

    async def _fake_build(*_args, **_kwargs):
        first_tick_done.set()
        return payload

    with (
        patch.object(console_mod, "_build_console_payload", side_effect=_fake_build),
        patch("butlers.api.routers.settings_console.asyncio.sleep", side_effect=_fast_sleep),
        patch.object(console_mod, "emit_event") as mock_emit,
    ):
        task = asyncio.create_task(
            console_mod.run_settings_console_delta_loop(
                _BUTLER_CONFIG, MagicMock(), _PRICING, None, interval_s=0.001
            )
        )
        try:
            await asyncio.wait_for(first_tick_done.wait(), timeout=_LOOP_WEDGE_TIMEOUT_S)
            await _real_sleep(0)  # let the tick finish updating the cache
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    mock_emit.assert_not_called()
    assert console_mod._cache_payload == payload


@pytest.mark.asyncio
async def test_settings_console_delta_loop_emits_deltas_on_change():
    """A change between tick 1 and tick 2 fans header_delta / attention_add /
    attention_remove onto the fleet event bus via emit_event."""
    added_item = {
        "id": "open_approvals",
        "tone": "red",
        "kind": "open_approvals",
        "text": "x",
        "action_route": "/approvals",
    }
    removed_item = {
        "id": "spend_ceiling",
        "tone": "amber",
        "kind": "spend_ceiling",
        "text": "y",
        "action_route": "/settings/spend",
    }
    payload_1 = _console_payload(active_butlers=1, attention_all=[removed_item])
    payload_2 = _console_payload(active_butlers=2, attention_all=[added_item])

    call_count = 0
    second_tick_done = asyncio.Event()

    async def _fake_build(*_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return payload_1
        second_tick_done.set()
        return payload_2

    with (
        patch.object(console_mod, "_build_console_payload", side_effect=_fake_build),
        patch("butlers.api.routers.settings_console.asyncio.sleep", side_effect=_fast_sleep),
        patch.object(console_mod, "emit_event") as mock_emit,
    ):
        task = asyncio.create_task(
            console_mod.run_settings_console_delta_loop(
                _BUTLER_CONFIG, MagicMock(), _PRICING, None, interval_s=0.001
            )
        )
        try:
            await asyncio.wait_for(second_tick_done.wait(), timeout=_LOOP_WEDGE_TIMEOUT_S)
            await _real_sleep(0)  # let the tick finish emitting before we cancel
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    calls = {c.args[0]: c.args[1] for c in mock_emit.call_args_list}
    assert calls["header_delta"] == {"active_butlers": 2}
    assert calls["attention_add"] == added_item
    assert calls["attention_remove"] == {"id": "spend_ceiling"}
    assert console_mod._cache_payload == payload_2


@pytest.mark.asyncio
async def test_settings_console_delta_loop_continues_after_aggregation_failure():
    """A failed aggregation tick is logged and swallowed -- the loop keeps
    running and still catches up on the next successful tick."""
    payload = _console_payload(active_butlers=5)
    call_count = 0
    recovered = asyncio.Event()

    async def _fake_build(*_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("aggregation exploded")
        recovered.set()
        return payload

    with (
        patch.object(console_mod, "_build_console_payload", side_effect=_fake_build),
        patch("butlers.api.routers.settings_console.asyncio.sleep", side_effect=_fast_sleep),
        patch.object(console_mod, "emit_event") as mock_emit,
    ):
        task = asyncio.create_task(
            console_mod.run_settings_console_delta_loop(
                _BUTLER_CONFIG, MagicMock(), _PRICING, None, interval_s=0.001
            )
        )
        try:
            await asyncio.wait_for(recovered.wait(), timeout=_LOOP_WEDGE_TIMEOUT_S)
            await _real_sleep(0)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    # The failed first tick never established a baseline; the recovered
    # second tick becomes the new first-successful-tick, so there is still no
    # diff to emit -- but the loop must have survived the RuntimeError to get
    # here at all.
    mock_emit.assert_not_called()
    assert console_mod._cache_payload == payload


@pytest.mark.asyncio
async def test_settings_console_delta_loop_rejects_non_positive_interval():
    with pytest.raises(ValueError):
        await console_mod.run_settings_console_delta_loop(
            _BUTLER_CONFIG, MagicMock(), _PRICING, None, interval_s=0
        )


@pytest.mark.asyncio
async def test_console_endpoint_db_none_reports_all_unavailable_subsystems():
    app = _make_app(db=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/settings/console")
    body = response.json()["data"]
    assert body["header_counts"]["open_approvals"] is None
    assert body["header_counts"]["models_total"] is None
    # ``attention`` is intentionally capped at five items; subsystem failures
    # must be asserted against the complete ordered collection so competing
    # attention items cannot displace an otherwise truthful failure signal.
    assert {item["id"] for item in body["attention_all"]} >= {
        "subsystem_error:approvals",
        "subsystem_error:models",
    }
    assert body["attention"] == body["attention_all"][:5]
    assert body["attention_truncated_count"] == max(
        0, len(body["attention_all"]) - len(body["attention"])
    )


@pytest.mark.asyncio
async def test_console_endpoint_healthy_empty_approvals_remain_truthful(
    clear_approval_table_cache,
):
    db, pools, probe_connections = _mock_approval_db(0)
    app = _make_app(db=db)
    with (
        patch.object(console_mod, "_count_active_butlers", new=AsyncMock(return_value=(1, None))),
        patch.object(console_mod, "_get_spend_mtd", new=AsyncMock(return_value=(0.0, None, None))),
        patch.object(console_mod, "_count_models", new=AsyncMock(return_value=(0, 0, None))),
        patch.object(console_mod, "_check_cli_auth", new=AsyncMock(return_value=[])),
        patch.object(console_mod, "_check_model_errors", new=AsyncMock(return_value=[])),
        patch.object(console_mod, "_check_failed_webhooks", new=AsyncMock(return_value=[])),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/settings/console")

    assert response.status_code == 200, response.text
    body = response.json()["data"]
    attention_ids = {item["id"] for item in body["attention"]}
    assert body["header_counts"]["open_approvals"] == 0
    assert "open_approvals" not in attention_ids
    assert "subsystem_error:approvals" not in attention_ids
    for name, pool in pools.items():
        probe_connections[name].fetchval.assert_awaited_once_with(
            "SELECT to_regclass($1) IS NOT NULL", "pending_actions"
        )
        pool.fetchval.assert_awaited_once_with(
            "SELECT COUNT(*) FROM pending_actions WHERE status = 'pending'"
        )


@pytest.mark.asyncio
async def test_console_endpoint_sums_two_healthy_approval_pools(
    clear_approval_table_cache,
):
    db, pools, probe_connections = _mock_approval_db(2, 3)
    app = _make_app(db=db)
    with (
        patch.object(console_mod, "_count_active_butlers", new=AsyncMock(return_value=(1, None))),
        patch.object(console_mod, "_get_spend_mtd", new=AsyncMock(return_value=(0.0, None, None))),
        patch.object(console_mod, "_count_models", new=AsyncMock(return_value=(0, 0, None))),
        patch.object(console_mod, "_check_cli_auth", new=AsyncMock(return_value=[])),
        patch.object(console_mod, "_check_model_errors", new=AsyncMock(return_value=[])),
        patch.object(console_mod, "_check_failed_webhooks", new=AsyncMock(return_value=[])),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/settings/console")

    assert response.status_code == 200, response.text
    body = response.json()["data"]
    approval_items = [item for item in body["attention"] if item["id"] == "open_approvals"]
    assert body["header_counts"]["open_approvals"] == 5
    assert approval_items == [
        {
            "id": "open_approvals",
            "tone": "red",
            "kind": "open_approvals",
            "text": "5 approval(s) are waiting for your review.",
            "action_route": "/approvals",
        }
    ]
    assert "subsystem_error:approvals" not in {item["id"] for item in body["attention"]}
    for name, pool in pools.items():
        probe_connections[name].fetchval.assert_awaited_once_with(
            "SELECT to_regclass($1) IS NOT NULL", "pending_actions"
        )
        pool.fetchval.assert_awaited_once_with(
            "SELECT COUNT(*) FROM pending_actions WHERE status = 'pending'"
        )


@pytest.mark.asyncio
async def test_console_endpoint_degraded_approval_discovery_never_returns_partial_total(
    clear_approval_table_cache,
):
    db, pools, probe_connections = _mock_approval_db(2, 3)
    failing_name = "settings-console-approval-1"
    probe_connections[failing_name].fetchval = AsyncMock(
        side_effect=RuntimeError("catalog unavailable")
    )
    app = _make_app(db=db)
    with (
        patch.object(console_mod, "_count_active_butlers", new=AsyncMock(return_value=(1, None))),
        patch.object(console_mod, "_get_spend_mtd", new=AsyncMock(return_value=(0.0, None, None))),
        patch.object(console_mod, "_count_models", new=AsyncMock(return_value=(0, 0, None))),
        patch.object(console_mod, "_check_cli_auth", new=AsyncMock(return_value=[])),
        patch.object(console_mod, "_check_model_errors", new=AsyncMock(return_value=[])),
        patch.object(console_mod, "_check_failed_webhooks", new=AsyncMock(return_value=[])),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/settings/console")

    assert response.status_code == 200, response.text
    body = response.json()["data"]
    approval_items = [
        item for item in body["attention"] if item["id"] == "subsystem_error:approvals"
    ]
    assert body["header_counts"]["open_approvals"] is None
    assert approval_items == [
        {
            "id": "subsystem_error:approvals",
            "tone": "amber",
            "kind": "subsystem_error",
            "text": "Could not reach the approvals subsystem.",
            "action_route": "/approvals",
        }
    ]
    assert "open_approvals" not in {item["id"] for item in body["attention"]}
    for probe_connection in probe_connections.values():
        probe_connection.fetchval.assert_awaited_once_with(
            "SELECT to_regclass($1) IS NOT NULL", "pending_actions"
        )
    for pool in pools.values():
        pool.fetchval.assert_not_awaited()
