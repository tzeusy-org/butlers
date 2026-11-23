"""Read model for the owner day sheet (bu-5ii4gj.2 S2).

Reads the deduped workspace rows for one owner-local day across every calendar butler schema,
plus each schema's sync freshness, and hands them to the pure ``build_day_sheet``. A schema
that cannot be read makes the sheet ``unreadable``; it never reads as an empty day
(REQ-module-calendar-036).
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from butlers.api.db import DatabaseManager
from butlers.api.read_models.calendar_workspace import (
    _dedup_workspace_rows,
    _read_dedup_rules,
    _read_keep_separate_keys,
    query_calendar_sources,
    query_calendar_workspace,
    shallow_asdict,
)
from butlers.core.temporal.day_sheet import (
    DEFAULT_WINDOW,
    DayOccurrence,
    DaySheet,
    SourceRead,
    build_day_sheet,
)
from butlers.modules.calendar.constants import (
    DEFAULT_SYNC_INTERVAL_MINUTES,
    PROJECTION_STALENESS_MULTIPLIER,
)

logger = logging.getLogger(__name__)

_STALE_AFTER = timedelta(minutes=DEFAULT_SYNC_INTERVAL_MINUTES * PROJECTION_STALENESS_MULTIPLIER)


def _calendar_butlers(db: DatabaseManager) -> list[str]:
    names = db.butlers_with_module("calendar")
    return sorted(names if names is not None else db.butler_names)


def _sync_disabled(source_metadata: object) -> bool:
    return isinstance(source_metadata, dict) and source_metadata.get("sync_enabled") is False


async def query_calendar_day_sheet(
    db: DatabaseManager,
    *,
    local_date: date,
    zone: ZoneInfo,
    now: datetime | None = None,
    window: tuple[time, time] | None = None,
) -> DaySheet:
    """The owner's ``local_date`` as a ``DaySheet``. Never raises for a read failure."""
    now = now or datetime.now(UTC)
    butlers = _calendar_butlers(db)
    start = datetime.combine(local_date, time(0), tzinfo=zone).astimezone(UTC)
    end = datetime.combine(local_date + timedelta(days=1), time(0), tzinfo=zone).astimezone(UTC)

    def sheet(units: list[SourceRead], occurrences=()) -> DaySheet:
        return build_day_sheet(
            list(occurrences),
            units,
            zone=zone.key,
            local_date=local_date,
            now=now,
            window=window or DEFAULT_WINDOW,
        )

    failed: set[str] = set()
    try:
        source_rows, failed_sources = await query_calendar_sources(db, butlers=butlers)
        failed.update(failed_sources)
        workspace: list[dict] = []
        for view in ("user", "butler"):
            rows, failed_view = await query_calendar_workspace(
                db, view=view, start=start, end=end, butlers=butlers
            )
            failed.update(failed_view)
            workspace.extend(shallow_asdict(row) for row in rows)
    except Exception:
        logger.warning("day sheet read failed; reporting every source unreadable", exc_info=True)
        return sheet([SourceRead(name=name, readable=False) for name in butlers])

    # One unit per calendar butler schema that has anything to sync; a schema with no provider
    # source has nothing to be stale about. A schema that failed to read counts even then.
    units: list[SourceRead] = []
    for name in butlers:
        provider = [
            r
            for r in source_rows
            if r.db_butler == name and r.lane == "user" and not _sync_disabled(r.source_metadata)
        ]
        if name in failed:
            # The last good read, when known, is what "unreadable since" reports.
            known = [r.last_success_at for r in provider if r.last_success_at is not None]
            units.append(SourceRead(name=name, readable=False, as_of=min(known) if known else None))
        elif provider:
            stamps = [r.last_success_at for r in provider if r.last_success_at is not None]
            # One source that never succeeded makes the whole schema's freshness unknown.
            as_of = min(stamps) if stamps and len(stamps) == len(provider) else None
            units.append(
                SourceRead(name=name, readable=True, as_of=as_of, stale_after=_STALE_AFTER)
            )

    try:
        # One instance belongs to one lane; collapse repeats so a view overlap cannot double a row.
        workspace = list({(r["instance_id"], r["db_butler"]): r for r in workspace}.values())
        workspace.sort(key=lambda r: (r["instance_starts_at"], r["instance_id"]))
        rules, _rules_read = await _read_dedup_rules(db)
        keep_separate, _pins_read = await _read_keep_separate_keys(db)
        deduped, _clusters = _dedup_workspace_rows(
            workspace, strategy=rules.match_strategy, keep_separate=keep_separate
        )
        occurrences = [
            DayOccurrence(
                ref=str(row["instance_id"]),
                title=row.get("title") or "Untitled",
                starts_at=row["instance_starts_at"],
                ends_at=row["instance_ends_at"],
                all_day=bool(row.get("all_day")),
                timezone=row.get("instance_timezone") or row.get("event_timezone"),
                status=str(row.get("instance_status") or row.get("event_status") or "confirmed"),
                event_type=row.get("event_type") or "default",
                time_role=row.get("source_time_role") or "unclassified",
                metadata=row.get("event_metadata"),
            )
            for row in deduped
        ]
    except Exception:
        logger.warning(
            "day sheet rows unreadable; reporting every source unreadable", exc_info=True
        )
        return sheet([SourceRead(name=name, readable=False) for name in butlers])
    return sheet(units, occurrences)
