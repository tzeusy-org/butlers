"""Tests for GET /api/butlers/{name} — single butler detail endpoint."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.deps import ButlerConnectionInfo, ButlerUnreachableError, MCPClientManager
from butlers.api.routers.butlers import _discover_skills, _get_live_status

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_roster(tmp_path: Path, name: str = "general", port: int = 8101) -> Path:
    """Create a minimal butler directory in tmp_path with valid butler.toml."""
    butler_dir = tmp_path / name
    butler_dir.mkdir(parents=True, exist_ok=True)
    (butler_dir / "butler.toml").write_text(
        f'[butler]\nname = "{name}"\nport = {port}\n'
        f'description = "Test butler"\n'
        f'[butler.db]\nname = "butler_{name}"\n'
        f"[runtime]\n"
        f'type = "claude-code"\n'
    )
    return butler_dir


def _make_roster_with_modules(tmp_path: Path, name: str = "switchboard", port: int = 8100) -> Path:
    """Create a butler directory with modules in config."""
    butler_dir = tmp_path / name
    butler_dir.mkdir(parents=True, exist_ok=True)
    (butler_dir / "butler.toml").write_text(
        f'[butler]\nname = "{name}"\nport = {port}\n'
        f'description = "Router butler"\n'
        f'[butler.db]\nname = "butler_{name}"\n'
        f"[runtime]\n"
        f'type = "claude-code"\n'
        f'[modules.telegram]\nmode = "polling"\n'
        f"[modules.telegram.user]\nenabled = false\n"
        f'[modules.telegram.bot]\ntoken_env = "BUTLER_TELEGRAM_TOKEN"\n'
        f"[modules.email]\n"
        f"[modules.email.user]\nenabled = false\n"
        f'[modules.email.bot]\naddress_env = "BUTLER_EMAIL_ADDRESS"\n'
        f'password_env = "BUTLER_EMAIL_PASSWORD"\n'
    )
    return butler_dir


def _make_roster_with_schedule(tmp_path: Path, name: str = "health", port: int = 8103) -> Path:
    """Create a butler directory with schedule entries."""
    butler_dir = tmp_path / name
    butler_dir.mkdir(parents=True, exist_ok=True)
    (butler_dir / "butler.toml").write_text(
        f'[butler]\nname = "{name}"\nport = {port}\n'
        f'description = "Health butler"\n'
        f'[butler.db]\nname = "butler_{name}"\n'
        f"[runtime]\n"
        f'type = "claude-code"\n'
        f"[[butler.schedule]]\n"
        f'name = "daily-check"\n'
        f'cron = "0 8 * * *"\n'
        f'prompt = "Run daily health check"\n'
    )
    return butler_dir


def _make_skills(butler_dir: Path, skill_names: list[str]) -> None:
    """Create skill directories with SKILL.md under butler_dir/skills/."""
    skills_dir = butler_dir / "skills"
    skills_dir.mkdir(exist_ok=True)
    for skill_name in skill_names:
        skill_dir = skills_dir / skill_name
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(f"# {skill_name}\n")


def _mock_mcp_manager(*, online: bool = True) -> MCPClientManager:
    """Create a mock MCPClientManager."""
    mgr = MagicMock(spec=MCPClientManager)
    if online:
        mock_client = MagicMock()
        mock_client.ping = AsyncMock(return_value=True)
        mgr.get_client = AsyncMock(return_value=mock_client)
    else:
        mgr.get_client = AsyncMock(
            side_effect=ButlerUnreachableError("test", cause=ConnectionRefusedError("refused"))
        )
    return mgr


def _create_test_app(
    tmp_path: Path,
    configs: list[ButlerConnectionInfo],
    mcp_manager: MCPClientManager,
):
    """Create a FastAPI test app with dependency overrides."""
    from butlers.api.deps import get_butler_configs, get_mcp_manager
    from butlers.api.routers.butlers import _get_roster_dir

    app = create_app()
    app.dependency_overrides[get_butler_configs] = lambda: configs
    app.dependency_overrides[get_mcp_manager] = lambda: mcp_manager
    app.dependency_overrides[_get_roster_dir] = lambda: tmp_path
    return app


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
        _make_skills(butler_dir, ["alpha-skill", "beta-skill"])
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
        _make_skills(butler_dir, ["zebra", "alpha", "middle"])
        result = _discover_skills(butler_dir)
        assert result == ["alpha", "middle", "zebra"]


# ---------------------------------------------------------------------------
# _get_live_status unit tests
# ---------------------------------------------------------------------------


class TestGetLiveStatus:
    async def test_returns_online_when_reachable(self):
        mgr = _mock_mcp_manager(online=True)
        status = await _get_live_status("test", mgr)
        assert status == "online"

    async def test_returns_offline_when_unreachable(self):
        mgr = _mock_mcp_manager(online=False)
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
    async def test_returns_404_for_unknown_butler(self, tmp_path: Path):
        """Unknown butler name returns 404."""
        configs = [ButlerConnectionInfo("general", 8101)]
        mgr = _mock_mcp_manager(online=True)
        app = _create_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/nonexistent")

        assert response.status_code == 404

    async def test_returns_detail_for_known_butler(self, tmp_path: Path):
        """Known butler returns full detail."""
        _make_roster(tmp_path, "general", 8101)
        configs = [ButlerConnectionInfo("general", 8101, description="Test butler")]
        mgr = _mock_mcp_manager(online=True)
        app = _create_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["name"] == "general"
        assert data["port"] == 8101
        assert data["description"] == "Test butler"
        assert data["db_name"] == "butler_general"
        assert data["status"] == "online"

    async def test_includes_modules(self, tmp_path: Path):
        """Butler detail includes module information."""
        _make_roster_with_modules(tmp_path, "switchboard", 8100)
        configs = [ButlerConnectionInfo("switchboard", 8100)]
        mgr = _mock_mcp_manager(online=True)
        app = _create_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/switchboard")

        assert response.status_code == 200
        data = response.json()["data"]
        module_names = [m["name"] for m in data["modules"]]
        assert "telegram" in module_names
        assert "email" in module_names

    async def test_includes_skills_list(self, tmp_path: Path):
        """Butler detail includes skills discovered from skills/ directory."""
        butler_dir = _make_roster(tmp_path, "general", 8101)
        _make_skills(butler_dir, ["data-organizer", "report-builder"])
        configs = [ButlerConnectionInfo("general", 8101)]
        mgr = _mock_mcp_manager(online=True)
        app = _create_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["skills"] == ["data-organizer", "report-builder"]

    async def test_includes_schedule(self, tmp_path: Path):
        """Butler detail includes schedule entries."""
        _make_roster_with_schedule(tmp_path, "health", 8103)
        configs = [ButlerConnectionInfo("health", 8103)]
        mgr = _mock_mcp_manager(online=True)
        app = _create_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/health")

        assert response.status_code == 200
        data = response.json()["data"]
        assert len(data["schedules"]) == 1
        assert data["schedules"][0]["name"] == "daily-check"
        assert data["schedules"][0]["cron"] == "0 8 * * *"

    async def test_handles_unreachable_butler(self, tmp_path: Path):
        """Butler detail returns 'offline' status when MCP is unreachable."""
        _make_roster(tmp_path, "general", 8101)
        configs = [ButlerConnectionInfo("general", 8101)]
        mgr = _mock_mcp_manager(online=False)
        app = _create_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "offline"
        assert data["name"] == "general"

    async def test_response_wrapped_in_api_response(self, tmp_path: Path):
        """Response follows the standard ApiResponse envelope."""
        _make_roster(tmp_path, "general", 8101)
        configs = [ButlerConnectionInfo("general", 8101)]
        mgr = _mock_mcp_manager(online=True)
        app = _create_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general")

        assert response.status_code == 200
        body = response.json()
        assert "data" in body
        assert "meta" in body

    async def test_404_when_config_not_found(self, tmp_path: Path):
        """Returns 404 when butler is in configs but has no butler.toml."""
        # Butler is registered in configs but directory/toml doesn't exist
        configs = [ButlerConnectionInfo("phantom", 8999)]
        mgr = _mock_mcp_manager(online=True)
        app = _create_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/phantom")

        assert response.status_code == 404

    async def test_empty_skills_when_no_skills_dir(self, tmp_path: Path):
        """Returns empty skills list when butler has no skills/ directory."""
        _make_roster(tmp_path, "general", 8101)
        # Don't create skills directory
        configs = [ButlerConnectionInfo("general", 8101)]
        mgr = _mock_mcp_manager(online=True)
        app = _create_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["skills"] == []

    async def test_empty_modules_when_no_modules(self, tmp_path: Path):
        """Returns empty modules list when butler has no modules configured."""
        _make_roster(tmp_path, "general", 8101)
        configs = [ButlerConnectionInfo("general", 8101)]
        mgr = _mock_mcp_manager(online=True)
        app = _create_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["modules"] == []
