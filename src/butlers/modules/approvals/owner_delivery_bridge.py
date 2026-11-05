"""Approvals side of canonical owner delivery (bu-dhloi1.13, RFC 0038 S6).

The approval action stays the authority row; :mod:`butlers.core.owner_delivery` holds its
one subject, presentations and single-use tokens. This module is the explicit bridge from
the RFC 0023 stores: it opens the subject for an action, records the old-store id -> subject
map (append-only), backfills already-delivered actions at cutover, and reads parity between
the two stores. It never writes RFC 0023 rows; their writer is unchanged until the switch.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from butlers.core import owner_delivery as od
from butlers.modules.approvals.previews import args_digest

#: Card actions minted for an approval presentation. ``ack`` exists in the ledger for
#: later surfaces; no approval card offers it.
APPROVAL_ACTIONS: tuple[od.Action, ...] = ("approve", "reject")
#: Bound for a token whose action has no expiry of its own.
_DEFAULT_TOKEN_TTL = timedelta(hours=72)
_DASHBOARD_ROUTE = "owner:dashboard"

#: ``pending_actions.status`` -> the subject state parity expects.
_EXPECTED_SUBJECT = {
    "pending": None,  # any open state
    "approved": ("decided", "approve"),
    "executed": ("decided", "approve"),
    "abandoned": ("decided", "approve"),
    "rejected": ("decided", "reject"),
    "expired": ("expired", None),
    "cancelled": ("cancelled", None),
}
#: RFC 0023 presentation state -> the owner-delivery presentation state it bridges to.
RFC0023_STATE_MAP = {
    "ready": "pending",
    "claimed": "pending",
    "retry_wait": "pending",
    "handoff_started": "unknown",
    "ambiguous": "unknown",
    "delivered": "sent",
    "cancelled": "retracted",
    "superseded": "retracted",
    "collapsed": "retracted",
}


def _tool_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        raw = json.loads(raw)
    return dict(raw) if isinstance(raw, Mapping) else {}


def approval_digests(tool_name: str, tool_args: Any) -> tuple[str, str]:
    """``(content_digest, action_digest)``; the action digest is the card's args digest."""
    args = _tool_args(tool_args)
    return od.digest_of({"tool_name": tool_name, "tool_args": args}), args_digest(args)


async def open_approval_subject(conn: Any, action: Mapping[str, Any]) -> od.Subject:
    """Open (or return) the subject of one locked ``pending_actions`` row."""
    owner = await od.owner_entity_id(conn)
    if owner is None:
        raise od.OwnerDeliveryRefused("owner_unavailable")
    content, action_digest = approval_digests(action["tool_name"], action["tool_args"])
    goal_effect = action.get("goal_id")
    return await od.open_subject(
        conn,
        kind="approval",
        source_ref=str(action["id"]),
        owner_entity=owner,
        content_digest=content,
        action_digest=action_digest,
        goal_effect_id=goal_effect if isinstance(goal_effect, uuid.UUID) else None,
    )


def token_expiry(action: Mapping[str, Any], now: datetime) -> datetime:
    expires_at = action.get("expires_at")
    if isinstance(expires_at, datetime) and expires_at > now:
        return expires_at
    return now + _DEFAULT_TOKEN_TTL


async def _map(
    conn: Any,
    *,
    store: str,
    source_id: str,
    subject_id: uuid.UUID,
    presentation_id: uuid.UUID | None = None,
) -> bool:
    tag = await conn.execute(
        "INSERT INTO public.owner_delivery_source_map "
        "(owning_schema, source_store, source_id, subject_id, presentation_id) "
        "VALUES (current_schema(), $1, $2, $3, $4) ON CONFLICT DO NOTHING",
        store,
        source_id,
        subject_id,
        presentation_id,
    )
    return str(tag).endswith(" 1")


def rfc0023_presentation_ref(presentation_id: Any, generation: int) -> str:
    return f"{presentation_id}:{generation}"


async def prepare_telegram_presentation(
    conn: Any,
    *,
    action: Mapping[str, Any],
    intent_id: uuid.UUID,
    rfc_presentation_id: uuid.UUID,
    rfc_generation: int,
    owner_recipient: str,
) -> od.Presentation:
    """Open the subject and its Telegram presentation for one fenced RFC 0023 claim.

    Call with the action row locked. The RFC 0023 claim is mapped to the owner-delivery
    presentation so the handoff marker and result settle the same presentation.
    """
    subject = await open_approval_subject(conn, action)
    now = await conn.fetchval("SELECT clock_timestamp()")
    legacy = rfc0023_presentation_ref(rfc_presentation_id, rfc_generation)
    presentation = await od.present(
        conn,
        subject.id,
        surface="telegram",
        route_ref=owner_recipient,
        actions=APPROVAL_ACTIONS,
        expires_at=token_expiry(action, now),
        legacy_ref=legacy,
    )
    await _map(conn, store="approval_action", source_id=str(action["id"]), subject_id=subject.id)
    await _map(conn, store="rfc0023_intent", source_id=str(intent_id), subject_id=subject.id)
    if await _map(
        conn,
        store="rfc0023_presentation",
        source_id=legacy,
        subject_id=subject.id,
        presentation_id=presentation.id,
    ):
        await od.append_receipt(
            conn,
            subject.id,
            "bridged",
            presentation.generation,
            presentation_id=presentation.id,
            detail={"store": "rfc0023_presentation"},
        )
    return presentation


async def owner_presentation_for(
    conn: Any, rfc_presentation_id: Any, rfc_generation: int
) -> uuid.UUID | None:
    """The owner-delivery presentation an RFC 0023 claim bridged to, if any."""
    value = await conn.fetchval(
        "SELECT presentation_id FROM public.owner_delivery_source_map "
        "WHERE owning_schema = current_schema() AND source_store = 'rfc0023_presentation' "
        "AND source_id = $1",
        rfc0023_presentation_ref(rfc_presentation_id, rfc_generation),
    )
    return value if isinstance(value, uuid.UUID) else None


async def present_on_dashboard(conn: Any, action: Mapping[str, Any]) -> od.Presentation | None:
    """The dashboard presentation of a pending action with fresh tokens, when enabled."""
    if action.get("status") != "pending" or not await od.rollout_enabled(conn):
        return None
    subject = await open_approval_subject(conn, action)
    now = await conn.fetchval("SELECT clock_timestamp()")
    presentation = await od.present(
        conn,
        subject.id,
        surface="dashboard",
        route_ref=_DASHBOARD_ROUTE,
        actions=APPROVAL_ACTIONS,
        expires_at=token_expiry(action, now),
    )
    await _map(conn, store="approval_action", source_id=str(action["id"]), subject_id=subject.id)
    return presentation


@dataclass(frozen=True, slots=True)
class BridgeReport:
    subjects_opened: int = 0
    presentations_bridged: int = 0
    mapped: int = 0


async def bridge_existing(conn: Any, *, owner_recipient: str | None) -> BridgeReport:
    """Backfill every pending, already-presented action of this schema (cutover step).

    Each action gets its subject; the current RFC 0023 single presentation becomes the
    generation-1 Telegram presentation with its old state and provider reference, and
    older generations and attempts map to the subject without a presentation. No token
    is minted: a backfilled card keeps its legacy ``apr1`` buttons, whose decision still
    runs through ``transition_pending_action`` and so closes the subject. Idempotent.

    ``owner_recipient`` is the trusted owner route the caller resolved (the same one the
    delivery worker uses); without it cards map to the subject but are not presentations,
    so nothing can later try to retract them.
    """
    owner = await od.owner_entity_id(conn)
    if owner is None:
        raise od.OwnerDeliveryRefused("owner_unavailable")
    opened = bridged = mapped = 0
    actions = await conn.fetch(
        "SELECT pa.*, i.id AS intent_id FROM pending_actions pa "
        "JOIN approval_delivery_intents i ON i.action_id = pa.id "
        "WHERE pa.status = 'pending' ORDER BY pa.requested_at, pa.id FOR UPDATE OF pa"
    )
    recipient = owner_recipient.strip() if isinstance(owner_recipient, str) else None
    for action in actions:
        existing = await od.subject_for_source(conn, kind="approval", source_ref=str(action["id"]))
        subject = await open_approval_subject(conn, action)
        opened += int(existing is None)
        mapped += await _map(
            conn, store="approval_action", source_id=str(action["id"]), subject_id=subject.id
        )
        mapped += await _map(
            conn, store="rfc0023_intent", source_id=str(action["intent_id"]), subject_id=subject.id
        )
        presentations = await conn.fetch(
            "SELECT id, presentation_generation, presentation_mode, state "
            "FROM approval_delivery_presentations WHERE intent_id = $1 "
            "ORDER BY presentation_generation DESC, created_at DESC",
            action["intent_id"],
        )
        current_bridged = False
        for rfc in presentations:
            legacy = rfc0023_presentation_ref(rfc["id"], rfc["presentation_generation"])
            target: uuid.UUID | None = None
            if (
                not current_bridged
                and rfc["presentation_mode"] == "single"
                and subject.generation == 1
                and recipient
            ):
                current_bridged = True
                target = await _bridge_presentation_row(
                    conn, subject, rfc, legacy=legacy, route_ref=recipient
                )
                bridged += int(target is not None)
            mapped += await _map(
                conn,
                store="rfc0023_presentation",
                source_id=legacy,
                subject_id=subject.id,
                presentation_id=target,
            )
            attempts = await conn.fetch(
                "SELECT attempt_number FROM approval_delivery_attempts "
                "WHERE presentation_id = $1 AND presentation_generation = $2",
                rfc["id"],
                rfc["presentation_generation"],
            )
            for attempt in attempts:
                mapped += await _map(
                    conn,
                    store="rfc0023_attempt",
                    source_id=f"{legacy}:{attempt['attempt_number']}",
                    subject_id=subject.id,
                )
    return BridgeReport(opened, bridged, mapped)


async def _bridge_presentation_row(
    conn: Any, subject: od.Subject, rfc: Mapping[str, Any], *, legacy: str, route_ref: str
) -> uuid.UUID | None:
    state = RFC0023_STATE_MAP.get(rfc["state"])
    if state is None or state == "pending":
        return None
    provider_ref = await conn.fetchval(
        "SELECT provider_reference FROM approval_delivery_attempts "
        "WHERE presentation_id = $1 AND presentation_generation = $2 "
        "AND outcome = 'confirmed' AND provider_reference IS NOT NULL "
        "ORDER BY attempt_number DESC LIMIT 1",
        rfc["id"],
        rfc["presentation_generation"],
    )
    row = await conn.fetchrow(
        "INSERT INTO public.owner_delivery_presentations "
        "(id, subject_id, owning_schema, generation, surface, route_ref, retry_key, "
        " provider_ref, state, legacy_ref) "
        "VALUES ($1, $2, current_schema(), 1, 'telegram', $3, $2::text || ':telegram:1', "
        " $4, $5, $6) ON CONFLICT (subject_id, surface, generation) DO NOTHING RETURNING id",
        uuid.uuid4(),
        subject.id,
        route_ref,
        provider_ref,
        state,
        legacy,
    )
    if row is None:
        return None
    await od.append_receipt(
        conn,
        subject.id,
        "bridged",
        1,
        presentation_id=row["id"],
        detail={"store": "rfc0023_presentation", "state": state},
    )
    if state == "sent" and subject.state in ("pending", "unknown"):
        await conn.execute(
            "UPDATE public.owner_delivery_subjects SET state = 'delivered' WHERE id = $1",
            subject.id,
        )
    return row["id"]


@dataclass(frozen=True, slots=True)
class ParityReport:
    """Old-store versus subject agreement for this schema; ``ok`` gates the switch."""

    checked: int
    mismatches: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.mismatches


async def parity(conn: Any) -> ParityReport:
    """Compare every subject with its approval action, and every delivered card with a map."""
    rows = await conn.fetch(
        "SELECT s.source_ref, s.state, s.decision, pa.status, pa.tool_name, pa.tool_args "
        "FROM public.owner_delivery_subjects s "
        "LEFT JOIN pending_actions pa ON pa.id::text = s.source_ref "
        "WHERE s.owning_schema = current_schema() AND s.kind = 'approval'"
    )
    mismatches: list[tuple[str, str]] = []
    for row in rows:
        ref = row["source_ref"]
        if row["status"] is None:
            mismatches.append((ref, "action_missing"))
            continue
        expected = _EXPECTED_SUBJECT.get(row["status"], ("?", None))
        if expected is None:
            if row["state"] in od.RESOLVED:
                mismatches.append((ref, "subject_resolved_while_pending"))
        elif (row["state"], row["decision"]) != expected:
            mismatches.append((ref, f"state:{row['status']}->{row['state']}"))
    unmapped = await conn.fetch(
        "SELECT i.action_id FROM approval_delivery_presentations p "
        "JOIN approval_delivery_intents i ON i.id = p.intent_id "
        "JOIN pending_actions pa ON pa.id = i.action_id "
        "WHERE pa.status = 'pending' AND p.state = 'delivered' "
        "AND NOT EXISTS (SELECT 1 FROM public.owner_delivery_source_map m "
        "  WHERE m.owning_schema = current_schema() "
        "  AND m.source_store = 'rfc0023_presentation' "
        "  AND m.source_id = p.id::text || ':' || p.presentation_generation::text)"
    )
    mismatches.extend((str(row["action_id"]), "presentation_unmapped") for row in unmapped)
    return ParityReport(len(rows), tuple(mismatches))


__all__ = [
    "APPROVAL_ACTIONS",
    "BridgeReport",
    "ParityReport",
    "RFC0023_STATE_MAP",
    "approval_digests",
    "bridge_existing",
    "open_approval_subject",
    "owner_presentation_for",
    "parity",
    "prepare_telegram_presentation",
    "present_on_dashboard",
    "rfc0023_presentation_ref",
    "token_expiry",
]
