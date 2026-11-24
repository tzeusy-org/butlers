"""Dark deterministic M7 delivery selection, on the S6 identity and receipt ledger.

Only authenticated surface adapters record activity. A focus heartbeat is evidence of
an active dashboard, never acknowledgement. Plans are immutable receipts per subject
generation; retries and process restarts read the same deadline. No model or provider
is called here. REQ-owner-delivery-006/007, bu-4c7nvp.1.8.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from butlers.core import owner_delivery as od
from butlers.perception import flags

FALLBACK_AFTER = timedelta(minutes=3)
FOCUS_FRESHNESS = timedelta(seconds=90)
INBOUND_FRESHNESS = timedelta(minutes=10)
Sensitivity = Literal["ordinary", "sensitive", "unknown"]


async def clock(conn: Any) -> datetime:
    """Database time in production; a controlled clock at the owning test seam."""
    return await conn.fetchval("SELECT clock_timestamp()")


@dataclass(frozen=True)
class SurfaceEvidence:
    surface: od.Surface
    source: Literal["dashboard_focus", "owner_inbound"]
    observed_at: datetime
    valid_until: datetime
    active: bool = True

    def live(self, now: datetime) -> bool:
        return self.active and self.observed_at <= now < self.valid_until


@dataclass(frozen=True)
class Selection:
    primary: od.Surface | None
    fallback: od.Surface | None
    reason: str


def select(
    *,
    kind: str,
    urgency: od.Urgency,
    sensitivity: Sensitivity,
    evidence: tuple[SurfaceEvidence, ...],
    now: datetime,
    origin: od.Surface | None = None,
    posture: Literal["available", "held", "unknown"] = "unknown",
) -> Selection:
    """Choose admissible surfaces; absence/staleness never asserts liveness or ack.

    Sensitive or unclassified content is confined to the authenticated dashboard.
    Answers keep their originating channel; an unsuitable origin holds the answer
    rather than silently redirecting it. Urgency is consumed, never re-derived.
    """
    if kind not in od.SUBJECT_KINDS or sensitivity not in {"ordinary", "sensitive", "unknown"}:
        raise od.OwnerDeliveryRefused("selection_input_invalid")
    telegram_allowed = sensitivity == "ordinary"
    if kind == "goal_answer":
        if origin == "dashboard" or (origin == "telegram" and telegram_allowed):
            return Selection(origin, None, "original_reply_target")
        return Selection(None, None, "reply_target_unavailable")
    if not telegram_allowed:
        return Selection("dashboard", None, "private_surface_required")
    if posture == "held" and urgency.reason != "home_safety":
        return Selection("dashboard", None, "owner_posture_held")
    desk = any(
        e.surface == "dashboard" and e.source == "dashboard_focus" and e.live(now) for e in evidence
    )
    if desk:
        return Selection("dashboard", "telegram", "desk_first")
    telegram_live = any(e.surface == "telegram" and e.live(now) for e in evidence)
    if telegram_live or urgency.cls == "interrupt":
        return Selection(
            "telegram",
            None,
            "verified_interrupt" if urgency.cls == "interrupt" else "owner_inbound",
        )
    return Selection("dashboard", None, "surface_liveness_unknown")


def _object(value: Any) -> dict[str, Any]:
    return json.loads(value) if isinstance(value, str) else dict(value)


async def activity(conn: Any, *, owner: UUID, surface: od.Surface, active: bool = True) -> None:
    """Record a server-timed authenticated adapter signal, without private content.

    API login only: runtime roles have SELECT, no write policy on this relation.
    """
    ttl = FOCUS_FRESHNESS if surface == "dashboard" else INBOUND_FRESHNESS
    await conn.execute(
        "INSERT INTO public.owner_delivery_surface_evidence "
        "(owner_entity, surface, source, observed_at, valid_until, active) "
        "VALUES ($1, $2, $3, clock_timestamp(), clock_timestamp() + $4::interval, $5) "
        "ON CONFLICT (owner_entity, surface) DO UPDATE SET "
        "observed_at = EXCLUDED.observed_at, valid_until = EXCLUDED.valid_until, "
        "active = EXCLUDED.active",
        owner,
        surface,
        "dashboard_focus" if surface == "dashboard" else "owner_inbound",
        ttl,
        active,
    )


async def read_evidence(conn: Any, owner: UUID) -> tuple[SurfaceEvidence, ...]:
    rows = await conn.fetch(
        "SELECT * FROM public.owner_delivery_surface_evidence WHERE owner_entity = $1 "
        "ORDER BY surface",
        owner,
    )
    return tuple(
        SurfaceEvidence(r["surface"], r["source"], r["observed_at"], r["valid_until"], r["active"])
        for r in rows
    )


async def plan(
    conn: Any,
    subject: od.Subject,
    *,
    sensitivity: Sensitivity,
    origin: od.Surface | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Persist/replay one plan under the canonical subject lock, never another identity."""
    current = await conn.fetchrow(
        "SELECT * FROM public.owner_delivery_subjects "
        "WHERE id = $1 AND owning_schema = current_schema() FOR UPDATE",
        subject.id,
    )
    if current is None:
        raise od.OwnerDeliveryRefused("subject_unavailable")
    if current["state"] in od.RESOLVED or current["state"] == "acknowledged":
        return {"primary": None, "fallback": None, "reason": "subject_acknowledged_or_resolved"}
    stored = await conn.fetchval(
        "SELECT detail FROM public.owner_delivery_receipts "
        "WHERE subject_id = $1 AND generation = $2 AND kind = 'selected'",
        subject.id,
        current["generation"],
    )
    if stored is not None:
        return _object(stored)
    moment = now or await clock(conn)
    evidence = await read_evidence(conn, current["owner_entity"])
    urgency = od.Urgency(current["urgency_class"] or "routine", current["urgency_reason"])
    selected = select(
        kind=current["kind"],
        urgency=urgency,
        sensitivity=sensitivity,
        evidence=evidence,
        now=moment,
        origin=origin,
    )
    result = {
        "version": 1,
        **asdict(selected),
        "selected_at": moment.isoformat(),
        "deadline": (moment + FALLBACK_AFTER).isoformat() if selected.fallback else None,
        "sensitivity": sensitivity,
        "urgency_class": urgency.cls,
        "urgency_reason": urgency.reason,
        "posture": "unknown" if current["holding_reason"] is None else "receipted",
        "evidence": [
            {
                **asdict(e),
                "observed_at": e.observed_at.isoformat(),
                "valid_until": e.valid_until.isoformat(),
            }
            for e in evidence
        ],
    }
    await od.append_receipt(conn, subject.id, "selected", current["generation"], detail=result)
    return result


async def telegram_due(
    conn: Any,
    subject: od.Subject,
    *,
    sensitivity: Sensitivity,
    now: datetime | None = None,
) -> bool:
    """Recheck ack and the durable deadline under the subject lock before provider handoff."""
    selection = await plan(conn, subject, sensitivity=sensitivity, now=now)
    if selection["primary"] == "telegram":
        return True
    if selection["fallback"] != "telegram":
        return False
    moment = now or await clock(conn)
    return moment >= datetime.fromisoformat(selection["deadline"])


async def prepare_presentation(
    conn: Any,
    subject: od.Subject,
    *,
    surface: od.Surface,
    route_ref: str,
    sensitivity: Sensitivity,
    actions: tuple[od.Action, ...],
    expires_at: datetime,
    origin: od.Surface | None = None,
    now: datetime | None = None,
) -> od.Presentation | None:
    """Canonical adapter for alerts/asks and answers, with no new queue or authority.

    Called in the source authority's transaction; the adapter supplies a verified route
    and labels. Provider adapters still use ``begin_send`` before a fake/real handoff.
    A non-selected surface produces nothing, including when its old subject was acked.
    """
    if not live() or not await od.rollout_enabled(conn):
        return None
    chosen = await plan(conn, subject, sensitivity=sensitivity, origin=origin, now=now)
    selected = chosen["primary"] == surface
    if surface == "telegram" and chosen.get("fallback") == "telegram":
        moment = now or await clock(conn)
        selected = moment >= datetime.fromisoformat(chosen["deadline"])
    if not selected:
        return None
    return await od.present(
        conn,
        subject.id,
        surface=surface,
        route_ref=route_ref,
        actions=actions,
        expires_at=expires_at,
    )


def action_sensitivity(action: Any) -> Sensitivity:
    """Use the checked-in tool descriptor, never a model's priority/category claim."""
    from butlers.core.tool_presentation_inventory import TOOL_PRESENTATION_BY_NAME

    descriptor = TOOL_PRESENTATION_BY_NAME.get(action.get("tool_name"))
    sensitivity = getattr(descriptor, "output_sensitivity", None)
    # Output classification alone cannot declassify arguments carrying private categories.
    if getattr(descriptor, "categories", None):
        return "sensitive"
    return sensitivity if sensitivity in {"ordinary", "sensitive"} else "unknown"


async def why(conn: Any, presentation_id: UUID, *, owner: UUID) -> dict[str, Any] | None:
    """Read a model-free receipt through an authenticated owner's presentation.

    No provider IDs, verifier, private location value, prompts or inferred health.
    Missing plan/effect/budget evidence stays explicitly unknown.
    """
    row = await conn.fetchrow(
        "SELECT s.*, p.generation AS presentation_generation, p.surface, p.state AS delivery_state "
        "FROM public.owner_delivery_presentations p "
        "JOIN public.owner_delivery_subjects s ON s.id = p.subject_id "
        "WHERE p.id = $1 AND s.owner_entity = $2 AND s.owning_schema = current_schema()",
        presentation_id,
        owner,
    )
    if row is None:
        return None
    raw = await conn.fetchval(
        "SELECT detail FROM public.owner_delivery_receipts "
        "WHERE subject_id = $1 AND generation = $2 AND kind = 'selected'",
        row["id"],
        row["presentation_generation"],
    )
    moment = await conn.fetchval("SELECT clock_timestamp()")
    selection = _object(raw) if raw is not None else None
    evidence = []
    for e in selection["evidence"] if selection else ():
        seen, until = (
            datetime.fromisoformat(e["observed_at"]),
            datetime.fromisoformat(e["valid_until"]),
        )
        evidence.append(
            {
                "source": e["source"],
                "surface": e["surface"],
                "age_seconds": max(0, (moment - seen).total_seconds()),
                "state": ("fresh" if e["active"] else "inactive")
                if seen <= moment < until
                else "stale",
            }
        )
    holding = row["holding_reason"]
    result = {
        "subject_id": str(row["id"]),
        "presentation_id": str(presentation_id),
        "generation": row["presentation_generation"],
        "current": row["presentation_generation"] == row["generation"]
        and row["state"] not in od.RESOLVED,
        "kind": row["kind"],
        "state": row["state"],
        "decision": row["decision"],
        "surface": row["surface"],
        "delivery_state": row["delivery_state"],
        "urgency_class": row["urgency_class"] or "routine",
        "urgency_reason": row["urgency_reason"],
        "cause": selection["reason"] if selection else "unknown",
        "deadline": selection["deadline"] if selection else None,
        "evidence": evidence,
        "holding_reason": _object(holding) if holding else None,
        "effect_ref": None,
        "effect_status": "unknown",
        "budget_status": "unknown",
        "origin": "unknown",
    }
    presentations = await conn.fetch(
        "SELECT surface, state, generation FROM public.owner_delivery_presentations "
        "WHERE subject_id = $1 ORDER BY generation, surface",
        row["id"],
    )
    result["presentations"] = [dict(p) for p in presentations]
    if row["kind"] == "approval":
        action = await conn.fetchrow(
            "SELECT status, goal_id, goal_origin FROM pending_actions WHERE id = $1",
            UUID(row["source_ref"]),
        )
        if action is not None:
            result["origin"] = action["goal_origin"] or "unknown"
            # Broker actions use the pending action's id as the effect identity.
            result["effect_ref"] = row["source_ref"] if action["goal_id"] else None
            result["effect_status"] = action["status"]
    return result


def live() -> bool:
    return flags.OWNER_DELIVERY_SELECTION_LIVE
