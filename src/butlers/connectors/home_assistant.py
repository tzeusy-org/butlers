"""Home Assistant connector — WebSocket-first with REST polling fallback.

STATUS: COMPLETE (tasks 3, 5–9 — WebSocket client core, filter pipeline, envelope
construction, checkpoint, history persistence, health, heartbeat, metrics)
==================================================================================

This module implements:
- Task 3 (WebSocket client core): HAWebSocketClient with auth handshake, event
  subscription, ping/pong keepalive, exponential backoff reconnection, and event
  dispatch to the filter pipeline.
- Task 8 (health, heartbeat, metrics): HAConnector health state derivation,
  heartbeat assembly, and HA-specific Prometheus metrics.

The connector bridges real-time HA events into the butler ecosystem via the
Switchboard's canonical ingest path.  It maintains a persistent WebSocket
connection to HA's WebSocket API and falls back to REST polling when the
WebSocket is unavailable.

The connector bridges real-time HA events into the butler ecosystem via the
Switchboard's canonical ingest path.  It maintains a persistent WebSocket
connection to HA's WebSocket API and falls back to REST polling when the
WebSocket is unavailable.

Environment variables:
- SWITCHBOARD_MCP_URL (required): Switchboard MCP SSE endpoint
- CONNECTOR_PROVIDER (default: home_assistant)
- CONNECTOR_CHANNEL (default: home_assistant)
- CONNECTOR_HEALTH_PORT (default: 40087)
- HA_BASE_URL: HA instance base URL (overrides entity_info)
- HA_ACCESS_TOKEN: HA long-lived access token (overrides entity_info)
- HA_DOMAIN_ALLOWLIST: comma-separated domain allowlist (default does NOT include
  ``person``; add ``person`` to capture presence/location data into
  ``connectors.home_assistant_history``)
- HA_POLL_INTERVAL_S (default: 60): REST fallback poll interval
- HA_CHECKPOINT_OVERLAP_S (default: 30): checkpoint resume safety margin
- HA_WS_PING_INTERVAL_S (default: 30): WebSocket keepalive ping interval
- HA_WS_PONG_TIMEOUT_S (default: 10): WebSocket pong wait timeout
- HA_DISCRETION_TIMEOUT_S (default: 5): discretion evaluator timeout
- HA_EVENT_QUEUE_MAX (default: 100): max queued events before dropping oldest

Metrics exported (HA-specific, in addition to standard ConnectorMetrics):
  Counters:
  - connector_ha_events_total{stage, outcome}
  - connector_ha_ws_reconnects_total{endpoint_identity}
  - connector_ha_rest_polls_total{endpoint_identity, status}
  - connector_ha_discretion_total{endpoint_identity, verdict}
  Gauges:
  - connector_ha_filter_pass_rate{endpoint_identity}
  - connector_ha_transport_mode{endpoint_identity}  (1=websocket, 0=rest_fallback)
  - connector_ha_entities_tracked{endpoint_identity}
  Histograms:
  - connector_ha_event_latency_seconds{endpoint_identity}
  - connector_ha_filter_pipeline_seconds{endpoint_identity}
"""

from __future__ import annotations

import asyncio
import heapq
import json
import logging
import os
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Thread
from typing import TYPE_CHECKING, Any, Literal, Protocol
from urllib.parse import urlparse

from prometheus_client import Counter, Gauge, Histogram, generate_latest

from butlers.connectors.db_role import connector_setup_role
from butlers.connectors.health_socket import make_health_socket
from butlers.connectors.heartbeat import ConnectorHeartbeat, HeartbeatConfig
from butlers.connectors.home_assistant_envelope import parse_time_fired
from butlers.connectors.home_assistant_wellness import (
    WellnessClassifier,
    WellnessRule,
    parse_rules_extra,
)
from butlers.connectors.mcp_client import CachedMCPClient
from butlers.connectors.metrics import ConnectorMetrics

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONNECTOR_TYPE = "home_assistant"
_CONNECTOR_CHANNEL = "home_assistant"
_CONNECTOR_PROVIDER = "home_assistant"

_DEFAULT_HEALTH_PORT = 40087
_DEFAULT_POLL_INTERVAL_S = 60
_DEFAULT_CHECKPOINT_OVERLAP_S = 30
_DEFAULT_WS_PING_INTERVAL_S = 30
_DEFAULT_WS_PONG_TIMEOUT_S = 10
_DEFAULT_DISCRETION_TIMEOUT_S = 5
_DEFAULT_EVENT_QUEUE_MAX = 100
# Wall-clock window an event is held in the reorder buffer before it becomes
# eligible for submission. HA-internal batching delivers out-of-order events
# within the same WebSocket burst (sub-second), so a short window is enough to
# let a late-but-earlier event slot ahead while keeping submission latency low.
_DEFAULT_EVENT_REORDER_WINDOW_S = 0.5
_DEFAULT_WS_RECONNECT_INITIAL_S = 1.0
_DEFAULT_WS_RECONNECT_MAX_S = 60.0

# Number of consecutive failed WS reconnect attempts before the connector
# activates the REST polling fallback (spec: REST API Polling Fallback).
_WS_FALLBACK_FAILURE_THRESHOLD = 3
_DEFAULT_WS_RECONNECT_JITTER = 0.5

_DEFAULT_DOMAIN_ALLOWLIST = frozenset(
    {
        "light",
        "switch",
        "sensor",
        "climate",
        "lock",
        "cover",
        "binary_sensor",
        "automation",
        "script",
    }
)

# Health states per spec §8
HealthState = Literal["healthy", "degraded", "error", "starting"]

# ---------------------------------------------------------------------------
# WebSocket event types subscribed by the connector (task 3.3)
# ---------------------------------------------------------------------------

_WS_EVENT_SUBSCRIPTIONS = (
    "state_changed",
    "automation_triggered",
    "call_service",
)

# ---------------------------------------------------------------------------
# HAWebSocketClient (tasks 3.1–3.6)
# ---------------------------------------------------------------------------


# Structural type for the event dispatch callback. A Protocol (rather than a
# Callable alias) so the optional ``transport`` override is part of the
# statically-checkable signature:
#   async def dispatch(event_type, event, transport=None) -> None
class _EventDispatch(Protocol):
    async def __call__(
        self,
        event_type: str,
        event: dict[str, Any],
        transport: str | None = None,
    ) -> None: ...


class HAWebSocketClient:
    """WebSocket client for the Home Assistant connector (tasks 3.1–3.6).

    Implements:
    - 3.1 Connector process lifecycle (start/stop)
    - 3.2 WebSocket auth handshake (auth_required → auth → auth_ok/auth_invalid)
    - 3.3 Event subscriptions: state_changed, automation_triggered, call_service
    - 3.4 Ping/pong keepalive (configurable interval + timeout)
    - 3.5 Exponential backoff reconnection (1s → cap, with jitter)
    - 3.6 Event message parsing and dispatch to filter pipeline callback

    The client is deliberately decoupled from the HAConnector health/metrics
    layer: it exposes callbacks (``on_connected``, ``on_disconnected``) so the
    HAConnector can update health state without tight coupling.

    Usage::

        async def handle_event(event_type: str, event: dict) -> None:
            ...  # route to filter pipeline

        client = HAWebSocketClient(
            ha_base_url="http://ha.local:8123",
            ha_access_token="super-secret-token",
            dispatch=handle_event,
            ping_interval_s=30,
            pong_timeout_s=10,
            reconnect_initial_s=1.0,
            reconnect_max_s=60.0,
            on_connected=connector.on_ws_connected,
            on_disconnected=connector.on_ws_disconnected,
        )
        await client.run()  # blocks until stop() is called
    """

    def __init__(
        self,
        *,
        ha_base_url: str,
        ha_access_token: str,
        dispatch: _EventDispatch,
        ping_interval_s: int = _DEFAULT_WS_PING_INTERVAL_S,
        pong_timeout_s: int = _DEFAULT_WS_PONG_TIMEOUT_S,
        reconnect_initial_s: float = _DEFAULT_WS_RECONNECT_INITIAL_S,
        reconnect_max_s: float = _DEFAULT_WS_RECONNECT_MAX_S,
        reconnect_jitter: float = _DEFAULT_WS_RECONNECT_JITTER,
        on_connected: Callable[[], None] | None = None,
        on_disconnected: Callable[[], None] | None = None,
        on_reconnect_failed: Callable[[], None] | None = None,
        verify_ssl: bool = False,
    ) -> None:
        self._ha_base_url = ha_base_url.rstrip("/")
        self._ha_access_token = ha_access_token
        self._dispatch = dispatch
        self._ping_interval_s = ping_interval_s
        self._pong_timeout_s = pong_timeout_s
        self._reconnect_initial_s = reconnect_initial_s
        self._reconnect_max_s = reconnect_max_s
        self._reconnect_jitter = reconnect_jitter
        self._on_connected = on_connected
        self._on_disconnected = on_disconnected
        self._on_reconnect_failed = on_reconnect_failed
        self._verify_ssl = verify_ssl

        # Internal state
        self._shutdown: bool = False
        self._connected: bool = False
        self._ws_connection: Any | None = None  # aiohttp.ClientWebSocketResponse
        self._ws_session: Any | None = None  # aiohttp.ClientSession
        self._ws_cmd_id: int = 0
        self._ws_pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._last_pong_time: float = 0.0

        # Background tasks
        self._loop_task: asyncio.Task[None] | None = None
        self._ping_task: asyncio.Task[None] | None = None

        self._stop_event: asyncio.Event | None = None

    # ------------------------------------------------------------------
    # WebSocket URL derivation
    # ------------------------------------------------------------------

    def _ws_url(self) -> str:
        """Derive the WebSocket URL from the HA base HTTP URL.

        Converts ``http://`` → ``ws://`` and ``https://`` → ``wss://``.

        Returns:
            HA WebSocket API endpoint URL.
        """
        base = self._ha_base_url
        if base.startswith("https://"):
            return base.replace("https://", "wss://", 1) + "/api/websocket"
        return base.replace("http://", "ws://", 1) + "/api/websocket"

    # ------------------------------------------------------------------
    # Task 3.2 — Authentication handshake
    # ------------------------------------------------------------------

    async def _connect(self) -> None:
        """Open WebSocket connection and complete the HA auth handshake (task 3.2).

        HA WebSocket auth flow:
        1. Server sends: ``{"type": "auth_required", "ha_version": "..."}``
        2. Client sends: ``{"type": "auth", "access_token": "..."}``
        3. Server replies: ``{"type": "auth_ok"}`` or ``{"type": "auth_invalid"}``

        After auth_ok, sends supported_features with ``coalesce_messages=1``.

        Raises:
            RuntimeError: If the server returns ``auth_invalid`` or an
                unexpected message type.
        """
        import aiohttp

        ws_url = self._ws_url()
        logger.debug("HAWebSocketClient: connecting to %s", ws_url)

        if self._ws_session is None or self._ws_session.closed:
            ssl_ctx: bool = self._verify_ssl
            connector = aiohttp.TCPConnector(ssl=ssl_ctx)
            self._ws_session = aiohttp.ClientSession(connector=connector)

        self._ws_connection = await self._ws_session.ws_connect(
            ws_url,
            heartbeat=None,  # custom keepalive via ping/pong task
        )

        try:
            # Step 1: expect auth_required
            msg = await self._ws_connection.receive_json(timeout=10.0)
            if msg.get("type") != "auth_required":
                raise RuntimeError(
                    f"HAWebSocketClient: expected auth_required, got: {msg.get('type')!r}"
                )

            # Step 2: send auth
            await self._ws_connection.send_json(
                {"type": "auth", "access_token": self._ha_access_token}
            )

            # Step 3: expect auth_ok or auth_invalid
            msg = await self._ws_connection.receive_json(timeout=10.0)
            msg_type = msg.get("type")
            if msg_type == "auth_invalid":
                raise RuntimeError(
                    "HAWebSocketClient: authentication failed (auth_invalid). "
                    "Check the HA_ACCESS_TOKEN or CredentialStore."
                )
            if msg_type != "auth_ok":
                raise RuntimeError(f"HAWebSocketClient: unexpected auth response: {msg_type!r}")
        except Exception:
            await self._close_connection()
            raise

        logger.debug(
            "HAWebSocketClient: authenticated (ha_version=%s)",
            msg.get("ha_version", "unknown"),
        )

        # Send supported_features — enables coalesce_messages optimisation
        self._ws_cmd_id += 1
        await self._ws_connection.send_json(
            {
                "type": "supported_features",
                "id": self._ws_cmd_id,
                "features": {"coalesce_messages": 1},
            }
        )

        self._connected = True
        self._last_pong_time = asyncio.get_running_loop().time()
        logger.info("HAWebSocketClient: WebSocket connected and authenticated.")

    async def _close_connection(self) -> None:
        """Close the current WebSocket connection gracefully."""
        if self._ws_connection is not None and not self._ws_connection.closed:
            try:
                await self._ws_connection.close()
            except Exception:
                pass
        self._ws_connection = None
        self._connected = False

    # ------------------------------------------------------------------
    # Task 3.3 — Event subscription
    # ------------------------------------------------------------------

    async def _subscribe_events(self) -> None:
        """Subscribe to HA event types defined in ``_WS_EVENT_SUBSCRIPTIONS`` (task 3.3).

        Sends a ``subscribe_events`` WS command for each event type.
        No-op when not connected.
        """
        if not self._connected:
            return

        for event_type in _WS_EVENT_SUBSCRIPTIONS:
            try:
                await self._ws_command(
                    {"type": "subscribe_events", "event_type": event_type},
                    timeout=5.0,
                )
                logger.debug("HAWebSocketClient: subscribed to %s", event_type)
            except Exception as exc:
                logger.warning("HAWebSocketClient: failed to subscribe to %s: %r", event_type, exc)

    # ------------------------------------------------------------------
    # Task 3.4 — Ping/pong keepalive
    # ------------------------------------------------------------------

    def _start_ping_task(self) -> None:
        """Start the keepalive ping task as a background asyncio task (task 3.4)."""
        if self._ping_task is not None and not self._ping_task.done():
            return
        self._ping_task = asyncio.create_task(self._ping_loop())

    async def _ping_loop(self) -> None:
        """Send keepalive pings and detect missed pongs (task 3.4).

        Sends ``{"type": "ping"}`` every ``ping_interval_s`` seconds.  If no
        pong arrives within ``pong_timeout_s`` seconds after a ping is sent,
        the connection is treated as dead and the loop exits so the outer
        reconnect loop can take over.
        """
        try:
            while not self._shutdown:
                await asyncio.sleep(self._ping_interval_s)
                if self._shutdown:
                    break
                if not self._connected or self._ws_connection is None:
                    break

                ping_sent_at = asyncio.get_running_loop().time()
                try:
                    self._ws_cmd_id += 1
                    await self._ws_connection.send_json({"type": "ping", "id": self._ws_cmd_id})
                    logger.debug("HAWebSocketClient: ping sent (id=%d)", self._ws_cmd_id)
                except Exception as exc:
                    logger.warning("HAWebSocketClient: failed to send ping: %s", exc)
                    break

                # Wait for pong to arrive
                await asyncio.sleep(self._pong_timeout_s)
                if self._last_pong_time < ping_sent_at:
                    logger.warning(
                        "HAWebSocketClient: missed pong after %ds; treating connection as dead.",
                        self._pong_timeout_s,
                    )
                    await self._close_connection()
                    break

        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.warning("HAWebSocketClient: ping loop error: %s", exc)

        if not self._shutdown:
            self._connected = False
            if self._on_disconnected is not None:
                self._on_disconnected()

    # ------------------------------------------------------------------
    # WebSocket message loop
    # ------------------------------------------------------------------

    def _start_message_loop(self) -> None:
        """Start the WebSocket message dispatch loop as a background task."""
        if self._loop_task is not None and not self._loop_task.done():
            return
        self._loop_task = asyncio.create_task(self._message_loop())

    async def _message_loop(self) -> None:
        """Read messages from the WebSocket and dispatch by type.

        Dispatches (task 3.6):
        - ``event``: parse and call the ``_dispatch`` callback
        - ``result``: correlate with pending WS command futures
        - ``pong``: update ``_last_pong_time``

        On any connection error, triggers reconnect.
        """
        import aiohttp

        try:
            while not self._shutdown:
                if self._ws_connection is None or self._ws_connection.closed:
                    break

                try:
                    raw = await self._ws_connection.receive(timeout=5.0)
                except TimeoutError:
                    continue

                if raw.type == aiohttp.WSMsgType.TEXT:
                    try:
                        msg: dict[str, Any] = json.loads(raw.data)
                    except json.JSONDecodeError:
                        logger.warning("HAWebSocketClient: invalid JSON: %r", raw.data[:200])
                        continue
                    if not isinstance(msg, dict):
                        continue
                    await self._dispatch_message(msg)

                elif raw.type == aiohttp.WSMsgType.BINARY:
                    try:
                        msg = json.loads(raw.data)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        logger.warning("HAWebSocketClient: invalid binary message")
                        continue
                    if not isinstance(msg, dict):
                        continue
                    await self._dispatch_message(msg)

                elif raw.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.ERROR,
                    aiohttp.WSMsgType.CLOSED,
                ):
                    logger.warning("HAWebSocketClient: WebSocket closed/error (type=%s)", raw.type)
                    break

        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.warning("HAWebSocketClient: message loop error: %s", exc)

        if not self._shutdown:
            self._connected = False
            if self._on_disconnected is not None:
                self._on_disconnected()

    # ------------------------------------------------------------------
    # Task 3.6 — Message parsing and dispatch
    # ------------------------------------------------------------------

    async def _dispatch_message(self, msg: dict[str, Any]) -> None:
        """Parse and dispatch a single WebSocket message (task 3.6).

        Args:
            msg: Parsed JSON message dict from HA WebSocket.
        """
        msg_type = msg.get("type")

        if msg_type == "event":
            event = msg.get("event", {})
            event_type = event.get("event_type", "")
            try:
                await self._dispatch(event_type, event)
            except Exception as exc:
                logger.warning(
                    "HAWebSocketClient: event dispatch error for %r: %s", event_type, exc
                )

        elif msg_type == "result":
            self._handle_result(msg)

        elif msg_type == "pong":
            self._last_pong_time = asyncio.get_running_loop().time()
            logger.debug("HAWebSocketClient: pong received")

        else:
            logger.debug("HAWebSocketClient: unhandled message type %r", msg_type)

    def _handle_result(self, msg: dict[str, Any]) -> None:
        """Correlate a WS result message with a pending command future."""
        cmd_id = msg.get("id")
        if cmd_id is None:
            return
        fut = self._ws_pending.pop(cmd_id, None)
        if fut is None or fut.done():
            return
        if msg.get("success"):
            fut.set_result(msg.get("result", {}))
        else:
            error = msg.get("error", {})
            fut.set_exception(
                RuntimeError(
                    f"HAWebSocketClient: WS command {cmd_id} failed: "
                    f"{error.get('code')!r} — {error.get('message')!r}"
                )
            )

    # ------------------------------------------------------------------
    # WebSocket command helper
    # ------------------------------------------------------------------

    async def _ws_command(
        self,
        command: dict[str, Any],
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        """Send a WebSocket command and await the correlated result.

        Args:
            command: Command dict (``type`` + payload). The ``id`` field
                will be overwritten with an auto-incrementing value.
            timeout: Seconds to wait before raising ``asyncio.TimeoutError``.

        Returns:
            The ``result`` payload from the HA response.

        Raises:
            RuntimeError: If WebSocket is not connected, or HA returns an error.
            asyncio.TimeoutError: If response doesn't arrive within ``timeout``.
        """
        if self._ws_connection is None or not self._connected:
            raise RuntimeError("HAWebSocketClient: not connected — cannot send command")

        self._ws_cmd_id += 1
        cmd_id = self._ws_cmd_id
        command = dict(command)
        command["id"] = cmd_id

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._ws_pending[cmd_id] = fut

        try:
            await self._ws_connection.send_json(command)
            return await asyncio.wait_for(fut, timeout=timeout)
        except Exception:
            self._ws_pending.pop(cmd_id, None)
            raise

    # ------------------------------------------------------------------
    # Task 3.5 — Exponential backoff reconnection
    # ------------------------------------------------------------------

    async def _reconnect_loop(self) -> None:
        """Attempt WebSocket reconnection with exponential backoff (task 3.5).

        Backoff: starts at ``reconnect_initial_s``, doubles on each failure,
        capped at ``reconnect_max_s``.  Jitter of ±(delay × jitter_fraction)
        is added to avoid thundering herds.

        On success: re-subscribes to events and restarts background tasks.
        """
        delay = self._reconnect_initial_s
        attempt = 0

        while not self._shutdown and not self._connected:
            jitter = delay * self._reconnect_jitter * (2 * random.random() - 1)
            sleep_time = max(0.1, delay + jitter)
            logger.info(
                "HAWebSocketClient: reconnect attempt %d in %.1fs",
                attempt + 1,
                sleep_time,
            )
            await asyncio.sleep(sleep_time)

            if self._shutdown:
                break

            try:
                await self._connect()
            except Exception as exc:
                logger.warning(
                    "HAWebSocketClient: reconnect attempt %d failed: %s",
                    attempt + 1,
                    exc,
                )
                # Signal the failed reconnect attempt so the connector can count
                # consecutive failures and activate the REST fallback per spec.
                # Guard the callback: this loop runs as a fire-and-forget task,
                # so an unhandled callback error (e.g. while starting the REST
                # poller) would otherwise kill reconnection entirely.
                if self._on_reconnect_failed is not None:
                    try:
                        self._on_reconnect_failed()
                    except Exception as callback_exc:
                        logger.exception(
                            "HAWebSocketClient: error in on_reconnect_failed callback: %s",
                            callback_exc,
                        )
                delay = min(delay * 2, self._reconnect_max_s)
                attempt += 1
                continue

            # Reconnected — restart background tasks and re-subscribe
            logger.info("HAWebSocketClient: reconnected after %d attempt(s)", attempt + 1)
            self._start_message_loop()
            self._start_ping_task()

            try:
                await self._subscribe_events()
            except Exception as exc:
                logger.warning(
                    "HAWebSocketClient: error subscribing events after reconnect: %s", exc
                )

            if self._on_connected is not None:
                self._on_connected()

            break

    # ------------------------------------------------------------------
    # Task 3.1 — Process lifecycle: start / run / stop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Run the WebSocket client until ``stop()`` is called (task 3.1).

        Connects, authenticates, subscribes to events, and starts background
        tasks.  On connection failure, uses exponential backoff to retry.
        Blocks until the stop event is set.
        """
        self._shutdown = False
        self._stop_event = asyncio.Event()

        # Initial connection attempt
        try:
            await self._connect()
        except Exception as exc:
            logger.warning("HAWebSocketClient: initial connect failed (%s); will retry.", exc)
            if self._on_disconnected is not None:
                self._on_disconnected()
            # Start reconnect loop in background; don't block run()
            asyncio.create_task(self._reconnect_loop())
        else:
            self._start_message_loop()
            self._start_ping_task()
            try:
                await self._subscribe_events()
            except Exception as exc:
                logger.warning("HAWebSocketClient: event subscription error: %s", exc)
            if self._on_connected is not None:
                self._on_connected()

        # Main supervision loop: detect disconnects and reconnect
        try:
            while not self._shutdown:
                await asyncio.sleep(5.0)
                if self._shutdown:
                    break
                if not self._connected:
                    # Spawn reconnect loop if not already running
                    if not any(
                        t is not None and not t.done() for t in (self._loop_task, self._ping_task)
                    ):
                        asyncio.create_task(self._reconnect_loop())
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Gracefully stop the client (task 3.1).

        Cancels background tasks, closes the WebSocket connection, and
        closes the aiohttp session.
        """
        self._shutdown = True

        # Cancel background tasks
        for task in (self._loop_task, self._ping_task):
            if task is not None and not task.done():
                task.cancel()
        pending = [t for t in (self._loop_task, self._ping_task) if t is not None]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._loop_task = None
        self._ping_task = None

        # Fail pending WS commands
        for fut in self._ws_pending.values():
            if not fut.done():
                fut.cancel()
        self._ws_pending.clear()

        # Close connection and session
        await self._close_connection()
        if self._ws_session is not None:
            try:
                await self._ws_session.close()
            except Exception:
                pass
            self._ws_session = None

        logger.info("HAWebSocketClient: stopped.")

        if self._stop_event is not None:
            self._stop_event.set()


# ---------------------------------------------------------------------------
# Event reordering buffer (connector-home-assistant spec — "Event ordering")
# ---------------------------------------------------------------------------


class HAEventReorderBuffer:
    """Bounded, time-windowed reorder buffer for HA WebSocket events.

    HA delivers ``state_changed`` / ``automation_triggered`` events in roughly
    chronological order, but during HA-internal batching a burst can arrive
    slightly out of ``time_fired`` order. The connector contract
    (connector-home-assistant spec, "Event ordering") requires events to be
    submitted in ``time_fired`` order, so this buffer briefly holds incoming
    events, orders them by ``time_fired``, and releases them in order once they
    have aged past a short reorder window.

    Ordering
        Events are held in a min-heap keyed by ``(time_fired, arrival_seq)``.
        ``arrival_seq`` is a monotonic counter that keeps the order stable for
        events sharing a ``time_fired`` (they retain arrival order). The head of
        the heap is therefore always the earliest buffered event. An event is
        released only once it has been held for at least ``window_s``, which
        gives a late-but-earlier event time to arrive and slot ahead of it.

    Bound (HA_EVENT_QUEUE_MAX)
        The buffer never holds more than ``max_size`` events. When a new event
        would exceed the bound, the earliest-buffered event is released
        immediately (forced flush of the head). Overflow therefore applies
        backpressure by draining the oldest event rather than dropping it, which
        preserves the connector's at-least-once delivery guarantee.

    Events with a missing or unparseable ``time_fired`` cannot be ordered; they
    are keyed to sort after all well-formed events and are released on window
    age-out or final flush. Downstream dispatch performs their validation.
    """

    def __init__(
        self,
        *,
        max_size: int,
        submit: _EventDispatch,
        window_s: float = _DEFAULT_EVENT_REORDER_WINDOW_S,
        time_source: Callable[[], float] | None = None,
    ) -> None:
        self._max_size = max(1, int(max_size))
        self._window_s = max(0.0, float(window_s))
        self._submit = submit
        self._time_source = time_source
        # Heap entries:
        #   (order_key, arrival_seq, arrival_time, event_type, event, transport)
        self._heap: list[tuple[float, int, float, str, dict[str, Any], str | None]] = []
        self._seq = 0

    @property
    def flush_interval_s(self) -> float:
        """Suggested cadence for a background ``flush_due`` driver."""
        return max(0.05, self._window_s / 2)

    def __len__(self) -> int:
        return len(self._heap)

    def _now(self) -> float:
        if self._time_source is not None:
            return self._time_source()
        return asyncio.get_running_loop().time()

    @staticmethod
    def _order_key(event: dict[str, Any]) -> float:
        time_fired = event.get("time_fired", "") if isinstance(event, dict) else ""
        if not time_fired:
            return float("inf")
        try:
            return parse_time_fired(time_fired).timestamp()
        except Exception:
            return float("inf")

    async def add(
        self, event_type: str, event: dict[str, Any], transport: str | None = None
    ) -> None:
        """Buffer an incoming event, draining the head if the bound is exceeded.

        ``transport`` is threaded through to the wrapped dispatch so events
        polled via the REST fallback keep their ``rest_fallback`` checkpoint
        tag; WebSocket events pass ``None`` and let the dispatcher infer it.
        """
        heapq.heappush(
            self._heap,
            (
                self._order_key(event),
                self._seq,
                self._now(),
                event_type,
                event,
                transport,
            ),
        )
        self._seq += 1
        while len(self._heap) > self._max_size:
            await self._release_head()

    async def flush_due(self) -> None:
        """Release every event that has aged past the reorder window, in order."""
        now = self._now()
        while self._heap and (now - self._heap[0][2]) >= self._window_s:
            await self._release_head()

    async def flush_all(self) -> None:
        """Release all buffered events in ``time_fired`` order (shutdown drain)."""
        while self._heap:
            await self._release_head()

    async def _release_head(self) -> None:
        _, _, _, event_type, event, transport = heapq.heappop(self._heap)
        await self._submit(event_type, event, transport)


# ---------------------------------------------------------------------------
# HA-specific Prometheus metrics (task 8.3–8.5)
# ---------------------------------------------------------------------------

# --- Counters (task 8.3) ---

ha_events_total = Counter(
    "connector_ha_events_total",
    "Total events processed at each filter stage",
    labelnames=["stage", "outcome"],
)
"""stage: domain_filter | significance_filter | discretion; outcome: passed | filtered"""

ha_ws_reconnects_total = Counter(
    "connector_ha_ws_reconnects_total",
    "Total WebSocket reconnection attempts",
    labelnames=["endpoint_identity"],
)

ha_rest_polls_total = Counter(
    "connector_ha_rest_polls_total",
    "Total REST fallback poll outcomes",
    labelnames=["endpoint_identity", "status"],
)
"""status: success | error"""

ha_discretion_total = Counter(
    "connector_ha_discretion_total",
    "Total discretion evaluation verdicts",
    labelnames=["endpoint_identity", "verdict"],
)
"""verdict: forward | ignore | error_forward"""

ha_wellness_classify_total = Counter(
    "connector_ha_wellness_classify_total",
    "Wellness classifier outcomes for filter-passing events",
    labelnames=["endpoint_identity", "outcome", "metric"],
)
"""outcome: promoted | no_match | skipped_non_numeric | denylisted.

metric: matched metric name, or 'none' when not promoted."""

ha_submissions_total = Counter(
    "connector_ha_submissions_total",
    "Switchboard ingest submissions by channel and status",
    labelnames=["endpoint_identity", "channel", "status"],
)
"""channel: home_assistant | wellness; status: success | error"""

# --- Gauges (task 8.4) ---

ha_filter_pass_rate = Gauge(
    "connector_ha_filter_pass_rate",
    "Ratio of events forwarded vs. total received (0.0–1.0)",
    labelnames=["endpoint_identity"],
)

ha_transport_mode = Gauge(
    "connector_ha_transport_mode",
    "Current transport mode: 1=websocket, 0=rest_fallback",
    labelnames=["endpoint_identity"],
)

ha_entities_tracked = Gauge(
    "connector_ha_entities_tracked",
    "Number of distinct entities from allowed domains seen",
    labelnames=["endpoint_identity"],
)

# --- Histograms (task 8.5) ---

_LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)

ha_event_latency_seconds = Histogram(
    "connector_ha_event_latency_seconds",
    "Time from HA event time_fired to Switchboard submission",
    labelnames=["endpoint_identity"],
    buckets=_LATENCY_BUCKETS,
)

ha_filter_pipeline_seconds = Histogram(
    "connector_ha_filter_pipeline_seconds",
    "Time spent in the three-layer filter pipeline",
    labelnames=["endpoint_identity"],
    buckets=_LATENCY_BUCKETS,
)


# ---------------------------------------------------------------------------
# HA-specific metrics helper (task 8.3–8.5)
# ---------------------------------------------------------------------------


class HAConnectorMetrics:
    """Per-endpoint metrics helper for the HA connector.

    Wraps the module-level Prometheus objects with a fixed ``endpoint_identity``
    label so call sites do not need to repeat it.
    """

    def __init__(self, endpoint_identity: str) -> None:
        self._eid = endpoint_identity

    # --- Counters ---

    def inc_events(self, stage: str, outcome: str) -> None:
        """Increment event filter stage counter.

        Args:
            stage: One of ``domain_filter``, ``significance_filter``, ``discretion``.
            outcome: One of ``passed``, ``filtered``.
        """
        ha_events_total.labels(stage=stage, outcome=outcome).inc()

    def inc_ws_reconnect(self) -> None:
        """Increment WebSocket reconnection attempt counter."""
        ha_ws_reconnects_total.labels(endpoint_identity=self._eid).inc()

    def inc_rest_poll(self, status: str) -> None:
        """Increment REST poll counter.

        Args:
            status: One of ``success``, ``error``.
        """
        ha_rest_polls_total.labels(endpoint_identity=self._eid, status=status).inc()

    def inc_discretion(self, verdict: str) -> None:
        """Increment discretion verdict counter.

        Args:
            verdict: One of ``forward``, ``ignore``, ``error_forward``.
        """
        ha_discretion_total.labels(endpoint_identity=self._eid, verdict=verdict).inc()

    def inc_wellness_classify(self, outcome: str, metric: str | None) -> None:
        """Record a wellness classifier outcome.

        Args:
            outcome: One of ``promoted``, ``no_match``, ``skipped_non_numeric``,
                ``denylisted``.
            metric: Matched metric name, or ``None`` (recorded as ``"none"``).
        """
        ha_wellness_classify_total.labels(
            endpoint_identity=self._eid,
            outcome=outcome,
            metric=metric or "none",
        ).inc()

    def inc_submission(self, channel: str, status: str) -> None:
        """Record a Switchboard submission outcome for a channel.

        Args:
            channel: One of ``home_assistant``, ``wellness``.
            status: One of ``success``, ``error``.
        """
        ha_submissions_total.labels(
            endpoint_identity=self._eid,
            channel=channel,
            status=status,
        ).inc()

    # --- Gauges ---

    def set_filter_pass_rate(self, rate: float) -> None:
        """Set the overall filter pass rate (0.0–1.0).

        Args:
            rate: Fraction of events that passed all three filter layers.
        """
        ha_filter_pass_rate.labels(endpoint_identity=self._eid).set(rate)

    def set_transport_mode(self, *, websocket: bool) -> None:
        """Set transport mode gauge.

        Args:
            websocket: True if currently using WebSocket, False for REST fallback.
        """
        ha_transport_mode.labels(endpoint_identity=self._eid).set(1.0 if websocket else 0.0)

    def set_entities_tracked(self, count: int) -> None:
        """Set the number of tracked entities.

        Args:
            count: Number of distinct allowed-domain entities seen.
        """
        ha_entities_tracked.labels(endpoint_identity=self._eid).set(count)

    # --- Histograms ---

    def observe_event_latency(self, latency_s: float) -> None:
        """Record end-to-end event latency (time_fired → submission).

        Args:
            latency_s: Elapsed time in seconds.
        """
        ha_event_latency_seconds.labels(endpoint_identity=self._eid).observe(latency_s)

    def observe_filter_pipeline(self, latency_s: float) -> None:
        """Record time spent in the three-layer filter pipeline.

        Args:
            latency_s: Elapsed time in seconds.
        """
        ha_filter_pipeline_seconds.labels(endpoint_identity=self._eid).observe(latency_s)


# ---------------------------------------------------------------------------
# Configuration (task 8 — connector config used by health/heartbeat/metrics)
# ---------------------------------------------------------------------------


@dataclass
class HAConnectorConfig:
    """Configuration for the Home Assistant connector runtime.

    Loaded from environment variables via ``from_env()``.  Credential values
    (base URL and access token) are resolved separately via CredentialStore.
    """

    switchboard_mcp_url: str
    provider: str = _CONNECTOR_PROVIDER
    channel: str = _CONNECTOR_CHANNEL
    health_port: int = _DEFAULT_HEALTH_PORT
    poll_interval_s: int = _DEFAULT_POLL_INTERVAL_S
    checkpoint_overlap_s: int = _DEFAULT_CHECKPOINT_OVERLAP_S
    ws_ping_interval_s: int = _DEFAULT_WS_PING_INTERVAL_S
    ws_pong_timeout_s: int = _DEFAULT_WS_PONG_TIMEOUT_S
    discretion_timeout_s: int = _DEFAULT_DISCRETION_TIMEOUT_S
    event_queue_max: int = _DEFAULT_EVENT_QUEUE_MAX
    domain_allowlist: frozenset[str] = field(
        default_factory=lambda: frozenset(_DEFAULT_DOMAIN_ALLOWLIST)
    )
    # Wellness promotion (home-assistant-wellness-promotion ADR-2)
    wellness_promotion_enabled: bool = True
    wellness_rules_extra: tuple[WellnessRule, ...] = ()
    wellness_entity_denylist: frozenset[str] = frozenset()

    @classmethod
    def from_env(cls) -> HAConnectorConfig:
        """Load connector configuration from environment variables.

        Raises:
            ValueError: If ``SWITCHBOARD_MCP_URL`` is not set, or if
                ``HA_WELLNESS_RULES_EXTRA`` holds malformed JSON / rules.
        """
        switchboard_mcp_url = os.environ.get("SWITCHBOARD_MCP_URL", "").strip()
        if not switchboard_mcp_url:
            raise ValueError("SWITCHBOARD_MCP_URL is required")

        def _int(key: str, default: int) -> int:
            raw = os.environ.get(key, "").strip()
            if not raw:
                return default
            try:
                return int(raw)
            except ValueError:
                logger.warning("Invalid %s=%r, using default %d", key, raw, default)
                return default

        raw_allowlist = os.environ.get("HA_DOMAIN_ALLOWLIST", "").strip()
        if raw_allowlist:
            domain_allowlist: frozenset[str] = frozenset(
                d.strip() for d in raw_allowlist.split(",") if d.strip()
            )
        else:
            domain_allowlist = frozenset(_DEFAULT_DOMAIN_ALLOWLIST)

        def _bool(key: str, default: bool) -> bool:
            raw = os.environ.get(key, "").strip().lower()
            if not raw:
                return default
            return raw in ("1", "true", "yes", "on")

        # Wellness promotion config (ADR-2). Malformed rules-extra fails fast at
        # startup with a clear error rather than silently disabling promotion.
        wellness_rules_extra = parse_rules_extra(os.environ.get("HA_WELLNESS_RULES_EXTRA", ""))

        raw_denylist = os.environ.get("HA_WELLNESS_ENTITY_DENYLIST", "").strip()
        wellness_entity_denylist = frozenset(
            d.strip() for d in raw_denylist.split(",") if d.strip()
        )

        return cls(
            switchboard_mcp_url=switchboard_mcp_url,
            provider=os.environ.get("CONNECTOR_PROVIDER", _CONNECTOR_PROVIDER),
            channel=os.environ.get("CONNECTOR_CHANNEL", _CONNECTOR_CHANNEL),
            health_port=_int("CONNECTOR_HEALTH_PORT", _DEFAULT_HEALTH_PORT),
            poll_interval_s=_int("HA_POLL_INTERVAL_S", _DEFAULT_POLL_INTERVAL_S),
            checkpoint_overlap_s=_int("HA_CHECKPOINT_OVERLAP_S", _DEFAULT_CHECKPOINT_OVERLAP_S),
            ws_ping_interval_s=_int("HA_WS_PING_INTERVAL_S", _DEFAULT_WS_PING_INTERVAL_S),
            ws_pong_timeout_s=_int("HA_WS_PONG_TIMEOUT_S", _DEFAULT_WS_PONG_TIMEOUT_S),
            discretion_timeout_s=_int("HA_DISCRETION_TIMEOUT_S", _DEFAULT_DISCRETION_TIMEOUT_S),
            event_queue_max=_int("HA_EVENT_QUEUE_MAX", _DEFAULT_EVENT_QUEUE_MAX),
            domain_allowlist=domain_allowlist,
            wellness_promotion_enabled=_bool("HA_WELLNESS_PROMOTION_ENABLED", True),
            wellness_rules_extra=wellness_rules_extra,
            wellness_entity_denylist=wellness_entity_denylist,
        )


# ---------------------------------------------------------------------------
# Connector class — health, heartbeat, metrics (task 8)
# ---------------------------------------------------------------------------


class HAConnector:
    """Home Assistant connector.

    Manages the full lifecycle: startup, WebSocket event streaming, REST
    polling fallback, three-layer filtering, Switchboard submission,
    checkpoint persistence, heartbeat, health endpoint, and graceful shutdown.

    Health state derivation (task 8.1):
    - ``error``    — HA unreachable and REST fallback also failing
    - ``degraded`` — WebSocket down but REST polling active, or discretion LLM unavailable
    - ``healthy``  — WebSocket connected and all pipeline services responsive
    - ``starting`` — Process started, not yet connected

    Transport mode in heartbeat (task 8.2):
    ``status.error_message`` includes the current transport mode:
    ``"transport=websocket"`` or ``"transport=rest_fallback, ws_reconnect_attempts=N"``
    """

    def __init__(
        self,
        config: HAConnectorConfig,
    ) -> None:
        self._config = config

        # Will be set after credential resolution
        self._endpoint_identity: str = ""
        self._ha_base_url: str | None = None
        self._ha_access_token: str | None = None

        # MCP client
        self._mcp_client = CachedMCPClient(
            config.switchboard_mcp_url,
            client_name="ha-connector",
        )

        # Transport state (task 8.1)
        self._ws_connected: bool = False
        self._rest_fallback_active: bool = False
        self._ws_reconnect_attempts: int = 0
        self._rest_poll_failing: bool = False  # True when last REST poll failed
        self._discretion_available: bool = True
        self._starting: bool = True  # Cleared once first connection attempt completes

        # Filter tracking for pass-rate gauge (task 8.4)
        self._total_events_received: int = 0
        self._total_events_forwarded: int = 0

        # Standard connector metrics
        self._base_metrics = ConnectorMetrics(
            connector_type=_CONNECTOR_TYPE,
            endpoint_identity="",  # Updated after identity resolution
        )

        # HA-specific metrics (task 8.3–8.5)
        self._ha_metrics: HAConnectorMetrics | None = None

        # Heartbeat
        self._heartbeat: ConnectorHeartbeat | None = None

        # Health server
        self._health_server: Any | None = None
        self._health_thread: Thread | None = None

        # Timing
        self._start_time = time.time()

    # ------------------------------------------------------------------
    # Identity resolution helper
    # ------------------------------------------------------------------

    @staticmethod
    def _derive_endpoint_identity(ha_base_url: str) -> str:
        """Derive endpoint_identity from the HA base URL.

        Per spec: ``"home_assistant:<ha_host>:<ha_port>"``.

        Args:
            ha_base_url: The HA instance base URL, e.g. ``"http://ha.local:8123"``.

        Returns:
            Endpoint identity string.
        """
        parsed = urlparse(ha_base_url)
        host = parsed.hostname or "unknown"
        port = parsed.port or (443 if parsed.scheme == "https" else 8123)
        return f"home_assistant:{host}:{port}"

    def _set_endpoint_identity(self, ha_base_url: str) -> None:
        """Set endpoint identity and initialize per-endpoint metrics/heartbeat.

        Call this once the HA base URL is resolved from CredentialStore or env.

        Args:
            ha_base_url: The HA instance base URL.
        """
        self._ha_base_url = ha_base_url
        self._endpoint_identity = self._derive_endpoint_identity(ha_base_url)

        # Re-create metrics with correct identity
        self._base_metrics = ConnectorMetrics(
            connector_type=_CONNECTOR_TYPE,
            endpoint_identity=self._endpoint_identity,
        )
        self._ha_metrics = HAConnectorMetrics(self._endpoint_identity)

        # Initialize transport mode gauge to websocket (optimistic)
        self._ha_metrics.set_transport_mode(websocket=True)

        # Initialize heartbeat
        heartbeat_config = HeartbeatConfig.from_env(
            connector_type=_CONNECTOR_TYPE,
            endpoint_identity=self._endpoint_identity,
        )
        self._heartbeat = ConnectorHeartbeat(
            config=heartbeat_config,
            mcp_client=self._mcp_client,
            metrics=self._base_metrics,
            get_health_state=self._get_health_state,
        )

        logger.info(
            "HAConnector: initialized endpoint_identity=%s",
            self._endpoint_identity,
        )

    # ------------------------------------------------------------------
    # Health state derivation (task 8.1)
    # ------------------------------------------------------------------

    def _get_health_state(self) -> tuple[str, str | None]:
        """Derive current health state for heartbeat reporting.

        Returns:
            ``(state, error_message)`` where state is one of:
            - ``"healthy"``  — WebSocket connected, discretion available
            - ``"degraded"`` — WS down but REST active, or discretion unavailable
            - ``"error"``    — HA unreachable and REST also failing
            - ``"starting"`` — Process started, no connection yet
        """
        if self._starting:
            return ("starting", "transport=starting")

        # Task 8.2: include transport mode in error_message
        transport_msg = self._build_transport_message()

        # Error: HA entirely unreachable (WS down AND REST failing)
        if not self._ws_connected and self._rest_fallback_active and self._rest_poll_failing:
            return ("error", f"HA unreachable — {transport_msg}")

        # Error: WS never connected, REST fallback also not working
        if not self._ws_connected and not self._rest_fallback_active and self._rest_poll_failing:
            return ("error", f"HA unreachable — {transport_msg}")

        # Degraded: WS down but REST polling active
        if not self._ws_connected and self._rest_fallback_active:
            return ("degraded", f"WebSocket disconnected, using REST fallback — {transport_msg}")

        # Degraded: discretion LLM unavailable
        if self._ws_connected and not self._discretion_available:
            return ("degraded", f"Discretion LLM unavailable — {transport_msg}")

        # Healthy: WebSocket connected
        if self._ws_connected:
            return ("healthy", transport_msg)

        # Fallback: WS not connected, no REST fallback
        return ("degraded", f"WebSocket disconnected — {transport_msg}")

    def _build_transport_message(self) -> str:
        """Build a transport mode description string for heartbeat error_message.

        Per spec §8 task 8.2:
        - ``"transport=websocket"`` when WS is active
        - ``"transport=rest_fallback, ws_reconnect_attempts=N"`` when on REST fallback

        Returns:
            Human-readable transport mode string.
        """
        if self._ws_connected:
            return "transport=websocket"
        if self._rest_fallback_active:
            return f"transport=rest_fallback, ws_reconnect_attempts={self._ws_reconnect_attempts}"
        return f"transport=disconnected, ws_reconnect_attempts={self._ws_reconnect_attempts}"

    # ------------------------------------------------------------------
    # Transport state mutators (called by WS/REST tasks)
    # ------------------------------------------------------------------

    def on_ws_connected(self) -> None:
        """Called when WebSocket authentication succeeds."""
        self._ws_connected = True
        self._rest_fallback_active = False
        self._starting = False
        if self._ha_metrics is not None:
            self._ha_metrics.set_transport_mode(websocket=True)

    def on_ws_disconnected(self) -> None:
        """Called when WebSocket connection drops or authentication fails."""
        self._ws_connected = False
        self._ws_reconnect_attempts += 1
        if self._ha_metrics is not None:
            self._ha_metrics.inc_ws_reconnect()
            self._ha_metrics.set_transport_mode(websocket=False)

    def on_rest_fallback_started(self) -> None:
        """Called when REST polling fallback is activated."""
        self._rest_fallback_active = True
        self._starting = False

    def on_rest_fallback_stopped(self) -> None:
        """Called when REST polling fallback is deactivated (WS reconnected)."""
        self._rest_fallback_active = False
        self._rest_poll_failing = False

    def on_rest_poll_success(self) -> None:
        """Called after a successful REST poll cycle."""
        self._rest_poll_failing = False
        if self._ha_metrics is not None:
            self._ha_metrics.inc_rest_poll(status="success")

    def on_rest_poll_error(self) -> None:
        """Called after a failed REST poll cycle."""
        self._rest_poll_failing = True
        if self._ha_metrics is not None:
            self._ha_metrics.inc_rest_poll(status="error")

    def on_event_received(self, *, passed_all_filters: bool) -> None:
        """Update filter pass-rate tracking.

        Called after each event is processed through the filter pipeline.

        Args:
            passed_all_filters: True if the event survived all three filter layers.
        """
        self._total_events_received += 1
        if passed_all_filters:
            self._total_events_forwarded += 1

        if self._ha_metrics is not None and self._total_events_received > 0:
            rate = self._total_events_forwarded / self._total_events_received
            self._ha_metrics.set_filter_pass_rate(rate)

    def on_entities_tracked_update(self, count: int) -> None:
        """Update the entities-tracked gauge.

        Args:
            count: Number of distinct allowed-domain entities seen so far.
        """
        if self._ha_metrics is not None:
            self._ha_metrics.set_entities_tracked(count)

    def on_discretion_result(self, *, available: bool, verdict: str | None = None) -> None:
        """Update discretion availability and record verdict.

        Args:
            available: Whether the discretion LLM responded successfully.
            verdict: The discretion verdict (``"forward"``, ``"ignore"``,
                ``"error_forward"``).  Required when ``available=True``.
        """
        self._discretion_available = available
        if self._ha_metrics is not None and verdict is not None:
            self._ha_metrics.inc_discretion(verdict=verdict)

    # ------------------------------------------------------------------
    # Health HTTP server (task 8 — reuses pattern from other connectors)
    # ------------------------------------------------------------------

    def start_health_server(self) -> None:
        """Start the health/metrics HTTP server in a background thread.

        Serves:
        - ``GET /health`` — JSON health status
        - ``GET /metrics`` — Prometheus text exposition
        """
        import uvicorn
        from fastapi import FastAPI

        app = FastAPI(title="ha-connector-health")

        connector = self  # capture

        @app.get("/health")
        async def health() -> dict[str, Any]:
            state, error = connector._get_health_state()
            uptime_s = int(time.time() - connector._start_time)
            return {
                "status": state,
                "connector_type": _CONNECTOR_TYPE,
                "endpoint_identity": connector._endpoint_identity,
                "uptime_seconds": uptime_s,
                "ws_connected": connector._ws_connected,
                "rest_fallback_active": connector._rest_fallback_active,
                "ws_reconnect_attempts": connector._ws_reconnect_attempts,
                "error": error,
            }

        @app.get("/metrics")
        async def metrics() -> bytes:
            return generate_latest()

        port = self._config.health_port
        try:
            sock = make_health_socket("127.0.0.1", port)
        except Exception as exc:
            logger.warning("HAConnector: could not bind health socket on port %d: %s", port, exc)
            return

        uvicorn_config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
        )
        self._health_server = uvicorn.Server(uvicorn_config)

        def _run_server() -> None:
            asyncio.run(self._health_server.serve(sockets=[sock]))

        self._health_thread = Thread(target=_run_server, daemon=True)
        self._health_thread.start()
        logger.info("HAConnector: health server started on port %d", port)

    # ------------------------------------------------------------------
    # Heartbeat lifecycle
    # ------------------------------------------------------------------

    def start_heartbeat(self) -> None:
        """Start the Switchboard heartbeat background task.

        Must be called after ``_set_endpoint_identity()`` so the heartbeat
        config has the correct connector_type and endpoint_identity.
        """
        if self._heartbeat is None:
            logger.warning("HAConnector: cannot start heartbeat — not yet initialized")
            return
        self._heartbeat.start()
        logger.info(
            "HAConnector: heartbeat started for endpoint_identity=%s",
            self._endpoint_identity,
        )

    async def stop_heartbeat(self) -> None:
        """Stop the Switchboard heartbeat background task."""
        if self._heartbeat is not None:
            await self._heartbeat.stop()


# ---------------------------------------------------------------------------
# Evidence table persistence — connectors.home_assistant_history (task 8)
# ---------------------------------------------------------------------------

_HA_HISTORY_TABLE = "connectors.home_assistant_history"


async def persist_ha_history(
    pool: Any,
    *,
    entity_id: str,
    state: str | None,
    attributes: dict[str, Any] | None,
    recorded_at: Any,
) -> bool:
    """Insert one row into ``connectors.home_assistant_history``.

    Idempotency: the table has no unique constraint on (entity_id, recorded_at)
    by design — each HA event produces a distinct row because ``recorded_at``
    carries millisecond precision from ``time_fired``.  Duplicate suppression
    happens via the Switchboard's idempotency key before this function is
    called.

    Errors are caught and logged; a write failure does NOT abort the ingest
    submission path.  Returns ``True`` when the row was inserted, ``False``
    on error.

    Args:
        pool: asyncpg connection pool (``connector_writer`` role).
        entity_id: HA entity ID (e.g. ``"person.tzeusy"``).
        state: New state value after the transition (may be ``None``).
        attributes: JSONB snapshot of HA event attributes (may be ``None``).
        recorded_at: Timezone-aware datetime for the ``recorded_at`` column.
    """
    try:
        await pool.execute(
            f"""
            INSERT INTO {_HA_HISTORY_TABLE} (entity_id, state, attributes, recorded_at)
            VALUES ($1, $2, $3, $4)
            """,
            entity_id,
            state,
            attributes,
            recorded_at,
        )
        return True
    except Exception:
        logger.warning(
            "ha-connector: failed to persist history row for entity_id=%s (non-fatal)",
            entity_id,
            exc_info=True,
        )
        return False


# ---------------------------------------------------------------------------
# Dual-channel emission (home-assistant-wellness-promotion ADR-3)
# ---------------------------------------------------------------------------


async def emit_with_wellness_promotion(
    *,
    mcp_client: Any,
    ha_envelope: dict[str, Any],
    classifier: WellnessClassifier,
    endpoint_identity: str,
    entity_id: str,
    time_fired: str,
    ha_event: dict[str, Any],
    device_class: str | None,
    unit_of_measurement: str | None,
    attributes: dict[str, Any],
    new_state: dict[str, Any] | None,
    friendly_name: str | None,
    metrics: HAConnectorMetrics | None,
    promotion_enabled: bool,
) -> bool:
    """Submit the ``home_assistant`` envelope and, when warranted, a second
    ``wellness`` envelope for the same event (design ADR-3).

    The ``home_assistant`` emission is unconditional and goes first; the
    ``wellness`` emission is additive and only happens when promotion is enabled
    and the classifier promotes the reading. Both emissions reuse the same
    ``external_event_id`` — the Switchboard's channel-inclusive dedup key keeps
    them independent on replay.

    Returns:
        ``True`` if every attempted submission succeeded (caller may advance the
        checkpoint once); ``False`` if any submission failed transiently (caller
        must NOT advance — replay re-emits both channels, deduped per channel).
    """
    from butlers.connectors.home_assistant_envelope import build_wellness_envelope

    # Primary (home_assistant) — unchanged behavior, always emitted.
    try:
        await mcp_client.call_tool("ingest", ha_envelope)
        if metrics is not None:
            metrics.inc_submission("home_assistant", "success")
    except Exception:
        logger.warning(
            "ha-connector: failed to submit home_assistant envelope for entity_id=%s",
            entity_id,
            exc_info=True,
        )
        if metrics is not None:
            metrics.inc_submission("home_assistant", "error")
        # Primary failed: do not attempt the secondary channel, do not advance.
        return False

    if not promotion_enabled:
        return True

    # Classify for the secondary (wellness) channel.
    result = classifier.classify_detailed(
        entity_id=entity_id,
        device_class=device_class,
        unit_of_measurement=unit_of_measurement,
        attributes=attributes,
        state=(new_state or {}).get("state"),
    )
    if metrics is not None:
        metrics.inc_wellness_classify(result.outcome, result.metric)

    if result.outcome != "promoted" or result.metric is None or result.value is None:
        return True

    wellness_envelope = build_wellness_envelope(
        endpoint_identity=endpoint_identity,
        entity_id=entity_id,
        time_fired=time_fired,
        ha_event=ha_event,
        metric=result.metric,
        value=result.value,
        unit=unit_of_measurement,
        device_class=device_class,
        friendly_name=friendly_name,
        new_state=new_state,
    )

    try:
        await mcp_client.call_tool("ingest", wellness_envelope)
        if metrics is not None:
            metrics.inc_submission("wellness", "success")
    except Exception:
        logger.warning(
            "ha-connector: failed to submit wellness envelope for entity_id=%s metric=%s",
            entity_id,
            result.metric,
            exc_info=True,
        )
        if metrics is not None:
            metrics.inc_submission("wellness", "error")
        # Secondary failed transiently: leave the checkpoint un-advanced so the
        # replay re-emits both channels (the accepted primary will dedupe).
        return False

    return True


# ---------------------------------------------------------------------------
# Process entrypoint
# ---------------------------------------------------------------------------


async def _main() -> None:
    """Home Assistant connector process entrypoint.

    Loads configuration, resolves credentials, initializes the connector,
    and runs the main event loop.
    """
    import asyncpg

    from butlers.connectors.home_assistant_checkpoint import load_ha_checkpoint
    from butlers.connectors.home_assistant_filter import HAFilterPersistence
    from butlers.connectors.home_assistant_pipeline import HAFilterPipeline, HAFilterPipelineConfig
    from butlers.core.logging import configure_logging
    from butlers.credential_store import resolve_owner_entity_info, shared_db_name_from_env
    from butlers.db import db_params_from_env, register_jsonb_codec

    configure_logging()
    logger.info("Home Assistant connector starting")

    config = HAConnectorConfig.from_env()
    connector = HAConnector(config=config)

    # ------------------------------------------------------------------
    # Open persistent DB pool (credential resolution + pipeline use)
    # ------------------------------------------------------------------

    db_params = db_params_from_env()
    db_pool: asyncpg.Pool | None = None
    try:
        db_pool = await asyncpg.create_pool(
            host=db_params["host"],
            port=db_params["port"],
            user=db_params["user"],
            password=db_params["password"],
            database=shared_db_name_from_env(),
            ssl=db_params.get("ssl"),  # type: ignore[arg-type]
            min_size=1,
            max_size=4,
            command_timeout=10,
            setup=connector_setup_role,
            init=register_jsonb_codec,
        )
    except Exception:
        logger.warning(
            "HAConnector: could not open DB pool; pipeline will run without DB",
            exc_info=True,
        )

    # ------------------------------------------------------------------
    # Resolve HA credentials: env override → entity_info
    # ------------------------------------------------------------------

    ha_base_url = os.environ.get("HA_BASE_URL", "").strip()
    ha_access_token = os.environ.get("HA_ACCESS_TOKEN", "").strip()

    if (not ha_base_url or not ha_access_token) and db_pool is not None:
        try:
            if not ha_base_url:
                ha_base_url = await resolve_owner_entity_info(db_pool, "home_assistant_url") or ""
            if not ha_access_token:
                ha_access_token = (
                    await resolve_owner_entity_info(db_pool, "home_assistant_token") or ""
                )
        except Exception:
            logger.error("HAConnector: failed to load credentials from entity_info", exc_info=True)

    if not ha_base_url:
        logger.error(
            "HAConnector: HA_BASE_URL not set and 'home_assistant_url' not found in "
            "entity_info. Set HA_BASE_URL or configure credentials on the owner entity."
        )
        raise SystemExit(1)

    if not ha_access_token:
        logger.error(
            "HAConnector: HA_ACCESS_TOKEN not set and 'home_assistant_token' not found "
            "in entity_info. Set HA_ACCESS_TOKEN or configure credentials on the owner entity."
        )
        raise SystemExit(1)

    connector._ha_access_token = ha_access_token
    connector._set_endpoint_identity(ha_base_url)

    connector.start_health_server()
    connector.start_heartbeat()

    logger.info(
        "HAConnector: initialized — endpoint_identity=%s, health_port=%d",
        connector._endpoint_identity,
        config.health_port,
    )

    # ------------------------------------------------------------------
    # Tasks 5–9: Build filter pipeline, checkpoint, and filter persistence
    # ------------------------------------------------------------------

    endpoint_identity = connector._endpoint_identity

    pipeline = HAFilterPipeline(
        config=HAFilterPipelineConfig(domain_allowlist=config.domain_allowlist),
        evaluator=None,  # discretion evaluator not wired (always passes via weight bypass)
        metrics=connector._ha_metrics,
    )

    # Wellness classifier for dual-channel promotion (ADR-1/ADR-3).
    wellness_classifier = WellnessClassifier(
        extra_rules=config.wellness_rules_extra,
        denylist=config.wellness_entity_denylist,
    )

    # Load checkpoint resume timestamp (fail-open: None when no DB or no prior
    # checkpoint). The dispatcher advances the checkpoint as events are
    # submitted, tagged with the active transport.
    resume_ts = None
    if db_pool is not None:
        _checkpoint, resume_ts = await load_ha_checkpoint(
            db_pool,
            endpoint_identity=endpoint_identity,
            overlap_seconds=config.checkpoint_overlap_s,
        )

    # Filter persistence (for recording dropped events)
    async def _submit_envelope_for_replay(envelope: dict[str, Any]) -> None:
        await connector._mcp_client.call_tool("ingest", envelope)

    ha_filter_persistence = HAFilterPersistence(
        endpoint_identity=endpoint_identity,
        db_pool=db_pool,
        submit_fn=_submit_envelope_for_replay,
    )

    # ------------------------------------------------------------------
    # Tasks 3.1–3.6 + 5–9: Real event dispatch (WS + REST fallback share it)
    # ------------------------------------------------------------------

    dispatch = _make_event_dispatcher(
        connector=connector,
        config=config,
        db_pool=db_pool,
        pipeline=pipeline,
        wellness_classifier=wellness_classifier,
        endpoint_identity=endpoint_identity,
        resume_ts=resume_ts,
        ha_filter_persistence=ha_filter_persistence,
    )

    # Reorder buffer: HA can deliver events slightly out of time_fired order
    # during internal batching. Buffer briefly, submit in time_fired order, and
    # bound the buffer at HA_EVENT_QUEUE_MAX (spec "Event ordering"). Both the WS
    # client and the REST fallback feed the buffer, which threads each event's
    # transport tag through to the shared dispatcher.
    reorder_buffer = HAEventReorderBuffer(
        max_size=config.event_queue_max,
        submit=dispatch,
    )

    # ------------------------------------------------------------------
    # Build and wire the transport supervisor: WebSocket client with REST
    # polling fallback after _WS_FALLBACK_FAILURE_THRESHOLD failed reconnects.
    # ------------------------------------------------------------------

    ws_client, rest_poller, _fallback_controller = _build_transport_supervisor(
        connector=connector,
        config=config,
        ha_base_url=ha_base_url,
        ha_access_token=ha_access_token,
        dispatch=reorder_buffer.add,
    )

    # Set up graceful shutdown via OS signals
    stop_event = asyncio.Event()

    def _handle_signal() -> None:
        stop_event.set()

    try:
        import signal

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, _handle_signal)
            except (NotImplementedError, RuntimeError):
                pass
    except Exception:
        pass

    # Drive the reorder buffer: periodically release events that have aged past
    # the reorder window so submission is not stalled waiting for new arrivals.
    async def _reorder_flush_loop() -> None:
        try:
            while not stop_event.is_set():
                await asyncio.sleep(reorder_buffer.flush_interval_s)
                await reorder_buffer.flush_due()
        except asyncio.CancelledError:
            return
        except Exception:
            logger.warning("ha-connector: reorder flush loop error", exc_info=True)

    # Run the WS client; stop when a signal arrives
    ws_task = asyncio.create_task(ws_client.run())
    flush_task = asyncio.create_task(_reorder_flush_loop())
    await stop_event.wait()

    logger.info("HAConnector: shutting down")
    # Stop ingress first so no new events arrive while the buffer drains.
    rest_poller.stop()
    ws_task.cancel()
    try:
        await ws_task
    except (asyncio.CancelledError, Exception):
        pass
    flush_task.cancel()
    try:
        await flush_task
    except (asyncio.CancelledError, Exception):
        pass
    # Drain any buffered events in time_fired order before exiting.
    try:
        await reorder_buffer.flush_all()
    except Exception:
        logger.warning("ha-connector: error draining reorder buffer on shutdown", exc_info=True)
    await connector.stop_heartbeat()

    if db_pool is not None:
        await db_pool.close()


def _make_event_dispatcher(
    *,
    connector: HAConnector,
    config: HAConnectorConfig,
    db_pool: Any | None,
    pipeline: Any,
    wellness_classifier: WellnessClassifier,
    endpoint_identity: str,
    resume_ts: Any,
    ha_filter_persistence: Any,
) -> _EventDispatch:
    """Build the HA event dispatcher shared by the WS client and REST poller.

    The returned coroutine routes each HA event (real WebSocket event or a
    synthetic ``state_changed`` event produced by the REST poller) through the
    three-layer filter pipeline, submits surviving events to the Switchboard,
    and advances the checkpoint with the transport currently in use
    (``"websocket"`` or ``"rest_fallback"``).
    """
    from butlers.connectors.home_assistant_checkpoint import save_ha_checkpoint
    from butlers.connectors.home_assistant_envelope import (
        build_automation_triggered_envelope,
        build_state_changed_envelope,
        build_state_changed_normalized_text,
        parse_time_fired,
    )

    # Tracked entity count for metrics
    _tracked_entities: set[str] = set()

    def _resolve_transport(transport: str | None) -> str:
        """Checkpoint transport literal for this dispatch.

        An explicit ``transport`` (passed by the REST poller) wins; otherwise
        fall back to the connector's current transport. Resolving once per
        dispatch — rather than re-reading ``_rest_fallback_active`` at each
        checkpoint-save ``await`` — keeps the literal stable even if the WS
        client reconnects mid-dispatch.
        """
        if transport is not None:
            return transport
        return "rest_fallback" if connector._rest_fallback_active else "websocket"

    async def _dispatch(
        event_type: str, event: dict[str, Any], transport: str | None = None
    ) -> None:
        """Route a HA event through the filter pipeline and persist.

        Only ``state_changed`` and ``automation_triggered`` events are
        processed.  ``call_service`` events are counted but not forwarded.
        ``transport`` lets the caller pin the checkpoint transport literal
        (the REST poller passes ``"rest_fallback"``); when omitted it is
        inferred from the connector's active transport.
        """
        active_transport = _resolve_transport(transport)

        if event_type == "call_service":
            # call_service events are not persisted; count as not-forwarded.
            connector.on_event_received(passed_all_filters=False)
            return

        if event_type not in ("state_changed", "automation_triggered"):
            logger.debug("ha-connector: unhandled event_type=%r, skipping", event_type)
            connector.on_event_received(passed_all_filters=False)
            return

        # Extract common fields
        event_data: dict[str, Any] = event.get("data", {})
        time_fired: str = event.get("time_fired", "")
        entity_id: str = event_data.get("entity_id", "")

        if not entity_id:
            logger.debug("ha-connector: event missing entity_id, skipping")
            connector.on_event_received(passed_all_filters=False)
            return

        if not time_fired:
            logger.debug("ha-connector: event missing time_fired for entity_id=%s", entity_id)
            connector.on_event_received(passed_all_filters=False)
            return

        # Parse time_fired for checkpoint dedup
        try:
            event_ts = parse_time_fired(time_fired)
        except Exception:
            logger.warning(
                "ha-connector: unparseable time_fired=%r for entity_id=%s",
                time_fired,
                entity_id,
            )
            connector.on_event_received(passed_all_filters=False)
            return

        # Checkpoint dedup: skip events we've already processed
        from butlers.connectors.home_assistant_checkpoint import is_duplicate_event

        if is_duplicate_event(resume_ts, event_ts):
            logger.debug(
                "ha-connector: duplicate event entity_id=%s time_fired=%s, skipping",
                entity_id,
                time_fired,
            )
            connector.on_event_received(passed_all_filters=False)
            return

        # Track entity for metrics
        domain = entity_id.split(".")[0] if "." in entity_id else entity_id
        _tracked_entities.add(entity_id)
        connector.on_entities_tracked_update(len(_tracked_entities))

        if event_type == "automation_triggered":
            # Automation events: extract friendly name and forward directly
            new_state_data: dict[str, Any] = event_data.get("new_state") or {}
            attrs: dict[str, Any] = new_state_data.get("attributes") or {}
            friendly_name: str | None = attrs.get("friendly_name") or None

            envelope = build_automation_triggered_envelope(
                endpoint_identity=endpoint_identity,
                entity_id=entity_id,
                time_fired=time_fired,
                ha_event=event,
                friendly_name=friendly_name,
                domain=domain,
            )
            try:
                ingest_start = time.monotonic()
                await connector._mcp_client.call_tool("ingest", envelope)
                connector._ha_metrics and connector._ha_metrics.observe_event_latency(
                    time.monotonic() - ingest_start
                )
            except Exception:
                logger.warning(
                    "ha-connector: failed to submit automation_triggered envelope",
                    exc_info=True,
                )
                connector.on_event_received(passed_all_filters=False)
                return

            connector.on_event_received(passed_all_filters=True)
            if db_pool is not None:
                await save_ha_checkpoint(
                    db_pool, endpoint_identity, event_ts, entity_id, active_transport
                )
            return

        # state_changed: extract old/new state and run filter pipeline
        old_state_data: dict[str, Any] = event_data.get("old_state") or {}
        new_state_data: dict[str, Any] = event_data.get("new_state") or {}
        old_state_str: str | None = old_state_data.get("state") or None
        new_state_str: str | None = new_state_data.get("state") or None
        new_attrs: dict[str, Any] = new_state_data.get("attributes") or {}
        device_class: str | None = new_attrs.get("device_class") or None
        friendly_name: str | None = new_attrs.get("friendly_name") or None
        unit_of_measurement: str | None = new_attrs.get("unit_of_measurement") or None

        time_fired_ts = event_ts.timestamp()

        # Build normalized_text for discretion (and envelope)
        normalized_text = build_state_changed_normalized_text(
            entity_id=entity_id,
            friendly_name=friendly_name,
            old_state=old_state_str,
            new_state=new_state_str,
            unit_of_measurement=unit_of_measurement,
        )

        # Run three-layer filter pipeline
        from butlers.connectors.home_assistant_pipeline import PipelineResult

        result: PipelineResult = await pipeline.run(
            entity_id=entity_id,
            domain=domain,
            device_class=device_class,
            old_state_str=old_state_str,
            new_state_str=new_state_str,
            normalized_text=normalized_text,
            ha_event=event,
            time_fired_ts=time_fired_ts,
        )

        if result.verdict == "filtered":
            connector.on_event_received(passed_all_filters=False)
            # Record to filter persistence based on stage
            if result.stage == "domain_filter":
                ha_filter_persistence.record_domain_excluded(
                    entity_id=entity_id,
                    domain=domain,
                    ha_event=event,
                    time_fired=time_fired,
                    friendly_name=friendly_name,
                    old_state=old_state_data or None,
                    new_state=new_state_data or None,
                )
            elif result.stage == "significance_filter":
                # Extract delta from filter_reason: "insignificant_delta:<cls>:<delta>"
                parts = result.filter_reason.split(":", 2)
                try:
                    delta_val = float(parts[2]) if len(parts) >= 3 else 0.0
                except ValueError:
                    delta_val = 0.0
                ha_filter_persistence.record_insignificant_delta(
                    entity_id=entity_id,
                    device_class=device_class or domain,
                    delta=delta_val,
                    ha_event=event,
                    time_fired=time_fired,
                    friendly_name=friendly_name,
                    old_state=old_state_data or None,
                    new_state=new_state_data or None,
                )
            elif result.stage == "discretion":
                ha_filter_persistence.record_discretion_ignore(
                    entity_id=entity_id,
                    ha_event=event,
                    time_fired=time_fired,
                    friendly_name=friendly_name,
                    old_state=old_state_data or None,
                    new_state=new_state_data or None,
                    domain=domain,
                    device_class=device_class,
                )
            await ha_filter_persistence.flush()
            return

        # Event passed all filters — build envelope and submit to Switchboard
        connector.on_event_received(passed_all_filters=True)

        envelope = build_state_changed_envelope(
            endpoint_identity=endpoint_identity,
            entity_id=entity_id,
            time_fired=time_fired,
            ha_event=event,
            friendly_name=friendly_name,
            old_state=old_state_data or None,
            new_state=new_state_data or None,
            domain=domain,
            device_class=device_class,
            unit_of_measurement=unit_of_measurement,
            discretion_reason=result.discretion_reason or None,
        )

        # Emit on the home_assistant channel and, when the reading is
        # health-shaped, additionally on the wellness channel (ADR-3). The
        # checkpoint is advanced once, only after BOTH submissions succeed; a
        # transient secondary failure leaves it un-advanced so the replay
        # re-emits both channels (deduped per channel by the Switchboard).
        ingest_start = time.monotonic()
        submitted_all = await emit_with_wellness_promotion(
            mcp_client=connector._mcp_client,
            ha_envelope=envelope,
            classifier=wellness_classifier,
            endpoint_identity=endpoint_identity,
            entity_id=entity_id,
            time_fired=time_fired,
            ha_event=event,
            device_class=device_class,
            unit_of_measurement=unit_of_measurement,
            attributes=new_attrs,
            new_state=new_state_data or None,
            friendly_name=friendly_name,
            metrics=connector._ha_metrics,
            promotion_enabled=config.wellness_promotion_enabled,
        )
        if connector._ha_metrics is not None:
            connector._ha_metrics.observe_event_latency(time.monotonic() - ingest_start)

        if not submitted_all:
            # Do not advance the checkpoint; the event will be replayed.
            return

        # Persist person.* state-change events to the history evidence table
        if domain == "person" and db_pool is not None:
            await persist_ha_history(
                db_pool,
                entity_id=entity_id,
                state=new_state_str,
                attributes=new_attrs or None,
                recorded_at=event_ts,
            )

        # Update checkpoint after successful Switchboard submission
        if db_pool is not None:
            await save_ha_checkpoint(
                db_pool, endpoint_identity, event_ts, entity_id, active_transport
            )

        # Flush filtered event buffer and drain replay queue
        await ha_filter_persistence.flush()
        await ha_filter_persistence.drain_replay()

    return _dispatch


def _build_transport_supervisor(
    *,
    connector: HAConnector,
    config: HAConnectorConfig,
    ha_base_url: str,
    ha_access_token: str,
    dispatch: _EventDispatch,
) -> tuple[HAWebSocketClient, Any, Any]:
    """Build and wire the WebSocket client with its REST polling fallback.

    Returns ``(ws_client, rest_poller, fallback_controller)``.  The controller
    counts consecutive failed WS reconnect attempts and, once
    ``_WS_FALLBACK_FAILURE_THRESHOLD`` is reached, starts the REST poller so HA
    ingestion continues via ``GET /api/states`` while WebSocket reconnection
    keeps retrying.  A successful WS reconnect stops the poller and restores the
    WebSocket as the active transport.
    """
    from butlers.connectors.home_assistant_rest import (
        HAFallbackController,
        HARestPoller,
        HAStateCache,
    )

    # REST poller shares the same dispatch path as the WS client so polled
    # state changes flow through the identical filter/envelope/checkpoint code.
    state_cache = HAStateCache()

    async def _on_rest_state_changed(
        _old_snap: Any, _new_snap: Any, event_dict: dict[str, Any]
    ) -> None:
        # Pin the checkpoint transport: events polled via REST must record
        # ``rest_fallback`` even if the WS client reconnects mid-dispatch.
        await dispatch("state_changed", event_dict, "rest_fallback")

    rest_poller = HARestPoller(
        base_url=ha_base_url,
        access_token=ha_access_token,
        state_cache=state_cache,
        poll_interval_s=config.poll_interval_s,
        on_state_changed=_on_rest_state_changed,
        on_poll_success=connector.on_rest_poll_success,
        on_poll_error=lambda _exc: connector.on_rest_poll_error(),
    )

    def _start_fallback() -> None:
        connector.on_rest_fallback_started()
        rest_poller.start()

    def _stop_fallback() -> None:
        rest_poller.stop()
        connector.on_rest_fallback_stopped()

    fallback_controller = HAFallbackController(
        ws_failure_threshold=_WS_FALLBACK_FAILURE_THRESHOLD,
        on_fallback_start=_start_fallback,
        on_fallback_stop=_stop_fallback,
    )

    def _on_ws_connected() -> None:
        connector.on_ws_connected()
        fallback_controller.on_ws_success()

    def _on_ws_disconnected() -> None:
        connector.on_ws_disconnected()

    def _on_reconnect_failed() -> None:
        fallback_controller.on_ws_failure()

    ws_client = HAWebSocketClient(
        ha_base_url=ha_base_url,
        ha_access_token=ha_access_token,
        dispatch=dispatch,
        ping_interval_s=config.ws_ping_interval_s,
        pong_timeout_s=config.ws_pong_timeout_s,
        reconnect_initial_s=_DEFAULT_WS_RECONNECT_INITIAL_S,
        reconnect_max_s=_DEFAULT_WS_RECONNECT_MAX_S,
        on_connected=_on_ws_connected,
        on_disconnected=_on_ws_disconnected,
        on_reconnect_failed=_on_reconnect_failed,
    )

    return ws_client, rest_poller, fallback_controller


if __name__ == "__main__":
    asyncio.run(_main())
