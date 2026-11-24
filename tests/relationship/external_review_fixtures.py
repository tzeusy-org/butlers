"""The dashboard's bound approve entry for an external proposal, for PostgreSQL tests.

An external MCP proposal approves only through ``operations.approve_action`` with an
``ExternalReviewBinding``, which the passkey dashboard route builds after it re-reads
the typed review under the pending-row lock and matches the owner's displayed call
(REQ-dashboard-approvals-904). ``dashboard_review_binding`` makes the same binding
from the same reader, so these tests approve through the one bound entry.
``record_external_proposal`` writes the importer's proposal row the reader needs.
All data is synthetic.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from fastmcp import FastMCP

from butlers.api.external_proposal_review import read_external_proposal_review
from butlers.modules.approvals import operations
from butlers.modules.approvals.external_proposals import ExternalReviewBinding
from butlers.modules.approvals.module import ApprovalsModule
from butlers.roster_imports import roster_module
from tests.relationship.synthetic_authority import synthetic_owner

relationship_modules = roster_module("relationship", "modules")


async def record_external_proposal(
    conn,
    *,
    proposal_id: uuid.UUID,
    action_id: uuid.UUID,
    subject: uuid.UUID,
    object_entity_id: uuid.UUID,
    received_at: datetime,
    predicate: str = "works-at",
    note: str | None = None,
    evidence_url: str | None = None,
) -> None:
    """Record the imported, parked proposal row naming *action_id* (idempotent)."""
    await conn.execute(
        """
        INSERT INTO relationship.external_fact_proposals
            (proposal_id, state, note, evidence_url, sub, cid, idempotency_key,
             subject_entity_id, predicate, object_entity_id, received_at, imported_at,
             pending_action_id)
        VALUES ($1, 'parked', $2, $3, 'synthetic-sub', 'https://client.example/cimd',
                $4, $5, $6, $7, $8, $8, $9)
        ON CONFLICT (proposal_id) DO NOTHING
        """,
        proposal_id,
        note,
        evidence_url,
        proposal_id.hex[:32],
        subject,
        predicate,
        object_entity_id,
        received_at,
        action_id,
    )


async def dashboard_review_binding(conn, action_id: uuid.UUID) -> ExternalReviewBinding:
    """Bind the call the typed review displays, read under the pending-row lock."""
    row = await conn.fetchrow(
        "SELECT tool_name, tool_args, goal_id, deduplication_key, evidence "
        "FROM pending_actions WHERE id = $1 FOR UPDATE",
        action_id,
    )
    review = await read_external_proposal_review(conn, action_id=action_id, row=row)
    assert review.displayed_call is not None, review.approve_unavailable_reason
    return ExternalReviewBinding(action_id=action_id, args_digest=review.displayed_call.args_digest)


async def _approve(pool, action_id: uuid.UUID, *, edits: dict | None = None) -> dict:
    """The owner's dashboard decision, bound to the call its typed review displayed.

    *edits* change the stored call after the review was shown, which the bound
    approve operation must refuse.
    """
    async with synthetic_owner(pool), pool.acquire() as conn, conn.transaction():
        binding = await dashboard_review_binding(conn, action_id)
        if edits:
            args = await conn.fetchval(
                "SELECT tool_args FROM pending_actions WHERE id=$1", action_id
            )
            await conn.execute(
                "UPDATE pending_actions SET tool_args=$2 WHERE id=$1", action_id, args | edits
            )
        return await operations.approve_action(conn, str(action_id), external_review=binding)


async def _dispatch(pool, action_id: uuid.UUID) -> dict:
    """The owning daemon's shared executor, resolving the registered tool by name."""
    relationship = relationship_modules.RelationshipModule()
    mcp = FastMCP("relationship")
    await relationship.register_tools(mcp, None, SimpleNamespace(pool=pool), "relationship")
    approvals = ApprovalsModule()
    await approvals.on_startup({}, pool)

    async def execute(tool_name: str, tool_args: dict):
        tool: Any = await mcp.get_tool(tool_name)
        handler: Any = tool.fn
        return await handler(**tool_args)

    approvals._tool_executor = execute
    approvals._butler_name = "relationship"
    try:
        return await approvals._dispatch_approved_action_by_id(str(action_id))
    finally:
        await approvals.on_shutdown()
