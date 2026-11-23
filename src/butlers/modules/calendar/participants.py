"""One participant record per calendar occurrence (REQ-module-calendar-043..045, bu-5ii4gj.3).

The projection calls :func:`sync_event_participants` after it writes a provider event. A row is an
"invited" claim: it is never written as an interaction, an encounter or an owner assertion, and a
quarantined invitation (REQ-module-calendar-041) has no provider-derived rows. Readers get entity
ids and display names, never an address; unresolved attendees are a count.

A failed claimant read never fails the event write: prior rows and their resolver version stay, a
counter increments, and :func:`read_participants` reports ``available=False`` instead of an empty
list.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

import asyncpg

from butlers.core.identity.attendees import (
    MAX_ADDRESSES,
    RESOLVER_VERSION,
    Claim,
    Resolved,
    is_resource_address,
    normalize_email,
    resolve_attendee_emails,
)
from butlers.core.temporal.calendar_provenance import is_quarantined_invitation
from butlers.core.temporal.invitation_quarantine import (
    QUARANTINED_INVITATION_SQL,
    quarantined_invitation_sql,
)

logger = logging.getLogger(__name__)

BACKFILL_BATCH: Final = 200
_STATUSES: Final = frozenset({"needsAction", "accepted", "declined", "tentative"})
_PROVIDER_SOURCE: Final = "provider_event"


@dataclass(frozen=True, slots=True)
class ParticipantRow:
    email_norm: str
    display_name: str | None
    role: str
    response_status: str
    entity_id: uuid.UUID | None
    resolution: str


def _count_failure() -> None:
    try:
        from opentelemetry import metrics

        metrics.get_meter("butlers").create_counter(
            name="butlers.calendar.participant_resolution_failed",
            description="Attendee resolutions that failed and left prior participant rows",
            unit="events",
        ).add(1)
    except Exception:  # noqa: BLE001
        logger.debug("participant failure counter unavailable", exc_info=True)


def _metadata_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except ValueError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _is_true(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.strip().lower() == "true")


def _status(attendee: Mapping[str, Any]) -> str:
    raw = attendee.get("response_status")
    if raw is None:
        raw = attendee.get("responseStatus")
    return raw if isinstance(raw, str) and raw in _STATUSES else "needsAction"


def _display_name(attendee: Mapping[str, Any]) -> str | None:
    raw = attendee.get("display_name")
    if raw is None:
        raw = attendee.get("displayName")
    if not isinstance(raw, str):
        return None
    name = raw.strip()
    return name[:200] or None


def attendee_inputs(metadata: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The projected attendees that carry one plain address, first occurrence of each address."""
    attendees = metadata.get("attendees")
    if not isinstance(attendees, list):
        return []
    seen: dict[str, dict[str, Any]] = {}
    for attendee in attendees:
        if not isinstance(attendee, Mapping):
            continue
        email = normalize_email(attendee.get("email"))
        if email is None:
            continue
        existing = seen.get(email)
        if existing is None:
            seen[email] = {"email": email, "raw": attendee}
        else:  # a repeated address keeps the strongest flags rather than the first alone
            existing["raw"] = {
                **existing["raw"],
                "self": _is_true(existing["raw"].get("self")) or _is_true(attendee.get("self")),
                "organizer": _is_true(existing["raw"].get("organizer"))
                or _is_true(attendee.get("organizer")),
            }
    return list(seen.values())


def derive_rows(metadata: Mapping[str, Any], claims: Sequence[Claim]) -> list[ParticipantRow]:
    """Pure: the participant rows for one event's metadata and the claimants read for it."""
    inputs = attendee_inputs(metadata)
    resolutions = resolve_attendee_emails((item["email"] for item in inputs), claims)
    organizer = normalize_email(metadata.get("organizer"))
    owner_claim = next((claim.entity_id for claim in claims if claim.is_owner), None)
    rows: list[ParticipantRow] = []
    for item in inputs:
        email, raw = item["email"], item["raw"]
        resolved: Resolved = resolutions[email]
        if _is_true(raw.get("self")) and resolved.resolution != "owner":
            # The provider says this is the owner's own seat, whatever the address facts say.
            resolved = Resolved("owner", owner_claim)
        if resolved.resolution == "resource" or is_resource_address(email):
            role = "resource"
        elif _is_true(raw.get("organizer")) or (organizer is not None and organizer == email):
            role = "organizer"
        elif _is_true(raw.get("optional")):
            role = "optional"
        else:
            role = "required"
        rows.append(
            ParticipantRow(
                email_norm=email,
                # The organizer's displayName is untrusted: kept only for an address that names no
                # entity, never as the name of one (readers use the entity's own name).
                display_name=(
                    _display_name(raw)
                    if resolved.resolution in ("ambiguous", "unresolved")
                    else None
                ),
                role=role,
                response_status=_status(raw),
                entity_id=resolved.entity_id,
                resolution=resolved.resolution,
            )
        )
    return sorted(rows, key=lambda row: row.email_norm)


async def fetch_claims(executor: Any, emails: Sequence[str]) -> list[Claim]:
    """Read the claimants through the definer, in batches under its cap. A failure propagates."""
    claims: list[Claim] = []
    for start in range(0, len(emails), MAX_ADDRESSES):
        rows = await executor.fetch(
            "SELECT email_norm, entity_id, is_owner "
            "FROM public.attendee_identity_claims($1::text[])",
            list(emails[start : start + MAX_ADDRESSES]),
        )
        claims.extend(
            Claim(
                email_norm=row["email_norm"],
                entity_id=row["entity_id"],
                is_owner=bool(row["is_owner"]),
            )
            for row in rows
        )
    return claims


_DELETE_PROVIDER_ROWS = (
    "DELETE FROM calendar_event_participants WHERE event_id = $1 AND resolution <> 'manual'"
)

_UPSERT = """
INSERT INTO calendar_event_participants (
    event_id, email_norm, display_name, role, response_status, entity_id, resolution,
    resolver_version, resolved_at
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, now())
ON CONFLICT (event_id, email_norm) WHERE email_norm IS NOT NULL DO UPDATE SET
    display_name = EXCLUDED.display_name,
    role = EXCLUDED.role,
    response_status = EXCLUDED.response_status,
    entity_id = EXCLUDED.entity_id,
    resolution = EXCLUDED.resolution,
    resolver_version = EXCLUDED.resolver_version,
    resolved_at = now()
WHERE (calendar_event_participants.display_name, calendar_event_participants.role,
       calendar_event_participants.response_status, calendar_event_participants.entity_id,
       calendar_event_participants.resolution, calendar_event_participants.resolver_version)
      IS DISTINCT FROM
      (EXCLUDED.display_name, EXCLUDED.role, EXCLUDED.response_status, EXCLUDED.entity_id,
       EXCLUDED.resolution, EXCLUDED.resolver_version)
"""


async def _write(conn: Any, event_id: uuid.UUID, rows: Sequence[ParticipantRow]) -> None:
    await conn.execute(
        """
        DELETE FROM calendar_event_participants
        WHERE event_id = $1 AND resolution <> 'manual' AND NOT (email_norm = ANY($2::text[]))
        """,
        event_id,
        [row.email_norm for row in rows],
    )
    for row in rows:
        await conn.execute(
            _UPSERT,
            event_id,
            row.email_norm,
            row.display_name,
            row.role,
            row.response_status,
            row.entity_id,
            row.resolution,
            RESOLVER_VERSION,
        )


async def sync_event_participants(pool: asyncpg.Pool, event_id: uuid.UUID) -> str:
    """Derive and store the participants of one provider event. Never raises.

    Returns ``written``, ``cleared`` (no attendees, or a quarantined invitation), ``failed`` (the
    claimant read or the write raised; prior rows are untouched) or ``skipped`` (not a provider
    event or gone).
    """
    try:
        row = await pool.fetchrow(
            "SELECT metadata FROM calendar_events WHERE id = $1 "
            "-- quarantine-exempt: reads metadata only, filtered by is_quarantined_invitation",
            event_id,
        )
        if row is None:
            return "skipped"
        metadata = _metadata_object(row["metadata"])
        if metadata.get("source_type") != _PROVIDER_SOURCE:
            return "skipped"
        if is_quarantined_invitation(metadata) or not attendee_inputs(metadata):
            await pool.execute(_DELETE_PROVIDER_ROWS, event_id)
            return "cleared"
        emails = [
            item["email"]
            for item in attendee_inputs(metadata)
            if not is_resource_address(item["email"])
        ]
        claims = await fetch_claims(pool, emails) if emails else []
        rows = derive_rows(metadata, claims)
        async with pool.acquire() as conn:
            async with conn.transaction():
                await _write(conn, event_id, rows)
        return "written"
    except Exception as exc:  # the event write has already committed; this must not undo it
        logger.warning("calendar_participants_failed error=%s", type(exc).__name__)
        _count_failure()
        return "failed"


async def sync_manual_participants(
    pool: asyncpg.Pool,
    event_id: uuid.UUID,
    entity_ids: Sequence[uuid.UUID],
    *,
    clear: bool = False,
) -> None:
    """Mirror owner-made entity links as ``manual`` rows (no address). Never raises."""
    if not entity_ids and not clear:
        return
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM calendar_event_participants "
                    "WHERE event_id = $1 AND resolution = 'manual'",
                    event_id,
                )
                for entity_id in sorted(set(entity_ids)):
                    await conn.execute(
                        """
                        INSERT INTO calendar_event_participants (
                            event_id, email_norm, role, response_status, entity_id, resolution,
                            resolver_version
                        )
                        VALUES ($1, NULL, 'required', 'needsAction', $2, 'manual', $3)
                        ON CONFLICT DO NOTHING
                        """,
                        event_id,
                        entity_id,
                        RESOLVER_VERSION,
                    )
    except Exception as exc:
        logger.warning("calendar_manual_participants_failed error=%s", type(exc).__name__)
        _count_failure()


async def read_participants(pool: asyncpg.Pool, event_id: uuid.UUID) -> dict[str, Any]:
    """What a reader may see: entity ids and display names, an unresolved count, availability.

    ``available`` is False when the stored rows do not cover the event's current attendees at the
    current resolver version (a failed or pending resolution), so a reader shows "not resolved",
    never an empty list that reads as "no one you know". An entity whose posture is not
    ``active`` is withheld and counted, never named.
    """
    event = await pool.fetchrow(
        "SELECT metadata FROM calendar_events WHERE id = $1 "
        "-- quarantine-exempt: reads metadata only, filtered by is_quarantined_invitation",
        event_id,
    )
    if event is None:
        return {"available": False, "participants": [], "unresolved": 0, "withheld": 0}
    metadata = _metadata_object(event["metadata"])
    if is_quarantined_invitation(metadata):
        return {
            "available": True,
            "quarantined": True,
            "participants": [],
            "unresolved": 0,
            "withheld": 0,
        }
    rows = await pool.fetch(
        """
        SELECT p.email_norm, p.role, p.response_status, p.resolution,
               p.resolver_version, p.entity_id, e.canonical_name AS entity_name,
               COALESCE(e.posture, 'active') AS posture
        FROM calendar_event_participants p
        LEFT JOIN public.entities e ON e.id = p.entity_id
        WHERE p.event_id = $1
        ORDER BY p.role, e.canonical_name NULLS LAST, p.id
        """,
        event_id,
    )
    expected = {item["email"] for item in attendee_inputs(metadata)}
    stored = {
        r["email_norm"] for r in rows if r["email_norm"] is not None and r["resolution"] != "manual"
    }
    current = all(r["resolver_version"] == RESOLVER_VERSION for r in rows)
    available = expected == stored and current
    people: list[dict[str, Any]] = []
    unresolved = withheld = 0
    for r in rows:
        if r["resolution"] != "owner" and (r["response_status"] or "").lower() == "declined":
            continue  # not expected at the meeting: neither named nor counted
        if r["resolution"] in ("ambiguous", "unresolved"):
            unresolved += 1
        elif r["resolution"] == "resource":
            continue
        elif r["entity_id"] is not None and r["posture"] != "active":
            withheld += 1
        elif r["entity_id"] is not None:
            people.append(
                {
                    "entity_id": str(r["entity_id"]),
                    "display_name": r["entity_name"]
                    or "Unknown",  # the entity's name, never the invite's
                    "role": r["role"],
                    "response_status": r["response_status"],
                    "resolution": r["resolution"],
                }
            )
    return {
        "available": available,
        "participants": people,
        "unresolved": unresolved,
        "withheld": withheld,
    }


#: The escapes of an E'' string. Vertical tab is octal ``\\013``: E'\\v' is the letter v there.
ASCII_WHITESPACE_SQL = " \\t\\n\\r\\f\\013"


def _sql_normalized_email(expr: str) -> str:
    """SQL twin of :func:`normalize_email` for a text expression (NULL stays NULL)."""
    return (
        f"translate(btrim({expr}, E'{ASCII_WHITESPACE_SQL}'), "
        f"'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')"
    )


_PLAIN_ADDRESS_REGEX = "^[^@ \\t\\n\\r\\f\\v]+@[^@ \\t\\n\\r\\f\\v]+$"


#: Participant rows a reader may turn into a person: a resolved entity that is not a room, not the
#: owner's own seat, not declined, and active. Ambiguous and unresolved attendees are counted.
NAMEABLE_PARTICIPANTS_SQL: Final = """
    SELECT p.event_id AS event_id, p.entity_id AS entity_id
    FROM calendar_event_participants p
    JOIN public.entities pe ON pe.id = p.entity_id
    WHERE p.resolution IN ('exact', 'manual') AND p.role <> 'resource'
      AND p.entity_id IS NOT NULL AND COALESCE(pe.posture, 'active') = 'active'
      AND lower(COALESCE(p.response_status, '')) <> 'declined'
"""


def _not_ready_predicate(alias: str, version_param: str) -> str:
    """SQL: the event's stored provider rows do not cover exactly its current plain addresses.

    Compares the distinct normalized addresses of the event's attendees with the addresses of its
    provider rows at the current resolver version, so an event with no rows, stale rows, a changed
    attendee list or a half-written set is "not ready". The SQL twin of the ``available`` check in
    :func:`read_participants` (a parity test runs both).
    """
    metadata = f"{alias}.metadata"
    return f"""(
        SELECT COALESCE(array_agg(DISTINCT {_sql_normalized_email("att ->> 'email'")}), '{{}}')
        FROM jsonb_array_elements(
            CASE WHEN jsonb_typeof({metadata} -> 'attendees') = 'array'
                 THEN {metadata} -> 'attendees' ELSE '[]'::jsonb END) AS att
        WHERE jsonb_typeof(att) = 'object'
          AND {_sql_normalized_email("att ->> 'email'")} ~ '{_PLAIN_ADDRESS_REGEX}'
    ) IS DISTINCT FROM (
        SELECT COALESCE(array_agg(p.email_norm ORDER BY p.email_norm), '{{}}')
        FROM calendar_event_participants p
        WHERE p.event_id = {alias}.id AND p.resolution <> 'manual'
          AND p.resolver_version = {version_param}
    )"""


def unavailable_events_sql(alias: str = "e") -> str:
    """SQL (``$1`` event ids, ``$2`` resolver version): provider events whose rows are not ready.

    Quarantined invitations are never listed (they have no participants by design). A reader shows
    "attendees unavailable" for these rather than an empty list.
    """
    return f"""
        SELECT {alias}.id
        FROM calendar_events {alias}
        WHERE {alias}.id = ANY($1::uuid[])
          AND {alias}.metadata ->> 'source_type' = '{_PROVIDER_SOURCE}'
          AND NOT {quarantined_invitation_sql(alias)}
          AND {_not_ready_predicate(alias, "$2")}
    """


async def unavailable_event_ids(executor: Any, event_ids: Sequence[uuid.UUID]) -> set[uuid.UUID]:
    """The events among ``event_ids`` whose participants are unavailable. A failure propagates."""
    if not event_ids:
        return set()
    rows = await executor.fetch(unavailable_events_sql(), list(event_ids), RESOLVER_VERSION)
    return {row["id"] for row in rows}


#: Events the backfill attempted recently (written or failed), so an event that stays "not ready"
#: (a failing read, or an address the SQL and Python normalizers disagree on) is retried at most
#: hourly instead of holding the bounded window while the events behind it wait. Process-local and
#: best effort: a restart retries them, which is the only cost of losing it.
_RETRY_AFTER: Final = 3600.0
_MAX_ATTEMPTS_TRACKED: Final = 1000
_attempted_at: dict[uuid.UUID, float] = {}


def _recent_attempts(now: float) -> list[uuid.UUID]:
    for event_id in [k for k, at in _attempted_at.items() if now - at >= _RETRY_AFTER]:
        del _attempted_at[event_id]
    return list(_attempted_at)


def _remember_attempt(event_id: uuid.UUID, now: float) -> None:
    if len(_attempted_at) >= _MAX_ATTEMPTS_TRACKED:
        del _attempted_at[min(_attempted_at, key=_attempted_at.__getitem__)]
    _attempted_at[event_id] = now


async def backfill_participants(pool: asyncpg.Pool, *, limit: int = BACKFILL_BATCH) -> int:
    """Derive rows for provider events whose stored rows do not match their attendees.

    Bounded and resumable with no stored cursor: a non-quarantined provider event whose provider
    rows do not cover exactly its current plain-address attendees at the current resolver version
    (none, stale, a changed list, a partial set) is selected and, once written, not selected again.
    An event attempted in the last hour is skipped, so one that stays not ready cannot hold the
    window while the events behind it wait. Quarantined events lose any provider rows they hold
    (a verdict can turn after a row was written). Returns the number of events written.
    """
    await pool.execute(
        f"""
        DELETE FROM calendar_event_participants p
        USING calendar_events e
        WHERE p.event_id = e.id AND p.resolution <> 'manual' AND {QUARANTINED_INVITATION_SQL}
        """
    )
    now = time.monotonic()
    rows = await pool.fetch(
        f"""
        SELECT e.id
        FROM calendar_events e
        WHERE e.metadata ->> 'source_type' = '{_PROVIDER_SOURCE}'
          AND NOT {QUARANTINED_INVITATION_SQL}
          AND NOT (e.id = ANY($3::uuid[]))
          AND {_not_ready_predicate("e", "$1")}
        ORDER BY e.id
        LIMIT $2
        """,
        RESOLVER_VERSION,
        limit,
        _recent_attempts(now),
    )
    written = 0
    for row in rows:
        _remember_attempt(row["id"], now)
        if await sync_event_participants(pool, row["id"]) == "written":
            written += 1
    return written


def other_people(read: dict[str, Any]) -> list[dict[str, Any]]:
    """The named people of a :func:`read_participants` result, without the owner's own seat."""
    return [p for p in read["participants"] if p["resolution"] != "owner"]
