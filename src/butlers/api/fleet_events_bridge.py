"""Dashboard-api side of the cross-process fleet-event transport.

Bridges the Postgres NOTIFY channel published by
``butlers.fleet_events.publish_fleet_event`` (called from the daemon
process) back into the real, in-process fleet event bus
(``butlers.api.routers.events.emit_event``) so every existing
``WS /api/events/stream`` consumer keeps working unchanged regardless of
which process originated the event.

See RFC 0022 (``about/legends-and-lore/rfcs/0022-cross-process-event-transport.md``).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import unquote, urlparse

import asyncpg

from butlers.db import db_params_from_env, should_retry_with_ssl_disable
from butlers.fleet_events import FLEET_EVENTS_CHANNEL

logger = logging.getLogger(__name__)

#: How long to wait before reconnecting after the LISTEN connection drops
#: (server restart, network blip) or a connect attempt fails.
_RECONNECT_BACKOFF_S = 5.0

#: How often to poll connection liveness while idle-listening.
_HEALTH_POLL_INTERVAL_S = 5.0


def _listener_database_name_from_env() -> str:
    """Resolve the dedicated listener's database with URL-first precedence."""
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        database_name = unquote(urlparse(database_url).path).removeprefix("/")
        if not database_name:
            raise ValueError("DATABASE_URL must include a database path for fleet-events listener")
        return database_name

    return os.environ.get("POSTGRES_DB", "butlers")


async def _connect_listener() -> asyncpg.Connection:
    """Open a dedicated (non-pooled) connection for LISTEN.

    LISTEN registrations are connection-scoped in Postgres, so this
    connection must be held for the lifetime of the listener rather than
    borrowed from a pool that recycles/closes connections underneath it.
    Uses the standard URL-first database target plus the same env-derived
    host/auth/SSL params as the daemon's pools, without depending on
    ``DatabaseManager``'s per-butler,
    schema-scoped pools (LISTEN/NOTIFY is database-scoped, not
    schema-scoped, so any single connection to the shared database sees
    every schema's NOTIFYs).
    """
    params = db_params_from_env()
    database = _listener_database_name_from_env()
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
            "Retrying fleet-events LISTEN connection with ssl=disable after SSL upgrade loss"
        )
        return await asyncpg.connect(**retry_kwargs)


def _bridge_to_event_bus(event_type: str, data: dict[str, Any]) -> None:
    """Re-publish a bridged NOTIFY payload onto the real in-process event bus.

    Lazy import avoids a module-load-time dependency between the bridge and
    the router module (mirrors the existing core→api lazy-import pattern
    used at every other ``emit_event`` call site).
    """
    from butlers.api.routers.events import emit_event

    emit_event(event_type, data)


def _on_notify(_conn: asyncpg.Connection, _pid: int, channel: str, payload: str) -> None:
    """asyncpg ``add_listener`` callback: parse and bridge one NOTIFY payload."""
    if channel != FLEET_EVENTS_CHANNEL:
        return
    try:
        envelope = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        logger.warning(
            "fleet_events_bridge: malformed NOTIFY payload on %r, dropping",
            channel,
            exc_info=True,
        )
        return

    if not isinstance(envelope, dict):
        logger.warning("fleet_events_bridge: NOTIFY payload is not a JSON object, dropping")
        return

    event_type = envelope.get("type")
    if not isinstance(event_type, str):
        logger.warning("fleet_events_bridge: NOTIFY payload missing string 'type', dropping")
        return

    data = envelope.get("data")
    _bridge_to_event_bus(event_type, data if isinstance(data, dict) else {})


async def run_fleet_events_listener(
    connect: Callable[[], Awaitable[asyncpg.Connection]] | None = None,
    *,
    on_notify: Callable[[asyncpg.Connection, int, str, str], None] | None = None,
    reconnect_backoff_s: float = _RECONNECT_BACKOFF_S,
    health_poll_interval_s: float = _HEALTH_POLL_INTERVAL_S,
) -> None:
    """Background task: LISTEN on the fleet-events channel and bridge events.

    Runs until cancelled. Reconnects with a fixed backoff whenever the
    connection is lost or cannot be established — a listener that
    permanently exits after one connection blip would silently recreate the
    exact failure mode this bridge exists to fix (the Live indicator stays
    green while events stop arriving).

    Parameters are injectable for testing: pass ``connect`` to target a
    specific database (e.g. a testcontainers instance) instead of the
    process environment, and ``on_notify`` to observe/override the bridge
    callback.
    """
    connect_fn = connect or _connect_listener
    notify_cb = on_notify or _on_notify

    while True:
        conn: asyncpg.Connection | None = None
        try:
            conn = await connect_fn()
            await conn.add_listener(FLEET_EVENTS_CHANNEL, notify_cb)
            logger.info("fleet_events_bridge: LISTEN active on channel %r", FLEET_EVENTS_CHANNEL)
            while not conn.is_closed():
                await asyncio.sleep(health_poll_interval_s)
            logger.warning("fleet_events_bridge: LISTEN connection closed; reconnecting")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "fleet_events_bridge: LISTEN connection error; reconnecting", exc_info=True
            )
        finally:
            # Defensive on every step, not just close(): a connection object
            # in an unexpected state (e.g. a pool-acquired proxy that was
            # already released/detached) can raise from is_closed() itself,
            # not just from close(). Any such failure here must still let
            # the reconnect loop continue rather than propagate and kill the
            # whole listener task -- that would silently and permanently
            # stop the bridge, exactly the failure mode it exists to avoid.
            with contextlib.suppress(Exception):
                if conn is not None and not conn.is_closed():
                    await conn.close()

        await asyncio.sleep(reconnect_backoff_s)
