"""Health meals projection adapter.

Projects meal records from ``health.meals`` into Chronicler ``eating_event``
point events.

Semantics:
- Each row in ``health.meals`` maps to exactly one ``eating_event`` point event.
  Meals have ``eaten_at`` only (no ``end_at``), so they are modelled as
  point events — NOT episodes. On the Gantt they render as tall thin markers.
- Boundary precision is ``exact`` — ``eaten_at`` carries timestamp resolution.
- Privacy class is ``sensitive`` — meal content and nutrition are personal data.
- Source ref format: ``health.meals:{row_id}`` so replays are idempotent.
- Watermark on ``eaten_at`` with ``id`` tie-breaker for tuple watermark support.
- Missing evidence table degrades gracefully (module not enabled / migration not
  run on this deployment).
- No LLM call per event — Tier-0 projection only (RFC 0014 §D5).
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

import asyncpg

from butlers.chronicler.adapters._owner_entity import resolve_owner_entity_id
from butlers.chronicler.adapters.base import AdapterResult, ProjectionAdapter
from butlers.chronicler.models import Layer, PointEvent, Precision, Privacy
from butlers.chronicler.storage import upsert_point_event

logger = logging.getLogger(__name__)

SOURCE_NAME = "health.meals"
EVENT_TYPE_EATING = "eating_event"
_EVIDENCE_TABLE = "health.meals"
DEFAULT_BATCH_LIMIT = 500


class MealsAdapter(ProjectionAdapter):
    """Project ``health.meals`` rows into Chronicler as eating_event point events.

    One row in the evidence table → one ``eating_event`` point event.
    No episode shape: meals have ``eaten_at`` only (no ``end_at``).
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

        rows = await self._fetch_meals(pool, since, since_id)
        if rows is None:
            result.skipped = True
            result.skipped_reason = (
                f"{_EVIDENCE_TABLE} not found; health meals evidence surface unavailable"
            )
            return result

        if not rows:
            result.watermark = since
            result.watermark_id = since_id
            return result

        # Resolve owner entity_id once per adapter run (not per row).
        entity_id = await resolve_owner_entity_id(pool)

        latest_watermark = since
        latest_watermark_id: int | None = since_id
        for row in rows:
            await self._project_row(chronicler_pool, row, entity_id=entity_id)
            result.rows_projected += 1
            result.point_events += 1

            candidate = row["eaten_at"]
            if candidate is not None:
                if latest_watermark is None or candidate > latest_watermark:
                    latest_watermark = candidate
                    latest_watermark_id = row["seq"]
                elif candidate == latest_watermark:
                    row_seq = row["seq"]
                    if row_seq is not None and (
                        latest_watermark_id is None or row_seq > latest_watermark_id
                    ):
                        latest_watermark_id = row_seq

        result.watermark = latest_watermark
        result.watermark_id = latest_watermark_id
        return result

    async def _fetch_meals(
        self,
        pool: asyncpg.Pool,
        since: datetime | None,
        since_id: int | None = None,
    ) -> list[asyncpg.Record] | None:
        """Fetch evidence rows since the watermark.

        The ``health.meals`` table uses UUID primary keys, not integer ``id``
        columns, so we use a stable integer sequence ``seq`` synthesised via
        ``ROW_NUMBER() OVER (ORDER BY eaten_at, id)`` for tie-breaking.  The
        source_ref is keyed to the row UUID (stable across replays), not the
        derived sequence number.

        When ``since`` and ``since_id`` are both provided, uses the tuple
        comparison ``WHERE (eaten_at, seq) > ($1, $2)`` for boundary precision.
        When only ``since`` is provided, falls back to ``WHERE eaten_at > $1``.

        Returns ``None`` if the evidence table is missing — degrade gracefully
        per RFC 0014 optional-schema guard.
        """
        try:
            async with pool.acquire() as conn:
                exists = await conn.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_schema = 'health'
                          AND table_name = 'meals'
                    )
                    """
                )
                if not exists:
                    return None

                # Use a CTE with ROW_NUMBER() to produce a stable integer
                # sequence for the tuple-watermark tie-breaker, since the
                # meals table uses UUIDs as its primary key.
                if since is None:
                    rows = await conn.fetch(
                        f"""
                        WITH numbered AS (
                            SELECT id, type, description, nutrition,
                                   eaten_at, notes, created_at,
                                   ROW_NUMBER() OVER (ORDER BY eaten_at ASC, id ASC) AS seq
                            FROM {_EVIDENCE_TABLE}
                        )
                        SELECT *
                        FROM numbered
                        ORDER BY eaten_at ASC, id ASC
                        LIMIT $1
                        """,
                        self.batch_limit,
                    )
                elif since_id is not None:
                    rows = await conn.fetch(
                        f"""
                        WITH numbered AS (
                            SELECT id, type, description, nutrition,
                                   eaten_at, notes, created_at,
                                   ROW_NUMBER() OVER (ORDER BY eaten_at ASC, id ASC) AS seq
                            FROM {_EVIDENCE_TABLE}
                        )
                        SELECT *
                        FROM numbered
                        WHERE (eaten_at, seq) > ($1, $2)
                        ORDER BY eaten_at ASC, id ASC
                        LIMIT $3
                        """,
                        since,
                        since_id,
                        self.batch_limit,
                    )
                else:
                    rows = await conn.fetch(
                        f"""
                        WITH numbered AS (
                            SELECT id, type, description, nutrition,
                                   eaten_at, notes, created_at,
                                   ROW_NUMBER() OVER (ORDER BY eaten_at ASC, id ASC) AS seq
                            FROM {_EVIDENCE_TABLE}
                        )
                        SELECT *
                        FROM numbered
                        WHERE eaten_at > $1
                        ORDER BY eaten_at ASC, id ASC
                        LIMIT $2
                        """,
                        since,
                        self.batch_limit,
                    )
        except asyncpg.PostgresError:
            logger.exception("Failed reading %s", _EVIDENCE_TABLE)
            return None

        return list(rows)

    async def _project_row(
        self,
        chronicler_pool: asyncpg.Pool,
        row: asyncpg.Record,
        *,
        entity_id: UUID | None = None,
    ) -> PointEvent:
        row_id = str(row["id"])
        source_ref = f"{_EVIDENCE_TABLE}:{row_id}"

        meal_type = row["type"]
        description = row["description"]

        if meal_type and description:
            title = f"{meal_type.capitalize()}: {description}"
        elif description:
            title = description
        else:
            title = f"Meal ({meal_type})" if meal_type else "Meal"

        payload: dict = {
            "id": row_id,
            "type": meal_type,
            "description": description,
        }
        nutrition = row["nutrition"]
        if nutrition is not None:
            payload["nutrition"] = nutrition
        notes = row["notes"]
        if notes is not None:
            payload["notes"] = notes

        async with chronicler_pool.acquire() as conn:
            event = await upsert_point_event(
                conn,
                PointEvent(
                    source_name=self.source_name,
                    source_ref=source_ref,
                    event_type=EVENT_TYPE_EATING,
                    occurred_at=row["eaten_at"],
                    precision=Precision.EXACT,
                    title=title,
                    payload=payload,
                    privacy=Privacy.SENSITIVE,
                    entity_id=entity_id,
                    layer=Layer.EVIDENCE,
                ),
            )
        return event


__all__ = [
    "DEFAULT_BATCH_LIMIT",
    "EVENT_TYPE_EATING",
    "MealsAdapter",
    "SOURCE_NAME",
]
