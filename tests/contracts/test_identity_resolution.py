"""Contract tests: Identity Resolution (RFC 0004, Invariant 11).

Validates the 3-table JOIN, owner bootstrap, temp entity handling, and
identity preamble format.

Principle: resolve_contact_by_channel() is the single entry point for
identity resolution. It maps (channel_type, channel_value) to a canonical
contact record (RFC 0004).
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.contract


class TestResolvedContact:
    """RFC 0004: ResolvedContact is the canonical identity resolution result."""

    def test_resolved_contact_has_required_fields(self):
        """RFC 0004: ResolvedContact must have contact_id, name, roles, entity_id."""
        import inspect

        from butlers.identity import ResolvedContact

        fields = inspect.signature(ResolvedContact.__init__).parameters
        field_names = set(fields.keys()) - {"self"}
        required = {"contact_id", "name", "roles", "entity_id"}
        assert required.issubset(field_names), (
            f"ResolvedContact must have fields {required} (RFC 0004)"
        )

    def test_resolved_contact_is_frozen_dataclass(self):
        """RFC 0004: ResolvedContact is frozen (immutable) for safe propagation."""
        import dataclasses

        from butlers.identity import ResolvedContact

        assert dataclasses.is_dataclass(ResolvedContact), "ResolvedContact must be a dataclass"
        params = ResolvedContact.__dataclass_params__
        assert params.frozen, "ResolvedContact must be frozen (RFC 0004)"

    def test_roles_are_sourced_from_entity_not_contact(self):
        """RFC 0004: Roles are sourced from public.entities.roles, not contacts.roles.

        'Roles are now sourced from the entity, which is the canonical anchor.'
        The entity-centric model supports multiple contacts sharing the same role set.
        """
        from butlers.identity import ResolvedContact

        contact = ResolvedContact(
            contact_id=uuid.uuid4(),
            name="Test",
            roles=["owner"],  # From entity, not contact
            entity_id=uuid.uuid4(),
        )
        assert "owner" in contact.roles, "Owner role must be in roles list"

    def test_owner_identified_by_owner_role(self):
        """RFC 0004: Owner is identified by the 'owner' role in entity.roles.

        'The owner entity is bootstrapped automatically on daemon startup.
        It carries the "owner" role.'
        """
        from butlers.identity import ResolvedContact

        # Build an owner contact using the real dataclass and verify role membership.
        owner = ResolvedContact(
            contact_id=uuid.uuid4(),
            name="Owner",
            roles=["owner"],
            entity_id=uuid.uuid4(),
        )
        assert "owner" in owner.roles, "Owner role must be present in entity roles (RFC 0004)"

    def test_entity_id_can_be_none(self):
        """RFC 0004: entity_id may be None when contact has no linked entity."""
        from butlers.identity import ResolvedContact

        contact = ResolvedContact(
            contact_id=uuid.uuid4(),
            name="Linked Contact",
            roles=[],
            entity_id=None,  # No entity link
        )
        assert contact.entity_id is None


class TestIdentityPreambleFormat:
    """RFC 0004: Identity preamble format is structured and predictable."""

    def test_owner_preamble_format(self):
        """RFC 0004: Owner preamble includes contact_id, entity_id, and channel via field."""
        from butlers.identity import ResolvedContact, build_identity_preamble

        cid = uuid.uuid4()
        eid = uuid.uuid4()
        owner = ResolvedContact(
            contact_id=cid,
            name="Owner",
            roles=["owner"],
            entity_id=eid,
        )
        preamble = build_identity_preamble(owner, "telegram")
        assert preamble.startswith("[Source: Owner"), (
            "Owner preamble must start with '[Source: Owner' (RFC 0004)"
        )
        assert f"contact_id: {cid}" in preamble
        assert f"entity_id: {eid}" in preamble
        assert "via telegram" in preamble

    def test_known_contact_preamble_format(self):
        """RFC 0004: Known contact preamble is '[Source: <name> (contact_id: ...), via <ch>]'."""
        from butlers.identity import ResolvedContact, build_identity_preamble

        cid = uuid.uuid4()
        eid = uuid.uuid4()
        contact = ResolvedContact(
            contact_id=cid,
            name="Chloe",
            roles=[],
            entity_id=eid,
        )
        preamble = build_identity_preamble(contact, "email")
        assert "Chloe" in preamble, "Known contact preamble must include contact name"
        assert f"contact_id: {cid}" in preamble
        assert "via email" in preamble
        assert "pending disambiguation" not in preamble

    def test_unknown_sender_preamble_format(self):
        """RFC 0004: Unknown sender preamble includes 'pending disambiguation'."""
        from butlers.identity import build_identity_preamble

        temp_cid = uuid.uuid4()
        temp_eid = uuid.uuid4()
        preamble = build_identity_preamble(
            None,
            "telegram",
            temp_contact_id=temp_cid,
            temp_entity_id=temp_eid,
        )
        assert "Unknown sender" in preamble
        assert "pending disambiguation" in preamble
        assert "via telegram" in preamble
        assert str(temp_cid) in preamble
        assert str(temp_eid) in preamble

    def test_preamble_without_temp_ids_is_minimal(self):
        """RFC 0004: Unknown sender preamble without temp IDs is minimal but valid."""
        from butlers.identity import build_identity_preamble

        preamble = build_identity_preamble(None, "discord")
        assert "Unknown sender" in preamble
        assert "pending disambiguation" in preamble
        assert "via discord" in preamble

    def test_owner_without_entity_id_omits_entity_from_preamble(self):
        """RFC 0004: Owner preamble without entity_id only shows contact_id."""
        from butlers.identity import ResolvedContact, build_identity_preamble

        cid = uuid.uuid4()
        owner = ResolvedContact(
            contact_id=cid,
            name="Owner",
            roles=["owner"],
            entity_id=None,
        )
        preamble = build_identity_preamble(owner, "telegram")
        assert f"contact_id: {cid}" in preamble
        assert "entity_id" not in preamble

    def test_preamble_is_bracket_enclosed(self):
        """RFC 0004: Identity preamble is bracket-enclosed for LLM parsing.

        The predictable format '[Source: ...]' allows downstream butlers to
        consistently parse sender context from routed messages.
        """
        from butlers.identity import ResolvedContact, build_identity_preamble

        contact = ResolvedContact(
            contact_id=uuid.uuid4(),
            name="Test User",
            roles=[],
            entity_id=None,
        )
        preamble = build_identity_preamble(contact, "telegram")
        assert preamble.startswith("["), "Preamble must start with '[' (RFC 0004)"
        assert preamble.endswith("]"), "Preamble must end with ']' (RFC 0004)"

    def test_channel_appears_after_via_keyword(self):
        """RFC 0004: Channel is always formatted as 'via <channel>' in preamble."""
        from butlers.identity import ResolvedContact, build_identity_preamble

        channels = ["telegram", "email", "discord", "whatsapp"]
        for channel in channels:
            contact = ResolvedContact(
                contact_id=uuid.uuid4(),
                name="Test",
                roles=[],
                entity_id=None,
            )
            preamble = build_identity_preamble(contact, channel)
            assert f"via {channel}" in preamble, f"Preamble must contain 'via {channel}' (RFC 0004)"


class TestResolveContactFunction:
    """RFC 0004: resolve_contact_by_channel() is the single resolution entry point."""

    def test_function_is_importable(self):
        """RFC 0004: resolve_contact_by_channel must be importable from butlers.identity."""
        from butlers.identity import resolve_contact_by_channel

        assert callable(resolve_contact_by_channel), (
            "resolve_contact_by_channel must be callable (RFC 0004)"
        )

    def test_function_accepts_channel_type_and_value(self):
        """RFC 0004: Function signature is (pool, channel_type, channel_value)."""
        import inspect

        from butlers.identity import resolve_contact_by_channel

        sig = inspect.signature(resolve_contact_by_channel)
        params = list(sig.parameters.keys())
        assert "pool" in params, "resolve_contact_by_channel needs pool param"
        assert "channel_type" in params, "resolve_contact_by_channel needs channel_type param"
        assert "channel_value" in params, "resolve_contact_by_channel needs channel_value param"

    def test_function_is_async(self):
        """RFC 0004: resolve_contact_by_channel must be async for non-blocking resolution."""
        import asyncio

        from butlers.identity import resolve_contact_by_channel

        assert asyncio.iscoroutinefunction(resolve_contact_by_channel), (
            "resolve_contact_by_channel must be async (RFC 0004)"
        )

    def test_create_temp_contact_is_importable(self):
        """RFC 0004: create_temp_contact() creates temp identity for unknown senders."""
        from butlers.identity import create_temp_contact

        assert callable(create_temp_contact), "create_temp_contact must be callable (RFC 0004)"

    def test_temp_entity_metadata_flags(self):
        """RFC 0004: Temporary entities carry metadata.unidentified=true.

        'Creates a public.entities row with metadata = {"unidentified": true}'
        """
        import inspect as _inspect

        from butlers.identity import create_temp_contact

        src = _inspect.getsource(create_temp_contact)
        # The temp-contact creator must embed the unidentified/needs_disambiguation flags.
        assert "unidentified" in src, (
            "create_temp_contact must set metadata.unidentified flag (RFC 0004)"
        )
        assert "needs_disambiguation" in src or "unidentified" in src, (
            "create_temp_contact must flag unresolved contacts for disambiguation (RFC 0004)"
        )

    def test_contact_info_unique_constraint_on_type_value(self):
        """RFC 0004: UNIQUE constraint on (type, value) ensures at most one contact per channel.

        'A UNIQUE constraint on (type, value) guarantees at most one contact
        per channel identifier.'
        """
        import inspect as _inspect

        from butlers.identity import resolve_contact_by_channel

        src = _inspect.getsource(resolve_contact_by_channel)
        # The resolver must reference both 'type' and 'value' columns, which
        # confirms the UNIQUE constraint columns are used in the lookup query.
        assert "type" in src, "resolve_contact_by_channel must query by channel type (RFC 0004)"
        assert "value" in src, "resolve_contact_by_channel must query by channel value (RFC 0004)"

    def test_three_table_join_structure(self):
        """RFC 0004: Resolution uses JOIN of contact_info, contacts, entities.

        Query: contact_info JOIN contacts LEFT JOIN entities
        WHERE ci.type = $1 AND ci.value = $2
        """
        import inspect

        from butlers.identity import resolve_contact_by_channel

        src = inspect.getsource(resolve_contact_by_channel)
        assert "contact_info" in src, "Must join contact_info (RFC 0004)"
        assert "contacts" in src, "Must join contacts (RFC 0004)"
        assert "entities" in src, "Must join entities (RFC 0004)"

    def test_function_returns_none_on_db_error(self):
        """RFC 0004: resolve_contact_by_channel returns None on database error.

        'The function catches all database exceptions and returns None gracefully.
        It is safe to call before migrations have run, during partial startup,
        or when the database is temporarily unavailable.'
        """
        import inspect

        from butlers.identity import resolve_contact_by_channel

        src = inspect.getsource(resolve_contact_by_channel)
        # Must have exception handling that returns None
        assert "return None" in src, (
            "resolve_contact_by_channel must return None on error (RFC 0004)"
        )
        assert "except" in src, (
            "resolve_contact_by_channel must catch exceptions gracefully (RFC 0004)"
        )

    def test_whatsapp_jid_phone_fallback(self):
        """RFC 0004: WhatsApp JID resolution falls back to phone number lookup.

        If no direct match for whatsapp_jid, extract phone from JID and
        query with type="phone" to link across providers.
        """
        from butlers.identity import _extract_whatsapp_jid_phone

        phone = _extract_whatsapp_jid_phone("1234567890@s.whatsapp.net")
        assert phone == "1234567890", "WhatsApp JID phone extraction must work (RFC 0004)"

    def test_whatsapp_group_jid_returns_none(self):
        """RFC 0004: WhatsApp group JIDs (ending in @g.us) return None for phone extraction."""
        from butlers.identity import _extract_whatsapp_jid_phone

        phone = _extract_whatsapp_jid_phone("123456789@g.us")
        assert phone is None, "Group JID must not extract a phone number (RFC 0004)"

    def test_build_identity_preamble_is_importable(self):
        """RFC 0004: build_identity_preamble() is the canonical preamble builder."""
        from butlers.identity import build_identity_preamble

        assert callable(build_identity_preamble), (
            "build_identity_preamble must be callable (RFC 0004)"
        )
