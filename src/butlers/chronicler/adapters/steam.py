"""Steam play-history projection adapter.

Projects daily playtime aggregates from ``connectors.steam_play_history``
into Chronicler ``play_episode`` episodes.

Semantics:
- Each row in ``connectors.steam_play_history`` maps to exactly one
  ``play_episode`` whose duration equals ``playtime_minutes`` and whose
  end is anchored to the most recent observation timestamp inside the
  calendar day. Concretely:
  ``end_at``   = ``min(recorded_at, end_of_date_UTC)``;
  ``start_at`` = ``max(start_of_date_UTC, end_at - playtime_minutes)``.
  This keeps the bar visually aligned with when the activity actually
  happened (typically late-day) instead of always starting at midnight.
- Boundary precision is ``day`` — the evidence table stores only a date
  and total playtime for the day, not exact session timestamps. The
  end-of-observation anchor is a best-effort visual hint.
- Privacy class is ``normal`` — game title and play duration are not
  sensitive personal data.
- Source ref format:
  ``connectors.steam_play_history:{steam_id}:{app_id}:{date}``
  so replays are idempotent regardless of batch boundary shifts.
- Watermark on ``recorded_at`` (monotonically written by the connector).
  ``connectors.steam_play_history.id`` is UUID-backed, so this adapter does
  not use the integer ``watermark_id`` tuple path.
- Missing evidence table degrades gracefully (module not enabled /
  migration not run on this deployment).
- No LLM call per event — Tier-0 projection only (RFC 0014 §D5).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from datetime import date as date_cls
from uuid import UUID

import asyncpg

from butlers.chronicler.adapters._owner_entity import (
    resolve_owner_entity_id,
    upsert_owner_episode_entity,
)
from butlers.chronicler.adapters.base import AdapterResult, ProjectionAdapter
from butlers.chronicler.models import Episode, Layer, Precision, Privacy
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
        since_id: int | None = None,
    ) -> AdapterResult:
        result = AdapterResult(source_name=self.source_name)

        rows = await self._fetch_rows(pool, since, since_id)
        if rows is None:
            result.skipped = True
            result.skipped_reason = (
                f"{_EVIDENCE_TABLE} not found; Steam evidence surface unavailable"
            )
            return result

        # Resolve owner entity_id once per adapter run (not per row).
        entity_id = await resolve_owner_entity_id(pool)

        latest_watermark = since
        for row in rows:
            candidate = row["recorded_at"]
            if candidate is not None:
                if latest_watermark is None or candidate > latest_watermark:
                    latest_watermark = candidate

            skip_reason = self._row_skip_reason(row)
            if skip_reason is not None:
                result.warnings.append(skip_reason)
                logger.warning("Skipping malformed %s row: %s", _EVIDENCE_TABLE, skip_reason)
                continue

            await self._project_row(chronicler_pool, row, entity_id=entity_id)
            result.rows_projected += 1
            result.episodes_closed += 1

        result.watermark = latest_watermark
        # ``projection_checkpoints.watermark_id`` is BIGINT, but the Steam
        # evidence table primary key is UUID. Keep timestamp-only semantics for
        # this adapter, matching other UUID-backed sources.
        result.watermark_id = None
        return result

    def _row_skip_reason(self, row: asyncpg.Record) -> str | None:
        """Return a reason when a source row cannot produce a valid episode."""
        steam_id = row["steam_id"]
        app_id = row["app_id"]
        row_date = row["date"]
        playtime_minutes = row["playtime_minutes"]

        source_ref = f"{_EVIDENCE_TABLE}:{steam_id}:{app_id}:{row_date}"
        if steam_id is None:
            return f"{source_ref} has NULL steam_id"
        if app_id is None:
            return f"{source_ref} has NULL app_id"
        if not isinstance(row_date, date_cls):
            return f"{source_ref} has invalid date"
        if playtime_minutes is None:
            return None
        if not isinstance(playtime_minutes, int) or isinstance(playtime_minutes, bool):
            return f"{source_ref} has non-integer playtime_minutes"
        if playtime_minutes < 0:
            return f"{source_ref} has negative playtime_minutes"
        return None

    async def _fetch_rows(
        self,
        pool: asyncpg.Pool,
        since: datetime | None,
        since_id: int | None = None,
    ) -> list[asyncpg.Record] | None:
        """Fetch evidence rows since the watermark.

        Always uses single-column ``recorded_at`` semantics. The evidence
        table's ``id`` column is a UUID, while the shared checkpoint
        ``watermark_id`` column is BIGINT for integer-backed sources.

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
                          AND table_name = 'steam_play_history'
                    )
                    """
                )
                if not exists:
                    return None

                if since is None:
                    rows = await conn.fetch(
                        f"""
                        SELECT steam_id, steam_account_id, app_id, app_name,
                               date, playtime_minutes, recorded_at
                        FROM {_EVIDENCE_TABLE}
                        ORDER BY recorded_at ASC, steam_id ASC, app_id ASC, date ASC
                        LIMIT $1
                        """,
                        self.batch_limit,
                    )
                else:
                    rows = await conn.fetch(
                        f"""
                        SELECT steam_id, steam_account_id, app_id, app_name,
                               date, playtime_minutes, recorded_at
                        FROM {_EVIDENCE_TABLE}
                        WHERE recorded_at > $1
                        ORDER BY recorded_at ASC, steam_id ASC, app_id ASC, date ASC
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
        steam_id = row["steam_id"]
        app_id = row["app_id"]
        date = row["date"]

        # Stable source_ref keyed to (steam_id, app_id, date).
        source_ref = f"{_EVIDENCE_TABLE}:{steam_id}:{app_id}:{date}"

        # Derive (start_at, end_at) from the daily aggregate.
        # Steam exposes only a date + cumulative playtime per day, not exact
        # session bounds. To avoid every gaming bar starting at midnight UTC
        # on the Gantt, anchor end_at to the most recent observation time
        # inside the date and back-calculate start_at from playtime_minutes.
        start_of_day = datetime(date.year, date.month, date.day, tzinfo=UTC)
        end_of_day = start_of_day + timedelta(days=1)
        playtime_minutes: int = row["playtime_minutes"] or 0
        duration = timedelta(minutes=playtime_minutes)
        recorded_at = row["recorded_at"]
        if recorded_at is not None and start_of_day <= recorded_at < end_of_day:
            anchor_end = recorded_at
        else:
            anchor_end = end_of_day
        start_at = max(start_of_day, anchor_end - duration)
        end_at = max(start_at + duration, anchor_end) if duration > timedelta(0) else anchor_end
        if end_at > end_of_day:
            end_at = end_of_day
            start_at = max(start_of_day, end_at - duration)

        # When the daily aggregate exceeds the wall-clock window from
        # start-of-day to the anchor observation, start_at clamps to
        # start_of_day. This is the documented best-effort behaviour for
        # daily-aggregate sources, but it is also a real signal of a
        # connector polling gap or accumulated overnight play that an
        # operator may want to investigate. Surface it via a structured
        # warning so it is greppable and never silently swallowed.
        elapsed_since_midnight = anchor_end - start_of_day
        if duration > elapsed_since_midnight and start_at == start_of_day:
            logger.warning(
                "steam.play_history clamp: start_at pinned to %s for "
                "steam_id=%s app_id=%s date=%s playtime_minutes=%d "
                "(exceeds elapsed_minutes=%d since midnight; anchor=%s)",
                start_of_day,
                steam_id,
                app_id,
                date,
                playtime_minutes,
                int(elapsed_since_midnight.total_seconds() // 60),
                anchor_end,
            )

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
                    layer=Layer.ACTIVITY,
                ),
            )
            # Write owner row into episode_entities join table (bu-4c1ks).
            await upsert_owner_episode_entity(conn, episode.id, owner_id=entity_id)
        return episode


__all__ = [
    "DEFAULT_BATCH_LIMIT",
    "EPISODE_TYPE_PLAY",
    "SOURCE_NAME",
    "SteamPlayAdapter",
]
