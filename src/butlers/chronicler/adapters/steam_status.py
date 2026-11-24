"""Steam status changes → minute-precision Play evidence (REQ-butler-chronicler-020).

Replay the sparse change log in keyset pages. A page boundary must not close a
session. Replaying also refreshes provisional sessions without waiting for a new
change or trusting a checkpoint as a completeness boundary. Memory is bounded by
one page and one active session; all episode upserts keep stable source refs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

from butlers.chronicler.adapters._owner_entity import (
    owner_episode_transaction,
    resolve_owner_entity_id,
    upsert_owner_episode_entity,
)
from butlers.chronicler.adapters.base import AdapterResult, ProjectionAdapter
from butlers.chronicler.models import Confidence, Episode, Layer, Precision, Privacy
from butlers.chronicler.storage import upsert_episode

SOURCE_NAME = "steam.status_history"
MAX_OPEN_SESSION = timedelta(hours=12)


def _now() -> datetime:
    return datetime.now(UTC)


def session_episode(start: Any, *, end: datetime | None, now: datetime) -> Episode | None:
    """An immutable status-start identity, refreshed until its closing change arrives."""
    stop = end if end is not None else min(now, start["observed_at"] + MAX_OPEN_SESSION)
    if stop <= start["observed_at"]:
        return None
    return Episode(
        source_name=SOURCE_NAME,
        source_ref=f"steam.status:{start['steam_id']}:{start['observed_at'].isoformat()}",
        episode_type="play_session",
        start_at=start["observed_at"],
        end_at=stop,
        precision=Precision.MINUTE,
        title=f"Played {start['game_name']}",
        payload={
            "steam_id": start["steam_id"],
            "steam_account_id": str(start["steam_account_id"])
            if start["steam_account_id"]
            else None,
            "game_id": start["game_id"],
            "game_name": start["game_name"],
            "provisional": end is None,
        },
        privacy=Privacy.NORMAL,
        layer=Layer.EVIDENCE,
        confidence=Confidence.MEDIUM,
    )


class SteamStatusAdapter(ProjectionAdapter):
    """Pair detected changes; same-game persona changes leave the session running."""

    def __init__(self, *, batch_limit: int = 500) -> None:
        super().__init__(SOURCE_NAME)
        if batch_limit <= 0:
            raise ValueError("batch_limit must be positive")
        self.batch_limit = batch_limit

    async def project(
        self,
        pool: asyncpg.Pool,
        *,
        chronicler_pool: asyncpg.Pool,
        since: datetime | None,
        since_id: int | None = None,
    ) -> AdapterResult:
        del since_id
        result = AdapterResult(source_name=self.source_name)
        if not await pool.fetchval("SELECT to_regclass('connectors.steam_status_history')"):
            result.skipped = True
            result.skipped_reason = "Steam status evidence surface unavailable"
            return result
        now = _now()
        owner = await resolve_owner_entity_id(pool)
        cursor_id = None
        cursor_at = None
        active = None

        async def emit(
            start: Any, end: datetime | None, changed_at: datetime | None = None
        ) -> None:
            episode = session_episode(start, end=end, now=now)
            if episode is None:
                return
            recorded_at = max(
                start.get("recorded_at", now), changed_at or start.get("recorded_at", now)
            )
            if since is not None and recorded_at <= since:
                if end is not None or start["observed_at"] + MAX_OPEN_SESSION <= since:
                    return  # Closed or already-capped evidence is unchanged.
            async with chronicler_pool.acquire() as conn:
                async with owner_episode_transaction(conn):
                    saved = await upsert_episode(conn, episode)
                    await upsert_owner_episode_entity(conn, saved.id, owner_id=owner)
            result.rows_projected += 1
            if end is None:
                result.episodes_opened += 1
            else:
                result.episodes_closed += 1

        while True:
            rows = await pool.fetch(
                """
                SELECT steam_id, steam_account_id, observed_at, persona_state,
                       game_id, game_name, recorded_at
                FROM connectors.steam_status_history
                WHERE ($1::bigint IS NULL OR (steam_id, observed_at) > ($1, $2::timestamptz))
                  AND observed_at <= $3
                ORDER BY steam_id, observed_at
                LIMIT $4
                """,
                cursor_id,
                cursor_at,
                now,
                self.batch_limit,
            )
            if not rows:
                break
            for row in rows:
                if active is not None:
                    if row["steam_id"] != active["steam_id"]:
                        await emit(active, None)
                        active = None
                    elif row["game_name"] != active["game_name"] or row["persona_state"] == 0:
                        await emit(active, row["observed_at"], row.get("recorded_at", now))
                        active = None
                if active is None and row["game_name"] and row["persona_state"] != 0:
                    active = row
                cursor_id, cursor_at = row["steam_id"], row["observed_at"]
            if len(rows) < self.batch_limit:
                break
        if active is not None:
            await emit(active, None)
        result.watermark = now
        return result
