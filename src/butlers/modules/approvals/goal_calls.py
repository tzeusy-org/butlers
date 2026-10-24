"""Private owning-domain goal approvals; no unsigned/model-facing ingress.

The verified domain ingress and approved replay producer are still separate
integration work. A lineage DTO is data, not an authentication mechanism.
No fallback admits a goal call on an unmigrated schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from butlers.core.capability_json import arguments_digest, canonical_bytes, parse_arguments
from butlers.core.goal_admission import Grant
from butlers.core.tool_catalog import ToolCatalog
from butlers.modules.approvals.models import ActionStatus, GoalCallLineage, PendingAction
from butlers.modules.approvals.park import ParkAdmission, ParkRequest


async def park_goal_call(
    owning_pool: Any,
    *,
    action_id: UUID,
    tool_name: str,
    raw_args: dict,
    lineage: GoalCallLineage,
    origin_butler: str,
    requested_at: datetime,
    expires_at: datetime | None,
    agent_summary: str | None = None,
    session_id: UUID | None = None,
    deduplication_key: str | None = None,
    owning_catalog: ToolCatalog | None = None,
    grant: Grant | None = None,
) -> ParkAdmission:
    """Bind and snapshot the raw call before the first owning-store await.

    The caller MUST be trusted verified infrastructure, never a handler of
    model-supplied lineage. Optional final registration and grant are supplied
    by that same verified infrastructure, never decoded from caller policy
    flags. They permit actual owning rule matching, not dispatch or D7 proof.
    """
    snapshot = parse_arguments(canonical_bytes(raw_args))
    if (
        not isinstance(lineage, GoalCallLineage)
        or lineage.goal_capability != f"{origin_butler}__{tool_name}"
        or lineage.goal_args_digest != arguments_digest(snapshot)
        or lineage.goal_approved_args_digest is not None
    ):
        raise ValueError("goal_call_binding_mismatch")
    from butlers.modules.approvals.goal_control import OwningGoalApprovals

    admission, _receipt = await OwningGoalApprovals(
        owning_pool, origin_butler, catalog=owning_catalog
    ).park(
        ParkRequest(
            action_id=action_id,
            tool_name=tool_name,
            tool_args=snapshot,
            agent_summary=agent_summary,
            session_id=session_id,
            requested_at=requested_at,
            expires_at=expires_at,
            origin_butler=origin_butler,
            deduplication_key=deduplication_key,
            goal_lineage=lineage,
        ),
        grant=grant,
    )
    return admission


@dataclass(frozen=True, slots=True)
class StoredGoalCall:
    """Snapshot of the actual approved row, not permission to dispatch it."""

    approval_ref: UUID
    tool_name: str
    args: dict = field(repr=False)
    lineage: GoalCallLineage


def stored_goal_call(locked_row: Any) -> StoredGoalCall:
    """Project only a locked, authenticated owner-approved stored call.

    The owning executor must snapshot under its row/gate locks, release those
    locks before central authorization, then re-lock and revalidate this exact
    snapshot while committing its local dispatch witness before forwarding.
    It must never hold an owning transaction over a central/network call.
    Those operations are not supplied by this projection. No caller replacement
    arguments are accepted.
    """
    action = PendingAction.from_row(locked_row)
    lineage = action.goal_lineage()
    if (
        action.status is not ActionStatus.APPROVED
        or lineage is None
        or lineage.goal_approved_args_digest is None
        or lineage.goal_capability.split("__")[1] != action.tool_name
    ):
        raise ValueError("stored_goal_approval_required")
    snapshot = parse_arguments(canonical_bytes(action.tool_args))
    if arguments_digest(snapshot) != lineage.goal_approved_args_digest:
        raise ValueError("approved_arguments_mismatch")
    return StoredGoalCall(action.id, action.tool_name, snapshot, lineage)
