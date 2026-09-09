"""Unit tests for General collection item helpers."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_item_create_resolves_via_vocabulary_before_insert(monkeypatch) -> None:
    """A collection unknown to the ``collections`` table resolves through the
    vocabulary before falling back to the ON CONFLICT upsert + item insert.

    The vocabulary resolution itself is exercised by
    ``roster/general/tests/test_collection_vocabulary.py``; this test only
    pins that ``item_create`` calls it on a miss and uses its result.
    """
    from butlers.tools.general import items as items_mod

    collection_id = uuid.uuid4()
    expected_id = uuid.uuid4()
    fetchval_mock = AsyncMock(side_effect=[None, None, collection_id, expected_id])
    pool = SimpleNamespace(fetchval=fetchval_mock)

    resolve_mock = AsyncMock(return_value="episodes")
    monkeypatch.setattr(items_mod._vocabulary, "resolve_collection_name", resolve_mock)

    item_id = await items_mod.item_create(
        pool,
        "episodes",
        {"summary": "Captured note"},
        tags=["auto-created"],
    )

    assert item_id == expected_id
    resolve_mock.assert_awaited_once_with(pool, "episodes")
    assert fetchval_mock.await_count == 4

    first_select = fetchval_mock.await_args_list[0].args
    assert "SELECT id FROM collections" in first_select[0]
    assert first_select[1] == "episodes"

    second_select = fetchval_mock.await_args_list[1].args
    assert "SELECT id FROM collections" in second_select[0]
    assert second_select[1] == "episodes"

    coll_insert_call = fetchval_mock.await_args_list[2].args
    assert "INSERT INTO collections" in coll_insert_call[0]
    assert "ON CONFLICT (name) DO UPDATE" in coll_insert_call[0]
    assert coll_insert_call[1] == "episodes"

    item_insert_call = fetchval_mock.await_args_list[3].args
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
