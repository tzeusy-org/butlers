"""Attention reissue maps P0002 to 404 and 23514 to 409 without echoing database text.

REQ-dashboard-model-settings-002. Complements tests/api/test_model_runtime_attention.py,
which covers the 55000 -> 409 and 503 paths.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.deps import get_db
from tests.api.auth_helpers import create_authenticated_domain_app

pytestmark = pytest.mark.unit

SENTINEL = "synthetic-db-message-sentinel"


@pytest.fixture(scope="module")
def app():
    return create_authenticated_domain_app(api_key="owner-key")


def _db(app, error: BaseException) -> AsyncMock:
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(side_effect=error)
    manager = MagicMock(spec=DatabaseManager)
    manager.credential_shared_pool.return_value = pool
    app.dependency_overrides[get_db] = lambda: manager
    return pool


def _postgres_error(sqlstate: str) -> asyncpg.PostgresError:
    error = asyncpg.RaiseError(f"episode lookup failed: {SENTINEL}")
    error.sqlstate = sqlstate
    return error


async def _reissue(app, monkeypatch: pytest.MonkeyPatch) -> httpx.Response:
    monkeypatch.setenv("DASHBOARD_API_KEY", "owner-key")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post(
            f"/api/settings/models/attention/{uuid.uuid4()}/reissue",
            headers={"X-API-Key": "owner-key"},
        )


async def test_missing_episode_reissue_is_404_with_fixed_detail(
    app, monkeypatch: pytest.MonkeyPatch
) -> None:
    _db(app, _postgres_error("P0002"))

    response = await _reissue(app, monkeypatch)

    assert response.status_code == 404
    assert response.json()["detail"] == "Attention episode not found"
    assert SENTINEL not in response.text


async def test_check_violation_reissue_is_409_with_fixed_detail(
    app, monkeypatch: pytest.MonkeyPatch
) -> None:
    _db(app, _postgres_error("23514"))

    response = await _reissue(app, monkeypatch)

    assert response.status_code == 409
    assert response.json()["detail"] == "Attention episode is not eligible for reissue"
    assert SENTINEL not in response.text
