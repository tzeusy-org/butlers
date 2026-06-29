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

Title-resolution rules (bu-fkqv0):
  trigger_source='route' AND contact resolved  → 'Conversation with {display_name}'
  trigger_source='route' AND contact unresolved → 'Conversation via {channel}'
  trigger_source IN ('trigger','external','dashboard') → '{schema}: manual task'
  All other / NULL trigger_source              → '{schema} session'  (legacy fallback)
"""

from __future__ import annotations

import logging
from datetime import datetime
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
from butlers.chronicler.models import (
    Episode,
    Layer,
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

# Trigger sources that represent operational telemetry rather than user
# activity.  Rows with these values are excluded at the SQL layer so the
# per-schema watermark advances only over user-visible work.
#
# Exact matches: 'tick', 'qa', 'healing'
# Prefix match:  'schedule:*'  (scheduler-fired background jobs)
#
# Rationale: heartbeat ticks, QA probes, healing sessions, and
# scheduler-fired background jobs dominate raw session counts but carry no
# "lived past time" signal for the Chronicler's mission.
EXCLUDED_TRIGGER_SOURCES: frozenset[str] = frozenset({"tick", "qa", "healing"})
EXCLUDED_TRIGGER_SOURCE_PREFIX: str = "schedule:"


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
        since_id: int | None = None,
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

        # Resolve owner entity_id once per adapter run (not per schema or row).
        entity_id = await resolve_owner_entity_id(pool)

        for schema in self.butler_schemas:
            schema_since = await self._get_schema_watermark(chronicler_pool, schema, fallback=since)
            schema_rows, schema_watermark = await self._fetch_sessions(pool, schema, schema_since)
            if schema_rows is None:
                # Schema missing / optional source unavailable — degrade.
                result.warnings.append(f"sessions table missing for schema {schema!r}; skipping")
                continue

            # Resolve contact display names for route-triggered sessions
            # (those with an ingestion_event_id linking back to a channel sender).
            # The result is a mapping from session_id → (display_name | None, channel | None).
            contact_map = await self._resolve_contacts(pool, schema_rows)

            for row in schema_rows:
                contact_info = contact_map.get(row["id"], (None, None))
                projected = await self._project_row(
                    chronicler_pool,
                    schema,
                    row,
                    contact_info=contact_info,
                    entity_id=entity_id,
                )
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

                # Build the exclusion parameters.  We pass the exact-match
                # set as a PostgreSQL array ($N) and the prefix as a LIKE
                # pattern ($N+1) so the filter lives entirely at the SQL
                # layer and the watermark advances only over included rows.
                excluded_exact = list(EXCLUDED_TRIGGER_SOURCES)
                excluded_prefix_pattern = EXCLUDED_TRIGGER_SOURCE_PREFIX + "%"

                if since is None:
                    rows = await conn.fetch(
                        f"""
                        SELECT id, started_at, completed_at, trigger_source,
                               success, request_id, ingestion_event_id,
                               duration_ms, model
                        FROM {quoted}.sessions
                        WHERE (trigger_source IS NULL
                               OR (trigger_source != ALL($2::text[])
                                   AND trigger_source NOT LIKE $3))
                        ORDER BY started_at ASC
                        LIMIT $1
                        """,
                        self.batch_limit,
                        excluded_exact,
                        excluded_prefix_pattern,
                    )
                else:
                    # Use (started_at OR completed_at) > since to re-pick
                    # up sessions that closed after last watermark.
                    rows = await conn.fetch(
                        f"""
                        SELECT id, started_at, completed_at, trigger_source,
                               success, request_id, ingestion_event_id,
                               duration_ms, model
                        FROM {quoted}.sessions
                        WHERE (started_at > $1
                               OR (completed_at IS NOT NULL AND completed_at > $1))
                          AND (trigger_source IS NULL
                               OR (trigger_source != ALL($3::text[])
                                   AND trigger_source NOT LIKE $4))
                        ORDER BY started_at ASC
                        LIMIT $2
                        """,
                        since,
                        self.batch_limit,
                        excluded_exact,
                        excluded_prefix_pattern,
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

    async def _resolve_contacts(
        self,
        pool: asyncpg.Pool,
        rows: list[asyncpg.Record],
    ) -> dict[int, tuple[str | None, str | None]]:
        """Resolve contact display names for route-triggered sessions.

        For sessions with ``trigger_source='route'`` and a non-NULL
        ``ingestion_event_id``, joins ``public.ingestion_events`` →
        ``relationship.entity_facts`` → ``public.entities`` to fetch the
        sender's display name and channel.  The JOIN uses a SQL CASE expression
        to map ``source_channel`` to the correct ``has-*`` predicate (bu-hjo3i).

        Returns a mapping ``{session_id: (display_name, channel)}``.
        Sessions that cannot be resolved get ``(None, channel)`` when the
        channel is known but the entity is not registered, or ``(None, None)``
        when the ingestion event is absent.

        The JOIN is guarded against missing tables (``public.ingestion_events``
        or ``relationship.entity_facts`` / ``public.entities``) by catching
        ``asyncpg.PostgresError`` and degrading to an empty dict.
        """
        # Collect ingestion_event_ids for route-triggered rows only.
        event_ids: list[UUID] = []
        session_to_event: dict[int, UUID] = {}
        for row in rows:
            if row["trigger_source"] != "route":
                continue
            raw_eid = row["ingestion_event_id"]
            if raw_eid is None:
                continue
            eid = raw_eid if isinstance(raw_eid, UUID) else UUID(str(raw_eid))
            event_ids.append(eid)
            session_to_event[row["id"]] = eid

        if not event_ids:
            return {}

        try:
            async with pool.acquire() as conn:
                contact_rows = await conn.fetch(
                    """
                    SELECT ie.id                     AS event_id,
                           ie.source_channel         AS channel,
                           e.canonical_name          AS display_name
                    FROM   public.ingestion_events ie
                    LEFT JOIN relationship.entity_facts ef
                           ON ef.predicate = CASE ie.source_channel
                                  WHEN 'email'                THEN 'has-email'
                                  WHEN 'phone'                THEN 'has-phone'
                                  WHEN 'telegram'             THEN 'has-handle'
                                  WHEN 'telegram_user_id'     THEN 'has-handle'
                                  WHEN 'telegram_user_client' THEN 'has-handle'
                                  WHEN 'telegram_username'    THEN 'has-handle'
                                  WHEN 'whatsapp_jid'         THEN 'has-handle'
                                  ELSE 'has-handle'
                              END
                          AND (
                              ef.object = ie.source_sender_identity
                              OR (
                                  ie.source_channel = 'telegram_user_client'
                                  AND ie.source_sender_identity NOT LIKE 'telegram:%'
                                  AND ef.object = 'telegram:' || ie.source_sender_identity
                              )
                          )
                          AND ef.object_kind  = 'literal'
                          AND ef.validity     = 'active'
                    LEFT JOIN public.entities e ON e.id = ef.subject
                    WHERE  ie.id = ANY($1::uuid[])
                    """,
                    event_ids,
                )
        except asyncpg.PostgresError:
            logger.debug(
                "CoreSessionsAdapter: contact resolution query failed; "
                "falling back to unresolved titles",
                exc_info=True,
            )
            return {}

        # Build event_id → (display_name, channel) map.
        event_map: dict[UUID, tuple[str | None, str | None]] = {}
        for cr in contact_rows:
            eid = cr["event_id"]
            if not isinstance(eid, UUID):
                eid = UUID(str(eid))
            event_map[eid] = (cr["display_name"], cr["channel"])

        # Map session_id → (display_name, channel).
        result: dict[int, tuple[str | None, str | None]] = {}
        for sid, eid in session_to_event.items():
            result[sid] = event_map.get(eid, (None, None))
        return result

    @staticmethod
    def _compute_episode_title(
        schema: str,
        trigger_source: str | None,
        contact_info: tuple[str | None, str | None],
    ) -> str:
        """Derive a human-readable episode title from session metadata.

        Resolution rules (bu-fkqv0):
        1. trigger_source='route' AND display_name resolved
               → 'Conversation with {display_name}'
        2. trigger_source='route' AND display_name unresolved, channel known
               → 'Conversation via {channel}'
        3. trigger_source='route' AND display_name unresolved, channel unknown
               → 'Conversation via unknown channel'
        4. trigger_source in ('trigger', 'external', 'dashboard')
               → '{schema}: manual task'
        5. Fallback (NULL or unrecognised trigger_source)
               → '{schema} session'
        """
        display_name, channel = contact_info
        if trigger_source == "route":
            if display_name:
                return f"Conversation with {display_name}"
            channel_label = channel or "unknown channel"
            return f"Conversation via {channel_label}"
        if trigger_source in ("trigger", "external", "dashboard"):
            return f"{schema}: manual task"
        return f"{schema} session"

    async def _project_row(
        self,
        chronicler_pool: asyncpg.Pool,
        schema: str,
        row: asyncpg.Record,
        *,
        contact_info: tuple[str | None, str | None] = (None, None),
        entity_id: UUID | None = None,
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
                        layer=Layer.EVIDENCE,
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
                            layer=Layer.EVIDENCE,
                        ),
                    )

                # Confidence: the session start/end boundary markers are a
                # single strong canonical signal (one explicit session) → the
                # work episode earns ``medium``. The two boundary events are not
                # independent kinds — they bracket the same session.
                confidence = derive_confidence([EvidenceKind(name="session_marker", strong=True)])

                # Evidence chain: the boundary point events that bracket this
                # work episode. Populated here (in addition to the canonical
                # ``episode_event_links`` written below) so the activity row
                # carries its corroborating signal ids without a join.
                evidence_event_ids = [started_event.id]
                if completed_event is not None and completed_event.id is not None:
                    evidence_event_ids.append(completed_event.id)
                evidence_refs = evidence_refs_from_event_ids(
                    eid for eid in evidence_event_ids if eid is not None
                )

                episode_title = self._compute_episode_title(schema, trigger_source, contact_info)
                episode = await upsert_episode(
                    conn,
                    Episode(
                        source_name=self.source_name,
                        source_ref=episode_ref,
                        episode_type=EPISODE_TYPE_WORK,
                        start_at=started_at,
                        end_at=completed_at,
                        precision=Precision.EXACT,
                        title=episode_title,
                        payload=payload_common,
                        privacy=Privacy.NORMAL,
                        layer=Layer.ACTIVITY,
                        confidence=confidence,
                        evidence_refs=evidence_refs,
                    ),
                )

                # Write owner row into episode_entities join table (bu-4c1ks).
                await upsert_owner_episode_entity(conn, episode.id, owner_id=entity_id)

                # Link boundary events.
                assert started_event.id is not None
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
    "EXCLUDED_TRIGGER_SOURCE_PREFIX",
    "EXCLUDED_TRIGGER_SOURCES",
    "SOURCE_NAME",
]
