"""Unit tests for contact-entity lifecycle bridge."""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

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
_infer_entity_type = _contacts_mod._infer_entity_type
entity_merge = _entities_mod.entity_merge

CONTACT_UUID = uuid.UUID("11111111-2222-3333-4444-555555555555")
CONTACT_UUID2 = uuid.UUID("aaaa1111-bbbb-cccc-dddd-eeeeeeeeeeee")
ENTITY_UUID = uuid.UUID("eeee1111-2222-3333-4444-555555555555")
ENTITY_UUID2 = uuid.UUID("eeee2222-3333-4444-5555-666666666666")

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
    contact_id=CONTACT_UUID, first_name="Alice", last_name="Smith", nickname="Ali", entity_id=None
):
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


def _asyncpg_record(d: dict):
    rec = MagicMock()
    rec.__iter__ = lambda s: iter(d.items())
    rec.__getitem__ = lambda s, k: d[k]
    rec.get = lambda k, default=None: d.get(k, default)
    rec.keys = lambda: d.keys()
    return rec


def test_name_and_type_helpers():
    """_build_canonical_name and _infer_entity_type return correct values."""
    assert _build_canonical_name("Alice", "Smith") == "Alice Smith"
    assert _build_canonical_name("Alice", None) == "Alice"
    assert _build_canonical_name(None, None) == "Unknown"
    assert _infer_entity_type("Alice", "Smith", None) == "person"
    assert _infer_entity_type(None, None, "Acme Corp") == "organization"
    assert _infer_entity_type(None, None, None) == "person"


async def test_contact_create_and_update_entity_sync():
    """contact_create calls _ensure_entity; contact_update syncs entity on name change."""
    contact_row = _make_contact_row(entity_id=ENTITY_UUID)
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=_asyncpg_record(contact_row))

    with (
        patch.object(_contacts_mod, "table_columns", AsyncMock(return_value=_FULL_COLS)),
        patch.object(_contacts_mod, "_log_activity", AsyncMock()),
        patch.object(
            _contacts_mod, "_ensure_entity", AsyncMock(return_value=str(ENTITY_UUID))
        ) as mock_ensure,
    ):
        result = await contact_create(pool, first_name="Alice", last_name="Smith", nickname="Ali")
        mock_ensure.assert_awaited_once()
        assert result["entity_id"] == ENTITY_UUID

    # Failure path
    pool2 = AsyncMock()
    with (
        patch.object(_contacts_mod, "table_columns", AsyncMock(return_value=_FULL_COLS)),
        patch.object(_contacts_mod, "_log_activity", AsyncMock()),
        patch.object(
            _contacts_mod, "_ensure_entity", AsyncMock(side_effect=RuntimeError("failed"))
        ),
    ):
        with pytest.raises(RuntimeError, match="failed"):
            await contact_create(pool2, first_name="Alice", last_name="Smith")

    # Update: syncs entity when entity_id present, skips when NULL
    for entity_id, should_sync in [(ENTITY_UUID, True), (None, False)]:
        old_row = _make_contact_row(entity_id=entity_id)
        updated_row = _make_contact_row(first_name="Alicia", entity_id=entity_id)
        pool3 = AsyncMock()
        pool3.fetchrow = AsyncMock(
            side_effect=[_asyncpg_record(old_row), _asyncpg_record(updated_row)]
        )
        with (
            patch.object(_contacts_mod, "table_columns", AsyncMock(return_value=_FULL_COLS)),
            patch.object(_contacts_mod, "_log_activity", AsyncMock()),
            patch.object(_contacts_mod, "_sync_entity_update", AsyncMock()) as mock_sync,
        ):
            await contact_update(pool3, CONTACT_UUID, first_name="Alicia")
            if should_sync:
                mock_sync.assert_awaited_once()
            else:
                mock_sync.assert_not_awaited()


async def test_contact_merge_and_entity_merge_validation():
    """contact_merge calls entity_merge when both have entity_ids; entity_merge validates."""
    src = _make_contact_row(contact_id=CONTACT_UUID, entity_id=ENTITY_UUID)
    tgt = _make_contact_row(contact_id=CONTACT_UUID2, entity_id=ENTITY_UUID2)
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(
        side_effect=[
            _asyncpg_record(src),
            _asyncpg_record(tgt),
            _asyncpg_record(dict(tgt)),
        ]
    )
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock()
    mock_conn.transaction = MagicMock()
    mock_conn.transaction.return_value.__aenter__ = AsyncMock(return_value=None)
    mock_conn.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

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
            pool, source_id=CONTACT_UUID, target_id=CONTACT_UUID2, memory_pool=memory_pool
        )
        mock_entity_merge.assert_awaited_once_with(
            memory_pool, str(ENTITY_UUID), str(ENTITY_UUID2)        )

    # entity_merge validation: same ID raises
    with pytest.raises(ValueError, match="different"):
        await entity_merge(
            AsyncMock(), str(ENTITY_UUID), str(ENTITY_UUID)        )

    # Missing entity raises
    def _mock_missing_src():
        p = AsyncMock()
        mc = AsyncMock()
        mc.fetchrow = AsyncMock(return_value=None)
        mc.transaction = MagicMock()
        mc.transaction.return_value.__aenter__ = AsyncMock(return_value=None)
        mc.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
        p.acquire = MagicMock()
        p.acquire.return_value.__aenter__ = AsyncMock(return_value=mc)
        p.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
        return p

    with pytest.raises(ValueError):
        await entity_merge(
            _mock_missing_src(), str(ENTITY_UUID), str(ENTITY_UUID2)        )
