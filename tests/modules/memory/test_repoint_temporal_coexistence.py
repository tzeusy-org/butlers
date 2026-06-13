"""Regression (bu-vvvcu): narrative-store merge repoint must preserve temporal
coexistence, matching ``store_fact`` semantics.

Background: ``_repoint_facts_on_pool`` (entity merge for the memory/narrative
store) used the conflict key ``(entity_id, scope, predicate)`` with
higher-conf-wins, ignoring ``valid_at``/``content``. ``store_fact`` itself only
supersedes PROPERTY facts (``valid_at IS NULL``); TEMPORAL facts
(``valid_at IS NOT NULL``) always coexist as independent active rows and never
supersede each other or property facts. The old repoint contradicted that: a
source temporal fact whose predicate the target also held got superseded instead
of coexisting.

Fix under test: ``_repoint_facts_on_pool`` skips supersession entirely when
either the source or the target row is a temporal fact (``valid_at IS NOT NULL``)
— temporal facts are always repointed and coexist; only property-vs-property
collisions resolve by confidence.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime

import asyncpg
import pytest

from butlers.modules.memory.tools.entities import _repoint_facts_on_pool

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]


@pytest.fixture
async def pool(provisioned_postgres_pool):
    """Minimal narrative ``facts`` table surface used by _repoint_facts_on_pool."""
    async with provisioned_postgres_pool(min_pool_size=2, max_pool_size=8) as p:
        await p.execute("""
            CREATE TABLE IF NOT EXISTS facts (
                id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                entity_id        UUID,
                object_entity_id UUID,
                predicate        TEXT        NOT NULL,
                content          TEXT,
                confidence       FLOAT       NOT NULL DEFAULT 1.0,
                scope            TEXT        NOT NULL DEFAULT 'global',
                valid_at         TIMESTAMPTZ,
                validity         TEXT        NOT NULL DEFAULT 'active',
                supersedes_id    UUID,
                created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        yield p


async def _add_fact(
    pool: asyncpg.Pool,
    *,
    entity_id: uuid.UUID,
    predicate: str,
    content: str,
    confidence: float = 1.0,
    scope: str = "global",
    valid_at: datetime | None = None,
) -> uuid.UUID:
    return await pool.fetchval(
        """
        INSERT INTO facts (entity_id, predicate, content, confidence, scope, valid_at)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING id
        """,
        entity_id,
        predicate,
        content,
        confidence,
        scope,
        valid_at,
    )


class TestRepointTemporalCoexistence:
    async def test_temporal_facts_coexist_not_collapsed(self, pool):
        """Source + target each hold a temporal fact for the same
        (scope, predicate) but DIFFERENT valid_at — both must remain active
        after merge (coexistence), not collapse to one."""
        src = uuid.uuid4()
        tgt = uuid.uuid4()

        # Target already holds a temporal "lived_in" fact for 2010.
        await _add_fact(
            pool,
            entity_id=tgt,
            predicate="lived_in",
            content="Boston",
            confidence=0.9,
            valid_at=datetime(2010, 1, 1, tzinfo=UTC),
        )
        # Source holds a temporal "lived_in" fact for a DIFFERENT year (2015),
        # lower confidence. Under the old (entity_id, scope, predicate) key with
        # higher-conf-wins, this source row would be superseded — wrong.
        await _add_fact(
            pool,
            entity_id=src,
            predicate="lived_in",
            content="Seattle",
            confidence=0.4,
            valid_at=datetime(2015, 1, 1, tzinfo=UTC),
        )

        counts = await _repoint_facts_on_pool(pool, src, tgt)

        active = await pool.fetch(
            """
            SELECT content, valid_at FROM facts
            WHERE entity_id = $1 AND predicate = 'lived_in' AND validity = 'active'
            ORDER BY valid_at
            """,
            tgt,
        )
        assert {r["content"] for r in active} == {
            "Boston",
            "Seattle",
        }, f"temporal facts must coexist, not collapse: {active}"
        assert counts["facts_superseded"] == 0
        assert counts["facts_repointed"] == 1

    async def test_property_facts_still_supersede_by_confidence(self, pool):
        """Property facts (valid_at IS NULL) for the same (scope, predicate) still
        resolve by confidence — higher-conf source wins, target superseded."""
        src = uuid.uuid4()
        tgt = uuid.uuid4()

        await _add_fact(
            pool, entity_id=tgt, predicate="job_title", content="Engineer", confidence=0.5
        )
        await _add_fact(
            pool, entity_id=src, predicate="job_title", content="Manager", confidence=0.9
        )

        counts = await _repoint_facts_on_pool(pool, src, tgt)

        active = await pool.fetch(
            """
            SELECT content FROM facts
            WHERE entity_id = $1 AND predicate = 'job_title' AND validity = 'active'
            """,
            tgt,
        )
        assert len(active) == 1
        assert active[0]["content"] == "Manager"
        assert counts["facts_superseded"] == 1

    async def test_property_target_temporal_source_coexist(self, pool):
        """A source TEMPORAL fact must coexist with a target PROPERTY fact of the
        same (scope, predicate) — store_fact never lets temporal facts supersede
        property facts (or vice versa)."""
        src = uuid.uuid4()
        tgt = uuid.uuid4()

        await _add_fact(pool, entity_id=tgt, predicate="title", content="current", confidence=0.9)
        await _add_fact(
            pool,
            entity_id=src,
            predicate="title",
            content="historical",
            confidence=0.5,
            valid_at=datetime(2018, 6, 1, tzinfo=UTC),
        )

        counts = await _repoint_facts_on_pool(pool, src, tgt)

        active = await pool.fetch(
            """
            SELECT content FROM facts
            WHERE entity_id = $1 AND predicate = 'title' AND validity = 'active'
            ORDER BY content
            """,
            tgt,
        )
        assert {r["content"] for r in active} == {"current", "historical"}
        assert counts["facts_superseded"] == 0
        assert counts["facts_repointed"] == 1
