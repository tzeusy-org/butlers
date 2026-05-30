"""Tests for GET /api/relationship/entities/{id}/linked-contacts (display-layer unification).

Covers the merged read introduced in bu-rf2dh: the endpoint now returns channels
from BOTH ``public.contact_info`` (legacy) AND ``relationship.entity_facts`` has-*
triples (new write path), de-duped by ``(type, value)``.

Acceptance criteria:
1. Entity with no linked contacts returns [].
2. Entity with contacts but no entity_facts returns contact_info rows unchanged
   (source=null, backward compatible).
3. Entity_facts-sourced channels are appended to first contact's contact_info
   with source="entity_facts".
4. De-dup: channel present in both stores appears once (contact_info wins,
   entity_facts duplicate silently dropped).
5. Multiple entity_facts channels (none in contact_info) all appear on first
   contact.
6. Unknown entity returns 404.
7. Entity with no entity_facts still returns contacts correctly.
8. De-dup across multiple contacts: entity_facts channel already on any contact
   is not duplicated.
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
_CI_ID = uuid4()
_LABEL_ID = uuid4()
_MISSING_ENT_ID = uuid4()

_EMAIL = "alice@example.com"
_PHONE = "+1-555-0100"

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
    email: str | None = _EMAIL,
    phone: str | None = None,
    preferred_channel: str | None = None,
) -> MagicMock:
    return _make_row(
        {
            "id": contact_id or _CONTACT_ID_A,
            "full_name": full_name,
            "email": email,
            "phone": phone,
            "preferred_channel": preferred_channel,
        }
    )


def _make_ci_row(
    *,
    id: UUID | None = None,
    contact_id: UUID | None = None,
    type: str = "email",
    value: str = _EMAIL,
    is_primary: bool = True,
    secured: bool = False,
    parent_id: UUID | None = None,
    context: str | None = None,
) -> MagicMock:
    return _make_row(
        {
            "id": id or _CI_ID,
            "contact_id": contact_id or _CONTACT_ID_A,
            "type": type,
            "value": value,
            "is_primary": is_primary,
            "secured": secured,
            "parent_id": parent_id,
            "context": context,
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


def _make_app(
    *,
    entity_exists: bool = True,
    contact_rows: list | None = None,
    ci_rows: list | None = None,
    label_rows: list | None = None,
    fact_rows: list | None = None,
) -> tuple[FastAPI, AsyncMock]:
    """Wire a FastAPI app with a mocked relationship DB pool.

    pool.fetch call sequence (4 total):
      1. contacts query (initial fetch, not in gather)
      2. contact_info query   ⎤
      3. label query          ⎬ asyncio.gather
      4. entity_facts query   ⎦

    pool.fetchval:
      - entity-exists check (returns 1 or None)
    """
    # entity_exists → fetchval returns 1; else None → 404
    mock_fetchval = AsyncMock(return_value=1 if entity_exists else None)

    # Build the side_effect sequence for pool.fetch:
    #   call 1 → contact_rows
    #   call 2 → ci_rows      (gather)
    #   call 3 → label_rows   (gather)
    #   call 4 → fact_rows    (gather)
    fetch_sequence = [
        contact_rows if contact_rows is not None else [],
        ci_rows if ci_rows is not None else [],
        label_rows if label_rows is not None else [],
        fact_rows if fact_rows is not None else [],
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


class TestLinkedContactsLegacyOnly:
    """Contacts with contact_info rows only (no entity_facts) — backward compatible."""

    async def test_contact_info_returned_with_source_null(self):
        """Legacy contact_info entries must NOT gain a source field (backward compat)."""
        contact = _make_contact_row()
        ci = _make_ci_row(type="email", value=_EMAIL)
        app, _ = _make_app(
            contact_rows=[contact],
            ci_rows=[ci],
            label_rows=[],
            fact_rows=[],
        )
        resp = await _get(app)

        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        contact_info = body[0]["contact_info"]
        assert len(contact_info) == 1
        # source must be absent or null — not "entity_facts"
        entry = contact_info[0]
        assert entry.get("source") is None
        assert entry["type"] == "email"
        assert entry["value"] == _EMAIL

    async def test_top_level_email_phone_preserved(self):
        """Top-level email/phone convenience fields are unchanged."""
        contact = _make_contact_row(email=_EMAIL, phone=_PHONE)
        app, _ = _make_app(
            contact_rows=[contact],
            ci_rows=[],
            label_rows=[],
            fact_rows=[],
        )
        resp = await _get(app)

        body = resp.json()
        assert body[0]["email"] == _EMAIL
        assert body[0]["phone"] == _PHONE

    async def test_labels_returned(self):
        """Label objects are returned for the contact."""
        contact = _make_contact_row()
        label = _make_label_row(contact_id=_CONTACT_ID_A, name="VIP", color="#ff0000")
        app, _ = _make_app(
            contact_rows=[contact],
            ci_rows=[],
            label_rows=[label],
            fact_rows=[],
        )
        resp = await _get(app)

        labels = resp.json()[0]["labels"]
        assert len(labels) == 1
        assert labels[0]["name"] == "VIP"

    async def test_no_entity_facts_does_not_change_contact_info_count(self):
        """Zero entity_facts → contact_info list has exactly the ci_rows count."""
        contact = _make_contact_row()
        ci_email = _make_ci_row(type="email", value=_EMAIL)
        ci_phone = _make_ci_row(id=uuid4(), type="phone", value=_PHONE)
        app, _ = _make_app(
            contact_rows=[contact],
            ci_rows=[ci_email, ci_phone],
            label_rows=[],
            fact_rows=[],
        )
        resp = await _get(app)

        contact_info = resp.json()[0]["contact_info"]
        assert len(contact_info) == 2


class TestLinkedContactsEntityFactsMerge:
    """Entity_facts has-* triples appear in contact_info with source="entity_facts"."""

    async def test_entity_facts_only_channel_appended_to_first_contact(self):
        """A has-phone triple not in contact_info is added with source=entity_facts."""
        contact = _make_contact_row(email=_EMAIL, phone=None)
        ci_email = _make_ci_row(type="email", value=_EMAIL)
        fact_phone = _make_fact_row(predicate="has-phone", object=_PHONE)
        app, _ = _make_app(
            contact_rows=[contact],
            ci_rows=[ci_email],
            label_rows=[],
            fact_rows=[fact_phone],
        )
        resp = await _get(app)

        contact_info = resp.json()[0]["contact_info"]
        # Should have 2 entries: legacy email + entity_facts phone
        assert len(contact_info) == 2

        ef_entry = next(e for e in contact_info if e["type"] == "phone")
        assert ef_entry["value"] == _PHONE
        assert ef_entry["source"] == "entity_facts"

    async def test_legacy_entry_has_no_source_field_when_mixed(self):
        """When both sources are present, legacy entries still have source=null."""
        contact = _make_contact_row()
        ci_email = _make_ci_row(type="email", value=_EMAIL)
        fact_phone = _make_fact_row(predicate="has-phone", object=_PHONE)
        app, _ = _make_app(
            contact_rows=[contact],
            ci_rows=[ci_email],
            label_rows=[],
            fact_rows=[fact_phone],
        )
        resp = await _get(app)

        contact_info = resp.json()[0]["contact_info"]
        legacy_entry = next(e for e in contact_info if e["type"] == "email")
        assert legacy_entry.get("source") is None

    async def test_multiple_entity_facts_channels_all_appended(self):
        """Multiple entity_facts triples (no overlap) are all added."""
        contact = _make_contact_row(email=None, phone=None)
        fact_email = _make_fact_row(id=_FACT_ID, predicate="has-email", object=_EMAIL)
        fact_phone = _make_fact_row(id=_FACT_ID_2, predicate="has-phone", object=_PHONE)
        app, _ = _make_app(
            contact_rows=[contact],
            ci_rows=[],
            label_rows=[],
            fact_rows=[fact_email, fact_phone],
        )
        resp = await _get(app)

        contact_info = resp.json()[0]["contact_info"]
        assert len(contact_info) == 2
        types_found = {e["type"] for e in contact_info}
        assert types_found == {"email", "phone"}
        for e in contact_info:
            assert e["source"] == "entity_facts"

    async def test_predicate_prefix_stripped_to_type(self):
        """'has-email' → type='email', 'has-phone' → type='phone', etc."""
        contact = _make_contact_row(email=None, phone=None)
        fact = _make_fact_row(predicate="has-handle", object="@alice")
        app, _ = _make_app(
            contact_rows=[contact],
            ci_rows=[],
            label_rows=[],
            fact_rows=[fact],
        )
        resp = await _get(app)

        contact_info = resp.json()[0]["contact_info"]
        assert len(contact_info) == 1
        assert contact_info[0]["type"] == "handle"
        assert contact_info[0]["value"] == "@alice"

    async def test_entity_facts_is_primary_propagated(self):
        """is_primary from entity_facts 'primary' column is mapped (not hardcoded False)."""
        contact = _make_contact_row(email=None, phone=None)
        fact_primary = _make_fact_row(predicate="has-email", object=_EMAIL, primary=True)
        fact_secondary = _make_fact_row(
            id=_FACT_ID_2, predicate="has-phone", object=_PHONE, primary=False
        )
        app, _ = _make_app(
            contact_rows=[contact],
            ci_rows=[],
            label_rows=[],
            fact_rows=[fact_primary, fact_secondary],
        )
        resp = await _get(app)

        contact_info = resp.json()[0]["contact_info"]
        assert len(contact_info) == 2
        email_entry = next(e for e in contact_info if e["type"] == "email")
        phone_entry = next(e for e in contact_info if e["type"] == "phone")
        assert email_entry["is_primary"] is True
        assert phone_entry["is_primary"] is False


class TestLinkedContactsDedup:
    """A channel present in both stores appears exactly once (contact_info wins)."""

    async def test_duplicate_email_appears_once(self):
        """Same email in contact_info AND entity_facts → only one entry."""
        contact = _make_contact_row(email=_EMAIL)
        ci_email = _make_ci_row(type="email", value=_EMAIL)
        fact_email = _make_fact_row(predicate="has-email", object=_EMAIL)
        app, _ = _make_app(
            contact_rows=[contact],
            ci_rows=[ci_email],
            label_rows=[],
            fact_rows=[fact_email],
        )
        resp = await _get(app)

        contact_info = resp.json()[0]["contact_info"]
        email_entries = [e for e in contact_info if e["type"] == "email"]
        assert len(email_entries) == 1
        # contact_info version wins — source should be null (not entity_facts)
        assert email_entries[0].get("source") is None

    async def test_duplicate_phone_appears_once(self):
        """Same phone in contact_info AND entity_facts → only one entry."""
        contact = _make_contact_row(phone=_PHONE)
        ci_phone = _make_ci_row(type="phone", value=_PHONE)
        fact_phone = _make_fact_row(predicate="has-phone", object=_PHONE)
        app, _ = _make_app(
            contact_rows=[contact],
            ci_rows=[ci_phone],
            label_rows=[],
            fact_rows=[fact_phone],
        )
        resp = await _get(app)

        contact_info = resp.json()[0]["contact_info"]
        phone_entries = [e for e in contact_info if e["type"] == "phone"]
        assert len(phone_entries) == 1
        assert phone_entries[0].get("source") is None

    async def test_different_value_same_type_not_deduped(self):
        """Different value for the same type → both kept (not de-dup candidates)."""
        new_email = "bob@example.com"
        contact = _make_contact_row(email=_EMAIL)
        ci_email = _make_ci_row(type="email", value=_EMAIL)
        fact_email = _make_fact_row(predicate="has-email", object=new_email)
        app, _ = _make_app(
            contact_rows=[contact],
            ci_rows=[ci_email],
            label_rows=[],
            fact_rows=[fact_email],
        )
        resp = await _get(app)

        contact_info = resp.json()[0]["contact_info"]
        email_entries = [e for e in contact_info if e["type"] == "email"]
        assert len(email_entries) == 2
        values = {e["value"] for e in email_entries}
        assert values == {_EMAIL, new_email}

    async def test_entity_facts_channel_on_second_contact_not_duplicated(self):
        """Entity_facts channel matching second contact's ci is not added to first contact."""
        # Two contacts: A has email, B has phone. entity_facts has phone (same as B).
        contact_a = _make_contact_row(contact_id=_CONTACT_ID_A, full_name="Alice", email=_EMAIL)
        contact_b = _make_contact_row(contact_id=_CONTACT_ID_B, full_name="Bob", phone=_PHONE)
        ci_email = _make_ci_row(id=uuid4(), contact_id=_CONTACT_ID_A, type="email", value=_EMAIL)
        ci_phone = _make_ci_row(id=uuid4(), contact_id=_CONTACT_ID_B, type="phone", value=_PHONE)
        # entity_facts has the phone that's already in ci_phone
        fact_phone = _make_fact_row(predicate="has-phone", object=_PHONE)
        app, _ = _make_app(
            contact_rows=[contact_a, contact_b],  # sorted by name → Alice first
            ci_rows=[ci_email, ci_phone],
            label_rows=[],
            fact_rows=[fact_phone],
        )
        resp = await _get(app)

        body = resp.json()
        # Alice's contact_info: only email (phone already in Bob's ci → de-duped out)
        alice = next(c for c in body if c["full_name"] == "Alice")
        alice_types = {e["type"] for e in alice["contact_info"]}
        assert "phone" not in alice_types

        # Bob's contact_info: phone from contact_info
        bob = next(c for c in body if c["full_name"] == "Bob")
        bob_phone_entries = [e for e in bob["contact_info"] if e["type"] == "phone"]
        assert len(bob_phone_entries) == 1
        assert bob_phone_entries[0].get("source") is None
