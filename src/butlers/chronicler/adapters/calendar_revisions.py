"""Plan-change projection over the Google Calendar revision journal (bu-uhbhzq.1 slice 3).

``connectors.calendar_event_revisions`` (core_369) holds one row per provider event version. A
``change`` or ``resync`` revision of an instance that starts on or before the owner's local day it
was observed is a changed plan: this adapter emits one ``plan_changed`` point event for it, on the
**intent** layer, carrying the prior and new start, end and status.

Boundaries (spec ``chronicler-intent-evidence-activity``):
- Plan revisions are intent evidence. They never assert attendance and never count toward
  activity or lived time; they are ``layer=intent`` point events, not episodes.
- Only times and status leave the journal. No title, location, attendee or raw payload reaches the
  Chronicler event, so a policy-blocked row (structure and times only) projects exactly like any
  other.
- ``baseline`` rows emit nothing. A revision whose start, end and status all equal its prior
  version's (an RSVP, a retitle) is not a plan change in these fields and emits nothing.
- A revision with no earlier journaled version (a resync after a 410 gap) still emits, with
  ``prior_known=false`` and null prior fields.
- Each event is keyed on the revision id, so a replay upserts the same row.

No LLM call: Tier-0 deterministic projection only (RFC 0014 section D5).
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg

from butlers.chronicler.adapters.base import (
    AdapterResult,
    KeyCols,
    ProjectionAdapter,
    cursor_key,
    keyset_page,
)
from butlers.chronicler.models import Layer, PointEvent, Precision, Privacy
from butlers.chronicler.storage import upsert_point_event

logger = logging.getLogger(__name__)

SOURCE_NAME = "calendar.revisions"
EVENT_TYPE_PLAN_CHANGED = "plan_changed"
_EVIDENCE_TABLE = "connectors.calendar_event_revisions"
DEFAULT_BATCH_LIMIT = 1000

# The prior version is the latest earlier revision of the same provider event. One sync batch is
# one transaction, so its rows share ``observed_at`` and the same event can appear twice in it
# with different etags (Google changes an event while the pages are being listed): the provider's
# own ``updated`` then ``sequence`` order the versions before the id breaks any remaining tie.
_REVISIONS = f"""(
    SELECT r.id, r.observed_at, r.observation, r.policy_blocked, r.all_day,
           r.status AS new_status, r.starts_at AS new_start, r.ends_at AS new_end,
           p.id IS NOT NULL AS prior_known,
           p.status AS prior_status, p.starts_at AS prior_start, p.ends_at AS prior_end
    FROM {_EVIDENCE_TABLE} AS r
    LEFT JOIN LATERAL (
        SELECT q.id, q.status, q.starts_at, q.ends_at
        FROM {_EVIDENCE_TABLE} AS q
        WHERE q.endpoint_identity = r.endpoint_identity
          AND q.calendar_id = r.calendar_id
          AND q.event_id = r.event_id
          AND (q.observed_at, COALESCE(q.provider_updated, '-infinity'::timestamptz),
               COALESCE(q.sequence, -1), q.id)
              < (r.observed_at, COALESCE(r.provider_updated, '-infinity'::timestamptz),
                 COALESCE(r.sequence, -1), r.id)
        ORDER BY q.observed_at DESC, COALESCE(q.provider_updated, '-infinity'::timestamptz) DESC,
                 COALESCE(q.sequence, -1) DESC, q.id DESC
        LIMIT 1
    ) AS p ON TRUE
    WHERE r.observation IN ('change', 'resync')
) AS revision"""


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def starts_by_observed_day(row: Any, zone: ZoneInfo) -> bool:
    """Whether the instance starts on or before the local day the revision was observed.

    The earlier of the prior and new starts counts, so a same-day meeting moved to tomorrow is
    still a change to today's plan.
    """
    starts = [s for s in (row["new_start"], row["prior_start"]) if s is not None]
    if not starts:
        return False
    observed = row["observed_at"].astimezone(zone)
    next_midnight = datetime.combine(observed.date() + timedelta(days=1), time.min, tzinfo=zone)
    return min(starts) < next_midnight


def _changed(row: Any) -> bool:
    if not row["prior_known"]:
        return True
    return (
        row["new_start"] != row["prior_start"]
        or row["new_end"] != row["prior_end"]
        or row["new_status"] != row["prior_status"]
    )


def _payload(row: Any) -> dict[str, Any]:
    return {
        "prior_known": bool(row["prior_known"]),
        "prior": {
            "start": _iso(row["prior_start"]),
            "end": _iso(row["prior_end"]),
            "status": row["prior_status"],
        },
        "new": {
            "start": _iso(row["new_start"]),
            "end": _iso(row["new_end"]),
            "status": row["new_status"],
        },
        "all_day": bool(row["all_day"]),
        "observation": row["observation"],
        "policy_blocked": bool(row["policy_blocked"]),
    }


class CalendarRevisionsAdapter(ProjectionAdapter):
    """Project journal ``change``/``resync`` revisions into ``plan_changed`` intent events."""

    keyset_cols: KeyCols = (("id", "uuid"),)

    def __init__(
        self, *, batch_limit: int = DEFAULT_BATCH_LIMIT, timezone: str | None = None
    ) -> None:
        super().__init__(SOURCE_NAME)
        self.batch_limit = batch_limit
        self.timezone = timezone  # an explicit override, as in the situation adapter

    async def _owner_zone(self, chronicler_pool: asyncpg.Pool) -> ZoneInfo:
        """The owner's configured zone, checked against the rollup's (situation's rule)."""
        from butlers.core.general_settings import load_configured_general_timezone

        repair_state = await chronicler_pool.fetchrow(
            "SELECT timezone, blocked_reason FROM chronicler.daily_repair_control WHERE singleton"
        )
        if repair_state is None or repair_state["blocked_reason"]:
            raise RuntimeError("daily repair timezone state missing or blocked")
        configured = self.timezone or await load_configured_general_timezone(chronicler_pool)
        zone = configured or repair_state["timezone"]
        if zone != repair_state["timezone"]:
            raise RuntimeError("calendar revisions/rollup timezone mismatch")
        return ZoneInfo(zone)

    async def project(
        self,
        pool: asyncpg.Pool,
        *,
        chronicler_pool: asyncpg.Pool,
        since: datetime | None,
        since_id: int | None = None,
        since_key: list[str] | None = None,
    ) -> AdapterResult:
        """Fetch the next page of revisions; emit one point event for each plan change."""
        del since_id  # the journal id is a UUID; the tiebreaker travels as ``since_key``
        result = AdapterResult(source_name=self.source_name)
        zone = await self._owner_zone(chronicler_pool)

        rows = await self._fetch(pool, since, since_key)
        if rows is None:
            result.skipped = True
            result.skipped_reason = (
                f"{_EVIDENCE_TABLE} not found; calendar revision surface unavailable"
            )
            return result
        if not rows:
            result.watermark = since
            return result

        latest = since
        for row in rows:
            observed_at: datetime = row["observed_at"]  # NOT NULL in core_369
            if latest is None or observed_at > latest:
                latest = observed_at
            if not (starts_by_observed_day(row, zone) and _changed(row)):
                continue
            async with chronicler_pool.acquire() as conn:
                await upsert_point_event(
                    conn,
                    PointEvent(
                        source_name=self.source_name,
                        source_ref=f"{_EVIDENCE_TABLE}:{row['id']}",
                        event_type=EVENT_TYPE_PLAN_CHANGED,
                        occurred_at=observed_at,
                        precision=Precision.EXACT,
                        title="Planned calendar event changed",
                        payload=_payload(row),
                        privacy=Privacy.NORMAL,
                        layer=Layer.INTENT,
                    ),
                )
            result.rows_projected += 1
            result.point_events += 1

        result.watermark = latest
        result.watermark_key = cursor_key(rows[-1], self.keyset_cols)
        return result

    async def _fetch(
        self, pool: asyncpg.Pool, since: datetime | None, since_key: list[str] | None
    ) -> list[asyncpg.Record] | None:
        """The next page, or ``None`` when the journal is absent (connector not deployed)."""
        try:
            async with pool.acquire() as conn:
                exists = await conn.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_schema = 'connectors'
                          AND table_name = 'calendar_event_revisions'
                    )
                    """
                )
                if not exists:
                    return None
                sql, args = keyset_page(
                    columns="*",
                    table=_REVISIONS,
                    ts_col="revision.observed_at",
                    key_cols=(("revision.id", "uuid"),),
                    since=since,
                    since_key=since_key,
                    batch_limit=self.batch_limit,
                )
                return list(await conn.fetch(sql, *args))
        except asyncpg.PostgresError:
            logger.exception("Failed reading %s", _EVIDENCE_TABLE)
            return None


__all__ = [
    "DEFAULT_BATCH_LIMIT",
    "EVENT_TYPE_PLAN_CHANGED",
    "SOURCE_NAME",
    "CalendarRevisionsAdapter",
    "starts_by_observed_day",
]
