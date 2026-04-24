"""Core sessions projection adapter.

Projects canonical butler/agent session records from each butler schema
into Chronicler lifecycle events and work episodes. Uses cross-schema
read access granted to ``butler_chronicler_rw`` (see scripts/init-db.sql).

Semantics:
- For every session row:
  - emit ``session_started`` point event at ``started_at``.
  - if ``completed_at`` is set: emit ``session_completed`` point event
    at ``completed_at``, AND emit a ``work`` episode spanning
    (started_at, completed_at).
  - if ``completed_at`` is NULL: emit an OPEN work episode with
    ``end_at=NULL``. On a later projection run with a non-NULL
    ``completed_at`` the episode is closed in place.
- Boundary precision is ``exact`` (sessions carry authoritative
  timestamps).
- Privacy defaults to ``normal``; sessions are not treated as
  retrospective-sensitive.
- The source_ref format is ``{schema}.sessions:{session_id}`` so
  replays idempotently update the same row regardless of watermark
  choice.
- This adapter does NOT use TTL diagnostic process logs as source
  truth (per bu-pa4e0.7 acceptance criteria).
"""

from __future__ import annotations

import logging
from datetime import datetime

import asyncpg

from butlers.chronicler.adapters.base import AdapterResult, ProjectionAdapter
from butlers.chronicler.models import (
    Episode,
    LinkRelation,
    PointEvent,
    Precision,
    Privacy,
)
from butlers.chronicler.storage import (
    get_checkpoint_subsource,
    link_event_to_episode,
    upsert_checkpoint_subsource,
    upsert_episode,
    upsert_point_event,
)

logger = logging.getLogger(__name__)

SOURCE_NAME = "core.sessions"
EPISODE_TYPE_WORK = "work"
EVENT_TYPE_SESSION_STARTED = "session_started"
EVENT_TYPE_SESSION_COMPLETED = "session_completed"

# Default batch cap to bound per-run work. Adapters are scheduled
# frequently enough that a small cap is fine.
DEFAULT_BATCH_LIMIT = 500


class CoreSessionsAdapter(ProjectionAdapter):
    """Project ``{schema}.sessions`` rows into Chronicler."""

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
    ) -> AdapterResult:
        """Project sessions from each butler schema using per-schema watermarks.

        Each schema's projection cursor is tracked independently via
        ``(source_name, schema)`` in ``projection_checkpoints``. The ``since``
        argument (global adapter watermark from the base ``run()`` method) is
        used only as a fallback for schemas that have no per-schema checkpoint
        yet (first run after migration).

        ``AdapterResult.watermark`` is set to the minimum per-schema watermark
        after all schemas are projected. This gives the base class's checkpoint
        write a conservative summary value — it does not affect per-schema
        correctness.
        """
        result = AdapterResult(source_name=self.source_name)
        schema_watermarks: list[datetime] = []

        for schema in self.butler_schemas:
            schema_since = await self._get_schema_watermark(chronicler_pool, schema, fallback=since)
            schema_rows, schema_watermark = await self._fetch_sessions(pool, schema, schema_since)
            if schema_rows is None:
                # Schema missing / optional source unavailable — degrade.
                result.warnings.append(f"sessions table missing for schema {schema!r}; skipping")
                continue

            for row in schema_rows:
                projected = await self._project_row(chronicler_pool, schema, row)
                result.point_events += projected["point_events"]
                if projected["opened_episode"]:
                    result.episodes_opened += 1
                if projected["closed_episode"]:
                    result.episodes_closed += 1
                result.rows_projected += 1

            if schema_watermark is not None:
                await upsert_checkpoint_subsource(
                    chronicler_pool,
                    self.source_name,
                    schema,
                    watermark=schema_watermark,
                    success=True,
                    rows_projected=len(schema_rows),
                )
                schema_watermarks.append(schema_watermark)
            elif schema_since is not None:
                # Include the existing watermark for schemas with no new rows
                # to keep the global summary conservative.
                schema_watermarks.append(schema_since)

        # Report the minimum per-schema watermark so the base class writes a
        # conservative global summary. A schema with no new rows contributes
        # its existing per-schema watermark (or None) to this floor.
        if schema_watermarks:
            result.watermark = min(schema_watermarks)
        return result

    async def _get_schema_watermark(
        self,
        chronicler_pool: asyncpg.Pool,
        schema: str,
        *,
        fallback: datetime | None,
    ) -> datetime | None:
        """Return the per-schema watermark, falling back to ``fallback``."""
        cp = await get_checkpoint_subsource(chronicler_pool, self.source_name, schema)
        if cp is not None and cp.watermark is not None:
            return cp.watermark
        return fallback

    async def _fetch_sessions(
        self,
        pool: asyncpg.Pool,
        schema: str,
        since: datetime | None,
    ) -> tuple[list[asyncpg.Record] | None, datetime | None]:
        """Fetch session rows from one butler schema since the watermark.

        Returns ``(None, None)`` if the schema or table is missing —
        degrade gracefully per RFC 0014 optional-schema guard.
        """
        quoted = self._quote_ident(schema)
        try:
            async with pool.acquire() as conn:
                # Verify table exists before selecting; otherwise
                # degrade cleanly.
                exists = await conn.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_schema = $1 AND table_name = 'sessions'
                    )
                    """,
                    schema,
                )
                if not exists:
                    return None, None

                if since is None:
                    rows = await conn.fetch(
                        f"""
                        SELECT id, started_at, completed_at, trigger_source,
                               success, request_id, duration_ms, model
                        FROM {quoted}.sessions
                        ORDER BY started_at ASC
                        LIMIT $1
                        """,
                        self.batch_limit,
                    )
                else:
                    # Use (started_at OR completed_at) > since to re-pick
                    # up sessions that closed after last watermark.
                    rows = await conn.fetch(
                        f"""
                        SELECT id, started_at, completed_at, trigger_source,
                               success, request_id, duration_ms, model
                        FROM {quoted}.sessions
                        WHERE started_at > $1
                           OR (completed_at IS NOT NULL AND completed_at > $1)
                        ORDER BY started_at ASC
                        LIMIT $2
                        """,
                        since,
                        self.batch_limit,
                    )
        except asyncpg.PostgresError:
            logger.exception("Failed reading sessions for schema %s", schema)
            return None, None

        # Advance the watermark only by started_at so that batched runs
        # ordered by started_at do not skip sessions whose started_at falls
        # between the last-fetched started_at and a later completed_at.
        # The query already re-fetches rows with completed_at > since, so
        # open sessions that close after the watermark are picked up on the
        # next run regardless of this choice.
        watermark: datetime | None = None
        for r in rows:
            candidate = r["started_at"]
            if candidate is not None and (watermark is None or candidate > watermark):
                watermark = candidate
        return list(rows), watermark

    async def _project_row(
        self,
        chronicler_pool: asyncpg.Pool,
        schema: str,
        row: asyncpg.Record,
    ) -> dict[str, int | bool]:
        """Upsert the point events + work episode for one session row."""
        session_id = row["id"]
        started_at = row["started_at"]
        completed_at = row["completed_at"]
        success = row["success"]
        trigger_source = row["trigger_source"]
        duration_ms = row["duration_ms"]

        source_ref_base = f"{schema}.sessions:{session_id}"
        started_ref = f"{source_ref_base}#started"
        completed_ref = f"{source_ref_base}#completed"
        episode_ref = source_ref_base

        payload_common = {
            "schema": schema,
            "session_id": str(session_id),
            "trigger_source": trigger_source,
            "model": row["model"],
            "success": success,
        }

        async with chronicler_pool.acquire() as conn:
            async with conn.transaction():
                started_event = await upsert_point_event(
                    conn,
                    PointEvent(
                        source_name=self.source_name,
                        source_ref=started_ref,
                        event_type=EVENT_TYPE_SESSION_STARTED,
                        occurred_at=started_at,
                        precision=Precision.EXACT,
                        title=f"{schema}: session started",
                        payload={**payload_common, "boundary": "start"},
                        privacy=Privacy.NORMAL,
                    ),
                )

                completed_event = None
                if completed_at is not None:
                    completed_event = await upsert_point_event(
                        conn,
                        PointEvent(
                            source_name=self.source_name,
                            source_ref=completed_ref,
                            event_type=EVENT_TYPE_SESSION_COMPLETED,
                            occurred_at=completed_at,
                            precision=Precision.EXACT,
                            title=f"{schema}: session completed",
                            payload={
                                **payload_common,
                                "boundary": "end",
                                "duration_ms": duration_ms,
                            },
                            privacy=Privacy.NORMAL,
                        ),
                    )

                episode = await upsert_episode(
                    conn,
                    Episode(
                        source_name=self.source_name,
                        source_ref=episode_ref,
                        episode_type=EPISODE_TYPE_WORK,
                        start_at=started_at,
                        end_at=completed_at,
                        precision=Precision.EXACT,
                        title=f"{schema} session",
                        payload=payload_common,
                        privacy=Privacy.NORMAL,
                    ),
                )

                # Link boundary events.
                assert started_event.id is not None
                assert episode.id is not None
                await link_event_to_episode(
                    conn,
                    episode_id=episode.id,
                    event_id=started_event.id,
                    relation=LinkRelation.BOUNDARY_START,
                )
                if completed_event is not None and completed_event.id is not None:
                    await link_event_to_episode(
                        conn,
                        episode_id=episode.id,
                        event_id=completed_event.id,
                        relation=LinkRelation.BOUNDARY_END,
                    )

        return {
            "point_events": 2 if completed_at is not None else 1,
            "opened_episode": completed_at is None,
            "closed_episode": completed_at is not None,
        }

    @staticmethod
    def _quote_ident(name: str) -> str:
        if not name.replace("_", "").isalnum():
            raise ValueError(f"Unsafe schema identifier: {name!r}")
        return '"' + name.replace('"', '""') + '"'


__all__ = [
    "CoreSessionsAdapter",
    "EPISODE_TYPE_WORK",
    "EVENT_TYPE_SESSION_COMPLETED",
    "EVENT_TYPE_SESSION_STARTED",
    "SOURCE_NAME",
]
