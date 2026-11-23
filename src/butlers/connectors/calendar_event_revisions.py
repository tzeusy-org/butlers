"""Append every observed Google Calendar event version to a journal (bu-uhbhzq.1 slice 2).

``connectors.calendar_event_revisions`` (core_366) is the calendar's own evidence row: one row per
provider event version, keyed on ``(endpoint_identity, calendar_id, event_id, etag)``. Google
changes the etag on every modification, RSVPs included, so a replayed page adds nothing and an
edit adds a row. The connector calls :func:`record_calendar_revisions` for each fetched batch
before it submits anything or saves its sync token; a failed write raises
:class:`EvidenceNotCommitted`, so the token stays where it was and the next cycle journals the same
delta again.

Privacy (owner decision 2026-11-22): titles, locations, times and attendees are kept verbatim;
descriptions are scrubbed of links and passcodes, conference data is reduced to the solution name
and key type, and attachment, source and gadget links are dropped. A policy-blocked event keeps
structure and times only (``raw`` is NULL). Policy evaluation that cannot be resolved raises, so the
batch is held rather than journaled as blocked or allowed.

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

_MEETING_HOSTS = (
    r"zoom\.(?:us|com)|teams\.microsoft\.com|teams\.live\.com|meet\.google\.com|"
    r"(?:[\w-]+\.)?webex\.com|gotomeeting\.com"
)
_URL = re.compile(
    r"(?i)\b(?:https?://|www\.)\S+"
    r"|\b[\w-]+(?:\.[\w-]+)*\.(?:google|zoom)\.[a-z]+/\S*"
    # Scheme-less meeting hosts, with or without a subdomain or a path.
    rf"|\b(?:[\w-]+\.)*(?:{_MEETING_HOSTS})(?:/\S*)?"
)
# "passcode: 123456", "Password - abc", "PIN 1234 #", "meeting id" is not a secret, so not matched.
_PASSCODE = re.compile(
    r"(?i)\b(pass\s?code|password|pin|access\s?code|dial[- ]in code)\b\s*[:=\-]?\s*#?\s*[\w#*-]+"
)
_REMOVED = "[removed]"


def scrub_description(text: str) -> str:
    """The description with links and passcodes replaced by a marker."""
    return _PASSCODE.sub(_REMOVED, _URL.sub(_REMOVED, text))


def _scrub_conference(conference: Any) -> dict[str, Any]:
    """Only the provider's solution name and key type survive.

    ``conferenceId`` is the Meet join code and ``notes`` and ``parameters`` hold dial-ins, so
    nothing else is kept.
    """
    kept: dict[str, Any] = {}
    solution = conference.get("conferenceSolution") if isinstance(conference, dict) else None
    if isinstance(solution, dict):
        scrubbed: dict[str, Any] = {}
        if isinstance(solution.get("name"), str):
            scrubbed["name"] = solution["name"]
        key = solution.get("key")
        if isinstance(key, dict) and isinstance(key.get("type"), str):
            scrubbed["key"] = {"type": key["type"]}
        if scrubbed:
            kept["conferenceSolution"] = scrubbed
    return kept


def _keep(value: Any, keys: tuple[str, ...]) -> Any:
    return {k: value[k] for k in keys if k in value} if isinstance(value, dict) else value


def scrub_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """The verbatim provider event minus join links, passcodes and link-bearing fields."""
    cleaned = copy.deepcopy(dict(event))
    cleaned.pop("hangoutLink", None)
    if isinstance(cleaned.get("description"), str):
        cleaned["description"] = scrub_description(cleaned["description"])
    if "conferenceData" in cleaned:
        cleaned["conferenceData"] = _scrub_conference(cleaned["conferenceData"])
    if isinstance(cleaned.get("attachments"), list):
        # Title and type only: fileUrl, fileId and iconLink are links into the owner's files.
        cleaned["attachments"] = [
            _keep(a, ("title", "mimeType")) for a in cleaned["attachments"] if isinstance(a, dict)
        ]
    if isinstance(cleaned.get("source"), dict):
        cleaned["source"] = _keep(cleaned["source"], ("title",))
    if isinstance(cleaned.get("gadget"), dict):
        cleaned["gadget"] = {
            k: v for k, v in cleaned["gadget"].items() if k not in {"link", "iconLink"}
        }
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


#: Fields a policy-blocked row retains; its fallback version key is derived from these only.
_STRUCTURE_KEYS = (
    "id",
    "recurringEventId",
    "originalStartTime",
    "updated",
    "sequence",
    "status",
    "start",
    "end",
)


def _etag(event: Mapping[str, Any], *, blocked: bool = False) -> str:
    """The provider's etag; a content hash when it is absent, so the dedupe key is never empty.

    A blocked event's hash covers only the structure the row keeps, never the excluded title or
    attendees, so a private edit neither adds a row nor leaks through the key.
    """
    etag = event.get("etag")
    if isinstance(etag, str) and etag:
        return etag
    material: Mapping[str, Any] = (
        {k: event.get(k) for k in _STRUCTURE_KEYS} if blocked else scrub_event(event)
    )
    digest = hashlib.sha256(
        json.dumps(material, sort_keys=True, default=str).encode("utf-8")
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
        _strip_nul(_etag(event, blocked=policy_blocked)),
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
