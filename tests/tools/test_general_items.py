"""Unit tests for General collection item helpers."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest


class _ScriptedConnection:
    """An acquired-connection stand-in: records SQL, answers from a script.

    It has no ``acquire``, so the ordinary transaction helper uses it directly
    as the caller's connection, exactly as the capture service will.
    """

    def __init__(self, collection_id: uuid.UUID | None, item_id: uuid.UUID) -> None:
        self.statements: list[str] = []
        self._collection_id = collection_id
        self._created_id = uuid.uuid4()
        self._item_id = item_id

    @asynccontextmanager
    async def transaction(self):
        yield

    async def fetchrow(self, sql: str, *args):
        self.statements.append(sql)
        if "has_flag" in sql:
            return {"has_flag": True, "has_ordinary_index": True, "has_global_unique": True}
        if "FOR SHARE OF c" in sql:
            return {"id": args[0], "name": "episodes", "eligibility_generation": 0}
        if "SELECT collection_id, data, tags" in sql:
            return {"collection_id": args[0], "data": {}, "tags": []}
        raise AssertionError(f"unscripted fetchrow: {sql}")

    async def fetchval(self, sql: str, *args):
        self.statements.append(sql)
        if "WHERE custody_private)" in sql:
            return False
        if "AS candidate" in sql:
            return self._collection_id
        if "INSERT INTO collections" in sql:
            return self._created_id
        if "INSERT INTO collection_items" in sql:
            return self._item_id
        if "INSERT INTO source_versions" in sql:
            return 1
        raise AssertionError(f"unscripted fetchval: {sql}")


@pytest.mark.asyncio
async def test_item_create_auto_creates_collection_before_insert() -> None:
    """A new ordinary name is created with the targetless writer, then the item.

    The namespace gate runs before any name lookup; the collection writer is
    ``ON CONFLICT DO NOTHING`` (valid with or without legacy global uniqueness,
    never ``ON CONFLICT (name)``); the created parent is share-locked before
    the item insert; and the item's first source version is recorded last.
    """
    from butlers.tools.general.items import item_create_versioned

    expected_id = uuid.uuid4()
    conn = _ScriptedConnection(collection_id=None, item_id=expected_id)

    item_id, version = await item_create_versioned(
        conn, "episodes", {"summary": "Captured note"}, tags=["auto-created"]
    )

    assert item_id == expected_id
    assert (version.operation, version.version) == ("create", 1)
    steps = [
        "has_flag",
        "WHERE custody_private)",
        "AS candidate",
        "INSERT INTO collections",
        "FOR SHARE OF c",
        "INSERT INTO collection_items",
        "SELECT collection_id, data, tags",
        "INSERT INTO source_versions",
    ]
    assert [next(s for s in steps if s in sql) for sql in conn.statements] == steps
    collection_insert = next(sql for sql in conn.statements if "INSERT INTO collections" in sql)
    assert "ON CONFLICT DO NOTHING" in collection_insert
    assert "ON CONFLICT (name)" not in collection_insert


@pytest.mark.asyncio
async def test_item_create_reuses_existing_collection_without_upsert() -> None:
    """When the ordinary collection exists, item_create never writes collections."""
    from butlers.tools.general.items import item_create_versioned

    conn = _ScriptedConnection(collection_id=uuid.uuid4(), item_id=uuid.uuid4())

    await item_create_versioned(conn, "episodes", {"summary": "note"})

    assert not any("INSERT INTO collections" in sql for sql in conn.statements)
    assert any("INSERT INTO collection_items" in sql for sql in conn.statements)


@pytest.mark.asyncio
async def test_generic_item_writes_refuse_reserved_custody_fields_before_any_query() -> None:
    """Generic input can never introduce or replace a custody profile."""
    from butlers.tools.general.items import item_create_versioned, item_update

    conn = _ScriptedConnection(collection_id=uuid.uuid4(), item_id=uuid.uuid4())

    with pytest.raises(ValueError, match="reserved"):
        await item_create_versioned(conn, "episodes", {"possession_profile": {}})
    with pytest.raises(ValueError, match="reserved"):
        await item_update(conn, uuid.uuid4(), {"possession_profile": None})
    assert conn.statements == []
