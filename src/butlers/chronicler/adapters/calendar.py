"""Completed calendar instance projection adapter.

Projects completed non-cancelled ``calendar_event_instances`` rows from
butler schemas that host the calendar module into Chronicler
``scheduled_block`` episodes.

Semantics:
- "Completed" means ``ends_at <= now()`` AND ``status != 'cancelled'``.
- Future or open instances are NOT projected.
- Each instance maps to one ``scheduled_block`` episode with
  ``source_ref = calendar:{origin_instance_ref}``.
- Boundary precision is ``exact``.
- Cross-butler deduplication: the same provider calendar event may
  appear in multiple butler schemas (one row per schema in
  ``calendar_event_instances``) and may even appear under multiple
  ``event_id`` values within a single schema (if the calendar sync
  inserted the upstream event more than once). The adapter dedups
  globally by ``origin_instance_ref`` — the upstream Google Calendar
  instance identifier, which is stable across schemas and resync
  rounds. The episode's ``source_ref`` is derived from
  ``origin_instance_ref`` alone (``calendar:{origin_instance_ref}``)
  so the upsert is idempotent across runs and schemas.
- Missing calendar tables (module not enabled on this deployment)
  degrades gracefully — the adapter emits a warning and exits clean.

Butler-managed calendar exclusion (defence-in-depth):
- Instances whose ``calendar_sources.lane = 'butler'`` are excluded
  from projection.  Butler-internal sources (``source_kind`` of
  ``'internal_scheduler'`` or ``'internal_reminders'``) always use
  ``lane='butler'``.  This prevents scheduled maintenance jobs such as
  ``memory_consolidation``, ``memory_episode_cleanup``, and
  ``memory_purge_superseded`` from polluting the user's Chronicle
  Calendar lane even if the writer-side guard is ever bypassed.
  The exclusion is applied via an inner join against ``calendar_sources``
  in ``_fetch_instances``.

Entity-id resolution (bu-f4755):
- Each calendar episode is tagged with the ``entity_id`` of the Google
  account whose calendar produced the event.  The lookup path is:
    ``{schema}.calendar_sources.metadata->>'account_email'``
    → ``public.google_accounts.entity_id``
- Resolution is done once per schema (not per row) and degrades
  gracefully: if the table is absent, the email is missing, or no
  matching Google account row exists, ``entity_id`` is left ``NULL``
  and a debug-level log is emitted.
- The dedup guard (``seen_origin``) means the **first** schema that
  projects a given ``origin_instance_ref`` determines the episode's
  ``entity_id``.  In practice every schema shares the same owner
  entity, so the winning schema makes no difference.
- To backfill ``entity_id`` on pre-013 episodes, run:
    ``python scripts/backfill_episode_entity_id.py [--dry-run]``
  Or reset the adapter watermark in ``projection_checkpoints`` to
  ``NULL`` and let the next scheduled run re-project all rows.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import asyncpg

from butlers.chronicler.adapters.base import AdapterResult, ProjectionAdapter
from butlers.chronicler.models import Episode, Precision, Privacy
from butlers.chronicler.storage import upsert_episode

logger = logging.getLogger(__name__)

SOURCE_NAME = "google_calendar.completed"
EPISODE_TYPE_SCHEDULED_BLOCK = "scheduled_block"
DEFAULT_BATCH_LIMIT = 500

# Butler-managed source kinds — instances from these sources are never projected
# into the user's Chronicle Calendar lane. The primary guard is the
# ``lane='butler'`` filter on ``calendar_sources``; this constant documents the
# underlying source kinds for clarity and test assertions.
BUTLER_MANAGED_SOURCE_KINDS: frozenset[str] = frozenset(
    {
        "internal_scheduler",
        "internal_reminders",
    }
)


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
        # Provider-level dedup set for this run, keyed on origin_instance_ref.
        # The upstream Google Calendar instance ID is stable across butler
        # schemas, so the same logical event appearing in multiple schemas
        # collapses to a single projection. The persistent upsert key
        # (source_name, source_ref) is also derived from origin_instance_ref,
        # so even without this in-run guard the database would converge to
        # one row per upstream instance — this just avoids redundant writes.
        seen_origin: set[str] = set()
        now = datetime.now(UTC)

        for schema in self.butler_schemas:
            rows = await self._fetch_instances(pool, schema, since, now)
            if rows is None:
                result.warnings.append(
                    f"calendar_event_instances missing for schema {schema!r}; skipping"
                )
                continue

            # Resolve entity_id once per schema (not per row) — all calendar
            # instances in a schema belong to the same Google account owner.
            entity_id = await self._resolve_schema_entity_id(pool, schema)

            for row in rows:
                dedup_key = row["origin_instance_ref"]
                if dedup_key in seen_origin:
                    # Earlier schema already projected this origin instance.
                    continue
                seen_origin.add(dedup_key)

                await self._project_row(chronicler_pool, schema, row, entity_id=entity_id)
                result.rows_projected += 1
                result.episodes_closed += 1

                candidate = row["ends_at"]
                if candidate is not None and (
                    latest_watermark is None or candidate > latest_watermark
                ):
                    latest_watermark = candidate

        result.watermark = latest_watermark
        return result

    async def _resolve_schema_entity_id(
        self,
        pool: asyncpg.Pool,
        schema: str,
    ) -> UUID | None:
        """Resolve the entity_id for the Google account that owns this schema's calendar.

        Lookup path:
          ``{schema}.calendar_sources.metadata->>'account_email'``
          → ``public.google_accounts.entity_id``

        Returns ``None`` and logs at DEBUG level when any step fails:
        - calendar_sources table absent (calendar module not installed)
        - no user-lane source with an ``account_email`` in metadata
        - no ``public.google_accounts`` row matching the email
        - ``public.google_accounts`` table absent
        """
        quoted = self._quote_ident(schema)
        try:
            async with pool.acquire() as conn:
                # Step 1: get account_email from a user-lane calendar source in this schema.
                email_row = await conn.fetchrow(
                    f"""
                    SELECT metadata->>'account_email' AS account_email
                    FROM {quoted}.calendar_sources
                    WHERE lane = 'user'
                      AND metadata->>'account_email' IS NOT NULL
                    LIMIT 1
                    """
                )
                if email_row is None:
                    logger.debug(
                        "CalendarCompletedAdapter: no user-lane source with account_email "
                        "in schema %r — entity_id will be NULL",
                        schema,
                    )
                    return None

                account_email: str = email_row["account_email"]

                # Step 2: look up entity_id from public.google_accounts.
                entity_row = await conn.fetchrow(
                    """
                    SELECT entity_id
                    FROM public.google_accounts
                    WHERE email = $1
                    LIMIT 1
                    """,
                    account_email,
                )
                if entity_row is None:
                    logger.debug(
                        "CalendarCompletedAdapter: google account %r not found in "
                        "public.google_accounts — entity_id will be NULL",
                        account_email,
                    )
                    return None

                raw = entity_row["entity_id"]
                if raw is None:
                    return None
                if isinstance(raw, UUID):
                    return raw
                if isinstance(raw, str):
                    return UUID(raw)
                # Unexpected type (e.g. in tests with mis-configured mocks).
                logger.debug(
                    "CalendarCompletedAdapter: entity_id has unexpected type %r "
                    "for schema %r — entity_id will be NULL",
                    type(raw).__name__,
                    schema,
                )
                return None

        except asyncpg.PostgresError:
            logger.debug(
                "CalendarCompletedAdapter: entity_id resolution failed for schema %r "
                "(table absent or query error) — entity_id will be NULL",
                schema,
                exc_info=True,
            )
            return None

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
                        INNER JOIN {quoted}.calendar_sources AS cs ON cs.id = i.source_id
                        WHERE i.ends_at <= $1
                          AND i.status != 'cancelled'
                          AND cs.lane != 'butler'
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
                        INNER JOIN {quoted}.calendar_sources AS cs ON cs.id = i.source_id
                        WHERE i.ends_at <= $1
                          AND i.ends_at > $2
                          AND i.status != 'cancelled'
                          AND cs.lane != 'butler'
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
        *,
        entity_id: UUID | None = None,
    ) -> Episode:
        instance_id = row["id"]
        # Stable across schemas and resync rounds: the upstream Google
        # Calendar instance identifier is the same regardless of which
        # butler synced the row. This makes the upsert idempotent and
        # collapses the per-schema fan-out into a single chronicler episode.
        source_ref = f"calendar:{row['origin_instance_ref']}"

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
                    entity_id=entity_id,
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


async def resolve_calendar_account_entity_id(
    pool: asyncpg.Pool,
    *,
    account_email: str,
) -> UUID | None:
    """Return the ``public.entities`` UUID for a Google account email address.

    Used by the backfill script to resolve ``entity_id`` for historical
    episodes without touching the adapter's watermark.

    Returns ``None`` when:
    - ``public.google_accounts`` table is absent.
    - No row matches ``email``.
    - The matching row has ``entity_id IS NULL``.
    """
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT entity_id
                FROM public.google_accounts
                WHERE email = $1
                LIMIT 1
                """,
                account_email,
            )
    except asyncpg.PostgresError:
        logger.debug(
            "resolve_calendar_account_entity_id: query failed for %r",
            account_email,
            exc_info=True,
        )
        return None

    if row is None:
        return None
    raw = row["entity_id"]
    if raw is None:
        return None
    return raw if isinstance(raw, UUID) else UUID(str(raw))


__all__ = [
    "BUTLER_MANAGED_SOURCE_KINDS",
    "CalendarCompletedAdapter",
    "EPISODE_TYPE_SCHEDULED_BLOCK",
    "SOURCE_NAME",
    "resolve_calendar_account_entity_id",
]
