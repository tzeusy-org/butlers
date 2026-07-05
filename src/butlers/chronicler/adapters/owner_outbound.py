"""Owner-outbound-message point-event projection adapter (bu-whhll.8).

Projects rows from ``connectors.owner_outbound_events`` (core_161) — one row
per owner-authored message observed live by ``telegram_user_client`` or
``whatsapp_user_client`` — into ``owner_outbound_message`` point events, epic
bu-whhll Tier 1 "workday sensors": the owner's own outbound messages are a
"phone in hand" signal that today is discarded entirely (``comms.message_bursts``
only sees whole-chat batch envelopes with no per-sender granularity; see the
module docstring on ``connectors/owner_outbound_events.py``).

Privacy (bead requirement, hard constraint): point events carry timestamp +
channel ONLY.
- No message content ever reaches this adapter — the evidence table itself
  has no content column.
- No counterpart identity: the evidence table has no chat/thread/counterpart
  column at all (its idempotency key is a one-way hash — see
  ``connectors/owner_outbound_events.py``), so there is nothing to
  accidentally leak here even before payload construction.
- Both point events and the evidence table are stamped ``Privacy.NORMAL``.

Layer discipline (IEA conventions, mirrors ``activitywatch.py``): these are
``layer=evidence`` point events, never episodes — an owner-outbound message
alone must never inflate lane/lived time. This adapter intentionally does
NOT roll rows up into an episode (unlike ActivityWatch's ``screen_episode``
or comms' ``social_episode``): the bead scope is "signal for consumers"
(a weak corroborator for occupation inference, a contradictor for sleep),
and consumers read directly from ``point_events`` — building an episode here
would just be an unused, un-vetted lane-time surface authored ahead of any
consumer needing it. A future consumer that wants an "outbound messaging
episode" rollup can add one without touching this projection.

No LLM call — Tier-0 deterministic projection only (RFC 0014 §D5).
"""

from __future__ import annotations

import logging
from datetime import datetime

import asyncpg

from butlers.chronicler.adapters.base import AdapterResult, ProjectionAdapter
from butlers.chronicler.models import Layer, PointEvent, Precision, Privacy
from butlers.chronicler.storage import upsert_point_event

logger = logging.getLogger(__name__)

SOURCE_NAME = "owner_outbound.messages"
EVENT_TYPE_OWNER_OUTBOUND = "owner_outbound_message"
_EVIDENCE_TABLE = "connectors.owner_outbound_events"
DEFAULT_BATCH_LIMIT = 1000

_CHANNEL_LABELS: dict[str, str] = {
    "telegram_user_client": "Telegram",
    "whatsapp_user_client": "WhatsApp",
}


def _channel_label(channel: str) -> str:
    return _CHANNEL_LABELS.get(channel, channel)


class OwnerOutboundMessageAdapter(ProjectionAdapter):
    """Project ``connectors.owner_outbound_events`` rows into Chronicler.

    Each row -> one ``owner_outbound_message`` point event. No episode
    rollup (see module docstring).
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
        """Fetch new owner-outbound evidence rows; emit one point event each.

        ``since_id`` is ignored: ``connectors.owner_outbound_events.id`` is a
        UUID, not an integer serial (mirrors ``ActivityWatchWindowAdapter``
        / ``OwnTracksPointAdapter``).
        """
        del since_id
        result = AdapterResult(source_name=self.source_name)

        rows = await self._fetch_events(pool, since)
        if rows is None:
            result.skipped = True
            result.skipped_reason = (
                f"{_EVIDENCE_TABLE} not found; owner-outbound evidence surface unavailable"
            )
            return result

        if not rows:
            result.watermark = since
            return result

        latest_watermark = since
        for row in rows:
            occurred_at = row["occurred_at"]
            if occurred_at is not None and (
                latest_watermark is None or occurred_at > latest_watermark
            ):
                latest_watermark = occurred_at

            channel = row["channel"]
            channel_label = _channel_label(channel)

            async with chronicler_pool.acquire() as conn:
                await upsert_point_event(
                    conn,
                    PointEvent(
                        source_name=self.source_name,
                        source_ref=f"{_EVIDENCE_TABLE}:{row['id']}",
                        event_type=EVENT_TYPE_OWNER_OUTBOUND,
                        occurred_at=occurred_at,
                        precision=Precision.EXACT,
                        title=f"Outbound message via {channel_label}",
                        payload={"channel": channel},
                        privacy=Privacy.NORMAL,
                        layer=Layer.EVIDENCE,
                    ),
                )
            result.rows_projected += 1
            result.point_events += 1

        result.watermark = latest_watermark
        return result

    async def _fetch_events(
        self,
        pool: asyncpg.Pool,
        since: datetime | None,
    ) -> list[asyncpg.Record] | None:
        """Fetch evidence rows since the watermark.

        Returns ``None`` if the evidence table is missing — degrade
        gracefully (connector not deployed / migration not run on this
        deployment).
        """
        try:
            async with pool.acquire() as conn:
                exists = await conn.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_schema = 'connectors'
                          AND table_name = 'owner_outbound_events'
                    )
                    """
                )
                if not exists:
                    return None

                if since is None:
                    rows = await conn.fetch(
                        f"""
                        SELECT id, channel, occurred_at
                        FROM {_EVIDENCE_TABLE}
                        ORDER BY occurred_at ASC, id ASC
                        LIMIT $1
                        """,
                        self.batch_limit,
                    )
                else:
                    rows = await conn.fetch(
                        f"""
                        SELECT id, channel, occurred_at
                        FROM {_EVIDENCE_TABLE}
                        WHERE occurred_at > $1
                        ORDER BY occurred_at ASC, id ASC
                        LIMIT $2
                        """,
                        since,
                        self.batch_limit,
                    )
        except asyncpg.PostgresError:
            logger.exception("Failed reading %s", _EVIDENCE_TABLE)
            return None

        return list(rows)


__all__ = [
    "DEFAULT_BATCH_LIMIT",
    "EVENT_TYPE_OWNER_OUTBOUND",
    "SOURCE_NAME",
    "OwnerOutboundMessageAdapter",
]
