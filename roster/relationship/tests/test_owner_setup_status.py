"""GET /api/relationship/owner/setup-status resolves the owner entity id only.

The dashboard's only consumer (EntityFinder's owner-pinned set) reads
``entity_id``.  The former ``has_*`` flags were computed from
``public.entity_info`` while the setup banner writes channels to
``relationship.entity_facts``, so they read false after setup; they were
dropped rather than re-pointed (bu-60pwv6.31.14).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

from butlers.api.db import DatabaseManager
from tests.api.auth_helpers import create_authenticated_domain_app as create_app

pytestmark = pytest.mark.unit

_PATH = "/api/relationship/owner/setup-status"


async def _get_setup_status(owner_row: dict | None) -> tuple[httpx.Response, AsyncMock]:
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=owner_row)
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool

    app = create_app()
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: db
            break

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(_PATH), pool


async def test_owner_setup_status_returns_only_the_owner_entity_id():
    owner_id = uuid4()
    resp, pool = await _get_setup_status({"id": owner_id})
    assert resp.status_code == 200
    assert resp.json() == {"entity_id": str(owner_id)}
    # One owner lookup; no public.entity_info channel scan behind removed flags.
    pool.fetchrow.assert_awaited_once()
    pool.fetch.assert_not_called()

    # No owner yet: 200 with a null id, as before (not a 404).
    resp, _ = await _get_setup_status(None)
    assert resp.status_code == 200
    assert resp.json() == {"entity_id": None}
