"""Real-Postgres guarantees of the dead-letter requeue transition (bu-kqnum.14.3).

The transition is one schema-qualified conditional UPDATE plus its lifecycle event in the
same transaction, so concurrent requeues cannot both win. A mock cannot prove that.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid

import pytest

from butlers.modules.memory.storage import EpisodeNotDeadLetterError, retry_dead_letter_episode

pytestmark = [
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE TABLE episodes (
    id UUID PRIMARY KEY,
    butler TEXT NOT NULL,
    session_id UUID,
    content TEXT NOT NULL,
    importance DOUBLE PRECISION NOT NULL DEFAULT 5.0,
    reference_count INTEGER NOT NULL DEFAULT 0,
    last_referenced_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    consolidated BOOLEAN NOT NULL DEFAULT false,
    consolidation_status TEXT NOT NULL DEFAULT 'pending',
    consolidation_attempts INTEGER NOT NULL DEFAULT 0,
    last_consolidation_error TEXT,
    leased_until TIMESTAMPTZ,
    leased_by TEXT,
    dead_letter_reason TEXT,
    next_consolidation_retry_at TIMESTAMPTZ
);
CREATE TABLE memory_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type TEXT NOT NULL,
    actor TEXT,
    memory_type TEXT,
    memory_id UUID,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


async def _dead_letter(pool) -> uuid.UUID:
    episode_id = uuid.uuid4()
    await pool.execute(
        """
        INSERT INTO episodes (id, butler, content, consolidation_status, consolidation_attempts,
                              last_consolidation_error, dead_letter_reason, leased_by)
        VALUES ($1, 'atlas', 'c', 'dead_letter', 5, 'Consolidation execution failed.',
                'terminal', 'abandoned-worker')
        """,
        episode_id,
    )
    return episode_id


async def test_concurrent_requeues_produce_exactly_one_transition_and_one_event(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_SCHEMA_SQL)
        episode_id = await _dead_letter(pool)

        results = await asyncio.gather(
            *(
                retry_dead_letter_episode(pool, episode_id, memory_schema="public")
                for _ in range(8)
            ),
            return_exceptions=True,
        )

        winners = [r for r in results if isinstance(r, dict)]
        losers = [r for r in results if isinstance(r, EpisodeNotDeadLetterError)]
        assert len(winners) == 1 and len(losers) == 7
        assert winners[0]["consolidation_status"] == "pending"
        assert winners[0]["consolidation_attempts"] == 0
        assert "leased_by" not in winners[0] and "leased_until" not in winners[0]
        events = await pool.fetch("SELECT event_type, payload FROM memory_events")
        assert [e["event_type"] for e in events] == ["episode_consolidation_requeued"]
        assert "abandoned-worker" not in str(events[0]["payload"])


async def test_requeue_clears_the_retry_state_and_the_lease(provisioned_postgres_pool) -> None:
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_SCHEMA_SQL)
        episode_id = await _dead_letter(pool)

        await retry_dead_letter_episode(pool, episode_id, memory_schema="public")

        row = await pool.fetchrow("SELECT * FROM episodes WHERE id = $1", episode_id)
        assert row["consolidation_status"] == "pending"
        assert row["consolidation_attempts"] == 0
        assert row["dead_letter_reason"] is None
        assert row["last_consolidation_error"] is None
        assert row["next_consolidation_retry_at"] is None
        assert row["leased_by"] is None and row["leased_until"] is None


async def test_a_non_dead_letter_episode_changes_nothing_and_writes_no_event(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_SCHEMA_SQL)
        episode_id = uuid.uuid4()
        await pool.execute(
            "INSERT INTO episodes (id, butler, content, consolidation_status,"
            " consolidation_attempts) VALUES ($1, 'atlas', 'c', 'failed', 2)",
            episode_id,
        )

        with pytest.raises(EpisodeNotDeadLetterError) as exc:
            await retry_dead_letter_episode(pool, episode_id, memory_schema="public")

        assert exc.value.current_status == "failed"
        row = await pool.fetchrow("SELECT * FROM episodes WHERE id = $1", episode_id)
        assert (row["consolidation_status"], row["consolidation_attempts"]) == ("failed", 2)
        assert await pool.fetchval("SELECT count(*) FROM memory_events") == 0
