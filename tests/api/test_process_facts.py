"""Backend tests for butler detail process facts.

Asserts:
  1. ``GET /api/butlers/{name}`` includes ``process_facts`` with all four fields.
  2. ``process_facts`` never contains a ``pid`` field.
  3. ``config_path`` is the roster-relative path.
  4. ``container_name`` is derived from ``BUTLERS_HOST`` env var.
  5. ``registered_duration_seconds`` comes from switchboard registry and is > 0 when present.
  6. ``registered_duration_seconds`` is None when switchboard is unavailable.

Bead: bu-8hbph.2
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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

# Minimal butler.toml that passes the current config loader (no top-level [runtime]).
_VALID_BUTLER_TOML = """\
[butler]
name = "{name}"
port = {port}
description = "Test butler for process facts"

[butler.db]
name = "butlers"
schema = "{name}"
"""


def _make_butler_dir(roster_dir, name: str, port: int):
    """Create a minimal valid butler directory for the current config loader."""
    butler_dir = roster_dir / name
    butler_dir.mkdir(parents=True, exist_ok=True)
    (butler_dir / "butler.toml").write_text(_VALID_BUTLER_TOML.format(name=name, port=port))
    return butler_dir


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_db_with_switchboard(registered_at: datetime | None = None) -> DatabaseManager:
    """Build a mock DatabaseManager whose switchboard pool returns a registry row."""
    sw_pool = AsyncMock()
    if registered_at is not None:
        row = {"registered_at": registered_at}
        sw_pool.fetchrow = AsyncMock(return_value=row)
    else:
        sw_pool.fetchrow = AsyncMock(return_value=None)

    db = MagicMock(spec=DatabaseManager)
    db.fan_out = AsyncMock(return_value={})
    db.pool = MagicMock(return_value=sw_pool)
    return db


def _mock_db_no_switchboard() -> DatabaseManager:
    """Build a mock DatabaseManager where pool("switchboard") raises KeyError."""
    db = MagicMock(spec=DatabaseManager)
    db.fan_out = AsyncMock(return_value={})
    db.pool = MagicMock(side_effect=KeyError("switchboard not available"))
    return db


async def _get_detail(app, name: str) -> dict:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/butlers/{name}")
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestProcessFactsCard:
    """process_facts is present on the butler detail endpoint and has the right shape."""

    async def test_process_facts_present_with_all_four_fields(self, app, roster_dir):
        """Butler detail response includes process_facts with all required fields."""
        _make_butler_dir(roster_dir, "general", 41101)
        configs = [ButlerConnectionInfo("general", 41101)]
        db = _mock_db_no_switchboard()
        app.dependency_overrides[get_butler_configs] = lambda: configs
        app.dependency_overrides[get_mcp_manager] = lambda: make_mock_mcp_manager(online=True)
        app.dependency_overrides[_get_roster_dir] = lambda: roster_dir
        app.dependency_overrides[_get_db_manager] = lambda: db

        data = await _get_detail(app, "general")
        assert "process_facts" in data
        pf = data["process_facts"]
        assert "port" in pf
        assert "config_path" in pf
        assert "container_name" in pf
        assert "registered_duration_seconds" in pf

    async def test_pid_not_in_process_facts(self, app, roster_dir):
        """process_facts must not contain a pid field."""
        _make_butler_dir(roster_dir, "general", 41101)
        configs = [ButlerConnectionInfo("general", 41101)]
        db = _mock_db_no_switchboard()
        app.dependency_overrides[get_butler_configs] = lambda: configs
        app.dependency_overrides[get_mcp_manager] = lambda: make_mock_mcp_manager(online=True)
        app.dependency_overrides[_get_roster_dir] = lambda: roster_dir
        app.dependency_overrides[_get_db_manager] = lambda: db

        data = await _get_detail(app, "general")
        pf = data["process_facts"]
        assert "pid" not in pf, f"pid must not appear in process_facts, got: {pf}"

    async def test_config_path_is_roster_relative(self, app, roster_dir):
        """config_path is the roster-relative path to butler.toml."""
        _make_butler_dir(roster_dir, "general", 41101)
        configs = [ButlerConnectionInfo("general", 41101)]
        db = _mock_db_no_switchboard()
        app.dependency_overrides[get_butler_configs] = lambda: configs
        app.dependency_overrides[get_mcp_manager] = lambda: make_mock_mcp_manager(online=True)
        app.dependency_overrides[_get_roster_dir] = lambda: roster_dir
        app.dependency_overrides[_get_db_manager] = lambda: db

        data = await _get_detail(app, "general")
        pf = data["process_facts"]
        assert pf["config_path"] == "roster/general/butler.toml"

    async def test_port_matches_butler_config(self, app, roster_dir):
        """port in process_facts matches the butler's configured port."""
        _make_butler_dir(roster_dir, "general", 41101)
        configs = [ButlerConnectionInfo("general", 41101)]
        db = _mock_db_no_switchboard()
        app.dependency_overrides[get_butler_configs] = lambda: configs
        app.dependency_overrides[get_mcp_manager] = lambda: make_mock_mcp_manager(online=True)
        app.dependency_overrides[_get_roster_dir] = lambda: roster_dir
        app.dependency_overrides[_get_db_manager] = lambda: db

        data = await _get_detail(app, "general")
        pf = data["process_facts"]
        assert pf["port"] == 41101

    async def test_container_name_from_butlers_host_env(self, app, roster_dir, monkeypatch):
        """container_name is derived from BUTLERS_HOST env var."""
        monkeypatch.setenv("BUTLERS_HOST", "butlers-up")
        _make_butler_dir(roster_dir, "general", 41101)
        configs = [ButlerConnectionInfo("general", 41101)]
        db = _mock_db_no_switchboard()
        app.dependency_overrides[get_butler_configs] = lambda: configs
        app.dependency_overrides[get_mcp_manager] = lambda: make_mock_mcp_manager(online=True)
        app.dependency_overrides[_get_roster_dir] = lambda: roster_dir
        app.dependency_overrides[_get_db_manager] = lambda: db

        data = await _get_detail(app, "general")
        pf = data["process_facts"]
        assert pf["container_name"] == "butlers-up"

    async def test_container_name_null_when_localhost(self, app, roster_dir, monkeypatch):
        """container_name is null when BUTLERS_HOST is unset or resolves to localhost."""
        monkeypatch.delenv("BUTLERS_HOST", raising=False)
        _make_butler_dir(roster_dir, "general", 41101)
        configs = [ButlerConnectionInfo("general", 41101)]
        db = _mock_db_no_switchboard()
        app.dependency_overrides[get_butler_configs] = lambda: configs
        app.dependency_overrides[get_mcp_manager] = lambda: make_mock_mcp_manager(online=True)
        app.dependency_overrides[_get_roster_dir] = lambda: roster_dir
        app.dependency_overrides[_get_db_manager] = lambda: db

        data = await _get_detail(app, "general")
        pf = data["process_facts"]
        assert pf["container_name"] is None

    async def test_registered_duration_from_switchboard(self, app, roster_dir):
        """registered_duration_seconds is positive when switchboard registry row is present."""
        registered_at = datetime.now(UTC) - timedelta(seconds=3600)
        _make_butler_dir(roster_dir, "general", 41101)
        configs = [ButlerConnectionInfo("general", 41101)]
        db = _mock_db_with_switchboard(registered_at=registered_at)
        app.dependency_overrides[get_butler_configs] = lambda: configs
        app.dependency_overrides[get_mcp_manager] = lambda: make_mock_mcp_manager(online=True)
        app.dependency_overrides[_get_roster_dir] = lambda: roster_dir
        app.dependency_overrides[_get_db_manager] = lambda: db

        data = await _get_detail(app, "general")
        pf = data["process_facts"]
        duration = pf["registered_duration_seconds"]
        assert duration is not None
        assert duration > 0
        # Should be approximately 3600s (allow 5s slack)
        assert abs(duration - 3600) < 5

    async def test_registered_duration_null_when_switchboard_unavailable(self, app, roster_dir):
        """registered_duration_seconds is null when switchboard pool is absent."""
        _make_butler_dir(roster_dir, "general", 41101)
        configs = [ButlerConnectionInfo("general", 41101)]
        db = _mock_db_no_switchboard()
        app.dependency_overrides[get_butler_configs] = lambda: configs
        app.dependency_overrides[get_mcp_manager] = lambda: make_mock_mcp_manager(online=True)
        app.dependency_overrides[_get_roster_dir] = lambda: roster_dir
        app.dependency_overrides[_get_db_manager] = lambda: db

        data = await _get_detail(app, "general")
        pf = data["process_facts"]
        assert pf["registered_duration_seconds"] is None
