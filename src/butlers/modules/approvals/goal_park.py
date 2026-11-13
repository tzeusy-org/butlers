"""Park a goal effect for its owning domain (RFC 0038 D13, bu-cadcr1).

The capability broker parks every call that needs approval here, with the
exact tool arguments and the call's goal lineage. The pending action's id is
the broker's effect id, so a repeated park resolves to the same action.

Standing rules are matched at park time only when the broker allows it (never
for park-only grants, ``always_per_event`` tools, administer or risky
actuation), through the approval gate's own admission
(``gate.admit_standing_rule``): owner-permission admission for fact rules and
every safety argument pinned. A tool whose rule approval must freeze its
arguments (``gate.RULE_PREPARED_TOOLS``) always parks here: the freeze would
rewrite the arguments the broker's effect digest commits to.

For an exposed lineage only two kinds of rule apply
(REQ-capability-authorization-006), judged on the rule itself, never on the
call: one whose own recipient is the owner (it exactly pins every recipient
argument the call supplies, and the call's recipient is the owner), or one that
is template-pinned. Template pins are recognised conservatively: the call
supplies at least one argument and the rule pins every one exactly. Typed-slot
templates are not yet recognised and so park.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from butlers.modules.approvals.delivery_lifecycle import transition_pending_action
from butlers.modules.approvals.events import ApprovalEventType, record_approval_event
from butlers.modules.approvals.gate import RULE_PREPARED_TOOLS, admit_standing_rule
from butlers.modules.approvals.models import ActionStatus
from butlers.modules.approvals.park import park_pending_action
from butlers.modules.approvals.rules import claim_rule_use_sql, parse_constraints

_PARK_LIFETIME = timedelta(hours=48)


@dataclass(frozen=True, slots=True)
class GoalLineage:
    goal_id: uuid.UUID
    invocation_id: uuid.UUID
    depth: int
    exposed: bool
    origin: str
    effect_id: uuid.UUID
    # The invocation's S1 exposure cause, read by the broker under the goal lock.
    exposure_cause: str | None = None


@dataclass(frozen=True, slots=True)
class GoalParkOutcome:
    action_id: uuid.UUID
    rule_id: uuid.UUID | None = None


def _exact(constraint: Any) -> bool:
    if isinstance(constraint, dict):
        return str(constraint.get("type", "")).lower() == "exact"
    return constraint != "*"  # A bare legacy value is an exact pin.


def _exposed_eligible(
    rule: dict[str, Any],
    args: dict[str, Any],
    recipient_args: tuple[str, ...],
    recipient_is_owner: bool,
) -> bool:
    """An owner-recipient or template-pinned rule (REQ-capability-authorization-006)."""
    try:
        constraints = parse_constraints(rule.get("arg_constraints", {}))
    except Exception:  # noqa: BLE001 - a malformed rule never approves
        return False
    if not isinstance(constraints, dict):
        return False

    def pinned(names) -> bool:
        return bool(names) and all(_exact(constraints.get(name, "*")) for name in names)

    named = [name for name in recipient_args if name in args]
    return (recipient_is_owner and pinned(named)) or pinned(list(args))


async def _set_lineage(connection, lineage: GoalLineage, rule_possible: bool) -> None:
    await connection.execute(
        "UPDATE pending_actions SET goal_id = $2, goal_invocation_id = $3, goal_depth = $4, "
        "goal_exposed = $5, goal_origin = $6, goal_exposure_cause = $7, "
        "goal_rule_possible = $8 WHERE id = $1",
        lineage.effect_id,
        lineage.goal_id,
        lineage.invocation_id,
        lineage.depth,
        lineage.exposed,
        lineage.origin,
        lineage.exposure_cause if lineage.exposed else None,
        rule_possible,
    )


async def park_goal_effect(
    pool: Any,
    *,
    lineage: GoalLineage,
    tool_name: str,
    tool_args: dict[str, Any],
    allow_standing_rule: bool,
    recipient_is_owner: bool,
    recipient_args: tuple[str, ...] = (),
    safety_args: tuple[str, ...] = (),
    origin_butler: str,
) -> GoalParkOutcome:
    """Park (or rule-approve) one goal effect with its lineage, atomically."""
    now = datetime.now(UTC)
    # The broker's own park-time answer to "may a standing rule ever match this call". An
    # explanation shown to the owner, never authority: admission below still judges the rule.
    rule_possible = allow_standing_rule and tool_name not in RULE_PREPARED_TOOLS
    async with pool.acquire() as connection, connection.transaction():
        existing = await connection.fetchrow(
            "SELECT id, approval_rule_id FROM pending_actions WHERE id = $1", lineage.effect_id
        )
        if existing is not None:
            return GoalParkOutcome(existing["id"], existing["approval_rule_id"])
        if allow_standing_rule and tool_name not in RULE_PREPARED_TOOLS:
            admission = await admit_standing_rule(
                connection,
                tool_name,
                tool_args,
                frozenset(safety_args),
                eligible=(
                    (
                        lambda rule: _exposed_eligible(
                            rule, tool_args, recipient_args, recipient_is_owner
                        )
                    )
                    if lineage.exposed
                    else None
                ),
            )
            rule_id = admission.rule["id"] if admission.rule is not None else None
            if rule_id is not None:
                claimed = await connection.fetchrow(
                    "WITH claimed AS (" + claim_rule_use_sql(rule_param=3, now_param=4) + ") "
                    "INSERT INTO pending_actions (id, tool_name, tool_args, status, requested_at, "
                    "approval_rule_id, decided_by, decided_at, goal_id, goal_invocation_id, "
                    "goal_depth, goal_exposed, goal_origin, goal_exposure_cause, "
                    "goal_rule_possible) "
                    # INSERT ... SELECT does not infer parameter types from the target.
                    "SELECT $1::uuid, $2::text, $5::jsonb, 'approved', $4::timestamptz, "
                    "claimed.id, 'rule:' || claimed.id::text, $4::timestamptz, $6::uuid, "
                    "$7::uuid, $8::smallint, $9::boolean, $10::text, $11::text, $12::boolean "
                    "FROM claimed RETURNING id",
                    lineage.effect_id,
                    tool_name,
                    rule_id,
                    now,
                    tool_args,
                    lineage.goal_id,
                    lineage.invocation_id,
                    lineage.depth,
                    lineage.exposed,
                    lineage.origin,
                    lineage.exposure_cause if lineage.exposed else None,
                    rule_possible,
                )
                if claimed is not None:
                    await record_approval_event(
                        connection,
                        ApprovalEventType.ACTION_AUTO_APPROVED,
                        actor="system:capability_broker",
                        action_id=lineage.effect_id,
                        rule_id=rule_id,
                        reason="standing rule matched at goal park",
                        metadata={"tool_name": tool_name, "goal_id": str(lineage.goal_id)},
                        occurred_at=now,
                    )
                    return GoalParkOutcome(lineage.effect_id, rule_id)
        await park_pending_action(
            connection,
            action_id=lineage.effect_id,
            tool_name=tool_name,
            tool_args=tool_args,
            agent_summary=None,
            requested_at=now,
            expires_at=now + _PARK_LIFETIME,
            origin_butler=origin_butler,
            deduplication_key=f"goal-effect:{lineage.effect_id}",
        )
        await _set_lineage(connection, lineage, rule_possible)
    return GoalParkOutcome(lineage.effect_id)


async def record_goal_outcome(
    pool: Any,
    goal_id: uuid.UUID,
    action_id: uuid.UUID,
    *,
    status: str,
    receipt_ref: str | None,
) -> bool:
    """Write the broker's settled outcome back onto the approved domain action.

    ``committed`` makes the action ``executed``; any other outcome leaves it
    ``approved`` with a non-null ``execution_result``, which already excludes
    it from legacy dispatch recovery and shows it stalled. Only an approved,
    not-yet-recorded goal action is written; anything else is left alone.
    """
    result = {
        "success": status == "committed",
        "status": status,
        "receipt_ref": receipt_ref,
        "source": "capability_broker",
    }
    succeeded = status == "committed"
    async with pool.acquire() as connection, connection.transaction():
        if succeeded:
            row = await connection.fetchrow(
                "UPDATE pending_actions SET status = $1, execution_result = $2, "
                "decided_at = coalesce(decided_at, clock_timestamp()) "
                "WHERE id = $3 AND goal_id = $4 AND status = $5 "
                "AND execution_result IS NULL RETURNING id",
                ActionStatus.EXECUTED.value,
                result,
                action_id,
                goal_id,
                ActionStatus.APPROVED.value,
            )
        else:
            row = await connection.fetchrow(
                "UPDATE pending_actions SET execution_result = $1 "
                "WHERE id = $2 AND goal_id = $3 AND status = 'approved' "
                "AND execution_result IS NULL RETURNING id",
                result,
                action_id,
                goal_id,
            )
        if row is None:
            return False
        await record_approval_event(
            connection,
            ApprovalEventType.ACTION_EXECUTION_SUCCEEDED
            if succeeded
            else ApprovalEventType.ACTION_EXECUTION_FAILED,
            actor="system:capability_broker",
            action_id=action_id,
            reason=f"goal effect {status}",
            metadata={"goal_id": str(goal_id), "status": status},
        )
    return True


@dataclass(frozen=True, slots=True)
class GoalDecision:
    """A goal action's status after an owner decision was mirrored onto it."""

    status: str
    tool_args: dict[str, Any] | None = None


async def decide_goal_effect(
    pool: Any,
    goal_id: uuid.UUID,
    action_id: uuid.UUID,
    *,
    approve: bool,
    decided_by: str,
) -> GoalDecision | None:
    """Mirror the owner's decision of a goal action, recorded first by the broker (D13).

    The broker committed the owner's decision receipt before calling this; the domain row
    follows it through the canonical transition, which also fences the action's owner prompts.
    An action whose window elapsed becomes ``expired`` instead, and a decision already mirrored
    (a re-drive) changes nothing. Returns the action's status afterwards, with its stored
    arguments when it is ``approved`` (the broker dispatches exactly those), or None when the
    goal holds no such action. This is the only path that approves a goal action: the legacy
    approve routes refuse one (``goal_fence``).
    """
    async with pool.acquire() as connection, connection.transaction():
        row = await connection.fetchrow(
            "SELECT status FROM pending_actions WHERE id = $1 AND goal_id = $2 FOR UPDATE",
            action_id,
            goal_id,
        )
        if row is None:
            return None
        if row["status"] == ActionStatus.PENDING.value:
            await transition_pending_action(
                connection,
                action_id=action_id,
                target_status=ActionStatus.APPROVED if approve else ActionStatus.REJECTED,
                decided_by=decided_by,
                event_actor=decided_by,
                event_reason="owner decision of a goal effect",
                event_metadata={"goal_id": str(goal_id)},
                _already_in_transaction=True,
            )
        after = await connection.fetchrow(
            "SELECT status, tool_args, execution_result FROM pending_actions WHERE id = $1",
            action_id,
        )
    status = after["status"]
    if status == ActionStatus.APPROVED.value and after["execution_result"] is None:
        stored = after["tool_args"]
        return GoalDecision(status, json.loads(stored) if isinstance(stored, str) else stored)
    return GoalDecision(status)


async def goal_action_states(
    pool: Any, goal_id: uuid.UUID, action_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """Current status of each named action of the goal (absent ids are omitted)."""
    if not action_ids:
        return {}
    rows = await pool.fetch(
        "SELECT id, status FROM pending_actions WHERE goal_id = $1 AND id = ANY($2::uuid[])",
        goal_id,
        action_ids,
    )
    return {row["id"]: row["status"] for row in rows}


async def _abandon_unexecuted(connection, action_id: uuid.UUID, goal_id: uuid.UUID) -> bool:
    """An approved goal action that never executed is abandoned by the Stop."""
    row = await connection.fetchrow(
        "UPDATE pending_actions SET status = $1, decided_by = 'system:goal_stop', "
        "decided_at = clock_timestamp() "
        "WHERE id = $2 AND status = $3 AND execution_result IS NULL RETURNING id",
        ActionStatus.ABANDONED.value,
        action_id,
        ActionStatus.APPROVED.value,
    )
    if row is None:
        return False
    await record_approval_event(
        connection,
        ApprovalEventType.ACTION_ABANDONED,
        actor="system:goal_stop",
        action_id=action_id,
        reason="goal stopped",
        metadata={"goal_id": str(goal_id)},
    )
    return True


async def cancel_goal_effects(
    pool: Any, goal_id: uuid.UUID, action_ids: list[uuid.UUID]
) -> list[uuid.UUID]:
    """End the named open actions of a stopped goal in one transaction.

    ``action_ids`` are exactly the effects the broker cancelled (never one it
    dispatched or settled, D14), so an approved action whose effect already
    ran is never touched. Pending actions are ``cancelled`` through the
    canonical transition, which also fences their owner prompts; approved
    actions that never executed are ``abandoned``.
    """
    ended: list[uuid.UUID] = []
    if not action_ids:
        return ended
    async with pool.acquire() as connection, connection.transaction():
        rows = await connection.fetch(
            "SELECT id, status FROM pending_actions WHERE goal_id = $1 "
            "AND id = ANY($2::uuid[]) AND status IN ('pending', 'approved') ORDER BY id",
            goal_id,
            action_ids,
        )
        for row in rows:
            if row["status"] == ActionStatus.PENDING.value:
                transition = await transition_pending_action(
                    connection,
                    action_id=row["id"],
                    target_status=ActionStatus.CANCELLED,
                    decided_by="system:goal_stop",
                    event_actor="system:goal_stop",
                    event_reason="goal stopped",
                    event_metadata={"goal_id": str(goal_id)},
                    _already_in_transaction=True,
                )
                changed = transition.changed
            else:
                changed = await _abandon_unexecuted(connection, row["id"], goal_id)
            if changed:
                ended.append(row["id"])
    return ended
