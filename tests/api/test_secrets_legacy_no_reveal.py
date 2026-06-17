"""Assert the legacy raw-secret reveal route is absent (bu-dl98i.1.1).

The GET /api/butlers/{name}/secrets/{key}/reveal endpoint was removed to
prevent plaintext secret values from being returned through the dashboard
API.  This test confirms the route does not exist and returns 404.

See: openspec/specs/dashboard-admin-gateway/spec.md — "Write-only value
masking: no actual value retrieval" under Secrets and Credentials Management.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.routers.secrets import _get_db_manager as _secrets_get_db

pytestmark = pytest.mark.unit


@contextmanager
def _secrets_app(app, *, secret_value: str = "s3cr3t"):
    """Wire the secrets router with a mock store that holds a value.

    Even with a live-looking store, the reveal route must not be reachable.
    """
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = MagicMock()
    mock_store = AsyncMock()
    mock_store.load.return_value = secret_value
    mock_store.list_secrets.return_value = []
    app.dependency_overrides[_secrets_get_db] = lambda: mock_db
    with patch("butlers.api.routers.secrets.CredentialStore", return_value=mock_store):
        yield app, mock_store


class TestLegacyRevealRouteAbsent:
    """The legacy plaintext-reveal endpoint must not exist on the secrets router."""

    async def test_reveal_route_returns_404(self, app):
        """GET /api/butlers/{name}/secrets/{key}/reveal must return 404.

        The route was intentionally removed in bu-dl98i.1.1 to eliminate
        the only path that returned raw secret values from the legacy router.
        """
        with _secrets_app(app) as (a, _):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=a), base_url="http://test"
            ) as client:
                resp = await client.get("/api/butlers/atlas/secrets/MY_KEY/reveal")
        assert resp.status_code == 404, (
            f"Legacy reveal route must not exist; expected 404 but got {resp.status_code}."
            " The raw-secret reveal endpoint was removed to prevent plaintext leakage."
        )

    async def test_reveal_route_body_contains_no_plaintext(self, app):
        """The response body must not contain the stored secret value."""
        with _secrets_app(app, secret_value="super_secret_value") as (a, _):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=a), base_url="http://test"
            ) as client:
                resp = await client.get("/api/butlers/atlas/secrets/MY_KEY/reveal")
        assert "super_secret_value" not in resp.text, (
            "Plaintext secret value must never appear in any legacy-router response."
        )

    async def test_reveal_route_absent_for_all_butler_names(self, app):
        """The reveal route is absent regardless of which butler name is given."""
        with _secrets_app(app) as (a, _):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=a), base_url="http://test"
            ) as client:
                shared_resp = await client.get(
                    "/api/butlers/shared/secrets/ANTHROPIC_API_KEY/reveal"
                )
                atlas_resp = await client.get(
                    "/api/butlers/atlas/secrets/BUTLER_TELEGRAM_TOKEN/reveal"
                )
        assert shared_resp.status_code == 404
        assert atlas_resp.status_code == 404
