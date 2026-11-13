"""Home departure card: one read-only timed card per confirmed leave edge (bu-jv1moa.8).

The owner declares what to check when they leave in ``home.departure_watch`` (entity, an
owner-written ``owner_label`` and the states that count as secure).  Every minute
:func:`run_departure_watch`:

1. reads the confirmed ``owner.place`` leave/enter edges of the last 40 minutes from the
   perception timeline, as the private owner reader (``BUTLERS_PERCEPTION_READER_DSN``; without
   it the job does nothing and says ``reader_unavailable``);
2. reads the watched entities from ``ha_entity_snapshot`` once, only when the HA source is
   confirmed healthy, and builds the card lines;
3. for each leave confirmed 2 to 30 minutes ago records or rechecks ``home.departure_cards``
   and, the first time it sees an edge that is still open and has something to say, queues one
   timed insight (dedup ``home:departure:<edge id>``, window until confirmed + 30 min, premise
   probe ``("home", "departure_still_open")``).

The Switchboard sends it through the timed lane and checks the premise first through
``public.resolve_home_departure`` (core_335): a closed card or a card whose lines changed since
it was queued is withdrawn, never sent stale; a card not rechecked for three minutes holds.

Hard rules:

* Retracted, late or re-entered edges, and an all-secure home, queue nothing.
* An unhealthy HA source says "cannot see" with the age of the last good reading; an
  unreadable entity says "<label>: cannot see".  Neither is ever reported as secure.
* Only the owner's labels name things; HA's ``friendly_name`` is never read.  No coordinates.
* Read-only: no HA service call, no buttons and no prepared action.

Dark: ``DEPARTURE_CARD_LIVE`` is off and no roster schedules ``home_departure_watch``.
Activation flips it together with the timed lane, the perception reader login and the presence
reducer schedule, in one owner-approved change.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

import asyncpg

logger = logging.getLogger(__name__)

#: Master switch. Off: the job returns ``{"status": "dark"}`` without reading or writing.
DEPARTURE_CARD_LIVE = False

#: The card is queued no earlier than this after the leave edge is confirmed...
DEPARTURE_DELAY = timedelta(minutes=2)
#: ...and is delivered before this after it, or not at all.
DEPARTURE_WINDOW = timedelta(minutes=30)
#: Evidence rows are kept this long after their edge.
CARD_RETENTION = timedelta(days=7)

PERCEPTION_READER_DSN_ENV = "BUTLERS_PERCEPTION_READER_DSN"
_LOOKBACK = timedelta(minutes=40)
_SCAN_PAGE = 100
_MAX_SCANS = 3
_PRIORITY = 80
_UNREADABLE_STATES = frozenset({"", "unavailable", "unknown"})
_MARKDOWN_CHARS = re.compile(r"[*_`\[\]\x00-\x1f\x7f]")

Direction = Literal["leave", "enter"]
Closure = Literal["retracted", "late", "reentered"]
#: One card line: a stable key (what the line claims) and the text the owner reads.
CardLine = tuple[str, str]
ReadEdges = Callable[[datetime], Awaitable[list["Edge"] | None]]


@dataclass(frozen=True)
class Edge:
    """One confirmed owner.place presence edge from the perception timeline."""

    id: UUID
    direction: Direction
    confirmed_at: datetime
    late: bool
    retracted: bool


@dataclass(frozen=True)
class WatchItem:
    """One thing the owner wants checked on departure."""

    entity_id: str
    owner_label: str
    secure_states: tuple[str, ...]


def parse_edge(item: Mapping[str, Any]) -> Edge | None:
    """A timeline row as an edge, or ``None`` for heartbeats, resets and retraction records."""
    if item.get("operation") != "assert" or item.get("class") != "transition":
        return None
    before, after = item.get("from_value"), item.get("to_value")
    if not isinstance(before, dict) or not isinstance(after, dict):
        return None
    if before.get("presence") == after.get("presence"):
        return None
    return Edge(
        id=UUID(str(item["id"])),
        direction="leave" if after.get("presence") == "outside" else "enter",
        confirmed_at=datetime.fromisoformat(str(item["confirmed_at"])),
        late=bool(item.get("late")),
        retracted=bool(item.get("retracted")),
    )


def closure(leave: Edge, edges: Sequence[Edge]) -> Closure | None:
    """Why ``leave`` must not produce a card, or ``None`` while the owner is still out."""
    if leave.retracted:
        return "retracted"
    if leave.late:
        return "late"
    for edge in edges:
        if (
            edge.direction == "enter"
            and not edge.retracted
            and edge.confirmed_at > leave.confirmed_at
        ):
            return "reentered"
    return None


def _clean(text: str, *, limit: int) -> str:
    return _MARKDOWN_CHARS.sub("", text).strip()[:limit]


def card_lines(
    watch: Sequence[WatchItem],
    readings: Mapping[str, str | None] | None,
    *,
    ha_last_success: datetime | None = None,
    now: datetime,
) -> tuple[CardLine, ...]:
    """The card's lines: every watched item not in a secure state, by owner label.

    ``readings`` is ``None`` when the HA source is not confirmed healthy: the card then says it
    cannot see the home, with the age of the last good reading.  An entity missing from the
    snapshot or reading ``unavailable``/``unknown`` is "cannot see", never secure.  An empty
    result means everything is secure.
    """
    if readings is None:
        if ha_last_success is None:
            age = "no good reading yet"
        else:
            minutes = max(0, int((now - ha_last_success).total_seconds() // 60))
            age = f"last good reading {minutes} min ago"
        return (("ha=unmeasurable", f"Home: cannot see ({age})"),)
    lines: list[CardLine] = []
    for item in watch:
        label = _clean(item.owner_label, limit=80) or "Watched item"
        raw = readings.get(item.entity_id)
        state = (raw or "").strip().lower()
        if state in _UNREADABLE_STATES:
            lines.append((f"{item.entity_id}=unreadable", f"{label}: cannot see"))
        elif state not in item.secure_states:
            lines.append((f"{item.entity_id}={state}", f"{label}: {_clean(state, limit=32)}"))
    return tuple(lines)


def card_message(lines: Sequence[CardLine]) -> str:
    """The owner-facing card: plain text, one line per item."""
    return "\n".join(["You left home.", *(text for _key, text in lines)])


def card_fingerprint(lines: Sequence[CardLine]) -> str:
    """What the card claims, as a stable digest; an outage's age does not change it."""
    keys = "\n".join(sorted(key for key, _text in lines))
    return hashlib.sha256(keys.encode()).hexdigest()[:32]


async def load_watch(pool: asyncpg.Pool) -> list[WatchItem]:
    rows = await pool.fetch(
        "SELECT entity_id, owner_label, secure_states FROM departure_watch ORDER BY entity_id"
    )
    return [
        WatchItem(r["entity_id"], r["owner_label"], tuple(s.lower() for s in r["secure_states"]))
        for r in rows
    ]


async def read_presence_edges(now: datetime) -> list[Edge] | None:
    """Confirmed owner.place edges of the last 40 minutes, or ``None`` without the reader login."""
    from butlers.db import register_jsonb_codec
    from butlers.perception import PerceptionStore

    dsn = (os.environ.get(PERCEPTION_READER_DSN_ENV) or "").strip()
    if not dsn:
        return None
    connection = await asyncpg.connect(dsn, command_timeout=15)
    try:
        await register_jsonb_codec(connection)
        store = PerceptionStore(connection)
        edges: list[Edge] = []
        before: UUID | None = None
        for _ in range(_MAX_SCANS):
            rows = await store.timeline(
                since=now - _LOOKBACK,
                until=now,
                kind="owner.place",
                before=before,
                limit=_SCAN_PAGE,
            )
            for row in rows:
                before = UUID(str(row["id"]))
                edge = parse_edge(row)
                if edge is not None:
                    edges.append(edge)
            if len(rows) < _SCAN_PAGE:
                break
        return edges
    finally:
        await connection.close()


async def _current_lines(
    pool: asyncpg.Pool, watch: Sequence[WatchItem], now: datetime
) -> tuple[CardLine, ...]:
    from butlers.jobs.home import HASourceUnmeasurableError, _require_ha_source_healthy

    try:
        await _require_ha_source_healthy(pool)
    except HASourceUnmeasurableError as exc:
        return card_lines(watch, None, ha_last_success=exc.last_success_at, now=now)
    rows = await pool.fetch(
        "SELECT entity_id, state FROM ha_entity_snapshot WHERE entity_id = ANY($1)",
        [item.entity_id for item in watch],
    )
    return card_lines(watch, {r["entity_id"]: r["state"] for r in rows}, now=now)


async def _propose(pool: asyncpg.Pool, leave: Edge, message: str, now: datetime) -> str:
    from butlers.core.insight_premise import probe_premise
    from butlers.roster_imports import roster_module

    broker = roster_module("switchboard", "tools/insight/broker")
    result = await broker.propose_insight_candidate(
        pool,
        origin_butler="home",
        priority=_PRIORITY,
        category="departure",
        dedup_key=f"home:departure:{leave.id}",
        message=message,
        expires_at=leave.confirmed_at + DEPARTURE_WINDOW,
        lane="timed",
        deliver_after=max(leave.confirmed_at + DEPARTURE_DELAY, now),
        premise=probe_premise(
            "home",
            "departure_still_open",
            {"edge_event_id": str(leave.id), "confirmed_at": leave.confirmed_at.isoformat()},
        ),
        now=now,
    )
    status = result.get("status", "error")
    if status not in ("accepted", "duplicate"):
        logger.warning("home departure: card not queued (%s)", result.get("reason", status))
    return status


async def run_departure_watch(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None = None,
    *,
    read_edges: ReadEdges | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Record or recheck the card for each recent leave edge; queue a new open one."""
    del job_args
    if not DEPARTURE_CARD_LIVE:
        return {"status": "dark"}
    now = now or datetime.now(UTC)
    await pool.execute("DELETE FROM departure_cards WHERE confirmed_at < $1", now - CARD_RETENTION)
    watch = await load_watch(pool)
    if not watch:
        return {"status": "no_watch"}
    try:
        edges = await (read_edges or read_presence_edges)(now)
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError):
        logger.warning("home departure: presence timeline read failed")
        edges = None
    if edges is None:
        return {"status": "reader_unavailable"}

    leaves = [
        e
        for e in edges
        if e.direction == "leave"
        and now - DEPARTURE_WINDOW <= e.confirmed_at <= now - DEPARTURE_DELAY
    ]
    counts = {"proposed": 0, "rechecked": 0, "closed": 0}
    if not leaves:
        return {"status": "ok", **counts}
    lines = await _current_lines(pool, watch, now)
    fingerprint = card_fingerprint(lines)
    known = {
        r["edge_event_id"]: (r["state"], r["proposed_fingerprint"])
        for r in await pool.fetch(
            "SELECT edge_event_id, state, proposed_fingerprint FROM departure_cards "
            "WHERE edge_event_id = ANY($1)",
            [e.id for e in leaves],
        )
    }
    for leave in leaves:
        reason: str | None = closure(leave, edges) or (None if lines else "all_secure")
        if reason is None and leave.id in known and known[leave.id][1] != fingerprint:
            # The queued text no longer matches: the definer withdraws it, and the evidence row
            # says so without recording either version of the lines.
            reason = "withdrawn_changed"
        state = "closed" if reason else "open"
        counts["closed"] += int(reason is not None)
        if leave.id in known:
            if known[leave.id][0] == "open":
                counts["rechecked"] += 1
                await pool.execute(
                    "UPDATE departure_cards SET state = $2, closed_reason = $3, "
                    "fingerprint = $4, checked_at = $5 "
                    "WHERE edge_event_id = $1 AND state = 'open'",
                    leave.id,
                    state,
                    reason,
                    None if reason else fingerprint,
                    now,
                )
            continue
        inserted = await pool.fetchval(
            "INSERT INTO departure_cards (edge_event_id, confirmed_at, state, closed_reason, "
            "proposed_fingerprint, fingerprint, checked_at) VALUES ($1, $2, $3, $4, $5, $5, $6) "
            "ON CONFLICT (edge_event_id) DO NOTHING RETURNING edge_event_id",
            leave.id,
            leave.confirmed_at,
            state,
            reason,
            None if reason else fingerprint,
            now,
        )
        if inserted is None or reason is not None:
            continue
        status = await _propose(pool, leave, card_message(lines), now)
        if status == "accepted":
            counts["proposed"] += 1
        elif status != "duplicate":
            await pool.execute(
                "UPDATE departure_cards SET state = 'closed', closed_reason = 'not_queued' "
                "WHERE edge_event_id = $1",
                leave.id,
            )
    return {"status": "ok", **counts}
