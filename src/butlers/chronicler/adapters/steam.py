"""Steam play-history projection adapter.

Projects daily playtime aggregates from ``connectors.steam_play_history``
into Chronicler ``play_episode`` episodes.

Semantics:
- Each row in ``connectors.steam_play_history`` maps to exactly one
  ``play_episode`` spanning the calendar day of the ``date`` column.
  ``start_at`` = midnight UTC on that date;
  ``end_at``   = ``start_at + playtime_minutes``.
- Boundary precision is ``day`` — the evidence table stores only a date
  and total playtime for the day, not exact session timestamps.
- Privacy class is ``normal`` — game title and play duration are not
  sensitive personal data.
- Source ref format:
  ``connectors.steam_play_history:{steam_id}:{app_id}:{date}``
  so replays are idempotent regardless of batch boundary shifts.
- Watermark on ``recorded_at`` (monotonically written by the connector).
- Missing evidence table degrades gracefully (module not enabled /
  migration not run on this deployment).
- No LLM call per event — Tier-0 projection only (RFC 0014 §D5).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import asyncpg

from butlers.chronicler.adapters.base import AdapterResult, ProjectionAdapter
from butlers.chronicler.models import Episode, Precision, Privacy
from butlers.chronicler.storage import upsert_episode

logger = logging.getLogger(__name__)

SOURCE_NAME = "steam.play_history"
EPISODE_TYPE_PLAY = "play_episode"
_EVIDENCE_TABLE = "connectors.steam_play_history"
DEFAULT_BATCH_LIMIT = 500


class SteamPlayAdapter(ProjectionAdapter):
    """Project ``connectors.steam_play_history`` rows into Chronicler.

    One row in the evidence table → one ``play_episode`` in Chronicler.
    The episode spans ``[date midnight UTC, date midnight UTC + playtime_minutes)``.
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
    ) -> AdapterResult:
        result = AdapterResult(source_name=self.source_name)

        rows = await self._fetch_rows(pool, since)
        if rows is None:
            result.skipped = True
            result.skipped_reason = (
                f"{_EVIDENCE_TABLE} not found; Steam evidence surface unavailable"
            )
            return result

        latest_watermark = since
        for row in rows:
            await self._project_row(chronicler_pool, row)
            result.rows_projected += 1
            result.episodes_closed += 1

            candidate = row["recorded_at"]
            if candidate is not None and (latest_watermark is None or candidate > latest_watermark):
                latest_watermark = candidate

        result.watermark = latest_watermark
        return result

    async def _fetch_rows(
        self,
        pool: asyncpg.Pool,
        since: datetime | None,
    ) -> list[asyncpg.Record] | None:
        """Fetch evidence rows since the watermark.

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
                          AND table_name = 'steam_play_history'
                    )
                    """
                )
                if not exists:
                    return None

                if since is None:
                    rows = await conn.fetch(
                        f"""
                        SELECT id, steam_id, steam_account_id, app_id, app_name,
                               date, playtime_minutes, recorded_at
                        FROM {_EVIDENCE_TABLE}
                        ORDER BY recorded_at ASC, id ASC
                        LIMIT $1
                        """,
                        self.batch_limit,
                    )
                else:
                    rows = await conn.fetch(
                        f"""
                        SELECT id, steam_id, steam_account_id, app_id, app_name,
                               date, playtime_minutes, recorded_at
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
    ) -> Episode:
        steam_id = row["steam_id"]
        app_id = row["app_id"]
        date = row["date"]

        # Stable source_ref keyed to (steam_id, app_id, date).
        source_ref = f"{_EVIDENCE_TABLE}:{steam_id}:{app_id}:{date}"

        # Derive (start_at, end_at) from the daily aggregate.
        # date is a Python date; attach UTC midnight as the anchor.
        start_at = datetime(date.year, date.month, date.day, tzinfo=UTC)
        playtime_minutes: int = row["playtime_minutes"] or 0
        end_at = start_at + timedelta(minutes=playtime_minutes)

        app_name = row["app_name"]
        steam_account_id = row["steam_account_id"]

        if app_name:
            title = f"Played {app_name}"
        else:
            title = f"Played app {app_id}"

        payload: dict = {
            "steam_id": steam_id,
            "app_id": app_id,
            "date": str(date),
            "playtime_minutes": playtime_minutes,
        }
        if app_name is not None:
            payload["app_name"] = app_name
        if steam_account_id is not None:
            payload["steam_account_id"] = str(steam_account_id)

        async with chronicler_pool.acquire() as conn:
            episode = await upsert_episode(
                conn,
                Episode(
                    source_name=self.source_name,
                    source_ref=source_ref,
                    episode_type=EPISODE_TYPE_PLAY,
                    start_at=start_at,
                    end_at=end_at,
                    precision=Precision.DAY,
                    title=title,
                    payload=payload,
                    privacy=Privacy.NORMAL,
                ),
            )
        return episode


__all__ = [
    "DEFAULT_BATCH_LIMIT",
    "EPISODE_TYPE_PLAY",
    "SOURCE_NAME",
    "SteamPlayAdapter",
]
