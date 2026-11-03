"""Situational activity adapter (bu-r3fmja, REQ-butler-chronicler-016).

Chronicler reading what Chronicler wrote, like the occupation and focus adapters.
Every run re-derives each owner-local day of the last ``lookback_days`` (today up
to now included) from:

- live ``owntracks.place_cluster`` ``place_episode`` stays overlapping the day;
- a provisional hold at the latest accepted ``owntracks.points`` fix when no stay
  or movement episode covers it yet (an open stay the clusterer has not closed);
- the owner's anchor places (:mod:`butlers.chronicler.anchors`);
- the day's other ``activity`` episodes, which outrank or corroborate a block.

The pure rules live in :mod:`butlers.chronicler.situation`. Each run upserts the
day's blocks on ``source_ref = chronicler.situation:{date}:{kind}:{start_epoch}``
and tombstones that day's earlier blocks it did not re-emit, unless the owner
overrode them. No payload, log line or title carries a coordinate.

Like other summary adapters, the watermark is the run's wall-clock time, for
telemetry only. No LLM call (RFC 0014 §D5).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import asyncpg

from butlers.chronicler.adapters._owner_entity import (
    owner_episode_transaction,
    resolve_owner_entity_id,
    upsert_owner_episode_entity,
)
from butlers.chronicler.adapters.base import AdapterResult, ProjectionAdapter
from butlers.chronicler.adapters.owntracks_place_cluster import (
    PlaceReference,
    parse_place_references,
)
from butlers.chronicler.anchors import AnchorPlace, centroid_of, load_anchors
from butlers.chronicler.models import Episode, Layer, Precision, Privacy
from butlers.chronicler.situation import (
    SOURCE_NAME,
    ActivitySpan,
    Fix,
    SituationBlock,
    WhereSegment,
    activity_span,
    derive_blocks,
    tail_hold,
)
from butlers.chronicler.storage import upsert_episode

logger = logging.getLogger(__name__)

_Conn = asyncpg.Connection | asyncpg.pool.PoolConnectionProxy

DEFAULT_LOOKBACK_DAYS = 3
TOMBSTONE_REASON = "bu-r3fmja: situation re-derived"

_TITLES = {
    "work_presence_block": "At work",
    "outing_block": "Out",
    "airport_block": "At the airport",
    "home_block": "At home",
    "play_block": "Playing",
}


def _now() -> datetime:
    """Wall-clock now, isolated for test patching."""
    return datetime.now(UTC)


def _payload_dict(payload: Any) -> dict[str, Any]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            return {}
    return payload if isinstance(payload, dict) else {}


async def load_accepted_fixes(pool: Any, *, start: datetime, end: datetime) -> list[Fix]:
    """Live OwnTracks location fixes in ``(start, end]``, trail outliers excluded."""
    rows = await pool.fetch(
        """
        SELECT occurred_at, payload FROM point_events
        WHERE tombstone_at IS NULL
          AND source_name = 'owntracks.points'
          AND event_type = 'location'
          AND occurred_at > $1 AND occurred_at <= $2
        ORDER BY occurred_at
        """,
        start,
        end,
    )
    fixes = []
    for row in rows:
        payload = _payload_dict(row["payload"])
        if payload.get("trail_outlier"):
            continue
        try:
            fixes.append(Fix(row["occurred_at"], float(payload["lat"]), float(payload["lon"])))
        except (KeyError, TypeError, ValueError):
            continue
    return fixes


def env_place_references() -> tuple[PlaceReference, ...]:
    """Legacy ``OWNTRACKS_PLACE_REFERENCES``; malformed degrades to none."""
    try:
        return parse_place_references(os.environ.get("OWNTRACKS_PLACE_REFERENCES", ""))
    except ValueError:
        logger.warning("Malformed OWNTRACKS_PLACE_REFERENCES; legacy anchors skipped")
        return ()


class SituationInferredAdapter(ProjectionAdapter):
    """Project situational blocks from place stays, anchors and precedence."""

    def __init__(
        self,
        *,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
        timezone: str | None = None,
        references: tuple[PlaceReference, ...] | None = None,
    ) -> None:
        super().__init__(SOURCE_NAME)
        if lookback_days <= 0:
            raise ValueError("lookback_days must be positive")
        self.lookback_days = lookback_days
        self.timezone = timezone
        self.references = references

    async def project(
        self,
        pool: asyncpg.Pool,
        *,
        chronicler_pool: asyncpg.Pool,
        since: datetime | None,
        since_id: int | None = None,
    ) -> AdapterResult:
        del since_id
        from butlers.core.general_settings import resolve_general_timezone

        result = AdapterResult(source_name=self.source_name)
        now = _now()
        tz = ZoneInfo(self.timezone or await resolve_general_timezone(chronicler_pool))
        references = self.references if self.references is not None else env_place_references()
        anchors = await load_anchors(chronicler_pool, tz=tz, now=now, references=references)
        entity_id = await resolve_owner_entity_id(pool)

        today = now.astimezone(tz).date()
        first_day = today - timedelta(days=self.lookback_days - 1)
        # Re-derive changed Steam dates as well as the usual trailing window.
        # chronicler_030 resets this checkpoint once so historical daily totals
        # keep counted fallback Play after their source rows become evidence.
        # Tombstones and owner corrections invalidate the old day too. Provenance
        # recovers intermediate dates after repeated moves of the same evidence.
        changed_start = await chronicler_pool.fetchval(
            """
            WITH changed AS (
                SELECT id, start_at, canonical_start_at
                FROM v_episodes_corrected
                WHERE source_name IN ('steam.status_history', 'steam.play_history')
                  AND ($1::timestamptz IS NULL
                       OR greatest(updated_at, corrected_at, tombstone_at) > $1)
            ), affected AS (
                SELECT start_at FROM changed
                UNION ALL
                SELECT canonical_start_at FROM changed
                UNION ALL
                SELECT derived.start_at FROM episodes AS derived
                WHERE derived.source_name = 'chronicler.situation_inferred'
                  AND derived.episode_type = 'play_block'
                  AND EXISTS (
                      SELECT 1 FROM changed
                      WHERE derived.evidence_refs ? changed.id::text
                  )
            )
            SELECT min(start_at) FROM affected
            """,
            since,
        )
        if changed_start is not None:
            first_day = min(first_day, changed_start.astimezone(tz).date())
        window_start = datetime.combine(first_day, datetime.min.time(), tz).astimezone(UTC)
        hold = await self._tail_hold(chronicler_pool, since=window_start, now=now, tz=tz)

        day = first_day
        while day <= today:
            day_start = datetime.combine(day, datetime.min.time(), tz).astimezone(UTC)
            day_end = min(
                datetime.combine(day + timedelta(days=1), datetime.min.time(), tz).astimezone(UTC),
                now,
            )
            written = await self._derive_day(
                chronicler_pool,
                day,
                day_start,
                day_end,
                anchors=anchors,
                hold=hold,
                entity_id=entity_id,
            )
            result.rows_projected += written
            result.episodes_closed += written
            day += timedelta(days=1)

        result.watermark = now
        return result

    async def _derive_day(
        self,
        pool: asyncpg.Pool,
        day: date,
        day_start: datetime,
        day_end: datetime,
        *,
        anchors: tuple[AnchorPlace, ...],
        hold: WhereSegment | None,
        entity_id: UUID | None,
    ) -> int:
        local_date = day.isoformat()
        async with pool.acquire() as conn:
            segments = await self._place_segments(conn, day_start, day_end)
            if hold is not None:
                segments.append(hold)
            activities = await self._activities(conn, day_start, day_end)
            corrected = await self._corrected_own(conn, local_date)
            corrected_refs = {ref for ref, _s, _e in corrected}
            blocks = [
                block
                for block in derive_blocks(
                    day_start=day_start,
                    day_end=day_end,
                    segments=segments,
                    anchors=anchors,
                    activities=activities,
                    corrected=[(s, e) for _ref, s, e in corrected],
                )
                if block.source_ref(local_date) not in corrected_refs
            ]
            async with owner_episode_transaction(conn):
                refs = []
                for block in blocks:
                    episode = await upsert_episode(conn, self._episode(block, local_date))
                    await upsert_owner_episode_entity(conn, episode.id, owner_id=entity_id)
                    refs.append(episode.source_ref)
                await self._tombstone_stale(conn, local_date, refs)
        return len(blocks)

    def _episode(self, block: SituationBlock, local_date: str) -> Episode:
        title = _TITLES[block.episode_type]
        if block.place_label and block.episode_type in ("work_presence_block", "outing_block"):
            title = f"{title} ({block.place_label})"
        return Episode(
            source_name=self.source_name,
            source_ref=block.source_ref(local_date),
            episode_type=block.episode_type,
            start_at=block.start_at,
            end_at=block.end_at,
            precision=Precision.MINUTE,
            title=title,
            payload={
                "local_date": local_date,
                "place_kind": block.place_kind,
                "place_label": block.place_label,
                "anchor_source": block.anchor_source,
                "distance_from_home_m": block.distance_from_home_m,
                "place_episode_ids": list(block.place_episode_ids),
                "bridged": block.bridged,
                "provisional": block.provisional,
            },
            privacy=Privacy.NORMAL,
            layer=Layer.ACTIVITY,
            confidence=block.confidence,
            evidence_refs=[*block.place_episode_ids, *block.evidence_refs],
        )

    @staticmethod
    async def _place_segments(
        conn: _Conn, day_start: datetime, day_end: datetime
    ) -> list[WhereSegment]:
        rows = await conn.fetch(
            """
            SELECT id, start_at, end_at, payload FROM v_episodes_corrected
            WHERE tombstone_at IS NULL
              AND source_name = 'owntracks.place_cluster'
              AND episode_type = 'place_episode'
              AND end_at IS NOT NULL
              AND start_at < $2 AND end_at > $1
            ORDER BY start_at, id
            """,
            day_start,
            day_end,
        )
        segments = []
        for row in rows:
            payload = _payload_dict(row["payload"])
            lat, lon = centroid_of(payload)
            if lat is None or lon is None:
                continue
            segments.append(
                WhereSegment(
                    start_at=row["start_at"],
                    end_at=row["end_at"],
                    lat=lat,
                    lon=lon,
                    place_episode_ids=(str(row["id"]),),
                    bridged=payload.get("bridged") is True,
                )
            )
        return segments

    async def _activities(
        self, conn: _Conn, day_start: datetime, day_end: datetime
    ) -> list[ActivitySpan]:
        rows = await conn.fetch(
            """
            SELECT id, source_name, episode_type, start_at, end_at, layer, payload
            FROM v_episodes_corrected
            WHERE tombstone_at IS NULL
              AND (layer = 'activity'
                   OR source_name IN ('steam.status_history', 'steam.play_history'))
              AND source_name <> $3
              AND end_at IS NOT NULL
              AND start_at < $2 AND end_at > $1
            """,
            day_start,
            day_end,
            self.source_name,
        )
        # Status sessions suppress the daily-total guess for the local date.
        has_sessions = any(r["source_name"] == "steam.status_history" for r in rows)
        spans = []
        for row in rows:
            if has_sessions and row["source_name"] == "steam.play_history":
                continue
            payload = _payload_dict(row["payload"])
            if row["source_name"] in {"steam.status_history", "steam.play_history"}:
                spans.append(
                    ActivitySpan(
                        row["start_at"],
                        row["end_at"],
                        row["source_name"],
                        row["episode_type"],
                        "play",
                        provisional=payload.get("provisional") is True,
                        evidence_refs=(str(row["id"]),) if row.get("id") else (),
                    )
                )
                continue
            span = activity_span(
                layer=row["layer"],
                source_name=row["source_name"],
                episode_type=row["episode_type"],
                start_at=row["start_at"],
                end_at=row["end_at"],
                trigger_source=payload.get("trigger_source"),
            )
            if span is not None:
                spans.append(span)
        return spans

    @staticmethod
    async def _tail_hold(
        pool: asyncpg.Pool, *, since: datetime, now: datetime, tz: ZoneInfo
    ) -> WhereSegment | None:
        last = await pool.fetchrow(
            """
            SELECT id, episode_type, end_at, payload FROM v_episodes_corrected
            WHERE tombstone_at IS NULL
              AND end_at IS NOT NULL
              AND ((source_name = 'owntracks.place_cluster' AND episode_type = 'place_episode')
                OR (source_name = 'owntracks.points' AND episode_type = 'movement_episode'))
            ORDER BY end_at DESC, id DESC
            LIMIT 1
            """
        )
        after = last["end_at"] if last is not None else None
        last_stay = None
        if last is not None and last["episode_type"] == "place_episode":
            payload = _payload_dict(last["payload"])
            lat, lon = centroid_of(payload)
            if lat is not None and lon is not None:
                last_stay = WhereSegment(
                    start_at=last["end_at"],
                    end_at=last["end_at"],
                    lat=lat,
                    lon=lon,
                    place_episode_ids=(str(last["id"]),),
                    bridged=payload.get("bridged") is True,
                )
        fixes = await load_accepted_fixes(pool, start=since - timedelta(days=1), end=now)
        return tail_hold(fixes, after=after, now=now, tz=tz, last_stay=last_stay)

    async def _corrected_own(
        self, conn: _Conn, local_date: str
    ) -> list[tuple[str, datetime, datetime]]:
        """This day's own blocks the owner corrected: kept, and never overlapped."""
        rows = await conn.fetch(
            """
            SELECT v.source_ref, v.start_at, v.end_at FROM v_episodes_corrected v
            WHERE v.source_name = $1
              AND v.tombstone_at IS NULL
              AND v.end_at IS NOT NULL
              AND v.payload ->> 'local_date' = $2
              AND EXISTS (
                  SELECT 1 FROM overrides o
                  WHERE o.target_kind = 'episode' AND o.target_id = v.id
              )
            """,
            self.source_name,
            local_date,
        )
        return [(r["source_ref"], r["start_at"], r["end_at"]) for r in rows]

    async def _tombstone_stale(self, conn: _Conn, local_date: str, keep: list[str]) -> None:
        await conn.execute(
            """
            UPDATE episodes e
            SET tombstone_at = now(), tombstone_reason = $3, updated_at = now()
            WHERE e.source_name = $1
              AND e.tombstone_at IS NULL
              AND e.payload ->> 'local_date' = $2
              AND NOT (e.source_ref = ANY($4::text[]))
              AND NOT EXISTS (
                  SELECT 1 FROM overrides o
                  WHERE o.target_kind = 'episode' AND o.target_id = e.id
              )
            """,
            self.source_name,
            local_date,
            TOMBSTONE_REASON,
            keep,
        )


__all__ = [
    "DEFAULT_LOOKBACK_DAYS",
    "SOURCE_NAME",
    "TOMBSTONE_REASON",
    "SituationInferredAdapter",
    "env_place_references",
    "load_accepted_fixes",
]
