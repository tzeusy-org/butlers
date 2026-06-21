"""Tests for /api/ingestion/priority-contacts endpoints.

Covers (priority_contacts is butler-agnostic — bu-gx13h):
- GET list (200, 503 on DB unavailable)
- POST add (201, 400 on unknown contact, 400 on roles field, 409 on duplicate)
- DELETE remove (204, 404 on missing)
- Audit entry emitted on POST and DELETE

Comprehensive wave-2 tests (cascade-delete audit emission etc.) are in §3.12
(Phase 3d) — this file provides the basic unit coverage for the CRUD surface.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from butlers.api.db import DatabaseManager
from butlers.api.routers.priority_contacts import _get_db_manager

pytestmark = pytest.mark.unit

_NOW = datetime.now(tz=UTC)


def _make_priority_contact_row(
    *,
    contact_id=None,
    added_by: str | None = "dashboard",
    contact_name: str | None = "Alice",
    entity_id=None,
):
    """Build a mock asyncpg Record-like dict for priority_contacts queries.

    After bu-hjo3i, the main SQL returns entity_id (not contact_info_values);
    channel identifiers are fetched in a separate entity_facts round-trip.
    """
    return {
        "contact_id": contact_id or uuid4(),
        "added_at": _NOW,
        "added_by": added_by,
        "contact_name": contact_name,
        "entity_id": entity_id,  # None → no entity_facts lookup
    }


def _app_with_mock_db(
    app: FastAPI,
    *,
    shared_pool=None,
    shared_pool_error=None,
):
    """Wire the app with a mock DatabaseManager over the shared pool."""
    mock_db = MagicMock(spec=DatabaseManager)
    if shared_pool_error is not None:
        mock_db.credential_shared_pool.side_effect = shared_pool_error
    else:
        if shared_pool is None:
            shared_pool = AsyncMock()
            shared_pool.fetchval = AsyncMock(return_value=0)
            shared_pool.fetch = AsyncMock(return_value=[])
            shared_pool.fetchrow = AsyncMock(return_value=None)
            shared_pool.execute = AsyncMock(return_value="DELETE 0")
        mock_db.credential_shared_pool.return_value = shared_pool
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return mock_db


def _make_record(row: dict):
    """Return a MagicMock that supports dict-style item access."""
    m = MagicMock()
    m.__getitem__ = MagicMock(side_effect=lambda key: row[key])
    return m


# ---------------------------------------------------------------------------
# GET /api/ingestion/priority-contacts — 200 (global, butler-agnostic)
# ---------------------------------------------------------------------------


async def test_list_priority_contacts_200(app):
    row = _make_priority_contact_row()
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=1)
    pool.fetch = AsyncMock(return_value=[_make_record(row)])
    _app_with_mock_db(app, shared_pool=pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/ingestion/priority-contacts")

    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body and "meta" in body
    assert body["meta"]["total"] == 1
    assert len(body["data"]) == 1
    assert "butler" not in body["data"][0]


async def test_list_priority_contacts_uses_entities_for_name(app):
    """GET data query must join public.entities (not the retired public.contacts)
    for the display name (bu-vat93)."""
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=0)
    pool.fetch = AsyncMock(return_value=[])
    _app_with_mock_db(app, shared_pool=pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.get("/api/ingestion/priority-contacts")

    data_sql = pool.fetch.call_args[0][0]
    assert "public.contacts" not in data_sql
    assert "public.entities" in data_sql


async def test_post_priority_contact_validates_against_entities(app):
    """POST validation must query public.entities, not public.contacts (bu-vat93)."""
    contact_id = uuid4()
    inserted_row = {
        "contact_id": contact_id,
        "added_at": _NOW,
        "added_by": "dashboard",
    }
    pool = AsyncMock()
    # Two fetchval calls: (1) entity-exists check → True, (2) entity_id duplicate
    # guard → False (not yet a priority contact).
    pool.fetchval = AsyncMock(side_effect=[True, False])
    pool.fetchrow = AsyncMock(return_value=_make_record(inserted_row))
    _app_with_mock_db(app, shared_pool=pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/api/ingestion/priority-contacts",
            json={"contact_id": str(contact_id)},
        )

    # The entity-existence check is the first fetchval call.
    first_fetchval_sql = pool.fetchval.call_args_list[0][0][0]
    assert "public.contacts" not in first_fetchval_sql
    assert "public.entities" in first_fetchval_sql


async def test_list_priority_contacts_503_on_db_unavailable(app):
    _app_with_mock_db(app, shared_pool_error=KeyError("no shared pool"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/ingestion/priority-contacts")
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# POST /api/ingestion/priority-contacts — 201 success
# ---------------------------------------------------------------------------


async def test_post_priority_contact_201(app):
    contact_id = uuid4()
    inserted_row = {
        "contact_id": contact_id,
        "added_at": _NOW,
        "added_by": "dashboard",
    }
    pool = AsyncMock()
    # Two fetchval calls: entity-exists → True, entity_id duplicate guard → False.
    pool.fetchval = AsyncMock(side_effect=[True, False])
    pool.fetchrow = AsyncMock(return_value=_make_record(inserted_row))
    _app_with_mock_db(app, shared_pool=pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/ingestion/priority-contacts",
            json={"contact_id": str(contact_id)},
        )

    assert resp.status_code == 201
    body = resp.json()
    assert body["contact_id"] == str(contact_id)
    assert "butler" not in body


async def test_post_priority_contact_400_on_unknown_contact(app):
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=False)  # entity does NOT exist
    _app_with_mock_db(app, shared_pool=pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/ingestion/priority-contacts",
            json={"contact_id": str(uuid4())},
        )

    assert resp.status_code == 400
    assert "not found" in resp.json()["detail"].lower()


async def test_post_priority_contact_400_on_roles_field(app):
    """Payloads containing a 'roles' field must be rejected with HTTP 400."""
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=True)
    _app_with_mock_db(app, shared_pool=pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/ingestion/priority-contacts",
            json={"contact_id": str(uuid4()), "roles": ["owner"]},
        )

    assert resp.status_code == 400
    assert "role" in resp.json()["detail"].lower()


async def test_post_priority_contact_409_on_duplicate_entity_id(app):
    """409 when entity_id already exists in priority_contacts (backfilled legacy row)."""
    pool = AsyncMock()
    # entity-exists → True, entity_id duplicate guard → True (already present).
    pool.fetchval = AsyncMock(side_effect=[True, True])
    _app_with_mock_db(app, shared_pool=pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/ingestion/priority-contacts",
            json={"contact_id": str(uuid4())},
        )

    assert resp.status_code == 409


async def test_post_priority_contact_409_on_contact_id_pk_violation(app):
    """409 via PK constraint when a same-session race causes a UniqueViolationError."""
    contact_id = uuid4()
    pool = AsyncMock()
    # entity-exists → True, entity_id duplicate guard → False (passed),
    # but the INSERT hits a PK conflict (race condition).
    pool.fetchval = AsyncMock(side_effect=[True, False])

    async def _raise_duplicate(*_args, **_kwargs):
        raise asyncpg.UniqueViolationError()

    pool.fetchrow = AsyncMock(side_effect=_raise_duplicate)
    _app_with_mock_db(app, shared_pool=pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/ingestion/priority-contacts",
            json={"contact_id": str(contact_id)},
        )

    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# DELETE /api/ingestion/priority-contacts/{contact_id}
# ---------------------------------------------------------------------------


async def test_delete_priority_contact_204(app):
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value="DELETE 1")
    _app_with_mock_db(app, shared_pool=pool)

    contact_id = uuid4()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.delete(f"/api/ingestion/priority-contacts/{contact_id}")

    assert resp.status_code == 204


async def test_delete_priority_contact_404_on_missing(app):
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value="DELETE 0")
    _app_with_mock_db(app, shared_pool=pool)

    contact_id = uuid4()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.delete(f"/api/ingestion/priority-contacts/{contact_id}")

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Audit entry emitted on POST and DELETE
# ---------------------------------------------------------------------------


async def test_post_priority_contact_emits_audit(app):
    """POST must call audit.append with action='ingestion.priority_contact.add'."""
    contact_id = uuid4()
    inserted_row = {
        "contact_id": contact_id,
        "added_at": _NOW,
        "added_by": "dashboard",
    }
    pool = AsyncMock()
    # Two fetchval calls: entity-exists → True, entity_id duplicate guard → False.
    pool.fetchval = AsyncMock(side_effect=[True, False])
    pool.fetchrow = AsyncMock(return_value=_make_record(inserted_row))
    _app_with_mock_db(app, shared_pool=pool)

    with patch(
        "butlers.api.routers.priority_contacts._audit_append", new_callable=AsyncMock
    ) as mock_audit:
        mock_audit.return_value = 1
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/ingestion/priority-contacts",
                json={"contact_id": str(contact_id)},
            )

    assert resp.status_code == 201
    mock_audit.assert_awaited_once()
    call_kwargs = mock_audit.await_args.kwargs
    assert call_kwargs["action"] == "ingestion.priority_contact.add"
    assert call_kwargs["target"] == str(contact_id)


async def test_delete_priority_contact_emits_audit(app):
    """DELETE must call audit.append with action='ingestion.priority_contact.remove'."""
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value="DELETE 1")
    _app_with_mock_db(app, shared_pool=pool)

    contact_id = uuid4()
    with patch(
        "butlers.api.routers.priority_contacts._audit_append", new_callable=AsyncMock
    ) as mock_audit:
        mock_audit.return_value = 1
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete(f"/api/ingestion/priority-contacts/{contact_id}")

    assert resp.status_code == 204
    mock_audit.assert_awaited_once()
    call_kwargs = mock_audit.await_args.kwargs
    assert call_kwargs["action"] == "ingestion.priority_contact.remove"
    assert call_kwargs["target"] == str(contact_id)
