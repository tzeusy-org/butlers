"""Single choke point for parking a PENDING ``pending_actions`` row.

bu-mda0r: an audit of every ``INSERT INTO pending_actions ... status='pending'``
call site found seven of them, and only one -- the MCP approval gate
(``gate.py``) -- ever called :func:`emit_approval_push`.  The other six parked
an action and left the owner with no way to know it existed short of opening
the dashboard.  Routing every PENDING insert through :func:`park_pending_action`
makes that failure mode impossible to reintroduce: a new park site cannot
construct a PENDING row without also attempting the push, because the row and
the push are inserted by the same function call.

Auto-approved inserts (``status='approved'``, e.g. the gate's owner-directed
and standing-rule auto-approve paths) are intentionally NOT covered by this
helper -- they never need an owner push and continue to ``INSERT`` directly.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from butlers.modules.approvals.notifications import (
    ApprovalPushOutcome,
    ApprovalPushRuntime,
    emit_approval_push,
)


async def park_pending_action(
    pool: Any,
    *,
    action_id: uuid.UUID,
    tool_name: str,
    tool_args: dict[str, Any],
    agent_summary: str | None,
    requested_at: datetime,
    expires_at: datetime | None,
    session_id: uuid.UUID | None = None,
    why: str | None = None,
    evidence: Sequence[dict[str, str]] | None = None,
    blast_radius: str | None = None,
    reversibility: str | None = None,
    origin_butler: str | None,
    approval_push_runtime: ApprovalPushRuntime | None,
    deduplication_key: str | None = None,
) -> ApprovalPushOutcome | None:
    """Insert one ``status='pending'`` row and push it to the owner.

    This is the only sanctioned way to park an action awaiting human review.
    ``tool_args`` must already be JSON-safe (callers typically round-trip
    through ``json.loads(json.dumps(tool_args, default=str))`` first, matching
    the rest of this codebase's jsonb-codec convention -- see gate.py).

    Returns the push outcome (:func:`emit_approval_push`'s return value), or
    ``None`` when no push runtime is wired for this call site (e.g. a
    dashboard-API context with no delivery plane, or a butler that has not
    enabled approval pushes). ``None`` means "no push was attempted", not
    "the push succeeded" -- callers that need to distinguish should inspect
    the returned outcome directly.

    ``deduplication_key`` opts a producer into the durable active-action
    uniqueness constraint. It is nullable so existing writers retain their
    stored shape until they have a stable semantic key.
    """
    if deduplication_key is None:
        await pool.execute(
            "INSERT INTO pending_actions "
            "(id, tool_name, tool_args, agent_summary, session_id, status, "
            "requested_at, expires_at, why, evidence, blast_radius, reversibility) "
            "VALUES ($1, $2, $3, $4, $5, 'pending', $6, $7, $8, $9, $10, $11)",
            action_id,
            tool_name,
            tool_args,
            agent_summary,
            session_id,
            requested_at,
            expires_at,
            why,
            list(evidence) if evidence is not None else [],
            blast_radius,
            reversibility,
        )
    else:
        await pool.execute(
            "INSERT INTO pending_actions "
            "(id, tool_name, tool_args, agent_summary, session_id, status, "
            "requested_at, expires_at, why, evidence, blast_radius, reversibility, "
            "deduplication_key) "
            "VALUES ($1, $2, $3, $4, $5, 'pending', $6, $7, $8, $9, $10, $11, $12)",
            action_id,
            tool_name,
            tool_args,
            agent_summary,
            session_id,
            requested_at,
            expires_at,
            why,
            list(evidence) if evidence is not None else [],
            blast_radius,
            reversibility,
            deduplication_key,
        )

    if approval_push_runtime is None or not origin_butler:
        return None

    return await emit_approval_push(
        pool=pool,
        action={
            "id": action_id,
            "tool_name": tool_name,
            "requested_at": requested_at,
            "expires_at": expires_at,
            "why": why,
            "blast_radius": blast_radius,
            "reversibility": reversibility,
        },
        origin_butler=origin_butler,
        runtime=approval_push_runtime,
        now=requested_at,
    )


async def park_prepared_action(
    pool: Any,
    *,
    action_id: uuid.UUID,
    tool_name: str,
    tool_args: dict[str, Any],
    agent_summary: str | None,
    requested_at: datetime,
    expires_at: datetime,
    why: str | None = None,
    evidence: Sequence[dict[str, str]] | None = None,
    blast_radius: str | None = None,
    reversibility: str | None = None,
    deduplication_key: str,
) -> None:
    """Insert one ``status='pending', origin='prepared'`` row. Never pushes.

    bu-2jtfw.11: a prepared action is a proactive draft parked on the
    approval spine by an insight-scan producer, surfaced only through the
    insight digest's door (see ``roster/switchboard/tools/insight/broker.py``)
    -- never through an owner push. Unlike :func:`park_pending_action`, this
    is deliberately NOT the choke point every park path routes through: it is
    a second, narrower entry point for exactly one shape of row, and it must
    never call :func:`~butlers.modules.approvals.notifications.emit_approval_push`.

    ``expires_at`` and ``deduplication_key`` are required (not optional, unlike
    ``park_pending_action``): every prepared action must eventually be swept
    by the nightly orphan sweep, and every prepared action concerns some
    durable real-world identity (a contact, a subscription, a thread) that a
    concurrent scan must not double-park -- see ``approvals_013``'s partial
    unique index on ``deduplication_key``.

    ``tool_args`` must already be JSON-safe, matching :func:`park_pending_action`.
    """
    await pool.execute(
        "INSERT INTO pending_actions "
        "(id, tool_name, tool_args, agent_summary, session_id, status, origin, "
        "requested_at, expires_at, why, evidence, blast_radius, reversibility, "
        "deduplication_key) "
        "VALUES ($1, $2, $3, $4, NULL, 'pending', 'prepared', $5, $6, $7, $8, $9, $10, $11)",
        action_id,
        tool_name,
        tool_args,
        agent_summary,
        requested_at,
        expires_at,
        why,
        list(evidence) if evidence is not None else [],
        blast_radius,
        reversibility,
        deduplication_key,
    )


__all__ = ["park_pending_action", "park_prepared_action"]
