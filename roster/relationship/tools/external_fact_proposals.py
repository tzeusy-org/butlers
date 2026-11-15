"""Park an external MCP fact proposal with its third-party provenance (RFC 0039 D13).

The deterministic proposal importer (a model-free Relationship job) calls
:func:`park_external_fact_proposal` for each valid proposal. In one transaction it
parks the ``relationship_assert_fact`` action through the approvals module's
:func:`~butlers.modules.approvals.park.park_external_proposal` (no push, no
standing rule) and records the action's server-side provenance in
``relationship.fact_approval_context``: ``src = 'external_mcp:<proposal_id>'``,
the proposal's receipt time as ``observed_at`` and a frozen
``content_authority = 'third_party'`` report. None of these is a tool argument,
so the approved replay reads them only from the row this function wrote, exactly
as REQ-relationship-facts-005 requires (REQ-relationship-facts-011).

On the owner's approval the fact is written with ``assert_origin = 'approved'``,
``content_authority = 'third_party'``, ``confirmed_at`` set and
``confirmation_source = 'approved_action'``: verified, owner-endorsed and still
attributed to the outside source.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from butlers.core.fact_authority import FactWriteContext
from butlers.modules.approvals.park import ParkAdmission, park_external_proposal
from butlers.roster_imports import roster_module

_temporal = roster_module("relationship", "tools/fact_temporal")
_writer = roster_module("relationship", "tools/relationship_assert_fact")

#: Every pending action this helper parks belongs to the Relationship butler.
_ORIGIN_BUTLER = "relationship"
EXTERNAL_FACT_SRC_PREFIX = "external_mcp:"


def external_fact_src(proposal_id: uuid.UUID) -> str:
    """The server-recorded ``src`` of a fact applied from one external proposal."""
    if not isinstance(proposal_id, uuid.UUID):
        raise ValueError("proposal_id must be a UUID")
    return f"{EXTERNAL_FACT_SRC_PREFIX}{proposal_id}"


async def park_external_fact_proposal(
    conn: Any,
    *,
    proposal_id: uuid.UUID,
    subject: uuid.UUID,
    predicate: str,
    object_entity_id: uuid.UUID,
    client_label: str,
    evidence_url: str | None,
    received_at: datetime,
) -> ParkAdmission:
    """Park one validated external proposal and freeze its third-party provenance.

    *conn* is an ``asyncpg`` connection; when it is already inside the caller's
    transaction the park, the approval context and the caller's proposal-state
    write commit or roll back together. The caller (the importer) has already
    checked the predicate allowlist, entity existence and the owner subject; this
    helper still validates the predicate against the registry and builds the
    stored arguments itself, so no caller can place ``src``,
    ``content_authority`` or ``observed_at`` among them.

    A repeated call for the same *proposal_id* returns the existing action with
    ``duplicate=True`` and writes no second action or context row.
    """
    if not isinstance(subject, uuid.UUID) or not isinstance(object_entity_id, uuid.UUID):
        raise ValueError("subject and object_entity_id must be UUIDs")
    if received_at.tzinfo is None or received_at.utcoffset() is None:
        raise ValueError("received_at must be timezone-aware")
    src = external_fact_src(proposal_id)
    action_id = uuid.uuid4()
    temporal = _temporal.normalize_request()
    async with conn.transaction():
        await _writer._validate_predicate(conn, predicate)
        parked = await _writer._parked_arguments(
            conn,
            temporal,
            subject=subject,
            predicate=predicate,
            object=str(object_entity_id),
            object_kind="entity",
            conf=1.0,
            verified=False,
            last_seen=None,
            weight=None,
            primary=None,
        )
        tool_args = {**parked.tool_args, "approval_action_id": str(action_id)}
        admission = await park_external_proposal(
            conn,
            proposal_id=proposal_id,
            action_id=action_id,
            tool_args=tool_args,
            client_label=client_label,
            evidence_url=evidence_url,
            origin_butler=_ORIGIN_BUTLER,
        )
        if admission.duplicate:
            return admission
        await _writer._record_approval_context(
            conn,
            action_id=action_id,
            src=src,
            observed_at=received_at,
            temporal_mode=temporal.mode,
            temporal_base_fact_id=parked.base_fact_id,
            fact_context=FactWriteContext("third_party"),
        )
    return admission


__all__ = [
    "EXTERNAL_FACT_SRC_PREFIX",
    "external_fact_src",
    "park_external_fact_proposal",
]
