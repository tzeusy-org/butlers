"""Real central approved-replay/Stop contracts, authored, NOT RUN locally.

No owning approval schema or authenticated A2 producer is simulated as delivered.
The controlled stored row is input data; every central lock, trigger, role,
commit/rollback and independent observation below uses the real sw047/sw048 DB.
"""

import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from butlers.capability_broker.assertions import AssertionSigner
from butlers.capability_broker.contracts import BrokerRefused, Capability, Reason, mint_token
from butlers.capability_broker.domains import DomainReceipt, ParkReceipt
from butlers.capability_broker.service import Broker
from butlers.core.capability_json import arguments_digest
from butlers.core.goal_admission import (
    Capability as AdmissionCapability,
)
from butlers.core.goal_admission import (
    CatalogView,
    Evidence,
    GoalAdmission,
    Grant,
    SourceRef,
    digest,
    encode_grants,
)
from butlers.core.goal_stop import GoalStop
from butlers.core.tool_catalog import RecipientParameters
from butlers.modules.approvals.goal_calls import StoredGoalCall
from butlers.modules.approvals.models import GoalCallLineage
from tests.integration.test_capability_broker_transactions import (
    ControlledAuthority,
    ControlledDomain,
)
from tests.switchboard.goal_admission_fixtures import Sources

pytest_plugins = ("tests.switchboard.goal_ledger_fixtures",)
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def stored_call(ledger):
    owner, runtime = ledger
    capability = Capability(
        "health",
        "write",
        "record",
        "untrusted",
        (),
        always_per_event=True,
        approvals_available=True,
        recipient_params=RecipientParameters(),
    )

    class AdmissionCatalog:
        async def load(self, connection):
            return CatalogView(
                "controlled-approved-replay-v1",
                (AdmissionCapability(capability.identity, "health", "record"),),
            )

    # Use real admission with the same canonical domain__tool spelling. The
    # older dot-name admission fixtures cannot stand in for this exact ceiling.
    admitted = await GoalAdmission(
        runtime, Sources(Evidence("message", "controlled-owner", owner=True)), AdmissionCatalog()
    ).admit_submission(SourceRef("message", str(uuid4())), uuid4())
    goal, invocation = admitted.goal_id, uuid4()
    await owner.execute("UPDATE switchboard.goals SET status='running' WHERE id=$1", goal)
    token, hashed = mint_token()
    grants = encode_grants((Grant(capability.identity),))
    await owner.execute(
        "INSERT INTO switchboard.invocations "
        "(id,goal_id,depth,role,lead_epoch,status,grant_snapshot,grant_digest,"
        "max_turns,max_tokens,token_hash) "
        "VALUES ($1,$2,0,'lead',0,'running',$3::jsonb,$4,5,500,$5)",
        invocation,
        goal,
        grants,
        digest(grants),
        hashed,
    )
    domain = ControlledDomain()
    broker = Broker(
        runtime,
        ControlledAuthority(capability),
        domain,
        AssertionSigner("central-replay-control", Ed25519PrivateKey.generate()),
    )

    async def park(reservation, arguments, assertion):
        domain.calls.append(reservation.effect_id)
        return ParkReceipt(str(reservation.effect_id))

    domain.park = park
    original = {"value": "original"}
    result = await broker.call(goal, token, capability.identity, original)
    await owner.execute(
        "UPDATE switchboard.invocations SET status='returned',token_hash=NULL WHERE id=$1",
        invocation,
    )
    await owner.execute("UPDATE switchboard.goals SET status='settling' WHERE id=$1", goal)
    approved = {"value": "owner edit"}
    call = StoredGoalCall(
        result.effect_id,
        "write",
        approved,
        GoalCallLineage(
            goal,
            invocation,
            0,
            False,
            "owner",
            result.effect_id,
            capability.identity,
            arguments_digest(original),
            arguments_digest(approved),
        ),
    )
    return broker, call, domain


class CommitBoundary:
    """Pause a real transaction BEFORE commit; optionally lose its reply AFTER.

    This wraps only its actual transaction context, not SQL or stored results.
    The original role, connection and PostgreSQL transaction remain in use.
    """

    def __init__(self, connection, *, pause=False, lost_reply=False, rollback=False):
        self.connection = connection
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.pause, self.lost_reply, self.rollback = pause, lost_reply, rollback

    def __getattr__(self, name):
        return getattr(self.connection, name)

    @asynccontextmanager
    async def transaction(self):
        async with self.connection.transaction():
            yield
            self.entered.set()
            if self.pause:
                async with asyncio.timeout(3):
                    await self.release.wait()
            if self.rollback:
                raise RuntimeError("controlled_precommit_rollback")
        if self.lost_reply:
            raise RuntimeError("controlled_postcommit_reply_loss")


class BoundPool:
    def __init__(self, connection):
        self.connection = connection

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


async def observed_lock_wait(owner, connection):
    pid = connection.get_server_pid()
    async with asyncio.timeout(2):
        while not await owner.fetchval(  # noqa: ASYNC110 -- bounded real server lock observation
            "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE pid=$1 "
            "AND state='active' AND wait_event_type='Lock')",
            pid,
        ):
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("first", ["stop", "replay"])
async def test_real_approved_replay_and_Stop_both_commit_orders(ledger, first):
    owner, runtime = ledger
    broker, call, domain = await stored_call(ledger)
    async with runtime.acquire() as first_connection, runtime.acquire() as second_connection:
        held = CommitBoundary(first_connection, pause=True)
        first_pool, second_pool = BoundPool(held), BoundPool(second_connection)
        request = uuid4()

        async def stop(pool):
            return await GoalStop(pool).stop_goal(call.lineage.goal_id, request, "a" * 64)

        async def replay(pool):
            broker.pool = pool
            return await broker.prepare_approved_dispatch(call)

        left = asyncio.create_task(stop(first_pool) if first == "stop" else replay(first_pool))
        right = None
        try:
            async with asyncio.timeout(3):
                await held.entered.wait()
            right = asyncio.create_task(
                replay(second_pool) if first == "stop" else stop(second_pool)
            )
            await observed_lock_wait(owner, second_connection)
            held.release.set()
            results = await asyncio.wait_for(asyncio.gather(left, right, return_exceptions=True), 4)
            assert not isinstance(results[0], BaseException)
            if first == "stop":
                assert isinstance(results[1], BrokerRefused)
                assert results[1].reason is Reason.GOAL
            else:
                assert not isinstance(results[1], BaseException)
                assert results[1].cancellation_work == 0
        finally:
            held.release.set()
            for task in (left, right):
                if task is not None and not task.done():
                    task.cancel()
            async with asyncio.timeout(5):
                await asyncio.gather(*(t for t in (left, right) if t), return_exceptions=True)
    # This is a controlled result, not an authenticated owning receipt.
    # Central settlement must still preserve the original digest and may
    # settle an earlier dispatch after Stop, with exposure in the same TX.
    if first == "replay":
        broker.pool = runtime
        await broker.settle_approved_dispatch(
            results[0], DomainReceipt("committed", receipt_ref="controlled-result")
        )
    async with runtime.acquire() as readback:
        row = await readback.fetchrow(
            "SELECT e.status,e.args_digest,a.dispatch_intent_id FROM switchboard.effects e "
            "JOIN switchboard.goal_park_associations a ON a.effect_id=e.id WHERE e.id=$1",
            call.lineage.goal_effect_id,
        )
        assert row["args_digest"] == call.lineage.goal_args_digest
        assert (row["dispatch_intent_id"] is not None) == (first == "replay")
        assert row["status"] == ("committed" if first == "replay" else "parked")
        assert (
            await readback.fetchval(
                "SELECT used_calls FROM switchboard.budgets WHERE goal_id=$1", call.lineage.goal_id
            )
            == 1
        )
        assert (
            await readback.fetchval(
                "SELECT status FROM switchboard.goals WHERE id=$1", call.lineage.goal_id
            )
            == "stopped"
        )
        assert await readback.fetchval(
            "SELECT exposed FROM switchboard.goals WHERE id=$1", call.lineage.goal_id
        ) == (first == "replay")
    assert len(domain.calls) == 1  # Only original PARK; central preparation never forwards.


@pytest.mark.parametrize("fault", ["rollback", "lost_reply"])
async def test_real_central_approved_intent_commit_fault_readback(ledger, fault):
    owner, runtime = ledger
    broker, call, domain = await stored_call(ledger)
    async with runtime.acquire() as connection:
        boundary = CommitBoundary(
            connection, rollback=fault == "rollback", lost_reply=fault == "lost_reply"
        )
        broker.pool = BoundPool(boundary)
        with pytest.raises(BrokerRefused) as failure:
            await broker.prepare_approved_dispatch(call)
        assert failure.value.reason is Reason.UNAVAILABLE
    async with runtime.acquire() as readback:
        row = await readback.fetchrow(
            "SELECT e.status,a.dispatch_intent_id FROM switchboard.effects e "
            "JOIN switchboard.goal_park_associations a ON a.effect_id=e.id WHERE e.id=$1",
            call.lineage.goal_effect_id,
        )
        assert row["status"] == ("parked" if fault == "rollback" else "dispatched")
        assert (row["dispatch_intent_id"] is not None) == (fault == "lost_reply")
    broker.pool = runtime
    if fault == "lost_reply":
        with pytest.raises(BrokerRefused) as repeated:
            await broker.prepare_approved_dispatch(call)
        assert repeated.value.reason is Reason.DUPLICATE
    else:
        prepared = await broker.prepare_approved_dispatch(call)
        assert prepared.reservation.args_digest == arguments_digest(call.args)
    assert len(domain.calls) == 1
