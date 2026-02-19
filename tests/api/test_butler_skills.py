"""Tests for GET /api/butlers/{name}/skills — list butler skills endpoint."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.deps import (
    ButlerConnectionInfo,
    ButlerUnreachableError,
    MCPClientManager,
    get_butler_configs,
    get_mcp_manager,
)
from butlers.api.routers.butlers import _get_roster_dir, _read_skills

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
    app = create_app()
    app.dependency_overrides[get_butler_configs] = lambda: configs
    app.dependency_overrides[get_mcp_manager] = lambda: mcp_manager
    app.dependency_overrides[_get_roster_dir] = lambda: tmp_path
    return app


def _make_skills(butler_dir: Path, skills: dict[str, str]) -> None:
    """Create skill directories with SKILL.md content under butler_dir/skills/.

    Parameters
    ----------
    butler_dir:
        The butler directory path.
    skills:
        Mapping of skill name to SKILL.md content.
    """
    skills_dir = butler_dir / "skills"
    skills_dir.mkdir(exist_ok=True)
    for skill_name, content in skills.items():
        skill_dir = skills_dir / skill_name
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# _read_skills unit tests
# ---------------------------------------------------------------------------


class TestReadSkills:
    def test_no_skills_dir(self, tmp_path: Path):
        """Returns empty list when butler has no skills/ directory."""
        butler_dir = tmp_path / "test"
        butler_dir.mkdir()
        assert _read_skills(butler_dir) == []

    def test_empty_skills_dir(self, tmp_path: Path):
        """Returns empty list when skills/ directory is empty."""
        butler_dir = tmp_path / "test"
        butler_dir.mkdir()
        (butler_dir / "skills").mkdir()
        assert _read_skills(butler_dir) == []

    def test_reads_skill_name_and_content(self, tmp_path: Path):
        """Returns skill name and SKILL.md content."""
        butler_dir = tmp_path / "test"
        butler_dir.mkdir()
        _make_skills(butler_dir, {"my-skill": "# My Skill\n\nDoes something useful."})

        result = _read_skills(butler_dir)
        assert len(result) == 1
        assert result[0].name == "my-skill"
        assert result[0].content == "# My Skill\n\nDoes something useful."

    def test_returns_multiple_skills_sorted(self, tmp_path: Path):
        """Returns skills sorted alphabetically."""
        butler_dir = tmp_path / "test"
        butler_dir.mkdir()
        _make_skills(
            butler_dir,
            {
                "zebra": "# Zebra\n",
                "alpha": "# Alpha\n",
                "middle": "# Middle\n",
            },
        )

        result = _read_skills(butler_dir)
        assert [s.name for s in result] == ["alpha", "middle", "zebra"]

    def test_ignores_dirs_without_skill_md(self, tmp_path: Path):
        """Skips directories that don't contain a SKILL.md file."""
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

        result = _read_skills(butler_dir)
        assert len(result) == 1
        assert result[0].name == "valid"


# ---------------------------------------------------------------------------
# GET /api/butlers/{name}/skills integration tests (ASGI transport)
# ---------------------------------------------------------------------------


class TestListButlerSkills:
    async def test_returns_404_for_unknown_butler(self, tmp_path: Path):
        """Unknown butler name returns 404."""
        configs = [ButlerConnectionInfo("general", 40101)]
        mgr = _mock_mcp_manager(online=True)
        app = _create_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/nonexistent/skills")

        assert response.status_code == 404

    async def test_returns_skills_for_butler(self, tmp_path: Path):
        """Returns skill list with name and content for a known butler."""
        butler_dir = tmp_path / "general"
        butler_dir.mkdir()
        _make_skills(
            butler_dir,
            {
                "data-organizer": "# Data Organizer\n\nOrganize your data.",
                "report-builder": "# Report Builder\n\nBuild reports.",
            },
        )

        configs = [ButlerConnectionInfo("general", 40101)]
        mgr = _mock_mcp_manager(online=True)
        app = _create_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general/skills")

        assert response.status_code == 200
        data = response.json()["data"]
        assert len(data) == 2
        assert data[0]["name"] == "data-organizer"
        assert data[0]["content"] == "# Data Organizer\n\nOrganize your data."
        assert data[1]["name"] == "report-builder"
        assert data[1]["content"] == "# Report Builder\n\nBuild reports."

    async def test_returns_empty_list_when_no_skills_dir(self, tmp_path: Path):
        """Returns empty list when butler has no skills/ directory."""
        butler_dir = tmp_path / "general"
        butler_dir.mkdir()

        configs = [ButlerConnectionInfo("general", 40101)]
        mgr = _mock_mcp_manager(online=True)
        app = _create_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general/skills")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data == []

    async def test_skill_content_read_correctly(self, tmp_path: Path):
        """Skill SKILL.md content is returned exactly as written."""
        content = "---\nname: test-skill\n---\n\n# Test Skill\n\nMulti-line content.\n"
        butler_dir = tmp_path / "general"
        butler_dir.mkdir()
        _make_skills(butler_dir, {"test-skill": content})

        configs = [ButlerConnectionInfo("general", 40101)]
        mgr = _mock_mcp_manager(online=True)
        app = _create_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general/skills")

        assert response.status_code == 200
        data = response.json()["data"]
        assert len(data) == 1
        assert data[0]["name"] == "test-skill"
        assert data[0]["content"] == content

    async def test_response_wrapped_in_api_response(self, tmp_path: Path):
        """Response follows the standard ApiResponse envelope."""
        butler_dir = tmp_path / "general"
        butler_dir.mkdir()

        configs = [ButlerConnectionInfo("general", 40101)]
        mgr = _mock_mcp_manager(online=True)
        app = _create_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general/skills")

        assert response.status_code == 200
        body = response.json()
        assert "data" in body
        assert "meta" in body
