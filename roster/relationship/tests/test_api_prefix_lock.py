"""Prefix-lock integration test — locks in /api/relationship/ as the canonical prefix.

Spec anchor: openspec/specs/dashboard-relationship/spec.md
             RFC 0007:31 (auto-discovery prefix convention)
Bead: bu-cj8om — Drop /api/butlers/ prefix from dashboard-relationship spec

The relationship router is registered with prefix='/api/relationship' (see
roster/relationship/api/router.py:127). This file asserts that:

  - /api/relationship/entities returns a valid HTTP response (route exists)
  - /api/butlers/relationship/entities returns 404 (wrong prefix is not mounted)

This guards against any accidental re-registration of routes under the legacy
/api/butlers/ prefix, which was erroneously cited in spec docs before bu-cj8om.

Tests are marked ``unit`` so they run without a Docker/Postgres daemon.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_OWNER_ENTITY_ID = uuid4()


def _make_owner_row() -> MagicMock:
    """Minimal owner row so the auth gate passes on the list endpoint."""
    data = {"id": _OWNER_ENTITY_ID, "roles": ["owner"]}
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda k: data[k])
    return row


def _wire_app_for_list() -> FastAPI:
    """Return a FastAPI app wired with a mock pool that handles list-entities."""
    mock_pool = AsyncMock()
    # Owner auth gate query
    mock_pool.fetchrow = AsyncMock(return_value=_make_owner_row())
    # Total count for the list
    mock_pool.fetchval = AsyncMock(return_value=0)
    # Empty item list
    mock_pool.fetch = AsyncMock(return_value=[])

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app(api_key="test-key")
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break
    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestApiPrefixLock:
    """Lock in /api/relationship/ as the sole prefix for the relationship router."""

    async def test_correct_prefix_returns_non_404(self):
        """GET /api/relationship/entities must be routed (not 404).

        The exact status code depends on the auth gate and mock data, but the
        route MUST exist — any non-404 code confirms the prefix is correct.
        """
        app = _wire_app_for_list()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/relationship/entities",
                headers={"X-API-Key": "test-key"},
            )
        assert resp.status_code != 404, (
            f"Expected /api/relationship/entities to be routed, got 404. "
            f"Prefix may have regressed. Response: {resp.text}"
        )

    async def test_legacy_butlers_prefix_returns_404(self):
        """GET /api/butlers/relationship/entities must return 404.

        The /api/butlers/<butler>/ prefix was never the correct convention
        (RFC 0007:31 specifies /api/<butler>/). This test locks in that the
        wrong prefix is NOT mounted, so any accidental re-registration is
        caught immediately.
        """
        app = _wire_app_for_list()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/butlers/relationship/entities",
                headers={"X-API-Key": "test-key"},
            )
        assert resp.status_code == 404, (
            f"Expected /api/butlers/relationship/entities to return 404, "
            f"got {resp.status_code}. The legacy /api/butlers/ prefix must NOT be mounted. "
            f"Response: {resp.text}"
        )
