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
- Watermark on ``started_at`` only. ``connectors.spotify_listening_sessions.id``
  is UUID-backed, so this adapter does not use the integer ``watermark_id``
  tuple path.
- Deferred fine-grained track projection: per-track events are OUT OF
  SCOPE per bu-pa4e0.10.  Only session-summary → listening episode.
- Missing evidence table degrades gracefully (module not enabled /
  migration not run on this deployment).
- No LLM call per event — Tier-0 projection only (RFC 0014 §D5).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from uuid import UUID

import asyncpg

from butlers.chronicler.adapters._owner_entity import (
    resolve_owner_entity_id,
    upsert_owner_episode_entity,
)
from butlers.chronicler.adapters.base import AdapterResult, ProjectionAdapter
from butlers.chronicler.confidence import EvidenceKind, derive_confidence
from butlers.chronicler.models import Episode, Layer, Precision, Privacy
from butlers.chronicler.storage import upsert_episode

logger = logging.getLogger(__name__)

# Maximum number of track names to enumerate inline in the episode title
# before switching to a "+N more" suffix. Keeps Gantt tooltips compact.
_TITLE_MAX_INLINE_TRACKS = 2

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

        # Resolve owner entity_id once per adapter run (not per row).
        entity_id = await resolve_owner_entity_id(pool)

        latest_watermark = since
        for row in rows:
            await self._project_row(chronicler_pool, row, entity_id=entity_id)
            result.rows_projected += 1
            # The adapter cannot tell from the schema alone whether
            # ``ended_at`` is a final drain timestamp or a live in-progress
            # cursor; both upsert the same chronicler episode and the
            # ``end_at`` extends on every connector update.
            result.episodes_closed += 1

            candidate = row["recorded_at"]
            if candidate is not None:
                if latest_watermark is None or candidate > latest_watermark:
                    latest_watermark = candidate

        result.watermark = latest_watermark
        # ``projection_checkpoints.watermark_id`` is BIGINT, but the Spotify
        # evidence table primary key is UUID. Keep timestamp-only semantics for
        # this adapter, matching other UUID-backed sources.
        result.watermark_id = None
        return result

    async def _fetch_sessions(
        self,
        pool: asyncpg.Pool,
        since: datetime | None,
        since_id: int | None = None,
    ) -> list[asyncpg.Record] | None:
        """Fetch evidence rows since the watermark.

        Watermarks on ``recorded_at`` so in-progress sessions that the
        connector upserts on every active poll are re-projected and their
        ``end_at`` extends in ``chronicler.episodes`` via the idempotent
        ``upsert_episode``. The evidence table's ``id`` column is a UUID,
        while the shared checkpoint ``watermark_id`` column is BIGINT for
        integer-backed sources, so this adapter keeps timestamp-only
        watermarks.

        Returns ``None`` if the evidence table is missing — degrade
        gracefully per RFC 0014 optional-schema guard.
        """
        del since_id
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
                        ORDER BY recorded_at ASC, id ASC
                        LIMIT $1
                        """,
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

    async def _project_row(
        self,
        chronicler_pool: asyncpg.Pool,
        row: asyncpg.Record,
        *,
        entity_id: UUID | None = None,
    ) -> Episode:
        idempotency_key = row["idempotency_key"]
        source_ref = f"{_EVIDENCE_TABLE}:{idempotency_key}"

        context_name = row["context_name"]
        context_uri = row["context_uri"]
        endpoint_identity = row["endpoint_identity"]
        track_names_raw = row["track_names"]
        track_names = _coerce_track_names(track_names_raw)

        title = _compose_session_title(
            context_name=context_name,
            context_uri=context_uri,
            track_names=track_names,
            endpoint_identity=endpoint_identity,
        )

        payload = {
            "idempotency_key": idempotency_key,
            "endpoint_identity": endpoint_identity,
            "spotify_user_id": row["spotify_user_id"],
            "track_count": row["track_count"],
            "duration_seconds": row["duration_seconds"],
            "context_uri": context_uri,
            "context_name": context_name,
            "track_names": track_names,
        }

        # Confidence: a Spotify listening session is a single strong canonical
        # signal — the connector materializes an explicit, API-derived session
        # with real start/end boundaries — so it earns ``medium``.
        confidence = derive_confidence([EvidenceKind(name="listening_session", strong=True)])

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
                    layer=Layer.ACTIVITY,
                    confidence=confidence,
                ),
            )
            # Write owner row into episode_entities join table (bu-4c1ks).
            await upsert_owner_episode_entity(conn, episode.id, owner_id=entity_id)
        return episode


def _coerce_track_names(raw: object) -> list[str]:
    """Best-effort decode of the JSONB ``track_names`` column to ``list[str]``.

    asyncpg may surface JSONB as either a Python list (when its codec is
    registered) or a JSON-encoded string (default). Handle both, and drop
    any non-string entries defensively.
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except (TypeError, ValueError):
            return []
    else:
        decoded = raw
    if not isinstance(decoded, list):
        return []
    return [t for t in decoded if isinstance(t, str) and t]


def _compose_session_title(
    *,
    context_name: str | None,
    context_uri: str | None,
    track_names: list[str],
    endpoint_identity: str,
) -> str:
    """Derive a human-readable Spotify episode title.

    Resolution order:
      1. ``Listened to {context_name}`` — playlist/album/show name.
      2. ``Listened to {context_uri-tail}`` — bare context URI when no name.
      3. ``Listened to {Track1, Track2}`` — when no context but track names
         exist; longer track lists collapse to ``Track1, Track2 (+N more)``.
      4. ``Spotify session ({endpoint_identity})`` — last-resort fallback
         (no context, no tracks). The endpoint identity at least disambiguates
         which Spotify account was active.
    """
    if context_name:
        return f"Listened to {context_name}"
    if context_uri:
        return f"Listened to {context_uri.split(':')[-1]}"
    if track_names:
        head = track_names[:_TITLE_MAX_INLINE_TRACKS]
        joined = ", ".join(head)
        remaining = len(track_names) - len(head)
        if remaining > 0:
            return f"Listened to {joined} (+{remaining} more)"
        return f"Listened to {joined}"
    return f"Spotify session ({endpoint_identity})"


__all__ = [
    "DEFAULT_BATCH_LIMIT",
    "EPISODE_TYPE_LISTENING",
    "SOURCE_NAME",
    "SpotifySessionAdapter",
]
