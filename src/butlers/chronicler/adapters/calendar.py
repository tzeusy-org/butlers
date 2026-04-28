"""Completed calendar instance projection adapter.

Projects completed non-cancelled ``calendar_event_instances`` rows from
butler schemas that host the calendar module into Chronicler
``scheduled_block`` episodes.

Semantics:
- "Completed" means ``ends_at <= now()`` AND ``status != 'cancelled'``.
- Future or open instances are NOT projected.
- Each instance maps to one ``scheduled_block`` episode with
  ``source_ref = {schema}.calendar_event_instances:{id}``.
- Boundary precision is ``exact``.
- Cross-butler deduplication: the same provider calendar event may
  appear in multiple butler schemas if more than one butler has the
  calendar module enabled. The adapter dedups by
  ``(source_id, origin_instance_ref)`` tuple exposed in the payload;
  the earliest-observed schema wins, and later schemas emit only the
  episode's deduped payload note.
- Missing calendar tables (module not enabled on this deployment)
  degrades gracefully — the adapter emits a warning and exits clean.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import asyncpg

from butlers.chronicler.adapters.base import AdapterResult, ProjectionAdapter
from butlers.chronicler.models import Episode, Precision, Privacy
from butlers.chronicler.storage import upsert_episode

logger = logging.getLogger(__name__)

SOURCE_NAME = "google_calendar.completed"
EPISODE_TYPE_SCHEDULED_BLOCK = "scheduled_block"
DEFAULT_BATCH_LIMIT = 500


class CalendarCompletedAdapter(ProjectionAdapter):
    """Project completed calendar instances into Chronicler episodes."""

    def __init__(
        self,
        butler_schemas: tuple[str, ...],
        *,
        batch_limit: int = DEFAULT_BATCH_LIMIT,
    ) -> None:
        super().__init__(SOURCE_NAME)
        self.butler_schemas = tuple(butler_schemas)
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
        latest_watermark = since
        # Provider-level dedup set for this run. Keyed on
        # (source_id, origin_instance_ref).
        seen_origin: set[tuple[str, str]] = set()
        now = datetime.now(UTC)

        for schema in self.butler_schemas:
            rows = await self._fetch_instances(pool, schema, since, now)
            if rows is None:
                result.warnings.append(
                    f"calendar_event_instances missing for schema {schema!r}; skipping"
                )
                continue

            for row in rows:
                dedup_key = (str(row["source_id"]), row["origin_instance_ref"])
                if dedup_key in seen_origin:
                    # Earlier schema already projected this origin instance.
                    continue
                seen_origin.add(dedup_key)

                await self._project_row(chronicler_pool, schema, row)
                result.rows_projected += 1
                result.episodes_closed += 1

                candidate = row["ends_at"]
                if candidate is not None and (
                    latest_watermark is None or candidate > latest_watermark
                ):
                    latest_watermark = candidate

        result.watermark = latest_watermark
        return result

    async def _fetch_instances(
        self,
        pool: asyncpg.Pool,
        schema: str,
        since: datetime | None,
        now: datetime,
    ) -> list[asyncpg.Record] | None:
        quoted = self._quote_ident(schema)
        try:
            async with pool.acquire() as conn:
                exists = await conn.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_schema = $1
                          AND table_name = 'calendar_event_instances'
                    )
                    """,
                    schema,
                )
                if not exists:
                    return None

                if since is None:
                    rows = await conn.fetch(
                        f"""
                        SELECT i.id, i.event_id, i.source_id, i.origin_instance_ref,
                               i.starts_at, i.ends_at, i.status, i.timezone,
                               i.metadata, i.updated_at,
                               e.title AS event_title,
                               e.description AS event_description,
                               e.location AS event_location
                        FROM {quoted}.calendar_event_instances AS i
                        LEFT JOIN {quoted}.calendar_events AS e ON e.id = i.event_id
                        WHERE i.ends_at <= $1
                          AND i.status != 'cancelled'
                        ORDER BY i.ends_at ASC
                        LIMIT $2
                        """,
                        now,
                        self.batch_limit,
                    )
                else:
                    rows = await conn.fetch(
                        f"""
                        SELECT i.id, i.event_id, i.source_id, i.origin_instance_ref,
                               i.starts_at, i.ends_at, i.status, i.timezone,
                               i.metadata, i.updated_at,
                               e.title AS event_title,
                               e.description AS event_description,
                               e.location AS event_location
                        FROM {quoted}.calendar_event_instances AS i
                        LEFT JOIN {quoted}.calendar_events AS e ON e.id = i.event_id
                        WHERE i.ends_at <= $1
                          AND i.ends_at > $2
                          AND i.status != 'cancelled'
                        ORDER BY i.ends_at ASC
                        LIMIT $3
                        """,
                        now,
                        since,
                        self.batch_limit,
                    )
        except asyncpg.PostgresError:
            logger.exception("Failed reading calendar_event_instances for schema %s", schema)
            return None

        return list(rows)

    async def _project_row(
        self,
        chronicler_pool: asyncpg.Pool,
        schema: str,
        row: asyncpg.Record,
    ) -> Episode:
        instance_id = row["id"]
        source_ref = f"{schema}.calendar_event_instances:{instance_id}"

        title = None
        metadata = row["metadata"] or {}
        if isinstance(metadata, dict):
            title = metadata.get("summary") or metadata.get("title")

        # Pull richer event-level context (joined from calendar_events).
        # asyncpg.Record raises KeyError for missing keys, so use defensive access.
        event_title = self._maybe(row, "event_title")
        event_description = self._maybe(row, "event_description")
        event_location = self._maybe(row, "event_location")

        payload = {
            "schema": schema,
            "instance_id": str(instance_id),
            "event_id": str(row["event_id"]),
            "source_id": str(row["source_id"]),
            "origin_instance_ref": row["origin_instance_ref"],
            "status": row["status"],
            "timezone": row["timezone"],
            "title": event_title,
            "description": event_description,
            "location": event_location,
        }

        resolved_title = (
            title
            or self._clean_text(event_title)
            or self._clean_text(event_location)
            or self._truncate(self._clean_text(event_description), 80)
            or f"{schema}: calendar block"
        )

        async with chronicler_pool.acquire() as conn:
            episode = await upsert_episode(
                conn,
                Episode(
                    source_name=self.source_name,
                    source_ref=source_ref,
                    episode_type=EPISODE_TYPE_SCHEDULED_BLOCK,
                    start_at=row["starts_at"],
                    end_at=row["ends_at"],
                    precision=Precision.EXACT,
                    title=resolved_title,
                    payload=payload,
                    privacy=Privacy.NORMAL,
                ),
            )
        return episode

    @staticmethod
    def _maybe(row: asyncpg.Record, key: str) -> Any:
        """Return ``row[key]`` if the column is present, else ``None``."""
        try:
            return row[key]
        except (KeyError, IndexError):
            return None

    @staticmethod
    def _clean_text(value: Any) -> str | None:
        """Return a stripped non-empty string, or ``None``."""
        if not isinstance(value, str):
            return None
        cleaned = value.strip()
        return cleaned or None

    @staticmethod
    def _truncate(value: str | None, max_len: int) -> str | None:
        """Truncate ``value`` to ``max_len`` characters with an ellipsis."""
        if value is None:
            return None
        if len(value) <= max_len:
            return value
        return value[: max(0, max_len - 1)].rstrip() + "…"

    @staticmethod
    def _quote_ident(name: str) -> str:
        if not name.replace("_", "").isalnum():
            raise ValueError(f"Unsafe schema identifier: {name!r}")
        return '"' + name.replace('"', '""') + '"'


__all__ = [
    "CalendarCompletedAdapter",
    "EPISODE_TYPE_SCHEDULED_BLOCK",
    "SOURCE_NAME",
]
