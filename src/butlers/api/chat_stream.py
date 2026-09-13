"""Per-request Postgres NOTIFY channel for live dashboard chat streaming.

The dashboard-api process (serving the SSE response) and the routed butler's
daemon process (persisting the eventual ``conversation_reply``) run in
separate containers but share one PostgreSQL database — the same topology
``butlers.fleet_events``/``butlers.api.fleet_events_bridge`` (RFC 0022)
already solves for the fleet event bus. This module is the chat-streaming
analog, scoped to one request instead of one global bus: each dashboard
turn gets its own NOTIFY channel, named from its ``request_id``, so any
process that learns something about that turn (a reply landed; a runtime
that can stream produced a delta) can wake the SSE generator watching it
without that generator falling back to a blind fixed-interval poll.

No runtime adapter (see ``butlers.core.runtimes``) currently exposes
incremental output — every adapter's ``invoke()`` returns only after its
subprocess exits (``proc.communicate()``), so nothing today publishes a
``"token"`` delta onto this channel. ``conversation_reply_create`` publishes
a ``"reply_ready"`` wake once the reply row lands (see
``butlers.api.conversations``), which is what actually shortens the
poll-to-delivery latency today; the ``"token"``/``"phase"`` envelope types
are honored end-to-end by the listener and the SSE generator so a future
streaming-capable producer has a real channel to publish onto, without
requiring another round of router surgery.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any
from uuid import UUID

import asyncpg

from butlers.db import database_name_from_env, db_params_from_env, should_retry_with_ssl_disable

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
        await pool.execute("SELECT pg_notify($1, $2)", channel, payload)
    except Exception:
        logger.debug(
            "publish_chat_stream_event: NOTIFY failed for event_type=%r (non-fatal)",
            event_type,
            exc_info=True,
        )
        return False
    return True


async def _connect_listener() -> asyncpg.Connection:
    """Open a dedicated (non-pooled) connection for LISTEN.

    Mirrors ``butlers.api.fleet_events_bridge._connect_listener``: LISTEN
    registrations are connection-scoped in Postgres, so this connection must
    be held for the life of the SSE request rather than borrowed from a pool
    that recycles/closes connections underneath it.
    """
    params = db_params_from_env()
    database = database_name_from_env("butlers")
    connect_kwargs: dict[str, Any] = {**params, "database": database}
    try:
        return await asyncpg.connect(**connect_kwargs)
    except Exception as exc:
        ssl = connect_kwargs.get("ssl")
        if not should_retry_with_ssl_disable(exc, ssl):
            raise
        retry_kwargs = dict(connect_kwargs)
        retry_kwargs["ssl"] = "disable"
        logger.info(
            "Retrying chat-stream LISTEN connection with ssl=disable after SSL upgrade loss"
        )
        return await asyncpg.connect(**retry_kwargs)


class ChatStreamListener:
    """Holds the dedicated LISTEN connection for one dashboard turn.

    ``get()`` returns the next envelope published on this turn's channel, or
    ``None`` if *timeout* elapses first — the SSE generator treats a timeout
    exactly like the old fixed-interval poll wakeup, so LISTEN only ever
    shortens the wait, never replaces the safety-net re-check.
    """

    def __init__(
        self,
        conn: asyncpg.Connection,
        channel: str,
        queue: asyncio.Queue[dict[str, Any]],
        on_notify: Any,
    ) -> None:
        self._conn = conn
        self._channel = channel
        self._queue = queue
        self._on_notify = on_notify

    async def get(self, timeout: float) -> dict[str, Any] | None:
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except TimeoutError:
            return None

    async def aclose(self) -> None:
        with contextlib.suppress(Exception):
            await self._conn.remove_listener(self._channel, self._on_notify)
        with contextlib.suppress(Exception):
            if not self._conn.is_closed():
                await self._conn.close()


async def open_chat_stream_listener(request_id: UUID | str) -> ChatStreamListener | None:
    """Best-effort: open a dedicated LISTEN connection for *request_id*.

    Returns ``None`` (never raises) when the dedicated connection cannot be
    established or LISTEN registration fails — callers must degrade to
    poll-only in that case, the same honest fallback used when the routed
    runtime cannot stream at all.
    """
    channel = chat_stream_channel(request_id)
    try:
        conn = await _connect_listener()
    except Exception:
        logger.debug(
            "open_chat_stream_listener: LISTEN connection failed (non-fatal)", exc_info=True
        )
        return None

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    def _on_notify(_conn: asyncpg.Connection, _pid: int, _channel: str, payload: str) -> None:
        try:
            envelope = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            return
        if isinstance(envelope, dict):
            queue.put_nowait(envelope)

    try:
        await conn.add_listener(channel, _on_notify)
    except Exception:
        logger.debug(
            "open_chat_stream_listener: LISTEN registration failed (non-fatal)", exc_info=True
        )
        with contextlib.suppress(Exception):
            await conn.close()
        return None

    return ChatStreamListener(conn, channel, queue, on_notify=_on_notify)
