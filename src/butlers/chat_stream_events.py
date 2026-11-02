"""Transport-neutral publication for the dashboard's request-scoped event channel.

SSE listening remains in the API. Shared message writers publish best-effort
notifications through this one primitive; persisted rows remain authoritative.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

import asyncpg

logger = logging.getLogger(__name__)

#: Prefix for the per-request NOTIFY channel. Postgres channel identifiers
#: are capped at 63 bytes (NAMEDATALEN); prefix + a 32-hex-digit request id
#: stays well under that.
_CHANNEL_PREFIX = "dashboard_chat_stream_"

# Same server-enforced NOTIFY payload cap as butlers.fleet_events, with the
# same safety margin.
_MAX_NOTIFY_PAYLOAD_BYTES = 7800


def chat_stream_channel(request_id: UUID | str) -> str:
    """Return the NOTIFY channel name for one dashboard turn's ``request_id``."""
    return f"{_CHANNEL_PREFIX}{str(request_id).replace('-', '')}"


async def publish_chat_stream_event(
    pool: asyncpg.Pool | asyncpg.Connection,
    request_id: UUID | str,
    event_type: str,
    data: dict[str, Any] | None = None,
) -> bool:
    """Publish one event onto *request_id*'s chat-stream NOTIFY channel.

    Best-effort and never raises, mirroring
    ``butlers.fleet_events.publish_fleet_event``: a dropped NOTIFY degrades
    the stream to its poll-based safety net, it must never fail the
    caller's actual persistence work.

    Returns ``True`` if the NOTIFY was sent, ``False`` if it was skipped
    (oversized/unserializable payload) or failed (logged at debug level).
    """
    channel = chat_stream_channel(request_id)
    envelope = {"type": event_type, "data": data or {}}
    try:
        payload = json.dumps(envelope, default=str)
    except Exception:
        logger.warning(
            "publish_chat_stream_event: event_type=%r data is not JSON-serializable; dropping",
            event_type,
            exc_info=True,
        )
        return False

    payload_bytes = len(payload.encode("utf-8"))
    if payload_bytes > _MAX_NOTIFY_PAYLOAD_BYTES:
        logger.warning(
            "publish_chat_stream_event: payload too large for NOTIFY (%d bytes > %d limit); "
            "dropping event_type=%r",
            payload_bytes,
            _MAX_NOTIFY_PAYLOAD_BYTES,
            event_type,
        )
        return False

    try:
        if isinstance(pool, (asyncpg.Connection, asyncpg.pool.PoolConnectionProxy)):
            # A swallowed SQL error still aborts its PostgreSQL transaction.
            # An acquired connection may carry canonical reply/terminal writes;
            # roll back only optional NOTIFY, leaving polling authoritative.
            async with pool.transaction():
                await pool.execute("SELECT pg_notify($1, $2)", channel, payload)
        else:
            await pool.execute("SELECT pg_notify($1, $2)", channel, payload)
    except Exception:
        logger.debug(
            "publish_chat_stream_event: NOTIFY failed for event_type=%r (non-fatal)",
            event_type,
            exc_info=True,
        )
        return False
    return True
