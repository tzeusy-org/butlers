"""Tests for GET /api/butlers/{name}/skills — list butler skills endpoint."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from butlers.api.deps import ButlerConnectionInfo
from butlers.api.routers.butlers import _read_skills

from .conftest import make_butler_dir, make_mock_mcp_manager, make_test_app

pytestmark = pytest.mark.unit


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
        make_butler_dir(
            tmp_path,
            "test",
            0,
            skills_with_content={"my-skill": "# My Skill\n\nDoes something useful."},
        )
        butler_dir = tmp_path / "test"
        result = _read_skills(butler_dir)
        assert len(result) == 1
        assert result[0].name == "my-skill"
        assert result[0].content == "# My Skill\n\nDoes something useful."

    def test_returns_multiple_skills_sorted(self, tmp_path: Path):
        """Returns skills sorted alphabetically."""
        make_butler_dir(
            tmp_path,
            "test",
            0,
            skills_with_content={
                "zebra": "# Zebra\n",
                "alpha": "# Alpha\n",
                "middle": "# Middle\n",
            },
        )
        butler_dir = tmp_path / "test"
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
    async def test_returns_404_for_unknown_butler(self, roster_dir):
        """Unknown butler name returns 404."""
        configs = [ButlerConnectionInfo("general", 40101)]
        mgr = make_mock_mcp_manager(online=True)
        app = make_test_app(roster_dir, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/nonexistent/skills")

        assert response.status_code == 404

    async def test_returns_skills_for_butler(self, roster_dir):
        """Returns skill list with name and content for a known butler."""
        make_butler_dir(
            roster_dir,
            "general",
            40101,
            skills_with_content={
                "data-organizer": "# Data Organizer\n\nOrganize your data.",
                "report-builder": "# Report Builder\n\nBuild reports.",
            },
        )
        configs = [ButlerConnectionInfo("general", 40101)]
        mgr = make_mock_mcp_manager(online=True)
        app = make_test_app(roster_dir, configs, mgr)

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

    async def test_returns_empty_list_when_no_skills_dir(self, roster_dir):
        """Returns empty list when butler has no skills/ directory."""
        (roster_dir / "general").mkdir()
        configs = [ButlerConnectionInfo("general", 40101)]
        mgr = make_mock_mcp_manager(online=True)
        app = make_test_app(roster_dir, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general/skills")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data == []

    async def test_skill_content_read_correctly(self, roster_dir):
        """Skill SKILL.md content is returned exactly as written."""
        content = "---\nname: test-skill\n---\n\n# Test Skill\n\nMulti-line content.\n"
        make_butler_dir(
            roster_dir,
            "general",
            40101,
            skills_with_content={"test-skill": content},
        )
        configs = [ButlerConnectionInfo("general", 40101)]
        mgr = make_mock_mcp_manager(online=True)
        app = make_test_app(roster_dir, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general/skills")

        assert response.status_code == 200
        data = response.json()["data"]
        assert len(data) == 1
        assert data[0]["name"] == "test-skill"
        assert data[0]["content"] == content

    async def test_response_wrapped_in_api_response(self, roster_dir):
        """Response follows the standard ApiResponse envelope."""
        (roster_dir / "general").mkdir()
        configs = [ButlerConnectionInfo("general", 40101)]
        mgr = make_mock_mcp_manager(online=True)
        app = make_test_app(roster_dir, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general/skills")

        assert response.status_code == 200
        body = response.json()
        assert "data" in body
        assert "meta" in body
