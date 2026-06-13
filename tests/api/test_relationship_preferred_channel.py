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


# ===========================================================================
# COMPAT removal: contact-keyed preferred_channel write path is gone (bu-g0y3m)
# ===========================================================================


class TestContactKeyedWritePathRemoved:
    """The legacy contact-keyed write of ``contacts.preferred_channel`` via
    PATCH /contacts/{id} has been removed in favour of the entity-keyed
    ``prefers-channel`` fact endpoints above. ``ContactPatchRequest`` no longer
    carries the field, and PATCH /contacts/{id} must never UPDATE the column.
    """

    def test_contact_patch_request_has_no_preferred_channel_field(self):
        # The router module is loaded dynamically at app startup; reach its
        # ContactPatchRequest via the butler registry (mirrors the pattern in
        # test_relationship_contact_info_migration._get_router_module).
        app = create_app()
        router_module = next(m for name, m in app.state.butler_routers if name == "relationship")
        assert "preferred_channel" not in router_module.ContactPatchRequest.model_fields

    async def test_patch_contact_does_not_write_preferred_channel_column(self):
        """A PATCH carrying an (now-extra) preferred_channel key must not emit an
        UPDATE that touches the preferred_channel column."""
        from uuid import uuid4

        contact_id = uuid4()
        entity_id = uuid4()

        mock_pool = AsyncMock()
        # patch_contact only fetchrow's for the roles-entity lookup path when roles
        # are provided; with just preferred_channel + first_name there are no extra
        # reads. Provide a generic detail row for the trailing get_contact() call.
        detail_row = MagicMock()
        detail_data = {
            "id": contact_id,
            "full_name": "Alice",
            "first_name": "Alice",
            "last_name": None,
            "nickname": None,
            "notes": None,
            "birthday": None,
            "company": None,
            "job_title": None,
            "address": None,
            "metadata": {},
            "created_at": __import__("datetime").datetime.now(__import__("datetime").UTC),
            "updated_at": __import__("datetime").datetime.now(__import__("datetime").UTC),
            "roles": [],
            "entity_id": entity_id,
            "preferred_channel": None,
            "last_interaction_at": None,
        }
        detail_row.__getitem__ = MagicMock(side_effect=lambda k: detail_data[k])
        detail_row.get = MagicMock(side_effect=lambda k, d=None: detail_data.get(k, d))

        async def _fetchrow(sql, *args):
            # Return the contact detail row for the existence check + get_contact
            # SELECT (both reference the contacts table by id); the secondary
            # birthday/address lookups in get_contact get None.
            if "FROM contacts" in sql or "preferred_channel" in sql:
                return detail_row
            return None

        mock_pool.fetchrow = AsyncMock(side_effect=_fetchrow)
        mock_pool.fetch = AsyncMock(return_value=[])
        mock_pool.execute = AsyncMock(return_value="UPDATE 1")

        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.return_value = mock_pool

        app = create_app()
        for butler_name, router_module in app.state.butler_routers:
            if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
                app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
                break

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            # The extra preferred_channel key is silently ignored by the model.
            resp = await client.patch(
                f"/api/relationship/contacts/{contact_id}",
                json={"first_name": "Alice", "preferred_channel": "telegram"},
            )

        assert resp.status_code == 200
        # No executed UPDATE statement may mention the preferred_channel column.
        for call in mock_pool.execute.await_args_list:
            sql = call.args[0] if call.args else ""
            assert "preferred_channel" not in sql
