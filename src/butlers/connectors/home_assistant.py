"""Home Assistant connector — WebSocket-first with REST polling fallback.

STATUS: PARTIAL (task 8 — health, heartbeat, metrics)
=======================================================

This module implements the health state derivation, heartbeat assembly, and
HA-specific Prometheus metrics for the Home Assistant connector process.

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
import logging
import os
import time
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
            from butlers.credential_store import CredentialStore, shared_db_name_from_env
            from butlers.db import db_params_from_env

            db_params = db_params_from_env()
            shared_db_name = shared_db_name_from_env()
            async with CredentialStore(
                db_params=db_params,
                schema="shared",
                db_name=shared_db_name,
            ) as cred_store:
                if not ha_base_url:
                    ha_base_url = await cred_store.get("home_assistant:base_url") or ""
                if not ha_access_token:
                    ha_access_token = await cred_store.get("home_assistant:access_token") or ""
        except Exception as exc:
            logger.error("HAConnector: failed to load credentials from CredentialStore: %s", exc)

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

    # TODO: implement WS event streaming, REST fallback, and filter pipeline
    # (tasks 3–7 in openspec/changes/connector-home-assistant/tasks.md)
    logger.warning(
        "HAConnector: WebSocket event streaming not yet implemented. "
        "Tasks 3–7 (connector core, REST fallback, filtering, envelope construction, "
        "checkpoint) are pending implementation."
    )

    # Keep running until interrupted
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

    await stop_event.wait()

    logger.info("HAConnector: shutting down")
    await connector.stop_heartbeat()


if __name__ == "__main__":
    asyncio.run(_main())
