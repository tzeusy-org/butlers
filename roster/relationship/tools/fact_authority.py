"""Writing-transaction attribution and row-derived authority for identity facts."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from butlers.core.fact_authority import FactWriteContext, current_fact_write_context

REPORT_COLUMNS = (
    "content_authority, authority_entity_id, authority_original_entity_id, "
    "authority_entity_created_at, confirmed_by_entity_id, confirmed_by_original_entity_id, "
    "confirmed_at, confirmation_source"
)
IDENTITY_PREDICATES = frozenset(
    {"has-email", "has-phone", "has-handle", "has-website", "prefers-channel"}
)


def stored_report(row: Any, *, preserve: bool = True) -> FactWriteContext:
    return FactWriteContext(
        row["content_authority"],
        row["authority_original_entity_id"],
        row["authority_entity_created_at"],
        row["authority_entity_id"],
        row["confirmed_by_entity_id"],
        row["confirmed_by_original_entity_id"],
        row["confirmed_at"],
        row["confirmation_source"],
        preserve,
    )


def report_sql_args(report: FactWriteContext) -> tuple[Any, ...]:
    return (
        report.authority,
        report.live_entity_id,
        report.original_entity_id,
        report.entity_created_at,
        report.confirmation_entity_id,
        report.confirmation_original_entity_id,
        report.confirmed_at,
        report.confirmation_source,
    )


async def locked_report(
    conn: Any,
    report: FactWriteContext,
    *,
    subject: uuid.UUID,
    object: str,
    object_kind: str,
    src: str,
    extra_entity_ids: set[uuid.UUID] | None = None,
) -> FactWriteContext:
    """Lock the full FK union first; compare the source-captured birth witness.

    An unavailable live pointer never refills on preservation. The original UUID
    is never used as a display join, recipient selector or source credential.
    """
    from butlers.tools.relationship.relationship_assert_fact import _lock_fact_entities_batch

    owner = None
    if report.authority == "owner_device" and report.original_entity_id is None:
        owner = await conn.fetchrow(
            "SELECT id,created_at FROM public.entities WHERE 'owner'=ANY(roles) ORDER BY id LIMIT 1"
        )
        if owner is None:
            raise PermissionError("no registered owner entity")
        report = replace(
            report,
            original_entity_id=owner["id"],
            live_entity_id=owner["id"],
            entity_created_at=owner["created_at"],
        )
    elif report.authority == "system" and src in {"owner-bootstrap", "owner-self"}:
        owner = await conn.fetchrow(
            "SELECT id,created_at FROM public.entities WHERE id=$1 AND 'owner'=ANY(roles)", subject
        )
        if owner:
            report = replace(
                report,
                authority="owner",
                original_entity_id=owner["id"],
                live_entity_id=owner["id"],
                entity_created_at=owner["created_at"],
            )
    ids = {subject} | (extra_entity_ids or set())
    if object_kind == "entity":
        ids.add(uuid.UUID(object))
    if report.live_entity_id:
        ids.add(report.live_entity_id)
    if report.confirmation_entity_id:
        ids.add(report.confirmation_entity_id)
    await _lock_fact_entities_batch(conn, ids)
    if report.live_entity_id:
        live = await conn.fetchrow(
            "SELECT created_at,metadata FROM public.entities WHERE id=$1", report.live_entity_id
        )
        if (
            live is None
            or live["created_at"] != report.entity_created_at
            or (
                (live["metadata"] or {}).get("merged_into")
                or (live["metadata"] or {}).get("deleted_at")
            )
        ):
            report = replace(report, live_entity_id=None)
    if report.confirmation_entity_id and not await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM public.entities WHERE id=$1 "
        "AND metadata->>'deleted_at' IS NULL AND metadata->>'merged_into' IS NULL)",
        report.confirmation_entity_id,
    ):
        report = replace(report, confirmation_entity_id=None)
    if report.owner_class and not report.preserve and report.confirmed_at is None:
        report = replace(
            report,
            confirmation_entity_id=report.live_entity_id,
            confirmation_original_entity_id=report.original_entity_id,
            confirmed_at=datetime.now(UTC),
            confirmation_source="owner_assertion",
        )
    return report


async def should_hold_candidate(
    conn: Any, subject: uuid.UUID, predicate: str, report: FactWriteContext
) -> bool:
    if (
        predicate not in IDENTITY_PREDICATES
        or report.authority not in {"third_party", "mixed"}
        or report.verified
    ):
        return False
    entity = await conn.fetchrow(
        "SELECT entity_type,metadata FROM public.entities WHERE id=$1", subject
    )
    return bool(
        entity
        and entity["entity_type"] == "person"
        and not (entity["metadata"] or {}).get("unidentified", False)
    )


async def stored_gap_authority(conn: Any, fact_id: uuid.UUID) -> Any:
    from butlers.modules.memory.content_authority import ContentAuthority

    row = await conn.fetchrow(
        "SELECT content_authority, authority_entity_id, confirmed_at,confirmed_by_entity_id "
        "FROM relationship.entity_facts WHERE id=$1 AND validity='active'",
        fact_id,
    )
    if row is None:
        raise ValueError("ineligible fact cannot answer a gap")
    if row["confirmed_at"] is not None:
        return ContentAuthority("owner", row["confirmed_by_entity_id"])
    # Legacy NULL is unknown/third-party for establishment, never guessed owner.
    return ContentAuthority(row["content_authority"] or "third_party", row["authority_entity_id"])


async def record_admitted_approval(conn: Any, action_id: uuid.UUID) -> None:
    """Private hook beside the real approval transition, not an actor label."""
    report = current_fact_write_context()
    row = await conn.fetchrow(
        "SELECT tool_name,tool_args FROM pending_actions WHERE id=$1", action_id
    )
    if row is None or row["tool_name"] != "relationship_assert_fact":
        return
    # Approvals is also installed in core-only/non-Relationship topologies.
    # The optional owning hook must not query a missing or inaccessible schema.
    reachable = await conn.fetchval(
        "SELECT COALESCE((SELECT has_schema_privilege(oid,'USAGE') "
        "FROM pg_namespace WHERE nspname='relationship'),false)"
    )
    if not reachable or not await conn.fetchval(
        "SELECT to_regclass('relationship.fact_approval_context') IS NOT NULL"
    ):
        return
    if not report.owner_class:
        fresh = await conn.fetchval(
            "SELECT frozen_report IS NOT NULL FROM relationship.fact_approval_context "
            "WHERE action_id=$1",
            action_id,
        )
        if fresh:
            raise PermissionError("fresh Relationship approval requires admitted owner authority")
        return
    args = row["tool_args"]
    if isinstance(args, str):
        import json

        args = json.loads(args)
    report = await locked_report(
        conn,
        report,
        subject=uuid.UUID(args["subject"]),
        object=args["object"],
        object_kind=args.get("object_kind", "literal"),
        src="relationship-approval",
    )
    decision = replace(
        report,
        confirmed_at=datetime.now(UTC),
        confirmation_entity_id=report.live_entity_id,
        confirmation_original_entity_id=report.original_entity_id,
        confirmation_source="approved_action",
    )
    from butlers.modules.approvals.execution_context import approval_tool_args_digest

    event_id = await conn.fetchval(
        "SELECT event_id FROM approval_events WHERE action_id=$1 AND event_type='action_approved' "
        "ORDER BY occurred_at DESC,event_id DESC LIMIT 1",
        action_id,
    )
    if event_id is None:
        raise ValueError("admitted approval event is unavailable")
    decision_record = decision.to_record()
    decision_record.update(
        action_id=str(action_id),
        tool_args_digest=approval_tool_args_digest(args),
        approval_event_id=str(event_id),
    )
    await conn.execute(
        "UPDATE relationship.fact_approval_context SET decision_report=$2::jsonb "
        "WHERE action_id=$1 AND decision_report IS NULL",
        action_id,
        decision_record,
    )


async def record_admitted_rule(pool: Any, rule_id: uuid.UUID) -> None:
    """Owner permission lineage from the actual creation request, not actor text.

    If the creator is unavailable/legacy, the rule keeps its existing generic
    semantics but cannot provide new Relationship owner confirmation.
    """
    report = current_fact_write_context()
    if not report.owner_class:
        return
    from contextlib import asynccontextmanager

    from butlers.modules.approvals.execution_context import approval_tool_args_digest

    @asynccontextmanager
    async def connection_scope():
        if hasattr(pool, "acquire"):
            async with pool.acquire() as connection:
                yield connection
        else:
            yield pool

    async with connection_scope() as conn, conn.transaction():
        reachable = await conn.fetchval(
            "SELECT COALESCE((SELECT has_schema_privilege(oid,'USAGE') "
            "FROM pg_namespace WHERE nspname='relationship'),false)"
        )
        if not reachable or not await conn.fetchval(
            "SELECT to_regclass('relationship.fact_approval_rule_context') IS NOT NULL"
        ):
            # Generic/core-only approval storage remains usable. An optional
            # foreign or not-yet-installed hook cannot supply fact confirmation.
            return
        row = await conn.fetchrow("SELECT * FROM approval_rules WHERE id=$1", rule_id)
        if row is None or row["tool_name"] != "relationship_assert_fact":
            return
        owner = await conn.fetchval("SELECT id FROM public.entities WHERE 'owner'=ANY(roles)")
        if owner is None:
            raise PermissionError("no registered owner for rule permission")
        report = await locked_report(
            conn, report, subject=owner, object="", object_kind="literal", src="approval-rule"
        )
        event_id = await conn.fetchval(
            "SELECT event_id FROM approval_events WHERE rule_id=$1 AND event_type='rule_created' "
            "ORDER BY occurred_at DESC,event_id DESC LIMIT 1",
            rule_id,
        )
        if event_id is None:
            raise ValueError("owner rule creation event is unavailable")
        await conn.execute(
            "INSERT INTO relationship.fact_approval_rule_context "
            "(rule_id,rule_created_at,rule_args_digest,owner_report,creation_event_id) "
            "VALUES ($1,$2,$3,$4,$5) ON CONFLICT(rule_id) DO NOTHING",
            rule_id,
            row["created_at"],
            approval_tool_args_digest(row["arg_constraints"]),
            report.to_record(),
            str(event_id),
        )


async def admitted_rule_report(pool: Any, rule_id: uuid.UUID) -> FactWriteContext | None:
    from butlers.modules.approvals.execution_context import approval_tool_args_digest

    row = await pool.fetchrow(
        "SELECT r.created_at,r.arg_constraints,r.active,r.expires_at,r.max_uses,r.use_count, "
        "c.rule_created_at,c.rule_args_digest,c.owner_report "
        "FROM approval_rules r JOIN relationship.fact_approval_rule_context c ON c.rule_id=r.id "
        "JOIN approval_events e ON e.event_id::text=c.creation_event_id "
        "AND e.rule_id=r.id AND e.event_type='rule_created' "
        "WHERE r.id=$1 AND r.tool_name='relationship_assert_fact'",
        rule_id,
    )
    if (
        row is None
        or row["created_at"] != row["rule_created_at"]
        or not row["active"]
        or (row["expires_at"] and row["expires_at"] < datetime.now(UTC))
        or (row["max_uses"] is not None and row["use_count"] >= row["max_uses"])
        or approval_tool_args_digest(row["arg_constraints"]) != row["rule_args_digest"]
    ):
        return None
    report = FactWriteContext.from_record(row["owner_report"])
    return report if report.owner_class else None


async def approved_rule_confirmation(
    conn: Any, action_id: uuid.UUID, decision: dict[str, Any]
) -> FactWriteContext:
    """Bind the accepted standing permission to its real creation/action events.

    Rule eligibility is admitted before automatic execution. A later revocation
    does not retroactively invalidate an already admitted request, and this
    read does not reacquire the executor's pending-row lock on another checkout.
    """
    from butlers.modules.approvals.execution_context import approval_tool_args_digest
    from butlers.modules.approvals.rules import _args_match_constraints

    try:
        rule_id = uuid.UUID(decision["rule_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("fact confirmation has no recorded standing rule") from exc
    row = await conn.fetchrow(
        "SELECT a.tool_args,r.created_at,r.arg_constraints,"
        "c.rule_created_at,c.rule_args_digest,c.owner_report "
        "FROM pending_actions a JOIN approval_rules r ON r.id=a.approval_rule_id "
        "JOIN relationship.fact_approval_rule_context c ON c.rule_id=r.id "
        "JOIN approval_events creation ON creation.event_id::text=c.creation_event_id "
        "AND creation.rule_id=r.id AND creation.event_type='rule_created' "
        "WHERE a.id=$1 AND r.id=$2 AND a.tool_name='relationship_assert_fact' "
        "AND r.tool_name='relationship_assert_fact' "
        "AND EXISTS(SELECT 1 FROM approval_events approval WHERE approval.action_id=a.id "
        "AND approval.rule_id=r.id AND approval.event_type='action_auto_approved')",
        action_id,
        rule_id,
    )
    if (
        row is None
        or row["created_at"] != row["rule_created_at"]
        or approval_tool_args_digest(row["arg_constraints"]) != row["rule_args_digest"]
        or not _args_match_constraints(row["tool_args"], row["arg_constraints"])
    ):
        raise ValueError("fact confirmation does not match standing permission lineage")
    original_owner = FactWriteContext.from_record(row["owner_report"])
    report = FactWriteContext.from_record(decision)
    if (
        not original_owner.owner_class
        or report.confirmation_source != "standing_rule"
        or report.confirmed_at is None
        or report.confirmation_original_entity_id != original_owner.original_entity_id
    ):
        raise ValueError("fact confirmation does not match the admitted rule owner")
    return report


async def prepare_rule_assertion(
    pool: Any,
    action_id: uuid.UUID,
    tool_args: dict[str, Any],
    rule_id: uuid.UUID,
) -> dict[str, Any]:
    """Freeze a new automatically approved fact before its pending row exists."""
    from butlers.modules.approvals.execution_context import approval_tool_args_digest
    from butlers.tools.relationship.fact_temporal import WIRE_KEYS, normalize_request
    from butlers.tools.relationship.relationship_assert_fact import (
        _parked_arguments,
        _record_approval_context,
    )

    owner_report = await admitted_rule_report(pool, rule_id)
    if owner_report is None:
        raise PermissionError("standing rule has no admitted owner permission")
    report = current_fact_write_context()
    subject = uuid.UUID(str(tool_args["subject"]))
    object_kind = tool_args.get("object_kind", "literal")
    temporal = normalize_request(**{key: tool_args.get(key) for key in WIRE_KEYS})
    async with pool.acquire() as conn, conn.transaction():
        report = await locked_report(
            conn,
            report,
            subject=subject,
            object=tool_args["object"],
            object_kind=object_kind,
            src="relationship",
            extra_entity_ids={owner_report.live_entity_id}
            if owner_report.live_entity_id
            else set(),
        )
        from butlers.tools.relationship.fact_temporal import require_temporal_admission

        await require_temporal_admission(conn)
        parked = await _parked_arguments(
            conn,
            temporal,
            subject=subject,
            predicate=tool_args["predicate"],
            object=tool_args["object"],
            object_kind=object_kind,
            conf=tool_args.get("conf", 1.0),
            verified=report.verified,
            last_seen=None,
            weight=tool_args.get("weight"),
            primary=tool_args.get("primary"),
        )
        canonical_args = {**tool_args, **parked.tool_args, "approval_action_id": str(action_id)}
        canonical_args.pop("verified", None)
        await _record_approval_context(
            conn,
            action_id=action_id,
            src="relationship",
            observed_at=datetime.now(UTC),
            temporal_mode=temporal.mode,
            temporal_base_fact_id=parked.base_fact_id,
            fact_context=report,
        )
        decision = replace(
            owner_report,
            confirmation_entity_id=owner_report.live_entity_id,
            confirmation_original_entity_id=owner_report.original_entity_id,
            confirmed_at=datetime.now(UTC),
            confirmation_source="standing_rule",
        ).to_record()
        decision.update(
            action_id=str(action_id),
            tool_args_digest=approval_tool_args_digest(canonical_args),
            rule_id=str(rule_id),
        )
        await conn.execute(
            "UPDATE relationship.fact_approval_context SET decision_report=$2 WHERE action_id=$1",
            action_id,
            decision,
        )
    return canonical_args
