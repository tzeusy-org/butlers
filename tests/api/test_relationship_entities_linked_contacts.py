"""Tests for GET /api/relationship/entities/{id}/linked-contacts.

After bu-6ioq3, channel identifiers come exclusively from
``relationship.entity_facts`` — ``public.contact_info`` reads are removed.

Acceptance criteria (post-migration):
1. Entity with no linked contacts returns [].
2. Entity with contacts but no entity_facts returns contacts with empty
   contact_info and null email/phone.
3. Entity_facts has-* triples appear in contact_info with source="entity_facts".
4. Multiple entity_facts channels all appear on the first contact (entity-level).
5. Unknown entity returns 404.
6. has-handle with "telegram:" prefix → type="telegram_user_id", value=numeric part.
7. has-handle without prefix → type="handle", value unchanged.
8. email/phone quick-display fields come from entity_facts (not a separate query).
9. Labels are returned correctly.
10. Multiple linked contacts: facts attached to first contact; subsequent contacts
    have empty contact_info unless they have their own entity_facts (entity-level,
    all on same entity_id).
11. is_primary propagated from entity_facts "primary" column.
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

_ENT_ID = uuid4()
_CONTACT_ID_A = uuid4()
_CONTACT_ID_B = uuid4()
_FACT_ID = uuid4()
_FACT_ID_2 = uuid4()
_LABEL_ID = uuid4()
_MISSING_ENT_ID = uuid4()
_ENTITY_INFO_ID = uuid4()
_ENTITY_INFO_ID_2 = uuid4()

_EMAIL = "alice@example.com"
_PHONE = "+1-555-0100"
_TELEGRAM_NUMERIC = "210454304"
_TELEGRAM_OBJECT = f"telegram:{_TELEGRAM_NUMERIC}"

_LINKED_PATH = f"/api/relationship/entities/{_ENT_ID}/linked-contacts"
_MISSING_PATH = f"/api/relationship/entities/{_MISSING_ENT_ID}/linked-contacts"


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------


def _make_row(data: dict) -> MagicMock:
    """Build a MagicMock that behaves like an asyncpg Record."""
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    row.get = MagicMock(side_effect=lambda key, default=None: data.get(key, default))
    return row


def _make_contact_row(
    *,
    contact_id: UUID | None = None,
    full_name: str = "Alice",
) -> MagicMock:
    # The contacts query no longer selects preferred_channel — it now comes from
    # the entity-keyed prefers-channel fact (see _make_app fetchval sequence).
    return _make_row(
        {
            "id": contact_id or _CONTACT_ID_A,
            "full_name": full_name,
        }
    )


def _make_label_row(
    *,
    contact_id: UUID | None = None,
    label_id: UUID | None = None,
    name: str = "VIP",
    color: str | None = "#ff0000",
) -> MagicMock:
    return _make_row(
        {
            "contact_id": contact_id or _CONTACT_ID_A,
            "id": label_id or _LABEL_ID,
            "name": name,
            "color": color,
        }
    )


def _make_fact_row(
    *,
    id: UUID | None = None,
    predicate: str = "has-email",
    object: str = _EMAIL,
    primary: bool | None = None,
) -> MagicMock:
    return _make_row(
        {
            "id": id or _FACT_ID,
            "predicate": predicate,
            "object": object,
            "primary": primary,
        }
    )


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def _make_entity_info_row(
    *,
    id: UUID | None = None,
    type: str = "telegram_api_key",
    is_primary: bool = False,
    secured: bool = True,
) -> MagicMock:
    """Build a mock entity_info row (secured credential, value NOT included)."""
    return _make_row(
        {
            "id": id or _ENTITY_INFO_ID,
            "type": type,
            "is_primary": is_primary,
            "secured": secured,
        }
    )


def _make_app(
    *,
    entity_exists: bool = True,
    contact_rows: list | None = None,
    label_rows: list | None = None,
    fact_rows: list | None = None,
    entity_info_rows: list | None = None,
    preferred_channel: str | None = None,
) -> tuple[FastAPI, AsyncMock]:
    """Wire a FastAPI app with a mocked relationship DB pool.

    pool.fetch call sequence (4 total after bu-gfzin: secured entity_info surface):
      1. contacts query (initial fetch, not in gather)
      2. label query          ⎤ asyncio.gather
      3. entity_facts query   ⎥
      4. entity_info query    ⎦

    pool.fetchval call sequence:
      1. entity-exists check (returns 1 or None)
      2. active prefers-channel fact object (entity-keyed-preferred-channel) —
         sourced inside the same asyncio.gather; returns the channel string or
         None. Replaces the old public.contacts.preferred_channel column read.
    """
    # fetchval call 1 → entity-exists (1 or None → 404); call 2 → preferred channel.
    mock_fetchval = AsyncMock(side_effect=[1 if entity_exists else None, preferred_channel])

    # Build the side_effect sequence for pool.fetch:
    #   call 1 → contact_rows
    #   call 2 → label_rows         (gather)
    #   call 3 → fact_rows          (gather)
    #   call 4 → entity_info_rows   (gather)
    fetch_sequence = [
        contact_rows if contact_rows is not None else [],
        label_rows if label_rows is not None else [],
        fact_rows if fact_rows is not None else [],
        entity_info_rows if entity_info_rows is not None else [],
    ]
    mock_fetch = AsyncMock(side_effect=fetch_sequence)

    mock_pool = AsyncMock()
    mock_pool.fetchval = mock_fetchval
    mock_pool.fetch = mock_fetch

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break

    return app, mock_pool


async def _get(app: FastAPI, path: str = _LINKED_PATH) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(path)


# ===========================================================================
# Tests
# ===========================================================================


class TestLinkedContactsEmpty:
    """Entity with no linked contacts returns []."""

    async def test_no_contacts_returns_empty_list(self):
        app, _ = _make_app(contact_rows=[])
        resp = await _get(app)

        assert resp.status_code == 200
        assert resp.json() == []

    async def test_unknown_entity_returns_404(self):
        app, _ = _make_app(entity_exists=False, contact_rows=[])
        resp = await _get(app, path=_MISSING_PATH)

        assert resp.status_code == 404


class TestLinkedContactsNoFacts:
    """Contacts with no entity_facts return empty contact_info."""

    async def test_no_entity_facts_returns_empty_contact_info(self):
        """When entity_facts is empty, contact_info list is empty and email/phone are None."""
        contact = _make_contact_row()
        app, _ = _make_app(contact_rows=[contact], label_rows=[], fact_rows=[])
        resp = await _get(app)

        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["contact_info"] == []
        assert body[0]["email"] is None
        assert body[0]["phone"] is None

    async def test_labels_returned_without_entity_facts(self):
        """Labels are returned even when entity_facts is empty."""
        contact = _make_contact_row()
        label = _make_label_row(contact_id=_CONTACT_ID_A, name="VIP", color="#ff0000")
        app, _ = _make_app(contact_rows=[contact], label_rows=[label], fact_rows=[])
        resp = await _get(app)

        labels = resp.json()[0]["labels"]
        assert len(labels) == 1
        assert labels[0]["name"] == "VIP"


class TestLinkedContactsEntityFacts:
    """Entity_facts has-* triples appear in contact_info with source="entity_facts"."""

    @pytest.mark.parametrize(
        "predicate,expected_type,value",
        [
            ("has-email", "email", _EMAIL),
            ("has-phone", "phone", _PHONE),
        ],
    )
    async def test_has_channel_triple_appears_in_contact_info(
        self, predicate, expected_type, value
    ):
        """A has-* triple → typed contact_info entry with source='entity_facts', unsecured."""
        contact = _make_contact_row()
        fact = _make_fact_row(predicate=predicate, object=value)
        app, _ = _make_app(contact_rows=[contact], label_rows=[], fact_rows=[fact])
        resp = await _get(app)

        assert resp.status_code == 200
        contact_info = resp.json()[0]["contact_info"]
        assert len(contact_info) == 1
        assert contact_info[0]["type"] == expected_type
        assert contact_info[0]["value"] == value
        assert contact_info[0]["source"] == "entity_facts"
        assert contact_info[0]["secured"] is False

    async def test_multiple_entity_facts_channels_all_appear(self):
        """Multiple entity_facts triples → all appear in contact_info."""
        contact = _make_contact_row()
        fact_email = _make_fact_row(id=_FACT_ID, predicate="has-email", object=_EMAIL)
        fact_phone = _make_fact_row(id=_FACT_ID_2, predicate="has-phone", object=_PHONE)
        app, _ = _make_app(
            contact_rows=[contact], label_rows=[], fact_rows=[fact_email, fact_phone]
        )
        resp = await _get(app)

        contact_info = resp.json()[0]["contact_info"]
        assert len(contact_info) == 2
        types_found = {e["type"] for e in contact_info}
        assert types_found == {"email", "phone"}
        for e in contact_info:
            assert e["source"] == "entity_facts"

    async def test_is_primary_propagated_from_facts(self):
        """is_primary from entity_facts 'primary' column is mapped correctly."""
        contact = _make_contact_row()
        fact_primary = _make_fact_row(predicate="has-email", object=_EMAIL, primary=True)
        fact_secondary = _make_fact_row(
            id=_FACT_ID_2, predicate="has-phone", object=_PHONE, primary=False
        )
        app, _ = _make_app(
            contact_rows=[contact], label_rows=[], fact_rows=[fact_primary, fact_secondary]
        )
        resp = await _get(app)

        contact_info = resp.json()[0]["contact_info"]
        email_entry = next(e for e in contact_info if e["type"] == "email")
        phone_entry = next(e for e in contact_info if e["type"] == "phone")
        assert email_entry["is_primary"] is True
        assert phone_entry["is_primary"] is False

    async def test_top_level_email_phone_from_entity_facts(self):
        """Top-level email/phone are derived from entity_facts (primary-first)."""
        contact = _make_contact_row()
        fact_email = _make_fact_row(predicate="has-email", object=_EMAIL, primary=True)
        fact_phone = _make_fact_row(id=_FACT_ID_2, predicate="has-phone", object=_PHONE)
        app, _ = _make_app(
            contact_rows=[contact], label_rows=[], fact_rows=[fact_email, fact_phone]
        )
        resp = await _get(app)

        body = resp.json()[0]
        assert body["email"] == _EMAIL
        assert body["phone"] == _PHONE


class TestTelegramDisambiguation:
    """Telegram has-handle entries are correctly typed and stripped of prefix."""

    async def test_telegram_handle_prefix_stripped_to_numeric(self):
        """has-handle with 'telegram:' prefix → type='telegram_user_id', numeric value."""
        contact = _make_contact_row()
        fact = _make_fact_row(predicate="has-handle", object=_TELEGRAM_OBJECT)
        app, _ = _make_app(contact_rows=[contact], label_rows=[], fact_rows=[fact])
        resp = await _get(app)

        contact_info = resp.json()[0]["contact_info"]
        assert len(contact_info) == 1
        assert contact_info[0]["type"] == "telegram_user_id"
        assert contact_info[0]["value"] == _TELEGRAM_NUMERIC

    async def test_bare_handle_returns_handle_type(self):
        """has-handle without 'telegram:' prefix → type='handle', value unchanged."""
        contact = _make_contact_row()
        fact = _make_fact_row(predicate="has-handle", object="kohjingyu")
        app, _ = _make_app(contact_rows=[contact], label_rows=[], fact_rows=[fact])
        resp = await _get(app)

        contact_info = resp.json()[0]["contact_info"]
        assert len(contact_info) == 1
        assert contact_info[0]["type"] == "handle"
        assert contact_info[0]["value"] == "kohjingyu"

    async def test_telegram_not_returned_for_bare_handle(self):
        """Bare has-handle (linkedin/twitter) is NOT typed as telegram_user_id."""
        contact = _make_contact_row()
        fact = _make_fact_row(predicate="has-handle", object="linkedin.com/in/alice")
        app, _ = _make_app(contact_rows=[contact], label_rows=[], fact_rows=[fact])
        resp = await _get(app)

        contact_info = resp.json()[0]["contact_info"]
        assert contact_info[0]["type"] != "telegram_user_id"
        assert contact_info[0]["type"] == "handle"


class TestLinkedContactsMultiContact:
    """Entity facts are attached to the first linked contact (entity-level)."""

    async def test_facts_on_first_contact_only(self):
        """When two contacts linked, facts go to first contact; second contact is empty."""
        contact_a = _make_contact_row(contact_id=_CONTACT_ID_A, full_name="Alice")
        contact_b = _make_contact_row(contact_id=_CONTACT_ID_B, full_name="Bob")
        fact = _make_fact_row(predicate="has-email", object=_EMAIL)
        app, _ = _make_app(
            contact_rows=[contact_a, contact_b],  # Alice first (sorted by name)
            label_rows=[],
            fact_rows=[fact],
        )
        resp = await _get(app)

        body = resp.json()
        alice = next(c for c in body if c["full_name"] == "Alice")
        bob = next(c for c in body if c["full_name"] == "Bob")
        assert len(alice["contact_info"]) == 1
        assert alice["email"] == _EMAIL
        assert len(bob["contact_info"]) == 0
        assert bob["email"] is None

    async def test_two_contacts_no_facts_both_empty(self):
        """Two contacts, no entity_facts → both have empty contact_info."""
        contact_a = _make_contact_row(contact_id=_CONTACT_ID_A, full_name="Alice")
        contact_b = _make_contact_row(contact_id=_CONTACT_ID_B, full_name="Bob")
        app, _ = _make_app(contact_rows=[contact_a, contact_b], label_rows=[], fact_rows=[])
        resp = await _get(app)

        body = resp.json()
        assert len(body) == 2
        for c in body:
            assert c["contact_info"] == []
            assert c["email"] is None
            assert c["phone"] is None


class TestLinkedContactsSecuredEntityInfo:
    """Secured entity_info rows are surfaced in linked-contacts WITHOUT value.

    These tests verify:
    - secured=true entity_info rows appear in contact_info with value=None.
    - The 'secured' flag is set to True.
    - source="entity_facts" (routes frontend reveal to entity-keyed endpoint).
    - predicate and value_hash are None (no inline edit/delete affordance).
    - Non-secured behavior (entity_facts facts) is unchanged.
    - The secret VALUE is never present in the response payload.
    - Secured rows are attached to the first linked contact (entity-level).
    """

    async def test_secured_entity_info_surfaced_with_masked_value(self):
        """Secured entity_info row appears in contact_info; value is None (masked)."""
        contact = _make_contact_row()
        ei_row = _make_entity_info_row(id=_ENTITY_INFO_ID, type="telegram_api_key")
        app, _ = _make_app(
            contact_rows=[contact],
            label_rows=[],
            fact_rows=[],
            entity_info_rows=[ei_row],
        )
        resp = await _get(app)

        assert resp.status_code == 200
        contact_info = resp.json()[0]["contact_info"]
        assert len(contact_info) == 1
        entry = contact_info[0]
        assert entry["type"] == "telegram_api_key"
        assert entry["value"] is None  # SECURITY: value must never be present
        assert entry["secured"] is True
        assert entry["source"] == "entity_facts"  # routes to entity-keyed reveal
        assert entry["predicate"] is None  # no triple predicate for entity_info rows
        assert entry["value_hash"] is None  # no value_hash → no inline edit/delete

    async def test_entity_info_sql_does_not_select_value(self):
        """The entity_info SQL in list_entity_linked_contacts must not include 'value'.

        This test verifies that the actual pool.fetch call for entity_info uses a
        query that selects only id, type, is_primary, secured — never the value column.
        """
        contact = _make_contact_row()
        ei_row = _make_entity_info_row(id=_ENTITY_INFO_ID, type="telegram_api_key")
        app, mock_pool = _make_app(
            contact_rows=[contact],
            label_rows=[],
            fact_rows=[],
            entity_info_rows=[ei_row],
        )
        await _get(app)

        # The 4th fetch call (index 3) is the entity_info query.
        assert mock_pool.fetch.call_count >= 4
        entity_info_call = mock_pool.fetch.call_args_list[3]
        sql = entity_info_call.args[0]
        assert "value" not in sql.lower(), (
            f"entity_info SQL must not select value column; got: {sql}"
        )

    async def test_secured_and_nonsecured_entries_coexist(self):
        """entity_facts entries and secured entity_info entries both appear together."""
        contact = _make_contact_row()
        fact = _make_fact_row(predicate="has-email", object=_EMAIL)
        ei_row = _make_entity_info_row(id=_ENTITY_INFO_ID, type="telegram_api_key")
        app, _ = _make_app(
            contact_rows=[contact],
            label_rows=[],
            fact_rows=[fact],
            entity_info_rows=[ei_row],
        )
        resp = await _get(app)

        assert resp.status_code == 200
        contact_info = resp.json()[0]["contact_info"]
        assert len(contact_info) == 2

        email_entry = next(e for e in contact_info if e["type"] == "email")
        secured_entry = next(e for e in contact_info if e["type"] == "telegram_api_key")

        assert email_entry["value"] == _EMAIL
        assert email_entry["secured"] is False

        assert secured_entry["value"] is None
        assert secured_entry["secured"] is True
        assert secured_entry["source"] == "entity_facts"

    async def test_multiple_secured_rows_all_surfaced(self):
        """Multiple secured entity_info rows all appear in contact_info."""
        contact = _make_contact_row()
        ei_row1 = _make_entity_info_row(id=_ENTITY_INFO_ID, type="telegram_api_key")
        ei_row2 = _make_entity_info_row(id=_ENTITY_INFO_ID_2, type="home_assistant_token")
        app, _ = _make_app(
            contact_rows=[contact],
            label_rows=[],
            fact_rows=[],
            entity_info_rows=[ei_row1, ei_row2],
        )
        resp = await _get(app)

        contact_info = resp.json()[0]["contact_info"]
        assert len(contact_info) == 2
        types_found = {e["type"] for e in contact_info}
        assert types_found == {"telegram_api_key", "home_assistant_token"}
        for entry in contact_info:
            assert entry["value"] is None
            assert entry["secured"] is True
            assert entry["source"] == "entity_facts"

    async def test_secured_rows_attached_to_first_contact_only(self):
        """With two linked contacts, secured entity_info rows go to the first contact only."""
        contact_a = _make_contact_row(contact_id=_CONTACT_ID_A, full_name="Alice")
        contact_b = _make_contact_row(contact_id=_CONTACT_ID_B, full_name="Bob")
        ei_row = _make_entity_info_row(id=_ENTITY_INFO_ID, type="telegram_api_key")
        app, _ = _make_app(
            contact_rows=[contact_a, contact_b],
            label_rows=[],
            fact_rows=[],
            entity_info_rows=[ei_row],
        )
        resp = await _get(app)

        body = resp.json()
        alice = next(c for c in body if c["full_name"] == "Alice")
        bob = next(c for c in body if c["full_name"] == "Bob")
        assert len(alice["contact_info"]) == 1
        assert alice["contact_info"][0]["secured"] is True
        assert len(bob["contact_info"]) == 0


class TestLinkedContactsPreferredChannel:
    """preferred_channel + reachable_channels are entity-keyed (prefers-channel fact).

    entity-keyed-preferred-channel (group 3): preferred_channel is sourced from
    the active ``prefers-channel`` fact (a fetchval inside the gather), NOT the
    orphaned public.contacts.preferred_channel column. reachable_channels is the
    deliverable set the entity has a contact fact for (email/telegram).
    """

    async def test_preferred_channel_from_fact(self):
        """An active prefers-channel fact populates preferred_channel."""
        contact = _make_contact_row()
        fact = _make_fact_row(predicate="has-email", object=_EMAIL)
        app, _ = _make_app(
            contact_rows=[contact],
            fact_rows=[fact],
            preferred_channel="email",
        )
        resp = await _get(app)

        assert resp.status_code == 200
        assert resp.json()[0]["preferred_channel"] == "email"

    async def test_no_preference_yields_null(self):
        """No active prefers-channel fact → preferred_channel is None."""
        contact = _make_contact_row()
        fact = _make_fact_row(predicate="has-email", object=_EMAIL)
        app, _ = _make_app(
            contact_rows=[contact],
            fact_rows=[fact],
            preferred_channel=None,
        )
        resp = await _get(app)

        assert resp.json()[0]["preferred_channel"] is None

    async def test_reachable_channels_email_only(self):
        """has-email alone → reachable_channels == ['email'] (no telegram)."""
        contact = _make_contact_row()
        fact = _make_fact_row(predicate="has-email", object=_EMAIL)
        app, _ = _make_app(contact_rows=[contact], fact_rows=[fact])
        resp = await _get(app)

        assert resp.json()[0]["reachable_channels"] == ["email"]

    async def test_reachable_channels_telegram_requires_prefix(self):
        """A telegram-prefixed has-handle → telegram reachable; email present too."""
        contact = _make_contact_row()
        fact_email = _make_fact_row(id=_FACT_ID, predicate="has-email", object=_EMAIL)
        fact_tg = _make_fact_row(id=_FACT_ID_2, predicate="has-handle", object=_TELEGRAM_OBJECT)
        app, _ = _make_app(contact_rows=[contact], fact_rows=[fact_email, fact_tg])
        resp = await _get(app)

        assert resp.json()[0]["reachable_channels"] == ["email", "telegram"]

    async def test_bare_handle_does_not_make_telegram_reachable(self):
        """A non-telegram-prefixed has-handle does NOT add telegram to the set."""
        contact = _make_contact_row()
        fact = _make_fact_row(predicate="has-handle", object="linkedin-handle")
        app, _ = _make_app(contact_rows=[contact], fact_rows=[fact])
        resp = await _get(app)

        assert resp.json()[0]["reachable_channels"] == []

    async def test_entity_level_fields_only_on_first_contact(self):
        """preferred_channel + reachable_channels attach to the first contact only."""
        contact_a = _make_contact_row(contact_id=_CONTACT_ID_A, full_name="Alice")
        contact_b = _make_contact_row(contact_id=_CONTACT_ID_B, full_name="Bob")
        fact = _make_fact_row(predicate="has-email", object=_EMAIL)
        app, _ = _make_app(
            contact_rows=[contact_a, contact_b],
            fact_rows=[fact],
            preferred_channel="email",
        )
        resp = await _get(app)

        body = resp.json()
        alice = next(c for c in body if c["full_name"] == "Alice")
        bob = next(c for c in body if c["full_name"] == "Bob")
        assert alice["preferred_channel"] == "email"
        assert alice["reachable_channels"] == ["email"]
        assert bob["preferred_channel"] is None
        assert bob["reachable_channels"] == []
