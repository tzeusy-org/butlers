"""Away-until and dead-address facts from automated email evidence (bu-s11n0s.14, slice S2b).

The Gmail connector (S2a) binds an auto-reply or bounce to mail the owner actually sent, checks
Gmail's own authentication verdict, and parses a return date. Switchboard ingest persists the
result under ``raw_payload.control.automated_evidence`` on a metadata-only inbox row. The
``automated_evidence_sync`` job (``run_automated_evidence_sync`` in the Relationship job module)
reads those rows beside ``interaction_sync``'s read of ``switchboard.message_inbox`` (same table,
same SELECT grant) and hands them to :func:`sync`, which writes through the central writer with no
session and no model:

* ``away-until <YYYY-MM-DD>`` on the auto-reply sender's entity, when the auto-reply answered the
  owner's sent mail, the sender was a recipient of it, the sender authenticated, and the date lies
  within 180 days after receipt;
* ``undeliverable-email <address>`` on the entity owning each address a bound, authenticated DSN
  reports as permanently failed.

The job re-checks every condition rather than trusting the row, so a backscatter DSN (no sent
original) writes nothing even if a malformed row still lists recipients. An address writes a fact
only when it resolves to exactly one live entity (``resolve_contacts_by_channel_bulk`` drops
ambiguous matches) that is not the owner. Resolution runs strict, so a database fault fails the
run without advancing the checkpoint instead of silently skipping rows.

Replay is a no-op: the fact identity is ``(subject, predicate, object)`` and the provenance is
fixed, so the central writer reports ``unchanged``. That is what lets each run overlap the last
checkpoint by an hour to catch rows committed late.

A dead address that comes back to life clears itself (bu-s11n0s.90). The same run asks the inbox,
through the injected :data:`SenderReader`, for the newest inbound email whose sender is exactly that
address (normalised the way the fact was keyed: stripped, lower-cased) and retracts an active
``undeliverable-email`` fact when that message was received after the fact's own ``observed_at``.
The retraction carries an evidence line citing that inbox row. An address with a later message is
also not re-marked from a replayed older bounce, so the clear is idempotent and never flaps. No
new table or cross-butler path: the read is the same ``switchboard.message_inbox`` SELECT.

Reading these facts is slice S2c: ``tools/away_until.py`` for nudge deferral and
``ButlerDaemon._resolve_entity_channel_identifier`` for dead-address avoidance. Not built here.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import asyncpg

from butlers.core.state import state_get, state_set
from butlers.identity import resolve_contacts_by_channel_bulk

from .fact_evidence import EvidencePacket
from .relationship_assert_fact import (
    AssertOutcome,
    relationship_assert_fact,
    retract_state_fact,
)

logger = logging.getLogger(__name__)

AWAY_UNTIL_PREDICATE = "away-until"
UNDELIVERABLE_EMAIL_PREDICATE = "undeliverable-email"
#: Server-set ``src`` for every fact this job writes.
SOURCE = "automated_evidence_sync"
STATE_KEY = "automated_evidence_sync.last_scan_at"

#: Same bound as ``interaction_sync``: an outage never triggers an unbounded backfill.
MAX_WINDOW_DAYS = 30
#: Each run re-reads this much before the checkpoint; replay is idempotent.
_OVERLAP = timedelta(hours=1)
#: Mirrors the connector's bound; a return date further out is refused.
MAX_AWAY_DAYS = 180
#: Mirrors the connector's DSN recipient cap.
MAX_FAILED_RECIPIENTS = 16
_EMAIL_CHANNEL = "email"
_FACT_CONF = 1.0

#: Reads inbound automated email rows received at or after the given instant. Each row maps
#: ``id``, ``received_at``, ``sender``, ``automated_kind`` and ``automated_evidence``. The
#: Relationship job module supplies it, beside ``interaction_sync``'s read of the same table.
InboxReader = Callable[[datetime], Awaitable[Sequence[Mapping[str, Any]]]]


#: Newest inbound email per exact sender address among ``addresses``, received after ``since``.
#: Maps each address (stripped, lower-cased) to ``(inbox row id, received_at)``; an address with no
#: such message is absent. The Relationship job module supplies it, beside :data:`InboxReader`.
SenderReader = Callable[[Sequence[str], datetime], Awaitable[Mapping[str, tuple[Any, datetime]]]]

#: Active dead-address facts checked for a revival per run, oldest observation first.
_MAX_DEAD_FACTS = 1000

_ACTIVE_DEAD_FACTS_SQL = """
SELECT id, subject, object, observed_at
FROM   relationship.entity_facts
WHERE  predicate = 'undeliverable-email' AND validity = 'active'
ORDER  BY observed_at, id
LIMIT  $1
"""


@dataclass(frozen=True)
class Claim:
    """One fact an inbox row supports: write ``predicate object`` on ``address``'s entity."""

    address: str
    predicate: str
    object: str


def _address(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    address = value.strip().lower()
    local, at, domain = address.partition("@")
    return address if at and local and domain and " " not in address else ""


GMAIL_BOUNCER = "mailer-daemon@googlemail.com"


def _sender_speaks_for(sender: str, address: str) -> bool:
    """Gmail's own bouncer, or a sender domain equal to or a subdomain of the address's.

    A co-recipient's own signed DSN must not name someone else's address as undeliverable.
    """
    if sender == GMAIL_BOUNCER:
        return True
    a, b = sender.rpartition("@")[2], address.rpartition("@")[2]
    return bool(a) and bool(b) and (a == b or a.endswith("." + b))


def _checked(evidence: dict[str, Any], key: str) -> bool:
    # A verified check is the JSON literal true, never a truthy string or number.
    return evidence.get(key) is True


def _away_until(evidence: dict[str, Any], received: date) -> str | None:
    raw = evidence.get("away_until")
    if not isinstance(raw, str):
        return None
    try:
        parsed = date.fromisoformat(raw)
    except ValueError:
        return None
    if raw != parsed.isoformat():
        return None
    if not received <= parsed <= received + timedelta(days=MAX_AWAY_DAYS):
        return None
    return parsed.isoformat()


def claims_for(
    automated_kind: Any, evidence: Any, sender: Any, received_at: datetime
) -> tuple[Claim, ...]:
    """The facts one inbox row supports, or ``()``. Pure; re-checks every connector check."""
    if not isinstance(evidence, dict) or not _checked(evidence, "original_sent"):
        return ()
    if not _checked(evidence, "sender_authenticated"):
        return ()
    received = received_at.astimezone(UTC).date()

    if automated_kind == "auto_reply":
        address = _address(sender)
        away_until = _away_until(evidence, received)
        if not address or away_until is None or not _checked(evidence, "sender_was_recipient"):
            return ()
        return (Claim(address, AWAY_UNTIL_PREDICATE, away_until),)

    if automated_kind == "bounce":
        failed = evidence.get("failed_recipients")
        if not isinstance(failed, list):
            return ()
        bouncer = _address(sender)
        addresses = sorted(
            {
                a
                for a in map(_address, failed[:MAX_FAILED_RECIPIENTS])
                if a and _sender_speaks_for(bouncer, a)
            }
        )
        return tuple(Claim(a, UNDELIVERABLE_EMAIL_PREDICATE, a) for a in addresses)

    return ()


async def _scan_start(pool: asyncpg.Pool, now: datetime) -> tuple[datetime, int]:
    floor = now - timedelta(days=MAX_WINDOW_DAYS)
    try:
        raw = await state_get(pool, STATE_KEY)
    except Exception:
        logger.warning("automated_evidence_sync: checkpoint read failed", exc_info=True)
        return floor, 1
    if raw is None:
        return floor, 0
    try:
        checkpoint = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        logger.warning("automated_evidence_sync: invalid checkpoint; using the window floor")
        return floor, 0
    if checkpoint.tzinfo is None:
        checkpoint = checkpoint.replace(tzinfo=UTC)
    return min(max(checkpoint - _OVERLAP, floor), now), 0


async def _clear_revived(
    pool: asyncpg.Pool,
    facts: Sequence[Mapping[str, Any]],
    latest: Mapping[str, tuple[Any, datetime]],
) -> tuple[int, int]:
    """Retract each dead-address fact whose address wrote after the fact. Returns (cleared, errors).

    Idempotent: the UPDATE is guarded on ``validity = 'active'``, so a repeat finds nothing.
    """
    cleared = errors = 0
    for fact in facts:
        seen = latest.get(_address(fact["object"]))
        if seen is None or not seen[1] > fact["observed_at"]:
            continue
        inbox_id, received_at = seen
        try:
            retracted = await retract_state_fact(
                pool,
                fact["subject"],
                UNDELIVERABLE_EMAIL_PREDICATE,
                fact["object"],
                observed_before=received_at,
                packet=EvidencePacket(
                    items=(
                        {
                            "type": "text",
                            "ref": f"switchboard.message_inbox:{inbox_id}",
                            "note": (
                                "A later inbound message arrived from this exact address, "
                                "so it is deliverable again"
                            ),
                        },
                    ),
                    src=SOURCE,
                    origin="direct",
                ),
            )
            if retracted is None:
                continue
            cleared += 1
        except Exception:
            logger.exception("automated_evidence_sync: dead-address clear failed")
            errors += 1
    return cleared, errors


async def sync(
    pool: asyncpg.Pool, *, read_inbox: InboxReader, read_senders: SenderReader | None = None
) -> dict[str, Any]:
    """Write away-until and dead-address facts from bound automated email evidence.

    With ``read_senders`` it also retracts a dead-address fact once its address writes again.
    """
    now = datetime.now(UTC)
    start, errors = await _scan_start(pool, now)
    stats: dict[str, Any] = {
        "scan_window_start": start.isoformat(),
        "scan_window_end": now.isoformat(),
        "scanned": 0,
        "written": 0,
        "unchanged": 0,
        "skipped_unbound": 0,
        "skipped_unresolved": 0,
        "skipped_owner": 0,
        "skipped_revived": 0,
        "cleared": 0,
        "errors": errors,
    }

    try:
        rows = await read_inbox(start)
    except Exception:
        logger.exception("automated_evidence_sync: failed to read switchboard.message_inbox")
        stats["errors"] += 1
        return stats
    stats["scanned"] = len(rows)

    planned: list[tuple[Mapping[str, Any], Claim]] = []
    for row in rows:
        claims = claims_for(
            row["automated_kind"], row["automated_evidence"], row["sender"], row["received_at"]
        )
        if not claims:
            stats["skipped_unbound"] += 1
        planned.extend((row, claim) for claim in claims)

    pairs = sorted({(_EMAIL_CHANNEL, claim.address) for _row, claim in planned})
    try:
        resolved = await resolve_contacts_by_channel_bulk(pool, pairs, raise_on_error=True)
    except Exception:
        logger.exception("automated_evidence_sync: entity resolution failed")
        stats["errors"] += 1
        return stats

    dead_facts: Sequence[Mapping[str, Any]] = ()
    latest: Mapping[str, tuple[Any, datetime]] | None = None
    if read_senders is not None:
        try:
            dead_facts = await pool.fetch(_ACTIVE_DEAD_FACTS_SQL, _MAX_DEAD_FACTS)
            watched = {
                a
                for a in (
                    *(_address(f["object"]) for f in dead_facts),
                    *(
                        c.address
                        for _r, c in planned
                        if c.predicate == UNDELIVERABLE_EMAIL_PREDICATE
                    ),
                )
                if a
            }
            since = min(
                (
                    *(f["observed_at"] for f in dead_facts),
                    *(
                        r["received_at"]
                        for r, c in planned
                        if c.predicate == UNDELIVERABLE_EMAIL_PREDICATE
                    ),
                ),
                default=None,
            )
            if watched and since is not None:
                latest = await read_senders(sorted(watched), since)
            else:
                latest = {}
        except Exception:
            logger.exception("automated_evidence_sync: revival read failed")
            stats["errors"] += 1

    for row, claim in planned:
        if claim.predicate == UNDELIVERABLE_EMAIL_PREDICATE and read_senders is not None:
            if latest is None:
                # The revival read failed: a dead mark is not safe to write. The checkpoint holds.
                continue
            seen = latest.get(claim.address)
            if seen is not None and seen[1] > row["received_at"]:
                stats["skipped_revived"] += 1
                continue
        contact = resolved.get((_EMAIL_CHANNEL, claim.address))
        if contact is None or contact.entity_id is None:
            stats["skipped_unresolved"] += 1
            continue
        if "owner" in contact.roles:
            stats["skipped_owner"] += 1
            continue
        try:
            result = await relationship_assert_fact(
                pool,
                contact.entity_id,
                claim.predicate,
                claim.object,
                src=SOURCE,
                conf=_FACT_CONF,
                observed_at=row["received_at"],
                evidence=[
                    {
                        "type": "text",
                        "ref": f"switchboard.message_inbox:{row['id']}",
                        "note": (
                            f"Gmail {row['automated_kind']} bound to the owner's sent mail "
                            "and authenticated by mx.google.com"
                        ),
                    }
                ],
            )
        except Exception:
            logger.exception(
                "automated_evidence_sync: fact write failed predicate=%s inbox_id=%s",
                claim.predicate,
                row["id"],
            )
            stats["errors"] += 1
            continue
        if result.outcome == AssertOutcome.unchanged:
            stats["unchanged"] += 1
        elif result.outcome in (AssertOutcome.inserted, AssertOutcome.superseded):
            stats["written"] += 1
        else:
            # Owner subjects are skipped above, so a park or candidate here is unexpected.
            logger.warning(
                "automated_evidence_sync: unexpected outcome=%s predicate=%s inbox_id=%s",
                result.outcome,
                claim.predicate,
                row["id"],
            )
            stats["errors"] += 1

    if latest and dead_facts:
        stats["cleared"], clear_errors = await _clear_revived(pool, dead_facts, latest)
        stats["errors"] += clear_errors

    if stats["errors"] == 0:
        try:
            await state_set(pool, STATE_KEY, now.isoformat())
        except Exception:
            logger.warning("automated_evidence_sync: checkpoint write failed", exc_info=True)
            stats["errors"] += 1
    return stats
