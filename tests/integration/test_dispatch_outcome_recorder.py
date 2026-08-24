"""Real-Postgres coverage for the atomic runtime-attention producers.

REQ-model-catalog-001; REQ-runtime-attention-outbox-001;
REQ-dashboard-spend-dashboard-001.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest

from butlers.core.dispatch_outcomes import (
    _FLEET_HALT_LOCK_SQL,
    record_dispatch_attempt,
)
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


async def _record_failure(
    pool: _RolePool | asyncpg.Pool, catalog_entry_id: uuid.UUID, index: int
) -> int | None:
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

    Two layers enforce this and the assertions below are deliberately
    end-to-end across both, so neither is credited with the other's work:
    the recorder's ``if breaker_is_open:`` guard decides not to call the
    producer, and the model-breaker trigger's edge CHECK rejects the call
    outright if it ever does. Dropping the Python guard alone fails this
    test at the database, not at the outbox count.
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


# Two participants in the fleet-halt transaction must name the same UTC month:
# the advisory lock the recorder takes at the top, and the ``v_month`` the
# producer locks on and writes.  They are evaluated at different instants of one
# transaction, so the only way they can be provably equal is for both to read
# transaction_timestamp, which does not move while the transaction runs.
#
# No test can push a real server clock past a real month rollover, so the
# boundary is simulated the one way that keeps the production SQL itself under
# test: each expression is evaluated with its timestamp function replaced by the
# instant that function would return on this timeline.  ``now()`` and
# ``transaction_timestamp()`` return the BEGIN instant wherever they appear;
# ``clock_timestamp()`` returns the instant of the statement that reads it.  The
# substitution is the whole of the simulation -- the expressions, the truncation,
# and the hashing are real Postgres running the real strings.
_SIMULATED_BEGIN = "2026-01-31 23:59:59.999999+00"
_SIMULATED_PRODUCER_CALL = "2026-02-01 00:00:00.000001+00"

_TRANSACTION_CLOCKS = ("now()", "transaction_timestamp()")
_STATEMENT_CLOCK = "clock_timestamp()"

_V_MONTH_DECLARATION = re.compile(r"v_month\s+DATE\s*:=\s*(.+?);", re.IGNORECASE)


def _at_simulated_boundary(expression: str, *, read_at: str) -> str:
    """Rewrite one production expression onto the simulated boundary timeline.

    ``read_at`` is when this expression is evaluated inside the transaction; it
    is what ``clock_timestamp()`` would return there.  A transaction clock reads
    the BEGIN instant no matter when it is evaluated, which is the property under
    test, so it is substituted with ``_SIMULATED_BEGIN`` regardless of ``read_at``.
    """
    found = [clock for clock in (*_TRANSACTION_CLOCKS, _STATEMENT_CLOCK) if clock in expression]
    assert len(found) == 1, (
        f"expected exactly one timestamp function in {expression!r}, found {found}; "
        "this simulation cannot say which instant a mixed expression observes"
    )
    clock = found[0]
    instant = read_at if clock == _STATEMENT_CLOCK else _SIMULATED_BEGIN
    return expression.replace(clock, f"TIMESTAMPTZ '{instant}'")


async def test_fleet_halt_month_agrees_across_a_simulated_utc_month_boundary(
    migrated_core_postgres_pool,
) -> None:
    """REQ-runtime-attention-outbox-001: one transaction names one month.

    bu-jxelx: the recorder's advisory lock read ``now()`` while the producer read
    ``clock_timestamp()``, so a transaction that began before a UTC month
    rollover locked one month and wrote another.  The partial unique index on
    ``fleet_halt_month`` still prevented a duplicate episode, so the lock did not
    fail loudly -- it just stopped serializing the key being written, at exactly
    the rollover when the fleet-halt path is most likely to fire.

    Scoped to the lock key and the producer's month on purpose.  The denial
    row's own ``ts`` stays on the statement clock -- REQ-model-catalog-001 orders
    outcomes by serialization instant, which ``now()`` cannot express -- so it is
    not part of the agreement this test proves.
    """
    async with migrated_core_postgres_pool() as admin_pool:
        definition = await admin_pool.fetchval(
            "SELECT pg_get_functiondef("
            "'public.append_runtime_attention_fleet_halt()'::regprocedure)"
        )
        declaration = _V_MONTH_DECLARATION.search(definition)
        assert declaration, "the installed fleet-halt producer no longer declares v_month"

        # v_month is a faithful proxy for the producer only while it is both what
        # the producer locks on and what it writes.  Prove that from the installed
        # body rather than assuming it.
        assert (
            "hashtextextended('runtime_attention_fleet_halt:' || v_month::text, 0)" in definition
        ), "the producer no longer keys its own advisory lock on v_month"
        assert re.search(r"'fleet_halt',\s*v_month\s*,", definition), (
            "the producer no longer writes v_month into fleet_halt_month"
        )

        lock_key = await admin_pool.fetchval(
            _at_simulated_boundary(
                # The lock statement without its outer call is the key expression.
                _FLEET_HALT_LOCK_SQL.replace("pg_advisory_xact_lock", ""),
                read_at=_SIMULATED_BEGIN,
            )
        )
        producer_month, producer_key = await admin_pool.fetchrow(
            "SELECT month, hashtextextended('runtime_attention_fleet_halt:' || month::text, 0)"
            " FROM (SELECT "
            + _at_simulated_boundary(declaration.group(1), read_at=_SIMULATED_PRODUCER_CALL)
            + " AS month) AS producer"
        )
        assert producer_key == lock_key, (
            f"the recorder serialized advisory-lock key {lock_key} while the producer "
            f"wrote month {producer_month} under key {producer_key}: across a UTC month "
            "boundary the lock guards a key nobody is writing"
        )


async def test_unauthorized_breaker_producer_still_commits_the_attempt_row(
    migrated_core_postgres_pool,
) -> None:
    """REQ-model-catalog-001: a producer we may not call cannot erase the failure.

    Every other test here drives the recorder through ``_RolePool``, which
    forces a canonical ``SET ROLE`` on each acquisition.  This one passes the
    raw pool instead, reproducing ``db.py``'s non-hardened fail-open path where
    role enforcement is disabled and ``current_setting('role')`` is ``none``.
    Both v2 producers raise ``42501`` there.

    Without a savepoint around the producer call that raise poisons the whole
    transaction, so the fifth consecutive ``runtime_failure`` — the breaker
    edge itself — is rolled back and lost, and the breaker can never trip for
    that entry.  The row must survive; only the edge may be skipped.
    """
    async with migrated_core_postgres_pool(min_pool_size=2, max_pool_size=4) as admin_pool:
        observer_pool = _RolePool(admin_pool, "butler_switchboard_rw")
        entry_id = await _seed_catalog(admin_pool, "atomic-unauthorized-breaker")
        await _seed_failures(admin_pool, entry_id, 4, ts=datetime.now(UTC))
        assert await admin_pool.fetchval("SELECT current_setting('role', true)") == "none"

        fifth_id = await _record_failure(admin_pool, entry_id, 4)

        assert isinstance(fifth_id, int)
        assert (
            await observer_pool.fetchval(
                "SELECT count(*) FROM public.model_dispatch_attempts WHERE catalog_entry_id=$1",
                entry_id,
            )
            == 5
        )
        assert (await get_breaker_state(admin_pool, entry_id)).open is True
        assert (
            await observer_pool.fetchval(
                "SELECT count(*) FROM public.runtime_attention_outbox WHERE source='model_breaker'"
            )
            == 0
        )
        # The counts above are read on a separate acquisition, so they prove the
        # outer transaction actually committed rather than merely surviving to
        # the end of its own statement stream.
        assert (
            await observer_pool.fetchval(
                "SELECT id FROM public.model_dispatch_attempts WHERE catalog_entry_id=$1"
                " ORDER BY id DESC LIMIT 1",
                entry_id,
            )
            == fifth_id
        )


async def test_unauthorized_fleet_halt_producer_still_commits_the_denial_row(
    migrated_core_postgres_pool,
) -> None:
    """REQ-runtime-attention-outbox-001: ceiling denials keep their provenance.

    ``produce_fleet_halt=True`` calls the producer on *every* monthly-ceiling
    denial, so with role enforcement off an unbounded producer failure loses
    every ``quota_skip`` provenance row — the dispatch-attempts API and
    evidence-based routing would see nothing at all.
    """
    async with migrated_core_postgres_pool(min_pool_size=2, max_pool_size=4) as admin_pool:
        observer_pool = _RolePool(admin_pool, "butler_switchboard_rw")
        entry_id = await _seed_catalog(admin_pool, "atomic-unauthorized-fleet-halt")
        assert await admin_pool.fetchval("SELECT current_setting('role', true)") == "none"

        attempt_id = await record_dispatch_attempt(
            admin_pool,
            catalog_entry_id=entry_id,
            butler="general",
            outcome="quota_skip",
            attempt_index=0,
            failure_reason=f"{CEILING_DENIAL_REASON_PREFIX}: current",
            produce_fleet_halt=True,
        )

        assert isinstance(attempt_id, int)
        assert (
            await observer_pool.fetchval(
                "SELECT count(*) FROM public.model_dispatch_attempts WHERE catalog_entry_id=$1",
                entry_id,
            )
            == 1
        )
        assert (
            await observer_pool.fetchval(
                "SELECT count(*) FROM public.runtime_attention_outbox WHERE source='fleet_halt'"
            )
            == 0
        )
