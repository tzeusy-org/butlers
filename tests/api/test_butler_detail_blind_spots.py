"""bu-2jtfw.13 AC5: GET /api/butlers/{name} and the injected preamble must
derive from the same evaluator call.

Rather than re-deriving the projection independently, the endpoint calls the
exact same ``butlers.core.expected_signals.evaluate_declared_signals()`` (via
``blind_spot_declarations.declared_signal_patterns``) that
``spawner_context.fetch_blind_spot_preamble`` calls — see
``_fetch_blind_spots`` in ``butlers/api/routers/butlers.py``. These tests
prove the endpoint surfaces that projection, not that the two call sites are
textually identical (covered by both importing the same functions).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.deps import ButlerConnectionInfo

from .conftest import make_butler_dir, make_mock_mcp_manager, make_test_app

pytestmark = pytest.mark.unit


class _FakePool:
    """Minimal pool double covering every query get_butler_detail issues."""

    def __init__(self, expected_signal_rows: list[dict[str, Any]] | None = None) -> None:
        self._expected_signal_rows = expected_signal_rows or []

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        if "public.expected_signals" in query:
            return self._expected_signal_rows
        if "v_qa_connector_state" in query:
            return []
        return []

    async def fetchval(self, *args: Any, **kwargs: Any) -> Any:
        return None

    async def fetchrow(self, *args: Any, **kwargs: Any) -> Any:
        return None


def _mock_db(pool: _FakePool) -> DatabaseManager:
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool
    db.fan_out_with_status = AsyncMock(return_value=({}, []))
    return db


async def test_no_declared_module_returns_empty_blind_spots(app, roster_dir) -> None:
    make_butler_dir(roster_dir, "general", 41201)
    configs = [ButlerConnectionInfo("general", 41201)]
    make_test_app(roster_dir, configs, make_mock_mcp_manager(online=True), app=app)
    from butlers.api.routers.butlers import _get_db_manager

    app.dependency_overrides[_get_db_manager] = lambda: _mock_db(_FakePool())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/general")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["blind_spots"] == []
    assert data["blind_spots_query_failed"] is False


async def test_health_module_absent_signal_surfaces_in_detail_endpoint(app, roster_dir) -> None:
    make_butler_dir(roster_dir, "health", 41202, modules={"health": ""})
    configs = [ButlerConnectionInfo("health", 41202)]
    make_test_app(roster_dir, configs, make_mock_mcp_manager(online=True), app=app)
    from butlers.api.routers.butlers import _get_db_manager

    now = datetime.now(UTC)
    pool = _FakePool(
        expected_signal_rows=[
            {
                "signal_key": "health:measurement-gap:weight",
                "producer": "owner",
                "producer_endpoint_identity": None,
                "expected_cadence_seconds": int(timedelta(days=14).total_seconds()),
                "last_observed_at": now - timedelta(days=30),
            }
        ]
    )
    app.dependency_overrides[_get_db_manager] = lambda: _mock_db(pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/health")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data["blind_spots"]) == 1
    assert data["blind_spots"][0]["signal_key"] == "health:measurement-gap:weight"
    assert data["blind_spots"][0]["state"] == "absent"
    assert data["blind_spots_evaluated_at"] is not None
    assert data["blind_spots_query_failed"] is False


async def test_query_failure_is_reported_not_silently_empty(app, roster_dir) -> None:
    make_butler_dir(roster_dir, "health", 41203, modules={"health": ""})
    configs = [ButlerConnectionInfo("health", 41203)]
    make_test_app(roster_dir, configs, make_mock_mcp_manager(online=True), app=app)
    from butlers.api.routers.butlers import _get_db_manager

    class _ExplodingPool(_FakePool):
        async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
            if "public.expected_signals" in query:
                raise RuntimeError("connection reset")
            return []

    app.dependency_overrides[_get_db_manager] = lambda: _mock_db(_ExplodingPool())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/health")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["blind_spots_query_failed"] is True
    assert data["blind_spots"] == []
