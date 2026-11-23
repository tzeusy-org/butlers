"""Append every observed Google Calendar event version to a journal (bu-uhbhzq.1 slice 2).

``connectors.calendar_event_revisions`` (core_366) is the calendar's own evidence row: one row per
provider event version, keyed on ``(endpoint_identity, calendar_id, event_id, etag)``. Google
changes the etag on every modification, RSVPs included, so a replayed page adds nothing and an
edit adds a row. The connector calls :func:`record_calendar_revisions` for each fetched batch
before it submits anything or saves its sync token; a failed write raises
:class:`EvidenceNotCommitted`, so the token stays where it was and the next cycle journals the same
delta again.

Privacy (owner decision 2026-11-22): titles, locations, times and attendees are kept verbatim;
descriptions are scrubbed of links and passcodes, and conference join details are removed. A
policy-blocked event keeps structure and times only (``raw`` is NULL).

The kind of change (moved, retitled, cancelled, RSVP) is not stored. A reader derives it by
diffing an event's consecutive revisions.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from typing import Any, Final

from butlers.connectors.evidence_journal import EvidenceNotCommitted
from butlers.ingestion_bearer_scrub import scrub_evidence_json

logger = logging.getLogger(__name__)

OBSERVATIONS: Final = frozenset({"baseline", "change", "resync"})

_INSERT = """
INSERT INTO connectors.calendar_event_revisions (
    endpoint_identity, calendar_id, event_id, recurring_event_id, original_start_time, etag,
    provider_updated, sequence, status, starts_at, ends_at, all_day, observation,
    policy_blocked, raw
) VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, ($15::text)::jsonb
)
ON CONFLICT (endpoint_identity, calendar_id, event_id, etag) DO NOTHING
"""

_URL = re.compile(
    r"(?i)\b(?:https?://|www\.)\S+|\b[\w-]+(?:\.[\w-]+)*\.(?:google|zoom)\.[a-z]+/\S*"
)
# "passcode: 123456", "Password - abc", "PIN 1234 #", "meeting id" is not a secret, so not matched.
_PASSCODE = re.compile(
    r"(?i)\b(pass\s?code|password|pin|access\s?code|dial[- ]in code)\b\s*[:=\-]?\s*#?\s*[\w#*-]+"
)
_REMOVED = "[removed]"
_CONFERENCE_SECRET_KEYS = frozenset(
    {"uri", "label", "pin", "password", "passcode", "accessCode", "meetingCode", "regionCode"}
)


def scrub_description(text: str) -> str:
    """The description with links and passcodes replaced by a marker."""
    return _PASSCODE.sub(_REMOVED, _URL.sub(_REMOVED, text))


def _scrub_conference(conference: Any) -> Any:
    """Conference data without join links, pins or passcodes; the provider's solution stays."""
    if not isinstance(conference, dict):
        return conference
    cleaned = {k: v for k, v in conference.items() if k != "entryPoints"}
    entry_points = conference.get("entryPoints")
    if isinstance(entry_points, list):
        cleaned["entryPoints"] = [
            {k: v for k, v in point.items() if k not in _CONFERENCE_SECRET_KEYS}
            for point in entry_points
            if isinstance(point, dict)
        ]
    return cleaned


def scrub_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """The verbatim provider event minus join links, passcodes and description links."""
    cleaned = copy.deepcopy(dict(event))
    cleaned.pop("hangoutLink", None)
    if isinstance(cleaned.get("description"), str):
        cleaned["description"] = scrub_description(cleaned["description"])
    if "conferenceData" in cleaned:
        cleaned["conferenceData"] = _scrub_conference(cleaned["conferenceData"])
    return cleaned


def _strip_nul(value: Any) -> Any:
    """PostgreSQL text and jsonb cannot hold U+0000."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {_strip_nul(k): _strip_nul(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_strip_nul(v) for v in value]
    return value


def _etag(event: Mapping[str, Any]) -> str:
    """The provider's etag; a content hash when it is absent, so the dedupe key is never empty."""
    etag = event.get("etag")
    if isinstance(etag, str) and etag:
        return etag
    digest = hashlib.sha256(
        json.dumps(event, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


def _when(block: Any, parse: Callable[[str], datetime | None]) -> tuple[datetime | None, bool]:
    if not isinstance(block, dict):
        return None, False
    if block.get("dateTime"):
        return parse(block["dateTime"]), False
    if block.get("date"):
        return parse(block["date"]), True
    return None, False


def revision_row(
    event: Mapping[str, Any],
    *,
    endpoint_identity: str,
    calendar_id: str,
    observation: str,
    policy_blocked: bool,
    parse: Callable[[str], datetime | None],
) -> tuple[Any, ...]:
    """One insert tuple for a provider event version (no I/O)."""
    if observation not in OBSERVATIONS:
        raise ValueError(f"unknown observation {observation!r}")
    starts_at, all_day = _when(event.get("start"), parse)
    ends_at, _ = _when(event.get("end"), parse)
    original, _ = _when(event.get("originalStartTime"), parse)
    updated = event.get("updated")
    sequence = event.get("sequence")
    raw = None
    if not policy_blocked:
        raw = json.dumps(scrub_evidence_json(_strip_nul(scrub_event(event))), default=str)
    return (
        endpoint_identity,
        calendar_id,
        _strip_nul(str(event.get("id") or "")),
        _strip_nul(event.get("recurringEventId")),
        original,
        _strip_nul(_etag(event)),
        parse(updated) if isinstance(updated, str) else None,
        sequence if isinstance(sequence, int) else None,
        event.get("status") if isinstance(event.get("status"), str) else None,
        starts_at,
        ends_at,
        all_day,
        observation,
        policy_blocked,
        raw,
    )


async def record_calendar_revisions(
    pool: Any,
    *,
    endpoint_identity: str,
    calendar_id: str,
    events: Iterable[Mapping[str, Any]],
    observation: str,
    parse: Callable[[str], datetime | None],
    blocked: Callable[[Mapping[str, Any]], bool] = lambda _event: False,
) -> int:
    """Append a revision per event in one transaction; duplicates collapse. Returns rows tried.

    Fails closed: no pool, or any database failure, raises :class:`EvidenceNotCommitted` carrying a
    reason code only, and nothing is committed.
    """
    if observation not in OBSERVATIONS:
        raise ValueError(f"unknown observation {observation!r}")
    try:
        rows = [
            revision_row(
                event,
                endpoint_identity=endpoint_identity,
                calendar_id=calendar_id,
                observation=observation,
                policy_blocked=bool(blocked(event)),
                parse=parse,
            )
            for event in events
            if event.get("id")
        ]
    except Exception as exc:  # noqa: BLE001
        # A scrub failure must not store raw text: nothing is committed and the cycle holds.
        raise EvidenceNotCommitted(type(exc).__name__) from None
    if not rows:
        return 0
    if pool is None:
        raise EvidenceNotCommitted("no_pool")
    try:
        async with pool.acquire() as conn, conn.transaction():
            await conn.executemany(_INSERT, rows)
    except Exception as exc:  # noqa: BLE001
        # Class name only, no chained cause: the original text can echo event content.
        raise EvidenceNotCommitted(type(exc).__name__) from None
    return len(rows)
