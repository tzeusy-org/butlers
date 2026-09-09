"""Real-Postgres tests for collection_items.search_vector and capture_search
(bu-2jtfw.9): the generated tsvector updates on item_update, and the GIN
index is actually used by the planner.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture
async def general_pool(postgres_container):
    from butlers.testing.migration import create_migrated_test_pool

    pool = await create_migrated_test_pool(
        postgres_container, chains=["general"], pool_schema="general"
    )
    try:
        yield pool
    finally:
        await pool.close()


async def test_search_vector_updates_on_item_update(general_pool):
    """The generated search_vector tracks item_update, not just item_create."""
    from butlers.tools.general.items import item_create, item_update
    from butlers.tools.general.vocabulary import collection_declare

    await collection_declare(general_pool, "notes", "A freeform note.")
    item_id = await item_create(general_pool, "notes", {"content": "original wording"})

    before = await general_pool.fetchval(
        "SELECT search_vector @@ plainto_tsquery('english', 'aardvark') FROM collection_items"
        " WHERE id = $1",
        item_id,
    )
    assert before is False

    await item_update(general_pool, item_id, {"content": "original wording about aardvark"})

    after = await general_pool.fetchval(
        "SELECT search_vector @@ plainto_tsquery('english', 'aardvark') FROM collection_items"
        " WHERE id = $1",
        item_id,
    )
    assert after is True


async def test_gin_index_is_used_by_planner(general_pool):
    """EXPLAIN shows the GIN search_vector index backs a capture_search query.

    A single-row table is too small for the planner to ever prefer an index
    scan on cost grounds alone, so ``enable_seqscan`` is forced off -- the
    proof this test wants is that the index is *usable* for this query
    shape, not that it wins a cost race against a trivial seq scan.
    """
    from butlers.tools.general.items import item_create
    from butlers.tools.general.vocabulary import collection_declare

    await collection_declare(general_pool, "notes", "A freeform note.")
    await item_create(
        general_pool, "notes", {"content": "a searchable structured note about penguins"}
    )
    await general_pool.execute("ANALYZE collection_items")

    async with general_pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL enable_seqscan = off")
            plan_rows = await conn.fetch(
                "EXPLAIN SELECT id FROM collection_items"
                " WHERE search_vector @@ plainto_tsquery('english', 'penguins')"
            )
    plan_text = "\n".join(r["QUERY PLAN"] for r in plan_rows)
    assert "idx_collection_items_search_vector" in plan_text


async def test_capture_search_finds_item_by_keyword(general_pool):
    """capture_search finds prose under an invented key by keyword."""
    from butlers.tools.general.capture_search import capture_search
    from butlers.tools.general.items import item_create
    from butlers.tools.general.vocabulary import collection_declare

    await collection_declare(general_pool, "notes", "A freeform note.")
    await item_create(
        general_pool, "notes", {"whatever_key_i_invented": "a structured note about the budget"}
    )
    await item_create(general_pool, "notes", {"unrelated": "something else entirely"})

    result = await capture_search(general_pool, "structured note")

    assert result["degraded"] is False
    contents = [item["data"] for item in result["items"]]
    assert any("structured note" in str(c) for c in contents)


async def test_capture_search_respects_limit_and_cursor(general_pool):
    """capture_search honours limit and returns a usable next_cursor."""
    from butlers.tools.general.capture_search import capture_search
    from butlers.tools.general.items import item_create
    from butlers.tools.general.vocabulary import collection_declare

    await collection_declare(general_pool, "notes", "A freeform note.")
    for i in range(5):
        await item_create(general_pool, "notes", {"content": f"keyword-match item {i}"})

    first_page = await capture_search(general_pool, "keyword-match", limit=2)
    assert len(first_page["items"]) == 2
    assert first_page["has_more"] is True
    assert first_page["next_cursor"] is not None

    second_page = await capture_search(
        general_pool, "keyword-match", limit=2, cursor=first_page["next_cursor"]
    )
    assert len(second_page["items"]) == 2
    first_ids = {i["id"] for i in first_page["items"]}
    second_ids = {i["id"] for i in second_page["items"]}
    assert first_ids.isdisjoint(second_ids)


async def test_capture_search_date_window_filters(general_pool):
    """capture_search's date_from/date_to bound the results."""
    from datetime import UTC, datetime, timedelta

    from butlers.tools.general.capture_search import capture_search
    from butlers.tools.general.items import item_create
    from butlers.tools.general.vocabulary import collection_declare

    await collection_declare(general_pool, "notes", "A freeform note.")
    item_id = await item_create(general_pool, "notes", {"content": "old dated keyword-window"})
    await general_pool.execute(
        "UPDATE collection_items SET created_at = $2 WHERE id = $1",
        item_id,
        datetime(2020, 1, 1, tzinfo=UTC),
    )
    await item_create(general_pool, "notes", {"content": "recent keyword-window"})

    result = await capture_search(
        general_pool, "keyword-window", date_from=datetime.now(UTC) - timedelta(days=1)
    )

    assert len(result["items"]) == 1
    assert "recent" in str(result["items"][0]["data"])
