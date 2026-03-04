"""Tests for unlinked contacts / entity disambiguation API endpoints.

Covers:
- GET /contacts/unlinked — paginated unlinked contacts with suggestions
- GET /contacts/{id}/entity-suggestions — on-demand suggestions
- POST /contacts/{id}/link-entity — link existing entity
- POST /contacts/{id}/create-entity — create entity and link
- _get_memory_pool helper
- _suggest_entities scoring
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from butlers.api.db import DatabaseManager

_roster_root = Path(__file__).resolve().parents[2] / "roster"
_router_path = _roster_root / "relationship" / "api" / "router.py"
_MODULE_NAME = "relationship_api_router"


def _get_rel_db_manager_fn():
    mod = sys.modules.get(_MODULE_NAME)
    if mod is None:
        raise RuntimeError("relationship_api_router not loaded in sys.modules")
    return mod._get_db_manager


pytestmark = pytest.mark.unit

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _app_with_mock_pool(
    app,
    *,
    fetchrow_side_effect=None,
    fetchrow_result=None,
    fetch_side_effect=None,
    fetch_rows=None,
    fetchval_result=None,
    fetchval_side_effect=None,
    execute_result=None,
    butler_names=None,
) -> tuple:
    """Create a FastAPI test app with a mocked relationship database pool."""
    mock_pool = AsyncMock()

    if fetchrow_side_effect is not None:
        mock_pool.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    else:
        mock_pool.fetchrow = AsyncMock(return_value=fetchrow_result)

    if fetch_side_effect is not None:
        mock_pool.fetch = AsyncMock(side_effect=fetch_side_effect)
    else:
        mock_pool.fetch = AsyncMock(return_value=fetch_rows or [])

    if fetchval_side_effect is not None:
        mock_pool.fetchval = AsyncMock(side_effect=fetchval_side_effect)
    else:
        mock_pool.fetchval = AsyncMock(return_value=fetchval_result)

    mock_pool.execute = AsyncMock(return_value=execute_result)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool = MagicMock(return_value=mock_pool)
    mock_db.butler_names = butler_names or ["relationship"]

    @asynccontextmanager
    async def _null_lifespan(_app):
        yield

    app.router.lifespan_context = _null_lifespan
    app.dependency_overrides[_get_rel_db_manager_fn()] = lambda: mock_db

    return app, mock_db, mock_pool


def _unlinked_row(
    cid=None,
    *,
    name="Alice Smith",
    first_name="Alice",
    last_name="Smith",
    company=None,
    email=None,
    phone=None,
):
    return {
        "id": cid or uuid4(),
        "full_name": name,
        "first_name": first_name,
        "last_name": last_name,
        "company": company,
        "email": email,
        "phone": phone,
    }


# ---------------------------------------------------------------------------
# GET /contacts/unlinked
# ---------------------------------------------------------------------------


def test_unlinked_returns_empty_when_none(app):
    """GET /contacts/unlinked returns empty list when all contacts have entity_id."""
    app, _, mock_pool = _app_with_mock_pool(
        app,
        fetchval_result=0,
        fetch_rows=[],
    )

    with TestClient(app=app) as client:
        resp = client.get("/api/relationship/contacts/unlinked")

    assert resp.status_code == 200
    data = resp.json()
    assert data["contacts"] == []
    assert data["total"] == 0


def test_unlinked_returns_contacts_without_entity_id(app):
    """GET /contacts/unlinked returns unlinked contacts with empty suggestions when no memory."""
    cid = uuid4()
    app, _, mock_pool = _app_with_mock_pool(
        app,
        fetchval_side_effect=[
            1,  # total count
            False,  # _get_memory_pool: no entities table
        ],
        fetch_rows=[_unlinked_row(cid, email="alice@example.com")],
    )

    with TestClient(app=app) as client:
        resp = client.get("/api/relationship/contacts/unlinked")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["contacts"]) == 1
    assert data["contacts"][0]["id"] == str(cid)
    assert data["contacts"][0]["email"] == "alice@example.com"
    assert data["contacts"][0]["suggestions"] == []
    assert data["total"] == 1


def test_unlinked_pagination(app):
    """GET /contacts/unlinked respects offset and limit."""
    app, _, mock_pool = _app_with_mock_pool(
        app,
        fetchval_side_effect=[
            50,  # total count
            False,  # _get_memory_pool: no entities table
        ],
        fetch_rows=[_unlinked_row()],
    )

    with TestClient(app=app) as client:
        resp = client.get("/api/relationship/contacts/unlinked?offset=20&limit=10")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 50

    # Verify SQL was called with the right offset/limit
    fetch_call = mock_pool.fetch.await_args
    assert fetch_call.args[1] == 20  # offset
    assert fetch_call.args[2] == 10  # limit


# ---------------------------------------------------------------------------
# GET /contacts/{id}/entity-suggestions
# ---------------------------------------------------------------------------


def test_entity_suggestions_404_when_contact_not_found(app):
    """GET /contacts/{id}/entity-suggestions returns 404 for missing contact."""
    app, _, mock_pool = _app_with_mock_pool(app, fetchrow_result=None)

    with TestClient(app=app) as client:
        resp = client.get(f"/api/relationship/contacts/{uuid4()}/entity-suggestions")

    assert resp.status_code == 404


def test_entity_suggestions_empty_when_no_memory(app):
    """GET /contacts/{id}/entity-suggestions returns [] when no memory module."""
    cid = uuid4()
    app, _, mock_pool = _app_with_mock_pool(
        app,
        fetchrow_result={
            "id": cid,
            "full_name": "Alice",
            "first_name": "Alice",
            "last_name": "Smith",
            "company": None,
        },
        fetchval_result=False,  # no entities table
    )

    with TestClient(app=app) as client:
        resp = client.get(f"/api/relationship/contacts/{cid}/entity-suggestions")

    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# POST /contacts/{id}/link-entity
# ---------------------------------------------------------------------------


def test_link_entity_success(app):
    """POST /contacts/{id}/link-entity sets entity_id on the contact."""
    cid = uuid4()
    eid = uuid4()

    mock_memory_pool = AsyncMock()
    mock_memory_pool.fetchval = AsyncMock(return_value=True)

    app, mock_db, mock_pool = _app_with_mock_pool(
        app,
        fetchrow_result={"id": cid},
        butler_names=["relationship", "memory"],
    )

    # Make the memory butler pool return our mock
    def _pool_side_effect(butler_name):
        if butler_name == "memory":
            return mock_memory_pool
        return mock_pool

    mock_db.pool = MagicMock(side_effect=_pool_side_effect)

    with patch(
        "butlers.modules.memory.tools.entities.entity_get", new_callable=AsyncMock
    ) as mock_get:
        mock_get.return_value = {
            "entity_id": str(eid),
            "canonical_name": "Alice Smith",
            "entity_type": "person",
        }

        with TestClient(app=app) as client:
            resp = client.post(
                f"/api/relationship/contacts/{cid}/link-entity",
                json={"entity_id": str(eid)},
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["contact_id"] == str(cid)
    assert data["entity_id"] == str(eid)

    # Verify UPDATE was called
    mock_pool.execute.assert_awaited_once()
    call_sql = mock_pool.execute.await_args.args[0]
    assert "entity_id" in call_sql


def test_link_entity_404_contact(app):
    """POST /contacts/{id}/link-entity returns 404 when contact not found."""
    app, _, mock_pool = _app_with_mock_pool(app, fetchrow_result=None)

    with TestClient(app=app) as client:
        resp = client.post(
            f"/api/relationship/contacts/{uuid4()}/link-entity",
            json={"entity_id": str(uuid4())},
        )

    assert resp.status_code == 404


def test_link_entity_404_entity(app):
    """POST /contacts/{id}/link-entity returns 404 when entity not found."""
    cid = uuid4()

    mock_memory_pool = AsyncMock()
    mock_memory_pool.fetchval = AsyncMock(return_value=True)

    app, mock_db, mock_pool = _app_with_mock_pool(
        app,
        fetchrow_result={"id": cid},
        butler_names=["relationship", "memory"],
    )

    def _pool_side_effect(butler_name):
        if butler_name == "memory":
            return mock_memory_pool
        return mock_pool

    mock_db.pool = MagicMock(side_effect=_pool_side_effect)

    with patch(
        "butlers.modules.memory.tools.entities.entity_get", new_callable=AsyncMock
    ) as mock_get:
        mock_get.return_value = None  # entity not found

        with TestClient(app=app) as client:
            resp = client.post(
                f"/api/relationship/contacts/{cid}/link-entity",
                json={"entity_id": str(uuid4())},
            )

    assert resp.status_code == 404
    assert "entity" in resp.json()["detail"].lower()


def test_link_entity_503_no_memory(app):
    """POST /contacts/{id}/link-entity returns 503 when memory module unavailable."""
    cid = uuid4()
    app, _, mock_pool = _app_with_mock_pool(
        app,
        fetchrow_result={"id": cid},
        fetchval_result=False,  # no entities table
    )

    with TestClient(app=app) as client:
        resp = client.post(
            f"/api/relationship/contacts/{cid}/link-entity",
            json={"entity_id": str(uuid4())},
        )

    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# POST /contacts/{id}/create-entity
# ---------------------------------------------------------------------------


def test_create_entity_success(app):
    """POST /contacts/{id}/create-entity creates entity and links to contact."""
    cid = uuid4()
    eid = uuid4()

    mock_memory_pool = AsyncMock()
    mock_memory_pool.fetchval = AsyncMock(return_value=True)

    app, mock_db, mock_pool = _app_with_mock_pool(
        app,
        fetchrow_result={
            "id": cid,
            "full_name": "Alice Smith",
            "first_name": "Alice",
            "nickname": "Ali",
            "company": "Acme",
        },
        butler_names=["relationship", "memory"],
    )

    def _pool_side_effect(butler_name):
        if butler_name == "memory":
            return mock_memory_pool
        return mock_pool

    mock_db.pool = MagicMock(side_effect=_pool_side_effect)

    with patch(
        "butlers.modules.memory.tools.entities.entity_create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.return_value = {"entity_id": str(eid)}

        with TestClient(app=app) as client:
            resp = client.post(
                f"/api/relationship/contacts/{cid}/create-entity",
                json={},
            )

    assert resp.status_code == 201
    data = resp.json()
    assert data["contact_id"] == str(cid)
    assert data["entity_id"] == str(eid)
    assert data["canonical_name"] == "Alice Smith"

    # Verify entity_create was called with inferred aliases
    call_kwargs = mock_create.await_args
    assert "Alice" in call_kwargs.kwargs.get(
        "aliases", call_kwargs.args[3] if len(call_kwargs.args) > 3 else []
    )


def test_create_entity_404_contact(app):
    """POST /contacts/{id}/create-entity returns 404 when contact not found."""
    app, _, mock_pool = _app_with_mock_pool(app, fetchrow_result=None)

    with TestClient(app=app) as client:
        resp = client.post(
            f"/api/relationship/contacts/{uuid4()}/create-entity",
            json={},
        )

    assert resp.status_code == 404


def test_create_entity_409_duplicate(app):
    """POST /contacts/{id}/create-entity returns 409 when entity already exists."""
    cid = uuid4()

    mock_memory_pool = AsyncMock()
    mock_memory_pool.fetchval = AsyncMock(return_value=True)

    app, mock_db, mock_pool = _app_with_mock_pool(
        app,
        fetchrow_result={
            "id": cid,
            "full_name": "Alice Smith",
            "first_name": "Alice",
            "nickname": None,
            "company": None,
        },
        butler_names=["relationship", "memory"],
    )

    def _pool_side_effect(butler_name):
        if butler_name == "memory":
            return mock_memory_pool
        return mock_pool

    mock_db.pool = MagicMock(side_effect=_pool_side_effect)

    with patch(
        "butlers.modules.memory.tools.entities.entity_create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.side_effect = ValueError(
            "Entity with canonical_name='Alice Smith' already exists"
        )

        with TestClient(app=app) as client:
            resp = client.post(
                f"/api/relationship/contacts/{cid}/create-entity",
                json={},
            )

    assert resp.status_code == 409


def test_create_entity_503_no_memory(app):
    """POST /contacts/{id}/create-entity returns 503 when memory module unavailable."""
    cid = uuid4()
    app, _, mock_pool = _app_with_mock_pool(
        app,
        fetchrow_result={
            "id": cid,
            "full_name": "Alice Smith",
            "first_name": "Alice",
            "nickname": None,
            "company": None,
        },
        fetchval_result=False,  # no entities table
    )

    with TestClient(app=app) as client:
        resp = client.post(
            f"/api/relationship/contacts/{cid}/create-entity",
            json={},
        )

    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# _get_memory_pool helper
# ---------------------------------------------------------------------------


def test_get_memory_pool_returns_none_when_no_butler_has_entities(app):
    """_get_memory_pool returns None when no butler has an entities table."""
    app, _, mock_pool = _app_with_mock_pool(
        app,
        fetchval_result=False,  # no entities table
    )

    # The unlinked endpoint exercises _get_memory_pool; with no entities table
    # it should return empty suggestions
    mock_pool.fetchval = AsyncMock(side_effect=[0, False])
    mock_pool.fetch = AsyncMock(return_value=[])

    with TestClient(app=app) as client:
        resp = client.get("/api/relationship/contacts/unlinked")

    assert resp.status_code == 200
