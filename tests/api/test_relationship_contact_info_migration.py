"""Tests for bu-6ioq3: public.contact_info reads migrated to relationship.entity_facts.

Covers the migrated read sites in roster/relationship/api/router.py:

  - GET /contacts (list_contacts) — batch email/phone via entity_facts
  - GET /contacts/pending (list_pending_contacts) — contact_info list via entity_facts
  - GET /contacts/{id} (get_contact) — email, phone, contact_info via entity_facts
  - GET /contacts/{id}/secrets/{info_id} (reveal_contact_secret) — entity_facts lookup
  - GET /contacts/unlinked (list_unlinked_contacts) — unlinked contacts → null email/phone
  - Telegram disambiguation in has-handle predicates

All tests are unit-level (mock pool — no Postgres or Docker required).
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager

_NOW = datetime.now(UTC)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONTACT_ID = uuid4()
_ENTITY_ID = uuid4()
_FACT_ID = uuid4()
_FACT_ID_2 = uuid4()

_EMAIL = "alice@example.com"
_PHONE = "+1-555-0100"
_TELEGRAM_NUMERIC = "210454304"
_TELEGRAM_OBJECT = f"telegram:{_TELEGRAM_NUMERIC}"

_CONTACT_PATH = f"/api/relationship/contacts/{_CONTACT_ID}"
_SECRETS_PATH = f"/api/relationship/contacts/{_CONTACT_ID}/secrets/{_FACT_ID}"
_CONTACTS_PATH = "/api/relationship/contacts"
_PENDING_PATH = "/api/relationship/contacts/pending"
_UNLINKED_PATH = "/api/relationship/contacts/unlinked"


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------


def _make_row(data: dict) -> MagicMock:
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    row.get = MagicMock(side_effect=lambda key, default=None: data.get(key, default))
    return row


def _make_contact_row_detail(
    *,
    entity_id=None,
    metadata=None,
    roles=None,
) -> MagicMock:
    """Simulate a row returned by the get_contact main SELECT."""
    return _make_row(
        {
            "id": _CONTACT_ID,
            "full_name": "Alice",
            "first_name": "Alice",
            "last_name": None,
            "nickname": None,
            "notes": None,
            "company": None,
            "job_title": None,
            "metadata": metadata or {},
            "created_at": _NOW,
            "updated_at": _NOW,
            "roles": roles or [],
            "entity_id": entity_id,
            "preferred_channel": None,
            "last_interaction_at": None,
        }
    )


def _make_pending_contact_row(*, entity_id=None) -> MagicMock:
    return _make_row(
        {
            "id": _CONTACT_ID,
            "full_name": "Alice",
            "first_name": "Alice",
            "last_name": None,
            "nickname": None,
            "notes": None,
            "company": None,
            "job_title": None,
            "metadata": {"needs_disambiguation": True},
            "created_at": None,
            "updated_at": None,
            "roles": [],
            "entity_id": entity_id,
        }
    )


def _make_ef_row(*, id=None, predicate: str, object_val: str, primary=None) -> MagicMock:
    return _make_row(
        {
            "id": id or _FACT_ID,
            "predicate": predicate,
            "object": object_val,
            "primary": primary,
            "entity_id": _ENTITY_ID,
        }
    )


def _make_entity_map_row(*, contact_id=None, entity_id=None) -> MagicMock:
    return _make_row(
        {
            "id": contact_id or _CONTACT_ID,
            "entity_id": entity_id,
        }
    )


def _make_contact_list_row(*, contact_id=None) -> MagicMock:
    return _make_row(
        {
            "id": contact_id or _CONTACT_ID,
            "full_name": "Alice",
            "first_name": "Alice",
            "last_name": None,
            "nickname": None,
        }
    )


# ---------------------------------------------------------------------------
# App factories
# ---------------------------------------------------------------------------


def _make_app_for_get_contact(
    *,
    contact_row=None,
    ef_rows: list | None = None,
) -> tuple[FastAPI, AsyncMock]:
    """Wire app for GET /contacts/{id}.

    pool.fetchrow call 1 → contact_row (main SELECT)

    asyncio.gather fires 4 coroutines:
      pool.fetch (labels)   → []
      pool.fetchrow (birthday) → None
      pool.fetchrow (address) → None
      _entity_facts_channels_by_entity (entity_facts) → uses pool.fetch

    Since _entity_facts_channels_by_entity does one pool.fetch, the mock
    fetch side_effect sees:
      call 1 → ef_rows (from the entity_facts helper called in gather)
      call 2 → []      (labels)

    Actually, asyncio.gather schedules all coroutines together.  The exact
    fetch order depends on which coroutine polls first.  We set fetch to always
    return ef_rows so the helper gets them regardless of order.
    """
    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(
        side_effect=[contact_row, None, None]  # main row, birthday, address
    )
    # asyncio.gather fires label fetch and entity_facts fetch concurrently.
    # With AsyncMock, gather executes them in the order they appear (labels first,
    # then entity_facts helper's internal fetch).
    mock_pool.fetch = AsyncMock(
        side_effect=[
            [],  # labels (first pool.fetch in gather)
            ef_rows if ef_rows is not None else [],  # entity_facts helper
        ]
    )

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break

    return app, mock_pool


def _make_app_for_reveal_secret(
    *,
    contact_row=None,
    ef_row=None,
) -> tuple[FastAPI, AsyncMock]:
    """Wire app for GET /contacts/{id}/secrets/{info_id}."""
    mock_pool = AsyncMock()
    # fetchrow call 1 → contact_row (contact entity_id lookup)
    # fetchrow call 2 → ef_row (entity_facts lookup by id)
    mock_pool.fetchrow = AsyncMock(side_effect=[contact_row, ef_row])

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
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(path)


# ===========================================================================
# Tests: GET /contacts/{contact_id} (get_contact)
# ===========================================================================


class TestGetContactEntityFacts:
    """GET /contacts/{id} derives channel data from relationship.entity_facts."""

    async def test_email_from_has_email_fact(self):
        """has-email fact → email field populated."""
        contact_row = _make_contact_row_detail(entity_id=_ENTITY_ID)
        ef_rows = [_make_ef_row(predicate="has-email", object_val=_EMAIL, primary=True)]
        app, _ = _make_app_for_get_contact(contact_row=contact_row, ef_rows=ef_rows)
        resp = await _get(app, _CONTACT_PATH)

        assert resp.status_code == 200
        body = resp.json()
        assert body["email"] == _EMAIL

    async def test_phone_from_has_phone_fact(self):
        """has-phone fact → phone field populated."""
        contact_row = _make_contact_row_detail(entity_id=_ENTITY_ID)
        ef_rows = [_make_ef_row(predicate="has-phone", object_val=_PHONE)]
        app, _ = _make_app_for_get_contact(contact_row=contact_row, ef_rows=ef_rows)
        resp = await _get(app, _CONTACT_PATH)

        assert resp.status_code == 200
        assert resp.json()["phone"] == _PHONE

    async def test_no_entity_id_returns_null_email_phone(self):
        """Contact with no linked entity → email and phone are None."""
        contact_row = _make_contact_row_detail(entity_id=None)
        app, _ = _make_app_for_get_contact(contact_row=contact_row, ef_rows=[])
        resp = await _get(app, _CONTACT_PATH)

        assert resp.status_code == 200
        body = resp.json()
        assert body["email"] is None
        assert body["phone"] is None
        assert body["contact_info"] == []

    async def test_contact_info_list_from_entity_facts(self):
        """contact_info list is synthesised from entity_facts with source='entity_facts'."""
        contact_row = _make_contact_row_detail(entity_id=_ENTITY_ID)
        ef_rows = [
            _make_ef_row(id=_FACT_ID, predicate="has-email", object_val=_EMAIL, primary=True),
            _make_ef_row(id=_FACT_ID_2, predicate="has-phone", object_val=_PHONE, primary=False),
        ]
        app, _ = _make_app_for_get_contact(contact_row=contact_row, ef_rows=ef_rows)
        resp = await _get(app, _CONTACT_PATH)

        assert resp.status_code == 200
        contact_info = resp.json()["contact_info"]
        assert len(contact_info) == 2
        for ci in contact_info:
            assert ci["source"] == "entity_facts"
            assert ci["secured"] is False

    async def test_telegram_handle_stripped_in_contact_info(self):
        """has-handle with 'telegram:' prefix → type=telegram_user_id, numeric value."""
        contact_row = _make_contact_row_detail(entity_id=_ENTITY_ID)
        ef_rows = [_make_ef_row(predicate="has-handle", object_val=_TELEGRAM_OBJECT)]
        app, _ = _make_app_for_get_contact(contact_row=contact_row, ef_rows=ef_rows)
        resp = await _get(app, _CONTACT_PATH)

        assert resp.status_code == 200
        contact_info = resp.json()["contact_info"]
        assert len(contact_info) == 1
        assert contact_info[0]["type"] == "telegram_user_id"
        assert contact_info[0]["value"] == _TELEGRAM_NUMERIC

    async def test_bare_handle_not_typed_as_telegram(self):
        """has-handle without 'telegram:' prefix → type='handle', NOT telegram_user_id."""
        contact_row = _make_contact_row_detail(entity_id=_ENTITY_ID)
        ef_rows = [_make_ef_row(predicate="has-handle", object_val="linkedin.com/in/alice")]
        app, _ = _make_app_for_get_contact(contact_row=contact_row, ef_rows=ef_rows)
        resp = await _get(app, _CONTACT_PATH)

        contact_info = resp.json()["contact_info"]
        assert contact_info[0]["type"] == "handle"
        assert contact_info[0]["type"] != "telegram_user_id"

    async def test_contact_not_found_returns_404(self):
        """Unknown contact → 404."""
        app, _ = _make_app_for_get_contact(contact_row=None, ef_rows=[])
        resp = await _get(app, _CONTACT_PATH)
        assert resp.status_code == 404


# ===========================================================================
# Tests: GET /contacts/{id}/secrets/{info_id} (reveal_contact_secret)
# ===========================================================================


class TestRevealContactSecret:
    """GET /contacts/{id}/secrets/{info_id} after bu-6ioq3 migration."""

    async def test_contact_not_found_returns_404(self):
        """No contact row → 404 (contact_id lookup fails)."""
        app, _ = _make_app_for_reveal_secret(contact_row=None, ef_row=None)
        resp = await _get(app, _SECRETS_PATH)
        assert resp.status_code == 404

    async def test_entity_id_null_returns_404(self):
        """Contact has no linked entity → fact cannot be found → 404."""
        contact_row = _make_row({"entity_id": None})
        app, _ = _make_app_for_reveal_secret(contact_row=contact_row, ef_row=None)
        resp = await _get(app, _SECRETS_PATH)
        assert resp.status_code == 404

    async def test_fact_not_found_returns_404(self):
        """entity_facts row not found for given info_id → 404."""
        contact_row = _make_row({"entity_id": _ENTITY_ID})
        app, _ = _make_app_for_reveal_secret(contact_row=contact_row, ef_row=None)
        resp = await _get(app, _SECRETS_PATH)
        assert resp.status_code == 404

    async def test_fact_found_returns_400_not_secured(self):
        """entity_facts has no secured concept — always returns 400 (not secured)."""
        contact_row = _make_row({"entity_id": _ENTITY_ID})
        ef_row = _make_row({"id": _FACT_ID, "predicate": "has-email", "object": _EMAIL})
        app, _ = _make_app_for_reveal_secret(contact_row=contact_row, ef_row=ef_row)
        resp = await _get(app, _SECRETS_PATH)
        # entity_facts entries are not secured; endpoint returns 400 per contract
        assert resp.status_code == 400
        assert "not secured" in resp.json().get("detail", "").lower()


# ===========================================================================
# Tests: GET /contacts/unlinked (list_unlinked_contacts)
# ===========================================================================


class TestListUnlinkedContactsEmailPhone:
    """Unlinked contacts have entity_id=NULL → email/phone are null after migration."""

    async def test_email_and_phone_are_null_for_unlinked(self):
        """GET /contacts/unlinked returns null email/phone (no entity_facts for unlinked)."""
        # Simulates the full fetch sequence for list_unlinked_contacts:
        #   count query: fetchval → 1
        #   data query:  fetch → [contact row with email=None, phone=None]
        #   memory pool check: fetchval for entities table → None (no memory pool)
        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(side_effect=[1, None])  # count=1, no memory pool
        mock_pool.fetch = AsyncMock(
            return_value=[
                _make_row(
                    {
                        "id": _CONTACT_ID,
                        "full_name": "Alice",
                        "first_name": "Alice",
                        "last_name": None,
                        "company": None,
                        "email": None,
                        "phone": None,
                    }
                )
            ]
        )

        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.return_value = mock_pool
        mock_db.butler_names = []  # no memory pool

        app = create_app()
        for butler_name, router_module in app.state.butler_routers:
            if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
                app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
                break

        resp = await _get(app, _UNLINKED_PATH)

        assert resp.status_code == 200
        body = resp.json()
        assert "contacts" in body
        if body["contacts"]:
            assert body["contacts"][0]["email"] is None
            assert body["contacts"][0]["phone"] is None


# ===========================================================================
# Tests: _ef_predicate_to_ci_type and _ef_object_to_display_value helpers
# ===========================================================================


def _get_router_module():
    """Return the loaded relationship router module via the app's butler registry.

    This avoids a direct ``from roster.relationship.api.router import ...`` which
    would fail under pytest's importlib mode.  The module is already loaded during
    app startup and stored in ``app.state.butler_routers``.
    """
    from butlers.api.app import create_app  # noqa: PLC0415

    app = create_app()
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "relationship":
            return router_module
    raise RuntimeError("relationship router module not found in app.state.butler_routers")


class TestEntityFactsHelpers:
    """Unit tests for the ef helper functions added in bu-6ioq3."""

    def test_ef_predicate_to_ci_type_email(self):
        m = _get_router_module()
        assert m._ef_predicate_to_ci_type("has-email", "alice@example.com") == "email"

    def test_ef_predicate_to_ci_type_phone(self):
        m = _get_router_module()
        assert m._ef_predicate_to_ci_type("has-phone", "+1-555-0100") == "phone"

    def test_ef_predicate_to_ci_type_website(self):
        m = _get_router_module()
        assert m._ef_predicate_to_ci_type("has-website", "https://example.com") == "website"

    def test_ef_predicate_to_ci_type_telegram_handle(self):
        m = _get_router_module()
        assert m._ef_predicate_to_ci_type("has-handle", "telegram:210454304") == "telegram_user_id"

    def test_ef_predicate_to_ci_type_bare_handle(self):
        m = _get_router_module()
        # linkedin, twitter, other handles without telegram prefix → "handle"
        assert m._ef_predicate_to_ci_type("has-handle", "kohjingyu") == "handle"
        assert m._ef_predicate_to_ci_type("has-handle", "linkedin.com/in/alice") == "handle"
        assert m._ef_predicate_to_ci_type("has-handle", "@alice_twitter") == "handle"

    def test_ef_object_to_display_value_telegram_strips_prefix(self):
        m = _get_router_module()
        assert m._ef_object_to_display_value("has-handle", "telegram:210454304") == "210454304"

    def test_ef_object_to_display_value_passthrough(self):
        m = _get_router_module()
        assert (
            m._ef_object_to_display_value("has-email", "alice@example.com") == "alice@example.com"
        )
        assert m._ef_object_to_display_value("has-handle", "kohjingyu") == "kohjingyu"
        assert m._ef_object_to_display_value("has-phone", "+1-555-0100") == "+1-555-0100"

    def test_telegram_prefix_exact_boundary(self):
        """'telegram:' prefix must match exactly — 'telegram' without colon is bare handle."""
        m = _get_router_module()
        assert m._ef_predicate_to_ci_type("has-handle", "telegram") == "handle"
        assert m._ef_predicate_to_ci_type("has-handle", "telegram:") == "telegram_user_id"
