"""Tests for butlers.tools.general — item and collection management."""

from __future__ import annotations

import uuid

import asyncpg
import pytest

# All async tests in this file must share the session event loop so that the
# asyncpg pool (created in the session-scoped fixture loop per
# asyncio_default_fixture_loop_scope="session") is never used from a different
# loop.  Without this mark each test function gets a fresh function-scoped loop,
# which causes "got Future attached to a different loop" / asyncpg
# InterfaceError failures under pytest-xdist.
pytestmark = [
    pytest.mark.asyncio(loop_scope="session"),
]


@pytest.fixture
async def pool(provisioned_postgres_pool):
    """Provision a fresh database with general tables and return a pool."""
    async with provisioned_postgres_pool() as p:
        # Create the general tables (mirrors Alembic general migrations)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS collections (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS collection_items (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                collection_id UUID NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
                data JSONB NOT NULL DEFAULT '{}',
                tags JSONB NOT NULL DEFAULT '[]',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await p.execute(
            "CREATE INDEX IF NOT EXISTS idx_collection_items_data_gin"
            " ON collection_items USING GIN (data)"
        )
        await p.execute(
            "CREATE INDEX IF NOT EXISTS idx_collection_items_collection_id"
            " ON collection_items (collection_id)"
        )
        await p.execute(
            "CREATE INDEX IF NOT EXISTS idx_collection_items_tags_gin"
            " ON collection_items USING GIN (tags)"
        )

        yield p


# ------------------------------------------------------------------
# collection_create
# ------------------------------------------------------------------


async def test_collection_create(pool):
    """collection_create inserts a new collection and returns its UUID."""
    from butlers.tools.general import collection_create

    cid = await collection_create(pool, "books", description="My book collection")
    assert isinstance(cid, uuid.UUID)

    row = await pool.fetchrow("SELECT * FROM collections WHERE id = $1", cid)
    assert row is not None
    assert row["name"] == "books"
    assert row["description"] == "My book collection"


async def test_collection_create_no_description(pool):
    """collection_create works without a description."""
    from butlers.tools.general import collection_create

    cid = await collection_create(pool, "movies")
    assert isinstance(cid, uuid.UUID)

    row = await pool.fetchrow("SELECT * FROM collections WHERE id = $1", cid)
    assert row["description"] is None


async def test_collection_create_duplicate_name(pool):
    """collection_create raises on duplicate collection name."""
    from butlers.tools.general import collection_create

    await collection_create(pool, "unique_coll")
    with pytest.raises(asyncpg.UniqueViolationError):
        await collection_create(pool, "unique_coll")


# ------------------------------------------------------------------
# collection_list
# ------------------------------------------------------------------


async def test_collection_list(pool):
    """collection_list returns all collections ordered by name."""
    from butlers.tools.general import collection_create, collection_list

    await collection_create(pool, "zeta_list")
    await collection_create(pool, "alpha_list")

    colls = await collection_list(pool)
    names = [c["name"] for c in colls]
    assert "alpha_list" in names
    assert "zeta_list" in names
    # Verify ordering
    alpha_idx = names.index("alpha_list")
    zeta_idx = names.index("zeta_list")
    assert alpha_idx < zeta_idx


# ------------------------------------------------------------------
# collection_delete
# ------------------------------------------------------------------


async def test_collection_delete(pool):
    """collection_delete removes the collection."""
    from butlers.tools.general import collection_create, collection_delete, collection_list

    cid = await collection_create(pool, "doomed_coll")
    await collection_delete(pool, cid)

    colls = await collection_list(pool)
    names = [c["name"] for c in colls]
    assert "doomed_coll" not in names


async def test_collection_delete_not_found(pool):
    """collection_delete raises ValueError for non-existent collection."""
    from butlers.tools.general import collection_delete

    with pytest.raises(ValueError, match="not found"):
        await collection_delete(pool, uuid.uuid4())


async def test_collection_delete_cascades_items(pool):
    """Deleting a collection cascades to its items."""
    from butlers.tools.general import (
        collection_create,
        collection_delete,
        item_create,
        item_get,
    )

    cid = await collection_create(pool, "cascade_test")
    eid = await item_create(pool, "cascade_test", {"key": "value"})

    await collection_delete(pool, cid)

    # Entity should be gone too
    result = await item_get(pool, eid)
    assert result is None


# ------------------------------------------------------------------
# item_create
# ------------------------------------------------------------------


async def test_item_create(pool):
    """item_create stores an item with JSON data."""
    from butlers.tools.general import collection_create, item_create, item_get

    await collection_create(pool, "people")
    eid = await item_create(pool, "people", {"name": "Alice", "age": 30})
    assert isinstance(eid, uuid.UUID)

    entity = await item_get(pool, eid)
    assert entity is not None
    assert entity["data"]["name"] == "Alice"
    assert entity["data"]["age"] == 30


async def test_item_create_collection_not_found(pool):
    """item_create raises ValueError for non-existent collection."""
    from butlers.tools.general import item_create

    with pytest.raises(ValueError, match="not found"):
        await item_create(pool, "nonexistent_collection", {"a": 1})


async def test_item_create_with_tags(pool):
    """item_create stores tags as JSONB array."""
    from butlers.tools.general import collection_create, item_create, item_get

    await collection_create(pool, "tagged_entities")
    eid = await item_create(
        pool, "tagged_entities", {"type": "recipe"}, tags=["italian", "dinner"]
    )
    assert isinstance(eid, uuid.UUID)

    entity = await item_get(pool, eid)
    assert entity is not None
    assert entity["tags"] == ["italian", "dinner"]


async def test_item_create_without_tags_defaults_to_empty_list(pool):
    """item_create with no tags stores an empty JSONB array."""
    from butlers.tools.general import collection_create, item_create, item_get

    await collection_create(pool, "no_tags_coll")
    eid = await item_create(pool, "no_tags_coll", {"x": 1})

    entity = await item_get(pool, eid)
    assert entity is not None
    assert entity["tags"] == []


# ------------------------------------------------------------------
# item_get
# ------------------------------------------------------------------


async def test_item_get_missing(pool):
    """item_get returns None for a non-existent item."""
    from butlers.tools.general import item_get

    result = await item_get(pool, uuid.uuid4())
    assert result is None


async def test_item_get_includes_tags(pool):
    """item_get returns the tags field."""
    from butlers.tools.general import collection_create, item_create, item_get

    await collection_create(pool, "get_tags_coll")
    eid = await item_create(pool, "get_tags_coll", {"a": 1}, tags=["alpha", "beta"])

    entity = await item_get(pool, eid)
    assert entity is not None
    assert "tags" in entity
    assert entity["tags"] == ["alpha", "beta"]


# ------------------------------------------------------------------
# item_update with deep merge
# ------------------------------------------------------------------


async def test_item_update_shallow(pool):
    """item_update merges top-level keys."""
    from butlers.tools.general import collection_create, item_create, item_get, item_update

    await collection_create(pool, "update_shallow")
    eid = await item_create(pool, "update_shallow", {"a": 1, "b": 2})

    await item_update(pool, eid, {"b": 99, "c": 3})

    entity = await item_get(pool, eid)
    assert entity["data"] == {"a": 1, "b": 99, "c": 3}


async def test_item_update_deep_merge(pool):
    """item_update deep merges nested objects."""
    from butlers.tools.general import collection_create, item_create, item_get, item_update

    await collection_create(pool, "update_deep")
    eid = await item_create(
        pool,
        "update_deep",
        {"config": {"theme": "dark", "lang": "en"}, "name": "test"},
    )

    await item_update(pool, eid, {"config": {"lang": "fr", "font_size": 14}})

    entity = await item_get(pool, eid)
    assert entity["data"]["config"] == {"theme": "dark", "lang": "fr", "font_size": 14}
    assert entity["data"]["name"] == "test"


async def test_item_update_not_found(pool):
    """item_update raises ValueError for non-existent item."""
    from butlers.tools.general import item_update

    with pytest.raises(ValueError, match="not found"):
        await item_update(pool, uuid.uuid4(), {"a": 1})


async def test_item_update_tags(pool):
    """item_update replaces tags when provided."""
    from butlers.tools.general import collection_create, item_create, item_get, item_update

    await collection_create(pool, "update_tags_coll")
    eid = await item_create(pool, "update_tags_coll", {"x": 1}, tags=["old_tag", "shared"])

    await item_update(pool, eid, {}, tags=["new_tag", "updated"])

    entity = await item_get(pool, eid)
    assert entity["tags"] == ["new_tag", "updated"]
    # Data should remain unchanged (empty merge)
    assert entity["data"] == {"x": 1}


async def test_item_update_tags_none_preserves(pool):
    """item_update with tags=None preserves existing tags."""
    from butlers.tools.general import collection_create, item_create, item_get, item_update

    await collection_create(pool, "update_tags_preserve")
    eid = await item_create(pool, "update_tags_preserve", {"x": 1}, tags=["keep_me"])

    await item_update(pool, eid, {"x": 2})  # No tags param

    entity = await item_get(pool, eid)
    assert entity["tags"] == ["keep_me"]
    assert entity["data"] == {"x": 2}


async def test_item_update_tags_to_empty(pool):
    """item_update can clear tags by passing an empty list."""
    from butlers.tools.general import collection_create, item_create, item_get, item_update

    await collection_create(pool, "update_tags_clear")
    eid = await item_create(pool, "update_tags_clear", {"x": 1}, tags=["remove_me"])

    await item_update(pool, eid, {}, tags=[])

    entity = await item_get(pool, eid)
    assert entity["tags"] == []


# ------------------------------------------------------------------
# item_delete
# ------------------------------------------------------------------


async def test_item_delete(pool):
    """item_delete removes the item."""
    from butlers.tools.general import collection_create, item_create, item_delete, item_get

    await collection_create(pool, "del_entity")
    eid = await item_create(pool, "del_entity", {"x": 1})

    await item_delete(pool, eid)
    assert await item_get(pool, eid) is None


async def test_item_delete_not_found(pool):
    """item_delete raises ValueError for non-existent item."""
    from butlers.tools.general import item_delete

    with pytest.raises(ValueError, match="not found"):
        await item_delete(pool, uuid.uuid4())


# ------------------------------------------------------------------
# item_search
# ------------------------------------------------------------------


async def test_item_search_by_collection(pool):
    """item_search filters by collection name."""
    from butlers.tools.general import collection_create, item_create, item_search

    await collection_create(pool, "search_a")
    await collection_create(pool, "search_b")
    await item_create(pool, "search_a", {"type": "alpha"})
    await item_create(pool, "search_b", {"type": "beta"})

    results = await item_search(pool, collection_name="search_a")
    assert len(results) == 1
    assert results[0]["data"]["type"] == "alpha"
    assert results[0]["collection_name"] == "search_a"


async def test_item_search_by_jsonb_query(pool):
    """item_search filters by JSONB containment."""
    from butlers.tools.general import collection_create, item_create, item_search

    await collection_create(pool, "search_jsonb")
    await item_create(pool, "search_jsonb", {"color": "red", "size": "large"})
    await item_create(pool, "search_jsonb", {"color": "blue", "size": "small"})
    await item_create(pool, "search_jsonb", {"color": "red", "size": "small"})

    results = await item_search(pool, query={"color": "red"})
    assert len(results) == 2
    for r in results:
        assert r["data"]["color"] == "red"


async def test_item_search_combined(pool):
    """item_search filters by both collection and JSONB query."""
    from butlers.tools.general import collection_create, item_create, item_search

    await collection_create(pool, "search_combo_x")
    await collection_create(pool, "search_combo_y")
    await item_create(pool, "search_combo_x", {"status": "active"})
    await item_create(pool, "search_combo_y", {"status": "active"})
    await item_create(pool, "search_combo_x", {"status": "inactive"})

    results = await item_search(
        pool, collection_name="search_combo_x", query={"status": "active"}
    )
    assert len(results) == 1
    assert results[0]["data"]["status"] == "active"
    assert results[0]["collection_name"] == "search_combo_x"


async def test_item_search_no_filters(pool):
    """item_search with no filters returns all items."""
    from butlers.tools.general import item_search

    results = await item_search(pool)
    # Just verify it returns a list (other tests may have populated items)
    assert isinstance(results, list)


async def test_item_search_by_single_tag(pool):
    """item_search filters by a single tag."""
    from butlers.tools.general import collection_create, item_create, item_search

    await collection_create(pool, "search_tag_single")
    await item_create(pool, "search_tag_single", {"name": "pasta"}, tags=["italian", "dinner"])
    await item_create(pool, "search_tag_single", {"name": "sushi"}, tags=["japanese", "dinner"])
    await item_create(
        pool, "search_tag_single", {"name": "tiramisu"}, tags=["italian", "dessert"]
    )

    results = await item_search(pool, tags=["italian"])
    assert len(results) == 2
    names = {r["data"]["name"] for r in results}
    assert names == {"pasta", "tiramisu"}


async def test_item_search_by_multiple_tags(pool):
    """item_search with multiple tags uses AND semantics (all tags must match)."""
    from butlers.tools.general import collection_create, item_create, item_search

    await collection_create(pool, "search_tag_multi")
    await item_create(pool, "search_tag_multi", {"name": "pasta"}, tags=["italian", "dinner"])
    await item_create(pool, "search_tag_multi", {"name": "tiramisu"}, tags=["italian", "dessert"])
    await item_create(
        pool, "search_tag_multi", {"name": "pizza"}, tags=["italian", "dinner", "fast"]
    )

    results = await item_search(pool, tags=["italian", "dinner"])
    assert len(results) == 2
    names = {r["data"]["name"] for r in results}
    assert names == {"pasta", "pizza"}


async def test_item_search_by_tag_no_matches(pool):
    """item_search returns empty list when no items match the tag."""
    from butlers.tools.general import item_search

    results = await item_search(pool, tags=["nonexistent_tag_xyz"])
    assert results == []


async def test_item_search_tag_and_collection(pool):
    """item_search combines tag filter with collection filter."""
    from butlers.tools.general import collection_create, item_create, item_search

    await collection_create(pool, "search_tag_coll_a")
    await collection_create(pool, "search_tag_coll_b")
    await item_create(pool, "search_tag_coll_a", {"name": "item1"}, tags=["important"])
    await item_create(pool, "search_tag_coll_b", {"name": "item2"}, tags=["important"])
    await item_create(pool, "search_tag_coll_a", {"name": "item3"}, tags=["trivial"])

    results = await item_search(pool, collection_name="search_tag_coll_a", tags=["important"])
    assert len(results) == 1
    assert results[0]["data"]["name"] == "item1"


async def test_item_search_tag_and_jsonb_query(pool):
    """item_search combines tag filter with JSONB query filter."""
    from butlers.tools.general import collection_create, item_create, item_search

    await collection_create(pool, "search_tag_jsonb")
    await item_create(pool, "search_tag_jsonb", {"status": "active"}, tags=["priority"])
    await item_create(pool, "search_tag_jsonb", {"status": "inactive"}, tags=["priority"])
    await item_create(pool, "search_tag_jsonb", {"status": "active"}, tags=["low"])

    results = await item_search(pool, query={"status": "active"}, tags=["priority"])
    assert len(results) == 1
    assert results[0]["data"]["status"] == "active"
    assert results[0]["tags"] == ["priority"]


async def test_item_search_all_filters_combined(pool):
    """item_search combines collection, JSONB query, and tag filters."""
    from butlers.tools.general import collection_create, item_create, item_search

    await collection_create(pool, "search_all_a")
    await collection_create(pool, "search_all_b")
    await item_create(pool, "search_all_a", {"color": "red"}, tags=["hot"])
    await item_create(pool, "search_all_a", {"color": "blue"}, tags=["hot"])
    await item_create(pool, "search_all_b", {"color": "red"}, tags=["hot"])
    await item_create(pool, "search_all_a", {"color": "red"}, tags=["cold"])

    results = await item_search(
        pool,
        collection_name="search_all_a",
        query={"color": "red"},
        tags=["hot"],
    )
    assert len(results) == 1
    assert results[0]["data"]["color"] == "red"
    assert results[0]["collection_name"] == "search_all_a"
    assert "hot" in results[0]["tags"]


# ------------------------------------------------------------------
# item_search returns tags in results
# ------------------------------------------------------------------


async def test_item_search_results_include_tags(pool):
    """item_search results include the tags field."""
    from butlers.tools.general import collection_create, item_create, item_search

    await collection_create(pool, "search_includes_tags")
    await item_create(pool, "search_includes_tags", {"v": 1}, tags=["alpha", "beta"])

    results = await item_search(pool, collection_name="search_includes_tags")
    assert len(results) == 1
    assert "tags" in results[0]
    assert results[0]["tags"] == ["alpha", "beta"]


# ------------------------------------------------------------------
# collection_export
# ------------------------------------------------------------------


async def test_collection_export(pool):
    """collection_export returns all items from a collection."""
    from butlers.tools.general import collection_create, collection_export, item_create

    await collection_create(pool, "export_coll")
    await item_create(pool, "export_coll", {"item": 1})
    await item_create(pool, "export_coll", {"item": 2})
    await item_create(pool, "export_coll", {"item": 3})

    exported = await collection_export(pool, "export_coll")
    assert len(exported) == 3
    items = [e["data"]["item"] for e in exported]
    assert sorted(items) == [1, 2, 3]


async def test_collection_export_empty(pool):
    """collection_export returns empty list for a collection with no items."""
    from butlers.tools.general import collection_create, collection_export

    await collection_create(pool, "empty_export")
    exported = await collection_export(pool, "empty_export")
    assert exported == []


async def test_collection_export_includes_tags(pool):
    """collection_export results include the tags field."""
    from butlers.tools.general import collection_create, collection_export, item_create

    await collection_create(pool, "export_tags_coll")
    await item_create(pool, "export_tags_coll", {"x": 1}, tags=["exported"])

    exported = await collection_export(pool, "export_tags_coll")
    assert len(exported) == 1
    assert "tags" in exported[0]
    assert exported[0]["tags"] == ["exported"]


# ------------------------------------------------------------------
# _deep_merge
# ------------------------------------------------------------------


def test_deep_merge_basic():
    """_deep_merge merges two flat dicts."""
    from butlers.tools.general import _deep_merge

    result = _deep_merge({"a": 1, "b": 2}, {"b": 3, "c": 4})
    assert result == {"a": 1, "b": 3, "c": 4}


def test_deep_merge_nested():
    """_deep_merge recursively merges nested dicts."""
    from butlers.tools.general import _deep_merge

    base = {"x": {"y": 1, "z": 2}, "top": "value"}
    override = {"x": {"z": 99, "w": 3}}
    result = _deep_merge(base, override)
    assert result == {"x": {"y": 1, "z": 99, "w": 3}, "top": "value"}


def test_deep_merge_override_replaces_non_dict():
    """_deep_merge replaces non-dict values even if base has a dict."""
    from butlers.tools.general import _deep_merge

    result = _deep_merge({"a": {"nested": 1}}, {"a": "string_now"})
    assert result == {"a": "string_now"}


def test_deep_merge_empty_override():
    """_deep_merge with empty override returns base unchanged."""
    from butlers.tools.general import _deep_merge

    result = _deep_merge({"a": 1}, {})
    assert result == {"a": 1}


# ------------------------------------------------------------------
# JSONB freeform value types
# ------------------------------------------------------------------


@pytest.mark.parametrize(
    "data",
    [
        {"string_val": "hello"},
        {"int_val": 42},
        {"float_val": 3.14},
        {"bool_val": True},
        {"null_val": None},
        {"list_val": [1, 2, 3]},
        {"nested": {"deep": {"value": "found"}}},
        {"mixed": [1, "two", {"three": 3}]},
    ],
    ids=["string", "integer", "float", "boolean", "null", "list", "nested", "mixed"],
)
async def test_item_freeform_jsonb_types(pool, data):
    """Entities accept various freeform JSONB data types."""
    from butlers.tools.general import collection_create, item_create, item_get

    coll_name = f"jsonb_types_{list(data.keys())[0]}"
    try:
        await collection_create(pool, coll_name)
    except Exception:
        pass  # May already exist from parametrize

    eid = await item_create(pool, coll_name, data)
    entity = await item_get(pool, eid)
    assert entity["data"] == data
