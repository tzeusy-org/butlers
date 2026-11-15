"""Typed dashboard review of an external MCP fact proposal (REQ-dashboard-approvals-904).

RFC 0039 D13 step 4. The Relationship importer parks an outside client's proposal as a
pending ``relationship_assert_fact`` action under the deduplication key
``external_mcp:<proposal_id>``. The approvals card for it is a typed review, after the
precedent in ``review-parked-entity-merge-approvals`` (REQ-dashboard-approvals-902):

* No generic ``call_preview`` is registered for ``relationship_assert_fact``. A shared
  preview would also make every *internal* relationship fact one-tap approvable from a
  notification card or a summary surface, and an older dashboard would show its plain
  Approve button. The generic preview stays null; only this envelope carries a
  ``displayed_call``, and only for an ``external_mcp:`` action.
* The review shows the entities the stored call names (name, type, ID, current state),
  the predicate, the client label, the third-party authority statement, and the note and
  full evidence URL read from ``relationship.external_fact_proposals`` as data. It never
  returns the raw stored arguments.
* The displayed call is offered only when the review shows *everything* the stored call
  carries: the exact argument shape the importer parks (two entity UUIDs, a predicate,
  ``object_kind='entity'``, ``conf=1.0``, the action's own ``approval_action_id`` and
  no temporal packet). Anything else, a proposal record that no longer backs this
  action, or an entity that is missing, merged or deleted makes approval unavailable
  with a stated reason. Reject is unaffected.

The approve path calls :func:`read_external_proposal_review` again under the action's
row lock, so the check and the decision see the same stored call.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

import asyncpg

from butlers.api.models.approval import (
    DisplayedApprovalCall,
    ExternalProposalEntity,
    ExternalProposalReview,
)
from butlers.modules.approvals.external_proposals import (
    EXTERNAL_PROPOSAL_KEY_PREFIX,
    EXTERNAL_PROPOSAL_TOOL,
    validate_client_label,
)
from butlers.modules.approvals.previews import args_digest

THIRD_PARTY_AUTHORITY_STATEMENT = (
    "An external AI client proposed this fact; neither you nor your butlers did. "
    "If you approve, it is written as a third-party report: your approval verifies it, "
    "and it stays attributed to this outside source."
)

#: ``roster/relationship/tools/fact_temporal.WIRE_KEYS``: the parked temporal packet.
#: An external proposal parks all six as null; any value is a call this review cannot show.
_TEMPORAL_KEYS = frozenset(
    {
        "effective_period_id",
        "effective_from",
        "effective_from_precision",
        "effective_to",
        "effective_to_precision",
        "corrects_fact_id",
    }
)
_SHOWN_KEYS = frozenset({"subject", "predicate", "object", "object_kind", "approval_action_id"})
_PREDICATE_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_CLIENT_REF_PREFIX = "external_mcp:client="

_UNSHOWABLE = "This stored call carries more than this review can show, so it cannot be approved."
_RECORD_UNAVAILABLE = "The external proposal record for this action is unavailable."
_RECORD_MISMATCH = "The stored call no longer matches the external proposal it came from."
_NO_CLIENT = "The proposing client is not recorded for this action."
_NOT_PARKED = "This proposal is no longer awaiting a decision."

_ENTITY_STATE_TEXT = {
    "missing": "no longer exists",
    "merged": "was merged into another entity",
    "deleted": "was deleted",
}


def proposal_id_from_key(deduplication_key: Any) -> UUID | None:
    """The proposal ID in an ``external_mcp:<uuid>`` key, or ``None``."""
    if not isinstance(deduplication_key, str):
        return None
    if not deduplication_key.startswith(EXTERNAL_PROPOSAL_KEY_PREFIX):
        return None
    try:
        return UUID(deduplication_key.removeprefix(EXTERNAL_PROPOSAL_KEY_PREFIX))
    except ValueError:
        return None


def _client_label(evidence: Any) -> str | None:
    """The server-derived client label from the parked evidence, validated, or ``None``."""
    if isinstance(evidence, str):
        try:
            evidence = json.loads(evidence)
        except json.JSONDecodeError:
            return None
    if not isinstance(evidence, Sequence):
        return None
    labels = {
        item["ref"].removeprefix(_CLIENT_REF_PREFIX)
        for item in evidence
        if isinstance(item, Mapping)
        and isinstance(item.get("ref"), str)
        and item["ref"].startswith(_CLIENT_REF_PREFIX)
    }
    if len(labels) != 1:
        return None
    try:
        return validate_client_label(labels.pop())
    except ValueError:
        return None


def _uuid(value: Any) -> UUID | None:
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def _shown_call(
    action_id: UUID, tool_name: Any, tool_args: Mapping[str, Any]
) -> tuple[UUID, str, UUID] | None:
    """``(subject, predicate, object)`` when the review shows the whole stored call."""
    if tool_name != EXTERNAL_PROPOSAL_TOOL:
        return None
    if set(tool_args) - _SHOWN_KEYS - _TEMPORAL_KEYS - {"conf"}:
        return None
    if any(tool_args.get(key) is not None for key in _TEMPORAL_KEYS):
        return None
    conf = tool_args.get("conf", 1.0)
    if isinstance(conf, bool) or conf != 1.0:
        return None
    subject = _uuid(tool_args.get("subject"))
    obj = _uuid(tool_args.get("object"))
    predicate = tool_args.get("predicate")
    if (
        subject is None
        or obj is None
        or tool_args.get("object_kind") != "entity"
        or not isinstance(predicate, str)
        or not _PREDICATE_RE.fullmatch(predicate)
        or tool_args.get("approval_action_id") != str(action_id)
    ):
        return None
    return subject, predicate, obj


def _entity(entity_id: UUID, row: Mapping[str, Any] | None) -> ExternalProposalEntity:
    if row is None:
        return ExternalProposalEntity(id=str(entity_id), status="missing")
    if row["merged_into"] is not None:
        status = "merged"
    elif row["deleted_at"] is not None:
        status = "deleted"
    else:
        status = "live"
    return ExternalProposalEntity(
        id=str(entity_id),
        name=row["canonical_name"],
        entity_type=row["entity_type"],
        status=status,
    )


def _unresolvable_reason(
    subject: ExternalProposalEntity, obj: ExternalProposalEntity
) -> str | None:
    gone = [
        f"The {role} entity {entity.id} {_ENTITY_STATE_TEXT[entity.status]} ({entity.status})"
        for role, entity in (("subject", subject), ("object", obj))
        if entity.status != "live"
    ]
    if not gone:
        return None
    return "; ".join(gone) + ". This proposal cannot be applied; reject it instead."


async def read_external_proposal_review(
    conn: Any,
    *,
    action_id: UUID,
    row: Mapping[str, Any],
) -> ExternalProposalReview:
    """Build the typed review of one ``external_mcp:`` action from current state.

    *row* is the ``pending_actions`` row (``tool_name``, ``tool_args``,
    ``deduplication_key``, ``evidence``). The proposal record must name this exact
    action (``pending_action_id``), so only the action the Relationship importer
    parked for that proposal can ever earn a displayed call. Fails closed: every read or
    shape problem leaves ``displayed_call`` null with a reason, never a partial approvable
    review.
    """
    proposal_id = proposal_id_from_key(row.get("deduplication_key"))
    raw_args = row.get("tool_args")
    tool_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
    if not isinstance(tool_args, Mapping):
        tool_args = {}
    review: dict[str, Any] = {
        "proposal_id": str(proposal_id) if proposal_id is not None else None,
        "client_label": _client_label(row.get("evidence")),
        "authority_statement": THIRD_PARTY_AUTHORITY_STATEMENT,
    }
    shown = _shown_call(action_id, row.get("tool_name"), tool_args)
    predicate = tool_args.get("predicate")
    if isinstance(predicate, str) and _PREDICATE_RE.fullmatch(predicate):
        review["predicate"] = predicate

    reasons: list[str] = []
    proposal = None
    if proposal_id is None:
        reasons.append(_RECORD_UNAVAILABLE)
    else:
        try:
            proposal = await conn.fetchrow(
                """
                SELECT state, note, evidence_url, subject_entity_id, predicate, object_entity_id
                  FROM relationship.external_fact_proposals
                 WHERE proposal_id = $1 AND pending_action_id = $2
                """,
                proposal_id,
                action_id,
            )
        except asyncpg.PostgresError:
            proposal = None
        if proposal is None:
            reasons.append(_RECORD_UNAVAILABLE)
        else:
            review["note"] = proposal["note"]
            review["evidence_url"] = proposal["evidence_url"]

    if shown is None:
        reasons.insert(0, _UNSHOWABLE)
    else:
        subject_id, predicate, object_id = shown
        try:
            rows = await conn.fetch(
                """
                SELECT id, canonical_name, entity_type,
                       metadata->>'merged_into' AS merged_into,
                       metadata->>'deleted_at' AS deleted_at
                  FROM public.entities
                 WHERE id = ANY($1::uuid[])
                """,
                [subject_id, object_id],
            )
        except asyncpg.PostgresError:
            rows = None
        if rows is None:
            reasons.append("The proposal's entities could not be read.")
        else:
            by_id = {row_["id"]: row_ for row_ in rows}
            review["subject"] = _entity(subject_id, by_id.get(subject_id))
            review["object"] = _entity(object_id, by_id.get(object_id))
            unresolvable = _unresolvable_reason(review["subject"], review["object"])
            if unresolvable is not None:
                review["unresolvable_entity"] = True
                reasons.insert(0, unresolvable)
        if proposal is not None:
            if (
                proposal["subject_entity_id"],
                proposal["predicate"],
                proposal["object_entity_id"],
            ) != (subject_id, predicate, object_id):
                reasons.append(_RECORD_MISMATCH)
            elif proposal["state"] != "parked":
                reasons.append(_NOT_PARKED)

    if review["client_label"] is None:
        reasons.append(_NO_CLIENT)

    if reasons:
        review["approve_unavailable_reason"] = reasons[0]
    else:
        review["displayed_call"] = DisplayedApprovalCall(
            tool_name=EXTERNAL_PROPOSAL_TOOL, args_digest=args_digest(dict(tool_args))
        )
    return ExternalProposalReview(**review)


__all__ = [
    "THIRD_PARTY_AUTHORITY_STATEMENT",
    "proposal_id_from_key",
    "read_external_proposal_review",
]
