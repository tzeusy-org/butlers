"""Unit tests for the channel tool surface (renamed from contact_info, bead bu-158ep).

Covers:
  (a) The app-level write-block guard raises ContactInfoWriteBlockedError.
  (b) channel_add asserts a channel triple via relationship_assert_fact()
      and never issues a direct INSERT/UPDATE/DELETE to public.contact_info.
  (c) channel_add maps types to predicates and honours the owner carve-out
      (pending_approval) surfaced by the central writer.
  (d) channel_list reads from relationship.entity_facts (reads remain allowed).
  (e) The revoke migration upgrade()/downgrade() are symmetric (REVOKE ↔ GRANT)
      and cover every runtime role + connector_writer for contact_info only.

All tests are pure unit tests (no Docker/Postgres). The asyncpg pool and the
central writer are mocked via unittest.mock.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

_ADD_PATCH_TARGET = "butlers.tools.relationship.channel.relationship_assert_fact"
_CONTACT_ID = uuid.uuid4()
_ENTITY_ID = uuid.uuid4()


class _AsyncCM:
    """Minimal async context manager helper for mocking pool.acquire()."""

    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *args):
        return False


# ===========================================================================
# (a) App-level write-block guard
# ===========================================================================


class TestWriteBlockGuard:
    def test_guard_raises(self):
        from butlers.contact_info_write_guard import (
            ContactInfoWriteBlockedError,
            assert_contact_info_writes_blocked,
        )

        with pytest.raises(ContactInfoWriteBlockedError):
            assert_contact_info_writes_blocked("insert")

    def test_error_is_runtime_error_subclass(self):
        from butlers.contact_info_write_guard import ContactInfoWriteBlockedError

        assert issubclass(ContactInfoWriteBlockedError, RuntimeError)


# ===========================================================================
# (b, c) channel_add asserts a triple via the central writer
# ===========================================================================


def _result(outcome: str, *, fact_id=None, action_id=None):
    """Build a stand-in AssertResult-like object for the central writer mock."""
    r = MagicMock()
    r.outcome.value = outcome
    r.fact_id = fact_id
    r.action_id = action_id
    return r


def _pool_with_entity(entity_id=_ENTITY_ID):
    pool = MagicMock()
    # resolve_contact_entity_id now queries contacts_source_links.local_entity_id
    # (contacts-schema retirement, bu-ozpyl) rather than public.contacts.entity_id.
    pool.fetchrow = AsyncMock(return_value={"local_entity_id": entity_id})
    # Wire execute/acquire so any accidental SQL DML would be observable.
    pool.execute = AsyncMock(return_value=None)
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=None)
    conn.fetchrow = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=_AsyncCM(None))
    pool.acquire = MagicMock(return_value=_AsyncCM(conn))
    return pool, conn


class TestContactInfoAddUsesCentralWriter:
    async def test_add_routes_through_writer_and_never_direct_dml(self):
        """channel_add maps email→has-email and asserts via relationship_assert_fact,
        issuing NO direct INSERT/UPDATE to public.contact_info (the write-block invariant)."""
        from butlers.tools.relationship.channel import channel_add

        pool, conn = _pool_with_entity()
        with patch(_ADD_PATCH_TARGET, new_callable=AsyncMock) as writer:
            writer.return_value = _result("inserted", fact_id=uuid.uuid4())
            await channel_add(pool, _CONTACT_ID, "email", "alice@example.com")

        writer.assert_awaited_once()
        call = writer.call_args
        assert call.args[1] == _ENTITY_ID  # subject = entity_id
        assert call.args[2] == "has-email"  # predicate
        assert call.args[3] == "alice@example.com"  # object
        # No direct write DML anywhere: neither pool.execute nor conn.execute called.
        pool.execute.assert_not_called()
        conn.execute.assert_not_called()

    async def test_owner_carveout_surfaces_pending_approval(self):
        from butlers.tools.relationship.channel import channel_add

        pool, _ = _pool_with_entity()
        action_id = uuid.uuid4()
        with patch(_ADD_PATCH_TARGET, new_callable=AsyncMock) as writer:
            writer.return_value = _result("pending_approval", action_id=action_id)
            result = await channel_add(pool, _CONTACT_ID, "email", "owner@example.com")

        assert result["status"] == "pending_approval"
        assert result["action_id"] == str(action_id)

    async def test_telegram_maps_to_has_handle_with_prefix(self):
        """channel_add('telegram', ...) must store 'telegram:<value>' in entity_facts.

        The 'telegram:' prefix disambiguates telegram entries from linkedin/twitter/other
        has-handle entries on the read side (bu-wni4z encoding fix).
        """
        from butlers.tools.relationship.channel import channel_add

        pool, _ = _pool_with_entity()
        with patch(_ADD_PATCH_TARGET, new_callable=AsyncMock) as writer:
            writer.return_value = _result("inserted", fact_id=uuid.uuid4())
            result = await channel_add(pool, _CONTACT_ID, "telegram", "210454304")

        writer.assert_awaited_once()
        call = writer.call_args
        assert call.args[2] == "has-handle"  # predicate
        assert call.args[3] == "telegram:210454304"  # object: prefixed (bu-wni4z)
        # The response value shows the user-supplied input (without prefix)
        assert result["value"] == "210454304"

    async def test_telegram_prefix_idempotent_in_add(self):
        """channel_add must not double-prefix if value already has 'telegram:'."""
        from butlers.tools.relationship.channel import channel_add

        pool, _ = _pool_with_entity()
        with patch(_ADD_PATCH_TARGET, new_callable=AsyncMock) as writer:
            writer.return_value = _result("inserted", fact_id=uuid.uuid4())
            await channel_add(pool, _CONTACT_ID, "telegram", "telegram:210454304")

        call = writer.call_args
        assert call.args[3] == "telegram:210454304"  # not 'telegram:telegram:210454304'

    async def test_unmapped_type_rejected(self):
        from butlers.tools.relationship.channel import channel_add

        pool, _ = _pool_with_entity()
        # 'address' is a valid input check would reject; use a type that passes
        # the type-set check but has no predicate is impossible here, so assert
        # the invalid-type path raises ValueError instead.
        with pytest.raises(ValueError):
            await channel_add(pool, _CONTACT_ID, "fax", "555")

    async def test_missing_entity_raises(self):
        from butlers.tools.relationship.channel import channel_add

        pool = MagicMock()
        # Simulate a contacts_source_links row with NULL local_entity_id — data
        # integrity issue that resolve_contact_entity_id should raise on.
        pool.fetchrow = AsyncMock(return_value={"local_entity_id": None})
        with patch(_ADD_PATCH_TARGET, new_callable=AsyncMock) as writer:
            with pytest.raises(ValueError):
                await channel_add(pool, _CONTACT_ID, "email", "a@b.com")
            writer.assert_not_called()


# ===========================================================================
# (d) reads still work
# ===========================================================================


class TestContactInfoReadsAllowed:
    async def test_list_reads_contact_info(self):
        from butlers.tools.relationship.channel import channel_list

        fact_id = uuid.uuid4()
        pool = MagicMock()
        # fetchrow is used by resolve_contact_entity_id (SELECT local_entity_id
        # FROM contacts_source_links — contacts-schema retirement, bu-ozpyl).
        pool.fetchrow = AsyncMock(return_value={"local_entity_id": _ENTITY_ID})
        # fetch is used by entity_facts_channels_by_entity (SELECT from relationship.entity_facts)
        pool.fetch = AsyncMock(
            return_value=[
                {
                    "entity_id": _ENTITY_ID,
                    "id": fact_id,
                    "predicate": "has-email",
                    "object": "a@b.com",
                    "primary": True,
                }
            ]
        )
        rows = await channel_list(pool, _CONTACT_ID)
        assert len(rows) == 1
        assert rows[0]["type"] == "email"
        assert rows[0]["value"] == "a@b.com"
        assert rows[0]["source"] == "entity_facts"


# ===========================================================================
# (f) revoke migration is reversible and well-scoped
# ===========================================================================


class TestRevokeMigration:
    def _load_migration(self):
        import importlib.util
        from pathlib import Path

        path = (
            Path(__file__).resolve().parents[2]
            / "alembic"
            / "versions"
            / "core"
            / "core_110_contact_info_write_block.py"
        )
        spec = importlib.util.spec_from_file_location("core_110_test", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_revision_chain(self):
        mod = self._load_migration()
        assert mod.revision == "core_110"
        assert mod.down_revision == "core_109"

    def test_targets_contact_info_only_not_contacts(self):
        mod = self._load_migration()
        # The table FQN constant targets contact_info — NOT public.contacts.
        assert mod._TABLE_FQN == "public.contact_info"

    def test_role_coverage_matches_core_065(self):
        mod = self._load_migration()
        # Every butler runtime role + connector_writer is covered.
        assert "connector_writer" in mod._ALL_ROLES
        assert "butler_relationship_rw" in mod._ALL_ROLES
        # 10 butler schemas + connector_writer.
        assert len(mod._ALL_ROLES) == 11

    def test_upgrade_revokes_and_downgrade_grants(self):
        """upgrade() emits REVOKE statements; downgrade() emits matching GRANTs."""
        mod = self._load_migration()
        emitted: list[str] = []

        def _capture(stmt, *, role_name=None):  # noqa: ARG001
            emitted.append(stmt)

        with patch.object(mod, "_execute_best_effort", side_effect=_capture):
            mod.upgrade()
        assert emitted, "upgrade emitted no statements"
        assert all("REVOKE INSERT, UPDATE, DELETE" in s for s in emitted)
        assert all("public.contact_info" in s for s in emitted)
        # SELECT is never revoked (reads stay allowed).
        assert not any("SELECT" in s for s in emitted)

        emitted.clear()
        with patch.object(mod, "_execute_best_effort", side_effect=_capture):
            mod.downgrade()
        assert emitted, "downgrade emitted no statements"
        assert all("GRANT INSERT, UPDATE, DELETE" in s for s in emitted)
        assert all("public.contact_info" in s for s in emitted)
