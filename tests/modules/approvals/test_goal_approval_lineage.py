"""REQ-capability-authorization-009: owning approval lineage cannot disappear."""

import copy
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from butlers.config import ApprovalConfig, ApprovalRiskTier
from butlers.core.capability_json import arguments_digest
from butlers.modules.approvals.autonomy_tracker import (
    check_promotion_threshold,
    compute_fingerprint,
    record_approval,
)
from butlers.modules.approvals.delivery_lifecycle import transition_pending_action
from butlers.modules.approvals.goal_calls import stored_goal_call
from butlers.modules.approvals.models import ActionStatus, PendingAction
from butlers.modules.approvals.module import ApprovalsModule
from butlers.modules.approvals.operations import approve_action, cancel_goal_pending_action

pytestmark = pytest.mark.unit


def action_row(**changes):
    row = {
        "id": uuid4(),
        "tool_name": "send",
        "tool_args": {"message": "controlled"},
        "status": "pending",
        "requested_at": datetime.now(UTC),
        "goal_id": uuid4(),
        "invocation_id": uuid4(),
        "goal_depth": 0,
        "goal_exposed": True,
        "goal_origin": "owner",
        "goal_effect_id": uuid4(),
        "goal_capability": "mail__send",
        "goal_args_digest": arguments_digest({"message": "controlled"}),
        "goal_approved_args_digest": None,
    }
    return row | changes


def test_live_goal_lineage_survives_stored_row_and_wire_roundtrip():
    row = action_row(origin="prepared")
    action = PendingAction.from_row(row)
    wire = action.to_dict()
    assert wire["goal_id"] == str(row["goal_id"])
    assert wire["goal_capability"] == "mail__send"
    assert wire["goal_exposed"] is True
    assert wire["goal_args_digest"] == row["goal_args_digest"]
    assert wire["origin"] == "prepared"
    assert PendingAction.from_dict(wire).to_dict() == wire


def test_cancelled_goal_action_is_terminal_and_roundtrips():
    row = action_row(status="cancelled")
    assert PendingAction.from_row(row).to_dict()["status"] == "cancelled"


def test_partial_or_coerced_goal_lineage_is_refused():
    for changed in [{"goal_id": None}, {"goal_depth": True}, {"goal_exposed": 1}]:
        with pytest.raises(ValueError):
            PendingAction.from_row(action_row(**changed))


class DeliveryStore:
    """Execute the real lifecycle SQL against state, not a PostgreSQL claim."""

    def __init__(self, row, *, refuse_event=False):
        self.row = copy.deepcopy(row)
        self.intent_id = uuid4()
        self.cohort_id = uuid4()
        self.presentations = [
            {"intent_id": self.intent_id, "state": "handoff_started"},
            {"cohort_id": self.cohort_id, "state": "ready"},
            {"intent_id": self.intent_id, "state": "delivered"},
        ]
        self.eligible = True
        self.events = []
        self.refuse_event = refuse_event

    @asynccontextmanager
    async def transaction(self):
        before = copy.deepcopy((self.row, self.presentations, self.eligible, self.events))
        try:
            yield self
        except BaseException:
            self.row, self.presentations, self.eligible, self.events = before
            raise

    async def fetchval(self, sql, *args):
        if sql == "SELECT clock_timestamp()":
            return datetime.now(UTC)
        if "to_regclass('approval_delivery_intents')" in sql:
            return True
        if "FROM approval_delivery_cohorts" in sql:
            assert args == (self.cohort_id,)
            return self.cohort_id
        if "SELECT EXISTS" in sql:
            return self.eligible and self.row["status"] == "pending"
        raise AssertionError("unsupported_delivery_scalar")

    async def fetchrow(self, sql, *args):
        if "SELECT * FROM pending_actions" in sql:
            assert args == (self.row["id"],)
            return copy.deepcopy(self.row)
        if "UPDATE pending_actions SET status" in sql:
            assert args[3] == self.row["id"]
            if self.row["status"] != "pending":
                return None
            self.row.update(status=args[0], decided_by=args[1], decided_at=args[2])
            return copy.deepcopy(self.row)
        if "FROM approval_delivery_intents" in sql:
            return {"id": self.intent_id, "action_key": "controlled", "admission_mode": "cohort"}
        if "FROM approval_delivery_cohort_members" in sql:
            return {"cohort_id": self.cohort_id, "eligible": self.eligible}
        if "AS ambiguous_count" in sql:
            return {"ambiguous_count": 0, "delivered_count": 1, "attempt_count": 1}
        raise AssertionError("unsupported_delivery_row")

    async def execute(self, sql, *args):
        if "UPDATE approval_delivery_presentations" in sql:
            cohort = "WHERE cohort_id" in sql
            key = "cohort_id" if cohort else "intent_id"
            allowed = args[1] if cohort else {"ready", "claimed", "handoff_started", "retry_wait"}
            changed = 0
            for item in self.presentations:
                if item.get(key) == args[0] and item["state"] in allowed:
                    item.update(state="cancelled", reason="cohort_empty" if cohort else args[1])
                    changed += 1
            return f"UPDATE {changed}"
        if "UPDATE approval_delivery_cohort_members" in sql:
            assert args == (self.cohort_id, self.intent_id)
            self.eligible = False
            return "UPDATE 1"
        if "INSERT INTO approval_events" in sql:
            if self.refuse_event:
                raise ValueError("controlled_unmigrated_event_constraint")
            self.events.append(copy.deepcopy(args))
            return "INSERT 0 1"
        raise AssertionError("unsupported_delivery_write")


class ApprovalStore(DeliveryStore):
    """Store state produced by real approval/tracker SQL, without claiming PG."""

    def __init__(self, row, *, refuse_digest=False, refuse_event=False):
        super().__init__(row, refuse_event=refuse_event)
        self.refuse_digest = refuse_digest
        self.history = []
        self.suggestions = []
        self.rules = []

    async def fetchrow(self, sql, *args):
        if "UPDATE pending_actions SET status" in sql and "goal_approved_args_digest" in sql:
            if self.refuse_digest:
                raise ValueError("controlled_unmigrated_digest_column")
            assert args[4] == self.row["id"]
            if self.row["status"] != "pending":
                return None
            self.row.update(
                status=args[0],
                decided_by=args[1],
                decided_at=args[2],
                goal_approved_args_digest=args[3],
            )
            return copy.deepcopy(self.row)
        if "FROM autonomy_approval_history" in sql:
            return {"cnt": sum(h[1] == args[0] and h[7] == args[1] for h in self.history)}
        if "FROM approval_rules" in sql or "FROM autonomy_suggestions" in sql:
            return None
        return await super().fetchrow(sql, *args)

    async def execute(self, sql, *args):
        if "INSERT INTO autonomy_approval_history" in sql:
            self.history.append(copy.deepcopy(args))
            return "INSERT 0 1"
        if "INSERT INTO autonomy_suggestions" in sql:
            self.suggestions.append(copy.deepcopy(args))
            return "INSERT 0 1"
        if "INSERT INTO approval_rules" in sql:
            self.rules.append(copy.deepcopy(args))
            return "INSERT 0 1"
        return await super().execute(sql, *args)


async def approve(store):
    return await transition_pending_action(
        store,
        action_id=store.row["id"],
        target_status=ActionStatus.APPROVED,
        decided_by="human:controlled-owner",
        event_actor="user:manual",
        event_reason="controlled approval",
    )


@pytest.mark.asyncio
async def test_locked_approval_binds_edited_stored_args_and_rolls_back_digest_or_event_refusal():
    original = action_row(tool_name="send")
    edited = original | {"tool_args": {"message": "owner-edited", "recipient_id": "fixed"}}
    store = ApprovalStore(edited)
    result = await approve(store)
    assert result.changed and result.action.status is ActionStatus.APPROVED
    assert result.action.goal_args_digest == original["goal_args_digest"]
    assert result.action.goal_approved_args_digest == arguments_digest(edited["tool_args"])
    assert stored_goal_call(store.row).args == edited["tool_args"]
    assert [p["state"] for p in store.presentations] == ["cancelled", "cancelled", "delivered"]
    assert not store.eligible
    for options, error in [
        ({"refuse_digest": True}, "controlled_unmigrated_digest_column"),
        ({"refuse_event": True}, "controlled_unmigrated_event_constraint"),
    ]:
        refused = ApprovalStore(edited, **options)
        before = copy.deepcopy((refused.row, refused.presentations, refused.eligible))
        with pytest.raises(ValueError, match=error):
            await approve(refused)
        assert (refused.row, refused.presentations, refused.eligible) == before
        assert refused.events == []
    stale = ApprovalStore(edited | {"goal_approved_args_digest": arguments_digest({})})
    with pytest.raises(ValueError, match="pending_goal_approval_digest_present"):
        await approve(stale)
    malformed = ApprovalStore(edited | {"tool_args": {"amount": float("nan")}})
    with pytest.raises(ValueError):
        await approve(malformed)
    assert malformed.row["status"] == "pending" and malformed.events == []


@pytest.mark.asyncio
async def test_stored_exposure_controls_history_and_promotion_not_caller_projection():
    row = action_row(
        status="approved",
        decided_at=datetime.now(UTC),
        goal_approved_args_digest=arguments_digest({"message": "controlled"}),
    )
    exposed = ApprovalStore(row)
    forged = PendingAction.from_row(row | {"goal_exposed": False})
    await record_approval(exposed, forged)
    await check_promotion_threshold(
        exposed,
        compute_fingerprint(row["tool_name"], row["tool_args"]),
        row["tool_name"],
        row["tool_args"],
        SimpleNamespace(promotion_threshold=0),
        action_id=row["id"],
    )
    assert exposed.history == exposed.suggestions == []
    for changes in [
        {"goal_exposed": False},
        {k: None for k in PendingAction.from_row(row).goal_lineage().columns()},
    ]:
        healthy = ApprovalStore(row | changes)
        # Supply different caller arguments: history must use the actual stored row.
        caller = PendingAction.from_row(healthy.row | {"tool_args": {"message": "forged"}})
        await record_approval(healthy, caller)
        assert len(healthy.history) == 1 and healthy.history[0][3] == row["tool_args"]
        await check_promotion_threshold(
            healthy,
            compute_fingerprint(row["tool_name"], row["tool_args"]),
            row["tool_name"],
            {"message": "forged"},
            SimpleNamespace(promotion_threshold=1),
            action_id=row["id"],
        )
        assert len(healthy.suggestions) == 1
        assert healthy.suggestions[0][5] == row["tool_args"]
    partial = ApprovalStore(row | {"goal_id": None})
    with pytest.raises(ValueError):
        await record_approval(partial, forged)
    with pytest.raises(ValueError):
        await check_promotion_threshold(
            partial,
            "controlled",
            row["tool_name"],
            {},
            SimpleNamespace(promotion_threshold=0),
            action_id=row["id"],
        )
    assert partial.history == partial.suggestions == []


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["rest-operation", "module"])
@pytest.mark.parametrize("create_rule", [True, False])
async def test_explicit_owner_rule_preserves_exposed_promotion_exclusion(entry, create_rule):
    store = ApprovalStore(action_row(tool_name="send"))
    module = ApprovalsModule()
    module._db = store

    async def decide():
        if entry == "rest-operation":
            # The operation is behind the HTTP owner boundary; actor_id is attribution.
            return await approve_action(
                store, str(store.row["id"]), actor_id="controlled-owner", create_rule=create_rule
            )
        return await module._approve_action(
            str(store.row["id"]),
            create_rule=create_rule,
            actor={"type": "human", "id": "controlled-owner", "authenticated": True},
        )

    result = await decide()
    assert result["status"] == "approved"
    assert result["goal_approved_args_digest"] == arguments_digest(store.row["tool_args"])
    assert "created_rule_error" not in result
    assert store.history == store.suggestions == []
    if create_rule:
        # These are writes by the actual rule producer and audit helper, not a rule stub.
        assert len(store.rules) == 1
        rule = store.rules[0]
        assert result["created_rule"]["id"] == str(rule[0])
        assert rule[1] == store.row["tool_name"] and rule[4] == store.row["id"]
        assert rule[7] is True
        if entry == "rest-operation":
            assert rule[2] == {"message": {"type": "any"}}
        else:
            assert rule[2] == store.row["tool_args"]
        created_events = [e for e in store.events if e[0] == "rule_created"]
        assert len(created_events) == 1
        assert created_events[0][1:3] == (store.row["id"], rule[0])
        assert created_events[0][5] == {"tool_name": store.row["tool_name"]}
    else:
        assert "created_rule" not in result and store.rules == []
        assert all(e[0] != "rule_created" for e in store.events)
    settled = copy.deepcopy((store.row, store.events, store.rules))
    assert "error" in await decide()
    assert (store.row, store.events, store.rules) == settled


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actor",
    [
        None,
        {"type": "agent", "id": "controlled-owner", "authenticated": True},
        {"type": "human", "id": "controlled-owner", "authenticated": False},
        {"type": "human", "authenticated": True},
    ],
)
async def test_explicit_rule_requires_actual_authenticated_human_before_writes(actor):
    store = ApprovalStore(action_row())
    module = ApprovalsModule()
    module._db = store
    before = copy.deepcopy(store.__dict__)
    result = await module._approve_action(str(store.row["id"]), create_rule=True, actor=actor)
    assert result["error_code"] == "human_actor_required"
    assert store.__dict__ == before


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [{"message": "controlled"}, {}])
async def test_explicit_exposed_rule_keeps_actual_high_risk_constraints(arguments):
    store = ApprovalStore(
        action_row(tool_args=arguments, goal_args_digest=arguments_digest(arguments))
    )
    module = ApprovalsModule()
    module._db = store
    module.set_approval_policy(
        ApprovalConfig(enabled=True, default_risk_tier=ApprovalRiskTier.HIGH)
    )
    result = await module._approve_action(
        str(store.row["id"]),
        create_rule=True,
        actor={"type": "human", "id": "controlled-owner", "authenticated": True},
    )
    assert result["status"] == "approved"
    assert store.history == store.suggestions == []
    if arguments:
        assert len(store.rules) == 1 and store.rules[0][6] == 1
        assert result["created_rule"]["id"] == str(store.rules[0][0])
    else:
        assert "requires at least one exact or pattern" in result["created_rule_error"]
        assert store.rules == []
        assert all(e[0] != "rule_created" for e in store.events)


async def cancel(store, **changes):
    identity = {
        "action_id": store.row["id"],
        "expected_goal_id": store.row["goal_id"],
        "expected_effect_id": store.row["goal_effect_id"],
    }
    return await cancel_goal_pending_action(store, **(identity | changes))


@pytest.mark.asyncio
async def test_actual_stop_transition_fences_delivery_and_preserves_expired_stop_reason():
    store = DeliveryStore(action_row(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    before = copy.deepcopy((store.row, store.presentations, store.eligible))
    for changes in [{"expected_goal_id": uuid4()}, {"expected_effect_id": uuid4()}]:
        assert not (await cancel(store, **changes)).changed
        assert (store.row, store.presentations, store.eligible) == before
    result = await cancel(store)
    assert result.changed and not result.expired_instead
    assert result.action.status.value == store.row["status"] == "cancelled"
    assert [p["state"] for p in store.presentations] == ["cancelled", "cancelled", "delivered"]
    assert not store.eligible
    assert [event[0] for event in store.events] == [
        "action_cancelled",
        "approval_delivery_terminal",
    ]
    assert store.events[0][4] == "goal_stopped"
    assert store.events[1][5]["cancelled_count"] == 2
    assert store.events[1][5]["delivered_count"] == 1
    assert not (await cancel(store)).changed
    assert len(store.events) == 2
    for status in ("approved", "executed"):
        finished = DeliveryStore(action_row(status=status))
        assert not (await cancel(finished)).changed
        assert finished.row["status"] == status and finished.eligible
        assert finished.events == []


@pytest.mark.asyncio
async def test_event_refusal_rolls_back_action_delivery_and_membership_together():
    store = DeliveryStore(action_row(), refuse_event=True)
    before = copy.deepcopy((store.row, store.presentations, store.eligible))
    with pytest.raises(ValueError, match="controlled_unmigrated_event_constraint"):
        await cancel(store)
    assert (store.row, store.presentations, store.eligible) == before
    assert store.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["module", "executor"])
async def test_goal_approval_never_falls_through_ordinary_effect_executor(entry):
    """An owner decision is not central stopped-goal/dispatch authorization."""
    from butlers.modules.approvals.executor import execute_approved_action

    effect = uuid4()
    row = action_row(
        id=effect,
        goal_effect_id=effect,
        status="approved",
        goal_approved_args_digest=arguments_digest({"message": "controlled"}),
        decided_by="controlled-owner",
    )

    class DispatchStore(DeliveryStore):
        async def fetchrow(self, sql, *args):
            if "FROM pending_actions WHERE id = $1" in sql:
                assert args == (effect,)
                return copy.deepcopy(self.row)
            return await super().fetchrow(sql, *args)

        async def execute(self, sql, *args):
            if sql.startswith("UPDATE pending_actions SET status"):
                self.row.update(status=args[0], execution_result=args[1])
                return "UPDATE 1"
            return await super().execute(sql, *args)

    store = DispatchStore(row)
    calls = []

    async def tool(**arguments):
        calls.append(arguments)
        return {"sent": True}

    before = copy.deepcopy(store.row)
    if entry == "module":
        module = ApprovalsModule()
        module._db = store

        async def dispatch(name, arguments):
            assert name == row["tool_name"]
            return await tool(**arguments)

        module.set_tool_executor(dispatch)
        outcome = await module._dispatch_approved_action_by_id(str(effect))
        error = outcome.get("error", "")
    else:
        outcome = await execute_approved_action(
            store, effect, row["tool_name"], row["tool_args"], tool
        )
        error = outcome.error or ""
    assert calls == [], "Goal approval bypassed central Stop and dispatch authorization"
    assert "goal" in error.lower() and "replay" in error.lower()
    assert store.row == before and store.events == []
