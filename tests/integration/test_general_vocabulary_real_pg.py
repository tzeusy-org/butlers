"""Real-Postgres tests for the collection vocabulary's trigram resolution and
concurrent-declare race safety (bu-2jtfw.9).
"""

from __future__ import annotations

import asyncio

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


async def test_trigram_similarity_above_threshold_resolves(general_pool):
    """A near-miss spelling above the threshold auto-resolves via pg_trgm."""
    from butlers.tools.general.vocabulary import collection_declare, resolve_collection_name

    await collection_declare(general_pool, "restaurants", "A restaurant recommendation.")

    resolved = await resolve_collection_name(general_pool, "resturants", threshold=0.4)

    assert resolved == "restaurants"


async def test_trigram_similarity_below_threshold_raises(general_pool):
    """A distant name never silently auto-resolves, even if it's the best match."""
    from butlers.tools.general.vocabulary import (
        UnknownCollectionError,
        collection_declare,
        resolve_collection_name,
    )

    await collection_declare(general_pool, "restaurants", "A restaurant recommendation.")

    with pytest.raises(UnknownCollectionError):
        await resolve_collection_name(general_pool, "zzz-nothing-alike", threshold=0.4)


async def test_concurrent_declares_race_safely_to_one_row(general_pool):
    """Two concurrent collection_declare calls for the same name yield one row."""
    from butlers.tools.general.vocabulary import collection_declare

    results = await asyncio.gather(
        collection_declare(general_pool, "journal", "A daily journal entry."),
        collection_declare(general_pool, "journal", "A daily journal entry."),
    )

    assert results[0] == results[1]

    count = await general_pool.fetchval(
        "SELECT count(*) FROM collection_vocabulary WHERE canonical_name = 'journal'"
    )
    assert count == 1
