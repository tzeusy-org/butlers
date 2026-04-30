"""Spotify session-summary projection adapter.

Projects listening sessions from ``connectors.spotify_listening_sessions``
into Chronicler ``listening_episode`` episodes.

Semantics:
- Each row in ``connectors.spotify_listening_sessions`` maps to exactly
  one ``listening_episode`` with the session's (started_at, ended_at)
  as boundaries.
- Boundary precision is ``exact`` — the connector derives timestamps
  from Spotify API responses, which carry millisecond resolution.
- Privacy class is ``normal`` — track names and session duration are not
  personally sensitive.  The blanket ``sensitive`` default caused the Music
  lane to render as opaque placeholders on the dashboard (bu-6c5i6).
  Per-row sensitive overrides remain possible via the Chronicler override
  mechanism if individual sessions need to be masked.
- Source ref format: ``connectors.spotify_listening_sessions:{idempotency_key}``
  matching the evidence table's unique key so replays are idempotent.
- Deferred fine-grained track projection: per-track events are OUT OF
  SCOPE per bu-pa4e0.10.  Only session-summary → listening episode.
- Missing evidence table degrades gracefully (module not enabled /
  migration not run on this deployment).
- No LLM call per event — Tier-0 projection only (RFC 0014 §D5).
"""

from __future__ import annotations

import logging
from datetime import datetime

import asyncpg

from butlers.chronicler.adapters.base import AdapterResult, ProjectionAdapter
from butlers.chronicler.models import Episode, Precision, Privacy
from butlers.chronicler.storage import upsert_episode

logger = logging.getLogger(__name__)

SOURCE_NAME = "spotify.session_summary"
EPISODE_TYPE_LISTENING = "listening_episode"
_EVIDENCE_TABLE = "connectors.spotify_listening_sessions"
DEFAULT_BATCH_LIMIT = 500


class SpotifySessionAdapter(ProjectionAdapter):
    """Project ``connectors.spotify_listening_sessions`` rows into Chronicler.

    One row in the evidence table → one ``listening_episode`` in Chronicler.
    Fine-grained per-track events are deferred (bu-pa4e0.10).
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

        rows = await self._fetch_sessions(pool, since, since_id)
        if rows is None:
            result.skipped = True
            result.skipped_reason = (
                f"{_EVIDENCE_TABLE} not found; Spotify evidence surface unavailable"
            )
            return result

        latest_watermark = since
        latest_watermark_id: int | None = since_id
        for row in rows:
            await self._project_row(chronicler_pool, row)
            result.rows_projected += 1
            result.episodes_closed += 1

            candidate = row["started_at"]
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

        result.watermark = latest_watermark
        result.watermark_id = latest_watermark_id
        return result

    async def _fetch_sessions(
        self,
        pool: asyncpg.Pool,
        since: datetime | None,
        since_id: int | None = None,
    ) -> list[asyncpg.Record] | None:
        """Fetch evidence rows since the watermark.

        When ``since`` and ``since_id`` are both provided, uses the tuple
        comparison ``WHERE (started_at, id) > ($1, $2)`` so rows sharing
        the same timestamp as the previous batch boundary are not missed.
        When only ``since`` is provided (pre-migration checkpoint), falls
        back to the single-column ``WHERE started_at > $1`` form.

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
                          AND table_name = 'spotify_listening_sessions'
                    )
                    """
                )
                if not exists:
                    return None

                if since is None:
                    rows = await conn.fetch(
                        f"""
                        SELECT id, idempotency_key, endpoint_identity,
                               spotify_user_id, started_at, ended_at,
                               duration_seconds, track_count, track_names,
                               context_uri, context_name, recorded_at
                        FROM {_EVIDENCE_TABLE}
                        ORDER BY started_at ASC, id ASC
                        LIMIT $1
                        """,
                        self.batch_limit,
                    )
                elif since_id is not None:
                    rows = await conn.fetch(
                        f"""
                        SELECT id, idempotency_key, endpoint_identity,
                               spotify_user_id, started_at, ended_at,
                               duration_seconds, track_count, track_names,
                               context_uri, context_name, recorded_at
                        FROM {_EVIDENCE_TABLE}
                        WHERE (started_at, id) > ($1, $2)
                        ORDER BY started_at ASC, id ASC
                        LIMIT $3
                        """,
                        since,
                        since_id,
                        self.batch_limit,
                    )
                else:
                    rows = await conn.fetch(
                        f"""
                        SELECT id, idempotency_key, endpoint_identity,
                               spotify_user_id, started_at, ended_at,
                               duration_seconds, track_count, track_names,
                               context_uri, context_name, recorded_at
                        FROM {_EVIDENCE_TABLE}
                        WHERE started_at > $1
                        ORDER BY started_at ASC, id ASC
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
    ) -> Episode:
        idempotency_key = row["idempotency_key"]
        source_ref = f"{_EVIDENCE_TABLE}:{idempotency_key}"

        context_name = row["context_name"]
        context_uri = row["context_uri"]
        endpoint_identity = row["endpoint_identity"]

        if context_name:
            title = f"Listened to {context_name}"
        elif context_uri:
            title = f"Listened to {context_uri.split(':')[-1]}"
        else:
            title = f"Spotify session ({endpoint_identity})"

        payload = {
            "idempotency_key": idempotency_key,
            "endpoint_identity": endpoint_identity,
            "spotify_user_id": row["spotify_user_id"],
            "track_count": row["track_count"],
            "duration_seconds": row["duration_seconds"],
            "context_uri": context_uri,
            "context_name": context_name,
        }

        async with chronicler_pool.acquire() as conn:
            episode = await upsert_episode(
                conn,
                Episode(
                    source_name=self.source_name,
                    source_ref=source_ref,
                    episode_type=EPISODE_TYPE_LISTENING,
                    start_at=row["started_at"],
                    end_at=row["ended_at"],
                    precision=Precision.EXACT,
                    title=title,
                    payload=payload,
                    privacy=Privacy.NORMAL,
                ),
            )
        return episode


__all__ = [
    "DEFAULT_BATCH_LIMIT",
    "EPISODE_TYPE_LISTENING",
    "SOURCE_NAME",
    "SpotifySessionAdapter",
]
