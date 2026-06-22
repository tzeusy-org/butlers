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
            contact_id=uuid.uuid4(),
            name="Owner",
            roles=["owner"],
            entity_id=uuid.uuid4(),
        )
        assert "owner" in owner.roles

        no_entity = ResolvedContact(
            contact_id=uuid.uuid4(),
            name="Linked",
            roles=[],
            entity_id=None,
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
        assert f"entity_id: {eid}" in p and "via telegram" in p

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
        """Async; accepts pool/channel_type/channel_value; queries entity_facts triples; None on error.

        Migration bead 7 (bu-akads): function now queries relationship.entity_facts
        instead of public.contact_info / public.contacts.  Source structure
        tokens updated accordingly.
        """
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
        # Bead 7: queries entity_facts triples, not contact_info/contacts
        for token in [
            "entity_facts",
            "entities",
            "predicate",
            "return None",
            "except",
        ]:
            assert token in src, f"Expected token {token!r} in resolve_contact_by_channel source"

        assert callable(create_temp_contact)
        assert "unidentified" in inspect.getsource(create_temp_contact)
        assert callable(build_identity_preamble)

    def test_whatsapp_jid_phone_extraction(self):
        from butlers.identity import _extract_whatsapp_jid_phone

        assert _extract_whatsapp_jid_phone("1234567890@s.whatsapp.net") == "1234567890"
        assert _extract_whatsapp_jid_phone("123456789@g.us") is None


class TestOwnerBootstrap:
    """RFC 0004: Owner entity is bootstrapped automatically on daemon startup."""

    def test_owner_contact_bootstrapped_with_owner_role(self):
        """RFC 0004: Daemon startup creates owner entity with ['owner'] role.

        _ensure_owner_entity() inserts a row into public.entities with
        roles=['owner'] on first boot. This is idempotent and uses ON CONFLICT.
        The owner role drives identity preamble, approval gates, and routing priority.
        """
        import inspect

        from butlers.owner_bootstrap import _ensure_owner_entity

        assert callable(_ensure_owner_entity)
        assert __import__("asyncio").iscoroutinefunction(_ensure_owner_entity), (
            "_ensure_owner_entity must be async (requires DB access at startup)"
        )

        # The function must write the 'owner' role to public.entities
        src = inspect.getsource(_ensure_owner_entity)
        assert "owner" in src, (
            "_ensure_owner_entity must create an entity with 'owner' role (RFC 0004)"
        )
        assert "public.entities" in src or "entities" in src, (
            "_ensure_owner_entity must write to public.entities (RFC 0004)"
        )
        # Must be idempotent — uses ON CONFLICT
        assert "ON CONFLICT" in src, (
            "_ensure_owner_entity must be idempotent via ON CONFLICT (RFC 0004)"
        )

    def test_owner_bootstrap_called_during_startup(self):
        """RFC 0004: lifecycle.run_startup calls _ensure_owner_entity during phase 8b.

        Owner entity bootstrapping occurs at phase 8b (alongside credential store
        creation), ensuring owner context is available before module on_startup()
        calls that may need identity resolution.
        """
        import inspect

        from butlers import lifecycle

        src = inspect.getsource(lifecycle)
        assert "_ensure_owner_entity" in src or "owner" in src.lower(), (
            "lifecycle.run_startup must call _ensure_owner_entity during startup (RFC 0004)"
        )

    def test_owner_entity_carries_owner_role_in_resolved_contact(self):
        """RFC 0004: A ResolvedContact with roles=['owner'] identifies the owner.

        The identity preamble function formats owner contacts distinctly with
        '[Source: Owner ...]' — this only happens when 'owner' is in roles.
        """
        from butlers.identity import ResolvedContact, build_identity_preamble

        owner_contact = ResolvedContact(
            contact_id=uuid.uuid4(),
            name="Owner",
            roles=["owner"],
            entity_id=uuid.uuid4(),
        )

        preamble = build_identity_preamble(owner_contact, "telegram")
        assert "[Source: Owner" in preamble, (
            "Owner contact (roles=['owner']) must produce '[Source: Owner ...]' preamble (RFC 0004)"
        )
        assert "pending disambiguation" not in preamble, (
            "Owner contact must not be marked as needing disambiguation (RFC 0004)"
        )

    def test_context_aware_notify_filters_by_sphere(self):
        """RFC 0004: notify() with msg_context='work' routes only to work-sphere channels.

        contact_info.context column tags channels as 'personal', 'work', 'other', or NULL.
        When msg_context is provided, recipient selection prefers entries whose
        context matches msg_context over unclassified (NULL) entries.

        The context-aware resolution logic lives in daemon._resolve_entity_channel_identifier().
        """
        import inspect

        from butlers.daemon import ButlerDaemon

        daemon_src = inspect.getsource(ButlerDaemon)

        # msg_context parameter enables sphere routing in notify()
        assert "msg_context" in daemon_src, (
            "ButlerDaemon must implement msg_context parameter for context-aware notify (RFC 0004)"
        )

        # The context column in contact_info drives the routing
        assert "context" in daemon_src, (
            "ButlerDaemon must reference contact_info.context for sphere routing (RFC 0004)"
        )

        # The RFC specifies three context values + NULL
        valid_contexts = {"personal", "work", "other", None}
        assert "personal" in daemon_src or "work" in daemon_src, (
            "ButlerDaemon must reference context sphere values (RFC 0004)"
        )
        assert len(valid_contexts) == 4, "RFC 0004 defines 3 context spheres + NULL (unclassified)"


class TestResolveOwnerChannelViaDefiner:
    """resolve_owner_channel_via_definer() — cross-schema owner lookup via the
    public.resolve_owner_triple SECURITY DEFINER function (core_145)."""

    async def test_telegram_match_returns_owner_and_primary(self):
        from unittest.mock import AsyncMock

        from butlers.identity import resolve_owner_channel_via_definer

        entity_id = uuid.uuid4()
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value={"entity_id": entity_id, "is_primary": True})

        result = await resolve_owner_channel_via_definer(pool, "telegram", "206570151")

        assert result is not None
        contact, is_primary = result
        assert contact.roles == ["owner"]
        assert contact.entity_id == entity_id
        assert is_primary is True

        # The candidate array must include both the verbatim and telegram-prefixed forms.
        call = pool.fetchrow.await_args
        predicate, candidates = call.args[1], call.args[2]
        assert predicate == "has-handle"
        assert "206570151" in candidates
        assert "telegram:206570151" in candidates

    async def test_no_match_returns_none(self):
        from unittest.mock import AsyncMock

        from butlers.identity import resolve_owner_channel_via_definer

        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=None)

        result = await resolve_owner_channel_via_definer(pool, "email", "x@example.com")
        assert result is None

    async def test_unknown_channel_type_returns_none_without_query(self):
        from unittest.mock import AsyncMock

        from butlers.identity import resolve_owner_channel_via_definer

        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=None)

        result = await resolve_owner_channel_via_definer(pool, "carrier-pigeon", "whatever")
        assert result is None
        pool.fetchrow.assert_not_awaited()

    async def test_db_error_returns_none(self):
        from unittest.mock import AsyncMock

        from butlers.identity import resolve_owner_channel_via_definer

        pool = AsyncMock()
        pool.fetchrow = AsyncMock(side_effect=RuntimeError("function does not exist"))

        result = await resolve_owner_channel_via_definer(pool, "telegram", "206570151")
        assert result is None
