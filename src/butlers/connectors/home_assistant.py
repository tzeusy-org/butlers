"""Home Assistant connector — WebSocket-first with REST polling fallback.

STATUS: PARTIAL (tasks 3 + 8 — WebSocket client core, health, heartbeat, metrics)
===================================================================================

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
- HA_BASE_URL: HA instance base URL (overrides CredentialStore)
- HA_ACCESS_TOKEN: HA long-lived access token (overrides CredentialStore)
- HA_DOMAIN_ALLOWLIST: comma-separated domain allowlist
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
import json
import logging
import os
import random
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from threading import Thread
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlparse

from prometheus_client import Counter, Gauge, Histogram, generate_latest

from butlers.connectors.health_socket import make_health_socket
from butlers.connectors.heartbeat import ConnectorHeartbeat, HeartbeatConfig
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
_DEFAULT_WS_RECONNECT_INITIAL_S = 1.0
_DEFAULT_WS_RECONNECT_MAX_S = 60.0
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

# Type alias for the event dispatch callback:
#   async def dispatch(event_type: str, event: dict) -> None
_EventDispatch = Callable[[str, dict[str, Any]], Coroutine[Any, Any, None]]


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

    @classmethod
    def from_env(cls) -> HAConnectorConfig:
        """Load connector configuration from environment variables.

        Raises:
            ValueError: If ``SWITCHBOARD_MCP_URL`` is not set.
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
# Process entrypoint
# ---------------------------------------------------------------------------


async def _main() -> None:
    """Home Assistant connector process entrypoint.

    Loads configuration, resolves credentials, initializes the connector,
    and runs the main event loop.
    """
    from butlers.core.logging import configure_logging

    configure_logging()
    logger.info("Home Assistant connector starting")

    config = HAConnectorConfig.from_env()
    connector = HAConnector(config=config)

    # Resolve HA credentials (CredentialStore or env override)
    ha_base_url = os.environ.get("HA_BASE_URL", "").strip()
    ha_access_token = os.environ.get("HA_ACCESS_TOKEN", "").strip()

    if not ha_base_url or not ha_access_token:
        try:
            import asyncpg

            from butlers.credential_store import CredentialStore, shared_db_name_from_env
            from butlers.db import db_params_from_env

            db_params = db_params_from_env()
            shared_db_name = shared_db_name_from_env()
            pool: asyncpg.Pool = await asyncpg.create_pool(
                host=db_params["host"],
                port=db_params["port"],
                user=db_params["user"],
                password=db_params["password"],
                database=shared_db_name,
                ssl=db_params.get("ssl"),  # type: ignore[arg-type]
                min_size=1,
                max_size=2,
                command_timeout=5,
                server_settings={"search_path": "shared,public"},
            )
            try:
                cred_store = CredentialStore(pool)
                if not ha_base_url:
                    ha_base_url = await cred_store.load("home_assistant:base_url") or ""
                if not ha_access_token:
                    ha_access_token = await cred_store.load("home_assistant:access_token") or ""
            finally:
                await pool.close()
        except Exception:
            logger.error(
                "HAConnector: failed to load credentials from CredentialStore", exc_info=True
            )

    if not ha_base_url:
        logger.error(
            "HAConnector: HA_BASE_URL not set and 'home_assistant:base_url' not found in "
            "CredentialStore. Set HA_BASE_URL or configure credentials via the dashboard."
        )
        raise SystemExit(1)

    if not ha_access_token:
        logger.error(
            "HAConnector: HA_ACCESS_TOKEN not set and 'home_assistant:access_token' not found "
            "in CredentialStore. Set HA_ACCESS_TOKEN or configure credentials via the dashboard."
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
    # Tasks 3.1–3.6: Wire up the WebSocket client
    # ------------------------------------------------------------------

    async def _null_dispatch(event_type: str, event: dict[str, Any]) -> None:
        """No-op event dispatch placeholder.

        In a full implementation (tasks 5–6), this would route events through
        the three-layer filter pipeline and construct ingest.v1 envelopes.
        Tasks 5–7 (REST fallback, filtering, envelope construction, checkpoint)
        remain pending; this stub ensures the connector loop and health state
        are exercised in the meantime.
        """
        logger.debug(
            "HAConnector: event received (type=%r, not yet dispatched to pipeline)",
            event_type,
        )
        connector.on_event_received(passed_all_filters=False)

    ws_client = HAWebSocketClient(
        ha_base_url=ha_base_url,
        ha_access_token=ha_access_token,
        dispatch=_null_dispatch,
        ping_interval_s=config.ws_ping_interval_s,
        pong_timeout_s=config.ws_pong_timeout_s,
        reconnect_initial_s=_DEFAULT_WS_RECONNECT_INITIAL_S,
        reconnect_max_s=_DEFAULT_WS_RECONNECT_MAX_S,
        on_connected=connector.on_ws_connected,
        on_disconnected=connector.on_ws_disconnected,
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

    # Run the WS client; stop when a signal arrives
    ws_task = asyncio.create_task(ws_client.run())
    await stop_event.wait()

    logger.info("HAConnector: shutting down")
    ws_task.cancel()
    try:
        await ws_task
    except (asyncio.CancelledError, Exception):
        pass
    await connector.stop_heartbeat()


if __name__ == "__main__":
    asyncio.run(_main())
