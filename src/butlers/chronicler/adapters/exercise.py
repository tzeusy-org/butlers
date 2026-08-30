"""Inferred-exercise candidate projection adapter.

Emits a *genuinely new* ``exercise_episode`` activity candidate from the
corroboration of two independent evidence kinds that no single source episode
already represents:

1. **GPS movement** — an ``owntracks.points`` ``movement_episode`` (the owner
   was physically moving over a window).
2. **Elevated heart rate** — one or more ``health.heart_rate``
   ``heart_rate_summary`` point events inside that window whose ``bpm`` clears
   :data:`ELEVATED_HR_BPM`.

When both hold and **no explicit ``workout_episode`` overlaps the window**, the
adapter infers an exercise activity. Two independent kinds (``heart_rate`` +
``gps``) yield ``high`` confidence via :func:`derive_confidence`.

No-duplication guard: explicit ``google_health.measurements`` workout episodes
are ALREADY ``activity``-layer rows from the storage phase; re-emitting one as
an inferred exercise would double-count. So the candidate is suppressed whenever
an explicit workout episode overlaps the same window. Overlap with the *movement*
episode is intentional and harmless — they live in different lanes and day-close
reconciliation (tasks.md §7) merges/dedupes overlapping same-lane candidates.

This adapter reads only ``chronicler.episodes`` / ``chronicler.point_events``
(its own schema) and writes ``exercise_episode`` rows under
``source_name = 'chronicler.exercise_inferred'`` with deterministic source refs
derived from the underlying movement episode id, so re-running is idempotent.

Late HR corroboration is rechecked over a bounded recent movement window. The
movement source is watermark-incremental, while Google Health deliberately
re-polls a trailing day to capture delayed device syncs; without this second
look, a movement already past the exercise watermark could never gain a later
heart-rate event. The same elevated-HR predicate is used on both paths.

No LLM call — Tier-1 deterministic projection only (RFC 0014 §D5).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

import asyncpg

from butlers.chronicler.adapters._owner_entity import (
    resolve_owner_entity_id,
    upsert_owner_episode_entity,
)
from butlers.chronicler.adapters.base import AdapterResult, ProjectionAdapter
from butlers.chronicler.confidence import (
    EvidenceKind,
    derive_confidence,
    evidence_refs_from_event_ids,
)
from butlers.chronicler.models import Episode, Layer, LinkRelation, Precision, Privacy
from butlers.chronicler.storage import link_event_to_episode, upsert_episode

logger = logging.getLogger(__name__)

SOURCE_NAME = "chronicler.exercise_inferred"
EPISODE_TYPE_EXERCISE = "exercise_episode"

# Source names of the corroborating signals this adapter reads from.
_MOVEMENT_SOURCE = "owntracks.points"
_MOVEMENT_EPISODE_TYPE = "movement_episode"
_HEART_RATE_SOURCE = "health.heart_rate"
_HEART_RATE_EVENT_TYPE = "heart_rate_summary"
_WORKOUT_SOURCE = "google_health.measurements"
_WORKOUT_EPISODE_TYPE = "workout_episode"

DEFAULT_BATCH_LIMIT = 500

# Google Health deliberately re-polls a trailing day to capture delayed device
# syncs. Cover that interval plus one normal half-hour projection cadence while
# keeping the late-corroboration pass bounded well below a history scan.
CORROBORATION_RECHECK_LOOKBACK_HOURS = 30
CORROBORATION_RECHECK_LIMIT = 500

# Heart-rate threshold (bpm) above which a summary is treated as "elevated" and
# thus evidence of physical exertion. Conservative: resting/ambient HR rarely
# clears 100 bpm, so this avoids inferring exercise from ordinary movement.
ELEVATED_HR_BPM = 100


def _now() -> datetime:
    """Wall-clock now, isolated for bounded recheck tests."""
    return datetime.now(UTC)


class ExerciseInferredAdapter(ProjectionAdapter):
    """Project ``exercise_episode`` candidates inferred from HR+GPS corroboration."""

    def __init__(
        self,
        *,
        batch_limit: int = DEFAULT_BATCH_LIMIT,
        elevated_hr_bpm: int = ELEVATED_HR_BPM,
    ) -> None:
        super().__init__(SOURCE_NAME)
        self.batch_limit = batch_limit
        self.elevated_hr_bpm = elevated_hr_bpm

    async def project(
        self,
        pool: asyncpg.Pool,
        *,
        chronicler_pool: asyncpg.Pool,
        since: datetime | None,
        since_id: int | None = None,
    ) -> AdapterResult:
        """Read recent movement episodes; emit inferred exercise where corroborated.

        Reads and writes the chronicler schema (per the focus/reading inferred
        pattern): chronicler may read what chronicler wrote, as long as the
        inference itself stays deterministic.
        """
        result = AdapterResult(source_name=self.source_name)

        rows = await self._fetch_movement_candidates(chronicler_pool, since)
        if rows is None:
            result.skipped = True
            result.skipped_reason = "chronicler.episodes not found"
            return result

        # Resolve owner entity_id once per adapter run (pool reaches public.contacts).
        entity_id = await resolve_owner_entity_id(pool)

        latest_watermark = since
        for row in rows:
            candidate = row["created_at"]
            if candidate is not None and (latest_watermark is None or candidate > latest_watermark):
                latest_watermark = candidate

            episode = await self._maybe_project(chronicler_pool, row, entity_id=entity_id)
            if episode is None:
                continue
            result.rows_projected += 1
            result.episodes_closed += 1

        # A movement episode is incremental, but Google Health deliberately
        # accepts late HR device syncs for a trailing day. Recheck recent
        # movement spans even on an empty movement tick so a late corroborator
        # cannot leave an otherwise-qualified exercise unprojected forever.
        result.episodes_closed += await self._recheck_late_corroboration(
            chronicler_pool, entity_id=entity_id
        )
        result.watermark = latest_watermark
        return result

    async def _fetch_movement_candidates(
        self, chronicler_pool: asyncpg.Pool, since: datetime | None
    ) -> list[asyncpg.Record] | None:
        """Read recent movement episodes plus an ``overlaps_workout`` guard flag."""
        try:
            async with chronicler_pool.acquire() as conn:
                if since is None:
                    rows = await conn.fetch(
                        """
                        SELECT e.id, e.source_ref, e.start_at, e.end_at, e.created_at,
                               EXISTS (
                                   SELECT 1
                                   FROM episodes w
                                   WHERE w.tombstone_at IS NULL
                                     AND w.source_name = $1
                                     AND w.episode_type = $2
                                     AND w.start_at < e.end_at
                                     AND (w.end_at IS NULL OR w.end_at > e.start_at)
                               ) AS overlaps_workout
                        FROM episodes e
                        WHERE e.tombstone_at IS NULL
                          AND e.source_name = $3
                          AND e.episode_type = $4
                          AND e.end_at IS NOT NULL
                        ORDER BY e.created_at ASC, e.id ASC
                        LIMIT $5
                        """,
                        _WORKOUT_SOURCE,
                        _WORKOUT_EPISODE_TYPE,
                        _MOVEMENT_SOURCE,
                        _MOVEMENT_EPISODE_TYPE,
                        self.batch_limit,
                    )
                else:
                    rows = await conn.fetch(
                        """
                        SELECT e.id, e.source_ref, e.start_at, e.end_at, e.created_at,
                               EXISTS (
                                   SELECT 1
                                   FROM episodes w
                                   WHERE w.tombstone_at IS NULL
                                     AND w.source_name = $1
                                     AND w.episode_type = $2
                                     AND w.start_at < e.end_at
                                     AND (w.end_at IS NULL OR w.end_at > e.start_at)
                               ) AS overlaps_workout
                        FROM episodes e
                        WHERE e.tombstone_at IS NULL
                          AND e.source_name = $3
                          AND e.episode_type = $4
                          AND e.end_at IS NOT NULL
                          AND e.created_at > $5
                        ORDER BY e.created_at ASC, e.id ASC
                        LIMIT $6
                        """,
                        _WORKOUT_SOURCE,
                        _WORKOUT_EPISODE_TYPE,
                        _MOVEMENT_SOURCE,
                        _MOVEMENT_EPISODE_TYPE,
                        since,
                        self.batch_limit,
                    )
        except (asyncpg.UndefinedTableError, asyncpg.PostgresError):
            logger.exception("Failed reading chronicler.episodes for exercise inference")
            return None
        return list(rows)

    async def _corroborator_event_ids(
        self,
        chronicler_pool: asyncpg.Pool,
        start_at: datetime,
        end_at: datetime,
    ) -> list[UUID]:
        """Return elevated-HR corroborator ids inside ``[start_at, end_at]``.

        This is the single HR corroboration predicate used by both initial
        movement projection and the bounded late-sync recheck. Keeping one
        predicate prevents a span from qualifying on one path but not the
        other as source definitions evolve.
        """
        async with chronicler_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id
                FROM point_events
                WHERE tombstone_at IS NULL
                  AND source_name = $1
                  AND event_type = $2
                  AND occurred_at >= $3
                  AND occurred_at <= $4
                  AND payload ? 'bpm'
                  AND (payload->>'bpm') ~ '^-?[0-9]+$'
                  AND (payload->>'bpm')::int >= $5
                ORDER BY occurred_at ASC, id ASC
                """,
                _HEART_RATE_SOURCE,
                _HEART_RATE_EVENT_TYPE,
                start_at,
                end_at,
                self.elevated_hr_bpm,
            )
        return [r["id"] for r in rows]

    async def _maybe_project(
        self,
        chronicler_pool: asyncpg.Pool,
        row: asyncpg.Record,
        *,
        entity_id: UUID | None = None,
    ) -> Episode | None:
        start_at = row["start_at"]
        end_at = row["end_at"]
        if start_at is None or end_at is None:
            return None

        # No-duplication guard: an explicit workout already represents this
        # window as an activity, so do not infer a second exercise candidate.
        if bool(row["overlaps_workout"]):
            return None

        hr_event_ids = await self._corroborator_event_ids(chronicler_pool, start_at, end_at)
        if not hr_event_ids:
            # GPS movement alone is not exercise — it is already a travel
            # candidate. Without an elevated-HR corroboration there is nothing
            # genuinely new to emit.
            return None

        movement_id = row["id"]
        source_ref = f"chronicler.episodes:{movement_id}:exercise"

        # Two independent evidence kinds — heart rate and GPS — corroborate the
        # block, so it earns ``high`` confidence.
        confidence = derive_confidence([EvidenceKind(name="heart_rate"), EvidenceKind(name="gps")])
        evidence_refs = evidence_refs_from_event_ids(hr_event_ids)

        duration_minutes = int((end_at - start_at).total_seconds() // 60)
        title = f"Exercise ({duration_minutes}m)" if duration_minutes else "Exercise"
        payload = {
            "signal": "hr_gps_corroboration",
            "movement_episode_id": str(movement_id),
            "movement_source_ref": row["source_ref"],
            "elevated_hr_event_count": len(hr_event_ids),
            "elevated_hr_bpm_threshold": self.elevated_hr_bpm,
            "duration_minutes": duration_minutes,
        }

        async with chronicler_pool.acquire() as conn:
            episode = await upsert_episode(
                conn,
                Episode(
                    source_name=self.source_name,
                    source_ref=source_ref,
                    episode_type=EPISODE_TYPE_EXERCISE,
                    start_at=start_at,
                    end_at=end_at,
                    precision=Precision.MINUTE,
                    title=title,
                    payload=payload,
                    privacy=Privacy.SENSITIVE,
                    layer=Layer.ACTIVITY,
                    confidence=confidence,
                    evidence_refs=evidence_refs,
                ),
            )
            # Write owner row into episode_entities join table (bu-4c1ks).
            await upsert_owner_episode_entity(conn, episode.id, owner_id=entity_id)
            # Link the corroborating heart-rate events into the evidence chain.
            if episode.id is not None:
                for event_id in hr_event_ids:
                    await link_event_to_episode(
                        conn,
                        episode_id=episode.id,
                        event_id=event_id,
                        relation=LinkRelation.EVIDENCE,
                    )
        return episode

    async def _recheck_late_corroboration(
        self,
        chronicler_pool: asyncpg.Pool,
        *,
        entity_id: UUID | None,
    ) -> int:
        """Create exercises for recent movement spans whose HR arrived late.

        The candidate scan is bounded by both a trailing window and a row cap.
        It excludes source spans that already have their deterministic inferred
        exercise, so the pass is idempotent and does no historical churn.
        """
        cutoff = _now() - timedelta(hours=CORROBORATION_RECHECK_LOOKBACK_HOURS)
        async with chronicler_pool.acquire() as conn:
            candidates = await conn.fetch(
                """
                SELECT e.id, e.source_ref, e.start_at, e.end_at, e.created_at,
                       EXISTS (
                           SELECT 1
                           FROM episodes w
                           WHERE w.tombstone_at IS NULL
                             AND w.source_name = $1
                             AND w.episode_type = $2
                             AND w.start_at < e.end_at
                             AND (w.end_at IS NULL OR w.end_at > e.start_at)
                       ) AS overlaps_workout
                FROM episodes e
                WHERE e.tombstone_at IS NULL
                  AND e.source_name = $3
                  AND e.episode_type = $4
                  AND e.end_at IS NOT NULL
                  AND e.start_at >= $5
                  AND NOT EXISTS (
                      SELECT 1
                      FROM episodes inferred
                      WHERE inferred.tombstone_at IS NULL
                        AND inferred.source_name = $6
                        AND inferred.episode_type = $7
                        AND inferred.source_ref =
                            'chronicler.episodes:' || e.id::text || ':exercise'
                  )
                ORDER BY e.start_at ASC, e.id ASC
                LIMIT $8
                """,
                _WORKOUT_SOURCE,
                _WORKOUT_EPISODE_TYPE,
                _MOVEMENT_SOURCE,
                _MOVEMENT_EPISODE_TYPE,
                cutoff,
                self.source_name,
                EPISODE_TYPE_EXERCISE,
                CORROBORATION_RECHECK_LIMIT,
            )

        created = 0
        for row in candidates:
            episode = await self._maybe_project(chronicler_pool, row, entity_id=entity_id)
            if episode is None:
                continue
            created += 1
            logger.info(
                "%s: inferred exercise %s after late HR corroboration",
                SOURCE_NAME,
                row["id"],
            )
        return created


__all__ = [
    "CORROBORATION_RECHECK_LIMIT",
    "CORROBORATION_RECHECK_LOOKBACK_HOURS",
    "DEFAULT_BATCH_LIMIT",
    "ELEVATED_HR_BPM",
    "EPISODE_TYPE_EXERCISE",
    "ExerciseInferredAdapter",
    "SOURCE_NAME",
]
