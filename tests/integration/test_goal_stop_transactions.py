"""Actual sw047->sw048/roles/races contracts: authored, NOT RUN locally.

Owning cancellation/authenticated transport/approved replay are not implemented
by this unit. Explicit domain controls below prove central SQL only.
"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from urllib.parse import unquote, urlparse
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy.exc import DBAPIError

from alembic import command
from butlers.capability_broker.contracts import BrokerRefused, Capability, Reason, Reservation
from butlers.capability_broker.domains import ParkReceipt
from butlers.capability_broker.ledger import BrokerLedger
from butlers.core.capability_json import arguments_digest
from butlers.core.goal_admission import AdmissionUnavailable
from butlers.core.goal_stop import CancelIdentity, GoalStop, StopUnavailable
from butlers.core.tool_catalog import RecipientParameters
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import (
    create_migrated_test_db,
    init_db_sql_for_dbapi,
    migration_bootstrap_db_url,
    migration_db_name,
)
from tests.integration.test_capability_broker_transactions import running
from tests.switchboard.goal_ledger_fixtures import admit, codec, pools

pytest_plugins = ("tests.switchboard.goal_ledger_fixtures",)
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


def capability():
    return Capability(
        "health",
        "write",
        "record",
        "trusted",
        (),
        always_per_event=True,
        approvals_available=True,
        recipient_params=RecipientParameters(),
    )


async def parked(ledger, *, lost=False, domain_call=None):
    owner, runtime = ledger
    cap = capability()
    broker, domain, goal, invocation, token = await running(owner, runtime, cap)

    async def park(reservation, arguments, assertion):
        domain.calls.append(reservation)
        if domain_call:
            await domain_call(reservation)
        if lost:
            raise TimeoutError("controlled_lost_PARK")
        return ParkReceipt("controlled-row")

    domain.park = park
    result = await broker.call(goal, token, cap.identity, {})
    return broker, goal, invocation, result, domain


def identity(row):
    return CancelIdentity(
        row["goal_id"], row["id"], "health", row["id"], row["capability"], row["args_digest"]
    )


async def test_real_states_tokens_RLS_bootstrap_and_monotonicity(
    ledger, ledger_url, postgres_container
):
    owner, runtime = ledger
    # Replay the real privileged bootstrap against the test database. The
    # fixture owns this database; no runtime role gains owner/super privileges.
    parsed = urlparse(ledger_url)
    url = urlparse(migration_bootstrap_db_url(postgres_container, parsed.path.lstrip("/")))
    admin = await asyncpg.connect(url._replace(scheme="postgresql").geturl(), timeout=10)
    try:
        await admin.execute(
            "SELECT set_config('butlers.connecting_user',$1,false)", unquote(parsed.username)
        )
        await admin.execute(init_db_sql_for_dbapi())
        # Even deliberately wider test grants must not bypass runtime RLS.
        await admin.execute(
            "GRANT USAGE ON SCHEMA switchboard TO butler_health_rw,connector_writer; "
            "GRANT SELECT,INSERT,UPDATE,DELETE ON switchboard.goal_park_associations,"
            "switchboard.goal_stop_requests,switchboard.goal_approval_cancel_work "
            "TO butler_health_rw,connector_writer"
        )
    finally:
        await admin.close()
    for state in ("admitted", "running", "awaiting_approval", "recovering", "settling", "stopped"):
        broker, goal, invocation, result, _ = await parked(ledger, lost=True)
        await owner.execute("UPDATE switchboard.goals SET status=$2 WHERE id=$1", goal, state)
        request = uuid4()
        receipt = await GoalStop(runtime).stop_goal(goal, request, "a" * 64)
        assert receipt.revoked_tokens == 1 and receipt.cancellation_work == 1
        async with runtime.acquire() as readback:
            assert (
                await readback.fetchval("SELECT status FROM switchboard.goals WHERE id=$1", goal)
                == "stopped"
            )
            assert (
                await readback.fetchval(
                    "SELECT token_hash FROM switchboard.invocations WHERE id=$1", invocation
                )
                is None
            )
            assert (
                await readback.fetchval(
                    "SELECT status FROM switchboard.effects WHERE id=$1", result.effect_id
                )
                == "unknown"
            )
        assert await GoalStop(runtime).stop_goal(goal, request, "a" * 64) == receipt
        async with runtime.acquire() as connection:
            for sql, value in (
                ("UPDATE switchboard.goals SET status=$2 WHERE id=$1", "running"),
                ("UPDATE switchboard.invocations SET token_hash=$2 WHERE goal_id=$1", "b" * 64),
            ):
                with pytest.raises(asyncpg.PostgresError):
                    async with connection.transaction():
                        await connection.execute(sql, goal, value)
    for terminal in ("completed", "partial", "failed", "budget_exhausted"):
        goal = (await admit(runtime)).goal_id
        await owner.execute("UPDATE switchboard.goals SET status=$2 WHERE id=$1", goal, terminal)
        with pytest.raises(StopUnavailable, match="goal_not_stoppable"):
            await GoalStop(runtime).stop_goal(goal, uuid4(), "a" * 64)
        assert (
            await owner.fetchval("SELECT status FROM switchboard.goals WHERE id=$1", goal)
            == terminal
        )
    # Actual runtime identities, after broad bootstrap DML grants.
    async with owner.acquire() as foreign:
        for role in ("butler_health_rw", "connector_writer"):
            exists = await foreign.fetchval(
                "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1)", role
            )
            assert exists, "declared_runtime_role_must_exist"
            async with foreign.transaction():
                await foreign.execute("SET LOCAL ROLE " + role)
                for table in (
                    "goal_park_associations",
                    "goal_stop_requests",
                    "goal_approval_cancel_work",
                ):
                    assert await foreign.fetchval("SELECT count(*) FROM switchboard." + table) == 0
                with pytest.raises((AdmissionUnavailable, asyncpg.InsufficientPrivilegeError)):
                    await GoalStop(BoundPool(foreign)).stop_goal(goal, uuid4(), "a" * 64)
    with pytest.raises(AdmissionUnavailable):
        await GoalStop(owner).stop_goal(goal, uuid4(), "a" * 64)


# A bound connection is only used to exercise a real SET ROLE identity above.
class BoundPool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return _bound(self.connection)


@asynccontextmanager
async def _bound(connection):
    yield connection


async def test_real_lost_PARK_and_late_reply_never_revive_or_recharge(ledger):
    owner, runtime = ledger
    _, goal, _, result, domain = await parked(ledger, lost=True)
    receipt = await GoalStop(runtime).stop_goal(goal, uuid4(), "a" * 64)
    assert result.status == "unknown" and receipt.cancellation_work == 1 and len(domain.calls) == 1
    async with runtime.acquire() as readback:
        assert (
            await readback.fetchval(
                "SELECT status FROM switchboard.effects WHERE id=$1", result.effect_id
            )
            == "unknown"
        )
        assert (
            await readback.fetchval(
                "SELECT used_calls FROM switchboard.budgets WHERE goal_id=$1", goal
            )
            == 1
        )
        assert (
            await readback.fetchval(
                "SELECT state FROM switchboard.goal_approval_cancel_work WHERE effect_id=$1",
                result.effect_id,
            )
            == "pending"
        )

    async def stop_before_reply(reservation):
        assert (
            await GoalStop(runtime).stop_goal(reservation.principal.goal_id, uuid4(), "a" * 64)
        ).cancellation_work == 1

    _, late_goal, _, late, domain = await parked(ledger, domain_call=stop_before_reply)
    assert late.status == "unknown" and late.approval_ref is None and len(domain.calls) == 1
    async with runtime.acquire() as readback:
        assert (
            await readback.fetchval(
                "SELECT approval_ref FROM switchboard.goal_park_associations WHERE effect_id=$1",
                late.effect_id,
            )
            == "controlled-row"
        )
        assert (
            await readback.fetchval("SELECT status FROM switchboard.goals WHERE id=$1", late_goal)
            == "stopped"
        )
        with pytest.raises(asyncpg.PostgresError):
            async with readback.transaction():
                await readback.execute(
                    "UPDATE switchboard.goal_park_associations SET approval_ref='conflict' WHERE effect_id=$1",
                    late.effect_id,
                )
        with pytest.raises(asyncpg.PostgresError):
            async with readback.transaction():
                await readback.execute(
                    "UPDATE switchboard.goal_park_associations SET dispatch_intent_id=$2,dispatch_committed_at=now() WHERE effect_id=$1",
                    late.effect_id,
                    uuid4(),
                )


async def test_real_claim_reclaim_ACK_and_dispatch_witness(ledger):
    owner, runtime = ledger
    _, goal, _, result, _ = await parked(ledger, lost=True)
    stop = GoalStop(runtime)
    await stop.stop_goal(goal, uuid4(), "a" * 64)
    row = await owner.fetchrow("SELECT * FROM switchboard.effects WHERE id=$1", result.effect_id)
    binding = identity(row)
    first = await stop.claim_cancel_work(binding, lease_seconds=1)
    assert await stop.claim_cancel_work(binding) is None
    await asyncio.sleep(1.05)  # actual DB-time lease expiry, not edited stored deadlines
    second = await stop.claim_cancel_work(binding)
    assert second.generation == first.generation + 1 and second.claim_id != first.claim_id
    for invalid in (first, replace(second, identity=replace(binding, args_digest="b" * 64))):
        with pytest.raises(StopUnavailable):
            await stop.ack_cancel_work(invalid, "c" * 64, "cancelled")
    await stop.ack_cancel_work(second, "c" * 64, "fenced_absent")
    await stop.ack_cancel_work(second, "c" * 64, "fenced_absent")
    async with runtime.acquire() as readback:
        assert (
            await readback.fetchval(
                "SELECT status FROM switchboard.effects WHERE id=$1", result.effect_id
            )
            == "cancelled"
        )
        assert (
            await readback.fetchval(
                "SELECT state FROM switchboard.goal_approval_cancel_work WHERE effect_id=$1",
                result.effect_id,
            )
            == "acknowledged"
        )
    # An associated effect cannot enter dispatch without a durable witness.
    _, other_goal, _, other, _ = await parked(ledger)
    async with runtime.acquire() as connection, connection.transaction():
        await connection.fetchrow(
            "SELECT id FROM switchboard.goals WHERE id=$1 FOR UPDATE", other_goal
        )
        with pytest.raises(asyncpg.PostgresError):
            async with connection.transaction():
                await connection.execute(
                    "UPDATE switchboard.effects SET status='dispatched' WHERE id=$1",
                    other.effect_id,
                )
        await connection.execute(
            "UPDATE switchboard.goal_park_associations SET dispatch_intent_id=$2,dispatch_committed_at=now() WHERE effect_id=$1",
            other.effect_id,
            uuid4(),
        )
        await connection.execute(
            "UPDATE switchboard.effects SET status='dispatched' WHERE id=$1", other.effect_id
        )
        await connection.execute(
            "UPDATE switchboard.effects SET status='unknown' WHERE id=$1", other.effect_id
        )
    local = await stop.stop_goal(other_goal, uuid4(), "a" * 64)
    assert local.cancellation_work == 0
    assert (
        await owner.fetchval("SELECT status FROM switchboard.effects WHERE id=$1", other.effect_id)
        == "unknown"
    )


async def test_real_Stop_rollback_and_goal_lock_wait_bound(ledger):
    owner, runtime = ledger
    _, goal, invocation, result, _ = await parked(ledger, lost=True)
    # A deliberate owning test trigger faults the final receipt INSERT, after
    # actual revocation/work writes. Independent acquisition must see rollback.
    await owner.execute("""
        CREATE FUNCTION switchboard.test_stop_receipt_failure() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'controlled_stop_receipt_failure'; END $$;
        CREATE TRIGGER test_stop_receipt_failure BEFORE INSERT ON switchboard.goal_stop_requests
        FOR EACH ROW EXECUTE FUNCTION switchboard.test_stop_receipt_failure();
    """)
    request = uuid4()
    try:
        with pytest.raises(asyncpg.PostgresError):
            await GoalStop(runtime).stop_goal(goal, request, "a" * 64)
    finally:
        await owner.execute(
            "DROP TRIGGER test_stop_receipt_failure ON switchboard.goal_stop_requests; DROP FUNCTION switchboard.test_stop_receipt_failure()"
        )
    async with runtime.acquire() as readback:
        assert (
            await readback.fetchval("SELECT status FROM switchboard.goals WHERE id=$1", goal)
            == "running"
        )
        assert (
            await readback.fetchval(
                "SELECT token_hash FROM switchboard.invocations WHERE id=$1", invocation
            )
            is not None
        )
        assert not await readback.fetchval(
            "SELECT EXISTS(SELECT 1 FROM switchboard.goal_approval_cancel_work WHERE effect_id=$1)",
            result.effect_id,
        )
        assert not await readback.fetchval(
            "SELECT EXISTS(SELECT 1 FROM switchboard.goal_stop_requests WHERE request_id=$1)",
            request,
        )
    async with runtime.acquire() as blocker, blocker.transaction():
        await blocker.fetchrow("SELECT id FROM switchboard.goals WHERE id=$1 FOR UPDATE", goal)
        with pytest.raises(
            (TimeoutError, asyncpg.LockNotAvailableError, asyncpg.QueryCanceledError)
        ):
            await GoalStop(runtime, timeout=0.1).stop_goal(goal, request, "a" * 64)
    assert (await GoalStop(runtime).stop_goal(goal, request, "a" * 64)).cancellation_work == 1


_STOP_TABLES = (
    "goal_park_associations",
    "goal_stop_requests",
    "goal_approval_cancel_work",
)


@asynccontextmanager
async def actor_pool(url):
    """Each actor has one real connection; a wait cannot hide a second acquire."""
    label = "a0_" + uuid4().hex

    async def setup(connection):
        await connection.execute("SET ROLE butler_switchboard_rw")
        await connection.execute("SELECT set_config('application_name',$1,false)", label)

    pool = await asyncpg.create_pool(url, init=codec, setup=setup, min_size=1, max_size=1)
    try:
        yield pool, label
    finally:
        try:
            await asyncio.wait_for(pool.close(), timeout=5)
        except TimeoutError:
            pool.terminate()
            raise


async def cancel_actors(tasks):
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=3)


async def wait_for_lock(owner, label, blocker_pid):
    # Actual pg_stat_activity/pg_blocking_pids, not a coroutine-start Event.
    async with asyncio.timeout(1.5):
        while True:
            pid = await owner.fetchval(
                "SELECT pid FROM pg_stat_activity WHERE application_name=$1 "
                "AND wait_event_type='Lock' AND $2=ANY(pg_blocking_pids(pid))",
                label,
                blocker_pid,
            )
            if pid is not None:
                return pid
            await asyncio.sleep(0.01)  # noqa: ASYNC110 - bounded real SQL lock observation


async def wait_for_lease_expiry(owner, effect_id):
    async with asyncio.timeout(2):
        while not await owner.fetchval(
            "SELECT lease_expires_at<=clock_timestamp() "
            "FROM switchboard.goal_approval_cancel_work WHERE effect_id=$1",
            effect_id,
        ):
            await asyncio.sleep(0.01)  # noqa: ASYNC110 - bounded database-clock observation


async def gate_ddl(owner, sql):
    async with asyncio.timeout(3):
        async with owner.acquire(timeout=1) as connection, connection.transaction():
            await connection.execute(
                "SET LOCAL lock_timeout='1s'; SET LOCAL statement_timeout='2s'"
            )
            await connection.execute(sql)


@asynccontextmanager
async def sql_gate(owner, *, table, event, goal, state=None):
    """Block a real producer before COMMIT, with finite setup/teardown."""
    assert table in _STOP_TABLES and event in {"INSERT", "UPDATE"}
    assert state in {None, "claimed", "acknowledged"}
    name = "test_a0_" + uuid4().hex
    key = uuid4().int % (2**63)
    condition = f"NEW.goal_id='{goal}'::uuid"
    if state is not None:
        condition += f" AND NEW.state='{state}'"
    await gate_ddl(
        owner,
        f"CREATE FUNCTION switchboard.{name}() RETURNS trigger LANGUAGE plpgsql AS $$ "
        f"BEGIN PERFORM pg_advisory_xact_lock({key}::bigint); RETURN NEW; END $$; "
        f"CREATE TRIGGER {name} BEFORE {event} ON switchboard.{table} "
        f"FOR EACH ROW WHEN ({condition}) EXECUTE FUNCTION switchboard.{name}()",
    )
    try:
        async with owner.acquire(timeout=1) as holder:
            assert await holder.fetchval("SELECT pg_try_advisory_lock($1)", key)
            pid = await holder.fetchval("SELECT pg_backend_pid()")
            released = False

            async def release():
                nonlocal released
                if not released:
                    assert await holder.fetchval("SELECT pg_advisory_unlock($1)", key)
                    released = True

            try:
                yield pid, release
            finally:
                await release()
    finally:
        # Caller cancels/drains its actual producer tasks before leaving the
        # nested context. Never wait indefinitely for a leftover table lock.
        await gate_ddl(
            owner,
            f"DROP TRIGGER {name} ON switchboard.{table}; DROP FUNCTION switchboard.{name}()",
        )


async def evidence_snapshot(connection):
    return {
        table: tuple(
            row["value"]
            for row in await connection.fetch(
                f"SELECT to_jsonb(t)::text AS value FROM switchboard.{table} t "
                "ORDER BY to_jsonb(t)::text"
            )
        )
        for table in _STOP_TABLES
    }


async def test_real_Stop_reserve_both_contended_orders(ledger, ledger_url):
    owner, _ = ledger
    for first in ("reserve", "stop"):
        async with actor_pool(ledger_url) as (calls, call_label):
            async with actor_pool(ledger_url) as (stops, stop_label):
                cap = capability()
                broker, domain, goal, invocation, token = await running(owner, calls, cap)

                async def owning_park(reservation, arguments, assertion):
                    domain.calls.append(reservation)
                    return ParkReceipt("controlled-row")

                domain.park = owning_park
                request = uuid4()
                table = "goal_park_associations" if first == "reserve" else "goal_stop_requests"
                tasks = []
                async with sql_gate(owner, table=table, event="INSERT", goal=goal) as gate:
                    gate_pid, release = gate
                    try:
                        if first == "reserve":
                            call = asyncio.create_task(broker.call(goal, token, cap.identity, {}))
                            tasks.append(call)
                            first_pid = await wait_for_lock(owner, call_label, gate_pid)
                            stop = asyncio.create_task(
                                GoalStop(stops).stop_goal(goal, request, "a" * 64)
                            )
                            tasks.append(stop)
                            await wait_for_lock(owner, stop_label, first_pid)
                        else:
                            stop = asyncio.create_task(
                                GoalStop(stops).stop_goal(goal, request, "a" * 64)
                            )
                            tasks.append(stop)
                            first_pid = await wait_for_lock(owner, stop_label, gate_pid)
                            call = asyncio.create_task(broker.call(goal, token, cap.identity, {}))
                            tasks.append(call)
                            await wait_for_lock(owner, call_label, first_pid)
                        # Both actual transactions are live and contending before release.
                        assert not call.done() and not stop.done()
                        await release()
                        local = await asyncio.wait_for(stop, timeout=4)
                        if first == "reserve":
                            result = await asyncio.wait_for(call, timeout=4)
                            assert result.status in {"parked", "unknown"}
                            assert local.cancellation_work == 1 and len(domain.calls) == 1
                        else:
                            with pytest.raises(BrokerRefused) as refused:
                                await asyncio.wait_for(call, timeout=4)
                            assert refused.value.reason is Reason.GOAL
                            assert local.cancellation_work == 0 and domain.calls == []
                    finally:
                        await release()
                        await cancel_actors(tasks)
                # Independent owner acquisition after both transaction exits.
                async with owner.acquire() as readback:
                    assert (
                        await readback.fetchval(
                            "SELECT status FROM switchboard.goals WHERE id=$1", goal
                        )
                        == "stopped"
                    )
                    assert (
                        await readback.fetchval(
                            "SELECT token_hash FROM switchboard.invocations WHERE id=$1", invocation
                        )
                        is None
                    )
                    expected = int(first == "reserve")
                    for table in ("effects", "goal_park_associations", "goal_approval_cancel_work"):
                        assert (
                            await readback.fetchval(
                                f"SELECT count(*) FROM switchboard.{table} WHERE goal_id=$1", goal
                            )
                            == expected
                        )
                    assert (
                        await readback.fetchval(
                            "SELECT used_calls FROM switchboard.budgets WHERE goal_id=$1", goal
                        )
                        == expected
                    )


async def test_real_simultaneous_claim_and_ACK_reclaim_linearization(ledger, ledger_url):
    owner, runtime = ledger
    _, goal, _, result, _ = await parked(ledger, lost=True)
    await GoalStop(runtime).stop_goal(goal, uuid4(), "a" * 64)
    binding = identity(
        await owner.fetchrow("SELECT * FROM switchboard.effects WHERE id=$1", result.effect_id)
    )
    async with actor_pool(ledger_url) as (first_pool, first_label):
        async with actor_pool(ledger_url) as (second_pool, second_label):
            tasks = []
            async with sql_gate(
                owner, table="goal_approval_cancel_work", event="UPDATE", goal=goal, state="claimed"
            ) as (gate_pid, release):
                try:
                    first = asyncio.create_task(GoalStop(first_pool).claim_cancel_work(binding))
                    tasks.append(first)
                    pid = await wait_for_lock(owner, first_label, gate_pid)
                    second = asyncio.create_task(GoalStop(second_pool).claim_cancel_work(binding))
                    tasks.append(second)
                    await wait_for_lock(owner, second_label, pid)
                    await release()
                    claims = await asyncio.wait_for(asyncio.gather(first, second), timeout=4)
                    assert claims[0] is not None and claims[1] is None
                    assert claims[0].generation == 1
                    async with owner.acquire() as readback:
                        row = await readback.fetchrow(
                            "SELECT state,generation,claim_id FROM switchboard.goal_approval_cancel_work "
                            "WHERE effect_id=$1",
                            result.effect_id,
                        )
                        assert row["state"] == "claimed" and row["generation"] == 1
                        assert row["claim_id"] == claims[0].claim_id
                finally:
                    await release()
                    await cancel_actors(tasks)
    for first_operation in ("ack", "reclaim"):
        _, goal, _, result, _ = await parked(ledger, lost=True)
        await GoalStop(runtime).stop_goal(goal, uuid4(), "a" * 64)
        binding = identity(
            await owner.fetchrow("SELECT * FROM switchboard.effects WHERE id=$1", result.effect_id)
        )
        old = await GoalStop(runtime).claim_cancel_work(binding, lease_seconds=1)
        if first_operation == "reclaim":
            await wait_for_lease_expiry(owner, result.effect_id)
        async with actor_pool(ledger_url) as (first_pool, first_label):
            async with actor_pool(ledger_url) as (second_pool, second_label):
                tasks = []
                state = "acknowledged" if first_operation == "ack" else "claimed"
                async with sql_gate(
                    owner, table="goal_approval_cancel_work", event="UPDATE", goal=goal, state=state
                ) as (gate_pid, release):
                    try:
                        if first_operation == "ack":
                            first = asyncio.create_task(
                                GoalStop(first_pool).ack_cancel_work(old, "c" * 64, "cancelled")
                            )
                        else:
                            first = asyncio.create_task(
                                GoalStop(first_pool).claim_cancel_work(binding)
                            )
                        tasks.append(first)
                        pid = await wait_for_lock(owner, first_label, gate_pid)
                        if first_operation == "ack":
                            # ACK validated while lease was live. Hold its goal lock
                            # across actual DB-time expiry so reclaim would be eligible.
                            await wait_for_lease_expiry(owner, result.effect_id)
                            second = asyncio.create_task(
                                GoalStop(second_pool).claim_cancel_work(binding)
                            )
                        else:
                            second = asyncio.create_task(
                                GoalStop(second_pool).ack_cancel_work(old, "c" * 64, "cancelled")
                            )
                        tasks.append(second)
                        await wait_for_lock(owner, second_label, pid)
                        await release()
                        outcome = await asyncio.wait_for(first, timeout=4)
                        if first_operation == "ack":
                            with pytest.raises(StopUnavailable, match="cancel_binding_unavailable"):
                                await asyncio.wait_for(second, timeout=4)
                            current = old
                        else:
                            assert outcome.generation == old.generation + 1
                            assert outcome.claim_id != old.claim_id
                            with pytest.raises(StopUnavailable, match="cancel_ack_conflict"):
                                await asyncio.wait_for(second, timeout=4)
                            current = outcome
                    finally:
                        await release()
                        await cancel_actors(tasks)
        async with owner.acquire() as readback:
            row = await readback.fetchrow(
                "SELECT * FROM switchboard.goal_approval_cancel_work WHERE effect_id=$1",
                result.effect_id,
            )
            assert row["generation"] == current.generation and row["claim_id"] == current.claim_id
            assert row["state"] == ("acknowledged" if first_operation == "ack" else "claimed")
            assert await readback.fetchval(
                "SELECT status FROM switchboard.effects WHERE id=$1", result.effect_id
            ) == ("cancelled" if first_operation == "ack" else "unknown")
        await GoalStop(runtime).ack_cancel_work(current, "c" * 64, "cancelled")
        await GoalStop(runtime).ack_cancel_work(current, "c" * 64, "cancelled")
        with pytest.raises(StopUnavailable):
            await GoalStop(runtime).ack_cancel_work(current, "d" * 64, "cancelled")


@asynccontextmanager
async def rollback_only(connection):
    transaction = connection.transaction()
    await transaction.start()
    try:
        yield
    finally:
        await transaction.rollback()


async def test_real_bootstrap_roles_policy_and_immutable_attack_matrix(
    ledger, ledger_url, postgres_container
):
    owner, runtime = ledger
    _, goal, _, result, _ = await parked(ledger, lost=True)
    request = uuid4()
    stop = GoalStop(runtime)
    await stop.stop_goal(goal, request, "a" * 64)
    binding = identity(
        await owner.fetchrow("SELECT * FROM switchboard.effects WHERE id=$1", result.effect_id)
    )
    claim = await stop.claim_cancel_work(binding)
    await stop.ack_cancel_work(claim, "c" * 64, "cancelled")
    _, other_goal, _, other, _ = await parked(ledger)
    attacks = (
        (
            "UPDATE switchboard.goal_park_associations SET goal_id=$2 WHERE effect_id=$1",
            (result.effect_id, other_goal),
        ),
        (
            "UPDATE switchboard.goal_park_associations SET effect_id=$2,action_id=$2 WHERE effect_id=$1",
            (result.effect_id, other.effect_id),
        ),
        (
            "UPDATE switchboard.goal_park_associations SET owning_domain='relationship' WHERE effect_id=$1",
            (result.effect_id,),
        ),
        (
            "UPDATE switchboard.goal_park_associations SET action_id=$2 WHERE effect_id=$1",
            (result.effect_id, uuid4()),
        ),
        (
            "UPDATE switchboard.goal_park_associations SET approval_ref='first' WHERE effect_id=$1",
            (result.effect_id,),
        ),
        ("DELETE FROM switchboard.goal_park_associations WHERE effect_id=$1", (result.effect_id,)),
        (
            "UPDATE switchboard.goal_park_associations SET dispatch_intent_id=$2,"
            "dispatch_committed_at=clock_timestamp() WHERE effect_id=$1",
            (result.effect_id, uuid4()),
        ),
        (
            "UPDATE switchboard.goal_stop_requests SET provenance_digest=$2 WHERE request_id=$1",
            (request, "b" * 64),
        ),
        ("DELETE FROM switchboard.goal_stop_requests WHERE request_id=$1", (request,)),
        (
            "UPDATE switchboard.goal_approval_cancel_work SET goal_id=$2 WHERE effect_id=$1",
            (result.effect_id, other_goal),
        ),
        (
            "UPDATE switchboard.goal_approval_cancel_work SET request_id=$2 WHERE effect_id=$1",
            (result.effect_id, uuid4()),
        ),
        (
            "UPDATE switchboard.goal_approval_cancel_work SET receipt_digest=$2 WHERE effect_id=$1",
            (result.effect_id, "d" * 64),
        ),
        (
            "UPDATE switchboard.goal_approval_cancel_work SET state='pending',generation=0,"
            "claim_id=NULL,lease_expires_at=NULL,receipt_digest=NULL,outcome=NULL WHERE effect_id=$1",
            (result.effect_id,),
        ),
        (
            "DELETE FROM switchboard.goal_approval_cancel_work WHERE effect_id=$1",
            (result.effect_id,),
        ),
    )
    # Use a row whose first reference is already attached to prove replacement,
    # rather than forbidding the legitimate attach-once late PARK path.
    await owner.execute(
        "UPDATE switchboard.goal_park_associations SET approval_ref='late-row' WHERE effect_id=$1",
        result.effect_id,
    )
    direct_cap = replace(capability(), always_per_event=False, approvals_available=False)
    direct_broker, _, direct_goal, _, direct_token = await running(owner, runtime, direct_cap)
    async with runtime.acquire() as connection, connection.transaction():
        ledger = BrokerLedger(connection)
        principal = await ledger.authenticate(direct_goal, direct_token)
        reserved = await ledger.reserve(
            principal, direct_cap.identity, {}, arguments_digest({}), direct_broker.authorities
        )
        assert isinstance(reserved, Reservation)
    insert = (
        "INSERT INTO switchboard.goal_park_associations "
        "(effect_id,goal_id,owning_domain,action_id,allow_standing_rule) "
        "VALUES($1,$2,'health',$1,false)"
    )
    insert_args = (reserved.effect_id, direct_goal)
    stop_insert = (
        "INSERT INTO switchboard.goal_stop_requests "
        "(request_id,goal_id,provenance_digest,revoked_tokens,cancellation_work) "
        "VALUES($1,$2,$3,0,0)"
    )
    stop_insert_args = (uuid4(), goal, "a" * 64)
    # This exact otherwise-valid requested-effect row is permitted for the
    # canonical runtime, then rolled back before testing foreign INSERT RLS.
    async with runtime.acquire() as connection, rollback_only(connection):
        assert await connection.fetchval("SELECT current_user") == "butler_switchboard_rw"
        effect = await connection.fetchrow(
            "SELECT id,goal_id,status FROM switchboard.effects WHERE id=$1", reserved.effect_id
        )
        assert effect is not None
        assert tuple(effect) == (reserved.effect_id, direct_goal, "requested")
        assert await connection.execute(insert, *insert_args) == "INSERT 0 1"
        assert await connection.execute(stop_insert, *stop_insert_args) == "INSERT 0 1"
    baseline = await evidence_snapshot(owner)
    for sql, args in attacks:
        async with runtime.acquire() as connection:
            try:
                async with connection.transaction():
                    command_status = await connection.execute(sql, *args)
                    # RLS denial may be zero affected rows rather than an exception.
                    assert command_status.rsplit(" ", 1)[-1] == "0"
            except asyncpg.PostgresError:
                pass
        assert await evidence_snapshot(owner) == baseline

    parsed = urlparse(ledger_url)
    admin_url = urlparse(migration_bootstrap_db_url(postgres_container, parsed.path.lstrip("/")))
    admin = await asyncpg.connect(admin_url._replace(scheme="postgresql").geturl(), timeout=10)
    try:
        await admin.execute(
            "SELECT set_config('butlers.connecting_user',$1,false)", unquote(parsed.username)
        )
        await admin.execute(init_db_sql_for_dbapi())
        await admin.execute(
            "GRANT USAGE ON SCHEMA switchboard TO butler_health_rw,connector_writer; "
            "GRANT SELECT,INSERT,UPDATE,DELETE ON switchboard.goal_park_associations,"
            "switchboard.goal_stop_requests,switchboard.goal_approval_cancel_work "
            "TO butler_health_rw,connector_writer"
        )
        for role in ("butler_health_rw", "connector_writer"):
            async with rollback_only(admin):
                await admin.execute("SET LOCAL ROLE " + role)
                for table in _STOP_TABLES:
                    assert await admin.fetchval("SELECT count(*) FROM switchboard." + table) == 0
                with pytest.raises(AdmissionUnavailable):
                    await GoalStop(BoundPool(admin)).stop_goal(goal, uuid4(), "a" * 64)
                assert await admin.fetchval("SELECT current_user") == role
                assert (
                    await admin.fetchval(
                        "SELECT count(*) FROM switchboard.effects WHERE id=$1", reserved.effect_id
                    )
                    == 0
                )
                # The BEFORE INSERT trigger reads the protected effect first.
                # Its invoker cannot see that row, so INTO STRICT raises P0002
                # before this row reaches the INSERT WITH CHECK policy.
                with pytest.raises(asyncpg.NoDataFoundError):
                    async with admin.transaction():
                        await admin.execute(insert, *insert_args)
                # This valid local Stop receipt has no hidden-effect read in
                # its BEFORE trigger, so it reaches the independent RLS check.
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    async with admin.transaction():
                        await admin.execute(stop_insert, *stop_insert_args)
                for sql, args in attacks:
                    try:
                        async with admin.transaction():
                            status = await admin.execute(sql, *args)
                            assert status.rsplit(" ", 1)[-1] == "0"
                    except asyncpg.PostgresError:
                        pass
            assert await evidence_snapshot(owner) == baseline
        # Uncommitted role catalog changes are visible only to this disposable
        # administrator transaction, then rolled back. Other tests never see a
        # committed alteration of the shared canonical runtime role.
        temporary_owner = "a0_owner_" + uuid4().hex
        drift = (
            "ALTER ROLE butler_switchboard_rw SUPERUSER",
            "ALTER ROLE butler_switchboard_rw BYPASSRLS",
            "CREATE ROLE " + temporary_owner + "; ALTER TABLE switchboard.goal_stop_requests "
            "OWNER TO "
            + temporary_owner
            + "; GRANT "
            + temporary_owner
            + " TO butler_switchboard_rw",
            "ALTER TABLE switchboard.goal_stop_requests OWNER TO butler_switchboard_rw",
            "ALTER TABLE switchboard.goal_stop_requests DISABLE ROW LEVEL SECURITY",
            "ALTER TABLE switchboard.goal_stop_requests FORCE ROW LEVEL SECURITY",
            "DROP POLICY goal_stop_select ON switchboard.goal_stop_requests",
            "CREATE POLICY unexpected_public_access ON switchboard.goal_stop_requests "
            "FOR SELECT TO PUBLIC USING (true)",
        )
        for mutation in drift:
            async with rollback_only(admin):
                await admin.execute(mutation)
                await admin.execute("SET LOCAL ROLE butler_switchboard_rw")
                with pytest.raises((AdmissionUnavailable, StopUnavailable)):
                    await GoalStop(BoundPool(admin)).stop_goal(goal, uuid4(), "a" * 64)
            assert await evidence_snapshot(owner) == baseline
        assert (await stop.stop_goal(goal, request, "a" * 64)).request_id == request
        assert await evidence_snapshot(owner) == baseline
        # The real ordinary migration-owner role can still read the data. It
        # does not gain permission to masquerade as the runtime repository.
        async with owner.acquire() as connection:
            flags = await connection.fetchrow(
                "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
            )
            assert not flags["rolsuper"] and not flags["rolbypassrls"]
            assert await evidence_snapshot(connection) == baseline
    finally:
        await admin.close()


async def stop_schema_snapshot(connection):
    return {
        "rows": await evidence_snapshot(connection),
        "triggers": tuple(
            tuple(r)
            for r in await connection.fetch(
                "SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid) "
                "FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
                "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='switchboard' "
                "AND t.tgname='goal_stop_guard' ORDER BY c.relname"
            )
        ),
        "policies": tuple(
            tuple(r)
            for r in await connection.fetch(
                "SELECT tablename,policyname,roles,cmd,qual,with_check FROM pg_policies "
                "WHERE schemaname='switchboard' AND tablename=ANY($1::text[]) "
                "ORDER BY tablename,policyname",
                list(_STOP_TABLES),
            )
        ),
    }


async def test_real_owner_dump_and_actual_downgrade_preservation(postgres_container):
    # A dedicated ordinary-owner database prevents a successful downgrade from
    # deleting the module fixture shared by the original four business cases.
    db_name = migration_db_name()
    url = await asyncio.to_thread(
        create_migrated_test_db,
        postgres_container,
        db_name,
        ["core", "switchboard"],
        schemas={"core": "switchboard", "switchboard": "switchboard"},
        revisions={"switchboard": "sw_048"},
    )
    config = _build_alembic_config(url, ["switchboard"], target_schema="switchboard")
    async with pools(url) as (owner, runtime):
        parsed = urlparse(url)
        migration_role = unquote(parsed.username)
        assert migration_role.startswith("migration_test_")
        await owner.execute(
            f'ALTER ROLE "{migration_role}" IN DATABASE "{db_name}" SET statement_timeout=\'5s\''
        )
        await owner.execute(
            f'ALTER ROLE "{migration_role}" IN DATABASE "{db_name}" SET lock_timeout=\'1s\''
        )
        # Actual Alembic downgrade on an empty database, not captured SQL only.
        await asyncio.wait_for(
            asyncio.to_thread(command.downgrade, config, "switchboard@sw_047"), timeout=30
        )
        for table in _STOP_TABLES:
            assert await owner.fetchval("SELECT to_regclass($1)", "switchboard." + table) is None
        await asyncio.wait_for(
            asyncio.to_thread(command.upgrade, config, "switchboard@sw_048"), timeout=30
        )
        # This database needs the same real source admission configuration as
        # the existing ledger fixture; do not synthesize goal/association rows.
        from tests.switchboard.goal_admission_fixtures import limits
        from tests.switchboard.goal_ledger_fixtures import configure

        await configure(owner, limits())
        for evidence in ("association_only", "request_only", "work_with_FK_parents"):
            await owner.execute(
                "TRUNCATE switchboard.goals,switchboard.invocations,switchboard.effects,"
                "switchboard.budgets,switchboard.admission_decisions,switchboard.goal_inbox CASCADE"
            )
            if evidence == "request_only":
                goal = (await admit(runtime)).goal_id
                await GoalStop(runtime).stop_goal(goal, uuid4(), "a" * 64)
                assert (
                    await owner.fetchval("SELECT count(*) FROM switchboard.goal_park_associations")
                    == 0
                )
                target = "goal_stop_requests"
            else:
                _, goal, _, _, _ = await parked((owner, runtime), lost=True)
                if evidence == "work_with_FK_parents":
                    await GoalStop(runtime).stop_goal(goal, uuid4(), "a" * 64)
                    target = "goal_approval_cancel_work"
                else:
                    target = "goal_park_associations"
                    assert (
                        await owner.fetchval("SELECT count(*) FROM switchboard.goal_stop_requests")
                        == 0
                    )
            assert await owner.fetchval("SELECT count(*) FROM switchboard." + target) > 0
            before = await stop_schema_snapshot(owner)
            with pytest.raises(DBAPIError, match="Cannot discard Stop evidence"):
                await asyncio.wait_for(
                    asyncio.to_thread(command.downgrade, config, "switchboard@sw_047"), timeout=30
                )
            assert await stop_schema_snapshot(owner) == before
        # Real pg_dump in the existing disposable PostgreSQL container, as the
        # ordinary migration owner. Material is test-only and never printed.
        parsed = urlparse(url)
        wrapped = postgres_container.get_wrapped_container()
        dumped = await asyncio.wait_for(
            asyncio.to_thread(
                wrapped.exec_run,
                [
                    "timeout",
                    "--signal=TERM",
                    "--kill-after=2s",
                    "20s",
                    "pg_dump",
                    "--host=127.0.0.1",
                    "--port=5432",
                    "--username=" + unquote(parsed.username),
                    "--dbname=" + db_name,
                    "--format=custom",
                    "--schema=switchboard",
                ],
                environment={"PGPASSWORD": unquote(parsed.password)},
            ),
            timeout=25,
        )
        assert dumped.exit_code == 0
        assert dumped.output.startswith(b"PGDMP") and len(dumped.output) > 5
        assert await stop_schema_snapshot(owner) == before
        assert (
            await runtime.fetchval("SELECT count(*) FROM switchboard.goal_approval_cancel_work")
            == 1
        )
    # The testcontainers fixture owns its disposable databases. No production
    # connection, role workaround, extra Docker lane or dump file is involved.


class CommitResponseLostPool:
    """Only the response after a real COMMIT is faulted; all SQL is delegated."""

    def __init__(self, pool):
        self.pool = pool
        self.committed = False

    @asynccontextmanager
    async def acquire(self):
        async with self.pool.acquire() as connection:
            parent = self

            class Connection:
                def __getattr__(self, name):
                    return getattr(connection, name)

                @asynccontextmanager
                async def transaction(self):
                    async with connection.transaction():
                        yield
                    parent.committed = True
                    raise ConnectionError("controlled_response_lost_after_real_commit")

            yield Connection()


async def test_real_inflight_cancellation_and_committed_response_loss(ledger, ledger_url):
    owner, runtime = ledger
    _, goal, invocation, result, _ = await parked(ledger, lost=True)
    request = uuid4()
    async with actor_pool(ledger_url) as (actor, label):
        task = None
        async with sql_gate(owner, table="goal_stop_requests", event="INSERT", goal=goal) as (
            gate_pid,
            release,
        ):
            try:
                task = asyncio.create_task(GoalStop(actor).stop_goal(goal, request, "a" * 64))
                await wait_for_lock(owner, label, gate_pid)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, timeout=3)
            finally:
                await release()
                if task is not None:
                    await cancel_actors([task])
    async with owner.acquire() as readback:
        assert (
            await readback.fetchval("SELECT status FROM switchboard.goals WHERE id=$1", goal)
            == "running"
        )
        assert (
            await readback.fetchval(
                "SELECT token_hash FROM switchboard.invocations WHERE id=$1", invocation
            )
            is not None
        )
        assert not await readback.fetchval(
            "SELECT EXISTS(SELECT 1 FROM switchboard.goal_stop_requests WHERE request_id=$1)",
            request,
        )
        assert not await readback.fetchval(
            "SELECT EXISTS(SELECT 1 FROM switchboard.goal_approval_cancel_work WHERE effect_id=$1)",
            result.effect_id,
        )
    ambiguous = CommitResponseLostPool(runtime)
    with pytest.raises(ConnectionError, match="controlled_response_lost_after_real_commit"):
        await GoalStop(ambiguous).stop_goal(goal, request, "a" * 64)
    assert ambiguous.committed
    # A fresh real acquisition resolves the exact durable request, not the
    # missing response or a second logical Stop with a new request identity.
    async with runtime.acquire() as readback:
        durable = await readback.fetchrow(
            "SELECT * FROM switchboard.goal_stop_requests WHERE request_id=$1", request
        )
        assert durable["goal_id"] == goal and durable["provenance_digest"] == "a" * 64
        assert durable["revoked_tokens"] == 1 and durable["cancellation_work"] == 1
        assert (
            await readback.fetchval("SELECT status FROM switchboard.goals WHERE id=$1", goal)
            == "stopped"
        )
        assert (
            await readback.fetchval(
                "SELECT token_hash FROM switchboard.invocations WHERE id=$1", invocation
            )
            is None
        )
    replay = await GoalStop(runtime).stop_goal(goal, request, "a" * 64)
    assert replay.request_id == request and replay.cancellation_work == 1
    assert (
        await owner.fetchval(
            "SELECT count(*) FROM switchboard.goal_stop_requests WHERE goal_id=$1", goal
        )
        == 1
    )
    assert (
        await owner.fetchval(
            "SELECT count(*) FROM switchboard.goal_approval_cancel_work WHERE goal_id=$1", goal
        )
        == 1
    )
    assert (
        await owner.fetchval("SELECT used_calls FROM switchboard.budgets WHERE goal_id=$1", goal)
        == 1
    )
