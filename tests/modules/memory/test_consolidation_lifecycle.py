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
        import hashlib

        from butlers.chronicler.location_catalog_copies import CatalogCopyRuntime, _runtimes
        from butlers.chronicler.location_memory_context import _context_writers
        from butlers.chronicler.location_tool_copies import (
            _current_tool_copy,
            _ToolCopy,
            finish_tool_copy,
        )
        from butlers.core.sessions import session_create

        tool_generation = uuid.uuid4()
        system_prompt = "synthetic frozen system"
        # The real producer validates the complete core_236 receipt and legal
        # trigger, then commits it before any immutable native input binding.
        # This remains planted receiving lineage, not online source admission.
        session_id = await session_create(
            domain,
            prompt="planted receiving input",
            trigger_source="trigger",
            request_id=str(uuid.uuid4()),
            effective_system_prompt=system_prompt,
            prompt_digest=hashlib.sha256(system_prompt.encode()).hexdigest(),
            prompt_provenance=[],
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
            # The expected FK alongside an extra FK is not the exact installed
            # set. Both migration convergence and actual producer must refuse.
            await domain.execute(
                "ALTER TABLE location_native_memory_mutation_inputs ADD CONSTRAINT "
                "planted_extra_tool_dependency FOREIGN KEY(tool_generation) "
                "REFERENCES location_runtime_tool_intents(tool_generation)"
            )
            with pytest.raises(asyncpg.RaiseError, match="tool dependency differs"):
                await domain.execute(tool_input_dependency_sql("chronicler"))
            with pytest.raises(PolicyUnavailableError, match="installed tool dependency"):
                await confirm_memory(pool, "fact", artifact)
            assert not await domain.fetchval(
                "SELECT EXISTS(SELECT 1 FROM location_native_memory_mutation_inputs "
                "WHERE tool_generation=$1)",
                tool_generation,
            )
            await domain.execute(
                "ALTER TABLE location_native_memory_mutation_inputs DROP CONSTRAINT "
                "planted_extra_tool_dependency"
            )
            await domain.execute(
                "ALTER TABLE location_native_memory_mutation_inputs ALTER CONSTRAINT "
                "location_native_memory_mutation_inputs_tool_generation_fkey DEFERRABLE"
            )
            with pytest.raises(asyncpg.RaiseError, match="tool dependency differs"):
                await domain.execute(tool_input_dependency_sql("chronicler"))
            with pytest.raises(PolicyUnavailableError, match="installed tool dependency"):
                await confirm_memory(pool, "fact", artifact)
            assert not await domain.fetchval(
                "SELECT EXISTS(SELECT 1 FROM location_native_memory_mutation_inputs "
                "WHERE tool_generation=$1)",
                tool_generation,
            )
            await domain.execute(
                "ALTER TABLE location_native_memory_mutation_inputs ALTER CONSTRAINT "
                "location_native_memory_mutation_inputs_tool_generation_fkey NOT DEFERRABLE"
            )
            await domain.execute(tool_input_dependency_sql("chronicler"))
            async with domain.acquire() as observed:
                assert await observed.fetchval(
                    "SELECT count(*)=1 AND bool_and(convalidated AND NOT condeferrable "
                    "AND NOT condeferred) FROM pg_catalog.pg_constraint WHERE "
                    "conrelid='chronicler.location_native_memory_mutation_inputs'::pg_catalog.regclass "
                    "AND contype='f' AND 2=ANY(conkey)"
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
            # Exercise the original legitimate mixed writer BEFORE the
            # delegation helper prepares this source generation for disposal.
            # Detach only this fixture's private MCP token; real configured
            # API writer/role/lineage and business transaction remain enforced.
            paused_tool = _current_tool_copy.set(None)
            try:
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
                _current_tool_copy.reset(paused_tool)
            await _assert_two_parent_native_mutation_inputs(pool, domain, runtime, session_id)
            await _assert_native_delegation_writer(domain, runtime, session_id)
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

        # The helper has now genuinely prepared this immutable source.
        # This is a separate refusal, not permission to annotate through a
        # prepared-generation fence or to remove its stored plan output.
        async with pool.acquire() as observed:
            before_prepared = dict(
                await observed.fetchrow("SELECT * FROM facts WHERE id=$1", artifact)
            )
            before_versions = await observed.fetchval(
                "SELECT count(*) FROM chronicler.location_native_memory_mutations "
                "WHERE artifact_generation=$1",
                generation,
            )
        with pytest.raises(PolicyUnavailableError, match="source generation is prepared"):
            async with memory_mutation_transaction(pool, "facts", artifact) as writer:
                await writer.execute(
                    "UPDATE facts SET metadata=$2::jsonb WHERE id=$1",
                    artifact,
                    {"independent_annotation": "must not overwrite behind prepared floor"},
                )
        async with pool.acquire() as observed:
            assert (
                dict(await observed.fetchrow("SELECT * FROM facts WHERE id=$1", artifact))
                == before_prepared
            )
            assert (
                await observed.fetchval(
                    "SELECT count(*) FROM chronicler.location_native_memory_mutations "
                    "WHERE artifact_generation=$1",
                    generation,
                )
                == before_versions
            )
            assert (
                await observed.fetchval(
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
    from butlers.chronicler.location_memory_derivation import _captured_bundle_parents
    from butlers.chronicler.location_projection import _digest_value
    from butlers.chronicler.location_retention import PolicyUnavailableError
    from butlers.chronicler.location_tool_copies import (
        _current_tool_copy,
        _ToolCopy,
        finish_tool_copy,
    )
    from butlers.location_retention import content_digest
    from butlers.modules.memory.storage import confirm_memory

    for species in ("missing", "mismatched_digest", "extra", "empty", "complete"):
        artifact, generation, bundle, tool_id = (uuid.uuid4() for _ in range(4))
        parents = [
            uuid.uuid4() for _ in range(0 if species == "empty" else 3 if species == "extra" else 2)
        ]
        async with pool.acquire() as writer:
            async with writer.transaction():
                await writer.execute(
                    "INSERT INTO facts(id,subject,predicate,content) "
                    "VALUES($1,$2,'location','two-parent fixed source body')",
                    artifact,
                    "native-" + species + "-" + artifact.hex,
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
        # The same frozen bundle feeds actual consolidation. Check all declared
        # parents before copying the prompt, independently of mutation admission.
        async with pool.acquire() as observed:
            async with observed.transaction():
                from butlers.chronicler.location_memory_copies import _lock

                await _lock(observed, "chronicler_mem", runtime.memory_identity[1])
                selected = [
                    dict(await observed.fetchrow("SELECT * FROM facts WHERE id=$1", artifact))
                ]
                if species != "complete":
                    with pytest.raises(PolicyUnavailableError, match="complete input ancestry"):
                        await _captured_bundle_parents(observed, [], selected, [])
                else:
                    captured_parents, exclusive = await _captured_bundle_parents(
                        observed, [], selected, []
                    )
                    assert exclusive is True and len(captured_parents) == 2
                    assert {p["copy_generation"] for p in captured_parents} == set(parents)
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


async def _assert_native_delegation_writer(domain, runtime, session_id):
    """Real owning transactions/readback; private invocation is planted, not online proof."""
    import hashlib

    from butlers.chronicler.location_delegation_copies import question_digest
    from butlers.chronicler.location_policy import PolicyUnavailableError
    from butlers.chronicler.location_tool_copies import (
        _current_tool_copy,
        _ToolCopy,
        finish_tool_copy,
    )
    from butlers.core.delegation_ledger import record_ask
    from butlers.core.tool_call_capture import fingerprint_tool_call_payload
    from butlers.location_retention import content_digest

    context, tool_generation = uuid.uuid4(), uuid.uuid4()
    async with domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            session = await conn.fetchrow("SELECT * FROM sessions WHERE id=$1", session_id)
            await conn.execute(
                "INSERT INTO location_runtime_context_intents(input_generation,receiving_session) "
                "VALUES($1,$2)",
                context,
                session_id,
            )
            await conn.execute(
                "INSERT INTO location_runtime_context_bindings "
                "(input_generation,receiving_session,bundle_digest,context_digest,system_digest,"
                "prompt_digest,exclusive_input,context_bytes) VALUES($1,$2,$3,$4,$5,$6,true,0)",
                context,
                session_id,
                content_digest(
                    {
                        "loans": [],
                        "context": (b"c" * 32).hex(),
                        "system": hashlib.sha256(
                            session["effective_system_prompt"].encode()
                        ).hexdigest(),
                        "prompt": hashlib.sha256(session["prompt"].encode()).hexdigest(),
                    }
                ),
                b"c" * 32,
                hashlib.sha256(session["effective_system_prompt"].encode()).digest(),
                hashlib.sha256(session["prompt"].encode()).digest(),
            )
            await conn.execute(
                "INSERT INTO location_runtime_tool_intents "
                "(tool_generation,receiving_session,tool_name,module_name,input_digest) "
                "VALUES($1,$2,'delegate_ask','core',$3)",
                tool_generation,
                session_id,
                bytes.fromhex(
                    fingerprint_tool_call_payload(
                        {"question": "synthetic native location question"}
                    )
                ),
            )
    tool = _ToolCopy(runtime, tool_generation, session_id, "delegate_ask", "core")
    token = _current_tool_copy.set(tool)
    try:
        fields = dict(
            asking_butler="chronicler",
            question="synthetic native location question",
            target_butler="relationship",
            catalog_match_id=None,
            catalog_score=None,
            metadata={"synthetic": "actual JSON object"},
        )
        selected_native = await domain.fetch(
            "SELECT DISTINCT copy_generation,input_digest FROM location_native_copy_births "
            "WHERE receiving_session=$1",
            session_id,
        )
        expected_native = {
            ("native_copy", row["copy_generation"], row["input_digest"]) for row in selected_native
        }
        identifier = uuid.UUID(await record_ask(domain, status="pending", **fields))
        original_ask_flags = tool.read_observed, tool.mixed_inputs
        async with domain.acquire() as observed:
            birth = await observed.fetchrow(
                "SELECT * FROM location_native_delegation_inputs WHERE ledger_id=$1",
                identifier,
            )
            canonical = await observed.fetchrow(
                "SELECT * FROM public.delegation_ledger WHERE id=$1", identifier
            )
            parents = await observed.fetch(
                "SELECT * FROM location_native_delegation_parents WHERE question_generation=$1",
                birth["question_generation"],
            )
            assert birth["context_generation"] == context
            assert birth["tool_generation"] == tool_generation
            assert (
                birth["body_digest"] == question_digest(fields) == question_digest(dict(canonical))
            )
            assert isinstance(canonical["metadata"], dict)
            assert birth["parent_count"] == len(parents) > 0
            assert all(row["parent_kind"] == "native_copy" for row in parents)
            for row in parents:
                # Boolean-only positioning preserves the original assertion;
                # never emit source IDs, digests, rows, prompt or arguments.
                diagnostic = await observed.fetchrow(
                    "SELECT EXISTS(SELECT 1 FROM location_native_copy_births "
                    "WHERE copy_generation=$1) AS generation_exists, "
                    "EXISTS(SELECT 1 FROM location_native_copy_births "
                    "WHERE copy_generation=$1 AND input_digest=$2) AS digest_matches, "
                    "EXISTS(SELECT 1 FROM location_native_copy_births "
                    "WHERE copy_generation=$1 AND receiving_session=$3) AS session_matches, "
                    "EXISTS(SELECT 1 FROM location_native_copy_births "
                    "WHERE copy_generation=$1 AND input_digest=$2 AND receiving_session=$3) "
                    "AS exact_matches",
                    row["parent_generation"],
                    row["parent_digest"],
                    session_id,
                )
                if diagnostic["exact_matches"] is not True:
                    print(
                        "closed_native_parent_readback "
                        + json.dumps(
                            {
                                **{key: value is True for key, value in dict(diagnostic).items()},
                                "tool_session_matches_fixture": tool.session == session_id,
                                "stored_session_matches_tool": birth["receiving_session"]
                                == uuid.UUID(str(tool.session)),
                                "parent_count_matches": birth["parent_count"] == len(parents),
                                "selected_parent_set_matches": {
                                    (p["parent_kind"], p["parent_generation"], p["parent_digest"])
                                    for p in parents
                                }
                                == expected_native,
                            },
                            sort_keys=True,
                        )
                    )
                assert await observed.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM location_native_copy_births "
                    "WHERE copy_generation=$1 AND input_digest=$2 AND receiving_session=$3)",
                    row["parent_generation"],
                    row["parent_digest"],
                    session_id,
                )
            # Core-owned header uses the permanent source-floor trigger.
            # The old Chronicle mutation trigger has a different fixed label.
            with pytest.raises(asyncpg.RaiseError, match="Location source floors are permanent"):
                async with observed.transaction():
                    await observed.execute(
                        "UPDATE location_native_delegation_inputs SET body_digest=$2 WHERE ledger_id=$1",
                        identifier,
                        b"x" * 32,
                    )
            assert (
                await observed.fetchval(
                    "SELECT body_digest FROM location_native_delegation_inputs WHERE ledger_id=$1",
                    identifier,
                )
                == birth["body_digest"]
            )
        before = await domain.fetchval("SELECT count(*) FROM location_native_delegation_inputs")
        # Refusal occurs before a new ledger body when the actual tool intent differs.
        tool.generation = uuid.uuid4()
        with pytest.raises(PolicyUnavailableError, match="binding is unavailable"):
            await record_ask(domain, status="pending", **fields)
        tool.generation = tool_generation
        assert (
            await domain.fetchval("SELECT count(*) FROM location_native_delegation_inputs")
            == before
        )

        async def rollback(conn, selected):
            await conn.execute(
                "INSERT INTO public.delegation_ledger(id,asking_butler,question,status) "
                "VALUES($1,'chronicler','synthetic rollback','pending')",
                selected,
            )
            raise RuntimeError("planted delegated business rollback")

        with pytest.raises(RuntimeError, match="delegated business rollback"):
            await runtime.delegation_writer.capture_ask(fields, rollback)
        async with domain.acquire() as observed:
            assert (
                await observed.fetchval("SELECT count(*) FROM location_native_delegation_inputs")
                == before
            )
            assert not await observed.fetchval(
                "SELECT EXISTS(SELECT 1 FROM public.delegation_ledger "
                "WHERE question='synthetic rollback')"
            )
        # First-answer body, wake identity and complete own input birth use
        # the actual registered connection; duplicates cannot replace history.
        from butlers.core.delegation_ledger import record_answer

        answer_tool, answer_ledger = uuid.uuid4(), uuid.uuid4()
        async with domain.acquire() as conn:
            async with conn.transaction():
                await runtime.lock_domain(conn)
                await conn.execute(
                    "INSERT INTO location_runtime_tool_intents "
                    "(tool_generation,receiving_session,tool_name,module_name,input_digest) "
                    "VALUES($1,$2,'delegate_answer','core',$3)",
                    answer_tool,
                    session_id,
                    b"a" * 32,
                )
                await conn.execute(
                    "INSERT INTO public.delegation_ledger "
                    "(id,asking_butler,question,target_butler,status) "
                    "VALUES($1,'relationship','synthetic independent answer input','chronicler','routed')",
                    answer_ledger,
                )
        tool.name, tool.generation = "delegate_answer", answer_tool
        result = await record_answer(
            domain, answer_ledger, answering_butler="chronicler", answer="synthetic native answer"
        )
        async with domain.acquire() as observed:
            answer_birth = await observed.fetchrow(
                "SELECT * FROM location_native_delegation_answers WHERE ledger_id=$1", answer_ledger
            )
            answer_parents = await observed.fetch(
                "SELECT * FROM location_native_delegation_answer_parents WHERE answer_generation=$1",
                answer_birth["answer_generation"],
            )
            stored_answer = await observed.fetchrow(
                "SELECT * FROM public.delegation_ledger WHERE id=$1", answer_ledger
            )
            assert result["wake_key"] == stored_answer["wake_key"]
            from butlers.chronicler.location_delegation_answers import answer_bundle_digest

            assert answer_birth["bundle_digest"] == answer_bundle_digest(stored_answer)
            assert stored_answer["answer_digest"] == answer_birth["body_digest"].hex()
            assert answer_birth["tool_generation"] == answer_tool
            assert answer_birth["context_generation"] == context
            assert answer_birth["parent_count"] == len(answer_parents) > 0
            assert {
                (row["parent_kind"], row["parent_generation"], row["parent_digest"])
                for row in answer_parents
            } == expected_native
            with pytest.raises(asyncpg.RaiseError, match="Location source floors are permanent"):
                async with observed.transaction():
                    await observed.execute(
                        "UPDATE location_native_delegation_answers SET body_digest=$2 WHERE ledger_id=$1",
                        answer_ledger,
                        b"x" * 32,
                    )
        assert (
            await record_answer(
                domain,
                answer_ledger,
                answering_butler="chronicler",
                answer="synthetic native answer",
            )
            is None
        )
        async with domain.acquire() as observed:
            assert (
                await observed.fetchval(
                    "SELECT body_digest FROM location_native_delegation_answers WHERE ledger_id=$1",
                    answer_ledger,
                )
                == answer_birth["body_digest"]
            )
            assert (
                await observed.fetchval(
                    "SELECT count(*) FROM location_native_delegation_answers WHERE ledger_id=$1",
                    answer_ledger,
                )
                == 1
            )
        from butlers.chronicler.location_delegation_copies import delegation_frontier_closed

        # Complete the earlier actual private ask and retain its full trace.
        # Later successful asks cannot hide an unfinished same-name sibling.
        previous_result = dict(
            status="routed", ledger_id=str(identifier), target_butler="relationship"
        )
        previous_tool = _ToolCopy(runtime, tool_generation, session_id, "delegate_ask", "core")
        previous_tool.read_observed, previous_tool.mixed_inputs = original_ask_flags
        previous_token = _current_tool_copy.set(previous_tool)
        await finish_tool_copy((previous_tool, previous_token), previous_result)
        previous_call = dict(
            name="delegate_ask",
            module="core",
            outcome="success",
            input_fingerprint=fingerprint_tool_call_payload(
                {"question": "synthetic native location question"}
            ),
            result=previous_result,
        )
        await _assert_source_question_disposal(domain, runtime, session_id, context, previous_call)
        await _assert_received_answer_disposal(domain, runtime)
        await _assert_source_answer_disposal(domain, runtime)
        unrelated_case = uuid.uuid4()
        assert await delegation_frontier_closed(domain, unrelated_case)
        # Purge predicate must not erase a declared cohort by joining only
        # surviving parents. This is a planted SQL-engine gap, not a producer.
        async with domain.acquire() as conn:
            async with conn.transaction():
                await runtime.lock_domain(conn)
                await conn.execute(
                    "INSERT INTO location_native_delegation_inputs "
                    "(question_generation,ledger_id,receiving_session,tool_generation,"
                    "context_generation,body_digest,parent_count,exclusive_input) "
                    "VALUES($1,$2,$3,$4,$5,$6,2,true)",
                    uuid.uuid4(),
                    uuid.uuid4(),
                    session_id,
                    tool_generation,
                    context,
                    b"g" * 32,
                )
        assert not await delegation_frontier_closed(domain, unrelated_case)
        tool.name, tool.generation = "delegate_ask", tool_generation
        # The core-only constructor must use this actual owning domain pool,
        # independently of the optional Memory runtime. This is migrated
        # constructor/identity proof, not online receiver/terminal evidence.
        from butlers.chronicler.location_delegation_runtime import NativeDelegationRuntime
        from butlers.chronicler.location_memory_context import register_context_writer
        from butlers.core.delegation_source import _writers, clear_writer, register_writer

        # Actual configured Memory pool/role remains distinct from core-only enrollment.
        await _assert_question_receiver_disposal(domain, runtime)
        clear_writer(domain, runtime.delegation_writer)
        core = None
        try:
            core = await NativeDelegationRuntime.create(
                domain=domain, name="chronicler", schema=runtime.identity[0], registry=object()
            )
            assert _writers[domain] is core.delegation_writer
            assert not hasattr(core, "memory")
            async with domain.acquire() as conn:
                async with conn.transaction():
                    await core.lock_domain(conn)
                    await conn.execute("SET LOCAL search_path TO public")
                    with pytest.raises(PolicyUnavailableError, match="owning writer differs"):
                        await core.lock_domain(conn)
            async with domain.acquire() as readback:
                assert await readback.fetchval("SELECT current_schema()") == runtime.identity[0]
            await _assert_question_receiver_disposal(domain, core)
            core.close()
            async with domain.acquire() as conn:
                with pytest.raises(PolicyUnavailableError, match="owning writer differs"):
                    await core.lock_domain(conn)
            assert domain not in _writers
        finally:
            if core is not None:
                core.close()
            register_writer(domain, runtime.delegation_writer)
            register_context_writer(runtime)
    finally:
        _current_tool_copy.reset(token)


async def _assert_question_receiver_disposal(domain, runtime):
    """Migrated owning-role floor/reduction/readback; planted source plan, NOT online proof."""
    import hashlib

    from butlers.chronicler.location_delegation_disposal import (
        _REDUCED_TASK,
        _close_question_receiver,
        question_receiver_status,
    )
    from butlers.chronicler.location_delegation_receivers import receiving_question_fenced
    from butlers.chronicler.location_policy import PolicyUnavailableError
    from butlers.core.scheduler import schedule_create

    generation, ledger, question, loan = (uuid.uuid4() for _ in range(4))
    decision, server = uuid.uuid4(), uuid.uuid4()
    digest = hashlib.sha256(b"synthetic receiver input").digest()
    binding = {
        "receiving_generation": generation,
        "decision_id": decision,
        "manifest_digest": b"m" * 32,
        "source_name": "chronicler",
        "question_generation": question,
        "ledger_id": ledger,
        "loan_id": loan,
        "body_digest": digest,
        "receiving_incarnation": runtime.incarnation,
    }
    # Missing own attempt cannot close, even though the exact permanent floor
    # prevents a later admission. The source-side loan alone is insufficient.
    assert await _close_question_receiver(runtime, binding) is None
    async with domain.acquire() as observed:
        assert await receiving_question_fenced(observed, generation)
        assert not await observed.fetchval(
            "SELECT EXISTS(SELECT 1 FROM location_received_delegation_dispositions "
            "WHERE receiving_generation=$1)",
            generation,
        )
    # A genuinely separate planted generation supplies the positive; never
    # refill the missing attempt behind the first permanent unknown floor.
    generation, loan = uuid.uuid4(), uuid.uuid4()
    binding = dict(binding, receiving_generation=generation, loan_id=loan)
    prompt = "synthetic full receiving question prompt"
    async with domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            await conn.execute(
                "INSERT INTO location_received_delegation_attempts "
                "(receiving_generation,ledger_id,body_digest,receiving_incarnation,server_request) "
                "VALUES($1,$2,$3,$4,$5)",
                generation,
                ledger,
                digest,
                runtime.incarnation,
                server,
            )
            await conn.execute(
                "INSERT INTO location_received_delegation_inputs "
                "(receiving_generation,ledger_id,source_name,source_incarnation,question_generation,"
                "loan_id,body_digest,receiving_incarnation,parent_count,exclusive_input,server_request) "
                "VALUES($1,$2,'chronicler',$3,$4,$5,$6,$3,1,true,$7)",
                generation,
                ledger,
                runtime.incarnation,
                question,
                loan,
                digest,
                server,
            )
            task = await schedule_create(conn, "receiver-" + str(generation), "0 0 * * *", prompt)
            await conn.execute(
                "INSERT INTO location_received_delegation_schedules "
                "(receiving_generation,task_id,prompt_digest) VALUES($1,$2,$3)",
                generation,
                task,
                hashlib.sha256(prompt.encode()).digest(),
            )
    # Actual copied processing is reserved before the permanent floor, not
    # manufactured from a terminal session after retention preparation.
    await _assert_core_question_context_disposal(domain, runtime, binding, task, prompt)
    # No final native response receipt: an enabled/ended-looking task does not
    # attest completion of the actual source-owned transient server copy.
    assert await _close_question_receiver(runtime, binding) is None
    async with domain.acquire() as observed:
        assert (
            await observed.fetchval("SELECT prompt FROM scheduled_tasks WHERE id=$1", task)
            == prompt
        )
        assert not await observed.fetchval(
            "SELECT EXISTS(SELECT 1 FROM location_received_delegation_dispositions "
            "WHERE receiving_generation=$1)",
            generation,
        )
    async with domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            await conn.execute(
                "INSERT INTO location_received_delegation_server_finished "
                "(receiving_generation,server_request,body_digest,receipt_id) VALUES($1,$2,$3,$4)",
                generation,
                server,
                digest,
                uuid.uuid4(),
            )
            await conn.execute(
                "UPDATE scheduled_tasks SET prompt=$2 WHERE id=$1",
                task,
                prompt + " independent change",
            )
    with pytest.raises(PolicyUnavailableError, match="task body changed"):
        await _close_question_receiver(runtime, binding)
    assert not await domain.fetchval(
        "SELECT EXISTS(SELECT 1 FROM location_received_delegation_dispositions "
        "WHERE receiving_generation=$1)",
        generation,
    )
    await domain.execute("UPDATE scheduled_tasks SET prompt=$2 WHERE id=$1", task, prompt)
    receipt = await _close_question_receiver(runtime, binding)
    assert receipt is not None
    async with domain.acquire() as observed:
        reduced = await observed.fetchrow(
            "SELECT prompt,enabled FROM scheduled_tasks WHERE id=$1", task
        )
        assert reduced["prompt"] == _REDUCED_TASK and reduced["enabled"] is False
        assert await receiving_question_fenced(observed, generation)
        assert (
            await observed.fetchval(
                "SELECT receipt_id FROM location_received_delegation_dispositions "
                "WHERE receiving_generation=$1",
                generation,
            )
            == receipt
        )
        with pytest.raises(asyncpg.RaiseError, match="Location source floors are permanent"):
            async with observed.transaction():
                await observed.execute(
                    "UPDATE location_received_delegation_floors SET body_digest=$2 "
                    "WHERE receiving_generation=$1",
                    generation,
                    b"x" * 32,
                )
        assert (
            await observed.fetchval(
                "SELECT body_digest FROM location_received_delegation_floors "
                "WHERE receiving_generation=$1",
                generation,
            )
            == digest
        )
    assert await _close_question_receiver(runtime, binding) == receipt
    observed = await question_receiver_status(runtime, decision, receipt)
    assert observed["loan_id"] == str(loan) and observed["manifest_digest"] == (b"m" * 32).hex()
    with pytest.raises(PolicyUnavailableError, match="floor differs"):
        await _close_question_receiver(runtime, dict(binding, body_digest=b"x" * 32))


async def _assert_source_question_disposal(domain, runtime, session_id, context, previous_call):
    """Real owning source/receiver receipt ordering; planted lineage, NOT online proof."""
    from butlers.chronicler.location_delegation_disposal import (
        _REDUCED_QUESTION,
        dispose_source_questions,
    )
    from butlers.chronicler.location_policy import PolicyUnavailableError
    from butlers.chronicler.location_tool_copies import (
        _current_tool_copy,
        _ToolCopy,
        finish_tool_copy,
    )
    from butlers.core.delegation_ledger import mark_dispatch_outcome, record_answer, record_ask
    from butlers.core.sessions import session_complete
    from butlers.core.tool_call_capture import fingerprint_tool_call_payload

    question_input = bytes.fromhex(
        fingerprint_tool_call_payload({"question": "synthetic source ledger copy"})
    )
    decision, run, generation = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    manifest = b"s" * 32
    await domain.execute(
        "INSERT INTO location_runtime_tool_intents "
        "(tool_generation,receiving_session,tool_name,module_name,input_digest) "
        "VALUES($1,$2,'delegate_ask','core',$3)",
        generation,
        session_id,
        question_input,
    )
    tool = _ToolCopy(runtime, generation, session_id, "delegate_ask", "core")
    token = _current_tool_copy.set(tool)
    try:
        ledger = uuid.UUID(
            await record_ask(
                domain,
                asking_butler="chronicler",
                question="synthetic source ledger copy",
                target_butler="relationship",
                status="pending",
                metadata={},
            )
        )
        assert tool.read_observed is True and tool.mixed_inputs is False
        question_result = {
            "ledger_id": str(ledger),
            "status": "routed",
            "target_butler": "relationship",
        }
        await finish_tool_copy((tool, token), question_result)
    finally:
        if _current_tool_copy.get() is tool:
            _current_tool_copy.reset(token)
    header = await domain.fetchrow(
        "SELECT * FROM location_native_delegation_inputs WHERE ledger_id=$1",
        ledger,
    )
    # A second real owning ask freezes its original input while the same
    # context is still live. Its remote answer receipt below is deliberately
    # planted engine evidence, never an online other-butler attestation.
    answered_text = "Synthetic answered source question"
    answered_tool = uuid.uuid4()
    answered_input = bytes.fromhex(fingerprint_tool_call_payload({"question": answered_text}))
    await domain.execute(
        "INSERT INTO location_runtime_tool_intents "
        "(tool_generation,receiving_session,tool_name,module_name,input_digest) "
        "VALUES($1,$2,'delegate_ask','core',$3)",
        answered_tool,
        session_id,
        answered_input,
    )
    second_tool = _ToolCopy(runtime, answered_tool, session_id, "delegate_ask", "core")
    second_token = _current_tool_copy.set(second_tool)
    try:
        answered_ledger = uuid.UUID(
            await record_ask(
                domain,
                asking_butler="chronicler",
                question=answered_text,
                target_butler="relationship",
                status="pending",
                metadata={},
            )
        )
        assert second_tool.read_observed is True and second_tool.mixed_inputs is False
        answered_result = dict(
            status="routed", ledger_id=str(answered_ledger), target_butler="relationship"
        )
        await finish_tool_copy((second_tool, second_token), answered_result)
    finally:
        if _current_tool_copy.get() is second_tool:
            _current_tool_copy.reset(second_token)
    answered_header = await domain.fetchrow(
        "SELECT * FROM location_native_delegation_inputs WHERE ledger_id=$1",
        answered_ledger,
    )
    # A third actual owning ask isolates the generic child engine. Its
    # receiving receipt below is planted ENGINE evidence, not online closure.
    nested_text, nested_tool_id = "Synthetic nested source question", uuid.uuid4()
    nested_input = bytes.fromhex(fingerprint_tool_call_payload({"question": nested_text}))
    await domain.execute(
        "INSERT INTO location_runtime_tool_intents "
        "(tool_generation,receiving_session,tool_name,module_name,input_digest) "
        "VALUES($1,$2,'delegate_ask','core',$3)",
        nested_tool_id,
        session_id,
        nested_input,
    )
    nested_tool = _ToolCopy(runtime, nested_tool_id, session_id, "delegate_ask", "core")
    nested_token = _current_tool_copy.set(nested_tool)
    try:
        nested_ledger = uuid.UUID(
            await record_ask(
                domain,
                asking_butler="chronicler",
                question=nested_text,
                target_butler="relationship",
                status="pending",
                metadata={},
            )
        )
        nested_result = dict(
            status="routed", ledger_id=str(nested_ledger), target_butler="relationship"
        )
        await finish_tool_copy((nested_tool, nested_token), nested_result)
    finally:
        if _current_tool_copy.get() is nested_tool:
            _current_tool_copy.reset(nested_token)
    nested_header = await domain.fetchrow(
        "SELECT * FROM location_native_delegation_inputs WHERE ledger_id=$1",
        nested_ledger,
    )
    nested_loan = uuid.uuid4()
    from butlers.chronicler.location_answer_sources import _REDUCED_ANSWER
    from butlers.core.delegation_ledger import compute_answer_digest, compute_wake_key

    remote_body = compute_answer_digest("Synthetic remote answer")
    remote_wake = compute_wake_key(answered_ledger, remote_body)
    await domain.execute(
        "UPDATE public.delegation_ledger SET status='answered',answer=$2,answer_digest=$3,"
        "answering_butler='relationship',answered_at=clock_timestamp(),wake_key=$4 WHERE id=$1",
        answered_ledger,
        _REDUCED_ANSWER,
        remote_body,
        remote_wake,
    )
    loan, receiver, incarnation = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            await conn.execute(
                "INSERT INTO location_retention_runs(run_id,policy_version,cutoff,lease_until,status) "
                "VALUES($1,1,clock_timestamp(),clock_timestamp()+interval '5 minutes','pending')",
                run,
            )
            await conn.execute(
                "INSERT INTO location_retention_plans "
                "(decision_id,run_id,policy_version,cutoff,manifest_digest,state) "
                "VALUES($1,$2,1,clock_timestamp(),$3,'holder_pending')",
                decision,
                run,
                manifest,
            )
            await conn.execute(
                "INSERT INTO location_retention_plan_outputs "
                "(decision_id,raw_id,source_revision,adapter_name,mapping_revision,output_kind,output_id) "
                "SELECT DISTINCT $1::uuid,$2::uuid,1::integer,'synthetic_source',"
                "$3::bytea,output_kind,output_id "
                "FROM location_native_copy_births WHERE receiving_session=$4::uuid",
                decision,
                uuid.uuid4(),
                b"m" * 32,
                session_id,
            )
            await conn.execute(
                "INSERT INTO location_native_delegation_loans "
                "(loan_id,question_generation,receiver_name,receiving_incarnation,"
                "receiving_generation,body_digest) VALUES($1,$2,'relationship',$3,$4,$5)",
                loan,
                header["question_generation"],
                incarnation,
                receiver,
                header["body_digest"],
            )
    await domain.execute(
        "INSERT INTO location_native_delegation_loans "
        "(loan_id,question_generation,receiver_name,receiving_incarnation,"
        "receiving_generation,body_digest) VALUES($1,$2,'relationship',$3,$4,$5)",
        nested_loan,
        nested_header["question_generation"],
        incarnation,
        uuid.uuid4(),
        nested_header["body_digest"],
    )
    # Neither a terminal-looking source nor a missing receiver receipt closes
    # the actual child; preserve the source body from a separate acquisition.
    await dispose_source_questions(domain, decision)
    assert (
        await domain.fetchval("SELECT question FROM public.delegation_ledger WHERE id=$1", ledger)
        == "synthetic source ledger copy"
    )
    await domain.execute(
        "INSERT INTO location_retention_holder_receipts "
        "(decision_id,owning_butler,holder_kind,holder_generation,source_digest,receipt_id) "
        "VALUES($1,'relationship','question_consumer',$2,$3,$4)",
        decision,
        loan,
        header["body_digest"],
        uuid.uuid4(),
    )
    await dispose_source_questions(domain, decision)
    assert not await domain.fetchval(
        "SELECT EXISTS(SELECT 1 FROM location_native_delegation_dispositions WHERE question_generation=$1)",
        header["question_generation"],
    )
    await domain.execute(
        "INSERT INTO location_runtime_context_ended(input_generation,receipt_id) VALUES($1,$2)",
        context,
        uuid.uuid4(),
    )
    await session_complete(
        domain,
        session_id,
        None,
        [
            previous_call,
            dict(
                name="delegate_ask",
                module="core",
                outcome="success",
                input_fingerprint=nested_input.hex(),
                result=nested_result,
            ),
            {
                "name": "delegate_ask",
                "module": "core",
                "outcome": "success",
                "input_fingerprint": question_input.hex(),
                "result": question_result,
            },
            {
                "name": "delegate_ask",
                "module": "core",
                "outcome": "success",
                "input_fingerprint": answered_input.hex(),
                "result": answered_result,
            },
        ],
        1,
        True,
    )
    # Business+receipt are atomic on failure too; no partial cleared source.
    with pytest.raises(RuntimeError, match="planted source disposal rollback"):
        async with domain.acquire() as conn:
            async with conn.transaction():
                await runtime.lock_domain(conn)
                await conn.execute(
                    "UPDATE public.delegation_ledger SET question=$2 WHERE id=$1",
                    ledger,
                    _REDUCED_QUESTION,
                )
                raise RuntimeError("planted source disposal rollback")
    assert (
        await domain.fetchval("SELECT question FROM public.delegation_ledger WHERE id=$1", ledger)
        == "synthetic source ledger copy"
    )
    await dispose_source_questions(domain, decision)
    async with domain.acquire() as observed:
        row = await observed.fetchrow("SELECT * FROM public.delegation_ledger WHERE id=$1", ledger)
        receipt = await observed.fetchrow(
            "SELECT * FROM location_native_delegation_dispositions WHERE question_generation=$1",
            header["question_generation"],
        )
        assert row["question"] == _REDUCED_QUESTION and row["status"] == "failed"
        assert receipt["body_digest"] == header["body_digest"]
        assert receipt["decision_id"] == decision and receipt["manifest_digest"] == manifest
        # Source context is not silently closed by the ledger receipt.
        assert not await observed.fetchval(
            "SELECT EXISTS(SELECT 1 FROM location_runtime_context_dispositions WHERE input_generation=$1)",
            context,
        )
    from butlers.chronicler.location_delegation_copies import question_digest
    from butlers.chronicler.location_delegation_disposal import source_question_status

    assert receipt["reduced_question_digest"] == question_digest(dict(row))
    status = await source_question_status(runtime, decision, receipt["receipt_id"])
    assert status["body_digest"] == header["body_digest"].hex()
    assert status["question_generation"] == str(header["question_generation"])
    await domain.execute(
        "UPDATE public.delegation_ledger SET metadata=$2::jsonb WHERE id=$1",
        ledger,
        {"copied": "Synthetic planted exact source"},
    )
    with pytest.raises(PolicyUnavailableError, match="source question is unknown"):
        await source_question_status(runtime, decision, receipt["receipt_id"])
    await domain.execute(
        "UPDATE public.delegation_ledger SET metadata=$2::jsonb WHERE id=$1", ledger, {}
    )
    assert await source_question_status(runtime, decision, receipt["receipt_id"]) == status
    await mark_dispatch_outcome(domain, ledger, status="routed")
    ordinary = _current_tool_copy.set(None)
    try:
        assert (
            await record_answer(
                domain, ledger, answering_butler="relationship", answer="synthetic late answer"
            )
            is None
        )
    finally:
        _current_tool_copy.reset(ordinary)
    await dispose_source_questions(domain, decision)
    assert (
        await domain.fetchval(
            "SELECT receipt_id FROM location_native_delegation_dispositions WHERE question_generation=$1",
            header["question_generation"],
        )
        == receipt["receipt_id"]
    )
    assert (
        await domain.fetchval("SELECT question FROM public.delegation_ledger WHERE id=$1", ledger)
        == _REDUCED_QUESTION
    )

    from contextlib import asynccontextmanager

    from butlers.chronicler.location_question_recursive import (
        dispose_owned_question_children,
        owned_question_status,
    )

    own_plan = dict(decision_id=str(decision), manifest_digest=manifest.hex(), catalog_loans=[])
    await dispose_owned_question_children(runtime, own_plan)
    async with domain.acquire() as readback:
        assert (
            await readback.fetchval(
                "SELECT question FROM public.delegation_ledger WHERE id=$1",
                nested_ledger,
            )
            == nested_text
        )
        assert not await readback.fetchval(
            "SELECT EXISTS(SELECT 1 FROM location_native_delegation_dispositions "
            "WHERE question_generation=$1)",
            nested_header["question_generation"],
        )
    # The observation is planted separately; it never claims registered
    # receiver authentication or receiving-context disposal in this engine test.
    await domain.execute(
        "INSERT INTO location_native_question_loan_observations "
        "(loan_id,decision_id,manifest_digest,receiver_receipt) VALUES($1,$2,$3,$4)",
        nested_loan,
        decision,
        manifest,
        uuid.uuid4(),
    )
    producer_updated = []

    class FaultConnection:
        def __init__(self, conn):
            self.conn = conn

        def __getattr__(self, name):
            return getattr(self.conn, name)

        async def execute(self, sql, *args):
            if "UPDATE public.delegation_ledger SET question=" in sql:
                producer_updated.append(True)
            if "INSERT INTO location_native_delegation_dispositions" in sql:
                raise RuntimeError("planted actual nested receipt fault")
            return await self.conn.execute(sql, *args)

    class FaultPool:
        @asynccontextmanager
        async def acquire(self):
            async with domain.acquire() as conn:
                yield FaultConnection(conn)

        async def execute(self, sql, *args):
            # A hypothetical separate pool write must reach the same real
            # fault and survivor check, rather than fail at a missing method.
            async with self.acquire() as conn:
                return await conn.execute(sql, *args)

    runtime.domain = FaultPool()
    try:
        with pytest.raises(RuntimeError, match="actual nested receipt fault"):
            await dispose_owned_question_children(runtime, own_plan)
    finally:
        runtime.domain = domain
    assert producer_updated == [True]
    async with domain.acquire() as readback:
        assert (
            await readback.fetchval(
                "SELECT question FROM public.delegation_ledger WHERE id=$1",
                nested_ledger,
            )
            == nested_text
        )
        assert not await readback.fetchval(
            "SELECT EXISTS(SELECT 1 FROM location_native_delegation_dispositions "
            "WHERE question_generation=$1)",
            nested_header["question_generation"],
        )
    await dispose_owned_question_children(runtime, own_plan)
    async with domain.acquire() as readback:
        assert (
            await readback.fetchval(
                "SELECT question FROM public.delegation_ledger WHERE id=$1",
                nested_ledger,
            )
            == _REDUCED_QUESTION
        )
        nested_receipt = await readback.fetchval(
            "SELECT receipt_id FROM location_native_delegation_dispositions "
            "WHERE question_generation=$1",
            nested_header["question_generation"],
        )
        assert nested_receipt is not None
    selected_status = await owned_question_status(runtime, decision, nested_receipt, plan=own_plan)
    assert selected_status["question_generation"] == str(nested_header["question_generation"])
    assert selected_status["body_digest"] == nested_header["body_digest"].hex()
    await dispose_owned_question_children(runtime, own_plan)
    assert (
        await owned_question_status(runtime, decision, nested_receipt, plan=own_plan)
        == selected_status
    )

    async with domain.acquire() as committed:
        assert (
            await committed.fetchval(
                "SELECT question FROM public.delegation_ledger WHERE id=$1", answered_ledger
            )
            == answered_text
        )
        assert not await committed.fetchval(
            "SELECT EXISTS(SELECT 1 FROM location_native_delegation_dispositions "
            "WHERE question_generation=$1)",
            answered_header["question_generation"],
        )
    remote_generation, remote_receipt = uuid.uuid4(), uuid.uuid4()
    await domain.execute(
        "INSERT INTO location_native_question_answer_observations "
        "(question_generation,decision_id,manifest_digest,answer_owner,answer_generation,"
        "answer_receipt,answer_body_digest,answer_bundle_digest,wake_key) "
        "VALUES($1,$2,$3,'relationship',$4,$5,$6,$7,$8)",
        answered_header["question_generation"],
        decision,
        manifest,
        remote_generation,
        remote_receipt,
        bytes.fromhex(remote_body),
        b"r" * 32,
        remote_wake,
    )
    with pytest.raises(asyncpg.RaiseError, match="permanent"):
        await domain.execute(
            "UPDATE location_native_question_answer_observations SET answer_owner='other' "
            "WHERE question_generation=$1",
            answered_header["question_generation"],
        )
    await dispose_source_questions(domain, decision)
    async with domain.acquire() as committed:
        reduced = await committed.fetchrow(
            "SELECT * FROM public.delegation_ledger WHERE id=$1",
            answered_ledger,
        )
        qreceipt = await committed.fetchrow(
            "SELECT * FROM location_native_delegation_dispositions WHERE question_generation=$1",
            answered_header["question_generation"],
        )
        assert reduced["question"] == _REDUCED_QUESTION and reduced["status"] == "answered"
        assert reduced["answer"] == _REDUCED_ANSWER and reduced["wake_key"] == remote_wake
        assert reduced["answer_digest"] == remote_body
        assert qreceipt["body_digest"] == answered_header["body_digest"]
    answered_status = await source_question_status(runtime, decision, qreceipt["receipt_id"])
    assert answered_status["answer_generation"] == str(remote_generation)
    assert answered_status["answer_receipt"] == str(remote_receipt)
    await domain.execute(
        "UPDATE public.delegation_ledger SET wake_key=$2 WHERE id=$1",
        answered_ledger,
        "changed",
    )
    with pytest.raises(PolicyUnavailableError, match="source question is unknown"):
        await source_question_status(runtime, decision, qreceipt["receipt_id"])
    await domain.execute(
        "UPDATE public.delegation_ledger SET wake_key=$2 WHERE id=$1",
        answered_ledger,
        remote_wake,
    )
    assert (
        await source_question_status(runtime, decision, qreceipt["receipt_id"]) == answered_status
    )


async def _assert_core_question_context_disposal(domain, runtime, binding, task, prompt):
    """Real configured core-only SQL profile; producer/source cells planted, not online proof."""
    import hashlib

    from butlers.chronicler.location_delegation_contexts import dispose_core_question_contexts
    from butlers.chronicler.location_delegation_disposal import _close_question_receiver
    from butlers.core.session_process_logs import write as write_process_log
    from butlers.core.sessions import session_complete, session_create
    from butlers.location_retention import content_digest

    dispose_context = dispose_core_question_contexts
    if getattr(runtime, "memory", None) is not None:
        from butlers.chronicler.location_delegation_contexts import dispose_memory_question_contexts

        async def dispose_context(runtime, binding):
            # Planted source plan for SQL-engine proof only, never online/source admission.
            await dispose_memory_question_contexts(
                runtime,
                binding,
                dict(
                    decision_id=str(binding["decision_id"]),
                    manifest_digest=binding["manifest_digest"].hex(),
                    catalog_loans=[],
                ),
            )

    generation, claim = uuid.uuid4(), uuid.uuid4()
    system = "Independent configured core instructions stay byte exact"
    session = await session_create(
        domain,
        prompt=prompt,
        trigger_source="trigger",
        request_id=str(uuid.uuid4()),
        effective_system_prompt=system,
        prompt_digest=hashlib.sha256(system.encode()).hexdigest(),
        prompt_provenance=[],
    )
    digest = hashlib.sha256(prompt.encode()).digest()
    empty = hashlib.sha256(b"").digest()
    bundle = content_digest(
        {
            "loans": [],
            "context": empty.hex(),
            "system": hashlib.sha256(system.encode()).hexdigest(),
            "prompt": digest.hex(),
        }
    )
    async with domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            await conn.execute(
                "INSERT INTO location_received_delegation_claims "
                "(claim_generation,receiving_generation,task_id,prompt_digest,receiving_incarnation,exclusive_input) "
                "VALUES($1,$2,$3,$4,$5,true)",
                claim,
                binding["receiving_generation"],
                task,
                digest,
                runtime.incarnation,
            )
            await conn.execute(
                "INSERT INTO location_runtime_context_intents(input_generation,receiving_session) VALUES($1,$2)",
                generation,
                session,
            )
            await conn.execute(
                "INSERT INTO location_runtime_context_question_intents(input_generation,claim_generation) VALUES($1,$2)",
                generation,
                claim,
            )
            await conn.execute(
                "INSERT INTO location_runtime_context_bindings "
                "(input_generation,receiving_session,bundle_digest,context_digest,system_digest,prompt_digest,exclusive_input,context_bytes) "
                "VALUES($1,$2,$3,$4,$5,$6,true,0)",
                generation,
                session,
                bundle,
                empty,
                hashlib.sha256(system.encode()).digest(),
                digest,
            )
            await conn.execute(
                "INSERT INTO location_received_delegation_contexts "
                "(input_generation,claim_generation,receiving_session,bundle_digest) VALUES($1,$2,$3,$4)",
                generation,
                claim,
                session,
                bundle,
            )
    await write_process_log(domain, session, command=prompt, stderr="synthetic copied output")
    assert await _close_question_receiver(runtime, binding) is None
    await dispose_context(runtime, binding)
    assert await domain.fetchval("SELECT prompt FROM sessions WHERE id=$1", session) == prompt
    assert not await domain.fetchval(
        "SELECT EXISTS(SELECT 1 FROM location_runtime_context_dispositions WHERE input_generation=$1)",
        generation,
    )
    await session_complete(domain, session, "synthetic derived response", [], 1, True)
    episode = None
    if getattr(runtime, "memory", None) is not None:
        from butlers.chronicler.location_memory_context import (
            _current_runtime_context,
            _RuntimeContext,
        )
        from butlers.modules.memory.storage import store_episode

        native = _RuntimeContext(runtime, generation, session, True, admitted=True)
        token = _current_runtime_context.set(native)
        try:
            episode = await store_episode(
                runtime.memory,
                "synthetic derived response",
                runtime.name,
                SimpleNamespace(
                    model_name="synthetic-native-receiving", embed=lambda _body: [0.0] * 384
                ),
                session_id=session,
            )
        finally:
            _current_runtime_context.reset(token)
        async with runtime.memory.acquire() as observed:
            assert (
                await observed.fetchval("SELECT content FROM episodes WHERE id=$1", episode)
                == "synthetic derived response"
            )
        async with domain.acquire() as observed:
            assert (
                await observed.fetchval(
                    "SELECT episode_id FROM location_runtime_context_episodes WHERE input_generation=$1",
                    generation,
                )
                == episode
            )
    async with domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            await conn.execute(
                "INSERT INTO location_received_delegation_claims_ended(claim_generation,receipt_id) VALUES($1,$2)",
                claim,
                uuid.uuid4(),
            )
            await conn.execute(
                "INSERT INTO location_runtime_context_ended(input_generation,receipt_id) VALUES($1,$2)",
                generation,
                uuid.uuid4(),
            )
    await dispose_context(runtime, binding)
    async with domain.acquire() as observed:
        row = await observed.fetchrow("SELECT * FROM sessions WHERE id=$1", session)
        receipt = await observed.fetchval(
            "SELECT receipt_id FROM location_runtime_context_dispositions WHERE input_generation=$1",
            generation,
        )
        assert receipt is not None
        diagnostic = await observed.fetchrow(
            "SELECT command,stderr FROM session_process_logs WHERE session_id=$1", session
        )
        assert (
            diagnostic["command"] == "[Location input forgotten]" and diagnostic["stderr"] is None
        )
        assert row["prompt"] == "[Location input forgotten]"
        assert row["result"] == "[Location output forgotten]"
        assert row["effective_system_prompt"] == system and row["prompt_provenance"] == []
    if episode is not None:
        async with runtime.memory.acquire() as observed:
            assert not await observed.fetchval(
                "SELECT EXISTS(SELECT 1 FROM episodes WHERE id=$1)", episode
            )
    await dispose_context(runtime, binding)
    assert (
        await domain.fetchval(
            "SELECT receipt_id FROM location_runtime_context_dispositions WHERE input_generation=$1",
            generation,
        )
        == receipt
    )

    await write_process_log(domain, session, command=prompt, stderr="synthetic late copied output")
    await dispose_context(runtime, binding)
    async with domain.acquire() as observed:
        diagnostic = await observed.fetchrow(
            "SELECT command,stderr FROM session_process_logs WHERE session_id=$1", session
        )
        assert diagnostic["command"] == "[Location-derived diagnostic forgotten]"
        assert diagnostic["stderr"] is None
        assert (
            await observed.fetchval(
                "SELECT receipt_id FROM location_runtime_context_dispositions WHERE input_generation=$1",
                generation,
            )
            == receipt
        )


async def _assert_received_answer_disposal(domain, runtime):
    """Existing migrated real-role species; planted source plan is NOT online authority."""
    import hashlib

    from butlers.chronicler.location_answer_disposal import (
        _REDUCED_RETURN,
        _close_answer_receiver,
        answer_receiver_status,
    )
    from butlers.chronicler.location_delegation_returns import finish_answer_server
    from butlers.chronicler.location_policy import PolicyUnavailableError
    from butlers.core.scheduler import schedule_create

    generation, ledger, loan, server, decision, answer, source_inc = [
        uuid.uuid4() for _ in range(7)
    ]
    binding = dict(
        receiving_generation=generation,
        decision_id=decision,
        manifest_digest=b"m" * 32,
        bundle_digest=b"b" * 32,
        source_name="relationship",
        answer_generation=answer,
        ledger_id=ledger,
        loan_id=loan,
        source_incarnation=source_inc,
        receiving_incarnation=runtime.incarnation,
    )
    prompt = "synthetic full native answer return"
    task = await schedule_create(domain, "planted-answer-" + str(generation), "0 0 * * *", prompt)
    async with domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            await conn.execute(
                "INSERT INTO location_received_answer_attempts "
                "(receiving_generation,ledger_id,source_name,wake_key,receiving_incarnation,server_request) "
                "VALUES($1,$2,'relationship','synthetic wake',$3,$4)",
                generation,
                ledger,
                runtime.incarnation,
                server,
            )
            await conn.execute(
                "INSERT INTO location_received_answer_inputs "
                "(receiving_generation,source_name,answer_generation,loan_id,bundle_digest,"
                "source_incarnation,parent_count,exclusive_input) "
                "VALUES($1,'relationship',$2,$3,$4,$5,2,true)",
                generation,
                answer,
                loan,
                b"b" * 32,
                source_inc,
            )
            await conn.execute(
                "INSERT INTO location_received_answer_schedules "
                "(receiving_generation,task_id,prompt_digest) VALUES($1,$2,$3)",
                generation,
                task,
                hashlib.sha256(prompt.encode()).digest(),
            )
    assert await _close_answer_receiver(runtime, binding, complete=True) is None
    async with domain.acquire() as observed:
        floor = await observed.fetchrow(
            "SELECT * FROM location_received_answer_floors WHERE receiving_generation=$1",
            generation,
        )
        assert all(floor[key] == value for key, value in binding.items())
        assert (
            await observed.fetchval("SELECT prompt FROM scheduled_tasks WHERE id=$1", task)
            == prompt
        )
        assert not await observed.fetchval(
            "SELECT EXISTS(SELECT 1 FROM location_received_answer_dispositions WHERE receiving_generation=$1)",
            generation,
        )
    with pytest.raises(RuntimeError, match="synthetic outer disposal rollback"):
        async with domain.acquire() as conn:
            async with conn.transaction():
                await runtime.lock_domain(conn)
                await conn.execute(
                    "INSERT INTO location_received_answer_server_finished "
                    "(receiving_generation,server_request,receipt_id) VALUES($1,$2,$3)",
                    generation,
                    server,
                    uuid.uuid4(),
                )
                raise RuntimeError("synthetic outer disposal rollback")
    assert await _close_answer_receiver(runtime, binding, complete=True) is None
    await finish_answer_server(runtime, generation, server)
    sibling, sibling_loan, sibling_server, claim = [uuid.uuid4() for _ in range(4)]
    sibling_binding = binding | {"receiving_generation": sibling, "loan_id": sibling_loan}
    async with domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            await conn.execute(
                "INSERT INTO location_received_answer_attempts "
                "(receiving_generation,ledger_id,source_name,wake_key,receiving_incarnation,server_request) "
                "VALUES($1,$2,'relationship','synthetic wake',$3,$4)",
                sibling,
                ledger,
                runtime.incarnation,
                sibling_server,
            )
            await conn.execute(
                "INSERT INTO location_received_answer_inputs "
                "(receiving_generation,source_name,answer_generation,loan_id,bundle_digest,"
                "source_incarnation,parent_count,exclusive_input) "
                "VALUES($1,'relationship',$2,$3,$4,$5,2,true)",
                sibling,
                answer,
                sibling_loan,
                b"b" * 32,
                source_inc,
            )
            await conn.execute(
                "INSERT INTO location_received_answer_schedules "
                "(receiving_generation,task_id,prompt_digest) VALUES($1,$2,$3)",
                sibling,
                task,
                hashlib.sha256(prompt.encode()).digest(),
            )
    assert await _close_answer_receiver(runtime, sibling_binding, complete=True) is None
    assert await _close_answer_receiver(runtime, binding, complete=True) is None
    async with domain.acquire() as observed:
        assert (
            await observed.fetchval("SELECT prompt FROM scheduled_tasks WHERE id=$1", task)
            == prompt
        )
        assert not await observed.fetchval(
            "SELECT EXISTS(SELECT 1 FROM location_received_answer_dispositions WHERE receiving_generation=$1)",
            generation,
        )
    await finish_answer_server(runtime, sibling, sibling_server)
    # A claim can precede a later duplicate receiving binding. Its original
    # cohort names ONLY the sibling, so the first generation's local claim
    # query cannot detect this genuinely still-live shared-task processing.
    async with domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            await conn.execute(
                "INSERT INTO location_received_answer_claims "
                "(claim_generation,task_id,prompt_digest,bundle_digest,parent_count,"
                "receiving_incarnation,exclusive_input) VALUES($1,$2,$3,$4,1,$5,true)",
                claim,
                task,
                hashlib.sha256(prompt.encode()).digest(),
                b"c" * 32,
                runtime.incarnation,
            )
            await conn.execute(
                "INSERT INTO location_received_answer_claim_parents "
                "(claim_generation,receiving_generation,bundle_digest) VALUES($1,$2,$3)",
                claim,
                sibling,
                b"b" * 32,
            )
    assert await _close_answer_receiver(runtime, binding, complete=True) is None
    async with domain.acquire() as observed:
        assert (
            await observed.fetchval("SELECT prompt FROM scheduled_tasks WHERE id=$1", task)
            == prompt
        )
        assert not await observed.fetchval(
            "SELECT EXISTS(SELECT 1 FROM location_received_answer_dispositions WHERE receiving_generation=$1)",
            generation,
        )
    # This planted failed-before-context processing lifetime is now ended.
    # No runtime/context descendant was created; ordinary healthy later-close
    # must remain possible without destroying the original task prematurely.
    await domain.execute(
        "INSERT INTO location_received_answer_claims_ended(claim_generation,receipt_id) VALUES($1,$2)",
        claim,
        uuid.uuid4(),
    )
    # A changed task cannot be silently reduced. Its original digest and all
    # immutable floor fields survive the refusal/rollback.
    await domain.execute("UPDATE scheduled_tasks SET prompt=$2 WHERE id=$1", task, "changed return")
    with pytest.raises(PolicyUnavailableError, match="return task changed"):
        await _close_answer_receiver(runtime, binding, complete=True)
    assert (
        await domain.fetchval("SELECT prompt FROM scheduled_tasks WHERE id=$1", task)
        == "changed return"
    )
    assert not await domain.fetchval(
        "SELECT EXISTS(SELECT 1 FROM location_received_answer_dispositions WHERE receiving_generation=$1)",
        generation,
    )
    await domain.execute("UPDATE scheduled_tasks SET prompt=$2 WHERE id=$1", task, prompt)
    receipt = await _close_answer_receiver(runtime, binding, complete=True)
    assert receipt is not None
    observed = await answer_receiver_status(runtime, decision, receipt)
    assert observed["receipt_id"] == str(receipt)
    assert observed["bundle_digest"] == binding["bundle_digest"].hex()
    assert observed["loan_id"] == str(loan)
    async with domain.acquire() as committed:
        reduced = await committed.fetchrow(
            "SELECT prompt,enabled FROM scheduled_tasks WHERE id=$1", task
        )
        assert reduced["prompt"] == _REDUCED_RETURN and reduced["enabled"] is False
    assert await _close_answer_receiver(runtime, binding, complete=True) == receipt
    sibling_receipt = await _close_answer_receiver(runtime, sibling_binding, complete=True)
    assert sibling_receipt is not None and sibling_receipt != receipt
    assert await _close_answer_receiver(runtime, sibling_binding, complete=True) == sibling_receipt
    assert (await answer_receiver_status(runtime, decision, sibling_receipt))["loan_id"] == str(
        sibling_loan
    )
    with pytest.raises(PolicyUnavailableError, match="receiving floor differs"):
        await _close_answer_receiver(runtime, binding | {"loan_id": uuid.uuid4()}, complete=True)
    assert (await answer_receiver_status(runtime, decision, receipt))["receipt_id"] == str(receipt)
    with pytest.raises(asyncpg.RaiseError, match="Location source floors are permanent"):
        async with domain.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "UPDATE location_received_answer_dispositions SET receipt_id=$2 WHERE receiving_generation=$1",
                    generation,
                    uuid.uuid4(),
                )
    assert (await answer_receiver_status(runtime, decision, receipt))["receipt_id"] == str(receipt)


async def _assert_source_answer_disposal(domain, runtime):
    """Real own writer/reducer/rollback/receipt; planted source/remote engine scope.

    The actual canonical first-answer producer captures a fresh configured Tool
    and full input binding. Initial native births and the receiving observation
    below are deliberately planted, NOT online route/remote erasure proof.
    """
    import hashlib

    from butlers.chronicler.location_answer_disposal import source_answer_cohort
    from butlers.chronicler.location_answer_sources import (
        _REDUCED_ANSWER,
        dispose_source_answers,
        source_answer_status,
    )
    from butlers.chronicler.location_policy import PolicyUnavailableError
    from butlers.chronicler.location_tool_copies import (
        _current_tool_copy,
        _ToolCopy,
        finish_tool_copy,
    )
    from butlers.core.delegation_ledger import record_answer
    from butlers.core.sessions import session_complete, session_create
    from butlers.core.tool_call_capture import fingerprint_tool_call_payload
    from butlers.location_retention import content_digest

    ledger, context, tool_id, native, output, decision, run, loan, receiving, receiver_inc = [
        uuid.uuid4() for _ in range(10)
    ]
    prompt, system, answer = (
        "Synthetic source prompt",
        "Synthetic fixed base",
        "Synthetic source answer",
    )
    session = await session_create(
        domain,
        prompt=prompt,
        trigger_source="trigger",
        request_id=str(uuid.uuid4()),
        effective_system_prompt=system,
        prompt_digest=hashlib.sha256(system.encode()).hexdigest(),
        prompt_provenance=[],
    )
    pd, sd, cd = [hashlib.sha256(value.encode()).digest() for value in (prompt, system, "")]
    bundle = content_digest(dict(loans=[], context=cd.hex(), system=sd.hex(), prompt=pd.hex()))
    input_digest = bytes.fromhex(
        fingerprint_tool_call_payload(dict(ledger_id=str(ledger), answer=answer))
    )
    async with domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            await conn.execute(
                "INSERT INTO location_runtime_context_intents(input_generation,receiving_session) VALUES($1,$2)",
                context,
                session,
            )
            await conn.execute(
                "INSERT INTO location_runtime_context_bindings "
                "(input_generation,receiving_session,bundle_digest,context_digest,system_digest,"
                "prompt_digest,exclusive_input,context_bytes) VALUES($1,$2,$3,$4,$5,$6,true,0)",
                context,
                session,
                bundle,
                cd,
                sd,
                pd,
            )
            await conn.execute(
                "INSERT INTO location_native_copy_births "
                "(copy_generation,output_kind,output_id,input_digest,lineage_known,receiving_session,"
                "exclusive_input,producer_kind) VALUES($1,'point_event',$2,$3,true,$4,true,'native_mcp')",
                native,
                output,
                pd,
                session,
            )
            await conn.execute(
                "INSERT INTO location_runtime_tool_intents "
                "(tool_generation,receiving_session,tool_name,module_name,input_digest) "
                "VALUES($1,$2,'delegate_answer','core',$3)",
                tool_id,
                session,
                input_digest,
            )
            await conn.execute(
                "INSERT INTO public.delegation_ledger "
                "(id,asking_butler,question,target_butler,status) "
                "VALUES($1,'relationship','Synthetic immutable question','chronicler','routed')",
                ledger,
            )
    tool = _ToolCopy(runtime, tool_id, session, "delegate_answer", "core")
    token = _current_tool_copy.set(tool)
    try:
        actual = await record_answer(domain, ledger, answering_butler="chronicler", answer=answer)
        assert actual is not None and tool.read_observed is True and tool.mixed_inputs is False
        result = dict(status="ok", ledger_id=str(ledger), answer_recorded=True)
        await finish_tool_copy((tool, token), result)
    finally:
        if _current_tool_copy.get() is tool:
            _current_tool_copy.reset(token)
    call = dict(
        name="delegate_answer",
        module="core",
        outcome="success",
        input_fingerprint=input_digest.hex(),
        result=result,
    )
    await session_complete(domain, session, None, [call], 1, True)
    header = await domain.fetchrow(
        "SELECT * FROM location_native_delegation_answers WHERE ledger_id=$1", ledger
    )
    assert header["parent_count"] == 1 and header["exclusive_input"] is True
    manifest = b"v" * 32
    plan = dict(
        decision_id=str(decision),
        manifest_digest=manifest.hex(),
        source_name=runtime.name,
        catalog_loans=[],
    )
    async with domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            await conn.execute(
                "INSERT INTO location_retention_runs(run_id,policy_version,cutoff,lease_until,status) "
                "VALUES($1,1,clock_timestamp(),clock_timestamp()+interval '5 minutes','pending')",
                run,
            )
            await conn.execute(
                "INSERT INTO location_retention_plans(decision_id,run_id,policy_version,cutoff,manifest_digest,state) "
                "VALUES($1,$2,1,clock_timestamp(),$3,'holder_pending')",
                decision,
                run,
                manifest,
            )
            await conn.execute(
                "INSERT INTO location_retention_plan_outputs "
                "(decision_id,raw_id,source_revision,adapter_name,mapping_revision,output_kind,output_id) "
                "VALUES($1,$2,1,'synthetic_source',$3,'point_event',$4)",
                decision,
                uuid.uuid4(),
                b"m" * 32,
                output,
            )
            await conn.execute(
                "INSERT INTO location_native_answer_loans "
                "(loan_id,answer_generation,receiving_generation,receiver_name,receiving_incarnation,bundle_digest,source_incarnation) "
                "VALUES($1,$2,$3,'relationship',$4,$5,$6)",
                loan,
                header["answer_generation"],
                receiving,
                receiver_inc,
                header["bundle_digest"],
                runtime.incarnation,
            )
    # A body/Tool completion alone cannot attest an unfinished context or receiver.
    assert await dispose_source_answers(runtime, plan) == []
    assert (
        await domain.fetchval("SELECT answer FROM public.delegation_ledger WHERE id=$1", ledger)
        == answer
    )
    await domain.execute(
        "INSERT INTO location_runtime_context_ended(input_generation,receipt_id) VALUES($1,$2)",
        context,
        uuid.uuid4(),
    )
    assert await dispose_source_answers(runtime, plan) == []
    # This is a planted receiver observation for reducer SQL, not a network receipt.
    await domain.execute(
        "INSERT INTO location_native_answer_observations(loan_id,decision_id,manifest_digest,receiver_receipt) VALUES($1,$2,$3,$4)",
        loan,
        decision,
        manifest,
        uuid.uuid4(),
    )
    # The exact own business write and immutable reference receipt roll back together.
    with pytest.raises(RuntimeError, match="planted answer rollback"):
        async with domain.acquire() as conn:
            async with conn.transaction():
                await runtime.lock_domain(conn)
                await conn.execute(
                    "UPDATE public.delegation_ledger SET answer=$2 WHERE id=$1",
                    ledger,
                    _REDUCED_ANSWER,
                )
                await conn.execute(
                    "INSERT INTO location_native_delegation_answer_dispositions "
                    "(answer_generation,decision_id,manifest_digest,body_digest,receipt_id) VALUES($1,$2,$3,$4,$5)",
                    header["answer_generation"],
                    decision,
                    manifest,
                    header["body_digest"],
                    uuid.uuid4(),
                )
                raise RuntimeError("planted answer rollback")
    async with domain.acquire() as observed:
        assert (
            await observed.fetchval(
                "SELECT answer FROM public.delegation_ledger WHERE id=$1", ledger
            )
            == answer
        )
        assert not await observed.fetchval(
            "SELECT EXISTS(SELECT 1 FROM location_native_delegation_answer_dispositions WHERE answer_generation=$1)",
            header["answer_generation"],
        )
    # Invoke the actual reducer with the same real physical connection. Only
    # its receipt INSERT faults after its own actual canonical UPDATE; no
    # handwritten alternate producer path can make this control green.
    from contextlib import asynccontextmanager

    class ReceiptFaultConnection:
        def __init__(self, conn):
            self.conn = conn

        def __getattr__(self, name):
            return getattr(self.conn, name)

        async def execute(self, sql, *args):
            if sql.startswith("UPDATE public.delegation_ledger SET answer="):
                value = await self.conn.execute(sql, *args)
                producer_updates.append(True)
                return value
            if sql.startswith("INSERT INTO location_native_delegation_answer_dispositions "):
                assert producer_updates
                producer_faults.append(True)
                raise RuntimeError("actual answer receipt insertion fault")
            return await self.conn.execute(sql, *args)

    class ReceiptFaultPool:
        @asynccontextmanager
        async def acquire(self):
            async with domain.acquire() as conn:
                yield ReceiptFaultConnection(conn)

        async def execute(self, sql, *args):
            # A regression using a separately committed pool write must reach
            # the same fault, then fail the independent survivor assertion.
            async with self.acquire() as conn:
                return await conn.execute(sql, *args)

    producer_updates = []
    producer_faults = []
    runtime.domain = ReceiptFaultPool()
    try:
        with pytest.raises(RuntimeError, match="actual answer receipt insertion fault"):
            await dispose_source_answers(runtime, plan)
    finally:
        runtime.domain = domain
    assert producer_faults == [True]
    async with domain.acquire() as committed:
        assert (
            await committed.fetchval(
                "SELECT answer FROM public.delegation_ledger WHERE id=$1", ledger
            )
            == answer
        )
        assert not await committed.fetchval(
            "SELECT EXISTS(SELECT 1 FROM location_native_delegation_answer_dispositions "
            "WHERE answer_generation=$1)",
            header["answer_generation"],
        )
    receipts = await dispose_source_answers(runtime, plan)
    assert len(receipts) == 1
    async with domain.acquire() as observed:
        reduced = await observed.fetchrow(
            "SELECT * FROM public.delegation_ledger WHERE id=$1", ledger
        )
        assert reduced["answer"] == _REDUCED_ANSWER
        assert reduced["question"] == actual["question"]
        assert (
            reduced["answer_digest"] == actual["answer_digest"]
            and reduced["wake_key"] == actual["wake_key"]
        )
        assert not await observed.fetchval(
            "SELECT EXISTS(SELECT 1 FROM location_runtime_context_dispositions WHERE input_generation=$1)",
            context,
        )
        async with observed.transaction():
            cohort = await source_answer_cohort(runtime, observed, plan)
        selected = [
            row for row in cohort if row["answer_generation"] == str(header["answer_generation"])
        ]
        assert len(selected) == 1 and selected[0]["complete_input"] is True
        with pytest.raises(asyncpg.RaiseError, match="Location source floors are permanent"):
            async with observed.transaction():
                await observed.execute(
                    "UPDATE location_native_answer_observations SET receiver_receipt=$2 WHERE loan_id=$1",
                    loan,
                    uuid.uuid4(),
                )
    assert await dispose_source_answers(runtime, plan) == receipts
    status = await source_answer_status(runtime, decision, uuid.UUID(receipts[0]), plan=plan)
    assert (
        status["body_digest"] == header["body_digest"].hex()
        and status["bundle_digest"] == header["bundle_digest"].hex()
    )
    await domain.execute(
        "UPDATE public.delegation_ledger SET answer='Synthetic unrelated tamper' WHERE id=$1",
        ledger,
    )
    with pytest.raises(PolicyUnavailableError, match="source answer is unknown"):
        await source_answer_status(runtime, decision, uuid.UUID(receipts[0]), plan=plan)
    await domain.execute(
        "UPDATE public.delegation_ledger SET answer=$2 WHERE id=$1", ledger, _REDUCED_ANSWER
    )
    assert await dispose_source_answers(runtime, plan) == receipts
