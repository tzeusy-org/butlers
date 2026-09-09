"""Repo guard: no declared collection name has both a hyphen and an
underscore variant (bu-2jtfw.9).

This is the structural fix for the mess the dossier found in production
(``banking`` / ``banking_transactions`` / ``bank-transaction-alerts`` /
``financial_transactions`` -- 121 collections, many of them punctuation
twins of each other). It cannot be checked against *live* data from this
repo's test suite -- there is no live database reachable from CI -- so this
guards the two things that actually are checkable here: the migration's own
bootstrap seed satisfies the invariant, and ``collection_declare`` refuses to
introduce a new twin going forward.
"""

from __future__ import annotations

import re

import pytest

pytestmark = [
    pytest.mark.asyncio(loop_scope="session"),
]


def _normalize(name: str) -> str:
    lowered = name.strip().lower()
    collapsed = re.sub(r"[\s_\-]+", "-", lowered)
    return re.sub(r"[^a-z0-9\-]", "", collapsed).strip("-")


@pytest.fixture
async def pool(provisioned_postgres_pool):
    async with provisioned_postgres_pool() as p:
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
        # Mirrors gen_003's bootstrap seed.
        await p.execute("""
            INSERT INTO collection_vocabulary (canonical_name, shape_description)
            VALUES
                ('notes', 'Freeform captured text with no other owner.'),
                ('facts', 'A short standalone statement captured for later recall.'),
                ('preferences', 'A stated preference captured verbatim.')
        """)
        yield p


async def test_bootstrap_seed_has_no_punctuation_twins(pool):
    """The migration's own seed collections normalize to distinct keys."""
    rows = await pool.fetch("SELECT canonical_name FROM collection_vocabulary")
    normalized = [_normalize(r["canonical_name"]) for r in rows]
    assert len(normalized) == len(set(normalized)), (
        f"punctuation-twin canonical names found: {rows}"
    )


async def test_declare_refuses_punctuation_twin_of_existing_name(pool):
    """collection_declare('banking_transactions') is refused once
    'banking-transactions' is already declared -- it cannot recreate the
    live 121's hyphen/underscore duplication going forward."""
    from butlers.tools.general.vocabulary import collection_declare

    await collection_declare(pool, "banking-transactions", "A bank transaction record.")

    with pytest.raises(ValueError, match="banking-transactions"):
        await collection_declare(pool, "banking_transactions", "A bank transaction record.")

    count = await pool.fetchval("SELECT count(*) FROM collection_vocabulary")
    assert count == 4  # 3 bootstrap seeds + the one successful declare
