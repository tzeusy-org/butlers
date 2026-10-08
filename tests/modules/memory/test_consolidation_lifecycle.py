"""Database-backed lifecycle regressions for episode consolidation.

The claimant is deliberately tested against a real PostgreSQL transaction.  A
mock cannot prove that ``FOR UPDATE SKIP LOCKED`` and the lease-owner fence
protect a retrying worker from a concurrent scheduler invocation.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

from butlers.background import dispatch_scheduled_task
from butlers.modules.memory import MemoryModule, MemoryModuleConfig
from butlers.modules.memory.consolidation import (
    BASE_RETRY_SECONDS,
    MAX_CONSOLIDATION_ATTEMPTS,
    _mark_group_failed,
    run_consolidation,
)
from butlers.modules.memory.consolidation_executor import execute_consolidation
from butlers.modules.memory.consolidation_parser import (
    ConsolidationResult,
    parse_consolidation_output,
)
from butlers.modules.memory.storage import (
    EpisodeNotDeadLetterError,
    retry_dead_letter_episode,
)

docker_available = shutil.which("docker") is not None

pytestmark = [
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


_LIFECYCLE_SCHEMA_SQL = """
CREATE TABLE episodes (
    content_authority TEXT, authority_entity_id UUID,
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
    content_authority TEXT, authority_entity_id UUID,
    id UUID PRIMARY KEY,
    subject TEXT NOT NULL DEFAULT '',
    predicate TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    embedding TEXT,
    search_vector TSVECTOR,
    importance DOUBLE PRECISION NOT NULL DEFAULT 5.0,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    decay_rate DOUBLE PRECISION NOT NULL DEFAULT 0.01,
    permanence TEXT NOT NULL DEFAULT 'standard',
    source_episode_id UUID,
    supersedes_id UUID,
    scope TEXT NOT NULL DEFAULT 'global',
    entity_id UUID,
    object_entity_id UUID,
    valid_at TIMESTAMPTZ,
    validity TEXT NOT NULL DEFAULT 'active',
    source_butler TEXT NOT NULL DEFAULT 'memory',
    tenant_id TEXT NOT NULL DEFAULT 'shared',
    request_id TEXT,
    idempotency_key TEXT,
    observed_at TIMESTAMPTZ,
    retention_class TEXT NOT NULL DEFAULT 'operational',
    sensitivity TEXT NOT NULL DEFAULT 'normal',
    embedding_model_version TEXT NOT NULL DEFAULT 'consolidation-lifecycle-test',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_confirmed_at TIMESTAMPTZ,
    tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE predicate_registry (
    name TEXT PRIMARY KEY,
    is_edge BOOLEAN NOT NULL DEFAULT false,
    is_temporal BOOLEAN NOT NULL DEFAULT false,
    status TEXT NOT NULL DEFAULT 'active',
    superseded_by TEXT,
    expected_subject_type TEXT,
    expected_object_type TEXT,
    inverse_of TEXT,
    is_symmetric BOOLEAN NOT NULL DEFAULT false,
    aliases TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    description TEXT,
    usage_count INTEGER NOT NULL DEFAULT 0,
    last_used_at TIMESTAMPTZ
);

INSERT INTO predicate_registry (name) VALUES ('test_property');

CREATE TABLE rules (
    content_authority TEXT, authority_entity_id UUID, endorsed_at TIMESTAMPTZ, endorsed_by UUID,
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


class _BlockingUnsuccessfulSpawner:
    """Hold a scheduled runtime after its claimant has acquired the lease."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def trigger(self, *, prompt: str, trigger_source: str) -> SimpleNamespace:
        assert prompt
        assert trigger_source == "schedule:consolidation"
        self.started.set()
        await self.release.wait()
        return SimpleNamespace(success=False, output=None)


class _BlockingSuccessfulSpawner:
    """Hold a live scheduled runtime until its pending-episode claim is observable."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.trigger_sources: list[str] = []

    async def trigger(self, *, prompt: str, trigger_source: str) -> SimpleNamespace:
        assert prompt
        self.trigger_sources.append(trigger_source)
        self.started.set()
        await self.release.wait()
        return SimpleNamespace(success=True, output="{}", error=None)


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


@pytest.mark.pg_clock
async def test_scheduled_run_claims_pending_and_only_retry_eligible_failed_episodes(
    provisioned_postgres_pool,
) -> None:
    """A live scheduler claims due retries but never terminal or premature rows."""
    async with provisioned_postgres_pool() as pool:
        await _install_lifecycle_schema(pool)
        await _install_artifact_schema(pool)
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

        spawner = _BlockingUnsuccessfulSpawner()
        scheduled_run = asyncio.create_task(
            run_consolidation(
                pool=pool,
                embedding_engine=_StaticEmbeddingEngine(),
                cc_spawner=spawner,
                batch_size=20,
            )
        )
        await asyncio.wait_for(spawner.started.wait(), timeout=5)

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

        spawner.release.set()
        stats = await scheduled_run
        assert stats["episodes_processed"] == 2


@pytest.fixture
def native_chronicler_memory_url(postgres_container):
    """Real separate configured domain/Memory schemas, using governing migrations.

    This claimant fixture is migration-created compatibility evidence; its
    connections do not stand in for the separate runtime-role authority proof.
    """
    from butlers.testing.migration import create_migrated_test_db, migration_db_name

    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "chronicler", "memory"],
        schemas={"core": "chronicler", "chronicler": "chronicler", "memory": "chronicler_mem"},
    )


@pytest.fixture
async def native_chronicler_memory_pools(native_chronicler_memory_url):
    from butlers.db import register_jsonb_codec

    pools = []
    try:
        for schema in ("chronicler_mem", "chronicler"):
            pools.append(
                await asyncpg.create_pool(
                    native_chronicler_memory_url,
                    min_size=1,
                    max_size=3,
                    init=register_jsonb_codec,
                    server_settings={"search_path": schema + ",public"},
                )
            )
        yield pools[0], pools[1]
    finally:
        for pool in pools:
            await pool.close()


@pytest.mark.pg_clock
async def test_private_memory_claim_path_does_not_retry_failed_episodes(
    native_chronicler_memory_pools,
    monkeypatch,
) -> None:
    """Chronicler's live private hook claims pending work but leaves due failures untouched."""
    pool, domain = native_chronicler_memory_pools
    assert await pool.fetchval("SELECT current_schema()") == "chronicler_mem"
    now = datetime.now(UTC)
    pending = await _insert_episode(pool)
    failed_due = await _insert_episode(
        pool,
        status="failed",
        attempts=1,
        retry_at=now - timedelta(seconds=1),
        last_error="previous private failure",
    )

    # The real Chronicler module owns a private memory pool.  Inject the
    # testcontainer-backed pool so its actual startup hook can register the
    # production scheduler callback without opening a second test pool.
    module = MemoryModule()
    module._memory_db = SimpleNamespace(pool=pool, close=AsyncMock())
    module._get_embedding_engine = lambda: _StaticEmbeddingEngine()
    monkeypatch.setattr(module, "_register_default_maintenance_schedules", AsyncMock())
    spawner = _BlockingSuccessfulSpawner()

    try:
        await module.on_startup(
            config=MemoryModuleConfig(memory_schema="chronicler_mem"),
            db=SimpleNamespace(pool=domain, schema="chronicler"),
        )
        assert module._allows_failed_consolidation_retry() is False

        failed_before = dict(await _episode_lifecycle(pool, failed_due))
        scheduled_run = asyncio.create_task(
            dispatch_scheduled_task(
                butler_name="chronicler",
                pool=pool,
                spawner=spawner,
                trigger_source="schedule:memory_consolidation",
                job_name="memory_consolidation",
                job_args={"batch_size": 20},
            )
        )
        try:
            await asyncio.wait_for(spawner.started.wait(), timeout=5)

            pending_claim = await _episode_lifecycle(pool, pending)
            assert pending_claim["leased_by"] is not None
            assert pending_claim["leased_until"] > datetime.now(UTC)
            assert dict(await _episode_lifecycle(pool, failed_due)) == failed_before
        finally:
            spawner.release.set()
            stats = await scheduled_run

        assert stats["episodes_processed"] == 1
        assert spawner.trigger_sources == ["schedule:consolidation"]
        pending_after = await _episode_lifecycle(pool, pending)
        assert pending_after["consolidation_status"] == "consolidated"
        assert pending_after["consolidated"] is True
        assert dict(await _episode_lifecycle(pool, failed_due)) == failed_before
        await _assert_native_memory_mutation_chain(pool, domain)
    finally:
        await module.on_shutdown()


@pytest.mark.pg_clock
async def test_registered_relationship_admin_dry_run_leaves_due_failed_retry_for_scheduler(
    provisioned_postgres_pool,
) -> None:
    """MCP dry runs cannot steal a due retry from the live scheduled Spawner."""
    async with provisioned_postgres_pool() as pool:
        await _install_lifecycle_schema(pool)
        await _install_artifact_schema(pool)
        episode_id = await _insert_episode(
            pool,
            status="failed",
            attempts=1,
            retry_at=datetime.now(UTC) - timedelta(seconds=1),
        )

        module = MemoryModule()
        module._get_embedding_engine = lambda: _StaticEmbeddingEngine()
        mcp = MagicMock()
        registered_tools: dict[str, object] = {}

        def capture_tool():
            def decorator(fn):
                registered_tools[fn.__name__] = fn
                return fn

            return decorator

        mcp.tool.side_effect = capture_tool
        await module.register_tools(
            mcp=mcp,
            config=None,
            db=SimpleNamespace(pool=pool, schema="relationship"),
            butler_name="relationship",
        )

        dry_run_stats = await registered_tools["memory_run_consolidation"]()
        assert dry_run_stats["episodes_processed"] == 0
        dry_run_row = await _episode_lifecycle(pool, episode_id)
        assert dry_run_row["consolidation_status"] == "failed"
        assert dry_run_row["leased_by"] is None
        assert dry_run_row["leased_until"] is None

        from butlers.core.memory_hooks import (
            bind_memory_maintenance_dispatch,
            register_memory_maintenance_runtime,
            unregister_memory_maintenance_runtime,
        )
        from butlers.scheduled_jobs import _run_memory_consolidation_job

        async def scheduled_consolidation(*, spawner, batch_size, enable_shared_catalog):
            return await run_consolidation(
                pool=pool,
                embedding_engine=_StaticEmbeddingEngine(),
                cc_spawner=spawner,
                batch_size=batch_size,
                enable_shared_catalog=enable_shared_catalog,
                retry_failed=True,
            )

        spawner = _BlockingUnsuccessfulSpawner()
        runtime = register_memory_maintenance_runtime(
            "relationship",
            pool_resolver=lambda: pool,
            consolidation=scheduled_consolidation,
        )
        try:
            with bind_memory_maintenance_dispatch(butler_name="relationship", spawner=spawner):
                scheduled_run = asyncio.create_task(
                    _run_memory_consolidation_job(pool=pool, job_args={"batch_size": 1})
                )
                await asyncio.wait_for(spawner.started.wait(), timeout=5)
                scheduled_claim = await _episode_lifecycle(pool, episode_id)
                assert scheduled_claim["consolidation_status"] == "failed"
                assert scheduled_claim["leased_by"] is not None
                assert scheduled_claim["leased_until"] > datetime.now(UTC)

                spawner.release.set()
                scheduled_stats = await scheduled_run
        finally:
            unregister_memory_maintenance_runtime("relationship", runtime)

        assert scheduled_stats["episodes_processed"] == 1


@pytest.mark.pg_clock
async def test_due_failed_claim_is_race_safe_between_scheduler_runs(
    provisioned_postgres_pool,
) -> None:
    """Concurrent runs cannot both claim one due failed episode."""
    async with provisioned_postgres_pool(max_pool_size=3) as pool:
        await _install_lifecycle_schema(pool)
        await _install_artifact_schema(pool)
        await _insert_episode(
            pool,
            status="failed",
            attempts=1,
            retry_at=datetime.now(UTC) - timedelta(seconds=1),
        )

        spawner = _BlockingUnsuccessfulSpawner()
        first_run = asyncio.create_task(
            run_consolidation(
                pool,
                _StaticEmbeddingEngine(),
                cc_spawner=spawner,
                batch_size=1,
            )
        )
        await asyncio.wait_for(spawner.started.wait(), timeout=5)
        second = await run_consolidation(
            pool,
            _StaticEmbeddingEngine(),
            cc_spawner=spawner,
            batch_size=1,
        )

        assert second["episodes_processed"] == 0
        assert await pool.fetchval("SELECT count(*) FROM episodes WHERE leased_by IS NOT NULL") == 1

        spawner.release.set()
        first = await first_run
        assert first["episodes_processed"] == 1


@pytest.mark.pg_clock
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


@pytest.mark.pg_clock
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


async def test_retry_dead_letter_episode_resets_and_is_reclaimed_by_the_real_scheduler(
    provisioned_postgres_pool,
) -> None:
    """The retry reset is a genuine re-enqueue, not a cosmetic status relabel.

    Resets a dead-lettered episode via ``storage.retry_dead_letter_episode``,
    then proves the reset actually matters by running the real scheduler
    claim query (``run_consolidation``) against it: a relabel with poisoned
    lifecycle fields (stale attempts/lease) would not be reclaimable, so the
    live worker lease this asserts on is direct evidence of reconsideration
    (bu-6t8ix.2 acceptance criterion 4).
    """
    async with provisioned_postgres_pool() as pool:
        await _install_lifecycle_schema(pool)
        await _install_artifact_schema(pool)
        episode_id = await _insert_episode(
            pool,
            status="dead_letter",
            attempts=MAX_CONSOLIDATION_ATTEMPTS,
            dead_letter_reason="terminal",
            last_error="Consolidation execution failed.",
            leased_until=datetime.now(UTC) - timedelta(seconds=1),
            leased_by="abandoned-worker",
        )

        updated = await retry_dead_letter_episode(pool, episode_id)
        assert updated["consolidation_status"] == "pending"

        row = await _episode_lifecycle(pool, episode_id)
        assert row["consolidation_status"] == "pending"
        assert row["consolidation_attempts"] == 0
        assert row["dead_letter_reason"] is None
        assert row["last_consolidation_error"] is None
        assert row["next_consolidation_retry_at"] is None
        assert row["leased_by"] is None
        assert (
            await pool.fetchval(
                "SELECT event_type FROM memory_events WHERE memory_id = $1",
                episode_id,
            )
            == "episode_consolidation_retry_requested"
        )

        # The proof: the real scheduler claims it on the very next sweep,
        # exactly like a freshly-stored pending episode.
        spawner = _BlockingUnsuccessfulSpawner()
        scheduled_run = asyncio.create_task(
            run_consolidation(
                pool=pool,
                embedding_engine=_StaticEmbeddingEngine(),
                cc_spawner=spawner,
                batch_size=10,
            )
        )
        await asyncio.wait_for(spawner.started.wait(), timeout=5)
        assert (await _episode_lifecycle(pool, episode_id))["leased_by"]
        spawner.release.set()
        stats = await scheduled_run
        assert stats["episodes_processed"] == 1


async def test_retry_dead_letter_episode_rejects_non_dead_letter_status(
    provisioned_postgres_pool,
) -> None:
    """A non-dead_letter episode is rejected, not silently reset."""
    async with provisioned_postgres_pool() as pool:
        await _install_lifecycle_schema(pool)
        episode_id = await _insert_episode(pool, status="failed", attempts=1)

        with pytest.raises(EpisodeNotDeadLetterError):
            await retry_dead_letter_episode(pool, episode_id)

        row = await _episode_lifecycle(pool, episode_id)
        assert row["consolidation_status"] == "failed"
        assert row["consolidation_attempts"] == 1
        assert await pool.fetchval("SELECT count(*) FROM memory_events") == 0


async def test_retry_dead_letter_episode_returns_none_for_unknown_id(
    provisioned_postgres_pool,
) -> None:
    """An id with no matching episode returns None rather than raising."""
    async with provisioned_postgres_pool() as pool:
        await _install_lifecycle_schema(pool)
        assert await retry_dead_letter_episode(pool, uuid.uuid4()) is None


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


async def test_terminal_event_failure_rolls_back_fenced_artifacts_and_confirmation(
    provisioned_postgres_pool,
) -> None:
    """A terminal audit failure rolls back every write in the protected group."""
    async with provisioned_postgres_pool() as pool:
        await _install_lifecycle_schema(pool)
        await _install_artifact_schema(pool)
        # Keep the original five-minute fence observably distinct from the
        # executor's 300-second renewal. A future-only assertion would allow a
        # leaked/committed renewal to masquerade as a complete rollback.
        original_lease = datetime.now(UTC) + timedelta(minutes=5, microseconds=123456)
        episode_id = await _insert_episode(
            pool,
            leased_until=original_lease,
            leased_by="claim-a",
        )
        confirmation_id = uuid.uuid4()
        await pool.execute("INSERT INTO facts (id) VALUES ($1)", confirmation_id)
        await pool.execute(
            "ALTER TABLE memory_events "
            "ADD CONSTRAINT reject_terminal_events "
            "CHECK (event_type <> 'episode_consolidated')"
        )

        parsed = parse_consolidation_output(
            json.dumps(
                {
                    "new_facts": [
                        {
                            "subject": "Owner",
                            "predicate": "test_property",
                            "content": "prefers transactional integrity",
                            "evidence_episode_ids": [str(episode_id)],
                        }
                    ],
                    "new_rules": [
                        {
                            "content": "Persist only with a durable terminal audit event.",
                            "evidence_episode_ids": [str(episode_id)],
                        }
                    ],
                    "confirmations": [str(confirmation_id)],
                }
            )
        )
        assert parsed.parse_errors == []

        result = await execute_consolidation(
            pool=pool,
            embedding_engine=_StaticEmbeddingEngine(),
            parsed=parsed,
            source_episode_ids=[episode_id],
            butler_name="memory",
            claim_token="claim-a",
        )

        assert result == {
            "facts_created": 0,
            "facts_updated": 0,
            "rules_created": 0,
            "confirmations_made": 0,
            "episodes_consolidated": 0,
            "episode_ttl_days": 0,
            "errors": ["Failed to mark episodes as consolidated"],
        }
        assert (
            await pool.fetchval("SELECT count(*) FROM facts WHERE predicate = 'test_property'") == 0
        )
        assert await pool.fetchval("SELECT count(*) FROM rules") == 0
        assert await pool.fetchval("SELECT count(*) FROM memory_links") == 0
        assert (
            await pool.fetchval(
                "SELECT last_confirmed_at FROM facts WHERE id = $1", confirmation_id
            )
        ) is None
        assert await pool.fetchval("SELECT count(*) FROM memory_events") == 0

        row = await _episode_lifecycle(pool, episode_id)
        assert row["consolidation_status"] == "pending"
        assert row["consolidated"] is False
        assert row["consolidation_attempts"] == 0
        assert row["leased_by"] == "claim-a"
        assert row["leased_until"] == original_lease


@pytest.mark.pg_clock
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


async def _assert_native_memory_mutation_chain(pool, domain):
    """Migrated SAME-writer evolution; planted lineage, not real ingress authority.

    Reuses the actual configured native Memory startup and the two real schema
    pools. It does not grant roles, copy DDL or claim these fixture births came
    from a registered remote source. Runtime-role isolation remains the other
    explicit owning species.
    """
    from butlers.chronicler.location_memory_copies import artifact_content_digest
    from butlers.chronicler.location_memory_mutations import (
        _api_writers,
        current_artifact_body_matches,
        memory_mutation_transaction,
        register_api_memory_writer,
    )
    from butlers.chronicler.location_projection import _digest_value
    from butlers.chronicler.location_retention import PolicyUnavailableError
    from butlers.location_retention import content_digest
    from butlers.modules.memory.storage import confirm_memory

    artifact, generation, input_generation = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO facts(id,subject,predicate,content) VALUES($1,'native','location','bound body')",
                artifact,
            )
            canonical = await conn.fetchrow("SELECT * FROM facts WHERE id=$1", artifact)
            frozen = content_digest({"memory_artifact": _digest_value(dict(canonical))})
            await conn.execute(
                "INSERT INTO chronicler.location_native_dispatch_inputs "
                "(input_generation,server_request,prompt_digest,parent_count,origin_kind) "
                "VALUES($1,$2,$3,1,'native_memory')",
                input_generation,
                uuid.uuid4(),
                b"p" * 32,
            )
            await conn.execute(
                "INSERT INTO chronicler.location_native_memory_bundles "
                "(input_generation,bundle_digest,exclusive_input) VALUES($1,$2,true)",
                input_generation,
                b"b" * 32,
            )
            parent = uuid.uuid4()
            await conn.execute(
                "INSERT INTO chronicler.location_native_copy_births "
                "(copy_generation,output_kind,output_id,input_digest,lineage_known,exclusive_input,"
                "producer_kind) VALUES($1,'point_event',$2,$3,true,true,'native_memory')",
                parent,
                uuid.uuid4(),
                b"n" * 32,
            )
            await conn.execute(
                "INSERT INTO chronicler.location_native_dispatch_parents "
                "(input_generation,copy_generation,input_digest) VALUES($1,$2,$3)",
                input_generation,
                parent,
                b"n" * 32,
            )
            await conn.execute(
                "INSERT INTO chronicler.location_native_memory_artifacts "
                "(artifact_generation,input_generation,memory_table,artifact_id,body_digest,content_digest) "
                "VALUES($1,$2,'facts',$3,$4,$5)",
                generation,
                input_generation,
                artifact,
                frozen,
                artifact_content_digest("facts", canonical),
            )
    assert await confirm_memory(pool, "fact", artifact)
    async with pool.acquire() as readback:
        original = await readback.fetchrow(
            "SELECT * FROM chronicler.location_native_memory_artifacts WHERE artifact_generation=$1",
            generation,
        )
        transitions = await readback.fetch(
            "SELECT * FROM chronicler.location_native_memory_mutations "
            "WHERE artifact_generation=$1 ORDER BY revision",
            generation,
        )
        row = await readback.fetchrow("SELECT * FROM facts WHERE id=$1", artifact)
        assert original["body_digest"] == frozen
        assert len(transitions) == 1 and transitions[0]["revision"] == 1
        assert transitions[0]["before_digest"] == original["content_digest"]
        assert transitions[0]["after_digest"] == artifact_content_digest("facts", row)
        assert await current_artifact_body_matches(readback, row, original)
        with pytest.raises(asyncpg.RaiseError, match="history is permanent"):
            async with readback.transaction():
                await readback.execute(
                    "UPDATE chronicler.location_native_memory_mutations "
                    "SET after_digest=$2 WHERE artifact_generation=$1",
                    generation,
                    b"z" * 32,
                )
    # Actual write rollback cannot leak either the canonical change or a new
    # immutable version; verify from another acquired connection afterward.
    with pytest.raises(RuntimeError, match="planted business rollback"):
        async with memory_mutation_transaction(pool, "facts", artifact) as writer:
            await writer.execute("UPDATE facts SET validity='retracted' WHERE id=$1", artifact)
            raise RuntimeError("planted business rollback")
    assert await pool.fetchval("SELECT validity FROM facts WHERE id=$1", artifact) == "active"
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM chronicler.location_native_memory_mutations WHERE artifact_generation=$1",
            generation,
        )
        == 1
    )
    # Fixed private API enrollment is a configured writer test, not a receiving
    # incarnation or an accepted-source authority claim.
    try:
        with pytest.raises(PolicyUnavailableError, match="configuration is unavailable"):
            await register_api_memory_writer(domain, "chronicler", "relationship")
        assert domain not in _api_writers
        await register_api_memory_writer(domain, "chronicler", "chronicler_mem")
        role = await domain.fetchval("SELECT current_user")
        assert await confirm_memory(domain, "fact", artifact, memory_schema="chronicler_mem")
        assert await domain.fetchval("SELECT current_schema()") == "chronicler"
        assert await domain.fetchval("SELECT current_user") == role
        with pytest.raises(PolicyUnavailableError, match="schema differs"):
            await confirm_memory(domain, "fact", artifact, memory_schema="relationship")
        async with pool.acquire() as readback:
            row = await readback.fetchrow("SELECT * FROM facts WHERE id=$1", artifact)
            versions = await readback.fetch(
                "SELECT * FROM chronicler.location_native_memory_mutations "
                "WHERE artifact_generation=$1 ORDER BY revision",
                generation,
            )
            assert len(versions) == 2
            assert versions[1]["previous_generation"] == versions[0]["mutation_generation"]
            assert versions[1]["before_digest"] == versions[0]["after_digest"]
            assert await current_artifact_body_matches(readback, row, original)
        # New native mutation-input SQL is a planted private receiving binding,
        # not proof that a remote invocation/guard authenticated this fixture.
        from butlers.chronicler.location_catalog_copies import CatalogCopyRuntime, _runtimes
        from butlers.chronicler.location_memory_context import _context_writers
        from butlers.chronicler.location_tool_copies import (
            _current_tool_copy,
            _ToolCopy,
            finish_tool_copy,
        )

        session_id, tool_generation = uuid.uuid4(), uuid.uuid4()
        await domain.execute(
            "INSERT INTO sessions(id,prompt,trigger_source,request_id) VALUES($1,$2,$3,$4)",
            session_id,
            "planted receiving input",
            "test:native_mutation",
            str(uuid.uuid4()),
        )
        await domain.execute(
            "INSERT INTO location_runtime_tool_intents "
            "(tool_generation,receiving_session,tool_name,module_name,input_digest) "
            "VALUES($1,$2,'memory_confirm','memory',$3)",
            tool_generation,
            session_id,
            b"t" * 32,
        )
        prior_runtime, prior_writer = _runtimes.get(pool), _context_writers.get(domain)
        runtime = CatalogCopyRuntime(
            domain=domain,
            memory=pool,
            name="chronicler",
            registry=object(),
            identity=("chronicler", await domain.fetchval("SELECT current_user")),
            memory_identity=("chronicler_mem", await pool.fetchval("SELECT current_user")),
        )
        tool = _ToolCopy(runtime, tool_generation, session_id, "memory_confirm", "memory")
        token = _current_tool_copy.set(tool)
        try:
            # Trusted disposable DDL positions the missing-installed-dependency
            # refusal; the runtime producer itself must not install/repair it.
            from butlers.location_retention_schema import tool_input_dependency_sql

            await domain.execute(
                "ALTER TABLE location_native_memory_mutation_inputs DROP CONSTRAINT "
                "location_native_memory_mutation_inputs_tool_generation_fkey"
            )
            with pytest.raises(PolicyUnavailableError, match="installed tool dependency"):
                await confirm_memory(pool, "fact", artifact)
            assert not await domain.fetchval(
                "SELECT EXISTS(SELECT 1 FROM location_native_memory_mutation_inputs "
                "WHERE tool_generation=$1)",
                tool_generation,
            )
            await domain.execute(tool_input_dependency_sql("chronicler"))
            assert await domain.fetchval(
                "SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_constraint WHERE "
                "conrelid='chronicler.location_native_memory_mutation_inputs'::pg_catalog.regclass "
                "AND confrelid='chronicler.location_runtime_tool_intents'::pg_catalog.regclass "
                "AND conname='location_native_memory_mutation_inputs_tool_generation_fkey' "
                "AND contype='f' AND convalidated)"
            )
            with pytest.raises(RuntimeError, match="planted input rollback"):
                async with memory_mutation_transaction(pool, "facts", artifact) as writer:
                    await writer.execute(
                        "UPDATE facts SET validity='retracted' WHERE id=$1", artifact
                    )
                    raise RuntimeError("planted input rollback")
            assert (
                await pool.fetchval("SELECT validity FROM facts WHERE id=$1", artifact) == "active"
            )
            assert not await domain.fetchval(
                "SELECT EXISTS(SELECT 1 FROM location_native_memory_mutation_inputs "
                "WHERE tool_generation=$1)",
                tool_generation,
            )
            assert not await domain.fetchval(
                "SELECT EXISTS(SELECT 1 FROM location_native_copy_births "
                "WHERE receiving_session=$1)",
                session_id,
            )
            assert await confirm_memory(pool, "fact", artifact)
            await finish_tool_copy((tool, token), {"confirmed": True})
            async with domain.acquire() as observed:
                captured = await observed.fetchrow(
                    "SELECT * FROM location_native_memory_mutation_inputs "
                    "WHERE tool_generation=$1 AND artifact_generation=$2",
                    tool_generation,
                    generation,
                )
                assert captured is not None and captured["lifecycle_only"] is True
                assert captured["parent_count"] == 1
                assert (
                    await observed.fetchval(
                        "SELECT count(*) FROM location_native_copy_births WHERE copy_generation=$1 "
                        "AND receiving_session=$2 AND input_digest=$3 AND lineage_known AND exclusive_input",
                        captured["input_generation"],
                        session_id,
                        captured["before_digest"],
                    )
                    == 1
                )
                assert (
                    await observed.fetchval(
                        "SELECT exclusive_inputs FROM location_runtime_tool_results WHERE tool_generation=$1",
                        tool_generation,
                    )
                    is True
                )
                with pytest.raises(asyncpg.RaiseError, match="history is permanent"):
                    async with observed.transaction():
                        await observed.execute(
                            "UPDATE location_native_memory_mutation_inputs SET lifecycle_only=false "
                            "WHERE input_generation=$1",
                            captured["input_generation"],
                        )
            await _assert_two_parent_native_mutation_inputs(pool, domain, runtime, session_id)
        finally:
            if _current_tool_copy.get() is tool:
                _current_tool_copy.reset(token)
            runtime.close()
            if prior_runtime is not None:
                _runtimes[pool] = prior_runtime
            if prior_writer is None:
                _context_writers.pop(domain, None)
            else:
                _context_writers[domain] = prior_writer

        # A real mixed annotation is recorded but never made source-exclusive.
        async with memory_mutation_transaction(pool, "facts", artifact) as writer:
            await writer.execute(
                "UPDATE facts SET metadata=$2::jsonb WHERE id=$1",
                artifact,
                {"independent_annotation": "preserve this unrelated owner annotation"},
            )
        async with pool.acquire() as readback:
            row = await readback.fetchrow("SELECT * FROM facts WHERE id=$1", artifact)
            assert not await current_artifact_body_matches(readback, row, original)
            assert (
                await readback.fetchval(
                    "SELECT lifecycle_only FROM chronicler.location_native_memory_mutations "
                    "WHERE artifact_generation=$1 ORDER BY revision DESC LIMIT 1",
                    generation,
                )
                is False
            )
            assert (
                await readback.fetchval(
                    "SELECT body_digest FROM chronicler.location_native_memory_artifacts "
                    "WHERE artifact_generation=$1",
                    generation,
                )
                == frozen
            )
    finally:
        _api_writers.pop(domain, None)


async def _assert_two_parent_native_mutation_inputs(pool, domain, runtime, session_id):
    """Declared ancestry SQL controls; private receiving/source bindings planted."""
    from butlers.chronicler.location_memory_copies import artifact_content_digest
    from butlers.chronicler.location_projection import _digest_value
    from butlers.chronicler.location_retention import PolicyUnavailableError
    from butlers.chronicler.location_tool_copies import (
        _current_tool_copy,
        _ToolCopy,
        finish_tool_copy,
    )
    from butlers.location_retention import content_digest
    from butlers.modules.memory.storage import confirm_memory

    for species in ("missing", "mismatched_digest", "extra", "complete"):
        artifact, generation, bundle, tool_id = (uuid.uuid4() for _ in range(4))
        parents = [uuid.uuid4() for _ in range(3 if species == "extra" else 2)]
        async with pool.acquire() as writer:
            async with writer.transaction():
                await writer.execute(
                    "INSERT INTO facts(id,subject,predicate,content) "
                    "VALUES($1,'native','location','two-parent fixed source body')",
                    artifact,
                )
                row = await writer.fetchrow("SELECT * FROM facts WHERE id=$1", artifact)
                await writer.execute(
                    "INSERT INTO chronicler.location_native_dispatch_inputs "
                    "(input_generation,server_request,prompt_digest,parent_count,origin_kind) "
                    "VALUES($1,$2,$3,2,'native_memory')",
                    bundle,
                    uuid.uuid4(),
                    b"p" * 32,
                )
                await writer.execute(
                    "INSERT INTO chronicler.location_native_memory_bundles "
                    "(input_generation,bundle_digest,exclusive_input) VALUES($1,$2,true)",
                    bundle,
                    b"b" * 32,
                )
                for index, parent in enumerate(parents):
                    await writer.execute(
                        "INSERT INTO chronicler.location_native_dispatch_parents "
                        "(input_generation,copy_generation,input_digest) VALUES($1,$2,$3)",
                        bundle,
                        parent,
                        b"n" * 32,
                    )
                    if index == 1 and species == "missing":
                        continue
                    await writer.execute(
                        "INSERT INTO chronicler.location_native_copy_births "
                        "(copy_generation,output_kind,output_id,input_digest,lineage_known,"
                        "exclusive_input,producer_kind) "
                        "VALUES($1,'point_event',$2,$3,true,true,'native_memory')",
                        parent,
                        uuid.uuid4(),
                        b"z" * 32 if index == 1 and species == "mismatched_digest" else b"n" * 32,
                    )
                await writer.execute(
                    "INSERT INTO chronicler.location_native_memory_artifacts "
                    "(artifact_generation,input_generation,memory_table,artifact_id,body_digest,"
                    "content_digest) VALUES($1,$2,'facts',$3,$4,$5)",
                    generation,
                    bundle,
                    artifact,
                    content_digest({"memory_artifact": _digest_value(dict(row))}),
                    artifact_content_digest("facts", row),
                )
        await domain.execute(
            "INSERT INTO location_runtime_tool_intents "
            "(tool_generation,receiving_session,tool_name,module_name,input_digest) "
            "VALUES($1,$2,'memory_confirm','memory',$3)",
            tool_id,
            session_id,
            b"t" * 32,
        )
        binding = _ToolCopy(runtime, tool_id, session_id, "memory_confirm", "memory")
        token = _current_tool_copy.set(binding)
        try:
            if species != "complete":
                with pytest.raises(PolicyUnavailableError, match="complete input ancestry"):
                    await confirm_memory(pool, "fact", artifact)
                assert (
                    await pool.fetchval(
                        "SELECT last_confirmed_at FROM facts WHERE id=$1",
                        artifact,
                    )
                    is None
                )
                assert not await domain.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM location_native_memory_mutation_inputs "
                    "WHERE tool_generation=$1)",
                    tool_id,
                )
                assert not binding.read_observed
            else:
                assert await confirm_memory(pool, "fact", artifact)
                await finish_tool_copy((binding, token), {"confirmed": True})
                async with domain.acquire() as observed:
                    captured = await observed.fetchrow(
                        "SELECT * FROM location_native_memory_mutation_inputs WHERE tool_generation=$1",
                        tool_id,
                    )
                    assert captured["parent_count"] == 2 and captured["lifecycle_only"] is True
                    assert (
                        await observed.fetchval(
                            "SELECT count(*) FROM location_native_copy_births WHERE copy_generation=$1 "
                            "AND receiving_session=$2 AND input_digest=$3 AND lineage_known "
                            "AND exclusive_input",
                            captured["input_generation"],
                            session_id,
                            captured["before_digest"],
                        )
                        == 2
                    )
        finally:
            if _current_tool_copy.get() is binding:
                _current_tool_copy.reset(token)
