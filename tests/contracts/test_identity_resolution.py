"""Contract tests: Identity Resolution (RFC 0004, Invariant 11).

Validates ResolvedContact dataclass, identity preamble format,
resolve_contact_by_channel, and WhatsApp JID handling.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.contract


class TestResolvedContact:
    """RFC 0004: ResolvedContact is the canonical identity resolution result."""

    def test_resolved_contact_structure_and_semantics(self):
        """Frozen dataclass with required fields; entity_id can be None; roles from entity."""
        import dataclasses
        import inspect

        from butlers.identity import ResolvedContact

        assert dataclasses.is_dataclass(ResolvedContact)
        assert ResolvedContact.__dataclass_params__.frozen

        fields = set(inspect.signature(ResolvedContact.__init__).parameters.keys()) - {"self"}
        assert {"contact_id", "name", "roles", "entity_id"}.issubset(fields)

        owner = ResolvedContact(
            contact_id=uuid.uuid4(), name="Owner", roles=["owner"], entity_id=uuid.uuid4(),
        )
        assert "owner" in owner.roles

        no_entity = ResolvedContact(
            contact_id=uuid.uuid4(), name="Linked", roles=[], entity_id=None,
        )
        assert no_entity.entity_id is None


class TestIdentityPreambleFormat:
    """RFC 0004: Identity preamble format is structured and predictable."""

    def test_preamble_formats(self):
        """Owner, known contact, and unknown sender preambles are bracket-enclosed with channel."""
        from butlers.identity import ResolvedContact, build_identity_preamble

        cid, eid = uuid.uuid4(), uuid.uuid4()
        owner = ResolvedContact(contact_id=cid, name="Owner", roles=["owner"], entity_id=eid)
        p = build_identity_preamble(owner, "telegram")
        assert p.startswith("[Source: Owner") and p.endswith("]")
        assert f"contact_id: {cid}" in p and f"entity_id: {eid}" in p and "via telegram" in p

        # Known contact
        contact = ResolvedContact(contact_id=cid, name="Chloe", roles=[], entity_id=eid)
        pc = build_identity_preamble(contact, "email")
        assert "Chloe" in pc and "pending disambiguation" not in pc

        # Unknown sender
        tcid, teid = uuid.uuid4(), uuid.uuid4()
        pu = build_identity_preamble(None, "telegram", temp_contact_id=tcid, temp_entity_id=teid)
        assert "Unknown sender" in pu and "pending disambiguation" in pu

        # Minimal unknown sender
        pm = build_identity_preamble(None, "discord")
        assert "Unknown sender" in pm and "via discord" in pm

        # Owner without entity_id
        owner_no_eid = ResolvedContact(
            contact_id=cid, name="Owner", roles=["owner"], entity_id=None
        )
        pne = build_identity_preamble(owner_no_eid, "telegram")
        assert "entity_id" not in pne

        # Channel via keyword across multiple channels
        for ch in ["telegram", "email", "discord", "whatsapp"]:
            c = ResolvedContact(contact_id=uuid.uuid4(), name="T", roles=[], entity_id=None)
            assert f"via {ch}" in build_identity_preamble(c, ch)


class TestResolveContactFunction:
    """RFC 0004: resolve_contact_by_channel() is the single resolution entry point."""

    def test_function_signature_and_source_structure(self):
        """Async, accepts pool/channel_type/channel_value, uses 3-table JOIN, returns None on error."""
        import asyncio
        import inspect

        from butlers.identity import (
            build_identity_preamble,
            create_temp_contact,
            resolve_contact_by_channel,
        )

        assert asyncio.iscoroutinefunction(resolve_contact_by_channel)
        params = list(inspect.signature(resolve_contact_by_channel).parameters.keys())
        assert "pool" in params and "channel_type" in params and "channel_value" in params

        src = inspect.getsource(resolve_contact_by_channel)
        for token in ["contact_info", "contacts", "entities", "type", "value",
                       "return None", "except"]:
            assert token in src

        assert callable(create_temp_contact)
        assert "unidentified" in inspect.getsource(create_temp_contact)
        assert callable(build_identity_preamble)

    def test_whatsapp_jid_phone_extraction(self):
        from butlers.identity import _extract_whatsapp_jid_phone

        assert _extract_whatsapp_jid_phone("1234567890@s.whatsapp.net") == "1234567890"
        assert _extract_whatsapp_jid_phone("123456789@g.us") is None
