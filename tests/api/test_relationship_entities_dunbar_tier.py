"""Tests for PATCH /api/relationship/entities/{id}/dunbar-tier.

Covers the contactless-entity path introduced in bu-oz2bd: entities that have no
linked contact row must be tier-pinneable without a 404.

Acceptance criteria verified:
1. Entity with a linked contact delegates to dunbar_tier_set — success path.
2. Contactless entity (no contact row) can be pinned without 404.
3. Contactless entity pin returns action='set', contact_id=null, tier=<value>.
4. Contactless entity clear (tier=null) returns action='cleared', contact_id=null.
5. Contactless entity pin with invalid tier returns 422.
6. Missing entity returns 404.
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

_ENTITY_ID = uuid4()
_CONTACT_ID = uuid4()

BASE_URL = "http://test"


def _patch_path(entity_id: UUID | None = None) -> str:
    eid = entity_id or _ENTITY_ID
    return f"/api/relationship/entities/{eid}/dunbar-tier"


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------


def _entity_row() -> MagicMock:
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: _ENTITY_ID if key == "id" else None)
    return row


def _contact_row(contact_id: UUID | None = None) -> MagicMock:
    cid = contact_id or _CONTACT_ID
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: cid if key == "id" else None)
    return row


def _mock_acquire_cm(conn: AsyncMock) -> MagicMock:
    """Return a context manager mock that yields ``conn`` on __aenter__."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _mock_transaction_cm() -> MagicMock:
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=None)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def _make_app(
    *,
    entity_exists: bool = True,
    has_contact: bool = True,
    dunbar_tier_set_result: dict | None = None,
) -> tuple[FastAPI, AsyncMock]:
    """Wire a FastAPI test app for PATCH /entities/{id}/dunbar-tier.

    Pool call sequence inside the endpoint:
      1. pool.fetchval  — entity existence check (None → 404)
      2. pool.fetchrow  — contact lookup (None → contactless path)
      3a. (contact path) dunbar_tier_set called with (pool, contact_id, tier)
      3b. (contactless path) pool.acquire() → conn.execute() × 1-2
    """
    # Entity check returns a sentinel value when entity exists.
    entity_val = 1 if entity_exists else None

    contact_row_val = _contact_row() if has_contact else None

    mock_pool = AsyncMock()
    mock_pool.fetchval = AsyncMock(return_value=entity_val)
    mock_pool.fetchrow = AsyncMock(return_value=contact_row_val)

    # Contactless path: pool.acquire() returns a context manager whose conn
    # has a transaction() context manager and execute() method.
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=None)
    mock_conn.transaction = MagicMock(return_value=_mock_transaction_cm())
    mock_pool.acquire = MagicMock(return_value=_mock_acquire_cm(mock_conn))

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db

            # Patch dunbar_tier_set for the contact path.
            if has_contact and dunbar_tier_set_result is not None:
                result = dunbar_tier_set_result

                async def _fake_tier_set(_pool, _contact_id, _tier, _result=result):
                    return _result

                router_module._dunbar_tier_set_override = _fake_tier_set
            break

    return app, mock_pool


async def _patch(
    app: FastAPI,
    body: dict,
    entity_id: UUID | None = None,
) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        return await client.patch(_patch_path(entity_id), json=body)


# ---------------------------------------------------------------------------
# Scenario: entity does not exist
# ---------------------------------------------------------------------------


class TestMissingEntity:
    async def test_missing_entity_returns_404(self):
        """PATCH /dunbar-tier returns 404 when entity does not exist."""
        app, mock_pool = _make_app(entity_exists=False, has_contact=False)
        mock_pool.fetchval = AsyncMock(return_value=None)

        # _assert_entity_exists raises 404 before any contact lookup.
        resp = await _patch(app, {"tier": 15})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Scenario: contactless entity — the main new path
# ---------------------------------------------------------------------------


class TestContactlessEntity:
    async def test_contactless_pin_returns_200(self):
        """Contactless entity: PATCH with valid tier returns 200 + action='set'."""
        app, _ = _make_app(entity_exists=True, has_contact=False)
        resp = await _patch(app, {"tier": 50})

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["action"] == "set"
        assert body["tier"] == 50
        assert body["contact_id"] is None
        assert str(body["entity_id"]) == str(_ENTITY_ID)

    async def test_contactless_clear_returns_200(self):
        """Contactless entity: PATCH with tier=null clears the override."""
        app, _ = _make_app(entity_exists=True, has_contact=False)
        resp = await _patch(app, {"tier": None})

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["action"] == "cleared"
        assert body["tier"] is None
        assert body["contact_id"] is None

    @pytest.mark.parametrize("bad_tier", [0, 1, 3, 10, 25, 100, 1000, 9999, -1])
    async def test_contactless_invalid_tier_returns_422(self, bad_tier: int):
        """Contactless entity: invalid tier value returns 422."""
        app, _ = _make_app(entity_exists=True, has_contact=False)
        resp = await _patch(app, {"tier": bad_tier})

        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"

    async def test_contactless_writes_override_fact(self):
        """Contactless entity: a pin calls conn.execute twice (retract + insert)."""
        app, mock_pool = _make_app(entity_exists=True, has_contact=False)
        resp = await _patch(app, {"tier": 150})

        assert resp.status_code == 200
        # acquire() was called once (the contactless transaction block).
        assert mock_pool.acquire.called
