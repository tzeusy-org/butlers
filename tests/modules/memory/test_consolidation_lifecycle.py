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
from contextlib import asynccontextmanager
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


@asynccontextmanager
async def _memory_pool(db, *, schema: str | None = None):
    """A fresh DB on the real core + memory chains, the memory chain in *schema*."""
    kwargs = {} if schema is None else {"schemas": {"memory": schema}, "schema": schema}
    yield await db("core", "memory", **kwargs)


async def _register_test_predicate(pool) -> None:
    """Consolidation output in these tests writes facts with a test-only predicate."""
    await pool.execute(
        "INSERT INTO predicate_registry (name) VALUES ('test_property') ON CONFLICT DO NOTHING"
    )


async def _insert_confirmable_fact(pool, fact_id: uuid.UUID) -> None:
    """A pre-existing fact for consolidation output to confirm."""
    await pool.execute(
        "INSERT INTO facts (id, subject, predicate, content) "
        "VALUES ($1, 'Owner', 'confirmable_note', 'an existing fact')",
        fact_id,
    )


class _StaticEmbeddingEngine:
    model_name = "consolidation-lifecycle-test"

    def embed(self, _text: str) -> list[float]:
        # The real columns are vector(384); a unit vector keeps cosine distance defined.
        return [1.0] + [0.0] * 383


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
    db,
) -> None:
    """A live scheduler claims due retries but never terminal or premature rows."""
    async with _memory_pool(db) as pool:
        await _register_test_predicate(pool)
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


@pytest.mark.pg_clock
async def test_private_memory_claim_path_does_not_retry_failed_episodes(
    db,
    monkeypatch,
) -> None:
    """Chronicler's live private hook claims pending work but leaves due failures untouched."""
    async with _memory_pool(db, schema="chronicler_mem") as pool:
        assert await pool.fetchval("SELECT current_schema()") == "chronicler_mem"
        await _register_test_predicate(pool)
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
                db=SimpleNamespace(pool=pool, schema="chronicler"),
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
        finally:
            await module.on_shutdown()


@pytest.mark.pg_clock
async def test_registered_relationship_admin_dry_run_leaves_due_failed_retry_for_scheduler(
    db,
) -> None:
    """MCP dry runs cannot steal a due retry from the live scheduled Spawner."""
    async with _memory_pool(db) as pool:
        await _register_test_predicate(pool)
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
    db,
) -> None:
    """Concurrent runs cannot both claim one due failed episode."""
    async with _memory_pool(db) as pool:
        await _register_test_predicate(pool)
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
    db,
) -> None:
    """A worker may only fail its own active lease, without persisting raw errors."""
    async with _memory_pool(db) as pool:
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
    db,
) -> None:
    """Lifecycle state cannot advance without its durable audit event."""
    async with _memory_pool(db) as pool:
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
    db,
) -> None:
    """An expired worker lease cannot overwrite a replacement claimant's state."""
    async with _memory_pool(db) as pool:
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
    db,
) -> None:
    """A stale or repeated terminal write cannot increment or replay a dead letter."""
    async with _memory_pool(db) as pool:
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
    db,
) -> None:
    """The retry reset is a genuine re-enqueue, not a cosmetic status relabel.

    Resets a dead-lettered episode via ``storage.retry_dead_letter_episode``,
    then proves the reset actually matters by running the real scheduler
    claim query (``run_consolidation``) against it: a relabel with poisoned
    lifecycle fields (stale attempts/lease) would not be reclaimable, so the
    live worker lease this asserts on is direct evidence of reconsideration
    (bu-6t8ix.2 acceptance criterion 4).
    """
    async with _memory_pool(db) as pool:
        await _register_test_predicate(pool)
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
            == "episode_consolidation_requeued"
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
    db,
) -> None:
    """A non-dead_letter episode is rejected, not silently reset."""
    async with _memory_pool(db) as pool:
        episode_id = await _insert_episode(pool, status="failed", attempts=1)

        with pytest.raises(EpisodeNotDeadLetterError):
            await retry_dead_letter_episode(pool, episode_id)

        row = await _episode_lifecycle(pool, episode_id)
        assert row["consolidation_status"] == "failed"
        assert row["consolidation_attempts"] == 1
        assert await pool.fetchval("SELECT count(*) FROM memory_events") == 0


async def test_retry_dead_letter_episode_returns_none_for_unknown_id(
    db,
) -> None:
    """An id with no matching episode returns None rather than raising."""
    async with _memory_pool(db) as pool:
        assert await retry_dead_letter_episode(pool, uuid.uuid4()) is None


async def test_success_transition_is_fenced_and_clears_retry_lifecycle_state(
    db,
) -> None:
    """Only the current claimant can finalize a group and clear retry evidence."""
    async with _memory_pool(db) as pool:
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
    db,
) -> None:
    """A terminal audit failure rolls back every write in the protected group."""
    async with _memory_pool(db) as pool:
        await _register_test_predicate(pool)
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
        await _insert_confirmable_fact(pool, confirmation_id)
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
    db,
) -> None:
    """A completed runtime loses every write when a later claimant owns its episodes."""
    async with _memory_pool(db) as pool:
        await _register_test_predicate(pool)
        episode_id = await _insert_episode(pool)
        confirmation_id = uuid.uuid4()
        await _insert_confirmable_fact(pool, confirmation_id)
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
