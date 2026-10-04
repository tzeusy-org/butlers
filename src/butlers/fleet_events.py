"""Cross-process fleet-event transport: the Postgres NOTIFY publish-side contract.

Daemon processes (``butlers up``) and the dashboard-api process run in
separate containers (see ``docker-compose.yml``) but share one PostgreSQL
database. The in-process fleet event bus (``butlers.api.routers.events``)
only reaches WebSocket clients connected to *that same process* — a daemon
that imports and calls ``emit_event()`` directly mutates its own,
unobserved copy of the ring buffer and subscriber list (bu-01r64).

``publish_fleet_event()`` is the daemon-side half of the fix: it publishes
onto a Postgres NOTIFY channel that any process connected to the same
database can LISTEN on, regardless of which container originated the
event. The dashboard-api process bridges that channel back into its real
``emit_event()`` bus (see ``butlers.api.fleet_events_bridge``), so every
existing ``WS /api/events/stream`` consumer keeps working unchanged.

See RFC 0022 (``about/legends-and-lore/rfcs/0022-cross-process-event-transport.md``)
for the full wire contract, delivery semantics, and failure modes.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

#: Postgres NOTIFY channel carrying the JSON-encoded fleet event envelope.
FLEET_EVENTS_CHANNEL = "butlers_fleet_events"

# Postgres hard-caps a NOTIFY payload at 8000 bytes (server-enforced; sending
# more raises "payload string too long"). Stay comfortably under that so a
# large event degrades to a dropped-and-logged NOTIFY rather than an
# exception bubbling out of a best-effort call site.
_MAX_NOTIFY_PAYLOAD_BYTES = 7800
# Bound acquisition/query work and each transaction cleanup operation. A caller
# connection may need one additional budget to roll back a failed notification.
_PUBLISH_TIMEOUT_S = 1.0


async def _publish_on_connection(connection: asyncpg.Connection, payload: str) -> bool:
    """Isolate NOTIFY from caller-owned work; failed cleanup must remain visible."""
    transaction = connection.transaction()
    # Starting or finishing isolation can itself fail. Those errors are not
    # safe to absorb: the caller must not mistake an unsafe transaction for one
    # it can commit. In an asyncpg-owned outer transaction this is a savepoint.
    async with asyncio.timeout(_PUBLISH_TIMEOUT_S):
        await transaction.start()
    try:
        async with asyncio.timeout(_PUBLISH_TIMEOUT_S):
            await connection.execute("SELECT pg_notify($1, $2)", FLEET_EVENTS_CHANNEL, payload)
    except BaseException as exc:
        async with asyncio.timeout(_PUBLISH_TIMEOUT_S):
            await transaction.rollback()
        if not isinstance(exc, Exception):
            raise  # cancellation remains cancellation, after rollback
        logger.debug("publish_fleet_event: isolated NOTIFY failed (non-fatal)", exc_info=True)
        return False
    async with asyncio.timeout(_PUBLISH_TIMEOUT_S):
        await transaction.commit()
    return True


async def publish_fleet_event(
    pool: asyncpg.Pool | asyncpg.Connection,
    event_type: str,
    data: dict[str, Any] | None = None,
) -> bool:
    """Publish one event onto the cross-process fleet event bus.

    Encodes ``{"type": event_type, "data": data}`` as JSON and sends it via
    ``SELECT pg_notify(channel, payload)`` on *pool*. Any process LISTENing
    on :data:`FLEET_EVENTS_CHANNEL` on the same database receives it —
    including the dashboard-api bridge, which re-publishes it onto the real
    in-process event bus.

    Best-effort: acquisition and sending are bounded to one second. Pool
    failures return ``False``; cancellation propagates. For a caller-owned
    asyncpg connection, a transaction/savepoint isolates SQL errors so they
    do not abort the caller's business transaction. Each isolation/cleanup
    operation also has a one-second budget. If isolation or cleanup fails,
    that error propagates: transaction health cannot safely be asserted.
    When nested in a caller transaction, PostgreSQL delivers NOTIFY only
    after that outer transaction commits.

    Returns ``True`` if the NOTIFY was sent, ``False`` if skipped or safely
    failed. This is lossy transport; callers do not retry or require delivery
    for their durable operation to succeed.
    """
    envelope = {"type": event_type, "data": data or {}}
    try:
        payload = json.dumps(envelope, default=str)
    except Exception:
        # Broad catch (not just TypeError/ValueError): json.dumps's default=str
        # fallback calls str() on any non-serializable value, which itself can
        # raise arbitrary exceptions from a pathological __str__/__repr__.
        # This function must never raise regardless of *data*'s contents.
        logger.warning(
            "publish_fleet_event: event_type=%r data is not JSON-serializable; dropping",
            event_type,
            exc_info=True,
        )
        return False

    payload_bytes = len(payload.encode("utf-8"))
    if payload_bytes > _MAX_NOTIFY_PAYLOAD_BYTES:
        logger.warning(
            "publish_fleet_event: payload too large for NOTIFY (%d bytes > %d limit); "
            "dropping event_type=%r",
            payload_bytes,
            _MAX_NOTIFY_PAYLOAD_BYTES,
            event_type,
        )
        return False

    if isinstance(pool, asyncpg.Connection):
        return await _publish_on_connection(pool, payload)

    try:
        async with asyncio.timeout(_PUBLISH_TIMEOUT_S):
            if isinstance(pool, asyncpg.Pool):
                # The driver query timeout alone does not cover pool exhaustion.
                # Acquisition also supplies a bounded release/reset budget.
                async with pool.acquire(timeout=_PUBLISH_TIMEOUT_S) as connection:
                    await connection.execute(
                        "SELECT pg_notify($1, $2)",
                        FLEET_EVENTS_CHANNEL,
                        payload,
                        timeout=_PUBLISH_TIMEOUT_S,
                    )
            else:
                # Preserve duck-typed pool seams used by existing producers.
                await pool.execute("SELECT pg_notify($1, $2)", FLEET_EVENTS_CHANNEL, payload)
    except Exception:
        logger.debug(
            "publish_fleet_event: NOTIFY failed for event_type=%r (non-fatal)",
            event_type,
            exc_info=True,
        )
        return False
    return True
