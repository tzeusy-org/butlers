"""Tests for GET /api/butlers/{name} — single butler detail endpoint."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.deps import ButlerConnectionInfo, MCPClientManager
from butlers.api.routers.butlers import _discover_skills, _get_live_status

from .conftest import make_butler_dir, make_mock_mcp_manager, make_test_app

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# _discover_skills unit tests
# ---------------------------------------------------------------------------


class TestDiscoverSkills:
    def test_empty_skills_dir(self, tmp_path: Path):
        butler_dir = tmp_path / "test"
        butler_dir.mkdir()
        (butler_dir / "skills").mkdir()
        assert _discover_skills(butler_dir) == []

    def test_no_skills_dir(self, tmp_path: Path):
        butler_dir = tmp_path / "test"
        butler_dir.mkdir()
        assert _discover_skills(butler_dir) == []

    def test_discovers_skills_with_skill_md(self, tmp_path: Path):
        butler_dir = tmp_path / "test"
        butler_dir.mkdir()
        make_butler_dir(tmp_path, "test", 0, skills=["alpha-skill", "beta-skill"])
        result = _discover_skills(butler_dir)
        assert result == ["alpha-skill", "beta-skill"]

    def test_ignores_dirs_without_skill_md(self, tmp_path: Path):
        butler_dir = tmp_path / "test"
        butler_dir.mkdir()
        skills_dir = butler_dir / "skills"
        skills_dir.mkdir()
        # Directory without SKILL.md
        (skills_dir / "incomplete").mkdir()
        # Directory with SKILL.md
        valid = skills_dir / "valid"
        valid.mkdir()
        (valid / "SKILL.md").write_text("# Valid\n")

        result = _discover_skills(butler_dir)
        assert result == ["valid"]

    def test_sorted_alphabetically(self, tmp_path: Path):
        butler_dir = tmp_path / "test"
        butler_dir.mkdir()
        make_butler_dir(tmp_path, "test", 0, skills=["zebra", "alpha", "middle"])
        result = _discover_skills(butler_dir)
        assert result == ["alpha", "middle", "zebra"]


# ---------------------------------------------------------------------------
# _get_live_status unit tests
# ---------------------------------------------------------------------------


class TestGetLiveStatus:
    async def test_returns_online_when_reachable(self):
        mgr = make_mock_mcp_manager(online=True)
        status = await _get_live_status("test", mgr)
        assert status == "online"

    async def test_returns_offline_when_unreachable(self):
        mgr = make_mock_mcp_manager(online=False)
        status = await _get_live_status("test", mgr)
        assert status == "offline"

    async def test_returns_offline_on_unexpected_error(self):
        mgr = MagicMock(spec=MCPClientManager)
        mgr.get_client = AsyncMock(side_effect=RuntimeError("something broke"))
        status = await _get_live_status("test", mgr)
        assert status == "offline"


# ---------------------------------------------------------------------------
# GET /api/butlers/{name} integration tests (ASGI transport)
# ---------------------------------------------------------------------------


class TestGetButlerDetail:
    async def test_returns_404_for_unknown_butler(self, roster_dir):
        """Unknown butler name returns 404."""
        configs = [ButlerConnectionInfo("general", 40101)]
        mgr = make_mock_mcp_manager(online=True)
        app = make_test_app(roster_dir, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/nonexistent")

        assert response.status_code == 404

    async def test_returns_detail_for_known_butler(self, roster_dir):
        """Known butler returns full detail."""
        make_butler_dir(roster_dir, "general", 40101)
        configs = [ButlerConnectionInfo("general", 40101, description="Test butler")]
        mgr = make_mock_mcp_manager(online=True)
        app = make_test_app(roster_dir, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["name"] == "general"
        assert data["port"] == 40101
        assert data["description"] == "Test butler"
        assert data["db_name"] == "butler_general"
        assert data["status"] == "online"

    async def test_includes_modules(self, roster_dir):
        """Butler detail includes module information."""
        make_butler_dir(
            roster_dir,
            "switchboard",
            40100,
            description="Router butler",
            modules={"telegram": 'mode = "polling"', "email": ""},
        )
        configs = [ButlerConnectionInfo("switchboard", 40100)]
        mgr = make_mock_mcp_manager(online=True)
        app = make_test_app(roster_dir, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/switchboard")

        assert response.status_code == 200
        data = response.json()["data"]
        module_names = [m["name"] for m in data["modules"]]
        assert "telegram" in module_names
        assert "email" in module_names

    async def test_includes_skills_list(self, roster_dir):
        """Butler detail includes skills discovered from skills/ directory."""
        make_butler_dir(roster_dir, "general", 40101, skills=["data-organizer", "report-builder"])
        configs = [ButlerConnectionInfo("general", 40101)]
        mgr = make_mock_mcp_manager(online=True)
        app = make_test_app(roster_dir, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["skills"] == ["data-organizer", "report-builder"]

    async def test_includes_schedule(self, roster_dir):
        """Butler detail includes schedule entries."""
        make_butler_dir(
            roster_dir,
            "health",
            40103,
            description="Health butler",
            extra_toml=(
                "[[butler.schedule]]\n"
                'name = "daily-check"\n'
                'cron = "0 8 * * *"\n'
                'prompt = "Run daily health check"\n'
            ),
        )
        configs = [ButlerConnectionInfo("health", 40103)]
        mgr = make_mock_mcp_manager(online=True)
        app = make_test_app(roster_dir, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/health")

        assert response.status_code == 200
        data = response.json()["data"]
        assert len(data["schedules"]) == 1
        assert data["schedules"][0]["name"] == "daily-check"
        assert data["schedules"][0]["cron"] == "0 8 * * *"

    async def test_includes_job_mode_schedule_without_prompt(self, roster_dir):
        """Butler detail allows deterministic schedules without prompt text."""
        make_butler_dir(
            roster_dir,
            "relationship",
            40104,
            description="Relationship butler",
            extra_toml=(
                "[[butler.schedule]]\n"
                'name = "eligibility-sweep"\n'
                'cron = "*/5 * * * *"\n'
                'dispatch_mode = "job"\n'
                'job_name = "eligibility_sweep"\n'
            ),
        )
        configs = [ButlerConnectionInfo("relationship", 40104)]
        mgr = make_mock_mcp_manager(online=True)
        app = make_test_app(roster_dir, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/relationship")

        assert response.status_code == 200
        data = response.json()["data"]
        assert len(data["schedules"]) == 1
        assert data["schedules"][0]["name"] == "eligibility-sweep"
        assert data["schedules"][0]["prompt"] is None

    async def test_handles_unreachable_butler(self, roster_dir):
        """Butler detail returns 'offline' status when MCP is unreachable."""
        make_butler_dir(roster_dir, "general", 40101)
        configs = [ButlerConnectionInfo("general", 40101)]
        mgr = make_mock_mcp_manager(online=False)
        app = make_test_app(roster_dir, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "offline"
        assert data["name"] == "general"

    async def test_response_wrapped_in_api_response(self, roster_dir):
        """Response follows the standard ApiResponse envelope."""
        make_butler_dir(roster_dir, "general", 40101)
        configs = [ButlerConnectionInfo("general", 40101)]
        mgr = make_mock_mcp_manager(online=True)
        app = make_test_app(roster_dir, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general")

        assert response.status_code == 200
        body = response.json()
        assert "data" in body
        assert "meta" in body

    async def test_404_when_config_not_found(self, roster_dir):
        """Returns 404 when butler is in configs but has no butler.toml."""
        # Butler is registered in configs but directory/toml doesn't exist
        configs = [ButlerConnectionInfo("phantom", 8999)]
        mgr = make_mock_mcp_manager(online=True)
        app = make_test_app(roster_dir, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/phantom")

        assert response.status_code == 404

    async def test_empty_skills_when_no_skills_dir(self, roster_dir):
        """Returns empty skills list when butler has no skills/ directory."""
        make_butler_dir(roster_dir, "general", 40101)
        # Don't create skills directory
        configs = [ButlerConnectionInfo("general", 40101)]
        mgr = make_mock_mcp_manager(online=True)
        app = make_test_app(roster_dir, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["skills"] == []

    async def test_empty_modules_when_no_modules(self, roster_dir):
        """Returns empty modules list when butler has no modules configured."""
        make_butler_dir(roster_dir, "general", 40101)
        configs = [ButlerConnectionInfo("general", 40101)]
        mgr = make_mock_mcp_manager(online=True)
        app = make_test_app(roster_dir, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["modules"] == []
