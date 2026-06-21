"""Backend tests for ButlerSummary.last_session_started_at field.

Asserts:
  1. ``GET /api/butlers/{name}`` returns ``last_session_started_at`` as ``None``
     when the butler has no sessions.
  2. ``GET /api/butlers/{name}`` returns the MAX ``started_at`` when sessions exist.
  3. No ``butler_name`` column filter is used in the SQL query (schema-scoped pool).

Bead: bu-iuol4.8
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.deps import (
    ButlerConnectionInfo,
    get_butler_configs,
    get_mcp_manager,
)
from butlers.api.routers.butlers import _get_db_manager, _get_roster_dir

from .conftest import make_mock_mcp_manager

pytestmark = pytest.mark.unit

_VALID_BUTLER_TOML = """\
[butler]
name = "{name}"
port = {port}
description = "Test butler for last_session_started_at"

[butler.db]
name = "butlers"
schema = "{name}"
"""


def _make_butler_dir(roster_dir, name: str, port: int):
    """Create a minimal valid butler directory for the config loader."""
    butler_dir = roster_dir / name
    butler_dir.mkdir(parents=True, exist_ok=True)
    (butler_dir / "butler.toml").write_text(_VALID_BUTLER_TOML.format(name=name, port=port))
    return butler_dir


def _mock_db(last_session_started_at: datetime | None) -> DatabaseManager:
    """Build a mock DatabaseManager whose butler pool returns a fetchval result.

    ``fan_out`` is stubbed to satisfy ``_fetch_sessions_24h``.
    ``pool`` is configured to return a butler-scoped pool whose ``fetchval``
    returns the given ``last_session_started_at`` value (simulating
    ``SELECT CASE WHEN to_regclass('sessions') ... END``).
    The switchboard pool lookup raises ``KeyError`` so ``registered_duration``
    is skipped, keeping the mock simple.
    """
    butler_pool = AsyncMock()
    butler_pool.fetchval = AsyncMock(return_value=last_session_started_at)

    def _pool(name: str):
        if name == "switchboard":
            raise KeyError("switchboard not available")
        return butler_pool

    db = MagicMock(spec=DatabaseManager)
    db.fan_out = AsyncMock(return_value={})
    db.pool = MagicMock(side_effect=_pool)
    return db


async def _get_detail(app, name: str) -> dict:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/butlers/{name}")
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


class TestLastSessionStartedAt:
    """last_session_started_at is present on GET /api/butlers/{name}."""

    async def test_returns_none_when_no_sessions(self, app, roster_dir):
        """last_session_started_at is null when the butler has no sessions."""
        _make_butler_dir(roster_dir, "general", 41101)
        configs = [ButlerConnectionInfo("general", 41101)]
        db = _mock_db(last_session_started_at=None)
        app.dependency_overrides[get_butler_configs] = lambda: configs
        app.dependency_overrides[get_mcp_manager] = lambda: make_mock_mcp_manager(online=True)
        app.dependency_overrides[_get_roster_dir] = lambda: roster_dir
        app.dependency_overrides[_get_db_manager] = lambda: db

        data = await _get_detail(app, "general")
        assert "last_session_started_at" in data
        assert data["last_session_started_at"] is None

        # Isolation invariant (only guard for this): the butler-scoped pool is
        # queried and the SQL carries no butler_name column filter.
        pool_calls = [call.args[0] for call in db.pool.call_args_list]
        assert "general" in pool_calls
        butler_pool = db.pool("general")
        fetchval_calls = butler_pool.fetchval.call_args_list
        assert fetchval_calls, "fetchval should have been called on the butler pool"
        for fv_call in fetchval_calls:
            sql = fv_call.args[0] if fv_call.args else ""
            assert "butler_name" not in sql, (
                f"SQL must not filter by butler_name column (schema-scoped): {sql!r}"
            )

    async def test_returns_max_started_at_when_sessions_exist(self, app, roster_dir):
        """last_session_started_at equals the MAX started_at from sessions."""
        expected_dt = datetime(2025, 5, 10, 14, 30, 0, tzinfo=UTC)
        _make_butler_dir(roster_dir, "general", 41101)
        configs = [ButlerConnectionInfo("general", 41101)]
        db = _mock_db(last_session_started_at=expected_dt)
        app.dependency_overrides[get_butler_configs] = lambda: configs
        app.dependency_overrides[get_mcp_manager] = lambda: make_mock_mcp_manager(online=True)
        app.dependency_overrides[_get_roster_dir] = lambda: roster_dir
        app.dependency_overrides[_get_db_manager] = lambda: db

        data = await _get_detail(app, "general")
        assert data["last_session_started_at"] is not None
        # Response is an ISO-8601 string; parse and compare
        returned_dt = datetime.fromisoformat(data["last_session_started_at"])
        assert returned_dt == expected_dt
