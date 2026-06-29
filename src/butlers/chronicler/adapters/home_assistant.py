"""Home Assistant history projection adapter.

Projects state-change rows from ``connectors.home_assistant_history``
into Chronicler ``presence_episode`` rollups.

Semantics:
- The evidence table stores one row per state change event received from
  Home Assistant (via the ``state_changed`` WebSocket event stream).
- Rows for ``person.*`` entities (e.g. ``person.alice``) are treated as
  presence data.  Contiguous runs of the ``home`` state are collapsed into
  ``presence_episode`` spans.
- A presence episode begins when a person entity transitions **into**
  ``home`` state and ends when it transitions **out of** ``home`` state.
  If the last known state is ``home`` (no closing transition in the batch)
  the episode's ``end_at`` is set to the timestamp of the last row seen
  for that entity.
- Cross-batch stitching: if the prior batch ended with an entity still in
  ``home`` state, the open episode's ``source_ref`` is carried over and
  reused in the next batch so that a continuous at-home span spanning a
  batch boundary is recorded as a single episode rather than two.
- Boundary precision is ``exact`` — HA event timestamps carry second
  resolution.
- Privacy class is ``sensitive`` — home/away presence is personally
  identifying retrospective location data.
- Source ref format:
  ``connectors.home_assistant_history:presence:{entity_id}:{start_tst}``
  keyed to (entity, episode-start-ts) so replays are idempotent regardless
  of batch boundary shifts.
- Watermark on ``recorded_at`` only. ``connectors.home_assistant_history.id``
  is a UUID, not an integer serial, so this adapter must not use the integer
  ``watermark_id`` tuple-watermark path.  ``since_id`` is intentionally
  ignored; ``result.watermark_id`` is never set.
- Missing evidence table degrades gracefully (module not enabled /
  migration not run on this deployment).
- No LLM call per event — Tier-0 projection only (RFC 0014 §D5).

Entity-id resolution (bu-v7hen, updated bu-e9xbw):
- Each presence episode is tagged with the ``entity_id`` of the person
  whose presence is being tracked, resolved via:
    ``connectors.home_assistant_persons.ha_entity_id``  (e.g. ``person.alice``)
    → ``connectors.home_assistant_persons.entity_id``   (core_132, direct FK to public.entities)
- Resolution is performed once per adapter run (batch-loaded for all
  distinct person entities present in the batch) — never per row.
- Degrades gracefully to ``entity_id = NULL`` when:
  - ``connectors.home_assistant_persons`` table is absent (migration not run)
  - No mapping exists for the HA person entity
  - The mapped entry has ``entity_id IS NULL`` (not yet linked to the
    memory entity graph, or core_132 migration not yet applied)
- To bootstrap person-entity mappings, see migration core_116 for SQL
  examples (single-person and multi-person households).
- To backfill the resolved person entity on historical presence episodes,
  reset the adapter watermark in ``projection_checkpoints`` to ``NULL``
  and let the next scheduled run re-project all rows.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from butlers.chronicler.adapters._owner_entity import upsert_owner_episode_entity
from butlers.chronicler.adapters.base import AdapterResult, ProjectionAdapter
from butlers.chronicler.models import Episode, Layer, Precision, Privacy
from butlers.chronicler.storage import get_carryover, save_carryover, upsert_episode

logger = logging.getLogger(__name__)

SOURCE_NAME = "home_assistant.history"
EPISODE_TYPE_PRESENCE = "presence_episode"
_EVIDENCE_TABLE = "connectors.home_assistant_history"
DEFAULT_BATCH_LIMIT = 1000

# Entity prefix used to detect presence (person) entities.
_PRESENCE_ENTITY_PREFIX = "person."

# State value that denotes the person is at home.
_STATE_HOME = "home"


async def resolve_ha_person_entity_ids(
    pool: asyncpg.Pool,
    ha_entity_ids: list[str],
) -> dict[str, UUID]:
    """Batch-resolve HA person entity IDs to entity graph UUIDs.

    Queries ``connectors.home_assistant_persons`` for the given HA entity IDs
    (e.g. ``person.alice``) and returns their directly-stored ``entity_id``
    values (core_132 — entity_id column on home_assistant_persons; no longer
    joins through public.contacts).

    Returns a mapping ``ha_entity_id → entity_id`` for entities that have a
    non-NULL entity_id.  Unmapped entities are absent from the returned dict
    (caller should default to ``None``).

    Degrades gracefully to an empty dict when:
    - ``connectors.home_assistant_persons`` table is absent (migration not run)
    - ``entity_id`` column is absent (core_132 migration not run — caught by
      PostgresError handler)
    - All entities are unmapped or have NULL entity_id
    - Any DB error occurs

    Called once per adapter run batch (not per row or per entity).
    """
    if not ha_entity_ids:
        return {}

    try:
        async with pool.acquire() as conn:
            exists = await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'connectors'
                      AND table_name = 'home_assistant_persons'
                )
                """
            )
            if not exists:
                logger.debug(
                    "resolve_ha_person_entity_ids: connectors.home_assistant_persons absent "
                    "(migration core_116 not run) — all presence episodes will have entity_id=NULL"
                )
                return {}

            rows = await conn.fetch(
                """
                SELECT hap.ha_entity_id, hap.entity_id
                FROM connectors.home_assistant_persons AS hap
                WHERE hap.ha_entity_id = ANY($1)
                  AND hap.entity_id IS NOT NULL
                """,
                ha_entity_ids,
            )
    except asyncpg.PostgresError:
        logger.debug(
            "resolve_ha_person_entity_ids: query failed (table absent or DB error) "
            "— all presence episodes will have entity_id=NULL",
            exc_info=True,
        )
        return {}

    result: dict[str, UUID] = {}
    for row in rows:
        ha_id: str = row["ha_entity_id"]
        raw = row["entity_id"]
        if raw is None:
            continue
        if isinstance(raw, UUID):
            result[ha_id] = raw
        elif isinstance(raw, str):
            try:
                result[ha_id] = UUID(raw)
            except ValueError:
                logger.debug(
                    "resolve_ha_person_entity_ids: invalid UUID %r for %r — skipping",
                    raw,
                    ha_id,
                )
    return result


class HomeAssistantHistoryAdapter(ProjectionAdapter):
    """Project ``connectors.home_assistant_history`` rows into Chronicler.

    Rows for ``person.*`` entities are collapsed into ``presence_episode``
    spans: one episode per contiguous run of ``home`` state per entity.

    Cross-batch stitching is applied automatically: if the previous batch
    ended with an entity still at home, the open episode's ``source_ref``
    is reused to extend it rather than starting a new fragmented episode.
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

        rows = await self._fetch_rows(pool, since)
        if rows is None:
            result.skipped = True
            result.skipped_reason = (
                f"{_EVIDENCE_TABLE} not found; Home Assistant evidence surface unavailable"
            )
            return result

        latest_watermark = since

        # Advance watermark from all rows (not just presence entities).
        # watermark_id is NOT set: home_assistant_history.id is a UUID and the
        # checkpoint column stores BIGINT; binding UUID to BIGINT raises asyncpg
        # DataError.  Watermark on recorded_at alone is sufficient for idempotent
        # progress tracking (same as the OwnTracks adapter).
        for row in rows:
            candidate = row["recorded_at"]
            if candidate is not None and (latest_watermark is None or candidate > latest_watermark):
                latest_watermark = candidate

        # Filter to presence entities only.
        presence_rows = [
            row
            for row in rows
            if isinstance(row["entity_id"], str)
            and row["entity_id"].startswith(_PRESENCE_ENTITY_PREFIX)
        ]

        if presence_rows:
            # Collect the unique HA person entity IDs in this batch so we can
            # batch-load their entity graph mappings in a single query.
            batch_ha_ids = list(
                {row["entity_id"] for row in presence_rows if isinstance(row["entity_id"], str)}
            )
            entity_id_map = await resolve_ha_person_entity_ids(pool, batch_ha_ids)

            # Load prior-batch carryover state for cross-batch stitching.
            prior_carryover = await get_carryover(chronicler_pool, self.source_name)

            episodes_closed, new_carryover = await self._project_presence_episodes(
                chronicler_pool, presence_rows, prior_carryover, entity_id_map=entity_id_map
            )
            result.rows_projected = len(presence_rows)
            result.episodes_closed += episodes_closed

            await save_carryover(chronicler_pool, self.source_name, new_carryover)
        else:
            # No presence rows — preserve existing carryover unchanged.
            # (Nothing was advanced; carryover remains valid for next batch.)
            pass

        result.watermark = latest_watermark
        # Leave watermark_id unset: home_assistant_history.id is UUID, while the
        # checkpoint column stores integer tie-breakers for serial-id sources.
        return result

    async def _fetch_rows(
        self,
        pool: asyncpg.Pool,
        since: datetime | None,
    ) -> list[asyncpg.Record] | None:
        """Fetch evidence rows since the watermark.

        ``since_id`` is intentionally not accepted. The evidence table primary
        key is UUID, while ``projection_checkpoints.watermark_id`` is an
        integer field for serial-id sources.  Use single-column
        ``WHERE recorded_at > $1`` so a stale integer checkpoint cannot make
        Postgres compare UUIDs to ints (which raises asyncpg DataError).

        Returns ``None`` if the evidence table is missing — degrade
        gracefully per RFC 0014 optional-schema guard.
        """
        try:
            async with pool.acquire() as conn:
                exists = await conn.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_schema = 'connectors'
                          AND table_name = 'home_assistant_history'
                    )
                    """
                )
                if not exists:
                    return None

                if since is None:
                    rows = await conn.fetch(
                        f"""
                        SELECT id, entity_id, state, attributes, recorded_at
                        FROM {_EVIDENCE_TABLE}
                        ORDER BY recorded_at ASC, id ASC
                        LIMIT $1
                        """,
                        self.batch_limit,
                    )
                else:
                    rows = await conn.fetch(
                        f"""
                        SELECT id, entity_id, state, attributes, recorded_at
                        FROM {_EVIDENCE_TABLE}
                        WHERE recorded_at > $1
                        ORDER BY recorded_at ASC, id ASC
                        LIMIT $2
                        """,
                        since,
                        self.batch_limit,
                    )
        except asyncpg.PostgresError:
            logger.exception("Failed reading %s", _EVIDENCE_TABLE)
            return None

        return list(rows)

    async def _project_presence_episodes(
        self,
        chronicler_pool: asyncpg.Pool,
        rows: list[Any],
        prior_carryover: dict,
        *,
        entity_id_map: dict[str, UUID],
    ) -> tuple[int, dict]:
        """Collapse per-entity presence state changes into presence episodes.

        For each ``person.*`` entity, runs of consecutive ``home`` state
        rows are collapsed into a single ``presence_episode``.  The episode
        spans from the first ``home`` row to the row immediately before the
        next non-``home`` row (or the last row if the entity is still home).

        ``prior_carryover`` maps ``entity_id`` → carryover dict from the
        prior batch.  When an entity's first row in this batch is ``home``
        and there is an open episode in the carryover, the open episode is
        extended rather than starting a new one.

        ``entity_id_map`` maps HA entity IDs (e.g. ``person.alice``) to the
        corresponding entity graph UUID from ``connectors.home_assistant_persons``.
        Episodes for unmapped persons receive ``entity_id=NULL``.  An empty
        dict (the result when the mapping table is absent or all persons are
        unmapped) degrades all episodes to ``entity_id=NULL``.

        Returns ``(episodes_upserted, new_carryover)`` where ``new_carryover``
        captures any entities that are still home at the end of this batch.
        """
        # Group rows by entity_id preserving recorded_at order.
        by_entity: dict[str, list[Any]] = {}
        for row in rows:
            ha_entity_id = row["entity_id"]
            by_entity.setdefault(ha_entity_id, []).append(row)

        episodes_upserted = 0
        new_carryover: dict = {}

        for ha_entity_id, entity_rows in by_entity.items():
            # Resolve the entity graph UUID for this HA person (or None).
            resolved_entity_id = entity_id_map.get(ha_entity_id)
            # entity_rows are already in (recorded_at ASC, id ASC) order.
            entity_carryover = prior_carryover.get(ha_entity_id)
            count, open_ep = await self._rollup_entity_episodes(
                chronicler_pool,
                ha_entity_id,
                entity_rows,
                entity_carryover,
                entity_id=resolved_entity_id,
            )
            episodes_upserted += count
            if open_ep is not None:
                new_carryover[ha_entity_id] = open_ep

        return episodes_upserted, new_carryover

    async def _rollup_entity_episodes(
        self,
        chronicler_pool: asyncpg.Pool,
        ha_entity_id: str,
        entity_rows: list[Any],
        carryover: dict | None,
        *,
        entity_id: UUID | None = None,
    ) -> tuple[int, dict | None]:
        """Rollup presence episodes for a single entity.

        Scans entity_rows and identifies contiguous spans where
        state == ``home``.  Each span becomes one ``presence_episode``.

        If ``carryover`` is provided and the first row is ``home``, the
        open episode from the prior batch is extended using the same
        ``source_ref`` (cross-batch stitching).

        ``entity_id`` is the resolved entity graph UUID for the person whose
        presence is being tracked (from ``connectors.home_assistant_persons``).
        Passed through to ``_upsert_presence_episode`` and stamped on every
        episode row.  ``None`` when no mapping exists.

        Returns ``(episodes_upserted, open_episode_carryover)``.
        ``open_episode_carryover`` is ``None`` when the entity is not at
        home at the end of the batch, or a dict carrying the ``source_ref``
        and timestamps of the still-open episode when it is.
        """
        episodes_upserted = 0

        # Seed span state from carryover (prior open episode) if applicable.
        span_start: datetime | None = None
        span_end: datetime | None = None
        span_source_ref: str | None = None  # set only when continuing a prior episode

        if carryover:
            try:
                span_start_iso = carryover["start_at"]
                span_source_ref = carryover["source_ref"]
                # Parse ISO string back to datetime.
                span_start = datetime.fromisoformat(span_start_iso)
                span_end = datetime.fromisoformat(carryover["end_at"])
            except (KeyError, ValueError):
                # Corrupt carryover — discard and start fresh.
                span_start = None
                span_end = None
                span_source_ref = None
                logger.warning("Discarding malformed carryover for %s: %r", ha_entity_id, carryover)

        for row in entity_rows:
            state = row["state"]
            ts: datetime = row["recorded_at"]

            if state == _STATE_HOME:
                if span_start is None:
                    # New episode starting this batch — compute fresh source_ref.
                    span_start = ts
                    span_source_ref = None
                span_end = ts
            else:
                if span_start is not None and span_end is not None:
                    # Close this span.
                    await self._upsert_presence_episode(
                        chronicler_pool,
                        ha_entity_id,
                        span_start,
                        span_end,
                        span_source_ref,
                        entity_id=entity_id,
                    )
                    episodes_upserted += 1
                    span_start = None
                    span_end = None
                    span_source_ref = None

        # If the entity is still home at the end of the batch, emit the open span
        # and record carryover for the next batch.
        open_episode_carryover: dict | None = None
        if span_start is not None and span_end is not None:
            upserted = await self._upsert_presence_episode(
                chronicler_pool,
                ha_entity_id,
                span_start,
                span_end,
                span_source_ref,
                entity_id=entity_id,
            )
            episodes_upserted += 1
            open_episode_carryover = {
                "source_ref": upserted.source_ref,
                "start_at": upserted.start_at.isoformat(),
                "end_at": upserted.end_at.isoformat(),
            }

        return episodes_upserted, open_episode_carryover

    async def _upsert_presence_episode(
        self,
        chronicler_pool: asyncpg.Pool,
        ha_entity_id: str,
        start_at: datetime,
        end_at: datetime,
        existing_source_ref: str | None = None,
        *,
        entity_id: UUID | None = None,
    ) -> Episode:
        """Upsert a presence episode.

        If ``existing_source_ref`` is provided (cross-batch continuation),
        it is used as-is so that the prior episode row is updated in place.
        Otherwise a new ``source_ref`` is derived from ``(ha_entity_id, start_tst)``.

        ``entity_id`` is the resolved entity graph UUID for the person whose
        presence is being tracked (from ``connectors.home_assistant_persons``).
        When non-None, also writes a row into ``chronicler.episode_entities``
        with ``role='owner'`` (the person whose episode this is).
        """
        if existing_source_ref is not None:
            source_ref = existing_source_ref
        else:
            start_tst = int(start_at.timestamp())
            source_ref = f"{_EVIDENCE_TABLE}:presence:{ha_entity_id}:{start_tst}"

        # Derive a human-readable label from the HA entity ID.
        # e.g. "person.alice" → "Alice at home"
        short_name = ha_entity_id.split(".", 1)[-1].replace("_", " ").title()
        title = f"{short_name} at home"

        payload: dict[str, Any] = {
            "entity_id": ha_entity_id,
            "state": _STATE_HOME,
        }

        async with chronicler_pool.acquire() as conn:
            episode = await upsert_episode(
                conn,
                Episode(
                    source_name=self.source_name,
                    source_ref=source_ref,
                    episode_type=EPISODE_TYPE_PRESENCE,
                    start_at=start_at,
                    end_at=end_at,
                    precision=Precision.EXACT,
                    title=title,
                    payload=payload,
                    privacy=Privacy.SENSITIVE,
                    layer=Layer.ACTIVITY,
                ),
            )
            # Write the person's entity row into episode_entities (bu-v7hen).
            # Uses role='owner' because this episode belongs to the person being tracked.
            # upsert_owner_episode_entity no-ops when entity_id or episode.id is None.
            await upsert_owner_episode_entity(conn, episode.id, owner_id=entity_id)
        return episode


__all__ = [
    "DEFAULT_BATCH_LIMIT",
    "EPISODE_TYPE_PRESENCE",
    "HomeAssistantHistoryAdapter",
    "SOURCE_NAME",
    "resolve_ha_person_entity_ids",
]
