"""Tests for the ``cost_usd`` field on ``SessionSummary`` (bu-ptaub).

GET /api/sessions and GET /api/butlers/{name}/sessions estimate a best-effort
per-session USD cost from the model + token counts the summary read-model
already selects (no new SQL column, no migration) via the shared
``PricingConfig``/``estimate_session_cost`` primitives.

Verifies:
- cost_usd is None when pricing is unavailable (default app state).
- cost_usd is computed from model + tokens when pricing is available.
- cost_usd is priced from a single side when only input_tokens or only
  output_tokens is present (each count coalesces independently).
- cost_usd is None (never 0.0) for a running session with no token data yet.
- cost_usd is None for a model with no pricing entry.
- Same behavior holds for the butler-scoped endpoint.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.sessions import _get_db_manager as _sessions_get_db
from butlers.api.routers.sessions import _get_pricing_optional as _sessions_get_pricing
from butlers.core.pricing import ModelPricing, PricingConfig

pytestmark = pytest.mark.unit

_NOW = datetime.now(tz=UTC)

_PRICING = PricingConfig(
    models={
        "claude-sonnet": ModelPricing(
            input_price_per_token=0.000003,
            output_price_per_token=0.000015,
        ),
    }
)


def _make_session_row(**overrides: object) -> dict:
    row = {
        "id": uuid4(),
        "prompt": "test prompt",
        "trigger_source": "api",
        "request_id": None,
        "success": True,
        "started_at": _NOW,
        "completed_at": _NOW,
        "duration_ms": 500,
        "model": "claude-sonnet",
        "complexity": None,
        "input_tokens": 1000,
        "output_tokens": 1000,
        "cancelled_by_owner": False,
    }
    row.update(overrides)
    return row


def _make_record(row: dict):
    m = MagicMock()
    m.__getitem__ = MagicMock(side_effect=lambda key: row[key])
    return m


def _make_app_with_sessions(rows: list[dict]) -> object:
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["atlas"]
    mock_db.fan_out_with_status = AsyncMock(
        side_effect=lambda sql, args, **kw: ({"atlas": [_make_record(r) for r in rows]}, [])
    )
    app = create_app()
    app.dependency_overrides[_sessions_get_db] = lambda: mock_db
    return app


def _make_butler_app_with_sessions(rows: list[dict]) -> object:
    mock_pool = AsyncMock()
    mock_pool.fetchval = AsyncMock(return_value=len(rows))
    mock_pool.fetch = AsyncMock(return_value=[_make_record(r) for r in rows])
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool
    app = create_app()
    app.dependency_overrides[_sessions_get_db] = lambda: mock_db
    return app


# ---------------------------------------------------------------------------
# Cross-butler /api/sessions
# ---------------------------------------------------------------------------


async def test_cost_usd_none_when_pricing_unavailable() -> None:
    """Default app state (no init_pricing() called) -> cost_usd is None, not 0.0."""
    app = _make_app_with_sessions([_make_session_row()])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/sessions")
    assert resp.status_code == 200
    assert resp.json()["data"][0]["cost_usd"] is None


async def test_cost_usd_computed_from_model_and_tokens() -> None:
    """With pricing available, cost_usd is estimated from model + token counts."""
    app = _make_app_with_sessions([_make_session_row(input_tokens=1000, output_tokens=1000)])
    app.dependency_overrides[_sessions_get_pricing] = lambda: _PRICING
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/sessions")
    assert resp.status_code == 200
    cost_usd = resp.json()["data"][0]["cost_usd"]
    # 1000 * 0.000003 + 1000 * 0.000015 = 0.018
    assert cost_usd == pytest.approx(0.018)


async def test_cost_usd_with_only_input_tokens() -> None:
    """Only input_tokens present (output None) -> priced from input alone.

    _cost_usd_for_dto coalesces each count independently (``... or 0``), so a
    None output_tokens contributes 0 rather than voiding the whole estimate.
    """
    app = _make_app_with_sessions([_make_session_row(input_tokens=1000, output_tokens=None)])
    app.dependency_overrides[_sessions_get_pricing] = lambda: _PRICING
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/sessions")
    assert resp.status_code == 200
    cost_usd = resp.json()["data"][0]["cost_usd"]
    # 1000 * 0.000003 + 0 * 0.000015 = 0.003
    assert cost_usd == pytest.approx(0.003)


async def test_cost_usd_with_only_output_tokens() -> None:
    """Only output_tokens present (input None) -> priced from output alone."""
    app = _make_app_with_sessions([_make_session_row(input_tokens=None, output_tokens=1000)])
    app.dependency_overrides[_sessions_get_pricing] = lambda: _PRICING
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/sessions")
    assert resp.status_code == 200
    cost_usd = resp.json()["data"][0]["cost_usd"]
    # 0 * 0.000003 + 1000 * 0.000015 = 0.015
    assert cost_usd == pytest.approx(0.015)


async def test_cost_usd_none_for_running_session_without_tokens() -> None:
    """A running session with no recorded tokens yet -> None, never 0.0."""
    app = _make_app_with_sessions(
        [_make_session_row(success=None, input_tokens=None, output_tokens=None)]
    )
    app.dependency_overrides[_sessions_get_pricing] = lambda: _PRICING
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/sessions")
    assert resp.status_code == 200
    assert resp.json()["data"][0]["cost_usd"] is None


async def test_cost_usd_none_for_unknown_model() -> None:
    """A model with no pricing.toml entry -> None (never a misleading 0.0)."""
    app = _make_app_with_sessions([_make_session_row(model="some-unpriced-model")])
    app.dependency_overrides[_sessions_get_pricing] = lambda: _PRICING
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/sessions")
    assert resp.status_code == 200
    assert resp.json()["data"][0]["cost_usd"] is None


# ---------------------------------------------------------------------------
# Butler-scoped /api/butlers/{name}/sessions
# ---------------------------------------------------------------------------


async def test_butler_scoped_cost_usd_computed_from_model_and_tokens() -> None:
    app = _make_butler_app_with_sessions([_make_session_row(input_tokens=1000, output_tokens=1000)])
    app.dependency_overrides[_sessions_get_pricing] = lambda: _PRICING
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/atlas/sessions")
    assert resp.status_code == 200
    cost_usd = resp.json()["data"][0]["cost_usd"]
    assert cost_usd == pytest.approx(0.018)
