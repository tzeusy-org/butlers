"""Stateful software repository controls, not PostgreSQL/RLS or owner-auth proof."""

import asyncio
import copy
from contextlib import asynccontextmanager
from dataclasses import replace
from uuid import uuid4

import pytest

from butlers.core.goal_stop import CancelIdentity, GoalStop, StopUnavailable
from tests.capability_broker.test_broker import Connection, Pool

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class StopConnection(Connection):
    """Small owning row store for actual repository SQL and broker orchestration.

    Only the explicit SQL boundaries below are modelled. Real query syntax,
    locking, transaction isolation, role/RLS and migration outcomes need PG.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.associations, self.effects, self.requests, self.work = {}, {}, {}, {}
        self.invocations = [
            {"id": self.invocation, "token_hash": "controlled", "status": "running"}
        ]
        self.fail_at = None
        self.schema_valid = True

    @asynccontextmanager
    async def transaction(self):
        assert not self.in_transaction
        before = copy.deepcopy(self.__dict__)
        self.in_transaction = True
        try:
            yield
            if self.fail_commit:
                raise RuntimeError("controlled_commit_unknown")
            self.events.append("commit")
        except BaseException:
            self.__dict__.clear()
            self.__dict__.update(before)
            raise
        finally:
            self.in_transaction = False

    def seed_effect(self, *, status="unknown", witness=None):
        effect = uuid4()
        self.effects[effect] = dict(
            id=effect,
            goal_id=self.goal,
            invocation_id=self.invocation,
            capability="health__write",
            args_digest="a" * 64,
            status=status,
        )
        self.associations[effect] = dict(
            effect_id=effect,
            goal_id=self.goal,
            owning_domain="health",
            action_id=effect,
            allow_standing_rule=False,
            approval_ref=None,
            dispatch_intent_id=witness,
        )
        return CancelIdentity(self.goal, effect, "health", effect, "health__write", "a" * 64)

    async def fetch(self, sql, *args):
        if "FROM pg_class" in sql:
            return [
                dict(
                    relname=t,
                    relrowsecurity=True,
                    relforcerowsecurity=False,
                    owner_member=False,
                    policies=4,
                    policy_valid=self.schema_valid,
                )
                for t in args[0]
            ]
        if "SELECT id,token_hash" in sql:
            return copy.deepcopy(self.invocations)
        if "INSERT INTO switchboard.goal_approval_cancel_work" in sql:
            if self.fail_at == "work":
                raise RuntimeError("controlled_work_rollback")
            inserted = []
            for effect, association in self.associations.items():
                row = self.effects[effect]
                if (
                    association["dispatch_intent_id"] is None
                    and row["status"] in {"requested", "parked", "approved", "unknown"}
                    and effect not in self.work
                ):
                    self.work[effect] = dict(
                        effect_id=effect,
                        goal_id=args[0],
                        request_id=args[1],
                        state="pending",
                        generation=0,
                        claim_id=None,
                        lease_valid=False,
                        receipt_digest=None,
                        outcome=None,
                    )
                    inserted.append({"effect_id": effect})
            return inserted
        return await super().fetch(sql, *args)

    async def fetchval(self, sql, *args):
        if "SELECT count(*) FROM switchboard.goal_approval_cancel_work" in sql:
            return sum(row["state"] != "acknowledged" for row in self.work.values())
        return await super().fetchval(sql, *args)

    async def fetchrow(self, sql, *args):
        if "FROM switchboard.goal_stop_requests" in sql:
            return self.requests.get(args[0])
        if "FROM switchboard.goal_approval_cancel_work w" in sql:
            row = self.work.get(args[1])
            if not row:
                return None
            return dict(
                row,
                **{k: v for k, v in self.associations[args[1]].items() if k not in row},
                capability=self.effects[args[1]]["capability"],
                args_digest=self.effects[args[1]]["args_digest"],
                effect_status=self.effects[args[1]]["status"],
            )
        if "UPDATE switchboard.goal_approval_cancel_work" in sql:
            row = self.work[args[1]]
            if "SET state='claimed'" in sql:
                if row["generation"] != args[4] or row["lease_valid"]:
                    return None
                row.update(
                    state="claimed",
                    claim_id=args[2],
                    generation=row["generation"] + 1,
                    lease_valid=True,
                )
                return {"generation": row["generation"]}
            if not row["lease_valid"] or row["claim_id"] != args[2] or row["generation"] != args[3]:
                return None
            row.update(state="acknowledged", receipt_digest=args[4], outcome=args[5])
            return {"effect_id": args[1]}
        if "WITH binding AS" in sql:
            association, effect = self.associations.get(args[0]), self.effects.get(args[0])
            if (
                not association
                or not effect
                or effect["goal_id"] != args[1]
                or effect["invocation_id"] != args[4]
                or effect["capability"] != args[5]
                or effect["args_digest"] != args[6]
                or association["owning_domain"] != args[8]
                or association["allow_standing_rule"] != args[9]
                or association["dispatch_intent_id"] is not None
                or association["approval_ref"] not in (None, args[3])
            ):
                return None
            if args[3] is not None:
                association["approval_ref"] = args[3]
            if not args[7]:
                effect["status"] = args[2]
            self.events.append(args[2])
            return {"id": args[0]}
        return await super().fetchrow(sql, *args)

    async def execute(self, sql, *args):
        if "INSERT INTO switchboard.effects" in sql:
            self.effects[args[0]] = dict(
                id=args[0],
                goal_id=args[1],
                invocation_id=args[2],
                capability=args[3],
                args_digest=args[4],
                status="requested",
            )
        if "INSERT INTO switchboard.goal_park_associations" in sql:
            self.associations[args[0]] = dict(
                effect_id=args[0],
                goal_id=args[1],
                owning_domain=args[2],
                action_id=args[0],
                allow_standing_rule=args[3],
                approval_ref=None,
                dispatch_intent_id=None,
            )
        if "SET token_hash=NULL" in sql:
            for row in self.invocations:
                row["token_hash"] = None
                if row["status"] in {"launching", "running"}:
                    row["status"] = "stopped"
        if "SET status='stopped'" in sql:
            self.status = "stopped"
        if "INSERT INTO switchboard.goal_stop_requests" in sql:
            if self.fail_at == "receipt":
                raise RuntimeError("controlled_receipt_rollback")
            self.requests[args[0]] = dict(
                request_id=args[0],
                goal_id=args[1],
                provenance_digest=args[2],
                revoked_tokens=args[3],
                cancellation_work=args[4],
            )
        if "UPDATE switchboard.effects SET status='cancelled'" in sql:
            self.effects[args[0]]["status"] = "cancelled"
        return await super().execute(sql, *args)


async def test_Stop_all_nonterminal_states_revokes_finished_tokens_without_claiming_remote_cancel():
    for status in ("admitted", "running", "awaiting_approval", "recovering", "settling", "stopped"):
        connection = StopConnection()
        connection.status = status
        connection.invocations += [dict(id=uuid4(), token_hash="controlled", status="returned")]
        lost = connection.seed_effect()
        dispatched = connection.seed_effect(witness=uuid4())
        committed = connection.seed_effect(status="committed", witness=uuid4())
        stop = GoalStop(Pool(connection))
        request = uuid4()
        result = await stop.stop_goal(connection.goal, request, "b" * 64)
        assert result.revoked_tokens == 2 and result.cancellation_work == 1
        assert result.owning_cancellation_pending and connection.status == "stopped"
        assert [r["status"] for r in connection.invocations] == ["stopped", "returned"]
        assert all(r["token_hash"] is None for r in connection.invocations)
        assert set(connection.work) == {lost.effect_id}
        assert connection.effects[dispatched.effect_id]["status"] == "unknown"
        assert connection.effects[committed.effect_id]["status"] == "committed"
        assert await stop.stop_goal(connection.goal, request, "b" * 64) == result
        repeated = await stop.stop_goal(connection.goal, uuid4(), "b" * 64)
        assert repeated.cancellation_work == 1 and repeated.owning_cancellation_pending
        with pytest.raises(StopUnavailable, match="stop_request_conflict"):
            await stop.stop_goal(connection.goal, request, "c" * 64)
    for status in ("completed", "partial", "failed", "budget_exhausted"):
        connection = StopConnection()
        connection.status = status
        with pytest.raises(StopUnavailable, match="goal_not_stoppable"):
            await GoalStop(Pool(connection)).stop_goal(connection.goal, uuid4(), "b" * 64)
        assert connection.invocations[0]["token_hash"] is not None and not connection.requests


async def test_Stop_rollback_includes_revocation_work_and_receipt_and_schema_drift_refuses():
    for stage in ("work", "receipt", "commit"):
        connection = StopConnection()
        connection.seed_effect()
        connection.fail_at = stage
        connection.fail_commit = stage == "commit"
        with pytest.raises(RuntimeError, match="controlled_"):
            await GoalStop(Pool(connection)).stop_goal(connection.goal, uuid4(), "b" * 64)
        assert connection.status == "running" and connection.invocations[0]["token_hash"]
        assert not connection.requests and not connection.work and not connection.in_transaction
    connection = StopConnection()
    connection.schema_valid = False
    with pytest.raises(StopUnavailable, match="stop_schema_unavailable"):
        await GoalStop(Pool(connection)).stop_goal(connection.goal, uuid4(), "b" * 64)
    assert connection.status == "running"


async def test_reclaimed_cancel_work_fences_stale_or_wrong_identity_and_ACK_is_exactly_idempotent():
    connection = StopConnection()
    identity = connection.seed_effect()
    stop = GoalStop(Pool(connection))
    await stop.stop_goal(connection.goal, uuid4(), "b" * 64)
    first = await stop.claim_cancel_work(identity)
    assert first.generation == 1 and await stop.claim_cancel_work(identity) is None
    connection.work[identity.effect_id]["lease_valid"] = False
    second = await stop.claim_cancel_work(identity)
    assert second.generation == 2 and second.claim_id != first.claim_id
    for forged in (first, replace(second, identity=replace(identity, args_digest="d" * 64))):
        with pytest.raises(StopUnavailable, match="cancel_ack_conflict"):
            await stop.ack_cancel_work(forged, "c" * 64, "cancelled")
    for unearned in ("unknown", "not_found", "dispatched", "committed"):
        with pytest.raises(ValueError, match="invalid_cancel_ack"):
            await stop.ack_cancel_work(second, "c" * 64, unearned)
    await stop.ack_cancel_work(second, "c" * 64, "fenced_absent")
    await stop.ack_cancel_work(second, "c" * 64, "fenced_absent")
    assert connection.work[identity.effect_id]["state"] == "acknowledged"
    assert connection.effects[identity.effect_id]["status"] == "cancelled"
    with pytest.raises(StopUnavailable, match="cancel_ack_conflict"):
        await stop.ack_cancel_work(second, "d" * 64, "fenced_absent")


async def test_actual_asyncpg_UUID_rows_reclaim_and_ACK_without_accepting_wire_coercions():
    # This is the pinned driver's real decoded UUID type, not a test lookalike.
    # No database/role/locking claim is made by this repository software control.
    from uuid import UUID

    from asyncpg.pgproto.pgproto import UUID as DriverUUID

    class DriverRows(StopConnection):
        async def fetchrow(self, sql, *args):
            row = await super().fetchrow(sql, *args)
            if row and "FROM switchboard.goal_approval_cancel_work w" in sql:
                return {
                    key: DriverUUID(str(value)) if isinstance(value, UUID) else value
                    for key, value in row.items()
                }
            return row

    connection = DriverRows()
    native = connection.seed_effect()
    identity = replace(
        native,
        goal_id=DriverUUID(str(native.goal_id)),
        effect_id=DriverUUID(str(native.effect_id)),
        action_id=DriverUUID(str(native.action_id)),
    )
    stop = GoalStop(Pool(connection))
    await stop.stop_goal(identity.goal_id, DriverUUID(str(uuid4())), "b" * 64)
    first = await stop.claim_cancel_work(identity)
    assert first.generation == 1 and await stop.claim_cancel_work(identity) is None
    # Accept a typed database value, never parse a caller's representation into authority.
    for invalid in (str(native.goal_id), native.goal_id.bytes, 1, True, None, {}):
        with pytest.raises(ValueError, match="invalid_stop_identity"):
            replace(identity, goal_id=invalid)
        with pytest.raises(ValueError, match="invalid_stop_identity"):
            replace(first, claim_id=invalid)
        with pytest.raises(ValueError, match="invalid_stop_identity"):
            await stop.stop_goal(invalid, uuid4(), "b" * 64)
    connection.work[identity.effect_id]["lease_valid"] = False
    second = await stop.claim_cancel_work(identity)
    assert second.generation == first.generation + 1 and second.claim_id != first.claim_id
    with pytest.raises(StopUnavailable, match="cancel_ack_conflict"):
        await stop.ack_cancel_work(first, "c" * 64, "cancelled")
    decoded_claim = replace(second, claim_id=DriverUUID(str(second.claim_id)))
    await stop.ack_cancel_work(decoded_claim, "c" * 64, "fenced_absent")
    await stop.ack_cancel_work(decoded_claim, "c" * 64, "fenced_absent")
    assert connection.work[identity.effect_id]["state"] == "acknowledged"
    assert connection.effects[identity.effect_id]["status"] == "cancelled"


async def test_dispatch_witness_after_claim_refuses_ACK_without_rewriting_uncertainty():
    connection = StopConnection()
    identity = connection.seed_effect()
    stop = GoalStop(Pool(connection))
    await stop.stop_goal(connection.goal, uuid4(), "b" * 64)
    claim = await stop.claim_cancel_work(identity)
    # Deliberate store corruption is a consumer refusal control. Real sw048
    # prevents installing this witness after Stop; that requires PG proof.
    connection.associations[identity.effect_id]["dispatch_intent_id"] = uuid4()
    with pytest.raises(StopUnavailable, match="cancel_ack_conflict"):
        await stop.ack_cancel_work(claim, "c" * 64, "cancelled")
    assert connection.effects[identity.effect_id]["status"] == "unknown"
    assert connection.work[identity.effect_id]["state"] == "claimed"


async def test_acquire_deadline_and_external_cancellation_propagate_without_owned_tasks():
    entered = asyncio.Event()
    released = asyncio.Event()

    class WaitingPool:
        @asynccontextmanager
        async def acquire(self):
            entered.set()
            try:
                await asyncio.Event().wait()
                yield
            finally:
                released.set()

    stop = GoalStop(WaitingPool(), timeout=0.02)
    with pytest.raises(TimeoutError):
        await stop.stop_goal(uuid4(), uuid4(), "b" * 64)
    assert released.is_set()
    released.clear()
    task = asyncio.create_task(GoalStop(WaitingPool()).stop_goal(uuid4(), uuid4(), "b" * 64))
    await entered.wait()
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert released.is_set() and task.done()
