"""Attach resolution options to the radar's overlap issues (bu-5ii4gj.11 slice R1b).

The pure compiler lives in :mod:`butlers.core.temporal.conflict_resolution`; this module adapts
workspace rows to it and fails toward "no options" everywhere:

* options are attached only to an unfiltered, complete read, and only when no provider source in
  the whole fleet is stale (``last_success_at``, which a failed sync never advances);
* free time is working-day time in the owner's saved zone, minus everything that could hold the
  owner: owner-attended events on unclassified calendars and all-day opaque events included;
* an event's ownership is proven (the owner is its organizer) before it may be moved, and the
  owner-role copy of an event decides its class, etag and calendar.

It never raises: a row it cannot read leaves that issue without options.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

from butlers.core.general_settings import load_configured_general_timezone
from butlers.core.temporal import conflict_resolution as cr
from butlers.core.temporal.calendar_provenance import counts_toward_owner_load
from butlers.core.temporal.calendar_source_roles import (
    DEFAULT_ROLE,
    asserts_owner_time,
    normalize_role,
)
from butlers.core.temporal.conflicts import DetectedIssue
from butlers.modules.calendar.constants import SOURCE_KIND_PROVIDER, projection_staleness_threshold

logger = logging.getLogger(__name__)


def resolution_options_live() -> bool:
    """Read at call time so a test (or the R2 cutover) can flip the dark flag."""
    return cr.RESOLUTION_OPTIONS_LIVE


def _metadata(row: Mapping[str, Any]) -> dict[str, Any]:
    value = row.get("event_metadata")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _is_true(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.strip().lower() == "true")


def _classify(row: Mapping[str, Any], metadata: dict[str, Any]) -> cr.EventClass:
    """Butlers block, owner-organised (proven), invited, or unproven (offered nothing).

    The owner organises an event only when their own attendee row says so, or the stored organizer
    is their own address (the comparison ``is_quarantined_invitation`` makes). An event with no
    attendees is the owner's own only on an owner-role calendar. Anything else is unproven: it is
    never moved, and never answered.
    """
    attendees = [a for a in metadata.get("attendees") or [] if isinstance(a, Mapping)]
    mine = next((a for a in attendees if _is_true(a.get("self"))), None)
    if row.get("source_time_role") == "butler":
        return "butlers_block"
    if mine is None:
        solo = not attendees and normalize_role(row.get("source_time_role")) == "owner"
        return "owner_organized" if solo else "unproven"
    organizer = metadata.get("organizer")
    email = mine.get("email")
    own_address = (
        isinstance(organizer, str)
        and isinstance(email, str)
        and organizer.strip().casefold() == email.strip().casefold()
        and bool(organizer.strip())
    )
    return "owner_organized" if _is_true(mine.get("organizer")) or own_address else "invited"


def resolution_event(row: Mapping[str, Any], *, entry_id: str | None = None) -> cr.ResolutionEvent:
    """One side of an overlap from a workspace row: class, owner response, attendee count."""
    metadata = _metadata(row)
    attendees = [a for a in metadata.get("attendees") or [] if isinstance(a, Mapping)]
    mine = next((a for a in attendees if _is_true(a.get("self"))), None)
    others = [a for a in attendees if not _is_true(a.get("self"))]
    response = None
    if mine is not None:
        raw = mine.get("responseStatus", mine.get("response_status"))
        response = raw if isinstance(raw, str) else None
    recurring = bool(row.get("recurrence_rule"))
    return cr.ResolutionEvent(
        entry_id=entry_id or str(row["instance_id"]),
        provider_event_id=str(row.get("origin_ref") or ""),
        calendar_id=row.get("calendar_id"),
        start_at=row["instance_starts_at"],
        end_at=row["instance_ends_at"],
        etag=row.get("event_etag"),
        event_class=_classify(row, metadata),
        has_other_attendees=bool(others),
        owner_response=response,
        recurring=recurring,
        # The instance's own start is the occurrence key; the series is never offered an edit.
        instance_start_at=row["instance_starts_at"] if recurring else None,
    )


def blocks_owner(row: Mapping[str, Any]) -> bool:
    """Whether a row could hold the owner: fail toward busy, never toward a free slot.

    Counted: the owner's own time and butler holds, owner-attended events on an unclassified
    calendar, and all-day opaque events (leave, out of office). Not counted: cancelled events, an
    event the owner declined or marked free, and another person's calendar.
    """
    status = str(row.get("instance_status") or row.get("event_status") or "confirmed")
    if status.lower() == "cancelled":
        return False
    metadata = _metadata(row)
    if not counts_toward_owner_load(metadata):
        return False
    role = normalize_role(row.get("source_time_role"))
    return role == DEFAULT_ROLE or asserts_owner_time(role, metadata)


def busy_intervals(rows: Iterable[Mapping[str, Any]]) -> list[tuple[datetime, datetime]]:
    return [(r["instance_starts_at"], r["instance_ends_at"]) for r in rows if blocks_owner(r)]


async def any_provider_source_stale(db: Any, now: datetime) -> bool:
    """True when any provider source never synced successfully or not within the threshold.

    Reads ``last_success_at``: ``last_synced_at`` advances on a failed attempt, so it would call a
    source that has failed for days fresh. An unreadable source list counts as stale.
    """
    from butlers.api.read_models.calendar_workspace import query_calendar_sources

    try:
        sources, failed = await query_calendar_sources(db)
    except Exception:
        logger.warning("resolution options: source list unreadable", exc_info=True)
        return True
    if failed:
        return True
    threshold = projection_staleness_threshold()
    for source in sources:
        if source.source_kind != SOURCE_KIND_PROVIDER:
            continue
        meta = source.source_metadata
        if isinstance(meta, dict) and meta.get("sync_enabled") is False:
            continue  # an operator-disabled source is not a failure
        ok = source.last_success_at
        if ok is None or now - ok > threshold:
            return True
    return False


async def owner_zone(db: Any) -> tzinfo | None:
    """The owner's saved zone, or None when it was never saved or cannot be read."""
    for name in sorted(db.butlers_with_module("calendar") or db.butler_names or []):
        try:
            zone = await load_configured_general_timezone(db.pool(name))
            return ZoneInfo(zone) if zone else None
        except Exception:
            logger.warning("resolution options: owner zone unreadable", exc_info=True)
            return None
    return None


def _owner_copy(
    row: Mapping[str, Any], copies: Mapping[str, Sequence[Mapping[str, Any]]], key: str
) -> Mapping[str, Any]:
    """The owner-role copy of the same provider event, else the surviving row itself."""
    for candidate in copies.get(key, ()):
        if normalize_role(candidate.get("source_time_role")) == "owner":
            return candidate
    return row


async def attach_resolution_options(
    db: Any,
    issues: Sequence[DetectedIssue],
    rows_by_entry: Mapping[str, Mapping[str, Any]],
    all_rows: Iterable[Mapping[str, Any]],
    identity_keys: Mapping[str, str],
    *,
    now: datetime,
    start: datetime,
    end: datetime,
    gap_minutes: int,
    complete: bool,
    filtered: bool,
) -> None:
    """Set ``options``, ``issue_digest`` and ``options_reason`` on each overlap issue in place.

    Leaves every issue without options on a filtered or partial read (the busy picture is
    incomplete), when the owner's zone is unknown, or when any provider source is stale.
    """
    pending = [i for i in issues if i.kind == "overlap" and i.pair_id and len(i.events) == 2]
    if not pending or filtered or not complete:
        return
    zone = await owner_zone(db)
    if zone is None:
        return
    stale = await any_provider_source_stale(db, now)
    rows = list(all_rows)
    free = cr.free_windows(
        busy_intervals(rows),
        max(start, now),
        end,
        zone,
        gap=timedelta(minutes=gap_minutes),
    )
    copies: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        key = identity_keys.get(str(row["instance_id"]))
        if key:
            copies.setdefault(key, []).append(row)
    for issue in pending:
        try:
            events = []
            for ref in issue.events:
                row = rows_by_entry[ref.entry_id]
                best = _owner_copy(row, copies, identity_keys.get(ref.entry_id, ""))
                events.append(resolution_event(best, entry_id=ref.entry_id))
            result = cr.options_for(
                str(issue.pair_id), events[0], events[1], free, not_before=now, stale_source=stale
            )
        except Exception:
            logger.warning("resolution options could not be compiled", exc_info=True)
            continue
        issue.options = result.options
        issue.options_reason = result.reason
        issue.issue_digest = cr.issue_digest(events, result.options) if result.options else None
