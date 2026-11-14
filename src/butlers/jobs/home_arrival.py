"""Home arrival card: one read-only timed card per confirmed enter edge (bu-jv1moa.116).

REQ-home-deterministic-jobs-010. When the owner gets home after an absence of at least
:data:`MIN_ABSENCE` for which Home had something to say (a departure card, a Home condition that
opened or resolved, or a parked fix), :func:`run_arrival_watch` records the decision in
``home.arrival_cards`` and queues one timed insight (dedup ``home:arrival:<edge id>``, deliverable
from confirmed + 2 minutes to + 30 minutes, premise probe ``("home", "arrival_card_current")``).

The card says, by the owner's labels and fixed words only:

* Home conditions that opened or resolved during the absence, with the recorded reason;
* departure-watch items that are still not secure now (or that Home cannot see);
* each parked fix with its resolver status (:mod:`butlers.jobs.home_prepared_status`);
* or, when a departure card existed and nothing changed, that nothing Home tracks changed and that
  Home only tracks what the owner declared.

Hard rules:

* Retracted, late or superseded (the owner left again) enter edges queue nothing, short absences
  and absences with nothing to report queue nothing, and each decision is recorded once.
* No Home Assistant service call, no button, no prepared action, no ``friendly_name`` and no
  coordinate. Condition summaries are never read: a condition is named by its source family.
* Nothing is inferred from Home Assistant history. An unhealthy HA source reads "cannot see".
* Routine priority on the timed lane; no urgency, no quiet-hours bypass beyond the lane's own
  confirmed-presence-edge rule (REQ-home-deterministic-jobs-012).

The Switchboard sends through the timed lane and checks the premise first through
``public.resolve_home_arrival`` (core_338): a closed card, or one whose lines changed since it
was queued, is withdrawn unsent; a card not rechecked for three minutes holds.

Dark: :data:`ARRIVAL_CARD_LIVE` is off and no roster schedules ``home_arrival_watch``. Activation
turns it on with the timed lane, the perception reader login and the presence reducer.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

import asyncpg

from butlers.core.delivery_rendering import defang_delivery_text
from butlers.jobs.home_departure import (
    CardLine,
    Edge,
    _clean,
    _current_lines,
    _reader_connection,
    load_watch,
    parse_edge,
)

logger = logging.getLogger(__name__)

#: Master switch. Off: the job returns ``{"status": "dark"}`` without reading or writing.
ARRIVAL_CARD_LIVE = False

#: The card is queued no earlier than this after the enter edge is confirmed...
ARRIVAL_DELAY = timedelta(minutes=2)
#: ...and is delivered before this after it, or not at all.
ARRIVAL_WINDOW = timedelta(minutes=30)
#: A shorter absence queues no card.
MIN_ABSENCE = timedelta(minutes=30)
#: Evidence rows are kept this long after their edge.
CARD_RETENTION = timedelta(days=7)

CARD_CATEGORY = "arrival"
_PRIORITY = 70
_MAX_LINES = 8
_SCAN_PAGE = 100
#: Heartbeats share the timeline; past this many pages the leave is not seen and no card is queued.
_MAX_SCANS = 30
_LOOKBACK = timedelta(days=30)

Closure = Literal["retracted", "late", "left_again"]
ReadEdges = Callable[[datetime, UUID], Awaitable[list[Edge] | None]]
ReadSubject = Callable[[asyncpg.Pool], Awaitable[UUID | None]]
#: Fix lines for one absence: ``(stable key, owner-facing text)``. Built by bu-jv1moa.115.
ReadFixes = Callable[[asyncpg.Pool, UUID], Awaitable[Sequence[CardLine]]]

#: Condition source family -> the words the card uses. Never the condition's own summary.
_SOURCE_WORDS = {
    "home:safety": "Safety alert",
    "home:freezer": "Freezer temperature",
    "home:away-door": "Door or window left open",
    "home:sensing-blind": "Home sensing was blind",
}
#: Recorded ``resolution_reason`` -> words. Anything else reads as plain "resolved".
_REASON_WORDS = {
    "owner_returned": "you came home",
    "sight_restored": "sensing came back",
    "premise_retracted": "the departure was retracted",
    "watch_removed": "the watch was removed",
}

_CONDITIONS_SQL = """
    SELECT source, state, first_detected_at, resolved_at,
           metadata ->> 'resolution_reason' AS reason
    FROM public.owner_conditions
    WHERE source LIKE 'home:%'
      AND ((first_detected_at >= $1 AND first_detected_at <= $2)
           OR (resolved_at >= $1 AND resolved_at <= $2))
    ORDER BY first_detected_at, source
    LIMIT 20
"""


@dataclass(frozen=True)
class ConditionRow:
    source: str
    state: str
    opened_at: datetime
    resolved_at: datetime | None
    reason: str | None


def closure(enter: Edge, edges: Sequence[Edge]) -> Closure | None:
    """Why ``enter`` must not produce a card, or ``None`` while the owner is still home."""
    if enter.retracted:
        return "retracted"
    if enter.late:
        return "late"
    for edge in edges:
        if (
            edge.direction == "leave"
            and not edge.retracted
            and not edge.late
            and edge.confirmed_at > enter.confirmed_at
        ):
            return "left_again"
    return None


def leave_before(enter: Edge, edges: Sequence[Edge]) -> Edge | None:
    """The latest usable leave confirmed before ``enter``, or ``None`` when none is in view."""
    candidates = [
        e
        for e in edges
        if e.direction == "leave"
        and not e.retracted
        and not e.late
        and e.confirmed_at < enter.confirmed_at
    ]
    return max(candidates, key=lambda e: e.confirmed_at) if candidates else None


def _hhmm(moment: datetime, tz: ZoneInfo) -> str:
    return moment.astimezone(tz).strftime("%H:%M")


def condition_line(row: ConditionRow, tz: ZoneInfo) -> CardLine:
    """One condition as a card line, from its source family and recorded times only."""
    name = _SOURCE_WORDS.get(row.source, "Home alert")
    opened = f"opened {_hhmm(row.opened_at, tz)}"
    if row.resolved_at is None:
        text = f"{name}: {opened}, still open"
        key = f"{row.source}=open"
    else:
        why = _REASON_WORDS.get(row.reason or "")
        resolved = f"resolved {_hhmm(row.resolved_at, tz)}" + (f" ({why})" if why else "")
        text = f"{name}: {opened}, {resolved}"
        key = f"{row.source}=resolved:{row.reason or ''}"
    return (key, text)


def arrival_message(lines: Sequence[CardLine], *, quiet: bool) -> str:
    """The owner-facing card: plain text, one line per thing."""
    if quiet:
        return (
            "Welcome home.\n"
            "Nothing Home tracks changed while you were away. "
            "Home only tracks what you declared."
        )
    shown = [text for _key, text in lines[:_MAX_LINES]]
    if len(lines) > _MAX_LINES:
        shown.append(f"+{len(lines) - _MAX_LINES} more")
    return "\n".join(["Welcome home. While you were away:", *shown])


def arrival_fingerprint(lines: Sequence[CardLine], *, quiet: bool) -> str:
    """What the card claims, as a stable digest; an outage's age does not change it."""
    keys = "\n".join(["quiet" if quiet else "lines", *sorted(key for key, _text in lines)])
    return hashlib.sha256(keys.encode()).hexdigest()[:32]


def _safe(text: str, limit: int) -> str:
    return defang_delivery_text(_clean(text, limit=limit))


def _defanged(lines: Sequence[CardLine]) -> tuple[CardLine, ...]:
    """Owner labels and HA states are plain text; defang them as the card is sent as written."""
    return tuple((key, _safe(text, 120)) for key, text in lines)


async def _scan(
    store: Any,
    subject: UUID,
    *,
    since: datetime,
    until: datetime,
    done: Callable[[list[Edge]], bool],
) -> list[Edge]:
    """Edges newest first, paging until ``done(edges)`` or the timeline or the page cap ends."""
    edges: list[Edge] = []
    before: UUID | None = None
    for _ in range(_MAX_SCANS):
        rows = await store.timeline(
            since=since,
            until=until,
            kind="owner.place",
            subject=subject,
            before=before,
            limit=_SCAN_PAGE,
        )
        for row in rows:
            before = UUID(str(row["id"]))
            edge = parse_edge(row)
            if edge is not None:
                edges.append(edge)
        if len(rows) < _SCAN_PAGE or done(edges):
            break
    return edges


async def read_arrival_edges(now: datetime, subject: UUID) -> list[Edge] | None:
    """Confirmed ``owner.place`` edges: the window first, then back to the leave before it.

    ``None`` without the perception reader login. When no enter edge is in the window the read
    stops after the window's pages, so an idle owner costs one cheap query a minute.
    """
    from butlers.perception import PerceptionStore

    connection = await _reader_connection()
    if connection is None:
        return None
    try:
        store = PerceptionStore(connection)
        floor = now - ARRIVAL_WINDOW
        recent = await _scan(
            store, subject, since=floor - timedelta(minutes=1), until=now, done=lambda _e: False
        )
        entered = [e for e in recent if e.direction == "enter" and e.confirmed_at >= floor]
        if not entered:
            return recent
        oldest = min(e.confirmed_at for e in entered)

        def found_leave(edges: list[Edge]) -> bool:
            return any(
                e.direction == "leave" and not e.retracted and e.confirmed_at < oldest
                for e in edges
            )

        return await _scan(store, subject, since=now - _LOOKBACK, until=now, done=found_leave)
    finally:
        await connection.close()


async def _owner_subject(pool: asyncpg.Pool) -> UUID | None:
    rows = await pool.fetch("SELECT id FROM public.entities WHERE 'owner' = ANY(roles)")
    return rows[0]["id"] if len(rows) == 1 else None


async def _no_fixes(pool: asyncpg.Pool, leave_id: UUID) -> Sequence[CardLine]:
    return ()


async def _conditions(
    pool: asyncpg.Pool, leave: Edge, enter: Edge, tz: ZoneInfo
) -> tuple[CardLine, ...]:
    rows = await pool.fetch(_CONDITIONS_SQL, leave.confirmed_at, enter.confirmed_at)
    return tuple(
        condition_line(
            ConditionRow(
                r["source"], r["state"], r["first_detected_at"], r["resolved_at"], r["reason"]
            ),
            tz,
        )
        for r in rows
    )


async def _timezone(pool: asyncpg.Pool) -> ZoneInfo:
    from butlers.core.general_settings import resolve_general_timezone

    try:
        return ZoneInfo(await resolve_general_timezone(pool))
    except (KeyError, ValueError):
        return ZoneInfo("UTC")


async def _build(
    pool: asyncpg.Pool,
    leave: Edge,
    enter: Edge,
    *,
    now: datetime,
    tz: ZoneInfo,
    read_fixes: ReadFixes,
) -> tuple[tuple[CardLine, ...], bool] | None:
    """The card lines and whether it is the quiet card, or ``None`` when there is nothing to say."""
    had_card = bool(
        await pool.fetchval("SELECT 1 FROM departure_cards WHERE edge_event_id = $1", leave.id)
    )
    changes = await _conditions(pool, leave, enter, tz)
    fixes = tuple(await read_fixes(pool, leave.id))
    if not (had_card or changes or fixes):
        return None
    still: tuple[CardLine, ...] = ()
    if had_card:
        watch = await load_watch(pool)
        if watch:
            still = await _current_lines(pool, watch, now)
    # Conditions are fixed words and times; labels and HA states in the rest are defanged.
    lines = (*changes, *_defanged((*fixes, *still)))
    return lines, not lines


async def _propose(pool: asyncpg.Pool, enter: Edge, message: str, now: datetime) -> str:
    from butlers.core.insight_premise import probe_premise
    from butlers.roster_imports import roster_module

    broker = roster_module("switchboard", "tools/insight/broker")
    result = await broker.propose_insight_candidate(
        pool,
        origin_butler="home",
        priority=_PRIORITY,
        category=CARD_CATEGORY,
        dedup_key=f"home:arrival:{enter.id}",
        message=message,
        expires_at=enter.confirmed_at + ARRIVAL_WINDOW,
        lane="timed",
        deliver_after=max(enter.confirmed_at + ARRIVAL_DELAY, now),
        premise=probe_premise(
            "home",
            "arrival_card_current",
            {"edge_event_id": str(enter.id), "confirmed_at": enter.confirmed_at.isoformat()},
        ),
        now=now,
    )
    status = result.get("status", "error")
    if status not in ("accepted", "duplicate"):
        logger.warning("home arrival: card not queued (%s)", result.get("reason", status))
    return status


async def _record(
    pool: asyncpg.Pool,
    enter: Edge,
    leave: Edge | None,
    *,
    reason: str | None,
    fingerprint: str | None,
    now: datetime,
) -> bool:
    inserted = await pool.fetchval(
        "INSERT INTO arrival_cards (edge_event_id, leave_event_id, confirmed_at, state, "
        "closed_reason, proposed_fingerprint, fingerprint, checked_at) "
        "VALUES ($1, $2, $3, $4, $5, $6, $6, $7) "
        "ON CONFLICT (edge_event_id) DO NOTHING RETURNING edge_event_id",
        enter.id,
        leave.id if leave else None,
        enter.confirmed_at,
        "closed" if reason else "open",
        reason,
        None if reason else fingerprint,
        now,
    )
    return inserted is not None


async def run_arrival_watch(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None = None,
    *,
    read_edges: ReadEdges | None = None,
    read_subject: ReadSubject | None = None,
    read_fixes: ReadFixes | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Record or recheck the card for each recent enter edge; queue a new open one."""
    del job_args
    if not ARRIVAL_CARD_LIVE:
        return {"status": "dark"}
    now = now or datetime.now(UTC)
    subject = await (read_subject or _owner_subject)(pool)
    if subject is None:
        return {"status": "no_owner"}
    try:
        edges = await (read_edges or read_arrival_edges)(now, subject)
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError):
        logger.warning("home arrival: presence timeline read failed")
        edges = None
    if edges is None:
        return {"status": "reader_unavailable"}
    # Retention runs only after a successful read: without the reader the job changes no card.
    await pool.execute("DELETE FROM arrival_cards WHERE confirmed_at < $1", now - CARD_RETENTION)

    enters = [
        e
        for e in edges
        if e.direction == "enter" and now - ARRIVAL_WINDOW <= e.confirmed_at <= now - ARRIVAL_DELAY
    ]
    counts = {"proposed": 0, "rechecked": 0, "closed": 0}
    if not enters:
        return {"status": "ok", **counts}
    tz = await _timezone(pool)
    fixes_reader = read_fixes or _no_fixes
    known = {
        r["edge_event_id"]: (r["state"], r["proposed_fingerprint"])
        for r in await pool.fetch(
            "SELECT edge_event_id, state, proposed_fingerprint FROM arrival_cards "
            "WHERE edge_event_id = ANY($1)",
            [e.id for e in enters],
        )
    }
    for enter in enters:
        leave = leave_before(enter, edges)
        reason: str | None = closure(enter, edges)
        built: tuple[tuple[CardLine, ...], bool] | None = None
        if reason is None and leave is None:
            reason = "no_leave"
        elif reason is None and leave is not None:
            if enter.confirmed_at - leave.confirmed_at < MIN_ABSENCE:
                reason = "short_absence"
            else:
                built = await _build(pool, leave, enter, now=now, tz=tz, read_fixes=fixes_reader)
                if built is None:
                    reason = "nothing_to_report"
        fingerprint: str | None = None
        if built is not None:
            fingerprint = arrival_fingerprint(built[0], quiet=built[1])
        if reason is None and enter.id in known and known[enter.id][1] != fingerprint:
            # The queued text no longer matches: the definer withdraws it, and the evidence row
            # says so without recording either version of the lines.
            reason = "withdrawn_changed"
        counts["closed"] += int(reason is not None)
        if enter.id in known:
            if known[enter.id][0] == "open":
                counts["rechecked"] += 1
                await pool.execute(
                    "UPDATE arrival_cards SET state = $2, closed_reason = $3, "
                    "fingerprint = $4, checked_at = $5 "
                    "WHERE edge_event_id = $1 AND state = 'open'",
                    enter.id,
                    "closed" if reason else "open",
                    reason,
                    None if reason else fingerprint,
                    now,
                )
            continue
        recorded = await _record(
            pool, enter, leave, reason=reason, fingerprint=fingerprint, now=now
        )
        if not recorded or reason is not None or built is None:
            continue
        status = await _propose(pool, enter, arrival_message(built[0], quiet=built[1]), now)
        if status == "accepted":
            counts["proposed"] += 1
        elif status != "duplicate":
            await pool.execute(
                "UPDATE arrival_cards SET state = 'closed', closed_reason = 'not_queued' "
                "WHERE edge_event_id = $1",
                enter.id,
            )
    return {"status": "ok", **counts}
