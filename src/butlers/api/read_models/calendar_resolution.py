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

import logging
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

from butlers.core.general_settings import load_configured_general_timezone
from butlers.core.temporal import conflict_resolution as cr
from butlers.core.temporal.calendar_source_roles import (
    normalize_role,
)
from butlers.core.temporal.conflicts import DetectedIssue
from butlers.core.temporal.resolution_rows import (
    busy_intervals,
    resolution_event,
)
from butlers.modules.calendar.constants import SOURCE_KIND_PROVIDER, projection_staleness_threshold
from butlers.modules.calendar.source_staleness import source_is_stale

logger = logging.getLogger(__name__)


def resolution_options_live() -> bool:
    """Read at call time so a test (or the R2 cutover) can flip the dark flag."""
    return cr.RESOLUTION_OPTIONS_LIVE


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
        if _source_stale(source, now, threshold):
            return True
    return False


def _source_stale(source: Any, now: datetime, threshold: timedelta) -> bool:
    """One source's own verdict (see ``source_is_stale``)."""
    return source_is_stale(
        last_success_at=source.last_success_at,
        last_error_at=source.last_error_at,
        full_sync_required=source.full_sync_required,
        now=now,
        threshold=threshold,
    )


async def owner_zone(db: Any) -> tzinfo | None:
    """The owner's saved zone, or None when it was never saved or cannot be read.

    TODO(bu-5ii4gj.5): use the shared target-zone function once #6191 lands (it also honours the
    clock's confirmed basis); until then this is the settings zone alone.
    """
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
    if not pending:
        return
    rows = list(all_rows)
    copies: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        key = identity_keys.get(str(row["instance_id"]))
        if key:
            copies.setdefault(key, []).append(row)
    compiled: list[tuple[DetectedIssue, list[cr.ResolutionEvent]]] = []
    issue_context: dict[int, dict[str, dict[str, Any]]] = {}
    for issue in pending:
        try:
            events = []
            context: dict[str, dict[str, Any]] = {}
            for ref in issue.events:
                row = rows_by_entry[ref.entry_id]
                best = _owner_copy(row, copies, identity_keys.get(ref.entry_id, ""))
                events.append(resolution_event(best, entry_id=ref.entry_id))
                context[ref.entry_id] = {
                    "instance_id": str(best["instance_id"]),
                    "etag": best.get("event_etag"),
                    # A series' occurrence is written as its own instance, but the etag the owner
                    # saw is the series': the executor checks that one before it writes.
                    "series_event_id": (
                        str(best["origin_ref"]) if best.get("recurrence_rule") else None
                    ),
                }
            # Every overlap gets its state digest, whether or not options can be offered: "keep
            # both" must keep suppressing on a filtered, partial or zone-less read.
            issue.state_digest = cr.state_digest(str(issue.pair_id), events)
        except Exception:
            logger.warning("resolution state could not be read", exc_info=True)
            continue
        issue.option_context = {}
        compiled.append((issue, events))
        issue_context[id(issue)] = context
    if filtered or not complete:
        return
    zone = await owner_zone(db)
    if zone is None:
        return
    stale = await any_provider_source_stale(db, now)
    free = cr.free_windows(
        busy_intervals(rows),
        max(start, now),
        end,
        zone,
        gap=timedelta(minutes=gap_minutes),
    )
    for issue, events in compiled:
        try:
            result = cr.options_for(
                str(issue.pair_id), events[0], events[1], free, not_before=now, stale_source=stale
            )
        except Exception:
            logger.warning("resolution options could not be compiled", exc_info=True)
            continue
        context = issue_context[id(issue)]
        issue.options = result.options
        issue.option_context = {
            o.id: context[o.rationale_fields["entry_id"]]
            for o in result.options
            if o.rationale_fields.get("entry_id") in context
        }
        issue.options_reason = result.reason
        issue.issue_digest = cr.issue_digest(events, result.options) if result.options else None
