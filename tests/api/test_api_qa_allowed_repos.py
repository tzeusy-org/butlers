"""Tests for QA allowed-repos CRUD endpoints.

Covers:
- GET  /api/qa/settings/allowed-repos  — list (empty, populated)
- POST /api/qa/settings/allowed-repos  — add (success, duplicate 409, invalid 422)
- PATCH /api/qa/settings/allowed-repos/{owner}/{repo} — toggle (success, not found 404)
- DELETE /api/qa/settings/allowed-repos/{owner}/{repo} — remove (success, not found 404)
- URL formats accepted by POST (HTTPS, SSH, bare owner/repo)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.qa import _get_db_manager

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 4, 6, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class _MockRecord(dict):
    """A dict subclass that mimics asyncpg Record access patterns."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


def _mock_record(row: dict[str, Any]) -> _MockRecord:
    return _MockRecord(row)


def _make_repo_row(
    *,
    owner: str = "acme",
    repo: str = "my-repo",
    enabled: bool = True,
    repo_id: uuid.UUID | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> dict[str, Any]:
    return {
        "id": repo_id or uuid.uuid4(),
        "owner": owner,
        "repo": repo,
        "enabled": enabled,
        "created_at": created_at or _NOW,
        "updated_at": updated_at or _NOW,
    }


def _build_app(
    *,
    fetch_rows: list[dict[str, Any]] | None = None,
    fetchrow_result: dict[str, Any] | None = None,
    execute_result: str = "DELETE 1",
    fetchrow_side_effect: Any = None,
) -> tuple[Any, MagicMock]:
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=[_mock_record(r) for r in (fetch_rows or [])])

    if fetchrow_side_effect is not None:
        mock_pool.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    else:
        mock_pool.fetchrow = AsyncMock(
            return_value=_mock_record(fetchrow_result) if fetchrow_result else None
        )

    mock_pool.execute = AsyncMock(return_value=execute_result)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = mock_pool

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return app, mock_pool


# ---------------------------------------------------------------------------
# GET /api/qa/settings/allowed-repos
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_allowed_repos_empty():
    """Returns an empty list when no repos are configured."""
    app, mock_pool = _build_app(fetch_rows=[])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/qa/settings/allowed-repos")

    assert response.status_code == 200
    body = response.json()
    assert body["data"] == []


@pytest.mark.asyncio
async def test_list_allowed_repos_populated():
    """Returns all repos ordered by owner/repo."""
    rows = [
        _make_repo_row(owner="acme", repo="alpha"),
        _make_repo_row(owner="acme", repo="beta", enabled=False),
    ]
    app, mock_pool = _build_app(fetch_rows=rows)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/qa/settings/allowed-repos")

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 2
    assert data[0]["owner"] == "acme"
    assert data[0]["repo"] == "alpha"
    assert data[0]["enabled"] is True
    assert data[1]["enabled"] is False


# ---------------------------------------------------------------------------
# POST /api/qa/settings/allowed-repos
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "owner_repo,norm_owner,norm_repo",
    [
        ("acme/my-repo", "acme", "my-repo"),  # bare owner/repo
        ("https://github.com/ACME/My-Repo.git", "acme", "my-repo"),  # HTTPS URL, lowercased
        ("git@github.com:org/proj.git", "org", "proj"),  # SSH URL
    ],
)
@pytest.mark.asyncio
async def test_create_allowed_repo_normalizes_formats(owner_repo, norm_owner, norm_repo):
    """POST accepts bare/HTTPS/SSH forms and normalises to lowercase owner/repo."""
    repo_row = _make_repo_row(owner=norm_owner, repo=norm_repo)
    app, mock_pool = _build_app(fetchrow_result=repo_row)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/qa/settings/allowed-repos",
            json={"owner_repo": owner_repo, "enabled": True},
        )

    assert response.status_code == 201
    data = response.json()["data"]
    assert data["owner"] == norm_owner
    assert data["repo"] == norm_repo
    # Normalised values are what hit the DB.
    call_args = mock_pool.fetchrow.call_args
    assert norm_owner in call_args[0]
    assert norm_repo in call_args[0]


@pytest.mark.asyncio
async def test_create_allowed_repo_invalid_format():
    """Returns 422 for an unparseable owner_repo string."""
    app, _ = _build_app()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/qa/settings/allowed-repos",
            json={"owner_repo": "not-a-valid-repo-path"},
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_allowed_repo_duplicate_409():
    """Returns 409 when the repo is already in the whitelist."""
    import asyncpg

    app, mock_pool = _build_app(
        fetchrow_side_effect=asyncpg.UniqueViolationError("duplicate key value")
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/qa/settings/allowed-repos",
            json={"owner_repo": "acme/my-repo"},
        )

    assert response.status_code == 409


# ---------------------------------------------------------------------------
# PATCH /api/qa/settings/allowed-repos/{owner}/{repo}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_allowed_repo_disable():
    """Disables a repository entry."""
    updated_row = _make_repo_row(owner="acme", repo="my-repo", enabled=False)
    app, mock_pool = _build_app(fetchrow_result=updated_row)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.patch(
            "/api/qa/settings/allowed-repos/acme/my-repo",
            json={"enabled": False},
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["enabled"] is False


@pytest.mark.asyncio
async def test_patch_allowed_repo_not_found():
    """Returns 404 when the owner/repo is not in the whitelist."""
    app, _ = _build_app(fetchrow_result=None)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.patch(
            "/api/qa/settings/allowed-repos/unknown/repo",
            json={"enabled": True},
        )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/qa/settings/allowed-repos/{owner}/{repo}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_allowed_repo_success():
    """Deletes a repository from the whitelist."""
    app, mock_pool = _build_app(execute_result="DELETE 1")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.delete("/api/qa/settings/allowed-repos/acme/my-repo")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["deleted"] is True
    assert data["owner"] == "acme"
    assert data["repo"] == "my-repo"


@pytest.mark.asyncio
async def test_delete_allowed_repo_not_found():
    """Returns 404 when no rows were deleted."""
    app, mock_pool = _build_app(execute_result="DELETE 0")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.delete("/api/qa/settings/allowed-repos/unknown/repo")

    assert response.status_code == 404
