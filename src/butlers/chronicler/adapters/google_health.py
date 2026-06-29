"""Google Health sleep-episode projection adapter.

Projects sleep session facts from the Health butler's ``facts`` table
(predicate = ``sleep_session``) into Chronicler ``sleep_episode`` episodes.

Semantics:
- Each ``sleep_session`` fact in ``health.facts`` maps to exactly one
  ``sleep_episode`` in Chronicler.
- ``start_at``  = ``facts.valid_at``   (session start time).
- ``end_at``    = ``metadata.end_time`` when present; falls back to
  ``start_at + duration_ms / 1000`` when ``end_time`` is absent.
- Boundary precision is ``minute`` — Google Health timestamps are derived
  from wearable device clocks; sub-minute precision is not guaranteed.
- Privacy class is ``sensitive`` — biometric sleep data is retrospective
  personal health information.
- Source ref format:
  ``health.facts:sleep_session:{idempotency_key}``
  where ``idempotency_key`` is the value written by the wellness-ingest
  pipeline (``google_health:sleep:{session_id}:session`` or the unqualified
  ``google_health:sleep:{session_id}``).  The key is stable across replays.
- Cross-batch stitching: if the prior batch ended with an open sleep episode
  (``end_at`` absent/None) and the first row in the new batch continues that
  same session (matching session_id OR starting within
  ``SLEEP_STITCH_GAP_MINUTES`` of the prior session's ``start_at``), the
  prior ``source_ref`` is reused so the episode is extended in-place rather
  than fragmented.
- Watermark on ``created_at`` (monotonically increasing at fact-write time).
  Because ``facts.id`` is a UUID (not an integer serial), the adapter uses
  single-column ``WHERE created_at > $1`` semantics rather than the tuple
  ``(created_at, id)`` form used by adapters with integer row IDs.
- Missing ``health.facts`` table or schema degrades gracefully
  (module/migration not deployed on this instance).
- No LLM call per event — Tier-0 projection only (RFC 0014 §D5).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg

from butlers.chronicler.adapters._owner_entity import (
    resolve_owner_entity_id,
    upsert_owner_episode_entity,
)
from butlers.chronicler.adapters.base import AdapterResult, ProjectionAdapter
from butlers.chronicler.models import Episode, Layer, PointEvent, Precision, Privacy
from butlers.chronicler.storage import (
    get_carryover,
    save_carryover,
    upsert_episode,
    upsert_point_event,
)

logger = logging.getLogger(__name__)

SOURCE_NAME = "google_health.measurements"
SOURCE_NAME_HEART_RATE = "health.heart_rate"
SOURCE_NAME_STEPS = "health.steps"
EPISODE_TYPE_SLEEP = "sleep_episode"
EPISODE_TYPE_WORKOUT = "workout_episode"
EVENT_TYPE_HEART_RATE = "heart_rate_summary"
EVENT_TYPE_STEPS = "daily_steps"
_FACTS_TABLE = "health.facts"
_PREDICATE = "sleep_session"
_WORKOUT_PREDICATE = "workout_session"
_HEART_RATE_PREDICATES = (
    "measurement_resting_hr",
    "heart_rate_summary",
    "measurement_heart_rate",
)
_STEPS_PREDICATES = ("measurement_steps", "daily_steps")
DEFAULT_BATCH_LIMIT = 500

# Maximum gap in minutes between the prior batch's open sleep episode start_at
# and a new batch row's start_at for them to be considered the same session
# when the session_id cannot be compared (e.g. idempotency key format differs).
SLEEP_STITCH_GAP_MINUTES = 30


class GoogleHealthSleepAdapter(ProjectionAdapter):
    """Project ``health.facts`` sleep-session rows into Chronicler.

    One ``sleep_session`` fact → one ``sleep_episode`` in Chronicler.
    The episode spans ``[valid_at, end_at)`` where ``end_at`` is derived from
    the fact's ``metadata.end_time`` (preferred) or
    ``valid_at + duration_ms / 1000`` (fallback).

    Cross-batch stitching is applied automatically: if the previous batch
    ended with an open sleep episode (no ``end_at``), the open episode's
    ``source_ref`` is reused for matching rows in the next batch so that
    the episode is extended in-place rather than producing a fragmented pair.
    A row "matches" the carryover when it shares the same session_id OR
    starts within ``SLEEP_STITCH_GAP_MINUTES`` of the prior session's start.
    """

    def __init__(
        self,
        *,
        batch_limit: int = DEFAULT_BATCH_LIMIT,
        sleep_stitch_gap_minutes: int = SLEEP_STITCH_GAP_MINUTES,
    ) -> None:
        super().__init__(SOURCE_NAME)
        self.batch_limit = batch_limit
        self.sleep_stitch_gap_minutes = sleep_stitch_gap_minutes

    async def project(
        self,
        pool: asyncpg.Pool,
        *,
        chronicler_pool: asyncpg.Pool,
        since: datetime | None,
        since_id: int | None = None,
    ) -> AdapterResult:
        result = AdapterResult(source_name=self.source_name)

        rows = await self._fetch_facts(pool, since)
        if rows is None:
            result.skipped = True
            result.skipped_reason = (
                f"{_FACTS_TABLE} not found; Google Health sleep evidence surface unavailable"
            )
            return result

        # Resolve owner entity_id once per adapter run (not per row).
        entity_id = await resolve_owner_entity_id(pool)

        # Load prior-batch carryover state for cross-batch stitching.
        prior_carryover = await get_carryover(chronicler_pool, self.source_name)

        latest_watermark = since
        last_open_episode: dict | None = None  # track the last open (no end_at) episode

        for row in rows:
            prior_source_ref = self._match_carryover(prior_carryover, row)
            episode = await self._project_row(
                chronicler_pool, row, prior_source_ref, entity_id=entity_id
            )
            if episode is None:
                continue
            result.rows_projected += 1
            result.episodes_closed += 1

            candidate: datetime | None = row["created_at"]
            if candidate is not None:
                if latest_watermark is None or candidate > latest_watermark:
                    latest_watermark = candidate

            # Track whether the last valid episode is "open" (no end_at).
            if episode.end_at is None:
                last_open_episode = {
                    "source_ref": episode.source_ref,
                    "start_at": episode.start_at.isoformat(),
                    "session_id": self._extract_session_id(row),
                }
            else:
                last_open_episode = None

        # Persist carryover only when there were valid rows (protects against
        # an empty/malformed batch erasing prior state).
        if result.rows_projected > 0:
            new_carryover = (
                {"open_episode": last_open_episode} if last_open_episode is not None else {}
            )
            await save_carryover(chronicler_pool, self.source_name, new_carryover)

        result.watermark = latest_watermark
        # watermark_id is left None: facts.id is UUID, not an integer serial.
        return result

    def _match_carryover(
        self,
        prior_carryover: dict,
        row: asyncpg.Record,
    ) -> str | None:
        """Return the prior episode's source_ref if this row continues it.

        Matching criteria (either is sufficient):
        1. Same session_id as the carryover open episode.
        2. Row's start_at (valid_at) is within ``sleep_stitch_gap_minutes``
           of the carryover episode's start_at (temporal proximity).

        Returns ``None`` when there is no carryover, the carryover is
        malformed, or the row does not match.
        """
        if not prior_carryover:
            return None

        open_ep = prior_carryover.get("open_episode")
        if not open_ep:
            return None

        try:
            prior_source_ref: str = open_ep["source_ref"]
            prior_start_at = datetime.fromisoformat(open_ep["start_at"])
            prior_session_id: str | None = open_ep.get("session_id")
        except (KeyError, ValueError):
            logger.warning(
                "google_health sleep adapter: discarding malformed carryover: %r", open_ep
            )
            return None

        # Check session_id match.
        row_session_id = self._extract_session_id(row)
        if prior_session_id and row_session_id and prior_session_id == row_session_id:
            return prior_source_ref

        # Check temporal proximity.
        row_start_at: datetime | None = row["valid_at"]
        if row_start_at is not None:
            gap = abs((row_start_at - prior_start_at).total_seconds())
            if gap <= self.sleep_stitch_gap_minutes * 60:
                return prior_source_ref

        return None

    @staticmethod
    def _extract_session_id(row: asyncpg.Record) -> str | None:
        """Extract session_id from a fact row's metadata, or None if absent."""
        metadata: dict = dict(row["metadata"] or {})
        session_id = metadata.get("session_id")
        return str(session_id) if session_id is not None else None

    async def _fetch_facts(
        self,
        pool: asyncpg.Pool,
        since: datetime | None,
    ) -> list[asyncpg.Record] | None:
        """Fetch sleep_session facts since the watermark.

        Uses single-column ``WHERE created_at > $1`` because ``facts.id`` is
        a UUID primary key and the base-class tuple-watermark contract requires
        an integer ``since_id``.

        Returns ``None`` if the ``health.facts`` table is missing — degrades
        gracefully per RFC 0014 optional-schema guard.
        """
        try:
            async with pool.acquire() as conn:
                exists = await conn.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_schema = 'health'
                          AND table_name = 'facts'
                    )
                    """
                )
                if not exists:
                    return None

                if since is None:
                    rows = await conn.fetch(
                        f"""
                        SELECT id, subject, predicate, content, metadata,
                               valid_at, created_at, idempotency_key
                        FROM {_FACTS_TABLE}
                        WHERE predicate = $1
                          AND validity = 'active'
                        ORDER BY created_at ASC, id ASC
                        LIMIT $2
                        """,
                        _PREDICATE,
                        self.batch_limit,
                    )
                else:
                    rows = await conn.fetch(
                        f"""
                        SELECT id, subject, predicate, content, metadata,
                               valid_at, created_at, idempotency_key
                        FROM {_FACTS_TABLE}
                        WHERE predicate = $1
                          AND validity = 'active'
                          AND created_at > $2
                        ORDER BY created_at ASC, id ASC
                        LIMIT $3
                        """,
                        _PREDICATE,
                        since,
                        self.batch_limit,
                    )
        except asyncpg.PostgresError:
            logger.exception("Failed reading %s (predicate=%s)", _FACTS_TABLE, _PREDICATE)
            return None

        return list(rows)

    async def _project_row(
        self,
        chronicler_pool: asyncpg.Pool,
        row: asyncpg.Record,
        prior_source_ref: str | None = None,
        *,
        entity_id: UUID | None = None,
    ) -> Episode | None:
        idempotency_key: str | None = row["idempotency_key"]
        fact_id = str(row["id"])

        # Stable source_ref: if cross-batch stitching identified a prior open
        # episode for this session, reuse it.  Otherwise prefer idempotency_key,
        # fall back to fact UUID.
        if prior_source_ref is not None:
            source_ref = prior_source_ref
        elif idempotency_key:
            source_ref = f"{_FACTS_TABLE}:{_PREDICATE}:{idempotency_key}"
        else:
            source_ref = f"{_FACTS_TABLE}:{_PREDICATE}:{fact_id}"

        # --- Derive episode boundaries -----------------------------------
        start_at: datetime | None = row["valid_at"]
        if start_at is None:
            logger.warning(
                "google_health sleep adapter: fact %s has null valid_at; skipping",
                fact_id,
            )
            return None

        metadata: dict[str, Any] = dict(row["metadata"] or {})
        end_at = _derive_end_at(start_at, metadata)

        # --- Build title -------------------------------------------------
        duration_ms: int = int(metadata.get("duration_ms") or 0)
        efficiency = metadata.get("efficiency")
        if duration_ms:
            hours = duration_ms // 3_600_000
            mins = (duration_ms % 3_600_000) // 60_000
            dur_label = f"{hours}h {mins}m" if hours else f"{mins}m"
        else:
            dur_label = "?"

        if efficiency is not None:
            title = f"Slept {dur_label} ({efficiency}% efficiency)"
        else:
            title = f"Slept {dur_label}"

        # --- Payload -----------------------------------------------------
        payload: dict[str, Any] = {
            "fact_id": fact_id,
            "idempotency_key": idempotency_key,
            "duration_ms": duration_ms or None,
            "efficiency": efficiency,
        }

        stages = metadata.get("stages") or {}
        if stages:
            payload["stages"] = stages

        for field in ("minutes_asleep", "minutes_awake", "session_id"):
            val = metadata.get(field)
            if val is not None:
                payload[field] = val

        async with chronicler_pool.acquire() as conn:
            episode = await upsert_episode(
                conn,
                Episode(
                    source_name=self.source_name,
                    source_ref=source_ref,
                    episode_type=EPISODE_TYPE_SLEEP,
                    start_at=start_at,
                    end_at=end_at,
                    precision=Precision.MINUTE,
                    title=title,
                    payload=payload,
                    privacy=Privacy.SENSITIVE,
                    layer=Layer.ACTIVITY,
                ),
            )
            # Write owner row into episode_entities join table (bu-4c1ks).
            await upsert_owner_episode_entity(conn, episode.id, owner_id=entity_id)
        return episode


def _derive_end_at(start_at: datetime, metadata: dict[str, Any]) -> datetime | None:
    """Derive ``end_at`` from metadata, returning ``None`` when undetermined.

    Priority order:
    1. ``metadata.end_time``  — ISO-8601 string written by the connector.
    2. ``start_at + duration_ms / 1000``  — computed from duration.
    3. ``None``  — insufficient data.
    """
    end_time_raw = metadata.get("end_time")
    if end_time_raw:
        try:
            # asyncpg may decode TIMESTAMPTZ columns automatically, but
            # end_time is stored as a JSONB string by wellness_ingest.
            if isinstance(end_time_raw, datetime):
                return end_time_raw
            dt = datetime.fromisoformat(str(end_time_raw).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt
        except (ValueError, TypeError):
            logger.warning(
                "google_health sleep adapter: could not parse end_time %r; "
                "falling back to duration",
                end_time_raw,
            )

    duration_ms = int(metadata.get("duration_ms") or 0)
    if duration_ms > 0:
        return start_at + timedelta(milliseconds=duration_ms)

    return None


def _source_ref_for_fact(row: asyncpg.Record) -> str:
    predicate = row["predicate"]
    idempotency_key: str | None = row["idempotency_key"]
    fact_id = str(row["id"])
    if idempotency_key:
        return f"{_FACTS_TABLE}:{predicate}:{idempotency_key}"
    return f"{_FACTS_TABLE}:{predicate}:{fact_id}"


def _first_number(metadata: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = metadata.get(key)
        if value is None or isinstance(value, bool):
            continue
        if isinstance(value, int | float):
            return float(value)
        try:
            return float(str(value).strip())
        except (TypeError, ValueError):
            continue
    return None


async def _fetch_fact_rows(
    pool: asyncpg.Pool,
    *,
    predicates: tuple[str, ...],
    since: datetime | None,
    batch_limit: int,
) -> list[asyncpg.Record] | None:
    """Fetch active health facts for one or more predicates.

    Returns ``None`` when ``health.facts`` is unavailable so adapters can
    degrade in the same way as the sleep projection.
    """
    try:
        async with pool.acquire() as conn:
            exists = await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'health'
                      AND table_name = 'facts'
                )
                """
            )
            if not exists:
                return None

            if since is None:
                rows = await conn.fetch(
                    f"""
                    SELECT id, subject, predicate, content, metadata,
                           valid_at, created_at, idempotency_key
                    FROM {_FACTS_TABLE}
                    WHERE predicate = ANY($1::text[])
                      AND validity = 'active'
                    ORDER BY created_at ASC, id ASC
                    LIMIT $2
                    """,
                    list(predicates),
                    batch_limit,
                )
            else:
                rows = await conn.fetch(
                    f"""
                    SELECT id, subject, predicate, content, metadata,
                           valid_at, created_at, idempotency_key
                    FROM {_FACTS_TABLE}
                    WHERE predicate = ANY($1::text[])
                      AND validity = 'active'
                      AND created_at > $2
                    ORDER BY created_at ASC, id ASC
                    LIMIT $3
                    """,
                    list(predicates),
                    since,
                    batch_limit,
                )
    except asyncpg.PostgresError:
        logger.exception("Failed reading %s (predicates=%s)", _FACTS_TABLE, predicates)
        return None
    return list(rows)


class GoogleHealthWorkoutAdapter(ProjectionAdapter):
    """Project ``health.facts`` workout-session rows into Chronicler.

    One ``workout_session`` fact → one ``workout_episode`` in Chronicler.
    The episode spans ``[valid_at, end_at)`` where ``end_at`` is derived
    from ``metadata.end_time`` (preferred) or ``valid_at + duration_ms``
    (fallback).

    Source ref format::

        health.facts:workout_session:{idempotency_key}

    Falls back to the fact UUID when ``idempotency_key`` is absent.

    Boundary precision is ``minute``. Privacy is ``normal`` for aggregate
    workout facts, and escalates to ``sensitive`` when the fact carries
    heart-rate fields.
    """

    def __init__(self, *, batch_limit: int = DEFAULT_BATCH_LIMIT) -> None:
        super().__init__(SOURCE_NAME)
        self.batch_limit = batch_limit

    async def project(
        self,
        pool: asyncpg.Pool,
        *,
        chronicler_pool: asyncpg.Pool,
        since: datetime | None,
        since_id: int | None = None,
    ) -> AdapterResult:
        result = AdapterResult(source_name=self.source_name)

        rows = await self._fetch_workout_facts(pool, since)
        if rows is None:
            result.skipped = True
            result.skipped_reason = (
                f"{_FACTS_TABLE} not found; Google Health workout evidence surface unavailable"
            )
            return result

        # Resolve owner entity_id once per adapter run (not per row).
        entity_id = await resolve_owner_entity_id(pool)

        latest_watermark = since
        for row in rows:
            episode = await self._project_row(chronicler_pool, row, entity_id=entity_id)
            if episode is None:
                continue
            result.rows_projected += 1
            result.episodes_closed += 1
            candidate: datetime | None = row["created_at"]
            if candidate is not None:
                if latest_watermark is None or candidate > latest_watermark:
                    latest_watermark = candidate
        result.watermark = latest_watermark
        return result

    async def _fetch_workout_facts(
        self,
        pool: asyncpg.Pool,
        since: datetime | None,
    ) -> list[asyncpg.Record] | None:
        """Fetch workout_session facts since the watermark.

        Returns ``None`` if ``health.facts`` is missing — degrades gracefully.
        """
        try:
            async with pool.acquire() as conn:
                exists = await conn.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_schema = 'health' AND table_name = 'facts'
                    )
                    """
                )
                if not exists:
                    return None

                if since is None:
                    rows = await conn.fetch(
                        f"""
                        SELECT id, subject, predicate, content, metadata,
                               valid_at, created_at, idempotency_key
                        FROM {_FACTS_TABLE}
                        WHERE predicate = $1
                          AND validity = 'active'
                        ORDER BY created_at ASC, id ASC
                        LIMIT $2
                        """,
                        _WORKOUT_PREDICATE,
                        self.batch_limit,
                    )
                else:
                    rows = await conn.fetch(
                        f"""
                        SELECT id, subject, predicate, content, metadata,
                               valid_at, created_at, idempotency_key
                        FROM {_FACTS_TABLE}
                        WHERE predicate = $1
                          AND validity = 'active'
                          AND created_at > $2
                        ORDER BY created_at ASC, id ASC
                        LIMIT $3
                        """,
                        _WORKOUT_PREDICATE,
                        since,
                        self.batch_limit,
                    )
        except asyncpg.PostgresError:
            logger.exception("Failed reading %s (predicate=%s)", _FACTS_TABLE, _WORKOUT_PREDICATE)
            return None
        return list(rows)

    async def _project_row(
        self,
        chronicler_pool: asyncpg.Pool,
        row: asyncpg.Record,
        *,
        entity_id: UUID | None = None,
    ) -> Episode | None:
        idempotency_key: str | None = row["idempotency_key"]
        fact_id = str(row["id"])

        if idempotency_key:
            source_ref = f"{_FACTS_TABLE}:{_WORKOUT_PREDICATE}:{idempotency_key}"
        else:
            source_ref = f"{_FACTS_TABLE}:{_WORKOUT_PREDICATE}:{fact_id}"

        start_at: datetime | None = row["valid_at"]
        if start_at is None:
            logger.warning(
                "google_health workout adapter: fact %s has null valid_at; skipping",
                fact_id,
            )
            return None

        metadata: dict[str, Any] = dict(row["metadata"] or {})
        end_at = _derive_end_at(start_at, metadata)

        activity_type = str(metadata.get("activity_type") or "workout").strip() or "workout"
        duration_ms = int(metadata.get("duration_ms") or 0)
        if duration_ms:
            mins = duration_ms // 60_000
            title = f"{activity_type.title()} ({mins}m)"
        else:
            title = activity_type.title()

        payload: dict[str, Any] = {
            "fact_id": fact_id,
            "idempotency_key": idempotency_key,
            "activity_type": activity_type,
            "duration_ms": duration_ms or None,
        }
        for field_name in (
            "calories",
            "distance_m",
            "average_heart_rate",
            "max_heart_rate",
            "session_id",
        ):
            val = metadata.get(field_name)
            if val is not None:
                payload[field_name] = val
        privacy = (
            Privacy.SENSITIVE
            if payload.get("average_heart_rate") is not None
            or payload.get("max_heart_rate") is not None
            else Privacy.NORMAL
        )

        async with chronicler_pool.acquire() as conn:
            episode = await upsert_episode(
                conn,
                Episode(
                    source_name=self.source_name,
                    source_ref=source_ref,
                    episode_type=EPISODE_TYPE_WORKOUT,
                    start_at=start_at,
                    end_at=end_at,
                    precision=Precision.MINUTE,
                    title=title,
                    payload=payload,
                    privacy=privacy,
                    layer=Layer.ACTIVITY,
                ),
            )
            # Write owner row into episode_entities join table (bu-4c1ks).
            await upsert_owner_episode_entity(conn, episode.id, owner_id=entity_id)
        return episode


class GoogleHealthStepsAdapter(ProjectionAdapter):
    """Project Google Health step-count facts into Chronicler point events."""

    def __init__(self, *, batch_limit: int = DEFAULT_BATCH_LIMIT) -> None:
        super().__init__(SOURCE_NAME_STEPS)
        self.batch_limit = batch_limit

    async def project(
        self,
        pool: asyncpg.Pool,
        *,
        chronicler_pool: asyncpg.Pool,
        since: datetime | None,
        since_id: int | None = None,
    ) -> AdapterResult:
        result = AdapterResult(source_name=self.source_name)
        rows = await _fetch_fact_rows(
            pool,
            predicates=_STEPS_PREDICATES,
            since=since,
            batch_limit=self.batch_limit,
        )
        if rows is None:
            result.skipped = True
            result.skipped_reason = (
                f"{_FACTS_TABLE} not found; Google Health steps evidence surface unavailable"
            )
            return result

        # Resolve owner entity_id once per adapter run (not per row).
        entity_id = await resolve_owner_entity_id(pool)

        latest_watermark = since
        for row in rows:
            event = await self._project_row(chronicler_pool, row, entity_id=entity_id)
            if event is None:
                continue
            result.rows_projected += 1
            result.point_events += 1
            candidate: datetime | None = row["created_at"]
            if candidate is not None:
                if latest_watermark is None or candidate > latest_watermark:
                    latest_watermark = candidate
        result.watermark = latest_watermark
        return result

    async def _project_row(
        self,
        chronicler_pool: asyncpg.Pool,
        row: asyncpg.Record,
        *,
        entity_id: UUID | None = None,
    ) -> PointEvent | None:
        occurred_at: datetime | None = row["valid_at"]
        if occurred_at is None:
            logger.warning(
                "google_health steps adapter: fact %s has null valid_at; skipping",
                row["id"],
            )
            return None

        metadata: dict[str, Any] = dict(row["metadata"] or {})
        steps = _first_number(metadata, "value", "steps", "count")
        title = f"Steps: {int(steps):,}" if steps is not None else "Steps"
        payload: dict[str, Any] = {
            "fact_id": str(row["id"]),
            "idempotency_key": row["idempotency_key"],
            "predicate": row["predicate"],
        }
        if steps is not None:
            payload["steps"] = int(steps)
        for field_name in (
            "distance_km",
            "distance_m",
            "floors",
            "very_active_minutes",
            "fairly_active_minutes",
            "lightly_active_minutes",
            "sedentary_minutes",
        ):
            val = metadata.get(field_name)
            if val is not None:
                payload[field_name] = val

        async with chronicler_pool.acquire() as conn:
            return await upsert_point_event(
                conn,
                PointEvent(
                    source_name=self.source_name,
                    source_ref=_source_ref_for_fact(row),
                    event_type=EVENT_TYPE_STEPS,
                    occurred_at=occurred_at,
                    precision=Precision.DAY,
                    title=title,
                    payload=payload,
                    privacy=Privacy.NORMAL,
                    entity_id=entity_id,
                    layer=Layer.EVIDENCE,
                ),
            )


class GoogleHealthHeartRateAdapter(ProjectionAdapter):
    """Project Google Health heart-rate summary facts into point events."""

    def __init__(self, *, batch_limit: int = DEFAULT_BATCH_LIMIT) -> None:
        super().__init__(SOURCE_NAME_HEART_RATE)
        self.batch_limit = batch_limit

    async def project(
        self,
        pool: asyncpg.Pool,
        *,
        chronicler_pool: asyncpg.Pool,
        since: datetime | None,
        since_id: int | None = None,
    ) -> AdapterResult:
        result = AdapterResult(source_name=self.source_name)
        rows = await _fetch_fact_rows(
            pool,
            predicates=_HEART_RATE_PREDICATES,
            since=since,
            batch_limit=self.batch_limit,
        )
        if rows is None:
            result.skipped = True
            result.skipped_reason = (
                f"{_FACTS_TABLE} not found; Google Health heart-rate surface unavailable"
            )
            return result

        # Resolve owner entity_id once per adapter run (not per row).
        entity_id = await resolve_owner_entity_id(pool)

        latest_watermark = since
        for row in rows:
            event = await self._project_row(chronicler_pool, row, entity_id=entity_id)
            if event is None:
                continue
            result.rows_projected += 1
            result.point_events += 1
            candidate: datetime | None = row["created_at"]
            if candidate is not None:
                if latest_watermark is None or candidate > latest_watermark:
                    latest_watermark = candidate
        result.watermark = latest_watermark
        return result

    async def _project_row(
        self,
        chronicler_pool: asyncpg.Pool,
        row: asyncpg.Record,
        *,
        entity_id: UUID | None = None,
    ) -> PointEvent | None:
        occurred_at: datetime | None = row["valid_at"]
        if occurred_at is None:
            logger.warning(
                "google_health heart-rate adapter: fact %s has null valid_at; skipping",
                row["id"],
            )
            return None

        metadata: dict[str, Any] = dict(row["metadata"] or {})
        bpm = _first_number(
            metadata,
            "value",
            "bpm",
            "avg_bpm",
            "average_heart_rate",
            "resting_hr",
        )
        predicate = row["predicate"]
        if bpm is None:
            title = "Heart rate"
        elif predicate == "measurement_resting_hr":
            title = f"Resting heart rate: {int(bpm)} bpm"
        else:
            title = f"Heart rate: {int(bpm)} bpm"

        payload: dict[str, Any] = {
            "fact_id": str(row["id"]),
            "idempotency_key": row["idempotency_key"],
            "predicate": predicate,
        }
        if bpm is not None:
            payload["bpm"] = int(bpm)
        for field_name in (
            "heart_rate_zones",
            "min_bpm",
            "max_bpm",
            "average_heart_rate",
            "max_heart_rate",
        ):
            val = metadata.get(field_name)
            if val is not None:
                payload[field_name] = val

        precision = Precision.MINUTE if predicate == "measurement_heart_rate" else Precision.DAY
        async with chronicler_pool.acquire() as conn:
            return await upsert_point_event(
                conn,
                PointEvent(
                    source_name=self.source_name,
                    source_ref=_source_ref_for_fact(row),
                    event_type=EVENT_TYPE_HEART_RATE,
                    occurred_at=occurred_at,
                    precision=precision,
                    title=title,
                    payload=payload,
                    privacy=Privacy.SENSITIVE,
                    entity_id=entity_id,
                    layer=Layer.EVIDENCE,
                ),
            )


__all__ = [
    "DEFAULT_BATCH_LIMIT",
    "EVENT_TYPE_HEART_RATE",
    "EVENT_TYPE_STEPS",
    "EPISODE_TYPE_SLEEP",
    "EPISODE_TYPE_WORKOUT",
    "GoogleHealthHeartRateAdapter",
    "GoogleHealthSleepAdapter",
    "GoogleHealthStepsAdapter",
    "GoogleHealthWorkoutAdapter",
    "SLEEP_STITCH_GAP_MINUTES",
    "SOURCE_NAME",
    "SOURCE_NAME_HEART_RATE",
    "SOURCE_NAME_STEPS",
]
