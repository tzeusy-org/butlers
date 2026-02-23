"""Tests for butler discovery and status endpoints — integration-style.

Comprehensive tests exercising the full discovery flow including:
- List endpoint with mixed butler statuses (ok, unreachable, timeout)
- Graceful handling of unexpected errors (no 500s)
- Detail endpoint with real config files on disk
- Detail endpoint online/offline status via MCP probing
- Config endpoint with present/missing markdown files
- Skills endpoint with multiple skills
- Modules endpoint with mixed health
- End-to-end discovery from a roster directory
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.deps import (
    ButlerConnectionInfo,
    ButlerUnreachableError,
    MCPClientManager,
    discover_butlers,
    get_butler_configs,
    get_mcp_manager,
)

from .conftest import make_butler_dir, make_test_app

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers local to this file (discovery-specific, not shared)
# ---------------------------------------------------------------------------


def _make_mock_client(*, ping_ok: bool = True) -> MagicMock:
    """Create a mock MCP client with configurable ping behavior."""
    client = MagicMock()
    client.is_connected = MagicMock(return_value=True)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    if ping_ok:
        client.ping = AsyncMock(return_value=True)
    else:
        client.ping = AsyncMock(side_effect=ConnectionError("ping failed"))
    client.list_tools = AsyncMock(return_value=[])
    return client


def _make_status_result(
    modules: list[str] | dict[str, dict[str, str]],
    health: str = "ok",
) -> MagicMock:
    """Create a mock CallToolResult from the status() MCP tool."""
    modules_payload: list[str] | dict[str, dict[str, str]]
    if isinstance(modules, list):
        modules_payload = {name: {"status": "active"} for name in modules}
    else:
        modules_payload = modules

    data = {
        "name": "test",
        "description": "Test butler",
        "port": 40101,
        "modules": modules_payload,
        "health": health,
        "uptime_seconds": 123.4,
    }
    content_block = MagicMock()
    content_block.text = json.dumps(data)
    result = MagicMock()
    result.content = [content_block]
    result.is_error = False
    return result


# ---------------------------------------------------------------------------
# 1. List endpoint with multiple butlers (ok, unreachable, timeout)
# ---------------------------------------------------------------------------


class TestListMultipleButlerStatuses:
    """Verify list endpoint correctly aggregates mixed statuses."""

    async def test_mixed_statuses_ok_unreachable_timeout(self):
        """Three butlers: A ok, B unreachable, C timeout — all appear in response."""
        configs = [
            ButlerConnectionInfo(name="alpha", port=40100, description="Butler A"),
            ButlerConnectionInfo(name="beta", port=40101, description="Butler B"),
            ButlerConnectionInfo(name="gamma", port=40102, description="Butler C"),
        ]

        mock_client = _make_mock_client(ping_ok=True)
        mgr = MagicMock(spec=MCPClientManager)

        async def _get_client_side_effect(name: str):
            if name == "alpha":
                return mock_client
            if name == "beta":
                raise ButlerUnreachableError(name, cause=ConnectionRefusedError())
            if name == "gamma":
                raise TimeoutError("connection timed out")
            raise RuntimeError(f"unexpected butler: {name}")

        mgr.get_client = _get_client_side_effect

        app = create_app()
        app.dependency_overrides[get_mcp_manager] = lambda: mgr
        app.dependency_overrides[get_butler_configs] = lambda: configs

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers")

        assert response.status_code == 200
        body = response.json()
        data = body["data"]
        assert len(data) == 3

        by_name = {b["name"]: b for b in data}
        assert by_name["alpha"]["status"] == "ok"
        assert by_name["alpha"]["port"] == 40100
        assert by_name["alpha"]["description"] == "Butler A"
        assert by_name["beta"]["status"] == "down"
        assert by_name["gamma"]["status"] == "down"

    async def test_all_butlers_reachable(self):
        """When all butlers respond, all show status='ok'."""
        configs = [
            ButlerConnectionInfo(name="one", port=40100),
            ButlerConnectionInfo(name="two", port=40101),
            ButlerConnectionInfo(name="three", port=40102),
        ]
        mock_client = _make_mock_client(ping_ok=True)
        mgr = MagicMock(spec=MCPClientManager)
        mgr.get_client = AsyncMock(return_value=mock_client)

        app = create_app()
        app.dependency_overrides[get_mcp_manager] = lambda: mgr
        app.dependency_overrides[get_butler_configs] = lambda: configs

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers")

        assert response.status_code == 200
        data = response.json()["data"]
        assert len(data) == 3
        assert all(b["status"] == "ok" for b in data)


# ---------------------------------------------------------------------------
# 2. List endpoint handles unexpected errors gracefully (no 500)
# ---------------------------------------------------------------------------


class TestListUnexpectedErrors:
    """Verify RuntimeError during probing yields status='down', not HTTP 500."""

    async def test_runtime_error_yields_down_not_500(self):
        """RuntimeError from MCPClientManager results in status='down'."""
        configs = [
            ButlerConnectionInfo(name="borked", port=40200, description="Broken butler"),
        ]

        mgr = MagicMock(spec=MCPClientManager)
        mgr.get_client = AsyncMock(side_effect=RuntimeError("something unexpected"))

        app = create_app()
        app.dependency_overrides[get_mcp_manager] = lambda: mgr
        app.dependency_overrides[get_butler_configs] = lambda: configs

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers")

        assert response.status_code == 200
        data = response.json()["data"]
        assert len(data) == 1
        assert data[0]["name"] == "borked"
        assert data[0]["status"] == "down"
        assert data[0]["port"] == 40200

    async def test_os_error_yields_down_not_500(self):
        """OSError from MCPClientManager results in status='down'."""
        configs = [
            ButlerConnectionInfo(name="disk-fail", port=40201),
        ]

        mgr = MagicMock(spec=MCPClientManager)
        mgr.get_client = AsyncMock(side_effect=OSError("disk I/O error"))

        app = create_app()
        app.dependency_overrides[get_mcp_manager] = lambda: mgr
        app.dependency_overrides[get_butler_configs] = lambda: configs

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data[0]["status"] == "down"


# ---------------------------------------------------------------------------
# 3. Detail endpoint with real config on disk
# ---------------------------------------------------------------------------


class TestDetailWithRealConfig:
    """Verify detail endpoint reads butler.toml from disk and populates response."""

    async def test_basic_detail_from_disk(self, tmp_path: Path):
        """Detail response includes name, port, description, db_name from disk config."""
        make_butler_dir(
            tmp_path,
            "general",
            40101,
            description="Catch-all assistant",
            skills=["classify"],
        )
        configs = [ButlerConnectionInfo("general", 40101, description="Catch-all assistant")]
        mock_client = _make_mock_client(ping_ok=True)
        mgr = MagicMock(spec=MCPClientManager)
        mgr.get_client = AsyncMock(return_value=mock_client)

        app = make_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["name"] == "general"
        assert data["port"] == 40101
        assert data["description"] == "Catch-all assistant"
        assert data["db_name"] == "butler_general"
        assert "classify" in data["skills"]

    async def test_detail_includes_modules_from_config(self, tmp_path: Path):
        """Detail response includes modules from butler.toml."""
        make_butler_dir(
            tmp_path,
            "comm",
            40102,
            description="Communications butler",
            modules={"telegram": 'mode = "polling"', "email": ""},
        )
        configs = [ButlerConnectionInfo("comm", 40102)]
        mock_client = _make_mock_client(ping_ok=True)
        mgr = MagicMock(spec=MCPClientManager)
        mgr.get_client = AsyncMock(return_value=mock_client)

        app = make_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/comm")

        assert response.status_code == 200
        data = response.json()["data"]
        module_names = [m["name"] for m in data["modules"]]
        assert "telegram" in module_names
        assert "email" in module_names

    async def test_detail_includes_schedules_from_config(self, tmp_path: Path):
        """Detail response includes schedule entries from butler.toml."""
        make_butler_dir(
            tmp_path,
            "health",
            40103,
            description="Health check butler",
            with_schedule=True,
        )
        configs = [ButlerConnectionInfo("health", 40103)]
        mock_client = _make_mock_client(ping_ok=True)
        mgr = MagicMock(spec=MCPClientManager)
        mgr.get_client = AsyncMock(return_value=mock_client)

        app = make_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/health")

        assert response.status_code == 200
        data = response.json()["data"]
        assert len(data["schedules"]) == 2
        schedule_names = [s["name"] for s in data["schedules"]]
        assert "morning-check" in schedule_names
        assert "nightly-cleanup" in schedule_names

    async def test_detail_with_multiple_skills(self, tmp_path: Path):
        """Detail response lists all skills from skills/ directory."""
        make_butler_dir(
            tmp_path,
            "general",
            40101,
            description="General butler",
            skills=["data-organizer", "report-builder", "classify"],
        )
        configs = [ButlerConnectionInfo("general", 40101)]
        mock_client = _make_mock_client(ping_ok=True)
        mgr = MagicMock(spec=MCPClientManager)
        mgr.get_client = AsyncMock(return_value=mock_client)

        app = make_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general")

        assert response.status_code == 200
        data = response.json()["data"]
        assert sorted(data["skills"]) == ["classify", "data-organizer", "report-builder"]


# ---------------------------------------------------------------------------
# 4. Detail endpoint when butler is online
# ---------------------------------------------------------------------------


class TestDetailOnline:
    """Verify detail endpoint reports 'online' when MCP ping succeeds."""

    async def test_status_online_when_reachable(self, tmp_path: Path):
        """Butler is online when MCP client connects and pings successfully."""
        make_butler_dir(tmp_path, "general", 40101)
        configs = [ButlerConnectionInfo("general", 40101)]
        mock_client = _make_mock_client(ping_ok=True)
        mgr = MagicMock(spec=MCPClientManager)
        mgr.get_client = AsyncMock(return_value=mock_client)

        app = make_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "online"
        # Verify ping was actually called
        mock_client.ping.assert_called_once()


# ---------------------------------------------------------------------------
# 5. Detail endpoint when butler is offline
# ---------------------------------------------------------------------------


class TestDetailOffline:
    """Verify detail endpoint reports 'offline' when MCP is unreachable."""

    async def test_status_offline_when_unreachable(self, tmp_path: Path):
        """Butler is offline when MCP client raises ButlerUnreachableError."""
        make_butler_dir(tmp_path, "general", 40101)
        configs = [ButlerConnectionInfo("general", 40101)]
        mgr = MagicMock(spec=MCPClientManager)
        mgr.get_client = AsyncMock(
            side_effect=ButlerUnreachableError("general", cause=ConnectionRefusedError())
        )

        app = make_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "offline"
        assert data["name"] == "general"

    async def test_status_offline_when_unexpected_error(self, tmp_path: Path):
        """Butler is offline when MCP client raises an unexpected exception."""
        make_butler_dir(tmp_path, "general", 40101)
        configs = [ButlerConnectionInfo("general", 40101)]
        mgr = MagicMock(spec=MCPClientManager)
        mgr.get_client = AsyncMock(side_effect=RuntimeError("something broke"))

        app = make_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "offline"

    async def test_detail_still_returns_config_when_offline(self, tmp_path: Path):
        """Config data (modules, skills, schedules) is still returned when offline."""
        make_butler_dir(
            tmp_path,
            "general",
            40101,
            modules={"telegram": ""},
            skills=["classify"],
        )
        configs = [ButlerConnectionInfo("general", 40101)]
        mgr = MagicMock(spec=MCPClientManager)
        mgr.get_client = AsyncMock(
            side_effect=ButlerUnreachableError("general", cause=ConnectionRefusedError())
        )

        app = make_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "offline"
        # Config data still present
        assert data["name"] == "general"
        module_names = [m["name"] for m in data["modules"]]
        assert "telegram" in module_names
        assert "classify" in data["skills"]


# ---------------------------------------------------------------------------
# 6. Config endpoint reads markdown files
# ---------------------------------------------------------------------------


class TestConfigEndpointMarkdownFiles:
    """Verify config endpoint reads butler.toml and markdown files."""

    async def test_reads_all_markdown_files(self, tmp_path: Path):
        """All three markdown files are returned when present."""
        make_butler_dir(
            tmp_path,
            "general",
            40101,
            claude_md="# Claude Instructions\nBe helpful.\n",
            manifesto_md="# Manifesto\nServe the user.\n",
            agents_md="# Agent Notes\nRemember this.\n",
        )
        configs = [ButlerConnectionInfo("general", 40101)]
        mgr = MagicMock(spec=MCPClientManager)

        app = make_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general/config")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["claude_md"] == "# Claude Instructions\nBe helpful.\n"
        assert data["manifesto_md"] == "# Manifesto\nServe the user.\n"
        assert data["agents_md"] == "# Agent Notes\nRemember this.\n"

    async def test_butler_toml_parsed_as_dict(self, tmp_path: Path):
        """butler.toml is returned as a parsed dict, not raw text."""
        make_butler_dir(tmp_path, "general", 40101, description="A fine butler")
        configs = [ButlerConnectionInfo("general", 40101)]
        mgr = MagicMock(spec=MCPClientManager)

        app = make_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general/config")

        assert response.status_code == 200
        data = response.json()["data"]
        assert isinstance(data["butler_toml"], dict)
        assert data["butler_toml"]["butler"]["name"] == "general"
        assert data["butler_toml"]["butler"]["port"] == 40101
        assert data["butler_toml"]["butler"]["description"] == "A fine butler"


# ---------------------------------------------------------------------------
# 7. Config endpoint with missing markdown files
# ---------------------------------------------------------------------------


class TestConfigEndpointMissingMarkdown:
    """Verify missing markdown files are returned as null."""

    async def test_all_markdown_files_missing(self, tmp_path: Path):
        """When no markdown files exist, all fields are null."""
        make_butler_dir(tmp_path, "general", 40101)
        configs = [ButlerConnectionInfo("general", 40101)]
        mgr = MagicMock(spec=MCPClientManager)

        app = make_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general/config")

        assert response.status_code == 200
        data = response.json()["data"]
        # butler.toml still present and valid
        assert data["butler_toml"]["butler"]["name"] == "general"
        # All markdown fields are null
        assert data["claude_md"] is None
        assert data["agents_md"] is None
        assert data["manifesto_md"] is None

    async def test_partial_markdown_files(self, tmp_path: Path):
        """Only some markdown files present — missing ones are null."""
        make_butler_dir(
            tmp_path,
            "general",
            40101,
            claude_md="# Instructions\n",
        )
        configs = [ButlerConnectionInfo("general", 40101)]
        mgr = MagicMock(spec=MCPClientManager)

        app = make_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general/config")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["claude_md"] == "# Instructions\n"
        assert data["manifesto_md"] is None
        assert data["agents_md"] is None


# ---------------------------------------------------------------------------
# 8. Skills endpoint with multiple skills
# ---------------------------------------------------------------------------


class TestSkillsEndpointMultiple:
    """Verify skills endpoint returns all skills with correct content."""

    async def test_returns_multiple_skills_with_content(self, tmp_path: Path):
        """Multiple skills are returned sorted with SKILL.md content."""
        make_butler_dir(
            tmp_path,
            "general",
            40101,
            skills=["data-organizer", "report-builder"],
        )
        configs = [ButlerConnectionInfo("general", 40101)]
        mgr = MagicMock(spec=MCPClientManager)

        app = make_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general/skills")

        assert response.status_code == 200
        data = response.json()["data"]
        assert len(data) == 2
        assert data[0]["name"] == "data-organizer"
        assert "data-organizer" in data[0]["content"]
        assert data[1]["name"] == "report-builder"
        assert "report-builder" in data[1]["content"]

    async def test_skills_are_sorted_alphabetically(self, tmp_path: Path):
        """Skills are returned in alphabetical order."""
        make_butler_dir(
            tmp_path,
            "general",
            40101,
            skills=["zebra-skill", "alpha-skill", "middle-skill"],
        )
        configs = [ButlerConnectionInfo("general", 40101)]
        mgr = MagicMock(spec=MCPClientManager)

        app = make_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general/skills")

        assert response.status_code == 200
        data = response.json()["data"]
        names = [s["name"] for s in data]
        assert names == ["alpha-skill", "middle-skill", "zebra-skill"]


# ---------------------------------------------------------------------------
# 9. Modules endpoint with mixed health
# ---------------------------------------------------------------------------


class TestModulesEndpointMixedHealth:
    """Verify modules endpoint reports mixed health statuses correctly."""

    async def test_mixed_module_health(self, tmp_path: Path):
        """Modules with mixed health: connected, error (not loaded)."""
        make_butler_dir(
            tmp_path,
            "general",
            40101,
            modules={"telegram": "", "email": "", "calendar": ""},
        )
        configs = [ButlerConnectionInfo("general", 40101)]
        # Only telegram and email are loaded by the butler
        status_result = _make_status_result(["telegram", "email"], health="ok")
        mock_client = MagicMock()
        mock_client.ping = AsyncMock(return_value=True)
        mock_client.call_tool = AsyncMock(return_value=status_result)
        mgr = MagicMock(spec=MCPClientManager)
        mgr.get_client = AsyncMock(return_value=mock_client)

        app = make_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general/modules")

        assert response.status_code == 200
        modules = response.json()["data"]
        by_name = {m["name"]: m for m in modules}

        assert by_name["telegram"]["status"] == "connected"
        assert by_name["telegram"]["error"] is None
        assert by_name["email"]["status"] == "connected"
        assert by_name["email"]["error"] is None
        assert by_name["calendar"]["status"] == "error"
        assert by_name["calendar"]["error"] is not None

    async def test_degraded_health_propagates_to_modules(self, tmp_path: Path):
        """When butler health is 'degraded', loaded modules show 'degraded'."""
        make_butler_dir(
            tmp_path,
            "general",
            40101,
            modules={"telegram": "", "email": ""},
        )
        configs = [ButlerConnectionInfo("general", 40101)]
        status_result = _make_status_result(["telegram", "email"], health="degraded")
        mock_client = MagicMock()
        mock_client.ping = AsyncMock(return_value=True)
        mock_client.call_tool = AsyncMock(return_value=status_result)
        mgr = MagicMock(spec=MCPClientManager)
        mgr.get_client = AsyncMock(return_value=mock_client)

        app = make_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general/modules")

        assert response.status_code == 200
        modules = response.json()["data"]
        for m in modules:
            assert m["status"] == "degraded"

    async def test_modules_unknown_when_butler_unreachable(self, tmp_path: Path):
        """All modules show 'unknown' when butler MCP server is unreachable."""
        make_butler_dir(
            tmp_path,
            "general",
            40101,
            modules={"telegram": "", "email": ""},
        )
        configs = [ButlerConnectionInfo("general", 40101)]
        mgr = MagicMock(spec=MCPClientManager)
        mgr.get_client = AsyncMock(
            side_effect=ButlerUnreachableError("general", cause=ConnectionRefusedError())
        )

        app = make_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general/modules")

        assert response.status_code == 200
        modules = response.json()["data"]
        assert len(modules) == 2
        for m in modules:
            assert m["status"] == "unknown"
            assert m["enabled"] is True


# ---------------------------------------------------------------------------
# 10. Full discovery flow — discover_butlers with a real roster directory
# ---------------------------------------------------------------------------


class TestFullDiscoveryFlow:
    """End-to-end: discover_butlers from a roster dir on disk."""

    def test_discovers_all_butlers_from_roster(self, tmp_path: Path):
        """discover_butlers finds all valid butler configs in a roster dir."""
        make_butler_dir(tmp_path, "switchboard", 40100, description="Routes messages")
        make_butler_dir(tmp_path, "general", 40101, description="Catch-all assistant")
        make_butler_dir(tmp_path, "health", 40103, description="Health monitor")

        results = discover_butlers(tmp_path)

        assert len(results) == 3
        names = {r.name for r in results}
        assert names == {"switchboard", "general", "health"}

    def test_discovery_returns_sorted_by_name(self, tmp_path: Path):
        """Discovered butlers are sorted alphabetically by name."""
        make_butler_dir(tmp_path, "zebra", 40103)
        make_butler_dir(tmp_path, "alpha", 40100)
        make_butler_dir(tmp_path, "middle", 40101)

        results = discover_butlers(tmp_path)

        names = [r.name for r in results]
        assert names == ["alpha", "middle", "zebra"]

    def test_discovery_skips_dirs_without_butler_toml(self, tmp_path: Path):
        """Directories without butler.toml are silently skipped."""
        make_butler_dir(tmp_path, "valid", 40100)
        # Create a directory without butler.toml
        (tmp_path / "not-a-butler").mkdir()

        results = discover_butlers(tmp_path)

        assert len(results) == 1
        assert results[0].name == "valid"

    def test_discovery_skips_files_in_roster_dir(self, tmp_path: Path):
        """Non-directory entries in the roster are ignored."""
        make_butler_dir(tmp_path, "valid", 40100)
        # Create a regular file in roster dir
        (tmp_path / "README.md").write_text("# Roster\n")

        results = discover_butlers(tmp_path)

        assert len(results) == 1
        assert results[0].name == "valid"

    def test_discovery_skips_invalid_toml(self, tmp_path: Path):
        """Butler dirs with invalid TOML are skipped gracefully."""
        make_butler_dir(tmp_path, "valid", 40100)
        # Create a butler dir with invalid TOML
        invalid_dir = tmp_path / "broken"
        invalid_dir.mkdir()
        (invalid_dir / "butler.toml").write_text("this is not valid [toml")

        results = discover_butlers(tmp_path)

        assert len(results) == 1
        assert results[0].name == "valid"

    def test_discovery_empty_roster(self, tmp_path: Path):
        """Empty roster directory returns empty list."""
        results = discover_butlers(tmp_path)
        assert results == []

    def test_discovery_nonexistent_roster_dir(self, tmp_path: Path):
        """Non-existent roster directory returns empty list."""
        results = discover_butlers(tmp_path / "nonexistent")
        assert results == []

    def test_discovery_captures_port_and_description(self, tmp_path: Path):
        """Discovered butlers have correct port and description."""
        make_butler_dir(tmp_path, "mybutler", 9500, description="My custom butler")

        results = discover_butlers(tmp_path)

        assert len(results) == 1
        assert results[0].name == "mybutler"
        assert results[0].port == 9500
        assert results[0].description == "My custom butler"

    def test_discovery_with_real_roster(self):
        """discover_butlers works with the actual repository roster directory."""
        roster_dir = Path(__file__).resolve().parents[2] / "roster"
        if not roster_dir.is_dir():
            pytest.skip("Roster directory not found in repo")

        results = discover_butlers(roster_dir)

        # At minimum, the repo has some butlers defined
        assert len(results) > 0
        names = {r.name for r in results}
        # These butlers should exist in the repo roster
        assert "switchboard" in names or "general" in names


# ---------------------------------------------------------------------------
# Cross-cutting: response envelope shape
# ---------------------------------------------------------------------------


class TestResponseEnvelope:
    """Verify all endpoints return standard ApiResponse envelope."""

    async def test_list_envelope(self):
        """GET /api/butlers returns {data: [...], meta: {...}}."""
        configs = [ButlerConnectionInfo("test", 40100)]
        mock_client = _make_mock_client()
        mgr = MagicMock(spec=MCPClientManager)
        mgr.get_client = AsyncMock(return_value=mock_client)

        app = create_app()
        app.dependency_overrides[get_mcp_manager] = lambda: mgr
        app.dependency_overrides[get_butler_configs] = lambda: configs

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers")

        body = response.json()
        assert "data" in body
        assert "meta" in body
        assert isinstance(body["data"], list)

    async def test_detail_envelope(self, tmp_path: Path):
        """GET /api/butlers/{name} returns {data: {...}, meta: {...}}."""
        make_butler_dir(tmp_path, "general", 40101)
        configs = [ButlerConnectionInfo("general", 40101)]
        mock_client = _make_mock_client()
        mgr = MagicMock(spec=MCPClientManager)
        mgr.get_client = AsyncMock(return_value=mock_client)

        app = make_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general")

        body = response.json()
        assert "data" in body
        assert "meta" in body
        assert isinstance(body["data"], dict)

    async def test_config_envelope(self, tmp_path: Path):
        """GET /api/butlers/{name}/config returns {data: {...}, meta: {...}}."""
        make_butler_dir(tmp_path, "general", 40101)
        configs = [ButlerConnectionInfo("general", 40101)]
        mgr = MagicMock(spec=MCPClientManager)

        app = make_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general/config")

        body = response.json()
        assert "data" in body
        assert "meta" in body

    async def test_skills_envelope(self, tmp_path: Path):
        """GET /api/butlers/{name}/skills returns {data: [...], meta: {...}}."""
        butler_dir = tmp_path / "general"
        butler_dir.mkdir()

        configs = [ButlerConnectionInfo("general", 40101)]
        mgr = MagicMock(spec=MCPClientManager)

        app = make_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general/skills")

        body = response.json()
        assert "data" in body
        assert "meta" in body
        assert isinstance(body["data"], list)

    async def test_modules_envelope(self, tmp_path: Path):
        """GET /api/butlers/{name}/modules returns {data: [...], meta: {...}}."""
        make_butler_dir(tmp_path, "general", 40101, modules={"telegram": ""})
        configs = [ButlerConnectionInfo("general", 40101)]
        mgr = MagicMock(spec=MCPClientManager)
        mgr.get_client = AsyncMock(
            side_effect=ButlerUnreachableError("general", cause=ConnectionRefusedError())
        )

        app = make_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general/modules")

        body = response.json()
        assert "data" in body
        assert "meta" in body
        assert isinstance(body["data"], list)
