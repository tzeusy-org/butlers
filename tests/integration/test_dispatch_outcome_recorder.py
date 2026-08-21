"""Real-Postgres coverage for the atomic runtime-attention producers.

REQ-model-catalog-001; REQ-runtime-attention-outbox-001;
REQ-dashboard-spend-dashboard-001.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest

from butlers.core.dispatch_outcomes import record_dispatch_attempt
from butlers.core.model_routing import CEILING_DENIAL_REASON_PREFIX, get_breaker_state

pytestmark = [pytest.mark.db, pytest.mark.integration]


class _RoleAcquire:
    def __init__(self, pool: asyncpg.Pool, role: str) -> None:
        self._pool = pool
        self._role = role
        self._context: Any = None
        self.connection: asyncpg.Connection | None = None

    async def __aenter__(self) -> asyncpg.Connection:
        self._context = self._pool.acquire()
        self.connection = await self._context.__aenter__()
        await self.connection.execute(f'SET ROLE "{self._role}"')
        return self.connection

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        assert self.connection is not None
        await self.connection.execute("RESET ROLE")
        await self._context.__aexit__(exc_type, exc, traceback)


class _RolePool:
    """Small test adapter that gives every recorder acquisition one runtime role."""

    def __init__(self, pool: asyncpg.Pool, role: str = "butler_general_rw") -> None:
        self._pool = pool
        self._role = role

    def acquire(self) -> _RoleAcquire:
        return _RoleAcquire(self._pool, self._role)

    async def execute(self, statement: str, *args: object) -> str:
        async with self.acquire() as connection:
            return await connection.execute(statement, *args)

    async def fetchval(self, statement: str, *args: object) -> object:
        async with self.acquire() as connection:
            return await connection.fetchval(statement, *args)

    async def fetchrow(self, statement: str, *args: object) -> asyncpg.Record | None:
        async with self.acquire() as connection:
            return await connection.fetchrow(statement, *args)


class _FailAfterAttemptConnection:
    """Delegate a real connection but fail the producer after its insert."""

    def __init__(self, connection: asyncpg.Connection) -> None:
        self._connection = connection

    def transaction(self) -> Any:
        return self._connection.transaction()

    async def execute(self, statement: str, *args: object) -> str:
        return await self._connection.execute(statement, *args)

    async def fetchval(self, statement: str, *args: object) -> object:
        if "append_runtime_attention_model_breaker" in statement:
            raise RuntimeError("injected producer failure after attempt insert")
        return await self._connection.fetchval(statement, *args)

    async def fetchrow(self, statement: str, *args: object) -> asyncpg.Record | None:
        return await self._connection.fetchrow(statement, *args)


class _FailAfterAttemptAcquire(_RoleAcquire):
    async def __aenter__(self) -> _FailAfterAttemptConnection:
        connection = await super().__aenter__()
        return _FailAfterAttemptConnection(connection)


class _FailAfterAttemptPool(_RolePool):
    def acquire(self) -> _FailAfterAttemptAcquire:
        return _FailAfterAttemptAcquire(self._pool, self._role)


class _DelayBeforeBreakerLockConnection:
    """Pause one transaction after BEGIN but before it takes the recorder lock."""

    def __init__(
        self,
        connection: asyncpg.Connection,
        transaction_started: asyncio.Event,
        release_lock_attempt: asyncio.Event,
    ) -> None:
        self._connection = connection
        self._transaction_started = transaction_started
        self._release_lock_attempt = release_lock_attempt

    def transaction(self) -> Any:
        return self._connection.transaction()

    async def execute(self, statement: str, *args: object) -> str:
        if "pg_advisory_xact_lock" in statement:
            self._transaction_started.set()
            await self._release_lock_attempt.wait()
        return await self._connection.execute(statement, *args)

    async def fetchval(self, statement: str, *args: object) -> object:
        return await self._connection.fetchval(statement, *args)

    async def fetchrow(self, statement: str, *args: object) -> asyncpg.Record | None:
        return await self._connection.fetchrow(statement, *args)


class _DelayBeforeBreakerLockAcquire(_RoleAcquire):
    def __init__(
        self,
        pool: asyncpg.Pool,
        role: str,
        transaction_started: asyncio.Event,
        release_lock_attempt: asyncio.Event,
    ) -> None:
        super().__init__(pool, role)
        self._transaction_started = transaction_started
        self._release_lock_attempt = release_lock_attempt

    async def __aenter__(self) -> _DelayBeforeBreakerLockConnection:
        connection = await super().__aenter__()
        return _DelayBeforeBreakerLockConnection(
            connection,
            self._transaction_started,
            self._release_lock_attempt,
        )


class _DelayBeforeBreakerLockPool(_RolePool):
    def __init__(
        self,
        pool: asyncpg.Pool,
        transaction_started: asyncio.Event,
        release_lock_attempt: asyncio.Event,
    ) -> None:
        super().__init__(pool)
        self._transaction_started = transaction_started
        self._release_lock_attempt = release_lock_attempt

    def acquire(self) -> _DelayBeforeBreakerLockAcquire:
        return _DelayBeforeBreakerLockAcquire(
            self._pool,
            self._role,
            self._transaction_started,
            self._release_lock_attempt,
        )


async def _seed_catalog(pool: asyncpg.Pool, alias: str) -> uuid.UUID:
    catalog_entry_id = uuid.uuid4()
    await pool.execute(
        """
        INSERT INTO public.model_catalog (id, alias, runtime_type, model_id)
        VALUES ($1, $2, 'codex', $3)
        """,
        catalog_entry_id,
        alias,
        f"{alias}-model",
    )
    return catalog_entry_id


async def _seed_failures(
    pool: asyncpg.Pool,
    catalog_entry_id: uuid.UUID,
    count: int,
    *,
    ts: datetime,
) -> None:
    for _ in range(count):
        await pool.execute(
            """
            INSERT INTO public.model_dispatch_attempts
                (catalog_entry_id, butler, outcome, ts)
            VALUES ($1, 'general', 'runtime_failure', $2)
            """,
            catalog_entry_id,
            ts,
        )


async def _record_failure(pool: _RolePool, catalog_entry_id: uuid.UUID, index: int) -> int | None:
    return await record_dispatch_attempt(
        pool,  # type: ignore[arg-type]
        catalog_entry_id=catalog_entry_id,
        butler="general",
        outcome="runtime_failure",
        attempt_index=index,
        failure_reason="provider unavailable",
        error_code="RuntimeError",
        error_message="safe test detail",
    )


async def test_fifth_failure_opens_once_and_success_closes(
    migrated_core_postgres_pool,
) -> None:
    async with migrated_core_postgres_pool(min_pool_size=2, max_pool_size=4) as admin_pool:
        runtime_pool = _RolePool(admin_pool)
        observer_pool = _RolePool(admin_pool, "butler_switchboard_rw")
        entry_id = await _seed_catalog(admin_pool, "atomic-fifth")
        await _seed_failures(admin_pool, entry_id, 4, ts=datetime.now(UTC))

        fifth_id = await _record_failure(runtime_pool, entry_id, 4)
        sixth_id = await _record_failure(runtime_pool, entry_id, 5)

        assert isinstance(fifth_id, int)
        assert isinstance(sixth_id, int)
        assert (
            await observer_pool.fetchval(
                "SELECT count(*) FROM public.runtime_attention_outbox WHERE source='model_breaker'"
            )
            == 1
        )
        assert (
            await observer_pool.fetchval(
                """
            SELECT triggering_attempt_id
            FROM public.runtime_attention_outbox
            WHERE source='model_breaker'
            """
            )
            == fifth_id
        )
        assert (await get_breaker_state(admin_pool, entry_id)).open is True

        success_id = await record_dispatch_attempt(
            runtime_pool,  # type: ignore[arg-type]
            catalog_entry_id=entry_id,
            butler="general",
            outcome="success",
            attempt_index=6,
        )
        assert isinstance(success_id, int)
        assert (await get_breaker_state(admin_pool, entry_id)).open is False


async def test_equal_timestamp_concurrent_failures_have_one_deterministic_edge(
    migrated_core_postgres_pool,
) -> None:
    async with migrated_core_postgres_pool(min_pool_size=3, max_pool_size=6) as admin_pool:
        runtime_pool = _RolePool(admin_pool)
        observer_pool = _RolePool(admin_pool, "butler_switchboard_rw")
        entry_id = await _seed_catalog(admin_pool, "atomic-tied")
        tied_ts = datetime.now(UTC)
        await _seed_failures(admin_pool, entry_id, 4, ts=tied_ts)
        await admin_pool.execute(
            "ALTER TABLE public.model_dispatch_attempts ALTER COLUMN ts "
            f"SET DEFAULT '{tied_ts.isoformat()}'::timestamptz"
        )

        attempt_ids = await asyncio.gather(
            _record_failure(runtime_pool, entry_id, 4),
            _record_failure(runtime_pool, entry_id, 5),
        )

        assert all(isinstance(attempt_id, int) for attempt_id in attempt_ids)
        trigger_id = await observer_pool.fetchval(
            """
            SELECT triggering_attempt_id
            FROM public.runtime_attention_outbox
            WHERE source='model_breaker'
            """
        )
        assert trigger_id == min(attempt_ids)
        assert (
            await observer_pool.fetchval(
                "SELECT count(*) FROM public.runtime_attention_outbox WHERE source='model_breaker'"
            )
            == 1
        )


async def test_outcome_order_is_assigned_after_lock_not_transaction_start(
    migrated_core_postgres_pool,
) -> None:
    """REQ-model-catalog-001: lock order, not BEGIN time, orders outcomes."""
    async with migrated_core_postgres_pool(min_pool_size=3, max_pool_size=6) as admin_pool:
        entry_id = await _seed_catalog(admin_pool, "atomic-post-lock-order")
        transaction_started = asyncio.Event()
        release_lock_attempt = asyncio.Event()
        delayed_pool = _DelayBeforeBreakerLockPool(
            admin_pool,
            transaction_started,
            release_lock_attempt,
        )
        runtime_pool = _RolePool(admin_pool)

        delayed_success = asyncio.create_task(
            record_dispatch_attempt(
                delayed_pool,  # type: ignore[arg-type]
                catalog_entry_id=entry_id,
                butler="general",
                outcome="success",
                attempt_index=0,
            )
        )
        await asyncio.wait_for(transaction_started.wait(), timeout=5)

        failure_id = await _record_failure(runtime_pool, entry_id, 1)
        release_lock_attempt.set()
        success_id = await delayed_success

        assert isinstance(failure_id, int)
        assert isinstance(success_id, int)
        rows = await admin_pool.fetch(
            """
            SELECT id, outcome, ts
            FROM public.model_dispatch_attempts
            WHERE id = ANY($1::bigint[])
            ORDER BY ts, id
            """,
            [failure_id, success_id],
        )
        assert [(row["id"], row["outcome"]) for row in rows] == [
            (failure_id, "runtime_failure"),
            (success_id, "success"),
        ]


async def test_concurrent_failed_half_open_probes_create_one_reopening_episode(
    migrated_core_postgres_pool,
) -> None:
    async with migrated_core_postgres_pool(min_pool_size=3, max_pool_size=6) as admin_pool:
        runtime_pool = _RolePool(admin_pool)
        observer_pool = _RolePool(admin_pool, "butler_switchboard_rw")
        entry_id = await _seed_catalog(admin_pool, "atomic-half-open")
        await _seed_failures(
            admin_pool,
            entry_id,
            5,
            ts=datetime.now(UTC) - timedelta(minutes=16),
        )
        assert (await get_breaker_state(admin_pool, entry_id)).open is False
        tied_probe_ts = datetime.now(UTC)
        await admin_pool.execute(
            "ALTER TABLE public.model_dispatch_attempts ALTER COLUMN ts "
            f"SET DEFAULT '{tied_probe_ts.isoformat()}'::timestamptz"
        )

        attempt_ids = await asyncio.gather(
            _record_failure(runtime_pool, entry_id, 5),
            _record_failure(runtime_pool, entry_id, 6),
        )

        assert all(isinstance(attempt_id, int) for attempt_id in attempt_ids)
        assert (
            await observer_pool.fetchval(
                "SELECT count(*) FROM public.runtime_attention_outbox WHERE source='model_breaker'"
            )
            == 1
        )
        assert (await get_breaker_state(admin_pool, entry_id)).open is True


async def test_closed_breaker_runtime_failures_append_no_episode(
    migrated_core_postgres_pool,
) -> None:
    """REQ-model-catalog-001: only the closed-to-open EDGE emits an episode.

    Every qualifying ``runtime_failure`` below the threshold is recorded while
    the breaker stays CLOSED, so none of them is a transition. This is the
    negative half of the edge contract: the other tests here assert that an
    edge DOES emit, and none of them would notice the recorder emitting on
    every failure instead of only on the transition.
    """
    async with migrated_core_postgres_pool(min_pool_size=2, max_pool_size=4) as admin_pool:
        runtime_pool = _RolePool(admin_pool)
        observer_pool = _RolePool(admin_pool, "butler_switchboard_rw")
        entry_id = await _seed_catalog(admin_pool, "atomic-closed-no-edge")

        for index in range(4):
            attempt_id = await _record_failure(runtime_pool, entry_id, index)
            assert isinstance(attempt_id, int)
            assert (await get_breaker_state(admin_pool, entry_id)).open is False

        assert (await get_breaker_state(admin_pool, entry_id)).consecutive_failures == 4
        assert (
            await observer_pool.fetchval(
                "SELECT count(*) FROM public.model_dispatch_attempts WHERE catalog_entry_id=$1",
                entry_id,
            )
            == 4
        )
        assert (
            await observer_pool.fetchval(
                "SELECT count(*) FROM public.runtime_attention_outbox WHERE source='model_breaker'"
            )
            == 0
        )


async def test_skipped_and_suppressed_do_not_qualify_and_failed_recorder_rolls_back(
    migrated_core_postgres_pool,
) -> None:
    async with migrated_core_postgres_pool(min_pool_size=2, max_pool_size=4) as admin_pool:
        runtime_pool = _RolePool(admin_pool)
        observer_pool = _RolePool(admin_pool, "butler_switchboard_rw")
        entry_id = await _seed_catalog(admin_pool, "atomic-ignored")
        await _seed_failures(admin_pool, entry_id, 4, ts=datetime.now(UTC))
        for index, outcome in enumerate(("quota_skip", "suppressed"), start=4):
            await record_dispatch_attempt(
                runtime_pool,  # type: ignore[arg-type]
                catalog_entry_id=entry_id,
                butler="general",
                outcome=outcome,
                attempt_index=index,
            )
        assert (await get_breaker_state(admin_pool, entry_id)).consecutive_failures == 4

        failed_id = await _record_failure(_FailAfterAttemptPool(admin_pool), entry_id, 6)
        assert failed_id is None
        assert (
            await observer_pool.fetchval(
                "SELECT count(*) FROM public.model_dispatch_attempts WHERE catalog_entry_id=$1",
                entry_id,
            )
            == 6
        )
        assert (
            await observer_pool.fetchval(
                "SELECT count(*) FROM public.runtime_attention_outbox WHERE source='model_breaker'"
            )
            == 0
        )


async def test_fleet_halt_first_new_denial_creates_one_month_episode_without_backfill(
    migrated_core_postgres_pool,
) -> None:
    async with migrated_core_postgres_pool(min_pool_size=2, max_pool_size=4) as admin_pool:
        runtime_pool = _RolePool(admin_pool)
        observer_pool = _RolePool(admin_pool, "butler_switchboard_rw")
        entry_id = await _seed_catalog(admin_pool, "atomic-fleet-halt")
        await admin_pool.execute(
            """
            INSERT INTO public.model_dispatch_attempts
                (catalog_entry_id, butler, outcome, failure_reason, ts)
            VALUES ($1, 'general', 'quota_skip', $2, now() - interval '1 month')
            """,
            entry_id,
            f"{CEILING_DENIAL_REASON_PREFIX}: historical",
        )
        assert (
            await observer_pool.fetchval(
                "SELECT count(*) FROM public.runtime_attention_outbox WHERE source='fleet_halt'"
            )
            == 0
        )

        async def deny(index: int) -> int | None:
            return await record_dispatch_attempt(
                runtime_pool,  # type: ignore[arg-type]
                catalog_entry_id=entry_id,
                butler="general",
                outcome="quota_skip",
                attempt_index=index,
                failure_reason=f"{CEILING_DENIAL_REASON_PREFIX}: current",
                produce_fleet_halt=True,
            )

        attempt_ids = await asyncio.gather(deny(0), deny(1))
        assert all(isinstance(attempt_id, int) for attempt_id in attempt_ids)
        row = await observer_pool.fetchrow(
            """
            SELECT source_snapshot, payload
            FROM public.runtime_attention_outbox
            WHERE source='fleet_halt'
            """
        )
        assert row is not None
        assert row["source_snapshot"]["denied_count"] >= 1
        assert row["payload"] == {
            "classification": "monthly_spend_ceiling",
            "door": "/spend?openDrawer=fleet-halt",
        }
        assert (
            await observer_pool.fetchval(
                "SELECT count(*) FROM public.runtime_attention_outbox WHERE source='fleet_halt'"
            )
            == 1
        )


async def test_current_month_fleet_halt_before_activation_is_not_repaged(
    migrated_core_postgres_pool,
) -> None:
    """REQ-runtime-attention-outbox-001: current-month rollout is not history."""
    async with migrated_core_postgres_pool(min_pool_size=2, max_pool_size=4) as admin_pool:
        runtime_pool = _RolePool(admin_pool)
        observer_pool = _RolePool(admin_pool, "butler_switchboard_rw")
        entry_id = await _seed_catalog(admin_pool, "atomic-fleet-halt-pre-activation")
        await admin_pool.execute(
            """
            INSERT INTO public.model_dispatch_attempts (
                catalog_entry_id, butler, outcome, failure_reason, ts
            ) VALUES ($1, 'general', 'quota_skip', $2, $3)
            """,
            entry_id,
            f"{CEILING_DENIAL_REASON_PREFIX}: before activation",
            await admin_pool.fetchval(
                "SELECT date_trunc('month', clock_timestamp()) + interval '1 microsecond'"
            ),
        )

        attempt_id = await record_dispatch_attempt(
            runtime_pool,  # type: ignore[arg-type]
            catalog_entry_id=entry_id,
            butler="general",
            outcome="quota_skip",
            attempt_index=1,
            failure_reason=f"{CEILING_DENIAL_REASON_PREFIX}: after activation",
            produce_fleet_halt=True,
        )

        assert isinstance(attempt_id, int)
        assert (
            await observer_pool.fetchval(
                "SELECT count(*) FROM public.runtime_attention_outbox WHERE source='fleet_halt'"
            )
            == 0
        )
