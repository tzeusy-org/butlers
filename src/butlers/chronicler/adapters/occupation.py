"""Occupation-block inference adapter (bu-whhll.10, epic bu-whhll Tier 2).

Emits deterministic ``occupation_block`` episodes on weekdays that match an
owner-enabled ``chronicler.routines`` row (bu-whhll.9, migration
``chronicler_018``) when the routine window carries **>=1 weak corroborator**
and **no contradictor**.

Corroborators (any one is enough to start — 2026-07-02 case study: five
desk-Spotify sessions alone would have yielded ~9h of recognized occupation):

- **Desk-Spotify listening** — a ``spotify.session_summary``
  ``listening_episode`` overlapping the window (SUPPORTED today).
- **Owner-outbound messages** — an ``owner_outbound.messages``
  ``owner_outbound_message`` point event inside the window (bu-whhll.8; the
  underlying connector table is landing separately — reading
  ``chronicler.point_events`` for this source naturally degrades to zero
  matches until that adapter is deployed and has run, no missing-table guard
  needed).
- **Office-SSID presence** — an ``owntracks.ssid_presence``
  ``occupation_presence_episode`` overlapping the window (bu-whhll.5).

Contradictors (any one present suppresses the block outright, regardless of
corroborator count):

- **Movement/travel** — an ``owntracks.points`` ``movement_episode``
  overlapping the window (the owner was physically away from the desk).
- **Gaming** — a ``steam.play_history`` ``play_episode`` overlapping the
  window.
- **Leave/holiday (all-day calendar block)** — a ``google_calendar.completed``
  ``scheduled_block`` overlapping the window whose span is at least
  :data:`ALL_DAY_MIN_HOURS`. There is no dedicated ``all_day`` flag
  denormalized onto the chronicler episode payload today, so duration is the
  deterministic proxy: a same-day meeting is a few hours and never crosses
  the threshold, while a genuine all-day (or multi-day) leave/holiday block
  does.

Emitted episodes always carry ``layer=activity``, ``confidence=low``
(occupation inference is inherently a weak-signal tier — even multiple weak
corroborators never earn the ``derive_confidence`` ladder's ``medium``/
``high`` tiers, so the value is fixed rather than derived), and
``precision=hour`` (the routine window, not an exact clock boundary).
``evidence_refs`` cites the corroborating row ids. Unlike most other inferred
adapters, corroborators here are not exclusively point events (Spotify's
corroboration is itself an episode, not a point event, since the Spotify
adapter has no underlying point-event stream — see ``adapters/spotify.py``),
so this adapter does not use ``storage.link_event_to_episode`` (which targets
point events specifically via ``episode_event_links``); it stringifies
whichever kind of row corroborated directly onto ``evidence_refs``.

This adapter reads only ``chronicler.routines`` / ``chronicler.episodes`` /
``chronicler.point_events`` (its own schema) — chronicler reading what
chronicler wrote, same convention as the focus/reading/exercise inferred
adapters.

Watermark semantics: like ``routines.mine_routines``, this is a **summary
job, not an event stream** — each run re-scans the last ``lookback_days``
per enabled routine (bounded, cheap at personal scale) and upserts
idempotently on ``(routine_id, local_date)``, rather than tracking a strict
incremental watermark. ``since``/``since_id`` are accepted per the
``ProjectionAdapter`` contract but are not used to bound the scan;
``result.watermark`` is set to the run's wall-clock time purely for
checkpoint/telemetry visibility (last_run_at etc.).

No LLM call — Tier-2 deterministic projection only (RFC 0014 §D5).
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import asyncpg

from butlers.chronicler.adapters._owner_entity import (
    resolve_owner_entity_id,
    upsert_owner_episode_entity,
)
from butlers.chronicler.adapters.base import AdapterResult, ProjectionAdapter
from butlers.chronicler.confidence import evidence_refs_from_event_ids
from butlers.chronicler.models import Confidence, Episode, Layer, Precision, Privacy, Routine
from butlers.chronicler.storage import list_routines, upsert_episode

logger = logging.getLogger(__name__)

SOURCE_NAME = "chronicler.occupation_inferred"
EPISODE_TYPE_OCCUPATION = "occupation_block"

DEFAULT_LOOKBACK_DAYS = 14

# Weak corroborator sources — episode-shaped. (source_name, episode_type).
_CORROBORATOR_EPISODE_SOURCES: tuple[tuple[str, str], ...] = (
    ("spotify.session_summary", "listening_episode"),
    ("owntracks.ssid_presence", "occupation_presence_episode"),
)
# Weak corroborator sources — point-event-shaped. (source_name, event_type).
# owner_outbound.messages is in flight (bu-whhll.8); querying it before that
# adapter has run/deployed simply returns zero rows (no guard needed — see
# module docstring).
_CORROBORATOR_POINT_EVENT_SOURCES: tuple[tuple[str, str], ...] = (
    ("owner_outbound.messages", "owner_outbound_message"),
)

# Contradictor sources — presence of any overlapping row suppresses the block.
_CONTRADICTOR_MOVEMENT: tuple[str, str] = ("owntracks.points", "movement_episode")
_CONTRADICTOR_GAMING: tuple[str, str] = ("steam.play_history", "play_episode")
_CONTRADICTOR_CALENDAR: tuple[str, str] = ("google_calendar.completed", "scheduled_block")

# An all-day leave/holiday calendar block spans at least a full day; a normal
# meeting never does. See module docstring for the rationale.
ALL_DAY_MIN_HOURS = 20


def _now() -> datetime:
    """Wall-clock now, isolated for test patching."""
    return datetime.now(UTC)


class OccupationInferredAdapter(ProjectionAdapter):
    """Project ``occupation_block`` episodes from enabled routine windows."""

    def __init__(self, *, lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> None:
        super().__init__(SOURCE_NAME)
        self.lookback_days = lookback_days

    async def project(
        self,
        pool: asyncpg.Pool,
        *,
        chronicler_pool: asyncpg.Pool,
        since: datetime | None,
        since_id: int | None = None,
    ) -> AdapterResult:
        del since_id
        result = AdapterResult(source_name=self.source_name)

        try:
            routines = await list_routines(chronicler_pool, enabled_only=True)
        except (asyncpg.UndefinedTableError, asyncpg.PostgresError):
            logger.exception("Failed reading chronicler.routines for occupation inference")
            result.skipped = True
            result.skipped_reason = "chronicler.routines not found"
            return result

        if not routines:
            result.watermark = since
            return result

        # Resolve owner entity_id once per adapter run (not per row/date).
        entity_id = await resolve_owner_entity_id(pool)
        now_utc = _now()

        for routine in routines:
            try:
                tzinfo = ZoneInfo(routine.timezone)
            except (ZoneInfoNotFoundError, ValueError):
                logger.warning(
                    "OccupationInferredAdapter: unknown timezone %r for routine %s; skipping",
                    routine.timezone,
                    routine.id,
                )
                continue

            today_local = now_utc.astimezone(tzinfo).date()
            lookback_start = today_local - timedelta(days=self.lookback_days)

            single_date = lookback_start
            while single_date < today_local:
                if routine.dow_mask & (1 << single_date.weekday()):
                    start_at = datetime.combine(
                        single_date, routine.window_start_local, tzinfo
                    ).astimezone(UTC)
                    end_at = datetime.combine(
                        single_date, routine.window_end_local, tzinfo
                    ).astimezone(UTC)
                    # Only project fully-elapsed windows.
                    if end_at <= now_utc:
                        episode = await self._maybe_project(
                            chronicler_pool,
                            routine,
                            single_date,
                            start_at,
                            end_at,
                            entity_id=entity_id,
                        )
                        if episode is not None:
                            result.rows_projected += 1
                            result.episodes_closed += 1
                single_date += timedelta(days=1)

        result.watermark = now_utc
        return result

    async def _maybe_project(
        self,
        chronicler_pool: asyncpg.Pool,
        routine: Routine,
        local_date: date,
        start_at: datetime,
        end_at: datetime,
        *,
        entity_id: UUID | None = None,
    ) -> Episode | None:
        async with chronicler_pool.acquire() as conn:
            corroborator_ids: list[UUID] = []
            for source in _CORROBORATOR_EPISODE_SOURCES:
                corroborator_ids.extend(
                    await self._fetch_episode_ids(conn, source, start_at, end_at)
                )
            for source in _CORROBORATOR_POINT_EVENT_SOURCES:
                corroborator_ids.extend(
                    await self._fetch_point_event_ids(conn, source, start_at, end_at)
                )
            if not corroborator_ids:
                # Nothing genuinely new to emit without at least one weak
                # corroborator — short-circuits before the contradictor
                # queries too.
                return None

            if await self._exists_episode(conn, _CONTRADICTOR_MOVEMENT, start_at, end_at):
                return None
            if await self._exists_episode(conn, _CONTRADICTOR_GAMING, start_at, end_at):
                return None
            if await self._exists_all_day_calendar(conn, start_at, end_at):
                return None

            source_ref = f"chronicler.routines:{routine.id}:{local_date.isoformat()}"
            evidence_refs = evidence_refs_from_event_ids(corroborator_ids)
            payload: dict[str, Any] = {
                "routine_id": str(routine.id),
                "routine_label": routine.label,
                "local_date": local_date.isoformat(),
                "corroborator_count": len(corroborator_ids),
            }

            episode = await upsert_episode(
                conn,
                Episode(
                    source_name=self.source_name,
                    source_ref=source_ref,
                    episode_type=EPISODE_TYPE_OCCUPATION,
                    start_at=start_at,
                    end_at=end_at,
                    precision=Precision.HOUR,
                    title=f"Occupation ({routine.label})",
                    payload=payload,
                    privacy=Privacy.NORMAL,
                    layer=Layer.ACTIVITY,
                    confidence=Confidence.LOW,
                    evidence_refs=evidence_refs,
                ),
            )
            # Write owner row into episode_entities join table (bu-4c1ks).
            await upsert_owner_episode_entity(conn, episode.id, owner_id=entity_id)
        return episode

    @staticmethod
    async def _fetch_episode_ids(
        conn: asyncpg.Connection,
        source: tuple[str, str],
        start_at: datetime,
        end_at: datetime,
    ) -> list[UUID]:
        source_name, episode_type = source
        rows = await conn.fetch(
            """
            SELECT id FROM episodes
            WHERE tombstone_at IS NULL
              AND source_name = $1
              AND episode_type = $2
              AND start_at < $4
              AND (end_at IS NULL OR end_at > $3)
            ORDER BY start_at ASC, id ASC
            """,
            source_name,
            episode_type,
            start_at,
            end_at,
        )
        return [r["id"] for r in rows]

    @staticmethod
    async def _fetch_point_event_ids(
        conn: asyncpg.Connection,
        source: tuple[str, str],
        start_at: datetime,
        end_at: datetime,
    ) -> list[UUID]:
        source_name, event_type = source
        rows = await conn.fetch(
            """
            SELECT id FROM point_events
            WHERE tombstone_at IS NULL
              AND source_name = $1
              AND event_type = $2
              AND occurred_at >= $3
              AND occurred_at < $4
            ORDER BY occurred_at ASC, id ASC
            """,
            source_name,
            event_type,
            start_at,
            end_at,
        )
        return [r["id"] for r in rows]

    @staticmethod
    async def _exists_episode(
        conn: asyncpg.Connection,
        source: tuple[str, str],
        start_at: datetime,
        end_at: datetime,
    ) -> bool:
        source_name, episode_type = source
        return bool(
            await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1 FROM episodes
                    WHERE tombstone_at IS NULL
                      AND source_name = $1
                      AND episode_type = $2
                      AND start_at < $4
                      AND (end_at IS NULL OR end_at > $3)
                )
                """,
                source_name,
                episode_type,
                start_at,
                end_at,
            )
        )

    @staticmethod
    async def _exists_all_day_calendar(
        conn: asyncpg.Connection,
        start_at: datetime,
        end_at: datetime,
    ) -> bool:
        source_name, episode_type = _CONTRADICTOR_CALENDAR
        return bool(
            await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1 FROM episodes
                    WHERE tombstone_at IS NULL
                      AND source_name = $1
                      AND episode_type = $2
                      AND end_at IS NOT NULL
                      AND (end_at - start_at) >= make_interval(hours => $5)
                      AND start_at < $4
                      AND end_at > $3
                )
                """,
                source_name,
                episode_type,
                start_at,
                end_at,
                ALL_DAY_MIN_HOURS,
            )
        )


__all__ = [
    "ALL_DAY_MIN_HOURS",
    "DEFAULT_LOOKBACK_DAYS",
    "EPISODE_TYPE_OCCUPATION",
    "OccupationInferredAdapter",
    "SOURCE_NAME",
]
