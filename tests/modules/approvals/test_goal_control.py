"""REQ-capability-authorization-009: actual owning producer fences late PARK.

Stateful SQL controls prove software ordering/rollback only, never PostgreSQL,
RLS, authenticated ingress or a committed external effect.
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from butlers.core.capability_json import arguments_digest
from butlers.modules.approvals.goal_calls import park_goal_call
from butlers.modules.approvals.models import GoalCallLineage

pytestmark = pytest.mark.unit


class FencedStore:
    def __init__(self):
        self.action_id = uuid4()
        self.goal_id = uuid4()
        self.arguments = {"message": "controlled"}
        self.digest = arguments_digest(self.arguments)
        self.writes = []

    @asynccontextmanager
    async def transaction(self):
        yield self

    async def fetchrow(self, sql, *args):
        if "FROM approval_delivery_rollout" in sql:
            return {"admission_enabled": False, "worker_enabled": False}
        if "FROM pending_actions" in sql:
            return None
        if "FROM approval_goal_effects" in sql:
            return {
                "goal_id": self.goal_id,
                "effect_id": self.action_id,
                "action_id": self.action_id,
                "owning_domain": "mail",
                "capability": "mail__send",
                "args_digest": self.digest,
                "stop_fenced": True,
                "phase": "absent",
                "state_version": 1,
                "pending_action_id": None,
                "dispatch_intent_id": None,
            }
        if "current_schema()" in sql and "current_user" in sql:
            return {
                "schema": "mail",
                "role": "butler_mail_rw",
                "unsafe": False,
                "schemas": ["pg_catalog", "mail", "public"],
            }
        raise AssertionError("unsupported_control_query")

    async def fetchval(self, sql, *args):
        if any(x in sql for x in ("FROM pg_class", "FROM pg_proc", "pg_trigger")):
            return True
        if "SELECT id FROM pending_actions" in sql:
            return None
        raise AssertionError("unsupported_control_scalar")

    async def execute(self, sql, *args):
        if "INSERT INTO pending_actions" in sql:
            self.writes.append(args[0])
        return "INSERT 0 1"


async def test_existing_stop_tombstone_prevents_actual_goal_park():
    store = FencedStore()
    lineage = GoalCallLineage(
        store.goal_id,
        uuid4(),
        0,
        False,
        "owner",
        store.action_id,
        "mail__send",
        store.digest,
    )
    with pytest.raises(ValueError, match="goal_stop_fenced"):
        await park_goal_call(
            store,
            action_id=store.action_id,
            tool_name="send",
            raw_args=store.arguments,
            lineage=lineage,
            origin_butler="mail",
            requested_at=datetime.now(UTC),
            expires_at=None,
        )
    assert store.writes == []


class DurableStore(FencedStore):
    """Interpret the producer's SQL against rollback-capable state (not PG)."""

    def __init__(self):
        super().__init__()
        self.gate = None
        self.row = None
        self.receipts = {}
        self.events = []
        self.refuse_receipt = False
        self.lose_commit_reply = False
        self.order = []

    @asynccontextmanager
    async def transaction(self):
        import copy

        before = copy.deepcopy((self.gate, self.row, self.receipts, self.events))
        try:
            yield self
        except BaseException:
            self.gate, self.row, self.receipts, self.events = before
            raise
        if self.lose_commit_reply:
            self.lose_commit_reply = False
            raise ConnectionError("controlled_commit_response_loss")

    async def fetchval(self, sql, *args):
        if "pg_class" in sql or "pg_proc" in sql or "pg_trigger" in sql:
            return True
        if "SELECT clock_timestamp()" in sql:
            return datetime.now(UTC)
        if "SELECT id FROM pending_actions" in sql:
            self.order.append("action")
            return self.row["id"] if self.row is not None else None
        if "to_regclass('approval_delivery_intents')" in sql:
            return False
        return await super().fetchval(sql, *args)

    async def fetchrow(self, sql, *args):
        import copy

        if "FROM approval_goal_effects" in sql:
            self.order.append("gate")
            return copy.deepcopy(self.gate)
        if "FROM approval_goal_receipts" in sql:
            return copy.deepcopy(self.receipts.get(tuple(args)))
        if "FROM pending_actions" in sql:
            self.order.append("action")
            if self.row is None:
                return None
            if "SELECT id\n" in sql:
                return {"id": self.row["id"]}
            return copy.deepcopy(self.row)
        if "UPDATE pending_actions SET status = $1" in sql:
            self.row.update(status=args[0], decided_by=args[1], decided_at=args[2])
            if "goal_approved_args_digest = $4" in sql:
                self.row["goal_approved_args_digest"] = args[3]
            return copy.deepcopy(self.row)
        if "UPDATE approval_goal_effects" in sql:
            self.gate.update(phase=args[1], state_version=self.gate["state_version"] + 1)
            if "stop_fenced=true" in sql:
                self.gate["stop_fenced"] = True
            else:
                self.gate["approved_args_digest"] = args[2]
            return copy.deepcopy(self.gate)
        if "FROM approval_delivery_rollout" in sql:
            self.order.append("rollout")
        return await super().fetchrow(sql, *args)

    async def execute(self, sql, *args):
        from butlers.modules.approvals.models import GOAL_LINEAGE_COLUMNS

        if "pg_advisory_xact_lock" in sql:
            self.order.append("serializer")
        elif "INSERT INTO approval_goal_effects" in sql:
            self.gate = dict(
                zip(
                    (
                        "effect_id",
                        "action_id",
                        "goal_id",
                        "owning_domain",
                        "capability",
                        "args_digest",
                        "phase",
                        "pending_action_id",
                        "stop_fenced",
                    ),
                    args,
                    strict=True,
                )
            ) | {"state_version": 1, "dispatch_intent_id": None, "approved_args_digest": None}
        elif "INSERT INTO pending_actions" in sql:
            self.row = dict(
                zip(
                    (
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
                        *GOAL_LINEAGE_COLUMNS,
                    ),
                    args,
                    strict=True,
                )
            ) | {"status": "pending"}
        elif "INSERT INTO approval_goal_receipts" in sql:
            if self.refuse_receipt:
                raise ValueError("controlled_receipt_refusal")
            row = dict(
                zip(
                    (
                        "receipt_id",
                        "effect_id",
                        "state_version",
                        "goal_id",
                        "owning_domain",
                        "action_id",
                        "capability",
                        "args_digest",
                        "outcome",
                        "pending_action_id",
                        "digest",
                    ),
                    args,
                    strict=True,
                )
            )
            key = (row["effect_id"], row["state_version"], row["outcome"])
            assert key not in self.receipts
            self.receipts[key] = row
        elif "UPDATE pending_actions" in sql:
            self.row["status"] = "cancelled"
        elif "INSERT INTO approval_events" in sql:
            self.events.append(args)
        return "INSERT 0 1"


def request_for(store):
    from butlers.modules.approvals.park import ParkRequest

    lineage = GoalCallLineage(
        store.goal_id, uuid4(), 0, True, "owner", store.action_id, "mail__send", store.digest
    )
    return ParkRequest(
        store.action_id,
        "send",
        store.arguments,
        None,
        datetime.now(UTC),
        None,
        origin_butler="mail",
        goal_lineage=lineage,
    )


def identity_for(store):
    from butlers.core.goal_stop import CancelIdentity

    return CancelIdentity(
        store.goal_id, store.action_id, "mail", store.action_id, "mail__send", store.digest
    )


async def test_actual_park_aliases_are_stable_and_lookup_never_creates_absence():
    from butlers.modules.approvals.goal_control import OwningGoalApprovals

    store = DurableStore()
    owner = OwningGoalApprovals(store, "mail")
    assert await owner.lookup(identity_for(store)) is None
    assert store.gate is None and store.receipts == {}
    request = request_for(store)
    admission, receipt = await owner.park(request)
    again, replay = await owner.park(request)
    lookup = await owner.lookup(identity_for(store))
    assert admission.action_id == store.action_id and not admission.duplicate
    assert again.duplicate and replay == receipt
    assert lookup["outcome"] == "pending" and receipt["outcome"] == "parked"
    assert lookup["receipt_id"] != receipt["receipt_id"]
    assert len(store.receipts) == 2 and store.gate["state_version"] == 1
    assert store.row["tool_args"] == request.tool_args
    assert (
        store.order.index("rollout") < store.order.index("serializer") < store.order.index("gate")
    )


@pytest.mark.parametrize("historical_status", ["pending", "approved", "rejected", "abandoned"])
async def test_stop_fences_once_preserving_negative_history_and_receipt_history(historical_status):
    from butlers.modules.approvals.goal_control import OwningGoalApprovals

    store = DurableStore()
    owner = OwningGoalApprovals(store, "mail")
    _, parked = await owner.park(request_for(store))
    store.row["status"] = historical_status
    if historical_status == "approved":
        store.row["goal_approved_args_digest"] = store.digest
        store.gate.update(phase="approved", approved_args_digest=store.digest)
    receipt = await owner.cancel(identity_for(store))
    repeated = await owner.cancel(identity_for(store))
    assert repeated == receipt == await owner.lookup(identity_for(store))
    assert receipt["outcome"] == "cancelled" and store.gate["stop_fenced"] is True
    assert store.row["status"] == (
        historical_status if historical_status in {"rejected", "abandoned"} else "cancelled"
    )
    assert len(store.events) == 1 and store.gate["state_version"] == 2
    assert store.receipts[(store.action_id, 1, "parked")]["digest"] == parked["digest"]
    with pytest.raises(ValueError, match="goal_stop_fenced"):
        await owner.park(request_for(store))


async def test_lost_cancel_reply_recovers_only_committed_receipt_and_never_cancels_witness():
    from butlers.modules.approvals.goal_control import OwningGoalApprovals

    store = DurableStore()
    owner = OwningGoalApprovals(store, "mail")
    store.lose_commit_reply = True
    with pytest.raises(ConnectionError, match="controlled_commit_response_loss"):
        await owner.cancel(identity_for(store))
    recovered = await owner.lookup(identity_for(store))
    assert recovered["outcome"] == "fenced_absent" and store.row is None
    assert recovered == await owner.cancel(identity_for(store))
    other = DurableStore()
    other_owner = OwningGoalApprovals(other, "mail")
    await other_owner.park(request_for(other))
    other.gate.update(phase="dispatch_intent", dispatch_intent_id=uuid4())
    before = (dict(other.gate), dict(other.row), dict(other.receipts))
    assert await other_owner.cancel(identity_for(other)) is None
    assert await other_owner.lookup(identity_for(other)) is None
    assert (other.gate, other.row, other.receipts) == before and other.events == []


async def test_receipt_failure_rolls_back_actual_park_and_mismatched_identity_never_rebinds():
    from dataclasses import replace

    from butlers.modules.approvals.goal_control import OwningGoalApprovals

    store = DurableStore()
    owner = OwningGoalApprovals(store, "mail")
    store.refuse_receipt = True
    with pytest.raises(ValueError, match="controlled_receipt_refusal"):
        await owner.park(request_for(store))
    assert store.row is None and store.gate is None and store.receipts == {}
    store.refuse_receipt = False
    await owner.park(request_for(store))
    before = (dict(store.gate), dict(store.row), dict(store.receipts))
    with pytest.raises(ValueError, match="goal_identity_mismatch"):
        await owner.cancel(replace(identity_for(store), goal_id=uuid4()))
    assert (store.gate, store.row, store.receipts) == before


@pytest.mark.parametrize(
    "schemas",
    [
        ["pg_temp_7", "pg_catalog", "mail", "public"],
        ["mail", "public", "pg_catalog"],
    ],
)
async def test_actual_park_refuses_shadowable_owning_search_path(schemas):
    from butlers.modules.approvals.goal_control import (
        GoalApprovalsUnavailable,
        OwningGoalApprovals,
    )

    class ShadowableStore(DurableStore):
        async def fetchrow(self, sql, *args):
            row = await super().fetchrow(sql, *args)
            if "current_user" in sql and "current_schema()" in sql:
                return row | {"schemas": schemas}
            return row

    store = ShadowableStore()
    with pytest.raises(GoalApprovalsUnavailable, match="owning_goal_role_unavailable"):
        await OwningGoalApprovals(store, "mail").park(request_for(store))
    assert store.row is None and store.gate is None and store.receipts == {}


@pytest.mark.parametrize("decision", ["approved", "rejected", "expired"])
async def test_real_decision_core_versions_actual_stored_arguments_and_preserves_history(decision):
    from butlers.modules.approvals.goal_control import OwningGoalApprovals
    from butlers.modules.approvals.models import ActionStatus

    store = DurableStore()
    owner = OwningGoalApprovals(store, "mail")
    _, parked = await owner.park(request_for(store))
    # The already-authenticated owner operation persists edits under its lock;
    # this internal decision core must bind the actual stored arguments.
    store.row["tool_args"] = {"message": "owner edited"}
    result = await owner.transition(
        identity_for(store),
        target_status=ActionStatus(decision),
        decided_by="owner",
        event_actor="owner",
        event_reason="controlled",
    )
    assert result.changed and result.action.status.value == decision
    assert store.gate["state_version"] == 2 and not store.gate["stop_fenced"]
    assert store.receipts[(store.action_id, 1, "parked")]["digest"] == parked["digest"]
    if decision == "approved":
        approved = await owner.lookup(identity_for(store))
        assert approved["outcome"] == "approved"
        assert store.gate["approved_args_digest"] == arguments_digest(store.row["tool_args"])
        assert store.row["goal_args_digest"] == store.digest
    else:
        assert await owner.lookup(identity_for(store)) is None
        assert store.gate["approved_args_digest"] is None
    cancelled = await owner.cancel(identity_for(store))
    assert cancelled["outcome"] == "cancelled"
    assert store.row["status"] == ("cancelled" if decision == "approved" else decision)


class RuleStore(DurableStore):
    """Real owning consumer and rule matcher with rollback-capable SQL state, not PG."""

    def __init__(self):
        super().__init__()
        self.arguments = {"message": "controlled", "recipient": "pinned-target"}
        self.digest = arguments_digest(self.arguments)
        self.rule = {
            "id": uuid4(),
            "tool_name": "send",
            "arg_constraints": {
                "message": {"type": "exact", "value": "controlled"},
                "recipient": {"type": "exact", "value": "pinned-target"},
            },
            "description": "owner rule",
            "created_at": datetime.now(UTC),
            "created_from": None,
            "expires_at": None,
            "max_uses": 1,
            "use_count": 0,
            "active": True,
        }
        self.rule_reads = 0
        self.refuse_approved_receipt = False

    @asynccontextmanager
    async def transaction(self):
        import copy

        before = copy.deepcopy((self.gate, self.row, self.receipts, self.events, self.rule))
        try:
            yield self
        except BaseException:
            self.gate, self.row, self.receipts, self.events, self.rule = before
            raise
        if self.lose_commit_reply:
            self.lose_commit_reply = False
            raise ConnectionError("controlled_commit_response_loss")

    async def fetch(self, sql, *args):
        assert "FROM approval_rules" in sql and "FOR UPDATE" in sql
        self.rule_reads += 1
        self.order.append("rules")
        return [dict(self.rule)] if self.rule["active"] else []

    async def fetchrow(self, sql, *args):
        if "FROM pending_actions AS pa" in sql and self.row is not None:
            return {
                "action_id": self.row["id"],
                "intent_id": None,
                "action_key": None,
                "admission_mode": None,
                "presentation_key": None,
                "cohort_key": None,
                "not_before": None,
            }
        if "UPDATE approval_rules SET use_count" in sql:
            if (
                self.rule["id"] != args[0]
                or not self.rule["active"]
                or (
                    self.rule["max_uses"] is not None
                    and self.rule["use_count"] >= self.rule["max_uses"]
                )
            ):
                return None
            self.rule["use_count"] += 1
            return {"id": self.rule["id"]}
        return await super().fetchrow(sql, *args)

    async def execute(self, sql, *args):
        if "UPDATE pending_actions SET approval_rule_id" in sql:
            self.row["approval_rule_id"] = args[1]
            return "UPDATE 1"
        if "INSERT INTO approval_goal_receipts" in sql and args[8] == "approved":
            if self.refuse_approved_receipt:
                raise ValueError("controlled_approved_receipt_refusal")
        return await super().execute(sql, *args)


async def registered_send_catalog(*, mode="default", domain="mail"):
    from fastmcp import FastMCP

    from butlers.core.tool_catalog import RecipientParameters, build_tool_catalog
    from butlers.modules.base import ToolMeta

    mcp = FastMCP("owning-rule-consumer")

    @mcp.tool()
    async def send(message: str, recipient: str):
        return None

    catalog = await build_tool_catalog(
        mcp,
        tool_owners={"send": "native"},
        tool_metadata={
            "send": ToolMeta(
                canonical_name="send",
                module_name="native",
                group_name="write",
                namespace="native.send",
                llm_presentable=True,
                load_posture="deferred",
                owner_domain=domain,
                effect_class="communicate",
                output_trust="untrusted",
                output_sensitivity="ordinary",
                resource_params=(),
                selection_bound={},
                safety_args=("recipient",),
                approval_policy={"mode": mode, "enabled": True, "configured_gate": True},
                idempotency="key_required",
                owner_authority=False,
                recipient_params=RecipientParameters(("recipient",)),
            )
        },
    )
    return catalog


async def test_registered_goal_park_claims_rule_and_commits_approved_receipt_once():
    from dataclasses import replace

    from butlers.core.goal_admission import Grant
    from butlers.modules.approvals.goal_control import OwningGoalApprovals

    store = RuleStore()
    call = request_for(store)
    line = replace(call.goal_lineage, goal_exposed=False)
    catalog = await registered_send_catalog()
    admission = await park_goal_call(
        store,
        action_id=call.action_id,
        tool_name=call.tool_name,
        raw_args=call.tool_args,
        lineage=line,
        origin_butler="mail",
        requested_at=call.requested_at,
        expires_at=None,
        owning_catalog=catalog,
        grant=Grant("mail__send"),
    )
    assert admission.action_id == call.action_id
    assert store.row["status"] == "approved"
    assert store.row["approval_rule_id"] == store.rule["id"]
    assert store.row["goal_approved_args_digest"] == store.digest
    assert store.rule["use_count"] == 1 and store.gate["phase"] == "approved"
    assert {r["outcome"] for r in store.receipts.values()} == {"pending", "parked", "approved"}
    owner = OwningGoalApprovals(store, "mail", catalog=catalog)
    again, parked = await owner.park(replace(call, goal_lineage=line), grant=Grant("mail__send"))
    assert again.duplicate and parked["outcome"] == "parked"
    assert (await owner.lookup(identity_for(store)))["outcome"] == "approved"
    assert store.rule["use_count"] == 1 and len(store.receipts) == 3
    assert store.order.index("gate") < store.order.index("rules")
    assert any(e[0] == "action_auto_approved" and e[2] == store.rule["id"] for e in store.events)


@pytest.mark.parametrize(
    "restriction",
    [
        "missing_catalog",
        "missing_grant",
        "park_only",
        "always_per_event",
        "third_party",
        "exposed_free_text",
        "unbounded_safety",
        "exhausted",
        "revoked",
    ],
)
async def test_registered_goal_rule_cannot_remove_independent_approval_restrictions(restriction):
    from dataclasses import replace

    from butlers.core.goal_admission import Grant
    from butlers.modules.approvals.goal_control import OwningGoalApprovals

    store = RuleStore()
    call = request_for(store)
    call = replace(call, goal_lineage=replace(call.goal_lineage, goal_exposed=False))
    catalog = await registered_send_catalog(
        mode="always_per_event" if restriction == "always_per_event" else "default"
    )
    grant = Grant("mail__send", park_only=restriction == "park_only")
    if restriction == "missing_catalog":
        catalog = None
    elif restriction == "missing_grant":
        grant = None
    elif restriction == "third_party":
        call = replace(call, goal_lineage=replace(call.goal_lineage, goal_origin="third_party"))
    elif restriction == "exposed_free_text":
        call = replace(call, goal_lineage=replace(call.goal_lineage, goal_exposed=True))
        store.rule["arg_constraints"]["message"] = {"type": "pattern", "value": "*"}
    elif restriction == "unbounded_safety":
        del store.rule["arg_constraints"]["recipient"]
    elif restriction == "exhausted":
        store.rule["use_count"] = 1
    elif restriction == "revoked":
        store.rule["active"] = False
    before_uses = store.rule["use_count"]
    _, parked = await OwningGoalApprovals(store, "mail", catalog=catalog).park(call, grant=grant)
    assert store.row["status"] == "pending" and store.gate["phase"] == "pending"
    assert store.rule["use_count"] == before_uses
    assert parked["outcome"] == "parked" and len(store.receipts) == 2
    assert not any(e[0] == "action_auto_approved" for e in store.events)


async def test_exposed_exact_pins_approve_but_receipt_failure_rolls_back_rule_and_action():
    from butlers.core.goal_admission import Grant
    from butlers.modules.approvals.goal_control import OwningGoalApprovals

    store = RuleStore()
    owner = OwningGoalApprovals(store, "mail", catalog=await registered_send_catalog())
    store.refuse_approved_receipt = True
    with pytest.raises(ValueError, match="controlled_approved_receipt_refusal"):
        await owner.park(request_for(store), grant=Grant("mail__send"))
    assert store.row is None and store.gate is None and store.receipts == {}
    assert store.rule["use_count"] == 0 and store.events == []
    store.refuse_approved_receipt = False
    await owner.park(request_for(store), grant=Grant("mail__send"))
    assert store.row["status"] == "approved" and store.row["goal_exposed"] is True
    assert store.rule["use_count"] == 1
    receipt = await owner.cancel(identity_for(store))
    assert receipt["outcome"] == "cancelled" and store.row["status"] == "cancelled"
    assert store.rule["use_count"] == 1  # An issued approval is not refunded by Stop.
    with pytest.raises(ValueError, match="goal_stop_fenced"):
        await owner.park(request_for(store), grant=Grant("mail__send"))


async def test_lost_rule_approval_commit_reply_recovers_stored_receipt_without_second_claim():
    from butlers.core.goal_admission import Grant
    from butlers.modules.approvals.goal_control import OwningGoalApprovals

    store = RuleStore()
    owner = OwningGoalApprovals(store, "mail", catalog=await registered_send_catalog())
    store.lose_commit_reply = True
    call = request_for(store)
    with pytest.raises(ConnectionError, match="controlled_commit_response_loss"):
        await owner.park(call, grant=Grant("mail__send"))
    assert store.row["status"] == "approved" and store.rule["use_count"] == 1
    approved = await owner.lookup(identity_for(store))
    assert approved["outcome"] == "approved"
    duplicate, _ = await owner.park(call, grant=Grant("mail__send"))
    assert duplicate.duplicate and store.rule["use_count"] == 1
