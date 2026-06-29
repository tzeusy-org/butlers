"""Focus / deep-work block inference adapter.

Derives ``focus_block`` episodes from already-projected chronicler data.

v1 signal:

1. Long single-context work episodes from ``core.sessions``: episodes with
   ``episode_type = 'work'`` whose duration is at least
   ``MIN_FOCUS_DURATION_MINUTES`` and whose category is ``tasks``
   (i.e. trigger_source is NOT ``route`` — see ``aggregations.category_for``).
   This catches scheduled and triggered work that ran long without
   route-driven interruption.

2. Calendar-titled focus blocks: ``google_calendar.completed`` episodes
   whose title matches one of the focus keywords
   (``focus``, ``deep work``, ``pomodoro``, case-insensitive).

Both signal classes project new ``focus_block`` episodes under
``source_name = 'chronicler.focus_inferred'`` with deterministic source
refs derived from the underlying episode id, so re-running the adapter
is idempotent.

The watermark is the ``created_at`` of the underlying chronicler row.

This adapter reads from ``chronicler.episodes`` (its own schema). That is
permitted by the manifesto: chronicler may read what chronicler wrote, as
long as the inference itself stays deterministic.

No LLM call.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from butlers.chronicler.adapters._owner_entity import (
    resolve_owner_entity_id,
    upsert_owner_episode_entity,
)
from butlers.chronicler.adapters.base import AdapterResult, ProjectionAdapter
from butlers.chronicler.aggregations import category_for
from butlers.chronicler.models import Episode, Layer, Precision, Privacy
from butlers.chronicler.storage import upsert_episode

logger = logging.getLogger(__name__)

SOURCE_NAME = "chronicler.focus_inferred"
EPISODE_TYPE_FOCUS = "focus_block"

DEFAULT_BATCH_LIMIT = 500
MIN_FOCUS_DURATION_MINUTES = 45

_FOCUS_KEYWORDS = ("focus", "deep work", "pomodoro")
_FOCUS_KEYWORDS_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _FOCUS_KEYWORDS) + r")\b",
    re.IGNORECASE,
)


def _title_matches_focus(title: str | None) -> bool:
    if not title:
        return False
    if len(title) > 80:
        # Defensive guard: don't match on accidentally long strings.
        return False
    return _FOCUS_KEYWORDS_RE.search(title) is not None


def _record_value(row: asyncpg.Record, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError):
        return default


class FocusInferredAdapter(ProjectionAdapter):
    """Project focus_block episodes inferred from chronicler.episodes."""

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
        """Read recently-created chronicler episodes; emit focus_block where applicable.

        Inference reads from the same chronicler schema we write to. We use
        the chronicler_pool for both read and write to keep the operation
        scoped to one role and one search_path.
        """
        result = AdapterResult(source_name=self.source_name)

        rows = await self._fetch_candidate_rows(chronicler_pool, since)
        if rows is None:
            result.skipped = True
            result.skipped_reason = "chronicler.episodes not found"
            return result

        # Resolve owner entity_id once per adapter run (not per row).
        # pool is the cross-butler pool that can access public.contacts.
        entity_id = await resolve_owner_entity_id(pool)

        latest_watermark = since
        for row in rows:
            episode = await self._maybe_project(chronicler_pool, row, entity_id=entity_id)
            candidate = row["created_at"]
            if candidate is not None:
                if latest_watermark is None or candidate > latest_watermark:
                    latest_watermark = candidate
            if episode is None:
                continue
            result.rows_projected += 1
            result.episodes_closed += 1
        result.watermark = latest_watermark
        return result

    async def _fetch_candidate_rows(
        self, chronicler_pool: asyncpg.Pool, since: datetime | None
    ) -> list[asyncpg.Record] | None:
        """Read candidate source episodes (sessions + calendar) since watermark."""
        try:
            async with chronicler_pool.acquire() as conn:
                if since is None:
                    rows = await conn.fetch(
                        """
                        SELECT e.id, e.source_name, e.source_ref, e.episode_type,
                               e.start_at, e.end_at, e.title, e.payload, e.created_at,
                               EXISTS (
                                   SELECT 1
                                   FROM episodes route
                                   WHERE route.tombstone_at IS NULL
                                     AND route.source_name = 'core.sessions'
                                     AND route.episode_type = 'work'
                                     AND route.payload->>'trigger_source' = 'route'
                                     AND route.id <> e.id
                                     AND route.start_at < e.end_at
                                     AND (
                                         route.end_at IS NULL
                                         OR route.end_at > e.start_at
                                     )
                               ) AS overlaps_route
                        FROM episodes e
                        WHERE e.tombstone_at IS NULL
                          AND e.source_name IN ('core.sessions', 'google_calendar.completed')
                        ORDER BY e.created_at ASC, e.id ASC
                        LIMIT $1
                        """,
                        self.batch_limit,
                    )
                else:
                    rows = await conn.fetch(
                        """
                        SELECT e.id, e.source_name, e.source_ref, e.episode_type,
                               e.start_at, e.end_at, e.title, e.payload, e.created_at,
                               EXISTS (
                                   SELECT 1
                                   FROM episodes route
                                   WHERE route.tombstone_at IS NULL
                                     AND route.source_name = 'core.sessions'
                                     AND route.episode_type = 'work'
                                     AND route.payload->>'trigger_source' = 'route'
                                     AND route.id <> e.id
                                     AND route.start_at < e.end_at
                                     AND (
                                         route.end_at IS NULL
                                         OR route.end_at > e.start_at
                                     )
                               ) AS overlaps_route
                        FROM episodes e
                        WHERE e.tombstone_at IS NULL
                          AND e.source_name IN ('core.sessions', 'google_calendar.completed')
                          AND e.created_at > $1
                        ORDER BY e.created_at ASC, e.id ASC
                        LIMIT $2
                        """,
                        since,
                        self.batch_limit,
                    )
        except (asyncpg.UndefinedTableError, asyncpg.PostgresError):
            logger.exception("Failed reading chronicler.episodes for focus inference")
            return None
        return list(rows)

    async def _maybe_project(
        self,
        chronicler_pool: asyncpg.Pool,
        row: asyncpg.Record,
        *,
        entity_id: UUID | None = None,
    ) -> Episode | None:
        source_name = row["source_name"]
        episode_type = row["episode_type"]
        start_at = row["start_at"]
        end_at = row["end_at"]
        title = row["title"]
        payload_raw: Any = row["payload"]
        payload: dict[str, Any] = dict(payload_raw) if isinstance(payload_raw, dict) else {}

        if start_at is None or end_at is None:
            return None
        duration_minutes = int((end_at - start_at).total_seconds() // 60)
        if duration_minutes < MIN_FOCUS_DURATION_MINUTES:
            return None

        signal: str | None = None
        if source_name == "core.sessions" and episode_type == "work":
            trigger_source = payload.get("trigger_source")
            cat = category_for(source_name, episode_type, trigger_source=trigger_source)
            if cat == "tasks" and not bool(_record_value(row, "overlaps_route", False)):
                signal = "long_task_session"
        elif source_name == "google_calendar.completed" and episode_type == "scheduled_block":
            if _title_matches_focus(title):
                signal = "calendar_titled"

        if signal is None:
            return None

        # Deterministic source_ref: tied to the underlying episode id.
        source_ref = f"chronicler.episodes:{row['id']}:{signal}"

        out_payload: dict[str, Any] = {
            "signal": signal,
            "source_episode_id": str(row["id"]),
            "source_episode_source_name": source_name,
            "source_episode_source_ref": row["source_ref"],
            "duration_minutes": duration_minutes,
        }
        out_title = f"Focus: {title}" if title else f"Focus block ({duration_minutes}m)"

        async with chronicler_pool.acquire() as conn:
            episode = await upsert_episode(
                conn,
                Episode(
                    source_name=self.source_name,
                    source_ref=source_ref,
                    episode_type=EPISODE_TYPE_FOCUS,
                    start_at=start_at,
                    end_at=end_at,
                    precision=Precision.MINUTE,
                    title=out_title[:200],
                    payload=out_payload,
                    privacy=Privacy.NORMAL,
                    layer=Layer.ACTIVITY,
                ),
            )
            # Write owner row into episode_entities join table (bu-4c1ks).
            await upsert_owner_episode_entity(conn, episode.id, owner_id=entity_id)
        return episode


__all__ = [
    "DEFAULT_BATCH_LIMIT",
    "EPISODE_TYPE_FOCUS",
    "FocusInferredAdapter",
    "MIN_FOCUS_DURATION_MINUTES",
    "SOURCE_NAME",
]
