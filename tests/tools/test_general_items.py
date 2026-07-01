"""Unit tests for General collection item helpers."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_item_create_auto_creates_collection_before_insert() -> None:
    """item_create resolves-or-creates the collection, then inserts the item.

    The common path is a lightweight SELECT; a new collection name falls back
    to an ON CONFLICT upsert before the item insert. This exercises the
    new-collection path (SELECT misses, upsert creates, item inserts).
    """
    from butlers.tools.general.items import item_create

    collection_id = uuid.uuid4()
    expected_id = uuid.uuid4()
    fetchval_mock = AsyncMock(side_effect=[None, collection_id, expected_id])
    pool = SimpleNamespace(fetchval=fetchval_mock)

    item_id = await item_create(
        pool,
        "episodes",
        {"summary": "Captured note"},
        tags=["auto-created"],
    )

    assert item_id == expected_id
    assert fetchval_mock.await_count == 3

    select_call = fetchval_mock.await_args_list[0].args
    assert "SELECT id FROM collections" in select_call[0]
    assert select_call[1] == "episodes"

    coll_insert_call = fetchval_mock.await_args_list[1].args
    assert "INSERT INTO collections" in coll_insert_call[0]
    assert "ON CONFLICT (name) DO UPDATE" in coll_insert_call[0]
    assert coll_insert_call[1] == "episodes"

    item_insert_call = fetchval_mock.await_args_list[2].args
    assert "INSERT INTO collection_items" in item_insert_call[0]
    assert item_insert_call[1] == collection_id
    assert item_insert_call[2] == {"summary": "Captured note"}
    assert item_insert_call[3] == ["auto-created"]


@pytest.mark.asyncio
async def test_item_create_reuses_existing_collection_without_upsert() -> None:
    """When the collection exists, item_create skips the collections upsert."""
    from butlers.tools.general.items import item_create

    collection_id = uuid.uuid4()
    expected_id = uuid.uuid4()
    fetchval_mock = AsyncMock(side_effect=[collection_id, expected_id])
    pool = SimpleNamespace(fetchval=fetchval_mock)

    item_id = await item_create(pool, "episodes", {"summary": "note"})

    assert item_id == expected_id
    # Only the SELECT + the item insert run — no write to the collections row.
    assert fetchval_mock.await_count == 2
    assert "SELECT id FROM collections" in fetchval_mock.await_args_list[0].args[0]
    assert "INSERT INTO collection_items" in fetchval_mock.await_args_list[1].args[0]
