"""Tests for GET /api/butlers/{name}/config — butler config file endpoint."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.deps import ButlerConnectionInfo, MCPClientManager

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_roster(
    tmp_path: Path,
    name: str = "general",
    port: int = 40101,
    *,
    claude_md: str | None = "# Claude instructions\n",
    agents_md: str | None = None,
    manifesto_md: str | None = "# Manifesto\nPurpose statement.\n",
) -> Path:
    """Create a minimal butler directory with butler.toml and optional markdown files."""
    butler_dir = tmp_path / name
    butler_dir.mkdir(parents=True, exist_ok=True)
    (butler_dir / "butler.toml").write_text(
        f'[butler]\nname = "{name}"\nport = {port}\n'
        f'description = "Test butler"\n'
        f'[butler.db]\nname = "butler_{name}"\n'
        f"[runtime]\n"
        f'type = "claude-code"\n'
    )
    if claude_md is not None:
        (butler_dir / "CLAUDE.md").write_text(claude_md)
    if agents_md is not None:
        (butler_dir / "AGENTS.md").write_text(agents_md)
    if manifesto_md is not None:
        (butler_dir / "MANIFESTO.md").write_text(manifesto_md)
    return butler_dir


def _create_test_app(
    tmp_path: Path,
    configs: list[ButlerConnectionInfo],
):
    """Create a FastAPI test app with dependency overrides for config endpoint."""
    from butlers.api.deps import get_butler_configs, get_mcp_manager
    from butlers.api.routers.butlers import _get_roster_dir

    mgr = MagicMock(spec=MCPClientManager)
    app = create_app()
    app.dependency_overrides[get_butler_configs] = lambda: configs
    app.dependency_overrides[get_mcp_manager] = lambda: mgr
    app.dependency_overrides[_get_roster_dir] = lambda: tmp_path
    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGetButlerConfig:
    async def test_returns_404_for_unknown_butler(self, tmp_path: Path):
        """Unknown butler name returns 404."""
        configs = [ButlerConnectionInfo("general", 40101)]
        app = _create_test_app(tmp_path, configs)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/nonexistent/config")

        assert response.status_code == 404

    async def test_returns_config_for_known_butler(self, tmp_path: Path):
        """Known butler returns parsed butler.toml and markdown files."""
        _make_roster(tmp_path, "general", 40101)
        configs = [ButlerConnectionInfo("general", 40101)]
        app = _create_test_app(tmp_path, configs)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general/config")

        assert response.status_code == 200
        data = response.json()["data"]

        # butler.toml parsed as dict
        assert data["butler_toml"]["butler"]["name"] == "general"
        assert data["butler_toml"]["butler"]["port"] == 40101

        # Markdown files present
        assert data["claude_md"] == "# Claude instructions\n"
        assert data["manifesto_md"] == "# Manifesto\nPurpose statement.\n"

    async def test_includes_markdown_file_contents(self, tmp_path: Path):
        """All three markdown files are returned when present."""
        _make_roster(
            tmp_path,
            "general",
            40101,
            claude_md="# Claude\n",
            agents_md="# Agents notes\n",
            manifesto_md="# Manifesto\n",
        )
        configs = [ButlerConnectionInfo("general", 40101)]
        app = _create_test_app(tmp_path, configs)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general/config")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["claude_md"] == "# Claude\n"
        assert data["agents_md"] == "# Agents notes\n"
        assert data["manifesto_md"] == "# Manifesto\n"

    async def test_handles_missing_markdown_files_gracefully(self, tmp_path: Path):
        """Missing markdown files are returned as None."""
        _make_roster(
            tmp_path,
            "general",
            40101,
            claude_md=None,
            agents_md=None,
            manifesto_md=None,
        )
        configs = [ButlerConnectionInfo("general", 40101)]
        app = _create_test_app(tmp_path, configs)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general/config")

        assert response.status_code == 200
        data = response.json()["data"]

        # butler.toml still parsed
        assert data["butler_toml"]["butler"]["name"] == "general"

        # All markdown fields are null
        assert data["claude_md"] is None
        assert data["agents_md"] is None
        assert data["manifesto_md"] is None

    async def test_response_wrapped_in_api_response(self, tmp_path: Path):
        """Response follows the standard ApiResponse envelope."""
        _make_roster(tmp_path, "general", 40101)
        configs = [ButlerConnectionInfo("general", 40101)]
        app = _create_test_app(tmp_path, configs)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general/config")

        assert response.status_code == 200
        body = response.json()
        assert "data" in body
        assert "meta" in body

    async def test_404_when_butler_toml_missing(self, tmp_path: Path):
        """Returns 404 when butler is in configs but butler.toml does not exist on disk."""
        # Butler is registered in configs but no directory/toml on disk
        configs = [ButlerConnectionInfo("phantom", 8999)]
        app = _create_test_app(tmp_path, configs)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/phantom/config")

        assert response.status_code == 404

    async def test_partial_markdown_files(self, tmp_path: Path):
        """Only some markdown files present — missing ones are None."""
        _make_roster(
            tmp_path,
            "general",
            40101,
            claude_md="# Instructions\n",
            agents_md=None,
            manifesto_md="# Purpose\n",
        )
        configs = [ButlerConnectionInfo("general", 40101)]
        app = _create_test_app(tmp_path, configs)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general/config")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["claude_md"] == "# Instructions\n"
        assert data["agents_md"] is None
        assert data["manifesto_md"] == "# Purpose\n"

    async def test_butler_toml_with_modules(self, tmp_path: Path):
        """butler.toml with modules section is parsed correctly."""
        butler_dir = tmp_path / "switchboard"
        butler_dir.mkdir(parents=True, exist_ok=True)
        (butler_dir / "butler.toml").write_text(
            '[butler]\nname = "switchboard"\nport = 40100\n'
            'description = "Router"\n'
            '[butler.db]\nname = "butler_switchboard"\n'
            "[runtime]\n"
            'type = "claude-code"\n'
            '[modules.telegram]\nmode = "polling"\n'
            "[modules.telegram.user]\nenabled = false\n"
            '[modules.telegram.bot]\ntoken_env = "BUTLER_TELEGRAM_TOKEN"\n'
            "[modules.email]\n"
            "[modules.email.user]\nenabled = false\n"
            '[modules.email.bot]\naddress_env = "BUTLER_EMAIL_ADDRESS"\n'
            'password_env = "BUTLER_EMAIL_PASSWORD"\n'
        )
        configs = [ButlerConnectionInfo("switchboard", 40100)]
        app = _create_test_app(tmp_path, configs)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/switchboard/config")

        assert response.status_code == 200
        data = response.json()["data"]
        assert "telegram" in data["butler_toml"]["modules"]
        assert data["butler_toml"]["modules"]["telegram"]["mode"] == "polling"
        assert "email" in data["butler_toml"]["modules"]
