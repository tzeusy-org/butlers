"""Break-through sensor: an inbound message from a sender the owner declared (bu-s11n0s.8).

The owner may declare who always reaches them (a person, an authenticated sender domain, a
channel, an account-security kind). After an inbound message is accepted this module asks the
fixed private evaluator whether a current clause matches. A match is recorded once as a durable
hit, announced as a metadata-only ``switchboard.break_through_hit`` event, and turned into one
urgent insight candidate that the broker delivers within seconds (S2).

Properties this module holds:

* **Never blocks or fails ingest.** The observation runs as a tracked background task; the
  record step is bounded by one timeout and every failure, including an evaluator that raises,
  is a miss.
* **Unknown is routine.** An unreadable evaluator, a deactivated interface or a timeout is a
  miss, never a hit. The fault is logged at most once per interval, not once per message.
* **Independent of ingestion rules.** The decision reads only sender, channel and the account
  classification, so a promoted skip rule cannot hide a hit.
* **Recorded once, announced until done.** The hit row is keyed by ``(clause_id,
  source_request_id)`` and inserted with ``ON CONFLICT DO NOTHING``. The row carries its own
  announcement lease: a crash or timeout between the insert and the announcement leaves it
  unpublished, and :func:`retry_unpublished` (run by the hourly urgent sub-cycle) claims and
  announces it. A published hit is never announced again.
* **Metadata only.** The event and the candidate name the sender (display name, never an
  address), the channel and the clause. They never carry a body, subject or snippet.
* **The owner's current word decides delivery.** The candidate carries the private origin the
  evaluator matched, so the broker re-evaluates the live clause at send time: a clause revoked
  after the hit sends nothing. A reply by the owner in the thread withdraws it unsent.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from butlers.core.attention_urgency import AttentionOrigin, _current_origin, accepted_source_origin

logger = logging.getLogger(__name__)

BREAK_THROUGH_EVENT_TYPE = "switchboard.break_through_hit"
BREAK_THROUGH_CATEGORY = "break_through"
BREAK_THROUGH_PRIORITY = 95
PROBE_BUTLER = "switchboard"
PROBE_NAME = "inbound_still_unanswered"

# Bounds. Ingest is a hot path; delivery is a network send.
RECORD_TIMEOUT_S = 2.0
ANNOUNCE_TIMEOUT_S = 30.0
# A claim older than this with no stamp belongs to a call that died.
LEASE_S = 120.0
_CANDIDATE_TTL = timedelta(hours=6)
# Per clause, per rolling hour, hits past this fold into one "and N more" line (not one each).
FLOOD_CAP_PER_HOUR = 3
_FAULT_LOG_INTERVAL_S = 300.0
_RESOLVABLE_CHANNELS = frozenset({"telegram", "email", "whatsapp"})
_CHANNEL_LABELS = {"email": "email", "telegram": "Telegram", "whatsapp": "WhatsApp"}

_last_fault_log: float | None = None
_tasks: set[asyncio.Task[Any]] = set()


@dataclass(frozen=True)
class Hit:
    """One recorded match, with what its announcement needs and nothing of the message."""

    clause_id: uuid.UUID
    source_request_id: uuid.UUID
    channel: str
    sender_name: str | None
    thread: str | None
    received_at: datetime | None
    origin: Mapping[str, str]


def _broker() -> Any:
    """The Switchboard insight broker, imported lazily (a roster tool package, not core)."""
    return importlib.import_module("butlers.tools.switchboard.insight.broker")


def _log_fault(error_class: str) -> None:
    """Log a sensor fault at most once per interval: a dark interface faults on every message."""
    global _last_fault_log
    now = time.monotonic()
    if _last_fault_log is not None and now - _last_fault_log < _FAULT_LOG_INTERVAL_S:
        return
    _last_fault_log = now
    logger.warning("break_through_sensor_unavailable error_class=%s", error_class)


async def _resolve_sender(
    pool: Any, channel: str | None, sender: str
) -> tuple[uuid.UUID | None, str | None]:
    """The sender's entity id and display name, or ``(None, None)`` when unresolved."""
    if channel not in _RESOLVABLE_CHANNELS or not sender:
        return None, None
    from butlers.identity import resolve_contact_by_channel

    contact = await resolve_contact_by_channel(pool, channel, sender)
    if contact is None:
        return None, None
    # An unidentified sender's placeholder name embeds its address: never a display name.
    return contact.entity_id, None if contact.is_unidentified else contact.name


async def _matching_clause(pool: Any, origin_record: Mapping[str, str]) -> uuid.UUID | None:
    """The first current owner clause matching the origin, via the fixed evaluator."""
    values = dict(origin_record)
    values["gate"] = "quiet_hours"
    raw = await pool.fetchval("SELECT dashboard_attention.evaluate($1::jsonb)", values)
    data = json.loads(raw) if isinstance(raw, str) else raw
    if type(data) is dict and data.get("cause") == "owner_clause":
        return uuid.UUID(str(data["ref"]))
    return None


_HIT_COLUMNS = "clause_id, source_request_id, channel, sender_name, thread, received_at, origin"


def _hit_from_row(row: Any) -> Hit:
    origin = row["origin"]
    origin = json.loads(origin) if isinstance(origin, str) else origin
    return Hit(
        clause_id=row["clause_id"],
        source_request_id=row["source_request_id"],
        channel=row["channel"],
        sender_name=row["sender_name"],
        thread=row["thread"],
        received_at=row["received_at"],
        origin=dict(origin or {}),
    )


async def _claim(pool: Any, hit: Hit) -> bool:
    """Take the announcement of a hit: the insert, or the lease of an abandoned one."""
    inserted = await pool.fetchval(
        "INSERT INTO switchboard.break_through_hits "
        "(clause_id, source_request_id, channel, sender_name, thread, received_at, origin) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb) "
        "ON CONFLICT (clause_id, source_request_id) DO NOTHING RETURNING clause_id",
        hit.clause_id,
        hit.source_request_id,
        hit.channel,
        hit.sender_name,
        hit.thread,
        hit.received_at,
        dict(hit.origin),
    )
    if inserted is not None:
        return True
    reclaimed = await pool.fetchval(
        "UPDATE switchboard.break_through_hits SET claimed_at = clock_timestamp() "
        "WHERE clause_id = $1 AND source_request_id = $2 AND published_at IS NULL "
        "AND claimed_at < clock_timestamp() - make_interval(secs => $3) RETURNING 1",
        hit.clause_id,
        hit.source_request_id,
        LEASE_S,
    )
    return reclaimed is not None


async def _record(
    pool: Any,
    *,
    channel: str,
    sender_identity: str,
    normalized_text: str | None,
    raw: Any,
    source_request_id: str,
    thread: str | None,
    received_at: datetime | None,
    owner_sender_identity: str | None,
) -> Hit | None:
    """Evaluate the message and, on a match this call owns, return the claimed hit."""
    sender = sender_identity.strip()
    if owner_sender_identity and sender == owner_sender_identity.strip():
        return None  # the owner's own message never interrupts the owner
    entity_id, name = await _resolve_sender(pool, channel, sender)
    row = {
        "request_context": {"source_channel": channel, "source_sender_identity": sender},
        "raw_payload": raw,
        "normalized_text": normalized_text,
    }
    origin = accepted_source_origin(row, entity_id)
    record = origin.record()
    if not record:
        return None
    clause_id = await _matching_clause(pool, record)
    if clause_id is None:
        return None
    hit = Hit(
        clause_id=clause_id,
        source_request_id=uuid.UUID(str(source_request_id)),
        channel=origin.channel or channel,
        sender_name=name,
        thread=thread,
        received_at=received_at,
        origin=record,
    )
    return hit if await _claim(pool, hit) else None


def _label(channel: str) -> str:
    return _CHANNEL_LABELS.get(channel, channel)


def _message(hit: Hit) -> str:
    who = hit.sender_name or "A sender you declared"
    return f"{who} messaged you ({_label(hit.channel)}) - open"


def _dedup_key(hit: Hit) -> str:
    # The family (everything before the last segment) is per request, so one hit never
    # cools down the next: every declared message is its own candidate.
    return f"switchboard:{BREAK_THROUGH_CATEGORY}:{hit.source_request_id}:{hit.clause_id}"


async def _publish_event(pool: Any, hit: Hit) -> bool:
    from butlers.core_tools._domain_events import (
        _SwitchboardInProcessRouteClient,
        publish_domain_event,
    )

    payload: dict[str, str] = {
        "clause_id": str(hit.clause_id),
        "channel": hit.channel,
        "source_request_id": str(hit.source_request_id),
    }
    if hit.sender_name:
        payload["sender_name"] = hit.sender_name
    result = await publish_domain_event(
        pool,
        _SwitchboardInProcessRouteClient(pool),
        event_type=BREAK_THROUGH_EVENT_TYPE,
        source_butler="switchboard",
        payload=payload,
    )
    return result.get("status") == "ok"


async def _propose(
    pool: Any, hit: Hit, *, key: str, message: str, premise: dict[str, Any] | None
) -> tuple[str | None, bool]:
    """Queue one urgent candidate for a hit: ``(id or None, terminal)``.

    ``terminal`` is True when the broker refused it for good (``filtered``: the owner's verbosity
    is off), so retrying can never succeed and the hit is closed instead of re-announced.
    The candidate carries the private origin the evaluator matched. Without it the broker has
    no authority to interrupt, and the line would wait for a normal cycle (it is never sent).
    """
    propose_insight_candidate = _broker().propose_insight_candidate

    existing = await pool.fetchval(
        "SELECT id FROM insight_candidates WHERE dedup_key = $1 LIMIT 1", key
    )
    if existing is not None:
        return str(existing), False
    token = _current_origin.set(AttentionOrigin.from_record(dict(hit.origin)))
    try:
        result = await propose_insight_candidate(
            pool,
            origin_butler="switchboard",
            priority=BREAK_THROUGH_PRIORITY,
            category=BREAK_THROUGH_CATEGORY,
            dedup_key=key,
            message=message,
            expires_at=datetime.now(UTC) + _CANDIDATE_TTL,
            premise=premise,
        )
    finally:
        _current_origin.reset(token)
    if result.get("status") != "accepted":
        logger.warning("break_through_candidate_not_queued status=%s", result.get("status"))
        return None, result.get("status") == "filtered"
    found = await pool.fetchval(
        "SELECT id FROM insight_candidates WHERE dedup_key = $1 LIMIT 1", key
    )
    return str(found), False


async def _propose_candidate(pool: Any, hit: Hit) -> tuple[str | None, bool]:
    from butlers.core.insight_premise import probe_premise

    after = (hit.received_at or datetime.now(UTC)).isoformat()
    premise = probe_premise(PROBE_BUTLER, PROBE_NAME, {"thread": hit.thread or "", "after": after})
    return await _propose(pool, hit, key=_dedup_key(hit), message=_message(hit), premise=premise)


async def _over_cap(pool: Any, hit: Hit) -> int:
    """How many hits past the per-clause hourly cap this one is (0 when within the cap).

    A hit's rank is its place among the clause's hits of the last hour in arrival order, read from
    the durable hit rows, so a replay (same row) never counts twice and concurrent announcers
    agree on who is over.
    """
    rank = await pool.fetchval(
        "SELECT count(*) FROM switchboard.break_through_hits h WHERE h.clause_id = $1 "
        "AND h.observed_at > clock_timestamp() - interval '1 hour' "
        "AND (h.observed_at, h.source_request_id) <= ("
        "  SELECT observed_at, source_request_id FROM switchboard.break_through_hits "
        "  WHERE clause_id = $1 AND source_request_id = $2)",
        hit.clause_id,
        hit.source_request_id,
    )
    return max(0, int(rank or 0) - FLOOD_CAP_PER_HOUR)


async def _fold(pool: Any, hit: Hit, overflow: int) -> tuple[str | None, bool]:
    """Fold an over-cap hit into the clause's single pending "and N more" candidate.

    The fold is delivered by the hourly urgent cycle, not at once, so its count reflects every
    overflow of that window. Content-free: a count and the channel, never a name or text.
    """
    message = f"and {overflow} more from senders you declared ({_label(hit.channel)}) - open"
    prefix = f"switchboard:break_through:fold-{hit.clause_id}-"
    pending = await pool.fetchval(
        "UPDATE insight_candidates SET message = $2 WHERE status = 'pending' "
        "AND claim_id IS NULL AND category = $3 AND dedup_key LIKE $1 RETURNING id",
        prefix + "%",
        message,
        BREAK_THROUGH_CATEGORY,
    )
    if pending is not None:
        return str(pending), False
    key = f"{prefix}{hit.source_request_id}:fold"
    return await _propose(pool, hit, key=key, message=message, premise=None)


async def _deliver_now(pool: Any, candidate_id: str) -> None:
    """Run one urgent cycle scoped to the candidate so it reaches the owner in seconds."""
    from butlers.scheduled_jobs import _build_switchboard_insight_notify_fn

    await _broker().delivery_cycle(
        pool,
        notify_fn=_build_switchboard_insight_notify_fn(pool),
        urgent_only=True,
        candidate_ids=[candidate_id],
    )


async def _stamp(pool: Any, hit: Hit) -> None:
    await pool.execute(
        "UPDATE switchboard.break_through_hits SET published_at = clock_timestamp() "
        "WHERE clause_id = $1 AND source_request_id = $2 AND published_at IS NULL",
        hit.clause_id,
        hit.source_request_id,
    )


async def announce(pool: Any, hit: Hit) -> bool:
    """Announce a claimed hit: the event, the candidate, the stamp, then an immediate delivery.

    The hit is stamped published only once the event and the candidate exist, so a failure before
    that leaves it for :func:`retry_unpublished`. The immediate delivery comes after the stamp: a
    failed send is the broker's to retry (the candidate stays pending for the hourly cycle), not
    a reason to announce the hit again.
    """
    if not await _publish_event(pool, hit):
        logger.warning("break_through_event_not_published")
        return False
    overflow = await _over_cap(pool, hit)
    if overflow:
        folded, terminal = await _fold(pool, hit, overflow)
        if folded is None and not terminal:
            return False
        await _stamp(pool, hit)
        return folded is not None
    candidate_id, terminal = await _propose_candidate(pool, hit)
    if candidate_id is None and not terminal:
        return False
    await _stamp(pool, hit)
    if candidate_id is None:
        return False  # refused for good (verbosity off): closed, never retried or sent
    await _deliver_now(pool, candidate_id)
    return True


async def retry_unpublished(pool: Any, *, limit: int = 20) -> int:
    """Claim and announce hits whose announcement lapsed; return how many were announced.

    A hit older than the candidate's lifetime is closed without an announcement: telling the
    owner about a weeks-old message as if it were fresh is worse than silence.
    """
    try:
        await pool.execute(
            "UPDATE switchboard.break_through_hits SET published_at = clock_timestamp() "
            "WHERE published_at IS NULL "
            "AND COALESCE(received_at, observed_at) "
            "< clock_timestamp() - make_interval(secs => $1)",
            _CANDIDATE_TTL.total_seconds(),
        )
        rows = await pool.fetch(
            "UPDATE switchboard.break_through_hits SET claimed_at = clock_timestamp() "
            "WHERE (clause_id, source_request_id) IN ("
            "  SELECT clause_id, source_request_id FROM switchboard.break_through_hits "
            "  WHERE published_at IS NULL "
            "  AND claimed_at < clock_timestamp() - make_interval(secs => $1) "
            "  ORDER BY claimed_at LIMIT $2 FOR UPDATE SKIP LOCKED"
            f") RETURNING {_HIT_COLUMNS}",
            LEASE_S,
            limit,
        )
    except Exception as exc:
        _log_fault(type(exc).__name__)
        return 0
    announced = 0
    for row in rows:
        try:
            announced += bool(
                await asyncio.wait_for(announce(pool, _hit_from_row(row)), ANNOUNCE_TIMEOUT_S)
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _log_fault(type(exc).__name__)
    return announced


async def observe_inbound(
    pool: Any,
    *,
    channel: str,
    sender_identity: str,
    normalized_text: str | None,
    raw: Any,
    source_request_id: str,
    thread: str | None = None,
    received_at: datetime | None = None,
    owner_sender_identity: str | None = None,
    timeout_s: float = RECORD_TIMEOUT_S,
) -> Hit | None:
    """Record a clause hit for an accepted inbound message and announce it.

    Returns the hit when this call recorded (or took over) it, otherwise ``None``: no match, a
    replay, or any failure. Never raises. The record step is bounded by ``timeout_s`` and the
    announcement by its own bound; a failed announcement leaves the hit for the retry sweep.
    """
    try:
        hit = await asyncio.wait_for(
            _record(
                pool,
                channel=channel,
                sender_identity=sender_identity,
                normalized_text=normalized_text,
                raw=raw,
                source_request_id=source_request_id,
                thread=thread,
                received_at=received_at,
                owner_sender_identity=owner_sender_identity,
            ),
            timeout_s,
        )
        if hit is None:
            return None
        await asyncio.wait_for(announce(pool, hit), ANNOUNCE_TIMEOUT_S)
        return hit
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # Class only: provider and SQL error text is never evidence.
        _log_fault(type(exc).__name__)
        return None


def spawn_observation(pool: Any, **kwargs: Any) -> asyncio.Task[Hit | None]:
    """Run :func:`observe_inbound` as a tracked background task: ingest never waits for it."""
    task = asyncio.ensure_future(observe_inbound(pool, **kwargs))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


async def drain() -> None:
    """Wait for every spawned observation (tests and orderly shutdown)."""
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)
