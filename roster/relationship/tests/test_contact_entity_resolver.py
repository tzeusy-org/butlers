"""Tests for GET /api/relationship/contacts/{contact_id}/entity.

Endpoint: resolve_contact_entity
Spec anchor: bu-m8gb6.4 (contact-to-entity redirect resolver)

Coverage:
  - Happy path: contact linked to an entity (status="linked", entity_id present)
  - Contact exists but has no entity link (status="unlinked", entity_id=null)
  - Contact does not exist (HTTP 404)
  - Invalid UUID in path (HTTP 422)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "http://test"
_CONTACT_ID = uuid4()
_ENTITY_ID = uuid4()
_MISSING_ID = uuid4()

_RESOLVER_PATH = f"/api/relationship/contacts/{_CONTACT_ID}/entity"
_MISSING_PATH = f"/api/relationship/contacts/{_MISSING_ID}/entity"
_INVALID_PATH = "/api/relationship/contacts/not-a-uuid/entity"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_contact_row(entity_id: UUID | None) -> MagicMock:
    """Minimal contact row with only the entity_id field."""
    data: dict = {"entity_id": entity_id}
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda k: data[k])
    return row


def _wire_app(mock_pool: AsyncMock) -> FastAPI:
    """Attach a mock pool to a fresh create_app() instance."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool
    app = create_app()
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break
    return app


async def _get(app: FastAPI, path: str) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        return await client.get(path)


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------


class TestResolveContactEntity:
    """GET /contacts/{contact_id}/entity — contact-to-entity resolver."""

    def _make_app(self, *, fetchrow_result) -> FastAPI:
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=fetchrow_result)
        return _wire_app(mock_pool)

    async def test_linked_contact_returns_200_with_entity_id(self):
        """Contact linked to an entity returns status=linked and the entity_id."""
        row = _make_contact_row(entity_id=_ENTITY_ID)
        app = self._make_app(fetchrow_result=row)
        resp = await _get(app, _RESOLVER_PATH)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "linked"
        assert body["entity_id"] == str(_ENTITY_ID)

    async def test_unlinked_contact_returns_200_with_null_entity_id(self):
        """Contact without an entity link returns status=unlinked and entity_id=null."""
        row = _make_contact_row(entity_id=None)
        app = self._make_app(fetchrow_result=row)
        resp = await _get(app, _RESOLVER_PATH)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "unlinked"
        assert body["entity_id"] is None

    async def test_missing_contact_returns_404(self):
        """Non-existent contact_id returns HTTP 404."""
        app = self._make_app(fetchrow_result=None)
        resp = await _get(app, _MISSING_PATH)
        assert resp.status_code == 404
        body = resp.json()
        assert "not found" in body.get("detail", "").lower()

    async def test_invalid_uuid_returns_422(self):
        """Non-UUID contact_id path segment returns HTTP 422 (FastAPI validation)."""
        mock_pool = AsyncMock()
        app = _wire_app(mock_pool)
        resp = await _get(app, _INVALID_PATH)
        assert resp.status_code == 422

    async def test_response_contains_no_extra_contact_fields(self):
        """Resolver returns only entity_id and status — no contact CRM payload."""
        row = _make_contact_row(entity_id=_ENTITY_ID)
        app = self._make_app(fetchrow_result=row)
        resp = await _get(app, _RESOLVER_PATH)
        assert resp.status_code == 200
        body = resp.json()
        # Only the two minimal fields are present — no contact name, labels, etc.
        assert set(body.keys()) == {"entity_id", "status"}

    async def test_db_query_uses_contact_id(self):
        """The endpoint queries contacts table by the supplied contact_id."""
        row = _make_contact_row(entity_id=_ENTITY_ID)
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=row)
        app = _wire_app(mock_pool)
        await _get(app, _RESOLVER_PATH)
        mock_pool.fetchrow.assert_awaited_once()
        call_args = mock_pool.fetchrow.call_args[0]
        # The contact UUID must appear in the positional args.
        assert _CONTACT_ID in call_args
