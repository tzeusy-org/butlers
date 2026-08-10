"""Database-backed lifecycle regressions for episode consolidation.

The claimant is deliberately tested against a real PostgreSQL transaction.  A
mock cannot prove that ``FOR UPDATE SKIP LOCKED`` and the lease-owner fence
protect a retrying worker from a concurrent scheduler invocation.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import asyncpg
import pytest

from butlers.modules.memory.consolidation import (
    BASE_RETRY_SECONDS,
    MAX_CONSOLIDATION_ATTEMPTS,
    _mark_group_failed,
    run_consolidation,
)
from butlers.modules.memory.consolidation_executor import execute_consolidation
from butlers.modules.memory.consolidation_parser import ConsolidationResult
from conftest import docker_available

pytestmark = [
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


_LIFECYCLE_SCHEMA_SQL = """
CREATE TABLE episodes (
    id UUID PRIMARY KEY,
    butler TEXT NOT NULL,
    content TEXT NOT NULL,
    importance DOUBLE PRECISION NOT NULL DEFAULT 5.0,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    tenant_id TEXT NOT NULL DEFAULT 'shared',
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
    tenant_id TEXT,
    actor_butler TEXT,
    memory_type TEXT,
    memory_id UUID,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


_ARTIFACT_SCHEMA_SQL = """
CREATE TABLE memory_policies (
    retention_class TEXT PRIMARY KEY,
    ttl_days INTEGER NOT NULL
);

INSERT INTO memory_policies (retention_class, ttl_days)
VALUES ('transient', 7);

CREATE TABLE facts (
    id UUID PRIMARY KEY,
    subject TEXT NOT NULL DEFAULT '',
    predicate TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    permanence TEXT NOT NULL DEFAULT 'standard',
    entity_id UUID,
    valid_at TIMESTAMPTZ,
    validity TEXT NOT NULL DEFAULT 'active',
    source_butler TEXT NOT NULL DEFAULT 'memory',
    tenant_id TEXT NOT NULL DEFAULT 'shared',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_confirmed_at TIMESTAMPTZ
);

CREATE TABLE rules (
    id UUID PRIMARY KEY,
    content TEXT NOT NULL,
    embedding TEXT NOT NULL,
    search_vector TSVECTOR NOT NULL,
    scope TEXT NOT NULL,
    maturity TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL,
    decay_rate DOUBLE PRECISION NOT NULL,
    effectiveness_score DOUBLE PRECISION NOT NULL,
    applied_count INTEGER NOT NULL,
    success_count INTEGER NOT NULL,
    harmful_count INTEGER NOT NULL,
    source_episode_id UUID,
    source_butler TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    tags JSONB NOT NULL,
    metadata JSONB NOT NULL,
    tenant_id TEXT NOT NULL,
    request_id TEXT,
    retention_class TEXT NOT NULL,
    sensitivity TEXT NOT NULL,
    embedding_model_version TEXT NOT NULL
);

CREATE TABLE memory_links (
    source_type TEXT NOT NULL,
    source_id UUID NOT NULL,
    target_type TEXT NOT NULL,
    target_id UUID NOT NULL,
    relation TEXT NOT NULL,
    UNIQUE (source_type, source_id, target_type, target_id)
);
"""


async def _install_lifecycle_schema(pool) -> None:
    await pool.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    await pool.execute(_LIFECYCLE_SCHEMA_SQL)


async def _install_artifact_schema(pool) -> None:
    await pool.execute(_ARTIFACT_SCHEMA_SQL)


class _StaticEmbeddingEngine:
    model_name = "consolidation-lifecycle-test"

    def embed(self, _text: str) -> list[float]:
        return [0.0]


class _CompletedThenReplacedSpawner:
    """Complete a runtime, then let a real second claimant replace its lease."""

    def __init__(self, pool, episode_id: uuid.UUID, confirmation_id: uuid.UUID) -> None:
        self._pool = pool
        self._episode_id = episode_id
        self._confirmation_id = confirmation_id
        self.replacement_stats: dict | None = None

    async def trigger(self, *, prompt: str, trigger_source: str) -> SimpleNamespace:
        assert prompt
        assert trigger_source == "schedule:consolidation"

        completed_runtime = SimpleNamespace(
            success=True,
            output=json.dumps(
                {
                    "new_rules": [
                        {
                            "content": "Persist only while the episode claim remains current.",
                            "evidence_episode_ids": [str(self._episode_id)],
                        }
                    ],
                    "confirmations": [str(self._confirmation_id)],
                }
            ),
        )

        await self._pool.execute(
            "UPDATE episodes SET leased_until = now() - interval '1 second' WHERE id = $1",
            self._episode_id,
        )
        self.replacement_stats = await run_consolidation(
            pool=self._pool,
            embedding_engine=_StaticEmbeddingEngine(),
            cc_spawner=None,
            batch_size=1,
        )
        return completed_runtime


async def _insert_episode(
    pool,
    *,
    status: str = "pending",
    attempts: int = 0,
    retry_at: datetime | None = None,
    leased_until: datetime | None = None,
    leased_by: str | None = None,
    last_error: str | None = None,
    dead_letter_reason: str | None = None,
    content: str | None = None,
) -> uuid.UUID:
    episode_id = uuid.uuid4()
    await pool.execute(
        """
        INSERT INTO episodes (
            id, butler, content, consolidation_status, consolidation_attempts,
            next_consolidation_retry_at, leased_until, leased_by,
            last_consolidation_error, dead_letter_reason
        )
        VALUES ($1, 'memory', $2, $3, $4, $5, $6, $7, $8, $9)
        """,
        episode_id,
        content or str(episode_id),
        status,
        attempts,
        retry_at,
        leased_until,
        leased_by,
        last_error,
        dead_letter_reason,
    )
    return episode_id


async def _episode_lifecycle(pool, episode_id: uuid.UUID):
    return await pool.fetchrow(
        """
        SELECT consolidation_status, consolidation_attempts,
               last_consolidation_error, leased_until, leased_by,
               dead_letter_reason, next_consolidation_retry_at, consolidated
        FROM episodes
        WHERE id = $1
        """,
        episode_id,
    )


async def test_claims_pending_and_only_retry_eligible_failed_episodes(
    provisioned_postgres_pool,
) -> None:
    """A scheduler claims due retry rows but never terminal or premature rows."""
    async with provisioned_postgres_pool() as pool:
        await _install_lifecycle_schema(pool)
        now = datetime.now(UTC)

        pending = await _insert_episode(pool, retry_at=now + timedelta(days=1))
        pending_leased = await _insert_episode(
            pool,
            leased_until=now + timedelta(minutes=5),
            leased_by="other-worker",
        )
        failed_due = await _insert_episode(
            pool,
            status="failed",
            attempts=1,
            retry_at=now - timedelta(seconds=1),
        )
        failed_future = await _insert_episode(
            pool,
            status="failed",
            attempts=1,
            retry_at=now + timedelta(minutes=5),
        )
        failed_without_retry = await _insert_episode(pool, status="failed", attempts=1)
        failed_exhausted = await _insert_episode(
            pool,
            status="failed",
            attempts=MAX_CONSOLIDATION_ATTEMPTS,
            retry_at=now - timedelta(seconds=1),
        )
        dead_letter = await _insert_episode(
            pool,
            status="dead_letter",
            attempts=MAX_CONSOLIDATION_ATTEMPTS,
            retry_at=now - timedelta(days=1),
            dead_letter_reason="terminal",
        )
        dead_letter_expired_lease = await _insert_episode(
            pool,
            status="dead_letter",
            attempts=MAX_CONSOLIDATION_ATTEMPTS,
            leased_until=now - timedelta(seconds=1),
            leased_by="abandoned-worker",
            dead_letter_reason="terminal",
        )
        consolidated = await _insert_episode(
            pool,
            status="consolidated",
            leased_until=now - timedelta(seconds=1),
            leased_by="abandoned-worker",
        )

        stats = await run_consolidation(
            pool=pool,
            embedding_engine=object(),
            cc_spawner=None,
            batch_size=20,
        )

        assert stats["episodes_processed"] == 2
        assert (await _episode_lifecycle(pool, pending))["leased_by"]
        assert (await _episode_lifecycle(pool, failed_due))["leased_by"]
        assert (await _episode_lifecycle(pool, pending_leased))["leased_by"] == "other-worker"
        for episode_id in (
            failed_future,
            failed_without_retry,
            failed_exhausted,
            dead_letter,
            dead_letter_expired_lease,
            consolidated,
        ):
            assert (await _episode_lifecycle(pool, episode_id))["leased_by"] in {
                None,
                "abandoned-worker",
            }


async def test_private_memory_claim_path_does_not_retry_failed_episodes(
    provisioned_postgres_pool,
) -> None:
    """The private-memory call path preserves old pending-only recovery behavior."""
    async with provisioned_postgres_pool() as pool:
        await _install_lifecycle_schema(pool)
        now = datetime.now(UTC)
        pending = await _insert_episode(pool)
        failed_due = await _insert_episode(
            pool,
            status="failed",
            attempts=1,
            retry_at=now - timedelta(seconds=1),
        )

        stats = await run_consolidation(
            pool=pool,
            embedding_engine=object(),
            cc_spawner=None,
            batch_size=20,
            retry_failed=False,
        )

        assert stats["episodes_processed"] == 1
        assert (await _episode_lifecycle(pool, pending))["leased_by"]
        assert (await _episode_lifecycle(pool, failed_due))["leased_by"] is None


async def test_due_failed_claim_is_race_safe_between_scheduler_runs(
    provisioned_postgres_pool,
) -> None:
    """Concurrent runs cannot both claim one due failed episode."""
    async with provisioned_postgres_pool(max_pool_size=3) as pool:
        await _install_lifecycle_schema(pool)
        await _insert_episode(
            pool,
            status="failed",
            attempts=1,
            retry_at=datetime.now(UTC) - timedelta(seconds=1),
        )

        first, second = await asyncio.gather(
            run_consolidation(pool, object(), cc_spawner=None, batch_size=1),
            run_consolidation(pool, object(), cc_spawner=None, batch_size=1),
        )

        assert sorted((first["episodes_processed"], second["episodes_processed"])) == [0, 1]
        assert await pool.fetchval("SELECT count(*) FROM episodes WHERE leased_by IS NOT NULL") == 1


async def test_failure_transition_is_fenced_sanitized_and_retryable(
    provisioned_postgres_pool,
) -> None:
    """A worker may only fail its own active lease, without persisting raw errors."""
    async with provisioned_postgres_pool() as pool:
        await _install_lifecycle_schema(pool)
        episode_id = await _insert_episode(
            pool,
            leased_until=datetime.now(UTC) + timedelta(minutes=5),
            leased_by="claim-a",
        )

        transition_started_at = datetime.now(UTC)
        await _mark_group_failed(
            pool,
            [episode_id],
            "Bearer live-secret must not reach lifecycle storage",
            tenant_id="tenant-a",
            claim_token="claim-a",
        )

        row = await _episode_lifecycle(pool, episode_id)
        assert row["consolidation_status"] == "failed"
        assert row["consolidation_attempts"] == 1
        assert row["last_consolidation_error"] == "Consolidation execution failed."
        assert row["leased_until"] is None
        assert row["leased_by"] is None
        assert row["dead_letter_reason"] is None
        expected_retry_at = transition_started_at + timedelta(seconds=2 * BASE_RETRY_SECONDS)
        assert (
            expected_retry_at
            <= row["next_consolidation_retry_at"]
            <= expected_retry_at + timedelta(seconds=5)
        )

        event = await pool.fetchrow(
            "SELECT event_type, tenant_id, payload FROM memory_events WHERE memory_id = $1",
            episode_id,
        )
        assert dict(event) == {
            "event_type": "episode_consolidation_failed",
            "tenant_id": "tenant-a",
            "payload": {"attempts": 1, "outcome": "retry_scheduled"},
        }
        assert "secret" not in str(event["payload"]).lower()


async def test_failure_transition_fails_closed_when_its_event_cannot_persist(
    provisioned_postgres_pool,
) -> None:
    """Lifecycle state cannot advance without its durable audit event."""
    async with provisioned_postgres_pool() as pool:
        await _install_lifecycle_schema(pool)
        episode_id = await _insert_episode(
            pool,
            leased_until=datetime.now(UTC) + timedelta(minutes=5),
            leased_by="claim-a",
        )
        await pool.execute("DROP TABLE memory_events")

        with pytest.raises(asyncpg.UndefinedTableError, match="memory_events"):
            await _mark_group_failed(
                pool,
                [episode_id],
                "execution_error",
                tenant_id="tenant-a",
                claim_token="claim-a",
            )

        row = await _episode_lifecycle(pool, episode_id)
        assert row["consolidation_status"] == "pending"
        assert row["consolidation_attempts"] == 0
        assert row["leased_by"] == "claim-a"


async def test_expired_claim_cannot_persist_a_stale_failure(
    provisioned_postgres_pool,
) -> None:
    """An expired worker lease cannot overwrite a replacement claimant's state."""
    async with provisioned_postgres_pool() as pool:
        await _install_lifecycle_schema(pool)
        episode_id = await _insert_episode(
            pool,
            leased_until=datetime.now(UTC) - timedelta(seconds=1),
            leased_by="stale-claim",
        )

        await _mark_group_failed(
            pool,
            [episode_id],
            "execution_error",
            tenant_id="tenant-a",
            claim_token="stale-claim",
        )

        row = await _episode_lifecycle(pool, episode_id)
        assert row["consolidation_status"] == "pending"
        assert row["consolidation_attempts"] == 0
        assert row["leased_by"] == "stale-claim"
        assert await pool.fetchval("SELECT count(*) FROM memory_events") == 0


async def test_terminal_failure_dead_letters_once_and_never_replays(
    provisioned_postgres_pool,
) -> None:
    """A stale or repeated terminal write cannot increment or replay a dead letter."""
    async with provisioned_postgres_pool(max_pool_size=3) as pool:
        await _install_lifecycle_schema(pool)
        episode_id = await _insert_episode(
            pool,
            status="failed",
            attempts=MAX_CONSOLIDATION_ATTEMPTS - 1,
            retry_at=datetime.now(UTC) - timedelta(seconds=1),
            leased_until=datetime.now(UTC) + timedelta(minutes=5),
            leased_by="claim-a",
        )

        await asyncio.gather(
            _mark_group_failed(
                pool,
                [episode_id],
                "secret terminal diagnostic",
                tenant_id="tenant-a",
                claim_token="claim-a",
            ),
            _mark_group_failed(
                pool,
                [episode_id],
                "secret terminal diagnostic",
                tenant_id="tenant-a",
                claim_token="claim-a",
            ),
        )

        row = await _episode_lifecycle(pool, episode_id)
        assert row["consolidation_status"] == "dead_letter"
        assert row["consolidation_attempts"] == MAX_CONSOLIDATION_ATTEMPTS
        assert row["last_consolidation_error"] == "Consolidation execution failed."
        assert row["dead_letter_reason"] == "Consolidation execution failed."
        assert row["next_consolidation_retry_at"] is None
        assert row["leased_until"] is None
        assert row["leased_by"] is None
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM memory_events WHERE memory_id = $1",
                episode_id,
            )
            == 1
        )
        event = await pool.fetchrow(
            "SELECT event_type, payload FROM memory_events WHERE memory_id = $1",
            episode_id,
        )
        assert dict(event) == {
            "event_type": "episode_consolidation_dead_letter",
            "payload": {"attempts": MAX_CONSOLIDATION_ATTEMPTS, "outcome": "dead_letter"},
        }
        assert "secret" not in str(event["payload"]).lower()


async def test_success_transition_is_fenced_and_clears_retry_lifecycle_state(
    provisioned_postgres_pool,
) -> None:
    """Only the current claimant can finalize a group and clear retry evidence."""
    async with provisioned_postgres_pool() as pool:
        await _install_lifecycle_schema(pool)
        episode_id = await _insert_episode(
            pool,
            status="failed",
            attempts=2,
            retry_at=datetime.now(UTC) - timedelta(seconds=1),
            leased_until=datetime.now(UTC) + timedelta(minutes=5),
            leased_by="claim-a",
            last_error="previous safe failure",
            dead_letter_reason="stale terminal reason",
        )

        lost = await execute_consolidation(
            pool=pool,
            embedding_engine=object(),
            parsed=ConsolidationResult(),
            source_episode_ids=[episode_id],
            butler_name="memory",
            claim_token="claim-b",
        )
        assert lost["episodes_consolidated"] == 0
        assert lost["errors"] == ["Consolidation lease was lost before episodes could be finalized"]
        assert (await _episode_lifecycle(pool, episode_id))["consolidation_status"] == "failed"

        completed = await execute_consolidation(
            pool=pool,
            embedding_engine=object(),
            parsed=ConsolidationResult(),
            source_episode_ids=[episode_id],
            butler_name="memory",
            claim_token="claim-a",
        )
        assert completed["episodes_consolidated"] == 1
        assert completed["errors"] == []
        row = await _episode_lifecycle(pool, episode_id)
        assert row["consolidation_status"] == "consolidated"
        assert row["consolidated"] is True
        assert row["leased_until"] is None
        assert row["leased_by"] is None
        assert row["last_consolidation_error"] is None
        assert row["dead_letter_reason"] is None
        assert row["next_consolidation_retry_at"] is None
        event = await pool.fetchrow(
            "SELECT event_type, payload FROM memory_events WHERE memory_id = $1",
            episode_id,
        )
        assert dict(event) == {
            "event_type": "episode_consolidated",
            "payload": {"outcome": "consolidated"},
        }


async def test_replaced_claim_cannot_persist_artifacts_or_terminal_lifecycle(
    provisioned_postgres_pool,
) -> None:
    """A completed runtime loses every write when a later claimant owns its episodes."""
    async with provisioned_postgres_pool(max_pool_size=3) as pool:
        await _install_lifecycle_schema(pool)
        await _install_artifact_schema(pool)
        episode_id = await _insert_episode(pool)
        confirmation_id = uuid.uuid4()
        await pool.execute("INSERT INTO facts (id) VALUES ($1)", confirmation_id)
        spawner = _CompletedThenReplacedSpawner(pool, episode_id, confirmation_id)

        displaced = await run_consolidation(
            pool=pool,
            embedding_engine=_StaticEmbeddingEngine(),
            cc_spawner=spawner,
            batch_size=1,
        )

        assert spawner.replacement_stats is not None
        assert spawner.replacement_stats["episodes_processed"] == 1
        assert displaced["episodes_consolidated"] == 0
        assert displaced["groups_consolidated"] == 0
        assert await pool.fetchval("SELECT count(*) FROM rules") == 0
        assert await pool.fetchval("SELECT count(*) FROM memory_links") == 0
        assert await pool.fetchval("SELECT count(*) FROM memory_events") == 0
        assert (
            await pool.fetchval(
                "SELECT last_confirmed_at FROM facts WHERE id = $1", confirmation_id
            )
        ) is None

        episode = await _episode_lifecycle(pool, episode_id)
        assert episode["consolidation_status"] == "pending"
        assert episode["consolidated"] is False
        assert episode["leased_by"] is not None
        assert episode["leased_until"] > datetime.now(UTC)
