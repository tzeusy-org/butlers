"""Tests for entity_info CRUD API endpoints.

Covers:
- GET /entities/{id} — entity detail with masked entity_info
- POST /entities/{id}/info — create entity_info entry
- PATCH /entities/{id}/info/{info_id} — update entity_info entry
- DELETE /entities/{id}/info/{info_id} — delete entity_info entry
- GET /entities/{id}/secrets/{info_id} — reveal secured entity_info value

Issue: bu-47d.6
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import asyncpg
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
    execute_result=None,
) -> tuple:
    mock_pool = AsyncMock()

    if fetchrow_side_effect is not None:
        mock_pool.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    else:
        mock_pool.fetchrow = AsyncMock(return_value=fetchrow_result)

    if fetch_side_effect is not None:
        mock_pool.fetch = AsyncMock(side_effect=fetch_side_effect)
    else:
        mock_pool.fetch = AsyncMock(return_value=fetch_rows or [])

    mock_pool.fetchval = AsyncMock(return_value=fetchval_result)
    mock_pool.execute = AsyncMock(return_value=execute_result)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool = MagicMock(return_value=mock_pool)
    mock_db.butler_names = ["relationship"]

    @asynccontextmanager
    async def _null_lifespan(_app):
        yield

    app.router.lifespan_context = _null_lifespan
    app.dependency_overrides[_get_rel_db_manager_fn()] = lambda: mock_db

    return app, mock_db, mock_pool


def _entity_row(eid=None, *, name="Alice Smith", entity_type="person", roles=None, metadata=None):
    return {
        "id": eid or uuid4(),
        "canonical_name": name,
        "entity_type": entity_type,
        "aliases": [],
        "roles": roles or [],
        "metadata": metadata or {},
        "created_at": _NOW,
        "updated_at": _NOW,
    }


def _entity_info_row(
    *,
    info_id=None,
    ei_type="telegram_api_hash",
    value="abc123",
    label=None,
    is_primary=False,
    secured=False,
):
    return {
        "id": info_id or uuid4(),
        "type": ei_type,
        "value": value,
        "label": label,
        "is_primary": is_primary,
        "secured": secured,
    }


# ---------------------------------------------------------------------------
# GET /api/relationship/entities/{id}
# ---------------------------------------------------------------------------


def test_get_entity_includes_entity_info(app):
    """GET /entities/{id} returns entity detail with entity_info entries."""
    eid = uuid4()
    info_id = uuid4()

    app, _, mock_pool = _app_with_mock_pool(
        app,
        fetchrow_result=_entity_row(eid),
        fetch_rows=[
            _entity_info_row(info_id=info_id, ei_type="email", value="a@b.com"),
        ],
    )

    with TestClient(app=app) as client:
        resp = client.get(f"/api/relationship/entities/{eid}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == str(eid)
    assert data["canonical_name"] == "Alice Smith"
    assert len(data["entity_info"]) == 1
    assert data["entity_info"][0]["type"] == "email"
    assert data["entity_info"][0]["value"] == "a@b.com"


def test_get_entity_masks_secured_info(app):
    """GET /entities/{id} returns value=None for secured entity_info entries."""
    eid = uuid4()

    app, _, mock_pool = _app_with_mock_pool(
        app,
        fetchrow_result=_entity_row(eid),
        fetch_rows=[
            _entity_info_row(ei_type="api_hash", value="secret", secured=True),
            _entity_info_row(ei_type="email", value="a@b.com", secured=False),
        ],
    )

    with TestClient(app=app) as client:
        resp = client.get(f"/api/relationship/entities/{eid}")

    assert resp.status_code == 200
    ei = resp.json()["entity_info"]
    secured = next(e for e in ei if e["type"] == "api_hash")
    assert secured["value"] is None
    assert secured["secured"] is True

    plain = next(e for e in ei if e["type"] == "email")
    assert plain["value"] == "a@b.com"


def test_get_entity_404_when_not_found(app):
    """GET /entities/{id} returns 404 when entity not found."""
    app, _, _ = _app_with_mock_pool(app, fetchrow_result=None)

    with TestClient(app=app) as client:
        resp = client.get(f"/api/relationship/entities/{uuid4()}")

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/relationship/entities/{id}/info
# ---------------------------------------------------------------------------


def test_create_entity_info_success(app):
    """POST /entities/{id}/info creates a new entity_info entry."""
    eid = uuid4()
    info_id = uuid4()

    app, _, mock_pool = _app_with_mock_pool(
        app,
        fetchrow_side_effect=[
            {"id": eid},  # entity exists check
            {  # INSERT RETURNING
                "id": info_id,
                "entity_id": eid,
                "type": "telegram_api_hash",
                "value": "abc123",
                "label": "Telegram",
                "is_primary": False,
                "secured": True,
            },
        ],
    )

    with TestClient(app=app) as client:
        resp = client.post(
            f"/api/relationship/entities/{eid}/info",
            json={
                "type": "telegram_api_hash",
                "value": "abc123",
                "label": "Telegram",
                "secured": True,
            },
        )

    assert resp.status_code == 201
    data = resp.json()
    assert data["id"] == str(info_id)
    assert data["entity_id"] == str(eid)
    assert data["type"] == "telegram_api_hash"
    assert data["secured"] is True
    assert data["label"] == "Telegram"


def test_create_entity_info_entity_not_found(app):
    """POST /entities/{id}/info returns 404 for missing entity."""
    app, _, _ = _app_with_mock_pool(app, fetchrow_result=None)

    with TestClient(app=app) as client:
        resp = client.post(
            f"/api/relationship/entities/{uuid4()}/info",
            json={"type": "email", "value": "a@b.com"},
        )

    assert resp.status_code == 404


def test_create_entity_info_duplicate_type_409(app):
    """POST /entities/{id}/info returns 409 on duplicate type."""
    eid = uuid4()

    app, _, mock_pool = _app_with_mock_pool(
        app,
        fetchrow_side_effect=[
            {"id": eid},  # entity exists
            asyncpg.UniqueViolationError("duplicate"),  # INSERT fails
        ],
    )

    with TestClient(app=app) as client:
        resp = client.post(
            f"/api/relationship/entities/{eid}/info",
            json={"type": "email", "value": "a@b.com"},
        )

    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# PATCH /api/relationship/entities/{id}/info/{info_id}
# ---------------------------------------------------------------------------


def test_patch_entity_info_updates_value(app):
    """PATCH /entities/{id}/info/{info_id} updates value."""
    eid = uuid4()
    info_id = uuid4()

    app, _, mock_pool = _app_with_mock_pool(
        app,
        fetchrow_side_effect=[
            {"id": info_id},  # existence check
            {  # final SELECT
                "id": info_id,
                "type": "email",
                "value": "new@example.com",
                "label": None,
                "is_primary": False,
                "secured": False,
            },
        ],
    )

    with TestClient(app=app) as client:
        resp = client.patch(
            f"/api/relationship/entities/{eid}/info/{info_id}",
            json={"value": "new@example.com"},
        )

    assert resp.status_code == 200
    assert resp.json()["value"] == "new@example.com"


def test_patch_entity_info_masks_secured_in_response(app):
    """PATCH response masks value when entry is secured."""
    eid = uuid4()
    info_id = uuid4()

    app, _, mock_pool = _app_with_mock_pool(
        app,
        fetchrow_side_effect=[
            {"id": info_id},  # existence check
            {  # final SELECT
                "id": info_id,
                "type": "api_key",
                "value": "secret-value",
                "label": None,
                "is_primary": False,
                "secured": True,
            },
        ],
    )

    with TestClient(app=app) as client:
        resp = client.patch(
            f"/api/relationship/entities/{eid}/info/{info_id}",
            json={"value": "secret-value"},
        )

    assert resp.status_code == 200
    assert resp.json()["value"] is None
    assert resp.json()["secured"] is True


def test_patch_entity_info_404_when_not_found(app):
    """PATCH /entities/{id}/info/{info_id} returns 404 when not found."""
    app, _, _ = _app_with_mock_pool(app, fetchrow_result=None)

    with TestClient(app=app) as client:
        resp = client.patch(
            f"/api/relationship/entities/{uuid4()}/info/{uuid4()}",
            json={"value": "x"},
        )

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/relationship/entities/{id}/info/{info_id}
# ---------------------------------------------------------------------------


def test_delete_entity_info_success(app):
    """DELETE /entities/{id}/info/{info_id} deletes the entry."""
    eid = uuid4()
    info_id = uuid4()

    app, _, mock_pool = _app_with_mock_pool(
        app,
        fetchrow_result={"id": info_id},  # existence check
    )

    with TestClient(app=app) as client:
        resp = client.delete(f"/api/relationship/entities/{eid}/info/{info_id}")

    assert resp.status_code == 204
    mock_pool.execute.assert_awaited_once()


def test_delete_entity_info_404_when_not_found(app):
    """DELETE /entities/{id}/info/{info_id} returns 404 when not found."""
    app, _, _ = _app_with_mock_pool(app, fetchrow_result=None)

    with TestClient(app=app) as client:
        resp = client.delete(f"/api/relationship/entities/{uuid4()}/info/{uuid4()}")

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/relationship/entities/{id}/secrets/{info_id}
# ---------------------------------------------------------------------------


def test_reveal_entity_secret_returns_value(app):
    """GET /entities/{id}/secrets/{info_id} returns the real value for secured entry."""
    eid = uuid4()
    info_id = uuid4()

    app, _, mock_pool = _app_with_mock_pool(
        app,
        fetchrow_result={
            "id": info_id,
            "type": "api_hash",
            "value": "the-real-secret",
            "secured": True,
        },
    )

    with TestClient(app=app) as client:
        resp = client.get(f"/api/relationship/entities/{eid}/secrets/{info_id}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["value"] == "the-real-secret"
    assert data["type"] == "api_hash"
    assert data["id"] == str(info_id)


def test_reveal_entity_secret_404_when_not_found(app):
    """GET /entities/{id}/secrets/{info_id} returns 404 when not found."""
    app, _, _ = _app_with_mock_pool(app, fetchrow_result=None)

    with TestClient(app=app) as client:
        resp = client.get(f"/api/relationship/entities/{uuid4()}/secrets/{uuid4()}")

    assert resp.status_code == 404


def test_reveal_entity_secret_400_when_not_secured(app):
    """GET /entities/{id}/secrets/{info_id} returns 400 when entry is not secured."""
    eid = uuid4()
    info_id = uuid4()

    app, _, mock_pool = _app_with_mock_pool(
        app,
        fetchrow_result={
            "id": info_id,
            "type": "email",
            "value": "a@b.com",
            "secured": False,
        },
    )

    with TestClient(app=app) as client:
        resp = client.get(f"/api/relationship/entities/{eid}/secrets/{info_id}")

    assert resp.status_code == 400
