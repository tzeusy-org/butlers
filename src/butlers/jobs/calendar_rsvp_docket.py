"""The invitation docket job: owner-written RSVP policies prepare exact answers (bu-5ii4gj.9 S3).

Deterministic and model-free. For each pending invitation the owner has not answered, the job
evaluates the owner's policies (``public.calendar_rsvp_policies``, written only by the dashboard)
against verified facts, and parks one per-event approvals card for the matched answer. It never
sends anything: the card is the exact ``calendar_respond`` call, bound to the event's etag, and
waits for the owner to approve it. No standing rule auto-applies ``calendar_respond``
(REQ-calendar-invitation-docket-003).

Where it runs and why
---------------------
The docket belongs to the Relationship butler, whose calendar module holds the response fence. The
facts need the whole fleet's calendar (focus blocks and accepted events live in other butlers'
schemas), which only the dashboard process's ``DatabaseManager`` reads, so the loop runs there, like
the overcommitment and agreement-drift jobs. It reads the policies through Relationship's own pool
(the one role granted SELECT, core_389) and parks each card by calling Relationship's gated
``calendar_respond`` tool, the same path the owner's respond route uses, so ``prepare_and_park`` and
the fence stay in Relationship. It refuses to run when Relationship is not a configured response
owner, and prepares nothing for an entry another butler owns (REQ-calendar-invitation-docket-008).

Idempotence and supersession
----------------------------
The request id is a digest of the event, occurrence, etag, policy id, policy version, verdict and
``send_updates`` (REQ-calendar-invitation-docket-003), so a rerun parks nothing new and any change
to one of them parks a fresh card. In the same run the job re-evaluates every open docket card and
rejects, through the approvals system reject with a closed reason and its receipt kept, each
one that is no longer current: ``stale_event``, ``policy_changed``, ``policy_revoked`` or
``policy_no_longer_matches`` (REQ-calendar-invitation-docket-004). A system rejection is not a
decision by the owner and is never counted as one.

Outage
------
A read that could not be completed prepares nothing and rejects nothing. A card is never rejected on
facts that could not be read, and each invitation whose card could not be parked is counted as
``unprepared`` with a reason, so an outage never looks like a clean docket
(REQ-calendar-invitation-docket-007).

Dark: :data:`DOCKET_LIVE_ENV` must be ``1`` for the dashboard to start the loop; activation is an
owner-approved change.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import uuid
from collections import Counter
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from butlers.api.butler_tool_calls import tool_result_payload
from butlers.api.db import DatabaseManager
from butlers.api.read_models.calendar_workspace import (
    query_calendar_invitations,
    query_owner_calendar_rows,
)
from butlers.config import list_butlers
from butlers.core.job_health import mark_tick_incomplete, record_tick
from butlers.core.temporal.calendar_provenance import FOCUS_TIME, OUT_OF_OFFICE
from butlers.core.temporal.rsvp_policy import (
    Evaluation,
    InvitationFacts,
    Policy,
    PolicyError,
    PolicyMatch,
    evaluate_policies,
)
from butlers.core.temporal.scheduling import SchedulingPreferences, get_scheduling_preferences
from butlers.modules.approvals.delivery_lifecycle import transition_pending_action
from butlers.modules.approvals.models import ActionStatus

logger = logging.getLogger(__name__)

DOCKET_BUTLER = "relationship"
DOCKET_LIVE_ENV = "BUTLERS_CALENDAR_RSVP_DOCKET"
DEFAULT_CHECK_INTERVAL_S = 900.0
DEFAULT_LOOKAHEAD_DAYS = 14
DOCKET_ACTOR = "system:rsvp_docket"
PARK_TIMEOUT_S = 30.0
INVITATION_LIMIT = 500
_DECIDED_REPLAYS = frozenset({"approved", "rejected", "executed", "abandoned", "expired"})
_LOCK_KEY = "butlers:calendar_rsvp_docket"
MARKER_PREFIX = "rsvp-docket:"
REJECT_REASONS = ("stale_event", "policy_changed", "policy_revoked", "policy_no_longer_matches")
_NAMESPACE = uuid.UUID("5b1e0c0e-3f4a-4d0e-9a52-6d0c5c1f7a11")


def docket_butler() -> str:
    """The one butler the docket acts for: the owner of the primary calendar's response fence."""
    return DOCKET_BUTLER


def docket_live() -> bool:
    return os.environ.get(DOCKET_LIVE_ENV) == "1"


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def docket_request_id(
    *,
    event_id: str,
    occurrence: str,
    etag: str | None,
    policy_id: str,
    policy_version: int,
    verdict: str,
    send_updates: str,
) -> str:
    """The card's request id: a digest of everything that makes the prepared answer what it is."""
    key = _digest(
        {
            "event": event_id,
            "occurrence": occurrence,
            "etag": etag,
            "policy": policy_id,
            "version": policy_version,
            "verdict": verdict,
            "send_updates": send_updates,
        }
    )
    return str(uuid.uuid5(_NAMESPACE, key))


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------


def _metadata(value: Any) -> Mapping[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value if isinstance(value, Mapping) else {}


def _self_response(metadata: Mapping[str, Any]) -> str | None:
    attendees = metadata.get("attendees")
    if not isinstance(attendees, list):
        return None
    for attendee in attendees:
        if isinstance(attendee, Mapping) and attendee.get("self") in (True, "true"):
            raw = attendee.get("response_status", attendee.get("responseStatus"))
            return raw.strip().lower() if isinstance(raw, str) else None
    return None


def _attendee_count(metadata: Mapping[str, Any]) -> int | None:
    attendees = metadata.get("attendees")
    return len(attendees) if isinstance(attendees, list) else None


def _overlaps(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    return (
        a["instance_starts_at"] < b["instance_ends_at"]
        and b["instance_starts_at"] < a["instance_ends_at"]
    )


def _is_accepted_opaque(row: Mapping[str, Any]) -> bool:
    """An event holding the owner's time: opaque, not a declared status, accepted or their own."""
    metadata = _metadata(row.get("event_metadata"))
    if str(metadata.get("transparency", "")).strip().lower() == "transparent":
        return False
    if row.get("event_type") in (FOCUS_TIME, OUT_OF_OFFICE, "workingLocation"):
        return False
    response = _self_response(metadata)
    return response in (None, "accepted")


def overlap_facts(
    row: Mapping[str, Any], owner_rows: Iterable[Mapping[str, Any]]
) -> tuple[bool, bool]:
    """``(overlaps a focus or out-of-office block, conflicts with an accepted opaque event)``."""
    focus = conflicts = False
    for other in owner_rows:
        if other["instance_id"] == row["instance_id"] or not _overlaps(row, other):
            continue
        if other.get("instance_status") in ("cancelled", "canceled"):
            continue
        if other.get("event_type") in (FOCUS_TIME, OUT_OF_OFFICE):
            focus = True
        elif _is_accepted_opaque(other):
            conflicts = True
    return focus, conflicts


def invitation_facts(
    row: Mapping[str, Any],
    *,
    now: datetime,
    owner_rows: Sequence[Mapping[str, Any]] | None,
    outside_hours: bool | None,
    organizer_roles: frozenset[str],
) -> InvitationFacts:
    """Verified facts about one invitation. A fact that could not be read is ``None``."""
    metadata = _metadata(row.get("event_metadata"))
    verdict = metadata.get("organizer_verdict")
    start = row["instance_starts_at"]
    if owner_rows is None:
        focus: bool | None = None
        conflicts: bool | None = None
    else:
        focus, conflicts = overlap_facts(row, owner_rows)
    return InvitationFacts(
        organizer_verdict=verdict if isinstance(verdict, str) else None,
        organizer_roles=organizer_roles,
        overlaps_focus_or_out_of_office=focus,
        conflicts_with_accepted=conflicts,
        outside_declared_hours=outside_hours,
        recurring=bool(row.get("recurrence_rule")),
        attendee_count=_attendee_count(metadata),
        lead_time_hours=max(0, int((start - now).total_seconds() // 3600)),
    )


# ---------------------------------------------------------------------------
# The card
# ---------------------------------------------------------------------------


def _fact_note(kind: str, expected: Any, observed: Any, op: str | None) -> str:
    comparison = {"lte": "at most", "gte": "at least"}.get(op or "", "is")
    return f"policy wants {comparison} {expected}; observed {observed}"


def build_dossier(
    match: PolicyMatch, *, request_id: str, entry_id: str, etag: str | None
) -> dict[str, Any]:
    """The server-built decision dossier: the policy, its version and every matched fact.

    The first evidence item is the docket marker: it carries the exact inputs the card was prepared
    from, so a later run can tell why a card stopped being current without reading any event text.
    """
    marker = {
        "request_id": request_id,
        "entry_id": entry_id,
        "policy_id": match.policy_id,
        "policy_version": match.version,
        "verdict": match.verdict,
        "send_updates": match.send_updates,
        "etag": etag,
    }
    evidence = [{"type": "text", "ref": MARKER_PREFIX + request_id, "note": json.dumps(marker)}]
    evidence.extend(
        {
            "type": "text",
            "ref": f"rsvp-fact:{fact.kind}",
            "note": _fact_note(fact.kind, fact.expected, fact.observed, fact.op),
        }
        for fact in match.matched
    )
    why = (
        f"Your policy {match.policy_id} (version {match.version}) prepared this answer: "
        f"{match.verdict}, sending updates: {match.send_updates}. "
        "Nothing is sent until you approve."
    )
    return {
        "_why": why,
        "_evidence": evidence,
        "_blast_radius": "self",
        "_reversibility": "compensable",
    }


def parse_marker(evidence: Any) -> dict[str, Any] | None:
    """The docket marker of a parked card's evidence, or ``None`` when it is not a docket card."""
    if isinstance(evidence, str):
        try:
            evidence = json.loads(evidence)
        except (ValueError, TypeError):
            return None
    if not isinstance(evidence, list):
        return None
    for item in evidence:
        if isinstance(item, Mapping) and str(item.get("ref", "")).startswith(MARKER_PREFIX):
            try:
                marker = json.loads(item.get("note", ""))
            except (ValueError, TypeError):
                return None
            return marker if isinstance(marker, dict) else None
    return None


def supersede_reason(
    marker: Mapping[str, Any],
    *,
    evaluation: Evaluation | None,
    policy_revoked: bool,
    etag: str | None,
) -> str | None:
    """Why an open docket card is no longer current, or ``None`` while it still is.

    ``evaluation`` is ``None`` when the event is gone or no longer a pending invitation.
    """
    if evaluation is None:
        return "stale_event"
    if etag != marker.get("etag"):
        return "stale_event"
    if policy_revoked:
        return "policy_revoked"
    prepared = evaluation.prepared
    if prepared is None:
        return "policy_no_longer_matches"
    if (prepared.policy_id, prepared.version, prepared.verdict, prepared.send_updates) != (
        marker.get("policy_id"),
        marker.get("policy_version"),
        marker.get("verdict"),
        marker.get("send_updates"),
    ):
        return "policy_changed"
    return None


def fact_independent_reason(
    marker: Mapping[str, Any], *, etag: str | None, policy_revoked: bool, policy_version: int | None
) -> str | None:
    """Why a card is stale whatever the calendar facts say, or ``None``.

    These need no fact that can be unreadable: the event's etag changed, or the card's own policy
    was revoked or re-versioned. A degraded tick rejects only for these.
    """
    if etag != marker.get("etag"):
        return "stale_event"
    if policy_revoked:
        return "policy_revoked"
    if policy_version is not None and policy_version != marker.get("policy_version"):
        return "policy_changed"
    return None


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def docket_butler_configured() -> bool:
    """Whether the docket butler is a configured response owner (a refusal to run otherwise)."""
    try:
        configs = list_butlers()
    except Exception:
        return False
    for config in configs:
        if config.name != docket_butler():
            continue
        calendar = config.modules.get("calendar") or {}
        approval = config.modules.get("approvals") or {}
        return (
            calendar.get("response_enabled") is True
            and approval.get("enabled") is True
            and "calendar_respond" in (approval.get("gated_tools") or {})
        )
    return False


async def read_policies(pool: Any) -> list[Policy] | None:
    """Every policy, or ``None`` when the store could not be read or a row is outside the set."""
    try:
        rows = await pool.fetch(
            "SELECT id, position, predicates, verdict, send_updates, version, revoked_at "
            "FROM public.calendar_rsvp_policies ORDER BY position, id"
        )
        return [Policy.from_row(dict(row)) for row in rows]
    except (PolicyError, Exception):  # noqa: BLE001 - an unreadable store is an outage, not a docket
        logger.warning("rsvp docket: policies unreadable", exc_info=True)
        return None


async def read_organizer_roles(pool: Any, organizer: str | None) -> frozenset[str] | None:
    """The owner-known entity roles of an organizer's email, or ``None`` when unreadable."""
    if not organizer:
        return frozenset()
    try:
        rows = await pool.fetch(
            "SELECT e.roles FROM entity_facts f "
            "JOIN public.entities e ON e.id = f.subject "
            "WHERE f.predicate = 'has-email' AND f.validity = 'active' "
            "AND lower(btrim(f.object)) = lower(btrim($1)) "
            "AND e.metadata ->> 'merged_into' IS NULL AND e.metadata ->> 'deleted_at' IS NULL",
            organizer,
        )
    except Exception:  # noqa: BLE001
        logger.warning("rsvp docket: organizer roles unreadable", exc_info=True)
        return None
    roles: set[str] = set()
    for row in rows:
        roles.update(str(r) for r in (row["roles"] or ()))
    return frozenset(roles)


async def _open_cards(pool: Any) -> list[dict[str, Any]] | None:
    try:
        rows = await pool.fetch(
            "SELECT id, tool_args, evidence FROM pending_actions "
            "WHERE tool_name = 'calendar_respond' AND status = 'pending'"
        )
    except Exception:  # noqa: BLE001
        logger.warning("rsvp docket: open cards unreadable", exc_info=True)
        return None
    cards = []
    for row in rows:
        marker = parse_marker(row["evidence"])
        if marker is not None:
            cards.append({"id": row["id"], "marker": marker})
    return cards


async def _reject(pool: Any, action_id: Any, reason: str) -> bool:
    transition = await transition_pending_action(
        pool,
        action_id=action_id if isinstance(action_id, uuid.UUID) else uuid.UUID(str(action_id)),
        target_status=ActionStatus.REJECTED,
        decided_by=DOCKET_ACTOR,
        event_actor=DOCKET_ACTOR,
        event_reason=reason,
        event_metadata={"tool_name": "calendar_respond", "docket": True},
    )
    return bool(transition.changed)


async def _park(mgr: Any, arguments: dict[str, Any], dossier: dict[str, Any]) -> dict[str, Any]:
    """Park one card through Relationship's gated ``calendar_respond``; never raises."""
    try:
        async with asyncio.timeout(PARK_TIMEOUT_S):
            client = await mgr.get_client(DOCKET_BUTLER)
            result = await client.call_tool("calendar_respond", {**arguments, **dossier})
        parsed = tool_result_payload(result)
    except Exception:  # noqa: BLE001 - a failure or a timeout is counted unprepared, never clean
        return {"error": "response_transport_unavailable"}
    return parsed if isinstance(parsed, dict) else {"error": "response_preparation_unavailable"}


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


@contextlib.asynccontextmanager
async def _single_flight(pool: Any) -> AsyncIterator[bool]:
    """Hold the docket's transaction advisory lock for one tick; ``False`` when another has it.

    The lock is transaction-scoped on one pooled connection, so it spans every dashboard process
    and a restart overlap, and a cancelled or failed tick rolls back and releases it: a session
    lock could stay held on a connection the pool hands back.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            got = await conn.fetchval(
                "SELECT pg_try_advisory_xact_lock(hashtextextended($1, 0))", _LOCK_KEY
            )
            yield bool(got)


async def run_calendar_rsvp_docket(
    db: DatabaseManager,
    mgr: Any,
    *,
    now: datetime | None = None,
    lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS,
) -> dict[str, Any]:
    """One single-flight pass: a tick that finds another running returns having done nothing."""
    try:
        pool = db.pool(DOCKET_BUTLER)
    except Exception:  # noqa: BLE001
        return await _run(db, mgr, now=now, lookahead_days=lookahead_days)
    ran = False
    try:
        async with _single_flight(pool) as acquired:
            if not acquired:
                return {"status": "another_tick_running", "complete": True, "prepared": 0}
            ran = True
            return await _run(db, mgr, now=now, lookahead_days=lookahead_days)
    except Exception:  # noqa: BLE001 - an outage is counted, never a clean docket
        logger.warning("rsvp docket: tick failed (ran=%s)", ran, exc_info=True)
        status = "run_failed" if ran else "lock_unavailable"
        return {"status": status, "complete": False, "prepared": 0}


async def _run(
    db: DatabaseManager,
    mgr: Any,
    *,
    now: datetime | None,
    lookahead_days: int,
) -> dict[str, Any]:
    """One pass. Returns a summary; ``complete`` is False whenever any read or park failed."""
    now = now or datetime.now(UTC)
    summary: dict[str, Any] = {
        "status": "ok",
        "complete": True,
        "prepared": 0,
        "already_parked": 0,
        "owner_decided": 0,
        "disagreed": [],
        "no_match": 0,
        "unprepared": Counter(),
        "rejected": Counter(),
    }

    def incomplete(status: str) -> dict[str, Any]:
        summary["status"] = status
        summary["complete"] = False
        return summary

    if not docket_butler_configured():
        return incomplete("docket_butler_not_configured")
    try:
        pool = db.pool(DOCKET_BUTLER)
    except Exception:  # noqa: BLE001
        return incomplete("docket_butler_unavailable")

    policies = await read_policies(pool)
    if policies is None:
        return incomplete("policies_unavailable")
    open_cards = await _open_cards(pool)
    if open_cards is None:
        return incomplete("open_cards_unavailable")

    end = now + timedelta(days=lookahead_days)
    read = await query_calendar_invitations(db, start=now, end=end, limit=INVITATION_LIMIT)
    if not read.status_available or read.failed_butlers:
        # An invitation we could not see might be answered or moved: prepare and reject nothing.
        summary["unprepared"]["invitations_unavailable"] += 1
        return incomplete("invitations_unavailable")
    truncated = len(read.rows) >= INVITATION_LIMIT

    owner_rows, rows_available = await query_owner_calendar_rows(db, start=now, end=end)
    degraded: list[str] = []
    if truncated:
        degraded.append("invitations_truncated")
    if not rows_available:
        degraded.append("calendar")
    outside_known = True
    prefs = None
    try:
        # TODO(bu-5ii4gj.9 S4): read through load_scheduling_preferences once #6226 lands.
        prefs = SchedulingPreferences.from_row(await get_scheduling_preferences(pool))
    except Exception:  # noqa: BLE001
        outside_known = False
        degraded.append("declared_hours")
    # A degraded tick prepares nothing new and rejects only for fact-independent reasons.
    tick_degraded = bool(degraded)
    if tick_degraded:
        summary["degraded"] = degraded
        summary["complete"] = False

    # entry id -> (etag, evaluation, readable): readable means every fact for it was read.
    current: dict[str, tuple[str | None, Evaluation | None, bool]] = {}
    for row in read.rows:
        entry_id = str(row["instance_id"])
        if row.get("butler_name") != docket_butler() or row.get("writable") is not True:
            continue  # another butler's entry: the docket never prepares an answer for it
        etag = row.get("event_etag")
        if tick_degraded:
            current[entry_id] = (etag, None, False)
            summary["unprepared"]["facts_unreadable"] += 1
            continue
        metadata = _metadata(row.get("event_metadata"))
        organizer = metadata.get("organizer")
        roles = await read_organizer_roles(pool, organizer if isinstance(organizer, str) else None)
        if roles is None:
            current[entry_id] = (etag, None, False)
            summary["unprepared"]["organizer_unreadable"] += 1
            summary["complete"] = False
            continue
        if not outside_known or prefs is None or not prefs.has_constraints:
            outside: bool | None = False if outside_known else None
        else:
            outside = not prefs.allows(row["instance_starts_at"], row["instance_ends_at"])
        facts = invitation_facts(
            row,
            now=now,
            owner_rows=owner_rows,
            outside_hours=outside,
            organizer_roles=roles,
        )
        evaluation = evaluate_policies(policies, facts)
        current[entry_id] = (etag, evaluation, True)

        if evaluation.status == "disagree":
            summary["disagreed"].append({"entry_id": entry_id, "policies": evaluation.disagreeing})
            continue
        match = evaluation.prepared
        if match is None:
            summary["no_match"] += 1
            continue
        request_id = docket_request_id(
            event_id=str(row["event_id"]),
            occurrence=row["instance_starts_at"].isoformat(),
            etag=etag,
            policy_id=match.policy_id,
            policy_version=match.version,
            verdict=match.verdict,
            send_updates=match.send_updates,
        )
        if any(card["marker"].get("request_id") == request_id for card in open_cards):
            summary["already_parked"] += 1
            continue
        result = await _park(
            mgr,
            {
                "entry_id": entry_id,
                "response_status": match.verdict,
                "request_id": request_id,
                "send_updates": match.send_updates,
            },
            build_dossier(match, request_id=request_id, entry_id=entry_id, etag=etag),
        )
        if result.get("status") == "pending_approval":
            summary["prepared"] += 1
        elif result.get("error"):
            summary["unprepared"][str(result["error"])] += 1
            summary["complete"] = False
        elif result.get("status") in _DECIDED_REPLAYS:
            summary["owner_decided"] += 1  # a replay of a card the owner already settled
        else:
            summary["already_parked"] += 1  # a replayed command: it already exists

    # Re-evaluate every open docket card. An entry whose facts could not be read keeps its card
    # unless a fact-independent reason applies, and is counted unprepared.
    by_policy = {p.id: p for p in policies}
    for card in open_cards:
        marker = card["marker"]
        entry_id = str(marker.get("entry_id"))
        seen = current.get(entry_id)
        policy = by_policy.get(str(marker.get("policy_id")))
        revoked = policy is not None and policy.revoked_at is not None
        version = policy.version if policy is not None else None
        if seen is None:
            # No longer a pending invitation (answered, moved or cancelled). Rejecting it needs the
            # owner's own row to tell an answer from a move, so an unreadable calendar keeps it.
            if truncated or not rows_available:
                summary["unprepared"]["card_kept_unreadable"] += 1
                summary["complete"] = False
                continue
            reason: str | None = "stale_event"
        else:
            etag, evaluation, readable = seen
            reason = fact_independent_reason(
                marker, etag=etag, policy_revoked=revoked, policy_version=version
            )
            if reason is None and not readable:
                summary["unprepared"]["card_kept_unreadable"] += 1
                continue
            if reason is None:
                reason = supersede_reason(
                    marker, evaluation=evaluation, policy_revoked=revoked, etag=etag
                )
        if reason is None:
            continue
        if await _reject(pool, card["id"], reason):
            summary["rejected"][reason] += 1
    return summary


async def run_calendar_rsvp_docket_loop(
    db: DatabaseManager,
    mgr_factory: Any,
    *,
    interval_s: float = DEFAULT_CHECK_INTERVAL_S,
    lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS,
) -> None:
    """Run :func:`run_calendar_rsvp_docket` every ``interval_s`` until cancelled."""
    if interval_s <= 0:
        raise ValueError(f"interval_s must be a positive number, got {interval_s!r}")
    while True:
        await asyncio.sleep(interval_s)
        try:
            summary = await run_calendar_rsvp_docket(
                db, mgr_factory(), lookahead_days=lookahead_days
            )
            if not summary["complete"]:
                mark_tick_incomplete()
            await record_tick(failed=False)
            logger.info("calendar_rsvp_docket: %s", summary)
        except asyncio.CancelledError:
            raise
        except Exception:
            await record_tick(failed=True)
            logger.exception("calendar_rsvp_docket: tick failed")
