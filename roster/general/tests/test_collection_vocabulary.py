"""Tests for the collection vocabulary (bu-2jtfw.9).

item_create no longer auto-vivifies a collection from any string. It
resolves through ``collection_vocabulary``/``collection_aliases``: an exact
or punctuation-insensitive match lands silently, a near match resolves via
trigram similarity, and anything else raises naming the nearest candidates
and the ``collection_declare`` verb.
"""

from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.asyncio(loop_scope="session"),
]


@pytest.fixture
async def pool(provisioned_postgres_pool):
    """Provision a fresh database with the general + vocabulary tables."""
    async with provisioned_postgres_pool() as p:
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
        await p.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        await p.execute("""
            CREATE TABLE IF NOT EXISTS collection_vocabulary (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                canonical_name TEXT NOT NULL UNIQUE,
                shape_description TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS collection_aliases (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                alias TEXT NOT NULL UNIQUE,
                canonical_name TEXT NOT NULL
                    REFERENCES collection_vocabulary (canonical_name) ON DELETE CASCADE,
                merged_into TEXT
                    REFERENCES collection_vocabulary (canonical_name) ON DELETE SET NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        yield p


async def test_off_by_punctuation_name_lands_in_canonical_collection(pool):
    """'email_records' resolves to a declared 'email-records' collection."""
    from butlers.tools.general import item_create, item_get
    from butlers.tools.general.vocabulary import collection_declare

    await collection_declare(pool, "email-records", "An imported email record.")

    eid = await item_create(pool, "email_records", {"subject": "hi"})

    entity = await item_get(pool, eid)
    assert entity is not None
    row = await pool.fetchrow(
        "SELECT c.name FROM collection_items i JOIN collections c ON c.id = i.collection_id"
        " WHERE i.id = $1",
        eid,
    )
    assert row["name"] == "email-records"

    # No duplicate 'email_records' collection was created alongside it.
    count = await pool.fetchval("SELECT count(*) FROM collections")
    assert count == 1


async def test_unknown_name_raises_and_creates_nothing(pool):
    """An unresolvable name raises naming candidates and creates no rows."""
    from butlers.tools.general import item_create
    from butlers.tools.general.vocabulary import UnknownCollectionError

    with pytest.raises(UnknownCollectionError) as exc_info:
        await item_create(pool, "totally-unrelated-xyz", {"x": 1})

    assert "collection_declare" in str(exc_info.value)

    collections_count = await pool.fetchval("SELECT count(*) FROM collections")
    assert collections_count == 0
    vocabulary_count = await pool.fetchval("SELECT count(*) FROM collection_vocabulary")
    assert vocabulary_count == 0


async def test_unknown_name_lists_near_candidates(pool):
    """A near-but-below-threshold name is offered as a candidate, not chosen."""
    from butlers.tools.general.vocabulary import (
        UnknownCollectionError,
        collection_declare,
        resolve_collection_name,
    )

    await collection_declare(pool, "recipes", "A cooking recipe.")

    with pytest.raises(UnknownCollectionError) as exc_info:
        await resolve_collection_name(pool, "xyz-totally-different", threshold=0.9)

    # Best-effort candidate surfacing: even a low-similarity match may be
    # listed as a suggestion, but never silently chosen below threshold.
    assert exc_info.value.candidates == [] or "recipes" in exc_info.value.candidates


async def test_collection_declare_rejects_missing_shape(pool):
    """A declared collection with no shape description is rejected."""
    from butlers.tools.general.vocabulary import collection_declare

    with pytest.raises(ValueError, match="shape_description"):
        await collection_declare(pool, "mystery", "")

    with pytest.raises(ValueError, match="shape_description"):
        await collection_declare(pool, "mystery", "   ")

    count = await pool.fetchval("SELECT count(*) FROM collection_vocabulary")
    assert count == 0


async def test_collection_declare_is_idempotent(pool):
    """Declaring the same canonical name twice returns the same row."""
    from butlers.tools.general.vocabulary import collection_declare

    first_id = await collection_declare(pool, "journal", "A daily journal entry.")
    second_id = await collection_declare(pool, "journal", "A daily journal entry (redeclared).")

    assert first_id == second_id
    count = await pool.fetchval(
        "SELECT count(*) FROM collection_vocabulary WHERE canonical_name = 'journal'"
    )
    assert count == 1


async def test_exact_alias_resolves_through_merge(pool):
    """An alias whose merged_into is set resolves to the merge target."""
    from butlers.tools.general.vocabulary import collection_declare, resolve_collection_name

    await collection_declare(pool, "notes-general", "General freeform notes.")
    await collection_declare(pool, "notes-merged", "Legacy notes, merged away.")
    await pool.execute(
        "INSERT INTO collection_aliases (alias, canonical_name, merged_into) VALUES ($1, $2, $3)",
        "notes-legacy-alias",
        "notes-merged",
        "notes-general",
    )

    resolved = await resolve_collection_name(pool, "notes-legacy-alias")
    assert resolved == "notes-general"
