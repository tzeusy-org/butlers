"""Park a goal effect for its owning domain (RFC 0038 D13, bu-cadcr1).

The capability broker parks every call that needs approval here, with the
exact tool arguments and the call's goal lineage. The pending action's id is
the broker's effect id, so a repeated park resolves to the same action.

Standing rules are matched at park time only when the broker allows it (never
for park-only grants, ``always_per_event`` tools, administer or risky
actuation). For an exposed lineage only two kinds of rule apply
(REQ-capability-authorization-006): one whose recipient is the owner, or one
that is template-pinned. This module recognises template pins conservatively:
every supplied argument must carry an ``exact`` constraint. Typed-slot
templates are not yet recognised and so park. Every declared safety argument
must be pinned by any matching rule.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from butlers.modules.approvals.delivery_lifecycle import transition_pending_action
from butlers.modules.approvals.events import ApprovalEventType, record_approval_event
from butlers.modules.approvals.models import ActionStatus
from butlers.modules.approvals.park import park_pending_action
from butlers.modules.approvals.rules import (
    claim_rule_use_sql,
    constraint_pins_value,
    match_rules_from_list,
    parse_constraints,
)

_PARK_LIFETIME = timedelta(hours=48)


@dataclass(frozen=True, slots=True)
class GoalLineage:
    goal_id: uuid.UUID
    invocation_id: uuid.UUID
    depth: int
    exposed: bool
    origin: str
    effect_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class GoalParkOutcome:
    action_id: uuid.UUID
    rule_id: uuid.UUID | None = None


def _exact(constraint: Any) -> bool:
    if isinstance(constraint, dict):
        return str(constraint.get("type", "")).lower() == "exact"
    return constraint != "*"  # A bare legacy value is an exact pin.


def _eligible(
    rule: Any,
    args: dict[str, Any],
    lineage: GoalLineage,
    recipient_is_owner: bool,
    safety_args: tuple[str, ...],
) -> bool:
    constraints = parse_constraints(rule["arg_constraints"])
    if any(
        not constraint_pins_value(constraints.get(name)) for name in safety_args if name in args
    ):
        return False
    if not lineage.exposed or recipient_is_owner:
        return True
    return all(_exact(constraints.get(name, "*")) for name in args)


async def _set_lineage(connection, lineage: GoalLineage) -> None:
    await connection.execute(
        "UPDATE pending_actions SET goal_id = $2, goal_invocation_id = $3, goal_depth = $4, "
        "goal_exposed = $5, goal_origin = $6 WHERE id = $1",
        lineage.effect_id,
        lineage.goal_id,
        lineage.invocation_id,
        lineage.depth,
        lineage.exposed,
        lineage.origin,
    )


async def park_goal_effect(
    pool: Any,
    *,
    lineage: GoalLineage,
    tool_name: str,
    tool_args: dict[str, Any],
    allow_standing_rule: bool,
    recipient_is_owner: bool,
    safety_args: tuple[str, ...] = (),
    origin_butler: str,
) -> GoalParkOutcome:
    """Park (or rule-approve) one goal effect with its lineage, atomically."""
    now = datetime.now(UTC)
    async with pool.acquire() as connection, connection.transaction():
        existing = await connection.fetchrow(
            "SELECT id, approval_rule_id FROM pending_actions WHERE id = $1", lineage.effect_id
        )
        if existing is not None:
            return GoalParkOutcome(existing["id"], existing["approval_rule_id"])
        if allow_standing_rule:
            rows = await connection.fetch(
                "SELECT * FROM approval_rules WHERE tool_name = $1 AND active = true "
                "ORDER BY created_at DESC, id ASC",
                tool_name,
            )
            eligible = [
                row
                for row in rows
                if _eligible(row, tool_args, lineage, recipient_is_owner, safety_args)
            ]
            rule = match_rules_from_list(tool_name, tool_args, eligible)
            if rule is not None:
                claimed = await connection.fetchrow(
                    "WITH claimed AS (" + claim_rule_use_sql(rule_param=3, now_param=4) + ") "
                    "INSERT INTO pending_actions (id, tool_name, tool_args, status, requested_at, "
                    "approval_rule_id, decided_by, decided_at, goal_id, goal_invocation_id, "
                    "goal_depth, goal_exposed, goal_origin) "
                    # INSERT ... SELECT does not infer parameter types from the target.
                    "SELECT $1::uuid, $2::text, $5::jsonb, 'approved', $4::timestamptz, "
                    "claimed.id, 'rule:' || claimed.id::text, $4::timestamptz, $6::uuid, "
                    "$7::uuid, $8::smallint, $9::boolean, $10::text FROM claimed RETURNING id",
                    lineage.effect_id,
                    tool_name,
                    rule.id,
                    now,
                    tool_args,
                    lineage.goal_id,
                    lineage.invocation_id,
                    lineage.depth,
                    lineage.exposed,
                    lineage.origin,
                )
                if claimed is not None:
                    await record_approval_event(
                        connection,
                        ApprovalEventType.ACTION_AUTO_APPROVED,
                        actor="system:capability_broker",
                        action_id=lineage.effect_id,
                        rule_id=rule.id,
                        reason="standing rule matched at goal park",
                        metadata={"tool_name": tool_name, "goal_id": str(lineage.goal_id)},
                        occurred_at=now,
                    )
                    return GoalParkOutcome(lineage.effect_id, rule.id)
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
        await _set_lineage(connection, lineage)
    return GoalParkOutcome(lineage.effect_id)


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


async def cancel_goal_effects(pool: Any, goal_id: uuid.UUID) -> list[uuid.UUID]:
    """End a stopped goal's open actions in one transaction.

    Pending actions are ``cancelled`` through the canonical transition, which
    also fences their owner prompts; approved actions that never executed are
    ``abandoned``.
    """
    ended: list[uuid.UUID] = []
    async with pool.acquire() as connection, connection.transaction():
        rows = await connection.fetch(
            "SELECT id, status FROM pending_actions "
            "WHERE goal_id = $1 AND status IN ('pending', 'approved') ORDER BY id",
            goal_id,
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
