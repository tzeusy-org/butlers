"""Reading / learning session inference adapter.

v1 signal:

1. Calendar-titled reading blocks: ``google_calendar.completed`` episodes
   whose title matches a reading keyword
   (``read``, ``reading``, ``book:``, ``article:``, ``paper:``,
   case-insensitive, word-boundary anchored).

2. Optional secondary signal: ``health.facts`` rows whose predicate is
   ``reading_session``. Degrades gracefully when the surface is absent;
   the v1 ingestion pipeline is not guaranteed to populate this.

Outputs ``reading_block`` episodes under
``source_name = 'chronicler.reading_inferred'`` with deterministic source
refs derived from the underlying episode/fact id.

Future extension paths (not in v1): browser history connector,
Readwise / Pocket integration, dedicated reading capture tool. The
adapter docstring documents this limit so the inference is transparent.

No LLM call.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg

from butlers.chronicler.adapters._owner_entity import (
    resolve_owner_entity_id,
    upsert_owner_episode_entity,
)
from butlers.chronicler.adapters.base import AdapterResult, ProjectionAdapter
from butlers.chronicler.models import Episode, Layer, Precision, Privacy
from butlers.chronicler.storage import upsert_episode

logger = logging.getLogger(__name__)

SOURCE_NAME = "chronicler.reading_inferred"
EPISODE_TYPE_READING = "reading_block"

DEFAULT_BATCH_LIMIT = 500

_READING_KEYWORDS_RE = re.compile(
    r"\b(read|reading)\b|\b(book|article|paper):",
    re.IGNORECASE,
)


def _title_matches_reading(title: str | None) -> bool:
    if not title:
        return False
    if len(title) > 120:
        return False
    return _READING_KEYWORDS_RE.search(title) is not None


class ReadingInferredAdapter(ProjectionAdapter):
    """Project reading_block episodes inferred from calendar titles + facts."""

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

        # Calendar-derived signal — read from chronicler's own episodes table.
        cal_rows = await self._fetch_calendar_rows(chronicler_pool, since)
        # Fact-derived signal — read from health.facts via the cross-butler pool.
        fact_rows = await self._fetch_reading_facts(pool, since)

        if cal_rows is None and fact_rows is None:
            result.skipped = True
            result.skipped_reason = "no source surface available"
            return result

        # Resolve owner entity_id once per adapter run (not per row).
        entity_id = await resolve_owner_entity_id(pool)

        latest_watermark = since
        for row in cal_rows or []:
            episode = await self._project_calendar_row(chronicler_pool, row, entity_id=entity_id)
            candidate = row["created_at"]
            if candidate is not None and (latest_watermark is None or candidate > latest_watermark):
                latest_watermark = candidate
            if episode is not None:
                result.rows_projected += 1
                result.episodes_closed += 1
        for row in fact_rows or []:
            episode = await self._project_fact_row(chronicler_pool, row, entity_id=entity_id)
            candidate = row["created_at"]
            if candidate is not None and (latest_watermark is None or candidate > latest_watermark):
                latest_watermark = candidate
            if episode is not None:
                result.rows_projected += 1
                result.episodes_closed += 1
        result.watermark = latest_watermark
        return result

    async def _fetch_calendar_rows(
        self, chronicler_pool: asyncpg.Pool, since: datetime | None
    ) -> list[asyncpg.Record] | None:
        try:
            async with chronicler_pool.acquire() as conn:
                if since is None:
                    rows = await conn.fetch(
                        """
                        SELECT id, source_name, source_ref, episode_type,
                               start_at, end_at, title, payload, created_at
                        FROM episodes
                        WHERE tombstone_at IS NULL
                          AND source_name = 'google_calendar.completed'
                          AND episode_type = 'scheduled_block'
                        ORDER BY created_at ASC, id ASC
                        LIMIT $1
                        """,
                        self.batch_limit,
                    )
                else:
                    rows = await conn.fetch(
                        """
                        SELECT id, source_name, source_ref, episode_type,
                               start_at, end_at, title, payload, created_at
                        FROM episodes
                        WHERE tombstone_at IS NULL
                          AND source_name = 'google_calendar.completed'
                          AND episode_type = 'scheduled_block'
                          AND created_at > $1
                        ORDER BY created_at ASC, id ASC
                        LIMIT $2
                        """,
                        since,
                        self.batch_limit,
                    )
        except (asyncpg.UndefinedTableError, asyncpg.PostgresError):
            logger.exception("Failed reading chronicler.episodes for reading inference")
            return None
        return list(rows)

    async def _fetch_reading_facts(
        self, pool: asyncpg.Pool, since: datetime | None
    ) -> list[asyncpg.Record] | None:
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
                        """
                        SELECT id, valid_at, content, metadata, created_at,
                               idempotency_key
                        FROM health.facts
                        WHERE predicate = 'reading_session'
                          AND validity = 'active'
                        ORDER BY created_at ASC, id ASC
                        LIMIT $1
                        """,
                        self.batch_limit,
                    )
                else:
                    rows = await conn.fetch(
                        """
                        SELECT id, valid_at, content, metadata, created_at,
                               idempotency_key
                        FROM health.facts
                        WHERE predicate = 'reading_session'
                          AND validity = 'active'
                          AND created_at > $1
                        ORDER BY created_at ASC, id ASC
                        LIMIT $2
                        """,
                        since,
                        self.batch_limit,
                    )
        except (asyncpg.UndefinedTableError, asyncpg.PostgresError):
            logger.exception("Failed reading health.facts for reading inference")
            return None
        return list(rows)

    async def _project_calendar_row(
        self,
        chronicler_pool: asyncpg.Pool,
        row: asyncpg.Record,
        *,
        entity_id: UUID | None = None,
    ) -> Episode | None:
        title = row["title"]
        if not _title_matches_reading(title):
            return None
        start_at = row["start_at"]
        end_at = row["end_at"]
        if start_at is None or end_at is None:
            return None
        duration_minutes = int((end_at - start_at).total_seconds() // 60)
        if duration_minutes <= 0:
            return None

        source_ref = f"chronicler.episodes:{row['id']}:reading"
        payload: dict[str, Any] = {
            "signal": "calendar_titled",
            "source_episode_id": str(row["id"]),
            "source_episode_source_name": row["source_name"],
            "source_episode_source_ref": row["source_ref"],
            "duration_minutes": duration_minutes,
        }
        out_title = f"Reading: {title}"
        async with chronicler_pool.acquire() as conn:
            episode = await upsert_episode(
                conn,
                Episode(
                    source_name=self.source_name,
                    source_ref=source_ref,
                    episode_type=EPISODE_TYPE_READING,
                    start_at=start_at,
                    end_at=end_at,
                    precision=Precision.MINUTE,
                    title=out_title[:200],
                    payload=payload,
                    privacy=Privacy.NORMAL,
                    layer=Layer.ACTIVITY,
                ),
            )
            # Write owner row into episode_entities join table (bu-4c1ks).
            await upsert_owner_episode_entity(conn, episode.id, owner_id=entity_id)
            return episode

    async def _project_fact_row(
        self,
        chronicler_pool: asyncpg.Pool,
        row: asyncpg.Record,
        *,
        entity_id: UUID | None = None,
    ) -> Episode | None:
        start_at: datetime | None = row["valid_at"]
        if start_at is None:
            return None

        metadata: dict[str, Any] = dict(row["metadata"] or {})
        duration_ms = int(metadata.get("duration_ms") or 0)
        if duration_ms <= 0:
            return None
        end_at = start_at + timedelta(milliseconds=duration_ms)
        idempotency_key = row["idempotency_key"]
        fact_id = str(row["id"])
        if idempotency_key:
            source_ref = f"health.facts:reading_session:{idempotency_key}"
        else:
            source_ref = f"health.facts:reading_session:{fact_id}"
        title_raw = metadata.get("title") or row["content"] or "Reading"
        title = f"Reading: {str(title_raw).strip()}"[:200]
        payload: dict[str, Any] = {
            "signal": "health_fact",
            "fact_id": fact_id,
            "idempotency_key": idempotency_key,
            "duration_minutes": duration_ms // 60_000,
        }
        for field_name in ("source", "url", "topic"):
            val = metadata.get(field_name)
            if val is not None:
                payload[field_name] = val
        async with chronicler_pool.acquire() as conn:
            episode = await upsert_episode(
                conn,
                Episode(
                    source_name=self.source_name,
                    source_ref=source_ref,
                    episode_type=EPISODE_TYPE_READING,
                    start_at=start_at,
                    end_at=end_at,
                    precision=Precision.MINUTE,
                    title=title,
                    payload=payload,
                    privacy=Privacy.NORMAL,
                    layer=Layer.ACTIVITY,
                ),
            )
            # Write owner row into episode_entities join table (bu-4c1ks).
            await upsert_owner_episode_entity(conn, episode.id, owner_id=entity_id)
            return episode


__all__ = [
    "DEFAULT_BATCH_LIMIT",
    "EPISODE_TYPE_READING",
    "ReadingInferredAdapter",
    "SOURCE_NAME",
]
