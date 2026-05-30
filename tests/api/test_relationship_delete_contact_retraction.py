"""Tests for retraction of entity_facts triples when a contact is deleted.

Covers the gap identified in bu-5bvzk: DELETE /contacts/{contact_id} must
retract all active ``has-*`` facts in ``relationship.entity_facts`` that
correspond to the contact's channel rows before the contact row (and its
CASCADE-linked ``public.contact_info`` rows) is deleted.

Acceptance criteria:
1. Deleting a contact with an entity_id and one email channel retracts the
   matching ``has-email`` fact in ``relationship.entity_facts``.
2. Deleting a contact with multiple channel types retracts each corresponding
   fact (one retraction call per channel row).
3. Deleting a contact with no entity_id (unlinked contact) skips retraction
   entirely and still succeeds (204).
4. Deleting a contact whose channel type has no predicate mapping (e.g.
   ``'telegram_chat_id'``) does not attempt retraction for that row.
5. A retraction failure does not abort the contact delete (graceful degradation).
6. Deleting a contact that does not exist returns 404.

All tests are unit-level (mock pool — no Postgres or Docker required).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
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

_CONTACT_ID = uuid4()
_ENTITY_ID = uuid4()
_MISSING_CONTACT_ID = uuid4()

_EMAIL = "alice@example.com"
_PHONE = "+1-555-0100"

_DELETE_PATH = f"/api/relationship/contacts/{_CONTACT_ID}"


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------


def _make_contact_row(
    contact_id: UUID | None = None,
    entity_id: UUID | None = None,
) -> MagicMock:
    """Simulate a row returned by 'SELECT id, entity_id FROM contacts WHERE id = $1'."""
    data = {
        "id": contact_id or _CONTACT_ID,
        "entity_id": entity_id,
    }
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    return row


def _make_ci_row(ci_type: str, value: str) -> MagicMock:
    """Simulate a contact_info row with type and value."""
    data = {"type": ci_type, "value": value}
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    return row


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def _make_app(
    *,
    contact_row: MagicMock | None,
    ci_rows: list[MagicMock] | None = None,
    source_links_exist: bool = True,
) -> tuple[FastAPI, AsyncMock]:
    """Wire a FastAPI app with a mocked relationship DB pool.

    ``fetchrow`` returns the contact row (or None when contact is missing).
    ``fetch``    returns the contact_info rows for the contact.
    ``fetchval`` returns True/1 when source_links table exists.
    ``execute``  is a spy — tracks all DELETE/UPDATE calls.
    """
    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(return_value=contact_row)
    mock_pool.fetch = AsyncMock(return_value=ci_rows or [])
    mock_pool.fetchval = AsyncMock(return_value=1 if source_links_exist else None)
    mock_pool.execute = AsyncMock(return_value="DELETE 1")

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break

    return app, mock_pool


async def _delete(app: FastAPI, path: str = _DELETE_PATH) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.delete(path)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDeleteContactRetractsEntityFacts:
    """DELETE /contacts/{id} retracts matching entity_facts triples."""

    async def test_single_email_channel_retracts_has_email_fact(self):
        """Deleting a contact with one email channel calls retract once."""
        contact_row = _make_contact_row(entity_id=_ENTITY_ID)
        ci_rows = [_make_ci_row("email", _EMAIL)]
        app, _ = _make_app(contact_row=contact_row, ci_rows=ci_rows)

        with patch(
            "butlers.tools.relationship.relationship_assert_fact.retract_contact_info_fact",
            new=AsyncMock(return_value=uuid4()),
        ) as mock_retract:
            resp = await _delete(app)

        assert resp.status_code == 204
        mock_retract.assert_awaited_once()
        call_kwargs = mock_retract.call_args
        # subject must be the entity_id
        assert call_kwargs.kwargs["subject"] == _ENTITY_ID or call_kwargs.args[1] == _ENTITY_ID
        # ci_type must be 'email'
        assert call_kwargs.kwargs.get("ci_type") == "email" or "email" in str(call_kwargs)
        # ci_value must be the email address
        assert call_kwargs.kwargs.get("ci_value") == _EMAIL or _EMAIL in str(call_kwargs)

    async def test_multiple_channels_each_retracted(self):
        """Two channel rows → two retraction calls, one per channel."""
        contact_row = _make_contact_row(entity_id=_ENTITY_ID)
        ci_rows = [
            _make_ci_row("email", _EMAIL),
            _make_ci_row("phone", _PHONE),
        ]
        app, _ = _make_app(contact_row=contact_row, ci_rows=ci_rows)

        with patch(
            "butlers.tools.relationship.relationship_assert_fact.retract_contact_info_fact",
            new=AsyncMock(return_value=uuid4()),
        ) as mock_retract:
            resp = await _delete(app)

        assert resp.status_code == 204
        assert mock_retract.await_count == 2

    async def test_no_entity_id_skips_retraction(self):
        """Unlinked contact (entity_id=None) → retraction is not called."""
        contact_row = _make_contact_row(entity_id=None)
        ci_rows = [_make_ci_row("email", _EMAIL)]
        app, _ = _make_app(contact_row=contact_row, ci_rows=ci_rows)

        with patch(
            "butlers.tools.relationship.relationship_assert_fact.retract_contact_info_fact",
            new=AsyncMock(return_value=None),
        ) as mock_retract:
            resp = await _delete(app)

        assert resp.status_code == 204
        mock_retract.assert_not_awaited()

    async def test_unmapped_ci_type_does_not_retract(self):
        """Channel types with no predicate mapping return None (no DB write)."""
        import asyncpg

        from butlers.tools.relationship.relationship_assert_fact import retract_contact_info_fact

        # Call retract_contact_info_fact directly with a no-mapped type.
        # It must return None without hitting the DB.
        mock_pool = AsyncMock(spec=asyncpg.Pool)

        result = await retract_contact_info_fact(
            mock_pool,
            subject=_ENTITY_ID,
            ci_type="telegram_chat_id",
            ci_value="12345678",
        )

        assert result is None
        # Pool must not have been used (no acquire call).
        mock_pool.acquire.assert_not_called()

    async def test_retraction_failure_does_not_abort_delete(self):
        """If retraction raises, delete still completes (graceful degradation)."""
        contact_row = _make_contact_row(entity_id=_ENTITY_ID)
        ci_rows = [_make_ci_row("email", _EMAIL)]
        app, mock_pool = _make_app(contact_row=contact_row, ci_rows=ci_rows)

        with patch(
            "butlers.tools.relationship.relationship_assert_fact.retract_contact_info_fact",
            new=AsyncMock(side_effect=RuntimeError("DB hiccup")),
        ):
            resp = await _delete(app)

        # Contact delete must still complete.
        assert resp.status_code == 204

        # The final DELETE FROM contacts must have been called.
        delete_calls = [str(c) for c in mock_pool.execute.call_args_list]
        assert any("contacts" in s.lower() for s in delete_calls), (
            "Expected a DELETE FROM contacts execute call"
        )

    async def test_contact_not_found_returns_404(self):
        """Unknown contact_id → 404, retraction is never attempted."""
        app, _ = _make_app(contact_row=None)

        with patch(
            "butlers.tools.relationship.relationship_assert_fact.retract_contact_info_fact",
            new=AsyncMock(return_value=None),
        ) as mock_retract:
            resp = await _delete(app, f"/api/relationship/contacts/{_MISSING_CONTACT_ID}")

        assert resp.status_code == 404
        mock_retract.assert_not_awaited()


class TestRetractContactInfoFactUnit:
    """Unit tests for retract_contact_info_fact() directly."""

    async def test_returns_fact_id_when_active_row_found(self):
        """Active fact found → retract and return its id."""
        import asyncpg

        from butlers.tools.relationship.relationship_assert_fact import retract_contact_info_fact

        fact_id = uuid4()
        fact_row = MagicMock()
        fact_row.__getitem__ = MagicMock(side_effect=lambda key: {"id": fact_id}[key])

        mock_conn = AsyncMock(spec=asyncpg.Connection)
        mock_conn.fetchrow = AsyncMock(return_value=fact_row)
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")

        result = await retract_contact_info_fact(
            pool=AsyncMock(),
            subject=_ENTITY_ID,
            ci_type="email",
            ci_value=_EMAIL,
            conn=mock_conn,
        )

        assert result == fact_id
        # Verify the UPDATE set validity='retracted'.
        mock_conn.execute.assert_awaited_once()
        sql_called = mock_conn.execute.call_args[0][0]
        assert "retracted" in sql_called.lower()

    async def test_returns_none_when_no_active_row(self):
        """No active fact → returns None without executing an UPDATE."""
        import asyncpg

        from butlers.tools.relationship.relationship_assert_fact import retract_contact_info_fact

        mock_conn = AsyncMock(spec=asyncpg.Connection)
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_conn.execute = AsyncMock()

        result = await retract_contact_info_fact(
            pool=AsyncMock(),
            subject=_ENTITY_ID,
            ci_type="email",
            ci_value=_EMAIL,
            conn=mock_conn,
        )

        assert result is None
        mock_conn.execute.assert_not_awaited()

    async def test_uses_pool_when_conn_is_none(self):
        """When conn=None, the function acquires a connection from the pool."""
        import asyncpg

        from butlers.tools.relationship.relationship_assert_fact import retract_contact_info_fact

        fact_id = uuid4()
        fact_row = MagicMock()
        fact_row.__getitem__ = MagicMock(side_effect=lambda key: {"id": fact_id}[key])

        mock_conn = AsyncMock(spec=asyncpg.Connection)
        mock_conn.fetchrow = AsyncMock(return_value=fact_row)
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")

        mock_pool = AsyncMock(spec=asyncpg.Pool)
        mock_pool.acquire = MagicMock(return_value=_async_ctx(mock_conn))

        result = await retract_contact_info_fact(
            pool=mock_pool,
            subject=_ENTITY_ID,
            ci_type="email",
            ci_value=_EMAIL,
        )

        assert result == fact_id
        mock_pool.acquire.assert_called_once()

    async def test_predicate_mapping_email_to_has_email(self):
        """ci_type='email' must resolve to predicate 'has-email'."""
        import asyncpg

        from butlers.tools.relationship.relationship_assert_fact import retract_contact_info_fact

        mock_conn = AsyncMock(spec=asyncpg.Connection)
        mock_conn.fetchrow = AsyncMock(return_value=None)

        await retract_contact_info_fact(
            pool=AsyncMock(),
            subject=_ENTITY_ID,
            ci_type="email",
            ci_value=_EMAIL,
            conn=mock_conn,
        )

        fetchrow_sql = mock_conn.fetchrow.call_args[0][0]
        assert "has-email" in fetchrow_sql or "has-email" in str(mock_conn.fetchrow.call_args)
        # Check the predicate argument passed to fetchrow
        fetchrow_args = mock_conn.fetchrow.call_args[0]
        assert "has-email" in fetchrow_args

    async def test_predicate_mapping_phone_to_has_phone(self):
        """ci_type='phone' must resolve to predicate 'has-phone'."""
        import asyncpg

        from butlers.tools.relationship.relationship_assert_fact import retract_contact_info_fact

        mock_conn = AsyncMock(spec=asyncpg.Connection)
        mock_conn.fetchrow = AsyncMock(return_value=None)

        await retract_contact_info_fact(
            pool=AsyncMock(),
            subject=_ENTITY_ID,
            ci_type="phone",
            ci_value=_PHONE,
            conn=mock_conn,
        )

        fetchrow_args = mock_conn.fetchrow.call_args[0]
        assert "has-phone" in fetchrow_args


# ---------------------------------------------------------------------------
# Helper: async context manager for pool.acquire()
# ---------------------------------------------------------------------------


class _async_ctx:
    """Minimal async context manager that yields the given value."""

    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *_):
        pass
