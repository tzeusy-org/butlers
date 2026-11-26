"""The moved-under-you changes feed (bu-5ii4gj.11 N3): organizer-made changes from the journal.

The revision journal is reached only through ``public.read_calendar_event_changes`` (core_386), a
narrow SECURITY DEFINER reader on the Switchboard role. Titles and the calendar entry to open come
from the projection the owner already reads, never from the journal.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from butlers.api.db import DatabaseManager
from butlers.core.temporal.invitation_quarantine import QUARANTINED_INVITATION_SQL

logger = logging.getLogger(__name__)

#: The journal reader clamps to the same bounds; the route refuses a window past them.
CHANGES_MAX_AGE = timedelta(days=14)
CHANGES_LIMIT = 200

_READ_SQL = "SELECT * FROM public.read_calendar_event_changes($1, $2)"
# The projection knows which events are untrusted invitations: their text never leaves, so the
# title, entry and journal location of a quarantined (or unprojected) event are left out.
_TITLES_SQL = f"""
SELECT e.origin_ref, i.origin_instance_ref, e.title, i.id AS instance_id, i.starts_at,
       {QUARANTINED_INVITATION_SQL} AS quarantined
FROM calendar_events e JOIN calendar_event_instances i ON i.event_id = e.id
WHERE e.origin_ref = ANY($1::text[]) OR i.origin_instance_ref = ANY($1::text[])
"""


@dataclass(frozen=True)
class Change:
    """One organizer-made change, with the projection's title and entry when it is known."""

    event_id: str
    calendar_id: str
    observed_at: datetime
    prior_known: bool
    all_day: bool
    prior_start: datetime | None
    new_start: datetime | None
    prior_end: datetime | None
    new_end: datetime | None
    prior_status: str | None
    new_status: str | None
    prior_location: str | None
    new_location: str | None
    organizer_self: bool | None
    title: str | None = None
    entry_id: str | None = None

    @property
    def fields(self) -> list[str]:
        if not self.prior_known:
            return []
        names = []
        if self.prior_start != self.new_start or self.prior_end != self.new_end:
            names.append("time")
        if self.prior_status != self.new_status:
            names.append("status")
        if self.prior_location != self.new_location:
            names.append("location")
        return names


@dataclass(frozen=True)
class ChangesRead:
    changes: list[Change]
    #: ``False`` when the journal read failed: the list is then unknown, never empty.
    available: bool
    #: ``True`` when more changes exist than the cap returned.
    truncated: bool = False


def _change(row: Any) -> Change:
    return Change(**{name: row[name] for name in Change.__dataclass_fields__ if name in row.keys()})


async def query_calendar_changes(db: DatabaseManager, *, since: datetime) -> ChangesRead:
    """Changes observed after ``since``, newest first; titles are best effort, never required."""
    try:
        rows = await db.pool("switchboard").fetch(_READ_SQL, since, CHANGES_LIMIT + 1)
    except Exception:
        logger.warning("calendar changes: journal reader failed", exc_info=True)
        return ChangesRead(changes=[], available=False)
    truncated = len(rows) > CHANGES_LIMIT
    changes = [_change(r) for r in rows[:CHANGES_LIMIT]]
    if not changes:
        return ChangesRead(changes=[], available=True)
    refs = sorted({c.event_id for c in changes})
    try:
        found, _failed = await db.fan_out_with_status(_TITLES_SQL, (refs,))
    except Exception:
        logger.warning("calendar changes: titles unavailable", exc_info=True)
        return ChangesRead(changes=changes, available=True, truncated=truncated)
    by_ref: dict[str, list[Any]] = {}
    for rows_of in found.values():
        for r in rows_of:
            for key in (r["origin_ref"], r["origin_instance_ref"]):
                by_ref.setdefault(key, []).append(r)
    enriched = []
    for change in changes:
        candidates = by_ref.get(change.event_id, [])
        # A recurring series shares one ref: the instance at the changed time is the one to open.
        starts = {t for t in (change.new_start, change.prior_start) if t is not None}
        match = next((r for r in candidates if r["starts_at"] in starts), None)
        match = match or (candidates[0] if len(candidates) == 1 else None)
        values = {n: getattr(change, n) for n in Change.__dataclass_fields__}
        if match is not None and not match["quarantined"]:
            values["title"] = match["title"]
            values["entry_id"] = str(match["instance_id"])
        else:
            # No projection row to vouch for the event: no third-party text (location) either.
            values["prior_location"] = values["new_location"] = None
        enriched.append(Change(**values))
    return ChangesRead(changes=enriched, available=True, truncated=truncated)
