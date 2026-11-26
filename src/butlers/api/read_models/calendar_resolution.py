"""Attach resolution options to the radar's overlap issues (bu-5ii4gj.11 slice R1b).

The pure compiler lives in :mod:`butlers.core.temporal.conflict_resolution`; this module only
adapts workspace rows to it: classifies each side of an overlap, finds the windows free on every
owner-busy calendar read, and decides whether a source is too stale to offer anything. It reads
nothing itself and never raises: a row it cannot read leaves that issue without options.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, tzinfo
from typing import Any

from butlers.core.temporal import conflict_resolution as cr
from butlers.core.temporal.conflicts import ConflictCandidate, DetectedIssue
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


def resolution_event(row: Mapping[str, Any]) -> cr.ResolutionEvent:
    """One side of an overlap from a workspace row: class, owner response, attendee count."""
    metadata = _metadata(row)
    attendees = [a for a in metadata.get("attendees") or [] if isinstance(a, Mapping)]
    mine = next((a for a in attendees if _is_true(a.get("self"))), None)
    others = [a for a in attendees if not _is_true(a.get("self"))]
    response = None
    if mine is not None:
        raw = mine.get("responseStatus", mine.get("response_status"))
        response = raw if isinstance(raw, str) else None
    organizer_self = mine is not None and _is_true(mine.get("organizer"))
    event_class: cr.EventClass
    if row.get("source_time_role") == "butler":
        event_class = "butlers_block"
    elif organizer_self or mine is None:
        event_class = "owner_organized"
    else:
        event_class = "invited"
    recurring = bool(row.get("recurrence_rule"))
    return cr.ResolutionEvent(
        entry_id=str(row["instance_id"]),
        provider_event_id=str(row.get("origin_ref") or ""),
        calendar_id=row.get("calendar_id"),
        start_at=row["instance_starts_at"],
        end_at=row["instance_ends_at"],
        etag=row.get("event_etag"),
        event_class=event_class,
        has_other_attendees=bool(others),
        owner_response=response,
        # The instance's own start is the occurrence key; the series is never offered an edit.
        instance_start_at=row["instance_starts_at"] if recurring else None,
    )


def _stale(rows: Iterable[Mapping[str, Any]], now: datetime) -> bool:
    """A provider source that never synced, or not within the deadman's threshold."""
    threshold = projection_staleness_threshold()
    for row in rows:
        if row.get("source_kind") != SOURCE_KIND_PROVIDER:
            continue
        synced = row.get("last_synced_at")
        if synced is None or now - synced > threshold:
            return True
    return False


def attach_resolution_options(
    issues: Sequence[DetectedIssue],
    rows_by_entry: Mapping[str, Mapping[str, Any]],
    busy: Iterable[ConflictCandidate],
    *,
    now: datetime,
    start: datetime,
    end: datetime,
    zone: tzinfo,
) -> None:
    """Set ``options``, ``issue_digest`` and ``options_reason`` on each overlap issue in place."""
    pending = [i for i in issues if i.kind == "overlap" and i.pair_id and len(i.events) == 2]
    if not pending:
        return
    free = cr.free_windows(((c.start_at, c.end_at) for c in busy), max(start, now), end, zone)
    for issue in pending:
        try:
            sides = [rows_by_entry[ref.entry_id] for ref in issue.events]
            events = [resolution_event(row) for row in sides]
            result = cr.options_for(
                str(issue.pair_id),
                events[0],
                events[1],
                free,
                not_before=now,
                stale_source=_stale(sides, now),
            )
        except Exception:
            logger.warning("resolution options could not be compiled", exc_info=True)
            continue
        issue.options = result.options
        issue.options_reason = result.reason
        issue.issue_digest = cr.issue_digest(events, result.options)
