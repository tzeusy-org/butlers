"""Tests for GET /api/butlers/{name}/analytics/friction (bu-8cdl1.9 S3).

Verifies the console-surfacing endpoint combines the typed
``sessions_friction`` ledger with ``sessions_summary``'s outcome fields for
the same period, zero-fills every known friction kind, and returns 503 when
the butler's DB pool is unavailable.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.deps import get_db
from butlers.testing.fake_pool import FakePool, fake_pool
from butlers.testing.records import Record, row
from tests.api.auth_helpers import create_authenticated_domain_app as create_app

pytestmark = pytest.mark.unit

_ALL_KINDS = (
    "degenerate_tool_loop",
    "guardrail_termination",
    "classification_timeout",
    "recovered_error",
    "dead_end",
)


_ZERO_TOTALS = {
    "total_sessions": 0,
    "total_input_tokens": 0,
    "total_output_tokens": 0,
    "total_cached_input_tokens": 0,
    "total_cache_creation_tokens": 0,
    "succeeded": 0,
    "failed": 0,
}


def _summary_pool(
    *,
    friction_rows: list[Record] | None = None,
    totals: dict | None = None,
    by_model_rows: list[Record] | None = None,
    by_marker_rows: list[Record] | None = None,
) -> FakePool:
    """Pool answering the endpoint's reads in call order.

    The endpoint awaits ``friction_summary`` (one fetch) then ``sessions_summary``
    (totals fetchrow, then by_model and by_error_marker fetches). SQL correctness
    of both readers is covered on a real DB in tests/core/test_core_sessions.py.
    """
    return fake_pool(
        fetchrow=row(**(totals or _ZERO_TOTALS)),
        fetch=AsyncMock(
            side_effect=[friction_rows or [], by_model_rows or [], by_marker_rows or []]
        ),
    )


def _make_app(pool: FakePool) -> object:
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = pool
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db
    return app


def _make_app_missing_butler(butler_name: str) -> object:
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.side_effect = KeyError(f"No pool for butler: {butler_name}")
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db
    return app


async def test_friction_summary_zero_fills_every_kind() -> None:
    """A clean-session butler still reports every kind at 0, not a sparse dict."""
    pool = _summary_pool()
    app = _make_app(pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/atlas/analytics/friction")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["period"] == "today"
    assert data["total"] == 0
    assert set(data["by_kind"].keys()) == set(_ALL_KINDS)
    assert all(count == 0 for count in data["by_kind"].values())
    assert data["succeeded"] == 0
    assert data["failed"] == 0
    assert data["by_error_marker"] == {}


async def test_friction_summary_combines_friction_and_outcomes() -> None:
    """Friction counts and session outcomes both land in one response."""
    pool = _summary_pool(
        friction_rows=[
            row(kind="guardrail_termination", count=3),
            row(kind="dead_end", count=1),
        ],
        totals={
            "total_sessions": 20,
            "total_input_tokens": 100,
            "total_output_tokens": 200,
            "total_cached_input_tokens": 0,
            "total_cache_creation_tokens": 0,
            "succeeded": 16,
            "failed": 4,
        },
        by_marker_rows=[row(marker="token_budget_exceeded", count=3)],
    )
    app = _make_app(pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/atlas/analytics/friction?period=7d")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["period"] == "7d"
    assert data["total"] == 4
    assert data["by_kind"]["guardrail_termination"] == 3
    assert data["by_kind"]["dead_end"] == 1
    assert data["by_kind"]["recovered_error"] == 0
    assert data["succeeded"] == 16
    assert data["failed"] == 4
    assert data["by_error_marker"] == {"token_budget_exceeded": 3}


async def test_friction_summary_invalid_period_returns_422() -> None:
    """period is constrained to the Literal set at the FastAPI layer."""
    pool = _summary_pool()
    app = _make_app(pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/atlas/analytics/friction?period=90d")

    assert resp.status_code == 422


async def test_friction_summary_missing_butler_db_returns_503() -> None:
    """Returns 503 when the butler's DB pool is not registered."""
    app = _make_app_missing_butler("unknown-butler")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/unknown-butler/analytics/friction")

    assert resp.status_code == 503
