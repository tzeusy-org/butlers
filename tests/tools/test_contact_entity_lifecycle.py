"""Unit tests for contact-entity lifecycle bridge.

Tests cover:
- contact_create: entity_create called and entity_id stored
- contact_create: entity_create failure is fail-open
- contact_create: no memory_pool -> no entity call
- contact_update: name change syncs entity
- contact_update: NULL entity_id handled gracefully
- contact_update: no memory_pool -> no entity call
- contact_merge: entity_merge called when both have entity_ids
- contact_merge: one or both NULL entity_ids handled gracefully
- contact_merge: entity_merge failure is fail-open
- entity_merge: facts re-pointed, aliases merged, source tombstoned
- entity_merge: same ID raises ValueError
- entity_merge: source not found raises ValueError
- entity_merge: target not found raises ValueError
"""

from __future__ import annotations

import importlib.util
import json
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Load modules from disk
# ---------------------------------------------------------------------------

_CONTACTS_PATH = Path(__file__).parent.parent.parent / "roster/relationship/tools/contacts.py"
_ENTITIES_PATH = (
    Path(__file__).parent.parent.parent / "src/butlers/modules/memory/tools/entities.py"
)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Patch dependencies before loading contacts module
_schema_mod = MagicMock()
_schema_mod.table_columns = AsyncMock()
_feed_mod = MagicMock()
_feed_mod._log_activity = AsyncMock()

sys.modules["butlers.tools.relationship._schema"] = _schema_mod
sys.modules["butlers.tools.relationship.feed"] = _feed_mod

_contacts_mod = _load_module("contacts_test_mod", _CONTACTS_PATH)
_entities_mod = _load_module("entities_test_mod", _ENTITIES_PATH)

contact_create = _contacts_mod.contact_create
contact_update = _contacts_mod.contact_update
contact_merge = _contacts_mod.contact_merge
_build_canonical_name = _contacts_mod._build_canonical_name
_build_entity_aliases = _contacts_mod._build_entity_aliases

entity_merge = _entities_mod.entity_merge

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONTACT_UUID = uuid.UUID("11111111-2222-3333-4444-555555555555")
CONTACT_UUID2 = uuid.UUID("aaaa1111-bbbb-cccc-dddd-eeeeeeeeeeee")
ENTITY_UUID = uuid.UUID("eeee1111-2222-3333-4444-555555555555")
ENTITY_UUID2 = uuid.UUID("eeee2222-3333-4444-5555-666666666666")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FULL_COLS = frozenset(
    {
        "id",
        "first_name",
        "last_name",
        "nickname",
        "name",
        "company",
        "job_title",
        "details",
        "metadata",
        "entity_id",
        "archived_at",
        "listed",
        "updated_at",
    }
)


def _make_contact_row(
    contact_id: uuid.UUID = CONTACT_UUID,
    first_name: str = "Alice",
    last_name: str = "Smith",
    nickname: str | None = "Ali",
    entity_id: uuid.UUID | None = None,
) -> dict:
    return {
        "id": contact_id,
        "first_name": first_name,
        "last_name": last_name,
        "nickname": nickname,
        "name": f"{first_name} {last_name}",
        "company": None,
        "job_title": None,
        "details": {},
        "metadata": {},
        "entity_id": entity_id,
        "archived_at": None,
        "listed": True,
        "updated_at": None,
    }


def _make_entity_row(
    entity_id: uuid.UUID = ENTITY_UUID,
    aliases: list[str] | None = None,
) -> dict:
    return {
        "id": entity_id,
        "tenant_id": "relationship",
        "canonical_name": "Alice Smith",
        "entity_type": "person",
        "aliases": aliases or [],
        "metadata": {},
        "created_at": None,
        "updated_at": None,
    }


def _asyncpg_record(d: dict):
    """Simulate an asyncpg Record via a MagicMock."""
    rec = MagicMock()
    rec.__iter__ = lambda s: iter(d.items())
    rec.__getitem__ = lambda s, k: d[k]
    rec.get = lambda k, default=None: d.get(k, default)
    rec.keys = lambda: d.keys()
    rec.values = lambda: d.values()
    rec.items = lambda: d.items()
    return rec


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestBuildCanonicalName:
    def test_first_and_last(self):
        assert _build_canonical_name("Alice", "Smith") == "Alice Smith"

    def test_first_only(self):
        assert _build_canonical_name("Alice", None) == "Alice"

    def test_last_only(self):
        assert _build_canonical_name(None, "Smith") == "Smith"

    def test_both_none(self):
        assert _build_canonical_name(None, None) == "Unknown"

    def test_strips_whitespace(self):
        assert _build_canonical_name("  Alice ", "  Smith ") == "Alice Smith"


class TestBuildEntityAliases:
    def test_nickname_and_first_name_included(self):
        aliases = _build_entity_aliases("Alice", "Smith", "Ali")
        assert "Ali" in aliases
        assert "Alice" in aliases
        assert "Alice Smith" not in aliases  # canonical not duplicated

    def test_no_duplicates_with_nickname_same_as_first(self):
        aliases = _build_entity_aliases("Alice", "Smith", "Alice")
        # "Alice" appears only once
        assert aliases.count("Alice") == 1

    def test_no_nickname(self):
        aliases = _build_entity_aliases("Alice", "Smith", None)
        assert "Alice" in aliases
        assert None not in aliases

    def test_all_none(self):
        aliases = _build_entity_aliases(None, None, None)
        assert aliases == []


# ---------------------------------------------------------------------------
# contact_create + entity bridge
# ---------------------------------------------------------------------------


class TestContactCreateEntityBridge:
    async def test_entity_create_called_on_create_with_memory_pool(self):
        """contact_create calls entity_create and stores entity_id when memory_pool given."""
        contact_row = _make_contact_row(entity_id=None)
        contact_row_with_entity = _make_contact_row(entity_id=ENTITY_UUID)

        pool = AsyncMock()
        pool.fetchrow = AsyncMock(
            side_effect=[
                _asyncpg_record(contact_row),  # INSERT RETURNING
                _asyncpg_record(contact_row_with_entity),  # UPDATE entity_id RETURNING
            ]
        )
        memory_pool = AsyncMock()

        with (
            patch.object(_contacts_mod, "table_columns", AsyncMock(return_value=_FULL_COLS)),
            patch.object(_contacts_mod, "_log_activity", AsyncMock()),
            patch.object(
                _contacts_mod,
                "_sync_entity_create",
                AsyncMock(return_value=str(ENTITY_UUID)),
            ) as mock_create,
        ):
            await contact_create(
                pool,
                first_name="Alice",
                last_name="Smith",
                nickname="Ali",
                memory_pool=memory_pool,
            )
            mock_create.assert_awaited_once()
            # entity_id UPDATE was called (second fetchrow call)
            assert pool.fetchrow.call_count == 2

    async def test_entity_create_failure_is_fail_open(self):
        """contact_create succeeds even when entity_create raises."""
        contact_row = _make_contact_row(entity_id=None)
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=_asyncpg_record(contact_row))

        memory_pool = AsyncMock()

        with (
            patch.object(_contacts_mod, "table_columns", AsyncMock(return_value=_FULL_COLS)),
            patch.object(_contacts_mod, "_log_activity", AsyncMock()),
            patch.object(
                _contacts_mod,
                "_sync_entity_create",
                AsyncMock(return_value=None),  # failure returns None
            ),
        ):
            result = await contact_create(
                pool,
                first_name="Alice",
                last_name="Smith",
                memory_pool=memory_pool,
            )
        assert result["first_name"] == "Alice"
        # No entity_id update attempted since _sync_entity_create returned None
        assert pool.fetchrow.call_count == 1  # only INSERT, no UPDATE

    async def test_no_memory_pool_skips_entity_create(self):
        """contact_create without memory_pool does not call entity functions."""
        contact_row = _make_contact_row(entity_id=None)
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=_asyncpg_record(contact_row))

        with (
            patch.object(_contacts_mod, "table_columns", AsyncMock(return_value=_FULL_COLS)),
            patch.object(_contacts_mod, "_log_activity", AsyncMock()),
            patch.object(
                _contacts_mod,
                "_sync_entity_create",
                AsyncMock(return_value=str(ENTITY_UUID)),
            ) as mock_create,
        ):
            await contact_create(pool, first_name="Alice", last_name="Smith")
            mock_create.assert_not_awaited()

    async def test_no_entity_id_column_skips_entity_create(self):
        """contact_create with memory_pool but no entity_id column skips entity sync."""
        contact_row = _make_contact_row(entity_id=None)
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=_asyncpg_record(contact_row))

        # No entity_id in cols
        cols_without_entity = _FULL_COLS - {"entity_id"}
        memory_pool = AsyncMock()

        with (
            patch.object(
                _contacts_mod,
                "table_columns",
                AsyncMock(return_value=cols_without_entity),
            ),
            patch.object(_contacts_mod, "_log_activity", AsyncMock()),
            patch.object(
                _contacts_mod,
                "_sync_entity_create",
                AsyncMock(return_value=str(ENTITY_UUID)),
            ) as mock_create,
        ):
            await contact_create(
                pool,
                first_name="Alice",
                last_name="Smith",
                memory_pool=memory_pool,
            )
            mock_create.assert_not_awaited()


# ---------------------------------------------------------------------------
# contact_update + entity bridge
# ---------------------------------------------------------------------------


class TestContactUpdateEntityBridge:
    async def test_entity_update_called_when_name_changes(self):
        """contact_update calls entity_update when first_name changes and entity_id set."""
        contact_row = _make_contact_row(entity_id=ENTITY_UUID)
        updated_row = _make_contact_row(first_name="Alicia", entity_id=ENTITY_UUID)

        pool = AsyncMock()
        pool.fetchrow = AsyncMock(
            side_effect=[
                _asyncpg_record(contact_row),  # SELECT existing
                _asyncpg_record(updated_row),  # UPDATE RETURNING
            ]
        )
        memory_pool = AsyncMock()

        with (
            patch.object(_contacts_mod, "table_columns", AsyncMock(return_value=_FULL_COLS)),
            patch.object(_contacts_mod, "_log_activity", AsyncMock()),
            patch.object(
                _contacts_mod,
                "_sync_entity_update",
                AsyncMock(),
            ) as mock_update,
        ):
            await contact_update(
                pool,
                CONTACT_UUID,
                memory_pool=memory_pool,
                first_name="Alicia",
            )
            mock_update.assert_awaited_once()
            call_kwargs = mock_update.call_args
            assert call_kwargs.kwargs["entity_id"] == str(ENTITY_UUID)
            assert call_kwargs.kwargs["first_name"] == "Alicia"

    async def test_null_entity_id_handled_gracefully(self):
        """contact_update with NULL entity_id does not crash when memory_pool provided."""
        contact_row = _make_contact_row(entity_id=None)  # no entity
        updated_row = _make_contact_row(first_name="Alicia", entity_id=None)

        pool = AsyncMock()
        pool.fetchrow = AsyncMock(
            side_effect=[
                _asyncpg_record(contact_row),
                _asyncpg_record(updated_row),
            ]
        )
        memory_pool = AsyncMock()

        with (
            patch.object(_contacts_mod, "table_columns", AsyncMock(return_value=_FULL_COLS)),
            patch.object(_contacts_mod, "_log_activity", AsyncMock()),
            patch.object(
                _contacts_mod,
                "_sync_entity_update",
                AsyncMock(),
            ) as mock_update,
        ):
            result = await contact_update(
                pool,
                CONTACT_UUID,
                memory_pool=memory_pool,
                first_name="Alicia",
            )
            # entity_update NOT called because entity_id is NULL
            mock_update.assert_not_awaited()
            assert result["first_name"] == "Alicia"

    async def test_no_memory_pool_skips_entity_update(self):
        """contact_update without memory_pool does not call entity sync."""
        contact_row = _make_contact_row(entity_id=ENTITY_UUID)
        updated_row = _make_contact_row(first_name="Alicia", entity_id=ENTITY_UUID)

        pool = AsyncMock()
        pool.fetchrow = AsyncMock(
            side_effect=[
                _asyncpg_record(contact_row),
                _asyncpg_record(updated_row),
            ]
        )

        with (
            patch.object(_contacts_mod, "table_columns", AsyncMock(return_value=_FULL_COLS)),
            patch.object(_contacts_mod, "_log_activity", AsyncMock()),
            patch.object(
                _contacts_mod,
                "_sync_entity_update",
                AsyncMock(),
            ) as mock_update,
        ):
            await contact_update(pool, CONTACT_UUID, first_name="Alicia")
            mock_update.assert_not_awaited()

    async def test_non_name_fields_do_not_trigger_entity_sync(self):
        """contact_update with only company change does not call entity sync."""
        contact_row = _make_contact_row(entity_id=ENTITY_UUID)
        updated_row = _make_contact_row(entity_id=ENTITY_UUID)

        pool = AsyncMock()
        pool.fetchrow = AsyncMock(
            side_effect=[
                _asyncpg_record(contact_row),
                _asyncpg_record(updated_row),
            ]
        )
        memory_pool = AsyncMock()

        with (
            patch.object(_contacts_mod, "table_columns", AsyncMock(return_value=_FULL_COLS)),
            patch.object(_contacts_mod, "_log_activity", AsyncMock()),
            patch.object(
                _contacts_mod,
                "_sync_entity_update",
                AsyncMock(),
            ) as mock_update,
        ):
            await contact_update(pool, CONTACT_UUID, memory_pool=memory_pool, company="Acme")
            mock_update.assert_not_awaited()


# ---------------------------------------------------------------------------
# contact_merge + entity bridge
# ---------------------------------------------------------------------------


class TestContactMergeEntityBridge:
    def _make_pool(
        self,
        src_entity_id: uuid.UUID | None = ENTITY_UUID,
        tgt_entity_id: uuid.UUID | None = ENTITY_UUID2,
    ):
        source_row = _make_contact_row(
            contact_id=CONTACT_UUID,
            entity_id=src_entity_id,
        )
        target_row = _make_contact_row(
            contact_id=CONTACT_UUID2,
            entity_id=tgt_entity_id,
        )
        updated_target_row = dict(target_row)

        pool = AsyncMock()
        pool.fetchrow = AsyncMock(
            side_effect=[
                _asyncpg_record(source_row),  # SELECT source
                _asyncpg_record(target_row),  # SELECT target
                _asyncpg_record(updated_target_row),  # SELECT after merge
            ]
        )
        # Simulate the acquire() context manager for transaction
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()
        mock_conn.transaction = MagicMock()
        mock_conn.transaction.return_value.__aenter__ = AsyncMock(return_value=None)
        mock_conn.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
        pool.acquire = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
        return pool

    async def test_entity_merge_called_when_both_have_entity_ids(self):
        """contact_merge calls entity_merge when source and target both have entity_ids."""
        pool = self._make_pool(src_entity_id=ENTITY_UUID, tgt_entity_id=ENTITY_UUID2)
        memory_pool = AsyncMock()

        with (
            patch.object(_contacts_mod, "table_columns", AsyncMock(return_value=_FULL_COLS)),
            patch.object(_contacts_mod, "_log_activity", AsyncMock()),
            patch(
                "butlers.modules.memory.tools.entities.entity_merge",
                AsyncMock(return_value={"entity_id": str(ENTITY_UUID2)}),
            ) as mock_entity_merge,
        ):
            await contact_merge(
                pool,
                source_id=CONTACT_UUID,
                target_id=CONTACT_UUID2,
                memory_pool=memory_pool,
            )
            mock_entity_merge.assert_awaited_once_with(
                memory_pool,
                str(ENTITY_UUID),
                str(ENTITY_UUID2),
                tenant_id="relationship",
            )

    async def test_entity_merge_skipped_when_source_has_no_entity_id(self):
        """contact_merge skips entity_merge when source entity_id is NULL."""
        pool = self._make_pool(src_entity_id=None, tgt_entity_id=ENTITY_UUID2)
        memory_pool = AsyncMock()

        with (
            patch.object(_contacts_mod, "table_columns", AsyncMock(return_value=_FULL_COLS)),
            patch.object(_contacts_mod, "_log_activity", AsyncMock()),
            patch(
                "butlers.modules.memory.tools.entities.entity_merge",
                AsyncMock(),
            ) as mock_entity_merge,
        ):
            await contact_merge(
                pool,
                source_id=CONTACT_UUID,
                target_id=CONTACT_UUID2,
                memory_pool=memory_pool,
            )
            mock_entity_merge.assert_not_awaited()

    async def test_entity_merge_skipped_when_target_has_no_entity_id(self):
        """contact_merge skips entity_merge when target entity_id is NULL."""
        pool = self._make_pool(src_entity_id=ENTITY_UUID, tgt_entity_id=None)
        memory_pool = AsyncMock()

        with (
            patch.object(_contacts_mod, "table_columns", AsyncMock(return_value=_FULL_COLS)),
            patch.object(_contacts_mod, "_log_activity", AsyncMock()),
            patch(
                "butlers.modules.memory.tools.entities.entity_merge",
                AsyncMock(),
            ) as mock_entity_merge,
        ):
            await contact_merge(
                pool,
                source_id=CONTACT_UUID,
                target_id=CONTACT_UUID2,
                memory_pool=memory_pool,
            )
            mock_entity_merge.assert_not_awaited()

    async def test_entity_merge_failure_is_fail_open(self):
        """contact_merge does not crash when entity_merge raises."""
        pool = self._make_pool(src_entity_id=ENTITY_UUID, tgt_entity_id=ENTITY_UUID2)
        memory_pool = AsyncMock()

        with (
            patch.object(_contacts_mod, "table_columns", AsyncMock(return_value=_FULL_COLS)),
            patch.object(_contacts_mod, "_log_activity", AsyncMock()),
            patch(
                "butlers.modules.memory.tools.entities.entity_merge",
                AsyncMock(side_effect=RuntimeError("entity DB down")),
            ),
        ):
            # Should not raise; fail-open
            result = await contact_merge(
                pool,
                source_id=CONTACT_UUID,
                target_id=CONTACT_UUID2,
                memory_pool=memory_pool,
            )
        assert result is not None

    async def test_same_id_raises(self):
        """contact_merge raises when source and target are identical."""
        pool = AsyncMock()
        with pytest.raises(ValueError, match="different"):
            await contact_merge(pool, source_id=CONTACT_UUID, target_id=CONTACT_UUID)

    async def test_source_not_found_raises(self):
        """contact_merge raises when source contact does not exist."""
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Source contact"):
            await contact_merge(pool, source_id=CONTACT_UUID, target_id=CONTACT_UUID2)

    async def test_target_not_found_raises(self):
        """contact_merge raises when target contact does not exist."""
        source_row = _make_contact_row(entity_id=ENTITY_UUID)
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(
            side_effect=[
                _asyncpg_record(source_row),
                None,  # target not found
            ]
        )
        with pytest.raises(ValueError, match="Target contact"):
            await contact_merge(pool, source_id=CONTACT_UUID, target_id=CONTACT_UUID2)

    async def test_no_memory_pool_skips_entity_merge(self):
        """contact_merge without memory_pool skips entity_merge."""
        pool = self._make_pool(src_entity_id=ENTITY_UUID, tgt_entity_id=ENTITY_UUID2)

        with (
            patch.object(_contacts_mod, "table_columns", AsyncMock(return_value=_FULL_COLS)),
            patch.object(_contacts_mod, "_log_activity", AsyncMock()),
            patch(
                "butlers.modules.memory.tools.entities.entity_merge",
                AsyncMock(),
            ) as mock_entity_merge,
        ):
            await contact_merge(
                pool,
                source_id=CONTACT_UUID,
                target_id=CONTACT_UUID2,
                # No memory_pool
            )
            mock_entity_merge.assert_not_awaited()


# ---------------------------------------------------------------------------
# entity_merge unit tests
# ---------------------------------------------------------------------------


class TestEntityMerge:
    def _make_entity_pool(
        self,
        src_aliases: list[str] | None = None,
        tgt_aliases: list[str] | None = None,
        src_exists: bool = True,
        tgt_exists: bool = True,
    ):
        src_row = {
            "id": ENTITY_UUID,
            "aliases": src_aliases or ["Ali"],
            "tenant_id": "relationship",
            "canonical_name": "Alice",
            "metadata": {},
        }
        tgt_row = {
            "id": ENTITY_UUID2,
            "aliases": tgt_aliases or ["Ally"],
            "tenant_id": "relationship",
            "canonical_name": "Alice Smith",
            "entity_type": "person",
            "metadata": {},
            "created_at": None,
            "updated_at": None,
        }

        pool = AsyncMock()

        # entity_merge uses conn.fetchrow (inside acquire() context), not pool.fetchrow
        fetchrow_results = []
        if src_exists:
            fetchrow_results.append(_asyncpg_record(src_row))
        else:
            fetchrow_results.append(None)
        if tgt_exists:
            fetchrow_results.append(_asyncpg_record(tgt_row))
        else:
            fetchrow_results.append(None)

        # Simulate acquire() transaction context
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])  # no facts by default
        mock_conn.fetchrow = AsyncMock(side_effect=fetchrow_results)
        mock_conn.transaction = MagicMock()
        mock_conn.transaction.return_value.__aenter__ = AsyncMock(return_value=None)
        mock_conn.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
        pool.acquire = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        return pool, mock_conn

    async def test_same_id_raises_value_error(self):
        pool = AsyncMock()
        with pytest.raises(ValueError, match="different"):
            await entity_merge(
                pool,
                str(ENTITY_UUID),
                str(ENTITY_UUID),
                tenant_id="relationship",
            )

    async def test_source_not_found_raises(self):
        pool, _ = self._make_entity_pool(src_exists=False)
        with pytest.raises(ValueError, match="Source entity"):
            await entity_merge(
                pool,
                str(ENTITY_UUID),
                str(ENTITY_UUID2),
                tenant_id="relationship",
            )

    async def test_target_not_found_raises(self):
        """entity_merge raises ValueError when target entity does not exist."""
        pool, _ = self._make_entity_pool(tgt_exists=False)
        with pytest.raises(ValueError, match="Target entity"):
            await entity_merge(
                pool,
                str(ENTITY_UUID),
                str(ENTITY_UUID2),
                tenant_id="relationship",
            )

    async def test_facts_repointed_in_transaction(self):
        """entity_merge updates facts to point to target entity."""
        pool, mock_conn = self._make_entity_pool()
        await entity_merge(
            pool,
            str(ENTITY_UUID),
            str(ENTITY_UUID2),
            tenant_id="relationship",
        )
        # Check UPDATE facts call
        execute_calls = [str(call) for call in mock_conn.execute.call_args_list]
        assert any("facts" in c and "entity_id" in c for c in execute_calls)

    async def test_aliases_merged_deduped(self):
        """entity_merge merges aliases from source into target, deduplicated."""
        pool, mock_conn = self._make_entity_pool(
            src_aliases=["Ali", "Alice"],
            tgt_aliases=["Ally", "Alice"],
        )
        await entity_merge(
            pool,
            str(ENTITY_UUID),
            str(ENTITY_UUID2),
            tenant_id="relationship",
        )
        # entity_merge calls UPDATE entities SET aliases for the target entity
        execute_calls = mock_conn.execute.call_args_list
        update_entity_calls = [c for c in execute_calls if "UPDATE entities SET aliases" in c[0][0]]
        assert len(update_entity_calls) >= 1
        merged_aliases = update_entity_calls[0][0][1]
        assert "Ali" in merged_aliases
        assert "Ally" in merged_aliases
        # "Alice" should appear only once (deduplicated)
        assert merged_aliases.count("Alice") == 1

    async def test_source_tombstoned(self):
        """entity_merge tombstones the source entity via merged_into metadata flag."""
        pool, mock_conn = self._make_entity_pool()
        await entity_merge(
            pool,
            str(ENTITY_UUID),
            str(ENTITY_UUID2),
            tenant_id="relationship",
        )
        # Verify one of the execute calls tombstones source entity with merged_into
        execute_calls = mock_conn.execute.call_args_list
        tombstone_calls = [
            c
            for c in execute_calls
            if "UPDATE entities SET metadata" in c[0][0] and ENTITY_UUID in c[0]
        ]
        assert len(tombstone_calls) == 1, f"Tombstone call not found in: {execute_calls}"
        tombstone_meta = json.loads(tombstone_calls[0][0][1])
        assert tombstone_meta.get("merged_into") == str(ENTITY_UUID2)


# ---------------------------------------------------------------------------
# Task 10.1-10.2: contact_update MCP tool strips roles (security guard)
# ---------------------------------------------------------------------------


class TestContactUpdateRolesGuard:
    """Verify that contact_update never modifies the roles column.

    Runtime LLM instances must not be able to grant themselves or others
    identity roles (e.g. 'owner') via the contact_update MCP tool.
    """

    def _make_pool_for_update(self, contact_row: dict) -> AsyncMock:
        """Return a mock pool suitable for contact_update tests."""
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(
            side_effect=[
                _asyncpg_record(contact_row),  # SELECT existing
                _asyncpg_record(contact_row),  # UPDATE RETURNING
            ]
        )
        return pool

    async def test_roles_in_args_is_silently_ignored(self):
        """contact_update with roles=['owner'] in args does not modify roles."""
        contact_row = _make_contact_row()
        pool = self._make_pool_for_update(contact_row)

        with (
            patch.object(_contacts_mod, "table_columns", AsyncMock(return_value=_FULL_COLS)),
            patch.object(_contacts_mod, "_log_activity", AsyncMock()),
        ):
            result = await contact_update(
                pool,
                CONTACT_UUID,
                first_name="Alice",
                roles=["owner"],  # Should be stripped — never written
            )

        # Verify the UPDATE SQL issued by pool.fetchrow (second call) does NOT
        # include 'roles' in the SET clause.
        update_call = pool.fetchrow.call_args_list[1]
        sql_fragment = update_call[0][0]
        assert "roles" not in sql_fragment, (
            f"'roles' found in UPDATE SQL: {sql_fragment!r} — contact_update must strip roles"
        )
        # Result still returned successfully
        assert result is not None

    async def test_roles_not_in_update_even_when_column_exists(self):
        """contact_update never includes roles in SET even if roles is a valid column."""
        contact_row = _make_contact_row()
        pool = self._make_pool_for_update(contact_row)

        # Pretend roles is a valid column — the guard must still prevent writing it
        cols_with_roles = _FULL_COLS | {"roles"}

        with (
            patch.object(_contacts_mod, "table_columns", AsyncMock(return_value=cols_with_roles)),
            patch.object(_contacts_mod, "_log_activity", AsyncMock()),
        ):
            await contact_update(
                pool,
                CONTACT_UUID,
                first_name="Alice",
                roles=["admin"],  # Must be stripped before building the query
            )

        update_call = pool.fetchrow.call_args_list[1]
        sql_fragment = update_call[0][0]
        assert "roles" not in sql_fragment, (
            f"'roles' found in UPDATE SQL even with column present: {sql_fragment!r}"
        )

    async def test_valid_fields_still_updated_after_roles_stripped(self):
        """Stripping roles does not prevent other valid fields from being updated."""
        contact_row = _make_contact_row()
        updated_row = _make_contact_row(first_name="NewName")
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(
            side_effect=[
                _asyncpg_record(contact_row),
                _asyncpg_record(updated_row),
            ]
        )

        with (
            patch.object(_contacts_mod, "table_columns", AsyncMock(return_value=_FULL_COLS)),
            patch.object(_contacts_mod, "_log_activity", AsyncMock()),
        ):
            result = await contact_update(
                pool,
                CONTACT_UUID,
                first_name="NewName",
                roles=["owner"],  # Stripped
            )

        # first_name update still went through
        assert result["first_name"] == "NewName"

    async def test_only_roles_in_args_raises_no_fields_error(self):
        """contact_update with only roles in args raises ValueError (nothing to update)."""
        contact_row = _make_contact_row()
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=_asyncpg_record(contact_row))

        with (
            patch.object(_contacts_mod, "table_columns", AsyncMock(return_value=_FULL_COLS)),
            patch.object(_contacts_mod, "_log_activity", AsyncMock()),
        ):
            with pytest.raises(ValueError, match="At least one field"):
                await contact_update(
                    pool,
                    CONTACT_UUID,
                    roles=["owner"],  # Stripped, leaving nothing to update
                )
