"""Condensed tests for butler management API endpoints.

Condensed from:
  test_butler_config.py (8) + test_butler_detail.py (21) + test_butler_discovery.py (37)
  + test_butler_list.py (15) + test_butler_mcp.py (7) + test_butler_modules.py (14)
  + test_butler_router.py (14) + test_butler_skills.py (10) + test_butler_tick.py (6)
  + test_butler_trigger.py (8) → ~15 tests (bu-egmz6) → 4 tests (bu-2yw2d)

Keeps: list structure, 404/503 CRUD paths (parametrized), trigger success, config 200.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.deps import (
    ButlerConnectionInfo,
    ButlerUnreachableError,
    MCPClientManager,
    get_butler_configs,
    get_mcp_manager,
)
from butlers.api.models.butler import ModuleStatus
from butlers.api.routers.butlers import _get_db_manager, _get_module_health_via_mcp

from .conftest import make_butler_dir, make_mock_mcp_manager, make_test_app

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_fan_out_rows(sessions_by_butler: dict[str, int]) -> dict:
    """Build a fan_out return value with per-butler session count rows."""
    result = {}
    for name, count in sessions_by_butler.items():
        row = MagicMock()
        row.__getitem__ = MagicMock(side_effect=lambda i, c=count: c)
        result[name] = [row]
    return result


def _mock_audit_db(sessions_24h: dict[str, int] | None = None):
    pool = AsyncMock()
    pool.execute = AsyncMock()
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool
    # fan_out is used by list_butlers to aggregate per-butler 24h session counts
    fan_out_return = _mock_fan_out_rows(sessions_24h) if sessions_24h else {}
    db.fan_out = AsyncMock(return_value=fan_out_return)
    return db


def _mock_tool_result(data: dict) -> MagicMock:
    block = MagicMock()
    block.text = json.dumps(data)
    result = MagicMock()
    result.content = [block]
    result.is_error = False
    return result


def _wire(app, configs, mcp_manager=None, db=None):
    if mcp_manager is None:
        mcp_manager = make_mock_mcp_manager(online=True)
    if db is None:
        db = _mock_audit_db()
    app.dependency_overrides[get_butler_configs] = lambda: configs
    app.dependency_overrides[get_mcp_manager] = lambda: mcp_manager
    app.dependency_overrides[_get_db_manager] = lambda: db
    return app


def _mcp_unreachable():
    mgr = MagicMock(spec=MCPClientManager)
    mgr.get_client = AsyncMock(
        side_effect=ButlerUnreachableError("general", cause=ConnectionRefusedError("unreachable"))
    )
    return mgr


# ---------------------------------------------------------------------------
# Butler list — all butlers returned, unreachable never 500
# ---------------------------------------------------------------------------


class TestButlerList:
    async def test_list_returns_all_butlers_and_unreachable_never_500(self, app, roster_dir):
        make_butler_dir(roster_dir, "general", 41101)
        configs = [ButlerConnectionInfo("general", 41101)]
        # Online case
        _wire(app, configs, make_mock_mcp_manager(online=True))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp_ok = await client.get("/api/butlers")
        assert resp_ok.status_code == 200
        assert resp_ok.json()["data"][0]["name"] == "general"

        # Offline — still 200, never 500
        _wire(app, configs, make_mock_mcp_manager(online=False))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp_down = await client.get("/api/butlers")
        assert resp_down.status_code == 200

    async def test_list_surfaces_sessions_24h_per_butler(self, app, roster_dir):
        """sessions_24h is aggregated from DB fan_out and returned in each butler summary."""
        make_butler_dir(roster_dir, "general", 41101)
        configs = [ButlerConnectionInfo("general", 41101)]
        db = _mock_audit_db(sessions_24h={"general": 7})
        _wire(app, configs, make_mock_mcp_manager(online=True), db=db)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers")
        assert resp.status_code == 200
        butler_data = resp.json()["data"][0]
        assert butler_data["name"] == "general"
        assert butler_data["sessions_24h"] == 7

    async def test_list_sessions_24h_defaults_to_zero_when_no_db_data(self, app, roster_dir):
        """Butlers with no sessions in the DB fan_out get sessions_24h == 0."""
        make_butler_dir(roster_dir, "general", 41101)
        configs = [ButlerConnectionInfo("general", 41101)]
        db = _mock_audit_db(sessions_24h={})  # fan_out returns nothing for "general"
        _wire(app, configs, make_mock_mcp_manager(online=True), db=db)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers")
        assert resp.status_code == 200
        assert resp.json()["data"][0]["sessions_24h"] == 0


# ---------------------------------------------------------------------------
# Butler config — 404 unknown, 200 known
# ---------------------------------------------------------------------------


class TestButlerConfig:
    async def test_config_404_unknown_and_200_known(self, roster_dir):
        make_butler_dir(roster_dir, "general", 41101, claude_md="Be helpful.")
        configs = [ButlerConnectionInfo("general", 41101)]
        app = make_test_app(roster_dir, configs)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            r404 = await client.get("/api/butlers/nonexistent/config")
            r200 = await client.get("/api/butlers/general/config")
        assert r404.status_code == 404
        assert r200.status_code == 200
        assert "data" in r200.json()


# ---------------------------------------------------------------------------
# Butler trigger — 404 unknown, 503 unreachable, 200 success
# ---------------------------------------------------------------------------


class TestButlerTrigger:
    @pytest.mark.parametrize(
        "butler_name,expected",
        [("nonexistent", 404), ("general", 503)],
        ids=["404-unknown", "503-unreachable"],
    )
    async def test_trigger_error_paths(self, app, roster_dir, butler_name, expected):
        configs = [ButlerConnectionInfo("general", 41101)]
        _wire(app, configs, _mcp_unreachable())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                f"/api/butlers/{butler_name}/trigger", json={"prompt": "hello"}
            )
        assert resp.status_code == expected

    async def test_trigger_success_returns_session_data(self, app, roster_dir):
        configs = [ButlerConnectionInfo("general", 41101)]
        trigger_data = {"session_id": "sess-1", "success": True, "output": "Done"}
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.call_tool = AsyncMock(return_value=_mock_tool_result(trigger_data))
        mgr = MagicMock(spec=MCPClientManager)
        mgr.get_client = AsyncMock(return_value=mock_client)
        _wire(app, configs, mgr)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/butlers/general/trigger", json={"prompt": "do something"}
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["session_id"] == "sess-1"


# ---------------------------------------------------------------------------
# ModuleStatus OAuth/credential fields — forward-compatible defaults
# ---------------------------------------------------------------------------


def _make_mcp_client_with_status(status_payload: dict) -> MagicMock:
    """Return a mock MCP client whose status() call returns the given payload."""
    block = MagicMock()
    block.text = json.dumps(status_payload)
    result = MagicMock()
    result.content = [block]
    result.is_error = False

    client = MagicMock()
    client.call_tool = AsyncMock(return_value=result)

    mgr = MagicMock(spec=MCPClientManager)
    mgr.get_client = AsyncMock(return_value=client)
    return mgr


class TestModuleStatusOAuthFields:
    """OAuth/credential fields default to None when butler hasn't emitted them."""

    def test_module_status_new_fields_default_none(self):
        """All three new fields must be absent (None) by default."""
        ms = ModuleStatus(name="gmail", enabled=True, status="connected")
        assert ms.oauth_status is None
        assert ms.oauth_expires_at is None
        assert ms.credential_health is None

    async def test_get_module_health_returns_none_fields_when_butler_omits_them(self):
        """Active module without OAuth fields in status() → all three fields None."""
        payload = {"health": "ok", "modules": {"gmail": {"status": "active"}}}
        mgr = _make_mcp_client_with_status(payload)

        results = await _get_module_health_via_mcp("general", mgr, ["gmail"])

        assert len(results) == 1
        ms = results[0]
        assert ms.name == "gmail"
        assert ms.status == "connected"
        assert ms.oauth_status is None
        assert ms.oauth_expires_at is None
        assert ms.credential_health is None

    async def test_get_module_health_populates_oauth_status_when_present(self):
        """oauth_status is forwarded from the MCP status payload."""
        payload = {
            "health": "ok",
            "modules": {
                "gmail": {
                    "status": "active",
                    "oauth_status": "granted",
                    "credential_health": "ok",
                }
            },
        }
        mgr = _make_mcp_client_with_status(payload)

        results = await _get_module_health_via_mcp("general", mgr, ["gmail"])

        ms = results[0]
        assert ms.oauth_status == "granted"
        assert ms.credential_health == "ok"
        assert ms.oauth_expires_at is None  # not present in this payload

    async def test_get_module_health_populates_oauth_expires_at_when_present(self):
        """oauth_expires_at ISO string is parsed to a datetime object."""
        payload = {
            "health": "ok",
            "modules": {
                "gcal": {
                    "status": "active",
                    "oauth_status": "granted",
                    "oauth_expires_at": "2026-06-01T12:00:00+00:00",
                    "credential_health": "ok",
                }
            },
        }
        mgr = _make_mcp_client_with_status(payload)

        results = await _get_module_health_via_mcp("general", mgr, ["gcal"])

        ms = results[0]
        assert ms.oauth_status == "granted"
        assert ms.oauth_expires_at is not None
        assert ms.oauth_expires_at.year == 2026
        assert ms.oauth_expires_at.month == 6

    async def test_get_module_health_oauth_fields_present_on_error_status(self):
        """OAuth fields are surfaced even when daemon_status != active."""
        payload = {
            "health": "ok",
            "modules": {
                "gmail": {
                    "status": "failed",
                    "error": "token expired",
                    "oauth_status": "reauth_needed",
                    "credential_health": "error",
                }
            },
        }
        mgr = _make_mcp_client_with_status(payload)

        results = await _get_module_health_via_mcp("general", mgr, ["gmail"])

        ms = results[0]
        assert ms.status == "error"
        assert ms.oauth_status == "reauth_needed"
        assert ms.credential_health == "error"

    async def test_get_module_health_all_modules_none_when_unreachable(self):
        """Unreachable butler returns ModuleStatus with status=unknown and None OAuth fields."""
        mgr = MagicMock(spec=MCPClientManager)
        mgr.get_client = AsyncMock(
            side_effect=ButlerUnreachableError("general", cause=ConnectionRefusedError("refused"))
        )

        results = await _get_module_health_via_mcp("general", mgr, ["gmail", "gcal"])

        assert len(results) == 2
        for ms in results:
            assert ms.status == "unknown"
            assert ms.oauth_status is None
            assert ms.oauth_expires_at is None
            assert ms.credential_health is None

    @pytest.mark.parametrize(
        "bad_field,fields,expect",
        [
            # Unparseable oauth_expires_at is dropped; other fields survive.
            (
                "oauth_expires_at",
                {
                    "oauth_status": "granted",
                    "oauth_expires_at": "not-a-date",
                    "credential_health": "ok",
                },
                {"oauth_expires_at": None, "oauth_status": "granted", "credential_health": "ok"},
            ),
            # Unknown oauth_status enum coerced to None.
            (
                "oauth_status",
                {"oauth_status": "pending", "credential_health": "ok"},
                {"oauth_status": None, "credential_health": "ok"},
            ),
            # Unknown credential_health enum coerced to None.
            (
                "credential_health",
                {"oauth_status": "granted", "credential_health": "degraded"},
                {"oauth_status": "granted", "credential_health": None},
            ),
        ],
    )
    async def test_get_module_health_invalid_fields_coerced_to_none(
        self, bad_field, fields, expect
    ):
        """Invalid/unknown OAuth field values are defensively coerced to None, never raise."""
        payload = {"health": "ok", "modules": {"gmail": {"status": "active", **fields}}}
        mgr = _make_mcp_client_with_status(payload)

        results = await _get_module_health_via_mcp("general", mgr, ["gmail"])

        ms = results[0]
        for key, value in expect.items():
            assert getattr(ms, key) == value
