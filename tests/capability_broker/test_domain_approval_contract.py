"""REQ-capability-authorization-009: real owning park producer, software store only.

These transaction doubles exercise actual SQL producer and rollback boundaries;
they are not PostgreSQL, a signature-verifying ingress or production wiring.
"""

import asyncio
import copy
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest

from butlers.core.capability_json import arguments_digest
from butlers.modules.approvals.goal_calls import park_goal_call, stored_goal_call
from butlers.modules.approvals.models import GOAL_LINEAGE_COLUMNS, GoalCallLineage
from butlers.modules.approvals.park import park_pending_action, park_prepared_action

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class OwningStore:
    """Stateful, default-off owning schema; unsupported SQL refuses the control."""

    def __init__(self, *, migrated=True):
        self.rows = {}
        self.queries = []
        self.migrated = migrated
        self.pause = None
        self.entered = asyncio.Event()

    @asynccontextmanager
    async def transaction(self):
        before = copy.deepcopy(self.rows)
        try:
            yield self
        except BaseException:
            self.rows = before
            raise

    async def fetchval(self, sql, *args):
        self.queries.append(sql)
        if "WHERE goal_id = $1 AND goal_effect_id = $2" in sql:
            if not self.migrated:
                raise asyncpg.UndefinedColumnError("controlled_unmigrated_schema")
            return next(
                (
                    r["id"]
                    for r in self.rows.values()
                    if r.get("goal_id") == args[0] and r.get("goal_effect_id") == args[1]
                ),
                None,
            )
        raise AssertionError("unsupported_store_read")

    async def fetchrow(self, sql, *args):
        self.queries.append(sql)
        if "FROM approval_delivery_rollout" in sql:
            self.entered.set()
            if self.pause is not None:
                await self.pause.wait()
            return {"admission_enabled": False, "worker_enabled": False}
        if "SELECT * FROM pending_actions" in sql:
            return copy.deepcopy(self.rows.get(args[0]))
        if "SELECT id" in sql and "FROM pending_actions" in sql:
            matches = [
                r
                for r in self.rows.values()
                if r["id"] == args[0]
                or (
                    args[1] is not None
                    and r.get("deduplication_key") == args[1]
                    and r["status"] in {"pending", "approved", "rejected", "abandoned"}
                )
            ]
            return {"id": matches[0]["id"]} if matches else None
        raise AssertionError("unsupported_store_row")

    async def execute(self, sql, *args):
        self.queries.append(sql)
        if "pg_advisory_xact_lock" in sql:
            return "SELECT 1"
        if "INSERT INTO pending_actions" in sql:
            names = (
                "id",
                "tool_name",
                "tool_args",
                "agent_summary",
                "session_id",
                "origin",
                "requested_at",
                "expires_at",
                "why",
                "evidence",
                "blast_radius",
                "reversibility",
                "deduplication_key",
            )
            if "goal_id" in sql:
                if not self.migrated:
                    raise asyncpg.UndefinedColumnError("controlled_unmigrated_schema")
                names += GOAL_LINEAGE_COLUMNS
            assert len(names) == len(args)
            row = dict(zip(names, copy.deepcopy(args), strict=True)) | {"status": "pending"}
            assert row["id"] not in self.rows
            self.rows[row["id"]] = row
            return "INSERT 0 1"
        raise AssertionError("unsupported_store_write")


class DeliveryOwningStore(OwningStore):
    """Enabled admission state including the actual written intent/presentation."""

    def __init__(self, *, refuse_presentation=False):
        super().__init__()
        self.intents = {}
        self.presentations = {}
        self.refuse_presentation = refuse_presentation

    @asynccontextmanager
    async def transaction(self):
        before = copy.deepcopy((self.rows, self.intents, self.presentations))
        try:
            yield self
        except BaseException:
            self.rows, self.intents, self.presentations = before
            raise

    async def fetchval(self, sql, *args):
        if sql == "SELECT clock_timestamp()":
            return datetime.now(UTC)
        if sql == "SELECT current_schema()":
            return "controlled_domain"
        if "SELECT count(*) FROM approval_delivery_intents" in sql:
            return len(self.intents)
        return await super().fetchval(sql, *args)

    async def fetchrow(self, sql, *args):
        if "FROM approval_delivery_rollout" in sql:
            return {"admission_enabled": True, "worker_enabled": False}
        if "FROM public.approvals_policy" in sql:
            return {"quiet_start_hour": None, "quiet_end_hour": None, "timezone": "UTC"}
        if "FROM approval_delivery_cohorts" in sql:
            return None  # This controlled first admission uses the single-action route.
        if "SELECT pa.id AS action_id" in sql:
            row = next(
                (
                    r
                    for r in self.rows.values()
                    if r["id"] == args[0]
                    or (
                        args[1] is not None
                        and r.get("deduplication_key") == args[1]
                        and r["status"] in {"pending", "approved", "rejected", "abandoned"}
                    )
                ),
                None,
            )
            if row is None:
                return None
            intent = next(i for i in self.intents.values() if i["action_id"] == row["id"])
            presentation = self.presentations[intent["id"]]
            return {
                "action_id": row["id"],
                "intent_id": intent["id"],
                "action_key": intent["action_key"],
                "admission_mode": intent["admission_mode"],
                "presentation_key": presentation["presentation_key"],
                "cohort_key": None,
                "not_before": presentation["not_before"],
            }
        return await super().fetchrow(sql, *args)

    async def execute(self, sql, *args):
        if "INSERT INTO approval_delivery_intents" in sql:
            names = (
                "id",
                "action_id",
                "action_key",
                "owning_schema",
                "origin_butler",
                "admission_mode",
                "created_at",
            )
            intent = dict(zip(names, copy.deepcopy(args), strict=True))
            assert intent["action_id"] in self.rows and intent["id"] not in self.intents
            self.intents[intent["id"]] = intent
            return "INSERT 0 1"
        if "INSERT INTO approval_delivery_presentations" in sql:
            if self.refuse_presentation:
                raise ValueError("controlled_presentation_constraint")
            names = (
                "intent_id",
                "cohort_id",
                "subject_key",
                "subject_kind",
                "presentation_mode",
                "presentation_generation",
                "presentation_key",
                "state",
                "last_reason_code",
                "not_before",
            )
            presentation = dict(zip(names, copy.deepcopy(args), strict=True))
            assert presentation["intent_id"] in self.intents
            self.presentations[presentation["intent_id"]] = presentation
            return "INSERT 0 1"
        return await super().execute(sql, *args)


def lineage_for(args):
    return GoalCallLineage(
        uuid4(), uuid4(), 0, True, "owner", uuid4(), "mail__send", arguments_digest(args)
    )


def park(store, lineage, args, **changes):
    options = dict(
        action_id=uuid4(),
        tool_name="send",
        raw_args=args,
        lineage=lineage,
        origin_butler="mail",
        requested_at=datetime.now(UTC),
        expires_at=None,
    )
    return park_goal_call(store, **(options | changes))


async def test_actual_park_duplicate_identity_and_drift_do_not_mutate_stored_action():
    store = OwningStore()
    args = {"message": "controlled", "recipient": "test-target"}
    lineage = lineage_for(args)
    first = await park(store, lineage, args, deduplication_key="controlled-collision")
    before = copy.deepcopy(store.rows)
    second = await park(store, lineage, dict(reversed(list(args.items()))))
    assert second.duplicate and second.action_id == first.action_id
    changed = args | {"message": "different"}
    for other, wire in [
        (replace(lineage, goal_id=uuid4()), args),
        (replace(lineage, invocation_id=uuid4()), args),
        (replace(lineage, goal_args_digest=arguments_digest(changed)), changed),
    ]:
        with pytest.raises(ValueError, match="goal_duplicate_binding_mismatch"):
            await park(store, other, wire, deduplication_key="controlled-collision")
        assert store.rows == before
    row = store.rows[first.action_id]
    assert row["tool_args"] == args and row["goal_id"] == lineage.goal_id
    assert row["goal_exposed"] is True and row["origin"] is None
    assert not any("approval_delivery_intents" in q for q in store.queries)


async def test_enabled_admission_reuses_actual_intent_and_refuses_cross_goal_or_argument_drift():
    store = DeliveryOwningStore()
    args = {"message": "controlled"}
    lineage = lineage_for(args)
    first = await park(store, lineage, args, deduplication_key="controlled-enabled")
    assert not first.duplicate and first.admission_mode == "single"
    assert store.rows[first.action_id]["goal_id"] == lineage.goal_id
    intent = store.intents[first.intent_id]
    presentation = store.presentations[first.intent_id]
    assert intent["action_id"] == first.action_id
    assert intent["origin_butler"] == "mail"
    assert presentation["presentation_key"] == first.presentation_key
    assert presentation["state"] == "ready" and presentation["cohort_id"] is None
    assert first.not_before == presentation["not_before"]
    before = copy.deepcopy((store.rows, store.intents, store.presentations))
    duplicate = await park(store, lineage, args)
    assert duplicate.duplicate and not duplicate.legacy_duplicate
    assert duplicate.action_id == first.action_id and duplicate.intent_id == first.intent_id
    assert duplicate.presentation_key == first.presentation_key
    changed = args | {"message": "altered"}
    for other, wire in [
        (replace(lineage, goal_id=uuid4()), args),
        (replace(lineage, invocation_id=uuid4()), args),
        (replace(lineage, goal_args_digest=arguments_digest(changed)), changed),
    ]:
        with pytest.raises(ValueError, match="goal_duplicate_binding_mismatch"):
            await park(store, other, wire, deduplication_key="controlled-enabled")
        assert (store.rows, store.intents, store.presentations) == before


async def test_enabled_presentation_refusal_leaves_no_approval_or_delivery_orphan():
    store = DeliveryOwningStore(refuse_presentation=True)
    args = {"message": "controlled"}
    with pytest.raises(ValueError, match="controlled_presentation_constraint"):
        await park(store, lineage_for(args), args)
    assert store.rows == store.intents == store.presentations == {}


async def test_raw_argument_snapshot_precedes_first_store_await():
    store = OwningStore()
    store.pause = asyncio.Event()
    args = {"message": {"body": "original"}}
    lineage = lineage_for(args)
    task = asyncio.create_task(park(store, lineage, args))
    try:
        async with asyncio.timeout(2):
            await store.entered.wait()
            args["message"]["body"] = "mutated"
            store.pause.set()
            receipt = await task
    finally:
        store.pause.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert store.rows[receipt.action_id]["tool_args"] == {"message": {"body": "original"}}
    assert store.rows[receipt.action_id]["goal_args_digest"] == lineage.goal_args_digest


async def test_unmigrated_goal_schema_refuses_without_legacy_fallback():
    store = OwningStore(migrated=False)
    args = {"message": "controlled"}
    with pytest.raises(asyncpg.UndefinedColumnError):
        await park(store, lineage_for(args), args)
    assert store.rows == {}
    assert not any("INSERT INTO pending_actions" in q for q in store.queries)


async def test_ordinary_and_prepared_rows_keep_the_legacy_insert_and_origin():
    store = OwningStore(migrated=False)
    now = datetime.now(UTC)
    ordinary = await park_pending_action(
        store,
        action_id=uuid4(),
        tool_name="send",
        tool_args={"message": "ordinary"},
        agent_summary=None,
        requested_at=now,
        expires_at=None,
        origin_butler="mail",
    )
    prepared = await park_prepared_action(
        store,
        action_id=uuid4(),
        tool_name="send",
        tool_args={"message": "prepared"},
        agent_summary=None,
        requested_at=now,
        expires_at=now + timedelta(hours=1),
        origin_butler="mail",
        deduplication_key="prepared",
    )
    assert store.rows[ordinary.action_id]["origin"] is None
    assert store.rows[prepared.action_id]["origin"] == "prepared"
    assert all("goal_id" not in row for row in store.rows.values())
    assert not any("goal_id" in query for query in store.queries)


async def test_stored_approved_projection_uses_actual_owner_edit_not_original_args():
    store = OwningStore()
    args = {"message": "original"}
    lineage = lineage_for(args)
    receipt = await park(store, lineage, args)
    row = store.rows[receipt.action_id]
    with pytest.raises(ValueError, match="stored_goal_approval_required"):
        stored_goal_call(row)
    edited = {"message": "owner edit"}
    row.update(
        status="approved", tool_args=edited, goal_approved_args_digest=arguments_digest(edited)
    )
    call = stored_goal_call(row)
    assert call.approval_ref == receipt.action_id and call.args == edited
    assert call.lineage.goal_args_digest == arguments_digest(args)
    assert call.lineage.goal_approved_args_digest == arguments_digest(edited)
    row["tool_args"] = {"message": "unapproved replacement"}
    with pytest.raises(ValueError, match="approved_arguments_mismatch"):
        stored_goal_call(row)
