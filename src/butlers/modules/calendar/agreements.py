"""What the owner accepted: the agreement snapshot (bu-5ii4gj.10 slice A-S1).

When the owner's own response to a provider event they did not organise becomes ``accepted`` or
``tentative``, :func:`record_owner_agreement` stores the terms (start, end) and digests of the
location and of the other attendees in ``calendar_owner_agreements``, one row per occurrence. A
later edit to the event never rewrites the row: the row is what the owner agreed to, which the
drift job (a later slice) compares the current event against. The owner's response becoming
``declined`` or ``needsAction``, or the event losing its owner seat or its provider source, ends
the agreement. Nothing here raises: the event write has already committed.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import asyncpg

from butlers.core.temporal.calendar_provenance import is_quarantined_invitation
from butlers.modules.calendar.participants import _is_true, _metadata_object, attendee_inputs
from butlers.modules.calendar.participants import _status as _response_status

logger = logging.getLogger(__name__)

_PROVIDER_SOURCE = "provider_event"
_AGREED = frozenset({"accepted", "tentative"})


def digest(value: str | None) -> str | None:
    """A short stable digest of free text; None for an absent or blank value."""
    if value is None or not value.strip():
        return None
    return hashlib.sha256(" ".join(value.split()).casefold().encode()).hexdigest()[:32]


def attendees_digest(addresses: list[str]) -> str:
    """A digest of the other attendees (normalized addresses, order-free)."""
    return hashlib.sha256("\n".join(sorted(set(addresses))).encode()).hexdigest()[:32]


def owner_agreement(metadata: Mapping[str, Any]) -> tuple[str, list[str]] | None:
    """The owner's agreed response and the other attendees' addresses, else None.

    None when the owner has no seat, organised the event themself, or has not accepted. A
    quarantined invitation never counts: nothing was agreed to.
    """
    if is_quarantined_invitation(metadata):
        return None
    inputs = attendee_inputs(metadata)
    owner = [i for i in inputs if _is_true(i["raw"].get("self"))]
    if not owner or any(_is_true(i["raw"].get("organizer")) for i in owner):
        return None
    response = _response_status(owner[0]["raw"])
    if response not in _AGREED:
        return None
    others = [i["email"] for i in inputs if not _is_true(i["raw"].get("self"))]
    return response, others


async def record_owner_agreement(pool: asyncpg.Pool, event_id: uuid.UUID) -> str:
    """Record or end the owner's agreement for one provider event. Never raises.

    Returns ``recorded``, ``kept`` (an agreement already exists and is left as agreed), ``ended``
    or ``skipped`` (not a provider event, gone, or recurring: occurrences are a later slice).
    """
    try:
        row = await pool.fetchrow(
            "SELECT metadata, starts_at, ends_at, location, recurrence_rule, status "
            "FROM calendar_events WHERE id = $1 "
            "-- quarantine-exempt: reads metadata only, filtered by is_quarantined_invitation",
            event_id,
        )
        if row is None:
            return "skipped"
        metadata = _metadata_object(row["metadata"])
        if metadata.get("source_type") != _PROVIDER_SOURCE or row["recurrence_rule"]:
            return "skipped"
        agreed = None if row["status"] == "cancelled" else owner_agreement(metadata)
        if agreed is None:
            await pool.execute(
                "DELETE FROM calendar_owner_agreements WHERE event_id = $1", event_id
            )
            return "ended"
        response, others = agreed
        starts: datetime = row["starts_at"]
        inserted = await pool.fetchval(
            """
            INSERT INTO calendar_owner_agreements (
                event_id, occurrence_start, agreed_starts_at, agreed_ends_at,
                location_digest, attendees_digest, response_status)
            SELECT $1, $2, $2, $3, $4, $5, $6
            -- A moved event keeps the terms the owner agreed to: one row per event until
            -- occurrences are tracked separately (recurring events are skipped for now).
            WHERE NOT EXISTS (SELECT 1 FROM calendar_owner_agreements WHERE event_id = $1)
            ON CONFLICT (event_id, occurrence_start) DO NOTHING
            RETURNING id
            """,
            event_id,
            starts,
            row["ends_at"],
            digest(row["location"]),
            attendees_digest(others),
            response,
        )
        return "recorded" if inserted else "kept"
    except Exception as exc:  # the event write has already committed; this must not undo it
        logger.warning("calendar_owner_agreement_failed error=%s", type(exc).__name__)
        return "skipped"
