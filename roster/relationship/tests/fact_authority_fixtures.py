"""Explicit synthetic admission for existing schema-faithful writer conformance.

These private test contexts are NOT a registered producer or authentication
proof. The genuine migrated/registered path lives in the nine integration groups.
"""

from __future__ import annotations

import asyncio
import json

from butlers.modules.approvals.execution_context import (
    ApprovalExecutionContext,
    approval_tool_args_digest,
    reset_approval_execution_context,
    set_approval_execution_context,
)
from butlers.tools.relationship.fact_authority import record_admitted_approval
from tests.relationship.synthetic_authority import synthetic_owner


async def approve_fixture(pool, action_id):
    """A synthetic decision with actual stored arguments and append-only event.

    *pool* must carry the approvals chain (``approval_events``).
    """
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("UPDATE pending_actions SET status='approved' WHERE id=$1", action_id)
        await conn.execute(
            "INSERT INTO approval_events(event_type,action_id,actor,event_metadata) "
            "VALUES('action_approved',$1,'synthetic-owner-fixture','{}'::jsonb)",
            action_id,
        )
        async with synthetic_owner(pool):
            await record_admitted_approval(conn, action_id)


async def replay_fixture(pool, writer, *args, **kwargs):
    """Bind the exact stored action for one task; no real-executor credit."""
    action_id = kwargs["approval_action_id"]
    original = await pool.fetchval("SELECT tool_args FROM pending_actions WHERE id=$1", action_id)
    if isinstance(original, str):
        original = json.loads(original)
    token = set_approval_execution_context(
        ApprovalExecutionContext(
            action_id,
            None,
            "synthetic-owner-fixture",
            "relationship_assert_fact",
            approval_tool_args_digest(original),
            asyncio.current_task(),
        )
    )
    try:
        return await writer(pool, *args, **kwargs)
    finally:
        reset_approval_execution_context(token)
