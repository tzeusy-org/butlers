"""Tests for GET /api/butlers/{name}/config — butler config file endpoint."""

from __future__ import annotations

import httpx
import pytest

from butlers.api.deps import ButlerConnectionInfo

from .conftest import make_butler_dir, make_test_app

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGetButlerConfig:
    async def test_returns_404_for_unknown_butler(self, roster_dir):
        """Unknown butler name returns 404."""
        configs = [ButlerConnectionInfo("general", 40101)]
        app = make_test_app(roster_dir, configs)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/nonexistent/config")

        assert response.status_code == 404

    async def test_returns_config_for_known_butler(self, roster_dir):
        """Known butler returns parsed butler.toml and markdown files."""
        make_butler_dir(
            roster_dir,
            "general",
            40101,
            claude_md="# Claude instructions\n",
            manifesto_md="# Manifesto\nPurpose statement.\n",
        )
        configs = [ButlerConnectionInfo("general", 40101)]
        app = make_test_app(roster_dir, configs)

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

    async def test_includes_markdown_file_contents(self, roster_dir):
        """All three markdown files are returned when present."""
        make_butler_dir(
            roster_dir,
            "general",
            40101,
            claude_md="# Claude\n",
            agents_md="# Agents notes\n",
            manifesto_md="# Manifesto\n",
        )
        configs = [ButlerConnectionInfo("general", 40101)]
        app = make_test_app(roster_dir, configs)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general/config")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["claude_md"] == "# Claude\n"
        assert data["agents_md"] == "# Agents notes\n"
        assert data["manifesto_md"] == "# Manifesto\n"

    async def test_handles_missing_markdown_files_gracefully(self, roster_dir):
        """Missing markdown files are returned as None."""
        make_butler_dir(roster_dir, "general", 40101)
        configs = [ButlerConnectionInfo("general", 40101)]
        app = make_test_app(roster_dir, configs)

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

    async def test_response_wrapped_in_api_response(self, roster_dir):
        """Response follows the standard ApiResponse envelope."""
        make_butler_dir(roster_dir, "general", 40101)
        configs = [ButlerConnectionInfo("general", 40101)]
        app = make_test_app(roster_dir, configs)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general/config")

        assert response.status_code == 200
        body = response.json()
        assert "data" in body
        assert "meta" in body

    async def test_404_when_butler_toml_missing(self, roster_dir):
        """Returns 404 when butler is in configs but butler.toml does not exist on disk."""
        # Butler is registered in configs but no directory/toml on disk
        configs = [ButlerConnectionInfo("phantom", 8999)]
        app = make_test_app(roster_dir, configs)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/phantom/config")

        assert response.status_code == 404

    async def test_partial_markdown_files(self, roster_dir):
        """Only some markdown files present — missing ones are None."""
        make_butler_dir(
            roster_dir,
            "general",
            40101,
            claude_md="# Instructions\n",
            manifesto_md="# Purpose\n",
        )
        configs = [ButlerConnectionInfo("general", 40101)]
        app = make_test_app(roster_dir, configs)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general/config")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["claude_md"] == "# Instructions\n"
        assert data["agents_md"] is None
        assert data["manifesto_md"] == "# Purpose\n"

    async def test_butler_toml_with_modules(self, roster_dir):
        """butler.toml with modules section is parsed correctly."""
        make_butler_dir(
            roster_dir,
            "switchboard",
            40100,
            description="Router",
            modules={
                "telegram": 'mode = "polling"\n[modules.telegram.user]\nenabled = false\n'
                '[modules.telegram.bot]\ntoken_env = "BUTLER_TELEGRAM_TOKEN"',
                "email": "[modules.email.user]\nenabled = false\n"
                '[modules.email.bot]\naddress_env = "BUTLER_EMAIL_ADDRESS"\n'
                'password_env = "BUTLER_EMAIL_PASSWORD"',
            },
        )
        configs = [ButlerConnectionInfo("switchboard", 40100)]
        app = make_test_app(roster_dir, configs)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/switchboard/config")

        assert response.status_code == 200
        data = response.json()["data"]
        assert "telegram" in data["butler_toml"]["modules"]
        assert data["butler_toml"]["modules"]["telegram"]["mode"] == "polling"
        assert "email" in data["butler_toml"]["modules"]
