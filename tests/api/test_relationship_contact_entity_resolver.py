"""Tests for GET /api/relationship/contacts/{contact_id}/entity.

Endpoint: resolve_contact_entity
Handler: roster/relationship/api/router.py — resolve_contact_entity()
Spec anchor: bu-m8gb6.4 (contact-to-entity redirect resolver)

The endpoint is used by the /contacts/:contactId frontend redirect to locate
the linked entity before navigating to /entities/:entityId.

Real contract (from handler):

  1. Contact WITH linked entity_id
     → HTTP 200  {"entity_id": "<uuid>", "status": "linked"}

  2. Contact EXISTS but has NO entity_id link
     → HTTP 200  {"entity_id": null, "status": "unlinked"}

  3. contact_id NOT found in contacts table
     → HTTP 404  {"detail": "<message containing 'not found'>"}

  4. contact_id is not a valid UUID
     → HTTP 422  (FastAPI path-param validation)

All tests are unit-level (mocked DB pool — no Postgres or Docker required).
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
    """Build a minimal asyncpg-Record mock with only the entity_id column."""
    data: dict = {"entity_id": entity_id}
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda k: data[k])
    return row


def _wire_app(fetchrow_result: object) -> tuple[FastAPI, AsyncMock]:
    """Return (app, mock_pool) wired with the given fetchrow result."""
    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(return_value=fetchrow_result)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break

    return app, mock_pool


async def _get(app: FastAPI, path: str) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        return await client.get(path)


# ---------------------------------------------------------------------------
# Case 1 — contact WITH a linked entity_id
# ---------------------------------------------------------------------------


class TestContactLinkedToEntity:
    """Contact exists and has a non-null entity_id."""

    async def test_returns_200(self):
        row = _make_contact_row(entity_id=_ENTITY_ID)
        app, _ = _wire_app(row)
        resp = await _get(app, _RESOLVER_PATH)
        assert resp.status_code == 200

    async def test_status_is_linked(self):
        row = _make_contact_row(entity_id=_ENTITY_ID)
        app, _ = _wire_app(row)
        resp = await _get(app, _RESOLVER_PATH)
        assert resp.json()["status"] == "linked"

    async def test_entity_id_matches(self):
        row = _make_contact_row(entity_id=_ENTITY_ID)
        app, _ = _wire_app(row)
        resp = await _get(app, _RESOLVER_PATH)
        assert resp.json()["entity_id"] == str(_ENTITY_ID)

    async def test_response_keys_are_exactly_entity_id_and_status(self):
        """Resolver returns only entity_id and status — no extra CRM fields."""
        row = _make_contact_row(entity_id=_ENTITY_ID)
        app, _ = _wire_app(row)
        resp = await _get(app, _RESOLVER_PATH)
        assert set(resp.json().keys()) == {"entity_id", "status"}

    async def test_db_queried_with_correct_contact_id(self):
        """The handler queries contacts by the exact contact_id in the path."""
        row = _make_contact_row(entity_id=_ENTITY_ID)
        app, mock_pool = _wire_app(row)
        await _get(app, _RESOLVER_PATH)
        mock_pool.fetchrow.assert_awaited_once()
        call_args = mock_pool.fetchrow.call_args[0]
        assert _CONTACT_ID in call_args


# ---------------------------------------------------------------------------
# Case 2 — contact EXISTS but has NO entity link (unlinked)
# ---------------------------------------------------------------------------


class TestContactUnlinked:
    """Contact exists but entity_id is NULL — not a hard error, recovery state."""

    async def test_returns_200(self):
        row = _make_contact_row(entity_id=None)
        app, _ = _wire_app(row)
        resp = await _get(app, _RESOLVER_PATH)
        assert resp.status_code == 200

    async def test_status_is_unlinked(self):
        row = _make_contact_row(entity_id=None)
        app, _ = _wire_app(row)
        resp = await _get(app, _RESOLVER_PATH)
        assert resp.json()["status"] == "unlinked"

    async def test_entity_id_is_null(self):
        row = _make_contact_row(entity_id=None)
        app, _ = _wire_app(row)
        resp = await _get(app, _RESOLVER_PATH)
        assert resp.json()["entity_id"] is None

    async def test_response_keys_are_exactly_entity_id_and_status(self):
        row = _make_contact_row(entity_id=None)
        app, _ = _wire_app(row)
        resp = await _get(app, _RESOLVER_PATH)
        assert set(resp.json().keys()) == {"entity_id", "status"}


# ---------------------------------------------------------------------------
# Case 3 — contact_id NOT found → 404
# ---------------------------------------------------------------------------


class TestContactNotFound:
    """Non-existent contact_id returns HTTP 404."""

    async def test_returns_404(self):
        app, _ = _wire_app(fetchrow_result=None)
        resp = await _get(app, _MISSING_PATH)
        assert resp.status_code == 404

    async def test_detail_mentions_not_found(self):
        app, _ = _wire_app(fetchrow_result=None)
        resp = await _get(app, _MISSING_PATH)
        assert "not found" in resp.json().get("detail", "").lower()


# ---------------------------------------------------------------------------
# Case 4 — invalid UUID path segment → 422
# ---------------------------------------------------------------------------


class TestInvalidUUID:
    """Non-UUID contact_id triggers FastAPI path-param validation failure."""

    async def test_returns_422(self):
        app, _ = _wire_app(fetchrow_result=None)
        resp = await _get(app, _INVALID_PATH)
        assert resp.status_code == 422
