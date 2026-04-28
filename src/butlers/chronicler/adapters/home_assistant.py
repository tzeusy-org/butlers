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
- Boundary precision is ``exact`` — HA event timestamps carry second
  resolution.
- Privacy class is ``sensitive`` — home/away presence is personally
  identifying retrospective location data.
- Source ref format:
  ``connectors.home_assistant_history:presence:{entity_id}:{start_tst}``
  keyed to (entity, episode-start-ts) so replays are idempotent regardless
  of batch boundary shifts.
- Watermark on ``recorded_at`` (monotonically written by the connector).
- Missing evidence table degrades gracefully (module not enabled /
  migration not run on this deployment).
- No LLM call per event — Tier-0 projection only (RFC 0014 §D5).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import asyncpg

from butlers.chronicler.adapters.base import AdapterResult, ProjectionAdapter
from butlers.chronicler.models import Episode, Precision, Privacy
from butlers.chronicler.storage import upsert_episode

logger = logging.getLogger(__name__)

SOURCE_NAME = "home_assistant.history"
EPISODE_TYPE_PRESENCE = "presence_episode"
_EVIDENCE_TABLE = "connectors.home_assistant_history"
DEFAULT_BATCH_LIMIT = 1000

# Entity prefix used to detect presence (person) entities.
_PRESENCE_ENTITY_PREFIX = "person."

# State value that denotes the person is at home.
_STATE_HOME = "home"


class HomeAssistantHistoryAdapter(ProjectionAdapter):
    """Project ``connectors.home_assistant_history`` rows into Chronicler.

    Rows for ``person.*`` entities are collapsed into ``presence_episode``
    spans: one episode per contiguous run of ``home`` state per entity.
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

        rows = await self._fetch_rows(pool, since, since_id)
        if rows is None:
            result.skipped = True
            result.skipped_reason = (
                f"{_EVIDENCE_TABLE} not found; Home Assistant evidence surface unavailable"
            )
            return result

        latest_watermark = since
        latest_watermark_id: int | None = since_id

        # Advance watermark from all rows (not just presence entities).
        for row in rows:
            candidate = row["recorded_at"]
            if candidate is not None:
                if latest_watermark is None or candidate > latest_watermark:
                    latest_watermark = candidate
                    latest_watermark_id = row["id"]
                elif candidate == latest_watermark:
                    row_id = row["id"]
                    if row_id is not None and (
                        latest_watermark_id is None or row_id > latest_watermark_id
                    ):
                        latest_watermark_id = row_id

        # Filter to presence entities only.
        presence_rows = [
            row
            for row in rows
            if isinstance(row["entity_id"], str)
            and row["entity_id"].startswith(_PRESENCE_ENTITY_PREFIX)
        ]

        if presence_rows:
            episodes_closed = await self._project_presence_episodes(chronicler_pool, presence_rows)
            result.rows_projected = len(presence_rows)
            result.episodes_closed += episodes_closed

        result.watermark = latest_watermark
        result.watermark_id = latest_watermark_id
        return result

    async def _fetch_rows(
        self,
        pool: asyncpg.Pool,
        since: datetime | None,
        since_id: int | None = None,
    ) -> list[asyncpg.Record] | None:
        """Fetch evidence rows since the watermark.

        When ``since`` and ``since_id`` are both provided, uses the tuple
        comparison ``WHERE (recorded_at, id) > ($1, $2)`` so rows sharing
        the same timestamp as the previous batch boundary are not missed.
        When only ``since`` is provided (pre-migration checkpoint), falls
        back to the single-column ``WHERE recorded_at > $1`` form.

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
                elif since_id is not None:
                    rows = await conn.fetch(
                        f"""
                        SELECT id, entity_id, state, attributes, recorded_at
                        FROM {_EVIDENCE_TABLE}
                        WHERE (recorded_at, id) > ($1, $2)
                        ORDER BY recorded_at ASC, id ASC
                        LIMIT $3
                        """,
                        since,
                        since_id,
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
    ) -> int:
        """Collapse per-entity presence state changes into presence episodes.

        For each ``person.*`` entity, runs of consecutive ``home`` state
        rows are collapsed into a single ``presence_episode``.  The episode
        spans from the first ``home`` row to the row immediately before the
        next non-``home`` row (or the last row if the entity is still home).

        Returns the number of episodes upserted.
        """
        # Group rows by entity_id preserving recorded_at order.
        by_entity: dict[str, list[Any]] = {}
        for row in rows:
            entity_id = row["entity_id"]
            by_entity.setdefault(entity_id, []).append(row)

        episodes_upserted = 0
        for entity_id, entity_rows in by_entity.items():
            # entity_rows are already in (recorded_at ASC, id ASC) order.
            episodes_upserted += await self._rollup_entity_episodes(
                chronicler_pool, entity_id, entity_rows
            )

        return episodes_upserted

    async def _rollup_entity_episodes(
        self,
        chronicler_pool: asyncpg.Pool,
        entity_id: str,
        entity_rows: list[Any],
    ) -> int:
        """Rollup presence episodes for a single entity.

        Scans entity_rows and identifies contiguous spans where
        state == ``home``.  Each span becomes one ``presence_episode``.

        Returns the number of episodes upserted.
        """
        episodes_upserted = 0

        # Walk the rows and identify home-spans.
        span_start: datetime | None = None
        span_end: datetime | None = None

        for row in entity_rows:
            state = row["state"]
            ts: datetime = row["recorded_at"]

            if state == _STATE_HOME:
                if span_start is None:
                    span_start = ts
                span_end = ts
            else:
                if span_start is not None and span_end is not None:
                    # Close this span.
                    await self._upsert_presence_episode(
                        chronicler_pool, entity_id, span_start, span_end
                    )
                    episodes_upserted += 1
                    span_start = None
                    span_end = None

        # If the entity is still home at the end of the batch, emit the open span.
        if span_start is not None and span_end is not None:
            await self._upsert_presence_episode(chronicler_pool, entity_id, span_start, span_end)
            episodes_upserted += 1

        return episodes_upserted

    async def _upsert_presence_episode(
        self,
        chronicler_pool: asyncpg.Pool,
        entity_id: str,
        start_at: datetime,
        end_at: datetime,
    ) -> Episode:
        start_tst = int(start_at.timestamp())
        source_ref = f"{_EVIDENCE_TABLE}:presence:{entity_id}:{start_tst}"

        # Derive a human-readable label from the entity_id.
        # e.g. "person.alice" → "Alice at home"
        short_name = entity_id.split(".", 1)[-1].replace("_", " ").title()
        title = f"{short_name} at home"

        payload: dict[str, Any] = {
            "entity_id": entity_id,
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
                ),
            )
        return episode


__all__ = [
    "DEFAULT_BATCH_LIMIT",
    "EPISODE_TYPE_PRESENCE",
    "HomeAssistantHistoryAdapter",
    "SOURCE_NAME",
]
