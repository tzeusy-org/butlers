"""Tests for the entity-keyed preferred-channel endpoints.

  PUT    /api/relationship/entities/{id}/preferred-channel
  DELETE /api/relationship/entities/{id}/preferred-channel

entity-keyed-preferred-channel (group 3, bu-zhvfw): the dashboard sets/clears the
preferred channel through the single-valued ``prefers-channel`` fact (group 1)
rather than the orphaned ``public.contacts.preferred_channel`` CRM column.

Each test mocks the DB pool (owner gate via fetchrow, entity-exists via fetchval)
and patches the group-1 fact helpers, so no real Postgres or Docker is needed.
Tests are marked ``unit``.

Acceptance criteria:
1. PUT asserts the preference; returns 200 + {outcome, channel}.
2. PUT returns 400 when the channel is unreachable (assert_prefers_channel raises).
3. PUT returns 403 (owner_required) when no owner entity is registered.
4. PUT returns 404 for an unknown entity.
5. DELETE retracts the preference; returns 200 + {cleared}.
6. DELETE is idempotent (cleared == 0 when nothing was set).
7. DELETE returns 403 / 404 on the same gates as PUT.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit

_ENT_ID = uuid4()
_OWNER_ENTITY_ID = uuid4()
_PATH = f"/api/relationship/entities/{_ENT_ID}/preferred-channel"

_ASSERT_TARGET = "butlers.tools.relationship.relationship_assert_fact.assert_prefers_channel"
_RETRACT_TARGET = "butlers.tools.relationship.relationship_assert_fact.retract_prefers_channel"


def _make_owner_row() -> MagicMock:
    data = {"id": _OWNER_ENTITY_ID, "roles": ["owner"]}
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    return row


def _make_assert_result(outcome: str = "inserted"):
    from butlers.tools.relationship.relationship_assert_fact import AssertOutcome, AssertResult

    return AssertResult(outcome=AssertOutcome(outcome), fact_id=uuid4())


def _make_app(*, owner_exists: bool = True, entity_exists: bool = True) -> FastAPI:
    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(return_value=_make_owner_row() if owner_exists else None)
    mock_pool.fetchval = AsyncMock(return_value=1 if entity_exists else None)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break
    return app


async def _put(app: FastAPI, json_body: dict, path: str = _PATH) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.put(path, json=json_body)


async def _delete(app: FastAPI, path: str = _PATH) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.delete(path)


# ===========================================================================
# PUT /entities/{id}/preferred-channel
# ===========================================================================


class TestSetPreferredChannel:
    async def test_asserts_preference_returns_200(self):
        app = _make_app()
        with patch(_ASSERT_TARGET, new=AsyncMock(return_value=_make_assert_result("inserted"))):
            resp = await _put(app, {"channel": "telegram"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["outcome"] == "inserted"
        assert body["channel"] == "telegram"

    async def test_supersede_outcome_is_passed_through(self):
        app = _make_app()
        with patch(_ASSERT_TARGET, new=AsyncMock(return_value=_make_assert_result("superseded"))):
            resp = await _put(app, {"channel": "email"})

        assert resp.status_code == 200
        assert resp.json()["outcome"] == "superseded"

    async def test_unreachable_channel_returns_400(self):
        app = _make_app()
        with patch(
            _ASSERT_TARGET,
            new=AsyncMock(side_effect=ValueError("entity has no contact fact for 'telegram'")),
        ):
            resp = await _put(app, {"channel": "telegram"})

        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "invalid_preferred_channel"

    async def test_no_owner_returns_403(self):
        app = _make_app(owner_exists=False)
        with patch(_ASSERT_TARGET, new=AsyncMock(return_value=_make_assert_result())):
            resp = await _put(app, {"channel": "email"})

        assert resp.status_code == 403

    async def test_unknown_entity_returns_404(self):
        app = _make_app(entity_exists=False)
        with patch(_ASSERT_TARGET, new=AsyncMock(return_value=_make_assert_result())):
            resp = await _put(app, {"channel": "email"})

        assert resp.status_code == 404


# ===========================================================================
# DELETE /entities/{id}/preferred-channel
# ===========================================================================


class TestClearPreferredChannel:
    async def test_retracts_preference_returns_200(self):
        app = _make_app()
        with patch(_RETRACT_TARGET, new=AsyncMock(return_value=1)):
            resp = await _delete(app)

        assert resp.status_code == 200
        assert resp.json()["cleared"] == 1

    async def test_idempotent_clear_returns_zero(self):
        app = _make_app()
        with patch(_RETRACT_TARGET, new=AsyncMock(return_value=0)):
            resp = await _delete(app)

        assert resp.status_code == 200
        assert resp.json()["cleared"] == 0

    async def test_no_owner_returns_403(self):
        app = _make_app(owner_exists=False)
        with patch(_RETRACT_TARGET, new=AsyncMock(return_value=0)):
            resp = await _delete(app)

        assert resp.status_code == 403

    async def test_unknown_entity_returns_404(self):
        app = _make_app(entity_exists=False)
        with patch(_RETRACT_TARGET, new=AsyncMock(return_value=0)):
            resp = await _delete(app)

        assert resp.status_code == 404
