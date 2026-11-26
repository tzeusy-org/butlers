"""A move the radar proposes is an approvals card, never a dashboard write (bu-5ii4gj.11 R2b).

The owner's decision (bu-kf3n4i) retired dashboard event updates, so resolving an overlap by
moving an event parks one approvals card whose call is the exact ``calendar_update_event`` and
the owner approves it in the existing approvals surface. Two things bind it:

* the card's arguments digest, which the approvals executor checks on replay, and
* the provider etag the owner saw, kept OUTSIDE the call's arguments in a
  ``calendar_action_log`` row. When the approved call runs, the event's current etag must still
  equal it, otherwise the call declines ``changed_since_check`` and writes nothing.

An approved move is written once (never resent), so a 412 can only mean the event changed. The
card text names no event title: the event may be a third party's.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any

import asyncpg

from butlers.modules.approvals.command_contracts import RESOLUTION_MOVE_COMMAND
from butlers.modules.approvals.execution_context import get_approval_execution_context
from butlers.modules.approvals.park import park_pending_action
from butlers.modules.calendar_approval import calendar_approval_args

from .models import CalendarEvent

logger = logging.getLogger(__name__)

#: The ``calendar_action_log.action_type`` that binds a card to the etag the owner saw.
RESOLUTION_ACTION_TYPE = "resolution_move"
#: The ``status`` of an approved move that wrote nothing.
RESOLUTION_DECLINED = "resolution_declined"
#: How long the owner's card stays actionable.
RESOLUTION_CARD_TTL = timedelta(days=3)

RESOLUTION_DECLINED_TEXT = (
    "Move not applied: the event changed after the overlap was checked, so nothing was moved."
)
#: Fixed owner-facing text of an approved move that wrote nothing, by reason. ``error`` makes the
#: approvals executor record a failed execution, so the card never reads as executed.
RESOLUTION_DECLINED_TEXTS: dict[str, str] = {
    "changed_since_check": RESOLUTION_DECLINED_TEXT,
    "dependency_pending": (
        "Move waiting: an earlier step of this plan that frees this slot has not been applied "
        "yet. This step runs on its own once that step applies."
    ),
    "dependency_not_applied": (
        "Move not applied: an earlier step of this plan that frees this slot was not applied, so "
        "nothing was moved."
    ),
    "changed_since_move": (
        "Move not undone: the event changed after the plan moved it, so it was left as it is."
    ),
    "unbound": (
        "Move not applied: this request has no recorded check against the calendar, so nothing "
        "was moved."
    ),
}
#: The closed code a deferred move reports (the executor audits it as the failure reason). A
#: deferral is not a failure: the card stays approved and runs once its dependency settles.
DEFERRED_ERROR = "dependency_pending: waiting for an earlier step of this plan"
#: How long a dependent step may wait for its dependency (the card's own lifetime).
DEPENDENCY_WAIT = timedelta(hours=48)
#: Refusals that end a card for good: the gate is deterministic, so a retry meets the same one.
_FINAL_REFUSALS = frozenset(
    {"dependency_not_applied", "unbound", "changed_since_check", "changed_since_move"}
)
#: The request id prefix every parked resolution move carries.
RESOLUTION_REQUEST_PREFIX = "resolve:"

RESOLUTION_CARD_WHY = "You asked to resolve a calendar overlap by moving one event."


def resolution_request_id(pair_id: str, option_id: str, issue_digest: str) -> str:
    """Deterministic, bound to the digest the owner saw: a changed overlap is a new request."""
    key = f"{pair_id}\x00{option_id}\x00{issue_digest}"
    return RESOLUTION_REQUEST_PREFIX + hashlib.sha256(key.encode()).hexdigest()[:24]


def resolution_card_args(call_args: Mapping[str, Any], *, request_id: str) -> dict[str, Any]:
    """The card's canonical call, bound exactly as the executor replays it."""
    # ``fail``: the slot was free when it was compiled; if the calendar has since filled it the
    # move fails rather than creating a new overlap. The calendar must be named: the executor
    # replays the call with the calendar the owner was shown bound in.
    if not call_args.get("calendar_id"):
        raise ValueError("a resolution move must name its calendar")
    return calendar_approval_args(
        "calendar_update_event",
        **{**call_args, "request_id": request_id, "conflict_policy": "fail"},
    )


def _shown(value: Any, zone: tzinfo | None) -> str:
    """A move time as the owner reads it: owner-local when the zone is known, else as stored."""
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return str(value)
    if zone is None or moment.tzinfo is None:
        return str(value)
    return moment.astimezone(zone).strftime("%a %d %b %H:%M %Z")


def resolution_card_summary(call_args: Mapping[str, Any], zone: tzinfo | None = None) -> str:
    """What approving does. It never repeats an event title (it may be a third party's text)."""
    return (
        "Resolve an overlap: move one event to "
        f"{_shown(call_args['start_at'], zone)} - {_shown(call_args['end_at'], zone)}."
    )


def blast_radius_for(authority: str) -> str:
    """Other attendees are notified by a move; every other move stays with the owner."""
    return "contact" if authority == "parked_per_event" else "self"


_INSERT_SQL = """
INSERT INTO calendar_action_log (
    idempotency_key, action_type, action_status, origin_ref,
    action_payload, action_result, applied_at
) VALUES ($1, 'resolution_move', 'noop', $2, $3, $4, now())
ON CONFLICT (idempotency_key) DO NOTHING
"""


async def park_resolution_move(
    pool: Any,
    *,
    butler_name: str,
    pair_id: str,
    option_id: str,
    issue_digest: str,
    call_args: Mapping[str, Any],
    etag: str,
    authority: str,
    zone: tzinfo | None = None,
    series_event_id: str | None = None,
    depends_on: Sequence[uuid.UUID] = (),
    stale_reason: str | None = None,
    summary: str | None = None,
) -> tuple[uuid.UUID, bool]:
    """Park the move's card and bind it to ``etag``; returns ``(action_id, created)``.

    One transaction: the card and its binding commit together or not at all. A repeat of the
    same request (same pair, option and digest) returns the card already parked.
    """
    request_id = resolution_request_id(pair_id, option_id, issue_digest)
    args = RESOLUTION_MOVE_COMMAND.materialize(
        resolution_card_args(call_args, request_id=request_id)
    )
    now = datetime.now(UTC)
    async with pool.acquire() as conn, conn.transaction():
        admission = await park_pending_action(
            conn,
            action_id=uuid.uuid4(),
            tool_name="calendar_update_event",
            tool_args=args,
            agent_summary=summary or resolution_card_summary(call_args, zone),
            requested_at=now,
            expires_at=now + RESOLUTION_CARD_TTL,
            why=RESOLUTION_CARD_WHY,
            blast_radius=blast_radius_for(authority),
            reversibility="reversible",
            origin_butler=butler_name,
            deduplication_key=request_id,
        )
        await conn.execute(
            _INSERT_SQL,
            f"{RESOLUTION_ACTION_TYPE}:{request_id}",
            str(call_args["event_id"]),
            {"pair_id": pair_id, "option_id": option_id, "issue_digest": issue_digest},
            {
                "action_id": str(admission.action_id),
                "etag": etag,
                # Set for one occurrence of a series: ``etag`` is then the series' etag.
                **({"series_event_id": series_event_id} if series_event_id else {}),
                # A plan step that uses a slot another step vacates runs only if those applied.
                **({"depends_on": [str(a) for a in depends_on]} if depends_on else {}),
                # An undo declines ``changed_since_move``, not ``changed_since_check``.
                **({"stale_reason": stale_reason} if stale_reason else {}),
            },
        )
        return admission.action_id, not admission.duplicate


async def offer_for_card(pool: Any, action_id: uuid.UUID) -> dict[str, Any] | None:
    """The binding an approved card runs under, or ``None`` for any other approval."""
    try:
        row = await pool.fetchrow(
            "SELECT action_result FROM calendar_action_log "
            "WHERE action_type = 'resolution_move' AND action_result->>'action_id' = $1",
            str(action_id),
        )
        result = row["action_result"] if row is not None else None
        if isinstance(result, str):
            result = json.loads(result)
    except asyncpg.UndefinedTableError:
        return None
    except Exception:
        # An unreadable binding is no binding: a resolution request then declines ``unbound``,
        # and any other approved update is the ordinary update it always was.
        logger.warning("resolution move: binding could not be read", exc_info=True)
        return None
    if not isinstance(result, dict) or result.get("action_id") != str(action_id):
        return None
    return result


#: The ``result.status`` of an approved update or response that really changed the calendar.
_WROTE_STATUSES = frozenset({"updated", "applied", "noop"})  # noop: the response already stood


def _object(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _command_outcome(command: Any) -> tuple[str, str | None] | None:
    """The settled outcome of a response's coordinator command row, or ``None`` while in flight.

    ``command`` is ``{"action_status", "action_result", "phase"}``. The approvals executor leaves a
    card ``approved`` with no ``execution_result`` when the handler raised, so the command row,
    which the response coordinator finishes itself, is the only record of how that response ended.
    """
    row = _object(command)
    result = _object(row.get("action_result"))
    reported = result.get("status") or row.get("action_status")
    if reported in ("applied", "noop"):
        return "applied", None
    if reported == "failed":
        reason = result.get("reason")
        return "declined", reason if isinstance(reason, str) else "response_failed"
    if reported == "uncertain" or row.get("phase") == "uncertain":
        return "uncertain", "response_uncertain"
    return None


def refusal_code_of(failure_reason: str | None) -> str | None:
    """The closed refusal a move's failure audit reason names, else ``None``."""
    if not isinstance(failure_reason, str):
        return None
    if failure_reason.startswith("dependency_pending"):
        return "dependency_pending"
    for code, text in RESOLUTION_DECLINED_TEXTS.items():
        if failure_reason == text:
            return code
    return None


def card_outcome(
    status: str | None,
    execution_result: Any,
    command: Any = None,
    failure_reason: str | None = None,
) -> tuple[str, str | None]:
    """``(outcome, reason)`` of one approved card: ``applied``, ``pending``, ``declined`` or
    ``uncertain``.

    ``applied`` only when the executor ran it, succeeded, and the call itself reports that it
    wrote (``updated`` or ``applied``). A card still waiting is ``pending``; one the owner rejected
    or that expired, a move that declined (``resolution_declined``) and a move onto a busy slot
    (``conflict``, the card's own ``fail`` policy), a response the provider rejected (``failed``)
    and one whose event changed under it (``response_version_changed``) are ``declined`` with the
    reason; ``noop`` (the response already stood) counts as ``applied``. An ``approved`` card the
    executor never finished (its handler raised) is read off ``command``, the response's own
    action-log row: failed is ``declined`` with its reason, uncertain stays ``uncertain`` and
    anything unsettled is ``pending``. A card the handler failed on (``execution_failed``: the
    executor's ``action_execution_failed`` audit event exists and no result was recorded, which is
    how a move whose ``calendar_update_event`` returned an error ends) reads off its audit
    ``failure_reason``: a deferral (``dependency_pending``) is ``pending``, never a failure; a
    refusal that ends the card (``dependency_not_applied``, ``unbound``, ``changed_since_check``,
    ``changed_since_move``) is ``declined`` with that code; anything else is ``uncertain`` with the
    closed reason ``execution_failed``, never the error text. The executor still allows a retry, so
    the card is neither applied nor finally declined: ``uncertain`` is the honest read, a later
    success flips it to ``applied``, and dependents fail closed meanwhile (only ``applied``
    counts). Anything else, including an execution that failed or reports no status, is
    ``uncertain``: never ``applied``.
    """
    if status == "approved" and not _object(execution_result) and command is not None:
        settled = _command_outcome(command)
        if settled is not None:
            return settled
    if status == "approved" and not _object(execution_result) and failure_reason is not None:
        code = refusal_code_of(failure_reason)
        if code == "dependency_pending":
            return "pending", None
        if code in _FINAL_REFUSALS:
            return "declined", code
        return "uncertain", "execution_failed"
    if status in ("pending", "approved"):
        return "pending", None
    if status in ("rejected", "expired", "cancelled", "abandoned"):
        return "declined", f"card_{status}"
    result = _object(execution_result)
    if status != "executed":
        return "uncertain", "card_state_unknown"
    inner = _object(result.get("result"))
    reported = inner.get("status")
    if result.get("success") is True and reported in _WROTE_STATUSES:
        return "applied", None
    if reported == RESOLUTION_DECLINED:
        reason = inner.get("reason")
        return "declined", reason if isinstance(reason, str) else "move_not_applied"
    if reported == "conflict":
        return "declined", "destination_busy"
    if reported == "failed":
        reason = inner.get("reason")
        return "declined", reason if isinstance(reason, str) else "response_failed"
    return "uncertain", "execution_not_confirmed"


async def command_row_of(pool: Any, card_id: Any) -> dict[str, Any] | None:
    """The response coordinator's action-log row for an approved card (same id), or ``None``."""
    try:
        row = await pool.fetchrow(
            "SELECT action_status, action_result, action_payload->>'phase' AS phase "
            "FROM calendar_action_log WHERE id = $1",
            card_id if isinstance(card_id, uuid.UUID) else uuid.UUID(str(card_id)),
        )
    except Exception:
        logger.warning("resolution move: command row unreadable", exc_info=True)
        return None
    if row is None:
        return None
    return {
        "action_status": row["action_status"],
        "action_result": row["action_result"],
        "phase": row["phase"],
    }


async def failure_reason_of(pool: Any, card_id: Any) -> str | None:
    """The reason of the executor's latest ``action_execution_failed`` audit for this card.

    ``None`` when the executor audited no failure; an empty string when it did but kept no reason.
    """
    try:
        row = await pool.fetchrow(
            "SELECT reason FROM approval_events WHERE action_id = $1 "
            "AND event_type = 'action_execution_failed' ORDER BY occurred_at DESC LIMIT 1",
            card_id if isinstance(card_id, uuid.UUID) else uuid.UUID(str(card_id)),
        )
    except Exception:
        logger.warning("resolution move: failure audit unreadable", exc_info=True)
        return None
    if row is None:
        return None
    return row["reason"] or ""


async def dependency_wait_over(pool: Any, action_id: Any) -> bool:
    """Whether the card has already waited its whole lifetime (48 hours) for a dependency."""
    try:
        return bool(
            await pool.fetchval(
                "SELECT now() - requested_at > $2::interval FROM pending_actions WHERE id = $1",
                action_id if isinstance(action_id, uuid.UUID) else uuid.UUID(str(action_id)),
                DEPENDENCY_WAIT,
            )
        )
    except Exception:
        logger.warning("resolution move: card age unreadable", exc_info=True)
        return False


_DEFERRED_SQL = """
SELECT p.id FROM pending_actions p
JOIN calendar_action_log l
  ON l.action_type = 'resolution_move' AND l.action_result->>'action_id' = p.id::text
WHERE p.status = 'approved' AND p.execution_result IS NULL
  AND CASE WHEN jsonb_typeof(l.action_result->'depends_on') = 'array'
           THEN jsonb_array_length(l.action_result->'depends_on') ELSE 0 END > 0
  AND ($1::text IS NULL OR l.action_result->'depends_on' ? $1)
  AND (SELECT e.reason FROM approval_events e
        WHERE e.action_id = p.id AND e.event_type = 'action_execution_failed'
        ORDER BY e.occurred_at DESC LIMIT 1) LIKE 'dependency_pending%'
ORDER BY p.requested_at
LIMIT 50
"""


async def release_deferred(pool: Any, execute: Any, *, after: str | None = None) -> int:
    """Re-run approved moves that were deferred behind a dependency that has now settled.

    Only a card the owner already approved, still without a result and last deferred as
    ``dependency_pending``, is touched; ``execute(card_id)`` is the approvals executor's own retry
    (same lock, same gate, same bound call and etag), so nothing is recompiled or rebuilt. A
    dependency that applied lets the card write; one that was rejected, expired, declined or never
    applied makes the gate decline it ``dependency_not_applied``. A dependency still undecided
    leaves the card waiting, until its 48-hour lifetime ends. ``after`` limits the sweep to the
    dependents of one card. Returns how many cards were re-run.
    """
    try:
        rows = await pool.fetch(_DEFERRED_SQL, after)
    except Exception:
        logger.warning("resolution move: deferred cards unreadable", exc_info=True)
        return 0
    ran = 0
    for row in rows:
        card_id = row["id"]
        offer = await offer_for_card(pool, card_id)
        depends = offer.get("depends_on") if offer else None
        if not isinstance(depends, list) or not depends:
            continue
        state = await dependency_state(pool, depends)
        if state == "pending" and not await dependency_wait_over(pool, card_id):
            continue
        try:
            if await execute(card_id):
                ran += 1
        except Exception:
            logger.warning("resolution move: deferred card re-run failed", exc_info=True)
    return ran


async def dependency_state(pool: Any, action_ids: Sequence[str]) -> str:
    """``applied``, ``pending`` or ``failed`` for the cards a step depends on.

    ``applied`` only when every card was approved and really wrote. ``pending`` when none failed
    but some are still waiting for the owner. Anything unreadable, declined, uncertain, rejected
    or expired is ``failed``: it fails closed.
    """
    if not action_ids:
        return "applied"
    try:
        ids = [uuid.UUID(a) for a in action_ids]
        rows = await pool.fetch(
            "SELECT id, status, execution_result FROM pending_actions WHERE id = ANY($1::uuid[])",
            ids,
        )
    except Exception:
        logger.warning("resolution move: dependencies could not be read", exc_info=True)
        return "failed"
    if len(rows) != len(ids):
        return "failed"
    waiting = False
    for row in rows:
        command = None
        failed = None
        if row["status"] == "approved" and row.get("id") is not None:
            command = await command_row_of(pool, row["id"])
            failed = await failure_reason_of(pool, row["id"])
        outcome, _reason = card_outcome(row["status"], row["execution_result"], command, failed)
        if outcome == "pending":
            waiting = True
        elif outcome != "applied":
            return "failed"
    return "pending" if waiting else "applied"


async def record_applied_move(
    pool: Any, action_id: uuid.UUID, *, etag_after: str | None, pre_start: str, pre_end: str
) -> None:
    """Best effort: keep what an applied move left, so a plan's undo is bound to it.

    Only times and the new etag are stored, never event text. A failed write loses the undo for
    that step (it is then not offered), never the move.
    """
    if not etag_after:
        return
    try:
        await pool.execute(
            "UPDATE calendar_action_log SET action_result = action_result || $2::jsonb "
            "WHERE action_type = 'resolution_move' AND action_result->>'action_id' = $1",
            str(action_id),
            {"etag_after": etag_after, "pre_start": pre_start, "pre_end": pre_end},
        )
    except Exception:
        logger.warning("resolution move: etag_after could not be recorded", exc_info=True)


def resolution_refusal(offer: Mapping[str, Any], event: CalendarEvent) -> str | None:
    """``None`` when the move may write, else why it declines (nothing is written).

    An event with no etag, or one that differs from the version the owner saw, never writes.
    """
    bound = offer.get("etag")
    if isinstance(bound, str) and bound and event.etag == bound:
        return None
    reason = offer.get("stale_reason")
    return reason if reason in RESOLUTION_DECLINED_TEXTS else "changed_since_check"


@dataclass(frozen=True, slots=True)
class ResolutionCheck:
    """What an approved ``calendar_update_event`` owes the overlap resolution it carries out."""

    #: True when a binding was found: the write is sent once (never resent).
    bound: bool = False
    #: Why the approved move must not write (``changed_since_check`` or ``unbound``), else None.
    refusal: str | None = None


async def check_resolution_binding(
    module: Any,
    *,
    tool_name: str,
    tool_args: Mapping[str, Any],
    request_id: str | None,
    event: CalendarEvent,
    provider: Any = None,
    calendar_id: str | None = None,
) -> ResolutionCheck:
    """Decide an approved call against its recorded binding. Fails closed.

    Not an approval, or an approval that is not a ``resolve:`` request: no binding applies and the
    call is the ordinary update. An approved ``resolve:`` request with no recorded binding (no
    database, no row) declines ``unbound``: it must never run as a plain update. A bound request
    declines ``changed_since_check`` unless the event's etag is the one the owner saw. For one
    occurrence of a series the owner saw the series' etag, so that is the one read and compared;
    the write itself still carries the occurrence's own etag.
    """
    approval = get_approval_execution_context(tool_name=tool_name, tool_args=dict(tool_args))
    if approval is None:
        return ResolutionCheck()
    pool = getattr(getattr(module, "_db", None), "pool", None)
    offer = await offer_for_card(pool, approval.action_id) if pool is not None else None
    if offer is None:
        marked = isinstance(request_id, str) and request_id.startswith(RESOLUTION_REQUEST_PREFIX)
        return ResolutionCheck(refusal="unbound" if marked else None)
    depends = offer.get("depends_on")
    if isinstance(depends, list):
        state = await dependency_state(pool, depends)
        if state == "pending" and await dependency_wait_over(pool, approval.action_id):
            state = "failed"  # waited the card's whole lifetime: never run on a stale approval
        if state != "applied":
            # Not applied: nothing is written. A dependency still undecided defers the card (it
            # stays approved and runs once the dependency applies); one that was rejected,
            # expired, declined or waited too long ends it.
            reason = "dependency_pending" if state == "pending" else "dependency_not_applied"
            return ResolutionCheck(bound=True, refusal=reason)
    series_id = offer.get("series_event_id")
    if isinstance(series_id, str) and series_id:
        series: CalendarEvent | None
        try:
            series = await provider.get_event(calendar_id=calendar_id, event_id=series_id)
        except Exception:
            logger.warning("resolution move: series could not be re-read", exc_info=True)
            series = None
        if series is None:
            return ResolutionCheck(bound=True, refusal="changed_since_check")
        return ResolutionCheck(bound=True, refusal=resolution_refusal(offer, series))
    return ResolutionCheck(bound=True, refusal=resolution_refusal(offer, event))


def resolution_declined_result(reason: str, *, event_id: str, calendar_id: str) -> dict[str, Any]:
    return {
        "status": RESOLUTION_DECLINED,
        "reason": reason,
        "error": RESOLUTION_DECLINED_TEXTS[reason],
        "event_id": event_id,
        "calendar_id": calendar_id,
    }


__all__ = [
    "RESOLUTION_DECLINED_TEXTS",
    "RESOLUTION_REQUEST_PREFIX",
    "ResolutionCheck",
    "check_resolution_binding",
    "resolution_declined_result",
    "RESOLUTION_ACTION_TYPE",
    "RESOLUTION_CARD_TTL",
    "RESOLUTION_DECLINED",
    "RESOLUTION_DECLINED_TEXT",
    "offer_for_card",
    "park_resolution_move",
    "resolution_card_args",
    "blast_radius_for",
    "resolution_card_summary",
    "resolution_refusal",
    "resolution_request_id",
]
