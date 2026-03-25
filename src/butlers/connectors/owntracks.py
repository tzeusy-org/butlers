"""OwnTracks connector runtime for location event ingestion via HTTP webhook.

This connector implements a FastAPI webhook server that receives HTTP POSTs from
the OwnTracks mobile app, normalizes location and transition events to ingest.v1
envelopes, and submits them to the Switchboard via MCP.

Unlike poll-based connectors, this connector is a webhook server — the OwnTracks
app pushes events to it. A single FastAPI application serves the webhook endpoint
plus the standard /health and /metrics endpoints.

Key behaviors:
- FastAPI HTTP server receiving OwnTracks webhook POSTs at /owntracks/webhook
- Bearer token authentication via CredentialStore (owntracks_webhook_token) with
  OWNTRACKS_WEBHOOK_TOKEN env var fallback; fail-closed if no token configured
- Payload type dispatch: location, transition, waypoints (ignored: lwt, cmd, etc.)
- ingest.v1 envelope normalization with privacy-conservative metadata tier default
- Timestamp-based checkpoint via cursor_store keyed by ("owntracks", endpoint_identity)
- Idempotency key: owntracks:<endpoint_identity>:<tst>:<type>[:<event>]
- Scheduled data retention purge (every 6 hours, DELETE from shared.ingestion_events)
- Heartbeat protocol with connector_type="owntracks" and event counters (6.1)
- Prometheus metrics including connector_owntracks_events_received_total (6.2)
- Health endpoint returning state/uptime/last_event_at/events_today (6.3)
- Filtered event batch flush to connectors.filtered_events (6.4)
- Replay queue drain loop after webhook event processing (6.5)
- IngestionPolicyEvaluator source filter gate (6.6)
- Graceful shutdown on SIGTERM/SIGINT

Environment variables:
- SWITCHBOARD_MCP_URL (required)
- OWNTRACKS_WEBHOOK_TOKEN (required if not in CredentialStore)
- OWNTRACKS_TRACKER_ID (optional): override device tracker ID
- OWNTRACKS_RETENTION_DAYS (optional, default 30): data retention in days
- CONNECTOR_INGESTION_TIER (optional, default "metadata"): "metadata" or "full"
- CONNECTOR_HEALTH_PORT (optional, default 40083): HTTP server port
- CONNECTOR_HEARTBEAT_INTERVAL_S (optional, default 120)
- BUTLER_SHARED_DB_NAME (optional; shared butler DB used for connector state
  and credentials, defaults to 'butlers')

Security requirements:
- Bearer token validated with constant-time hmac.compare_digest
- Connector refuses to start if no token is configured (fail-closed)
- Raw GPS coordinates are NOT stored at rest in metadata tier (default)
- SSID is not included in normalized text in metadata tier
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import math
import os
import signal
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from threading import Thread
from typing import TYPE_CHECKING, Any, Literal

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from prometheus_client import Counter, generate_latest

from butlers.connectors.cursor_store import load_cursor, save_cursor
from butlers.connectors.filtered_event_buffer import FilteredEventBuffer, drain_replay_pending
from butlers.connectors.health_socket import make_health_socket
from butlers.connectors.heartbeat import ConnectorHeartbeat, HeartbeatConfig
from butlers.connectors.mcp_client import CachedMCPClient, wait_for_switchboard_ready
from butlers.connectors.metrics import ConnectorMetrics
from butlers.core.logging import configure_logging
from butlers.credential_store import CredentialStore, shared_db_name_from_env
from butlers.db import db_params_from_env, should_retry_with_ssl_disable
from butlers.ingestion_policy import IngestionEnvelope, IngestionPolicyEvaluator

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONNECTOR_TYPE = "owntracks"
CONNECTOR_TYPE = _CONNECTOR_TYPE  # Public alias for use in tests and external code
_CONNECTOR_CHANNEL = "owntracks"
_CONNECTOR_PROVIDER = "owntracks"

# Default configuration
_DEFAULT_HEALTH_PORT = 40083
_DEFAULT_HEARTBEAT_INTERVAL_S = 120
_DEFAULT_RETENTION_DAYS = 30
_MIN_RETENTION_DAYS = 1
_RETENTION_PURGE_INTERVAL_S = 6 * 60 * 60  # 6 hours

# Supported OwnTracks payload types (others are silently ignored)
_SUPPORTED_PAYLOAD_TYPES = frozenset({"location", "transition", "waypoints"})

# Ingestion tier values
_TIER_METADATA = "metadata"
_TIER_FULL = "full"

# Credential key in CredentialStore
_CRED_WEBHOOK_TOKEN = "owntracks_webhook_token"

# ---------------------------------------------------------------------------
# Checkpoint dataclass (task 4.1)
# ---------------------------------------------------------------------------


@dataclass
class OwnTracksCheckpoint:
    """Checkpoint state for the OwnTracks connector.

    Attributes:
        last_tst: Unix timestamp (integer seconds) of the most recently
            successfully processed OwnTracks event.  ``None`` on first run.
    """

    last_tst: int | None = None

    def to_json(self) -> str:
        """Serialise checkpoint to a JSON string for storage in cursor_store."""
        return json.dumps({"last_tst": self.last_tst})

    @classmethod
    def from_json(cls, raw: str) -> OwnTracksCheckpoint:
        """Deserialise checkpoint from a JSON string as stored in cursor_store.

        Args:
            raw: JSON string previously produced by :meth:`to_json`.

        Returns:
            Populated :class:`OwnTracksCheckpoint`.

        Raises:
            ValueError: If ``raw`` is not valid JSON or is missing ``last_tst``.
        """
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"OwnTracks checkpoint is not valid JSON: {raw!r}") from exc

        if not isinstance(data, dict):
            raise ValueError(
                f"OwnTracks checkpoint JSON must be an object, got {type(data).__name__}: {raw!r}"
            )

        if "last_tst" not in data:
            raise ValueError(f"OwnTracks checkpoint JSON missing 'last_tst' key: {raw!r}")

        last_tst = data["last_tst"]
        # bool is a subclass of int in Python; reject it explicitly to avoid
        # silently accepting true/false as 1/0 checkpoint values.
        if last_tst is not None and (isinstance(last_tst, bool) or not isinstance(last_tst, int)):
            got = type(last_tst).__name__
            raise ValueError(f"OwnTracks checkpoint 'last_tst' must be int or null, got {got}")

        return cls(last_tst=last_tst)

    def advance(self, tst: int) -> None:
        """Update the checkpoint if ``tst`` is strictly newer.

        Args:
            tst: Unix timestamp of the event that was just processed
                successfully.
        """
        if self.last_tst is None or tst > self.last_tst:
            self.last_tst = tst


# ---------------------------------------------------------------------------
# Idempotency key construction (task 4.3)
# ---------------------------------------------------------------------------


def build_idempotency_key(
    endpoint_identity: str,
    tst: int,
    event_type: str,
    event_suffix: str | None = None,
) -> str:
    """Construct the canonical idempotency key for an OwnTracks event.

    Format: ``owntracks:<endpoint_identity>:<tst>:<event_type>[:<event_suffix>]``

    For transition events, pass ``event_suffix="enter"`` or ``event_suffix="leave"``
    to disambiguate enter vs leave transitions that share the same ``tst``.

    Args:
        endpoint_identity: Connector endpoint identifier, typically
            ``"owntracks:<user>:<device>"`` or a similar opaque string that
            uniquely identifies the reporting device.
        tst: OwnTracks device timestamp (Unix seconds) from the ``tst`` field
            of the incoming payload.
        event_type: OwnTracks ``_type`` field value (e.g. ``"location"``,
            ``"transition"``, ``"waypoint"``).
        event_suffix: Optional discriminator appended after ``event_type``.
            Use for transition events to include the enter/leave direction
            (e.g. ``event_suffix="enter"`` → ``...:transition:enter``).

    Returns:
        Idempotency key string in
        ``owntracks:<endpoint_identity>:<tst>:<event_type>`` format, or
        ``owntracks:<endpoint_identity>:<tst>:<event_type>:<event_suffix>``
        when ``event_suffix`` is provided.

    Example::

        >>> build_idempotency_key("owntracks:alice:phone", 1711360400, "location")
        'owntracks:owntracks:alice:phone:1711360400:location'
        >>> build_idempotency_key("owntracks:alice:phone", 1711360400, "transition", "enter")
        'owntracks:owntracks:alice:phone:1711360400:transition:enter'
    """
    base = f"owntracks:{endpoint_identity}:{tst}:{event_type}"
    if event_suffix:
        return f"{base}:{event_suffix}"
    return base


# ---------------------------------------------------------------------------
# Deduplication (task 4.4)
# ---------------------------------------------------------------------------


def is_duplicate_event(
    checkpoint: OwnTracksCheckpoint,
    tst: int,
) -> bool:
    """Return ``True`` if the event should be skipped as a duplicate.

    An event is considered a duplicate if its ``tst`` (device timestamp) is
    at or before the last successfully processed checkpoint timestamp.  This
    guarantees at-least-once delivery: on connector restart the connector may
    briefly reprocess the event at exactly ``last_tst``, but it will never miss
    events that arrived after the checkpoint.

    Args:
        checkpoint: Current persisted checkpoint.
        tst: Unix timestamp of the incoming OwnTracks event.

    Returns:
        ``True`` if the event should be skipped; ``False`` if it should be
        processed.
    """
    if checkpoint.last_tst is None:
        return False
    return tst <= checkpoint.last_tst


# ---------------------------------------------------------------------------
# Checkpoint persistence (task 4.2)
# ---------------------------------------------------------------------------


async def load_checkpoint(
    pool: asyncpg.Pool,
    endpoint_identity: str,
) -> OwnTracksCheckpoint:
    """Load the persisted checkpoint from the DB for the given endpoint.

    Uses :func:`butlers.connectors.cursor_store.load_cursor` under the hood.

    On missing or malformed checkpoint data, logs a warning and returns a fresh
    :class:`OwnTracksCheckpoint` (``last_tst=None``) so the connector starts
    from scratch without crashing.

    Args:
        pool: asyncpg connection pool that can reach ``switchboard.connector_registry``.
        endpoint_identity: Endpoint identifier for this connector instance.

    Returns:
        :class:`OwnTracksCheckpoint` — either loaded from DB or freshly initialised.
    """
    try:
        raw = await load_cursor(pool, _CONNECTOR_TYPE, endpoint_identity)
        if raw is not None:
            checkpoint = OwnTracksCheckpoint.from_json(raw)
            logger.info(
                "Loaded OwnTracks checkpoint from DB: last_tst=%s",
                checkpoint.last_tst,
                extra={"endpoint_identity": endpoint_identity},
            )
            return checkpoint
        else:
            logger.info(
                "No OwnTracks checkpoint in DB, starting from scratch",
                extra={"endpoint_identity": endpoint_identity},
            )
    except Exception:
        logger.exception(
            "Failed to load OwnTracks checkpoint from DB, starting from scratch",
            extra={"endpoint_identity": endpoint_identity},
        )

    return OwnTracksCheckpoint()


async def save_checkpoint(
    pool: asyncpg.Pool,
    endpoint_identity: str,
    checkpoint: OwnTracksCheckpoint,
) -> None:
    """Persist the checkpoint to the DB.

    Uses :func:`butlers.connectors.cursor_store.save_cursor` under the hood.
    Failures are logged at ERROR level but never re-raised so a transient DB
    outage does not crash the connector.

    Args:
        pool: asyncpg connection pool that can reach ``switchboard.connector_registry``.
        endpoint_identity: Endpoint identifier for this connector instance.
        checkpoint: Current checkpoint to persist.
    """
    try:
        await save_cursor(
            pool,
            _CONNECTOR_TYPE,
            endpoint_identity,
            checkpoint.to_json(),
        )
        logger.debug(
            "Saved OwnTracks checkpoint to DB: last_tst=%s",
            checkpoint.last_tst,
            extra={"endpoint_identity": endpoint_identity},
        )
    except Exception:
        logger.exception(
            "Failed to save OwnTracks checkpoint to DB",
            extra={"endpoint_identity": endpoint_identity},
        )


# ---------------------------------------------------------------------------
# Payload extraction helpers
# ---------------------------------------------------------------------------


def extract_tst(payload: dict[str, Any]) -> int | None:
    """Extract the ``tst`` (device timestamp) from an OwnTracks payload.

    Args:
        payload: Parsed OwnTracks JSON payload dictionary.

    Returns:
        Integer Unix timestamp if ``tst`` is present and is a finite number,
        ``None`` otherwise (including for NaN, Infinity, non-numeric types).
    """
    tst = payload.get("tst")
    if isinstance(tst, bool):
        return None
    if isinstance(tst, int):
        return tst
    if isinstance(tst, float):
        if not math.isfinite(tst):
            return None
        return int(tst)
    return None


def extract_event_type(payload: dict[str, Any]) -> str:
    """Extract the ``_type`` field from an OwnTracks payload.

    Args:
        payload: Parsed OwnTracks JSON payload dictionary.

    Returns:
        The ``_type`` string, or ``"unknown"`` if absent, null, or empty/whitespace.
    """
    event_type = payload.get("_type")
    if event_type is None:
        return "unknown"
    if isinstance(event_type, str) and not event_type.strip():
        return "unknown"
    return str(event_type)


# ---------------------------------------------------------------------------
# OwnTracks-specific Prometheus metrics (task 6.2)
# ---------------------------------------------------------------------------

owntracks_events_received_total = Counter(
    "connector_owntracks_events_received_total",
    "Total number of OwnTracks events received by type",
    labelnames=["endpoint_identity", "event_type"],
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class OwnTracksConnectorConfig:
    """Configuration for the OwnTracks connector runtime."""

    switchboard_mcp_url: str
    provider: str = _CONNECTOR_PROVIDER
    channel: str = _CONNECTOR_CHANNEL

    # OwnTracks-specific
    tracker_id_override: str | None = None
    retention_days: int = _DEFAULT_RETENTION_DAYS
    ingestion_tier: Literal["metadata", "full"] = _TIER_METADATA

    # Health / heartbeat
    health_port: int = _DEFAULT_HEALTH_PORT
    heartbeat_interval_s: int = _DEFAULT_HEARTBEAT_INTERVAL_S

    @classmethod
    def from_env(cls) -> OwnTracksConnectorConfig:
        """Load configuration from environment variables."""
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
                logger.warning("Invalid value for %s=%r, using default %d", key, raw, default)
                return default

        retention_days = _int("OWNTRACKS_RETENTION_DAYS", _DEFAULT_RETENTION_DAYS)
        if retention_days < _MIN_RETENTION_DAYS:
            raise ValueError(
                f"OWNTRACKS_RETENTION_DAYS={retention_days} is below minimum {_MIN_RETENTION_DAYS}"
            )

        raw_tier = os.environ.get("CONNECTOR_INGESTION_TIER", _TIER_METADATA).strip().lower()
        if raw_tier not in (_TIER_METADATA, _TIER_FULL):
            logger.warning("Unknown CONNECTOR_INGESTION_TIER=%r; using 'metadata'", raw_tier)
            raw_tier = _TIER_METADATA
        ingestion_tier: Literal["metadata", "full"] = raw_tier  # type: ignore[assignment]

        tracker_id_override = os.environ.get("OWNTRACKS_TRACKER_ID", "").strip() or None

        return cls(
            switchboard_mcp_url=switchboard_mcp_url,
            provider=os.environ.get("CONNECTOR_PROVIDER", _CONNECTOR_PROVIDER),
            channel=os.environ.get("CONNECTOR_CHANNEL", _CONNECTOR_CHANNEL),
            tracker_id_override=tracker_id_override,
            retention_days=retention_days,
            ingestion_tier=ingestion_tier,
            health_port=_int("CONNECTOR_HEALTH_PORT", _DEFAULT_HEALTH_PORT),
            heartbeat_interval_s=_int(
                "CONNECTOR_HEARTBEAT_INTERVAL_S", _DEFAULT_HEARTBEAT_INTERVAL_S
            ),
        )


# ---------------------------------------------------------------------------
# Normalized text generation
# ---------------------------------------------------------------------------


def _cardinal(value: float, pos_suffix: str, neg_suffix: str) -> str:
    """Format a coordinate with cardinal direction suffix."""
    if value >= 0:
        return f"{value}{pos_suffix}"
    return f"{abs(value)}{neg_suffix}"


def build_location_normalized_text(
    payload: dict[str, Any],
    ingestion_tier: str,
) -> str:
    """Build human-readable normalized text for a location event.

    In metadata tier, SSID is excluded from the text (privacy).
    """
    lat = payload.get("lat", 0.0)
    lon = payload.get("lon", 0.0)
    acc = payload.get("acc")
    vel = payload.get("vel")
    inregions = payload.get("inregions")

    lat_str = _cardinal(lat, "N", "S")
    lon_str = _cardinal(lon, "E", "W")

    parts = [f"Location update: {lat_str}, {lon_str}"]
    if acc is not None:
        parts[0] += f", acc {acc}m"

    if vel is not None and vel > 0:
        parts.append(f"{vel} km/h")

    text = ", ".join(parts) if len(parts) > 1 else parts[0]

    if inregions and isinstance(inregions, list) and len(inregions) > 0:
        regions_str = ", ".join(str(r) for r in inregions)
        text += f" (in: {regions_str})"

    # SSID intentionally not included in metadata tier (privacy)
    # In full tier, the SSID would be in payload.raw but not in normalized text

    return text


def build_transition_normalized_text(payload: dict[str, Any]) -> str:
    """Build human-readable normalized text for a transition event."""
    event = payload.get("event", "")
    desc = payload.get("desc", "unknown region")

    if event == "enter":
        return f"Entered region: {desc}"
    elif event == "leave":
        return f"Left region: {desc}"
    else:
        return f"Transition ({event}): {desc}"


def build_waypoints_normalized_text(payload: dict[str, Any]) -> str:
    """Build human-readable normalized text for a waypoints sync event."""
    waypoints = payload.get("waypoints", [])
    count = len(waypoints)

    names = []
    for wp in waypoints:
        if isinstance(wp, dict):
            name = wp.get("desc") or wp.get("tst")
            if name:
                names.append(str(name))

    _MAX_DISPLAY = 5
    if len(names) <= _MAX_DISPLAY:
        names_str = ", ".join(names) if names else "unnamed"
        return f"Waypoint sync: {count} regions ({names_str})"
    else:
        shown = ", ".join(names[:_MAX_DISPLAY])
        extra = len(names) - _MAX_DISPLAY
        return f"Waypoint sync: {count} regions ({shown}, and {extra} more)"


# ---------------------------------------------------------------------------
# Envelope builders
# ---------------------------------------------------------------------------


def build_location_envelope(
    payload: dict[str, Any],
    endpoint_identity: str,
    observed_at: str,
    ingestion_tier: Literal["metadata", "full"],
) -> dict[str, Any]:
    """Build an ingest.v1 envelope for a location event."""
    tst = payload.get("tst", 0)
    tid = payload.get("tid", "unknown")
    normalized_text = build_location_normalized_text(payload, ingestion_tier)
    raw = payload if ingestion_tier == _TIER_FULL else None

    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": _CONNECTOR_CHANNEL,
            "provider": _CONNECTOR_PROVIDER,
            "endpoint_identity": endpoint_identity,
        },
        "event": {
            "external_event_id": f"{tst}:location",
            "external_thread_id": f"owntracks:{tid}",
            "observed_at": observed_at,
        },
        "sender": {
            "identity": f"owntracks:{tid}",
        },
        "payload": {
            "raw": raw,
            "normalized_text": normalized_text,
        },
        "control": {
            "idempotency_key": f"owntracks:{endpoint_identity}:{tst}:location",
            "policy_tier": "default",
            "ingestion_tier": ingestion_tier,
        },
    }


def build_transition_envelope(
    payload: dict[str, Any],
    endpoint_identity: str,
    observed_at: str,
    ingestion_tier: Literal["metadata", "full"],
) -> dict[str, Any]:
    """Build an ingest.v1 envelope for a transition event."""
    tst = payload.get("tst", 0)
    tid = payload.get("tid", "unknown")
    event = payload.get("event", "unknown")
    normalized_text = build_transition_normalized_text(payload)
    raw = payload if ingestion_tier == _TIER_FULL else None

    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": _CONNECTOR_CHANNEL,
            "provider": _CONNECTOR_PROVIDER,
            "endpoint_identity": endpoint_identity,
        },
        "event": {
            "external_event_id": f"{tst}:transition:{event}",
            "external_thread_id": f"owntracks:{tid}",
            "observed_at": observed_at,
        },
        "sender": {
            "identity": f"owntracks:{tid}",
        },
        "payload": {
            "raw": raw,
            "normalized_text": normalized_text,
        },
        "control": {
            "idempotency_key": f"owntracks:{endpoint_identity}:{tst}:transition:{event}",
            "policy_tier": "default",
            "ingestion_tier": ingestion_tier,
        },
    }


def build_waypoints_envelope(
    payload: dict[str, Any],
    endpoint_identity: str,
    observed_at: str,
    ingestion_tier: Literal["metadata", "full"],
) -> dict[str, Any]:
    """Build an ingest.v1 envelope for a waypoints sync event."""
    tst = payload.get("tst", int(time.time()))
    tid = payload.get("tid", "unknown")
    normalized_text = build_waypoints_normalized_text(payload)
    raw = payload if ingestion_tier == _TIER_FULL else None

    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": _CONNECTOR_CHANNEL,
            "provider": _CONNECTOR_PROVIDER,
            "endpoint_identity": endpoint_identity,
        },
        "event": {
            "external_event_id": f"{tst}:waypoints",
            "external_thread_id": f"owntracks:{tid}",
            "observed_at": observed_at,
        },
        "sender": {
            "identity": f"owntracks:{tid}",
        },
        "payload": {
            "raw": raw,
            "normalized_text": normalized_text,
        },
        "control": {
            "idempotency_key": f"owntracks:{endpoint_identity}:{tst}:waypoints",
            "policy_tier": "default",
            "ingestion_tier": ingestion_tier,
        },
    }


# ---------------------------------------------------------------------------
# Main connector class
# ---------------------------------------------------------------------------


class OwnTracksConnector:
    """OwnTracks webhook connector.

    Runs a FastAPI HTTP server that receives OwnTracks webhook POSTs,
    normalizes location/transition/waypoints events to ingest.v1 envelopes,
    and submits them to the Switchboard.

    Follows the connector base contract:
    - Heartbeat protocol (task 6.1)
    - Prometheus metrics (task 6.2)
    - Health endpoint (task 6.3)
    - Filtered event batch flush (task 6.4)
    - Replay queue drain (task 6.5)
    - IngestionPolicyEvaluator source filter gate (task 6.6)
    """

    def __init__(
        self,
        config: OwnTracksConnectorConfig,
        webhook_token: str,
        db_pool: asyncpg.Pool | None = None,
        cursor_pool: asyncpg.Pool | None = None,
    ) -> None:
        self._config = config
        self._webhook_token = webhook_token
        self._db_pool = db_pool
        self._cursor_pool = cursor_pool

        # Endpoint identity (set during startup, may be updated on first event)
        self._endpoint_identity = f"owntracks:{config.tracker_id_override or 'unknown'}"

        # MCP client
        self._mcp_client = CachedMCPClient(
            config.switchboard_mcp_url,
            client_name="owntracks-connector",
        )

        # State
        self._start_time = time.time()
        self._running = False
        self._shutdown_event = asyncio.Event()

        # Event tracking (for health endpoint - task 6.3)
        self._last_event_at: datetime | None = None
        self._events_today: int = 0
        self._events_today_date: date = datetime.now(UTC).date()

        # Checkpoint tracking
        self._last_checkpoint_tst: int | None = None
        self._last_checkpoint_save: float | None = None

        # Metrics (initialized with placeholder identity, updated on first event)
        self._metrics = ConnectorMetrics(
            connector_type=_CONNECTOR_TYPE,
            endpoint_identity=self._endpoint_identity,
        )

        # Health/error state
        self._health_error: str | None = None
        self._last_ingest_ok: bool | None = None

        # Heartbeat (initialized after identity is finalized)
        self._heartbeat: ConnectorHeartbeat | None = None

        # Ingestion policy evaluator (initialized during startup)
        self._ingestion_policy: IngestionPolicyEvaluator | None = None

        # Filtered event buffer
        self._filtered_event_buffer = FilteredEventBuffer(
            connector_type=_CONNECTOR_TYPE,
            endpoint_identity=self._endpoint_identity,
        )

        # Health server
        self._health_server: uvicorn.Server | None = None
        self._health_thread: Thread | None = None

        # Retention purge task
        self._retention_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Full startup sequence followed by the main webhook server loop."""
        logger.info("OwnTracksConnector starting")
        self._running = True

        try:
            # Signal handlers
            try:
                loop = asyncio.get_running_loop()
                for sig in (signal.SIGTERM, signal.SIGINT):
                    loop.add_signal_handler(sig, self._handle_signal)
            except (NotImplementedError, OSError):
                logger.debug("OwnTracksConnector: signal handlers not supported on this platform")

            # Phase 1: Initialize ingestion policy evaluator
            scope = f"connector:{_CONNECTOR_TYPE}:{self._endpoint_identity}"
            self._ingestion_policy = IngestionPolicyEvaluator(
                scope=scope,
                db_pool=self._db_pool,
            )
            await self._ingestion_policy.ensure_loaded()

            # Phase 2: Initialize heartbeat
            self._init_heartbeat()

            # Phase 3: Load checkpoint
            await self._load_checkpoint()

            # Phase 4: Warn if ingestion tier is full
            if self._config.ingestion_tier == _TIER_FULL:
                logger.warning(
                    "OwnTracks ingestion tier set to 'full' -- "
                    "raw GPS coordinates will be stored at rest"
                )

            # Phase 5: Wait for Switchboard readiness
            try:
                await wait_for_switchboard_ready(self._config.switchboard_mcp_url)
            except TimeoutError:
                logger.warning(
                    "OwnTracksConnector: Switchboard readiness probe timed out; proceeding."
                )

            # Phase 6: Start health + webhook server
            app = self._build_app()
            self._start_health_server(app)

            # Phase 7: Start heartbeat
            assert self._heartbeat is not None
            self._heartbeat.start()

            # Phase 8: Send initial heartbeat
            try:
                await self._heartbeat._send_heartbeat()
            except Exception as exc:
                logger.debug("OwnTracksConnector: initial heartbeat failed (non-fatal): %s", exc)

            # Phase 9: Start retention purge task
            self._retention_task = asyncio.create_task(self._retention_purge_loop())

            # Phase 10: Wait for shutdown
            await self._shutdown_event.wait()

        finally:
            await self._shutdown()

    async def stop(self) -> None:
        """Request graceful shutdown."""
        if not self._shutdown_event.is_set():
            logger.info("OwnTracksConnector: stop() called, requesting shutdown")
            self._shutdown_event.set()

    def _handle_signal(self) -> None:
        """Handle SIGTERM/SIGINT: request graceful shutdown."""
        logger.info("OwnTracksConnector: received shutdown signal")
        self._shutdown_event.set()

    async def _shutdown(self) -> None:
        """Graceful shutdown: persist checkpoint, final heartbeat, clean up."""
        logger.info("OwnTracksConnector: shutting down")
        self._running = False

        # Cancel retention task
        if self._retention_task is not None:
            self._retention_task.cancel()
            try:
                await self._retention_task
            except asyncio.CancelledError:
                pass

        # Flush filtered event buffer
        if self._db_pool is not None:
            try:
                await self._filtered_event_buffer.flush(self._db_pool)
            except Exception:
                logger.warning("OwnTracksConnector: filtered event flush failed on shutdown")

        # Send final heartbeat
        if self._heartbeat is not None:
            try:
                await self._heartbeat._send_heartbeat()
            except Exception as exc:
                logger.debug("OwnTracksConnector: final heartbeat failed (non-fatal): %s", exc)
            await self._heartbeat.stop()

        # Stop health server
        if self._health_server is not None:
            self._health_server.should_exit = True

        logger.info("OwnTracksConnector: shutdown complete")

    # ------------------------------------------------------------------
    # Initialization helpers
    # ------------------------------------------------------------------

    def _init_heartbeat(self) -> None:
        """Initialize heartbeat with current endpoint identity."""
        hb_config = HeartbeatConfig.from_env(
            connector_type=_CONNECTOR_TYPE,
            endpoint_identity=self._endpoint_identity,
        )
        self._heartbeat = ConnectorHeartbeat(
            config=hb_config,
            mcp_client=self._mcp_client,
            metrics=self._metrics,
            get_health_state=self._get_health_state,
            get_checkpoint=self._get_checkpoint_info,
        )

    def _endpoint_identity_ready(self) -> None:
        """Re-initialize identity-bound components once the real endpoint identity is known.

        Called once when the first event's ``tid`` resolves the endpoint identity from the
        placeholder ``owntracks:unknown`` to the actual device tracker ID.  This keeps
        ConnectorMetrics labels, FilteredEventBuffer flush keys, IngestionPolicyEvaluator
        scope, and HeartbeatConfig endpoint all consistent with the real identity.
        """
        self._metrics = ConnectorMetrics(
            connector_type=_CONNECTOR_TYPE,
            endpoint_identity=self._endpoint_identity,
        )

        scope = f"connector:{_CONNECTOR_TYPE}:{self._endpoint_identity}"
        self._ingestion_policy = IngestionPolicyEvaluator(
            scope=scope,
            db_pool=self._db_pool,
        )

        self._filtered_event_buffer = FilteredEventBuffer(
            connector_type=_CONNECTOR_TYPE,
            endpoint_identity=self._endpoint_identity,
        )

        self._init_heartbeat()

    # ------------------------------------------------------------------
    # Webhook event processing
    # ------------------------------------------------------------------

    async def _process_webhook_event(self, body: dict[str, Any]) -> None:
        """Process a single OwnTracks webhook payload.

        Dispatches by _type, normalizes to ingest.v1, applies policy gate,
        submits to Switchboard, updates checkpoint, drains replay queue.
        """
        payload_type = body.get("_type", "")
        observed_at = datetime.now(UTC).isoformat()

        # Determine endpoint identity from tid if not overridden.
        # On first event carrying a real tid, re-initialize identity-bound
        # components (metrics, policy, buffer, heartbeat) so they all use the
        # resolved identity rather than the placeholder "owntracks:unknown".
        tid = body.get("tid")
        if tid and not self._config.tracker_id_override:
            resolved = f"owntracks:{tid}"
            if resolved != self._endpoint_identity:
                self._endpoint_identity = resolved
                self._endpoint_identity_ready()

        # Track event counter for today (task 6.3)
        now_date = datetime.now(UTC).date()
        if now_date != self._events_today_date:
            self._events_today = 0
            self._events_today_date = now_date

        if payload_type not in _SUPPORTED_PAYLOAD_TYPES:
            logger.debug("OwnTracksConnector: ignoring unsupported _type=%r", payload_type)
            owntracks_events_received_total.labels(
                endpoint_identity=self._endpoint_identity,
                event_type="ignored",
            ).inc()
            return

        # Increment received counter (task 6.2)
        owntracks_events_received_total.labels(
            endpoint_identity=self._endpoint_identity,
            event_type=payload_type,
        ).inc()

        self._last_event_at = datetime.now(UTC)
        self._events_today += 1

        # Apply source filter gate (task 6.6)
        if self._ingestion_policy is not None:
            ingest_envelope = IngestionEnvelope(
                source_channel=_CONNECTOR_CHANNEL,
                raw_key=self._endpoint_identity,
            )
            decision = self._ingestion_policy.evaluate(ingest_envelope)
            if not decision.allowed:
                logger.debug(
                    "OwnTracksConnector: event blocked by policy (scope=%s, reason=%s)",
                    self._ingestion_policy.scope,
                    decision.reason,
                )
                # Record in filtered event buffer (task 6.4)
                tst = body.get("tst", 0)
                external_event_id = f"{tst}:{payload_type}"
                if payload_type == "transition":
                    external_event_id = f"{tst}:transition:{body.get('event', 'unknown')}"

                self._filtered_event_buffer.record(
                    external_message_id=external_event_id,
                    source_channel=_CONNECTOR_CHANNEL,
                    sender_identity=self._endpoint_identity,
                    subject_or_preview=f"OwnTracks {payload_type} event",
                    filter_reason=FilteredEventBuffer.reason_policy_rule(
                        scope="connector_rule",
                        action=decision.action,
                        rule_type=decision.matched_rule_type or "unknown",
                    ),
                    full_payload=FilteredEventBuffer.full_payload(
                        channel=_CONNECTOR_CHANNEL,
                        provider=_CONNECTOR_PROVIDER,
                        endpoint_identity=self._endpoint_identity,
                        external_event_id=external_event_id,
                        external_thread_id=f"owntracks:{body.get('tid', 'unknown')}",
                        observed_at=observed_at,
                        sender_identity=self._endpoint_identity,
                        raw=body,
                    ),
                )
                return

        # Build envelope based on payload type
        envelope: dict[str, Any]
        if payload_type == "location":
            envelope = build_location_envelope(
                body,
                self._endpoint_identity,
                observed_at,
                self._config.ingestion_tier,
            )
        elif payload_type == "transition":
            envelope = build_transition_envelope(
                body,
                self._endpoint_identity,
                observed_at,
                self._config.ingestion_tier,
            )
        else:  # waypoints
            envelope = build_waypoints_envelope(
                body,
                self._endpoint_identity,
                observed_at,
                self._config.ingestion_tier,
            )

        # Submit to Switchboard
        start_t = time.perf_counter()
        status = "success"
        try:
            await self._mcp_client.call_tool("ingest", envelope)
            self._last_ingest_ok = True
            self._health_error = None
            logger.debug(
                "OwnTracksConnector: ingested %s event from %s",
                payload_type,
                self._endpoint_identity,
            )
        except Exception as exc:
            status = "error"
            self._last_ingest_ok = False
            self._health_error = str(exc)
            logger.warning("OwnTracksConnector: failed to submit %s event: %s", payload_type, exc)
            raise
        finally:
            latency = time.perf_counter() - start_t
            self._metrics.record_ingest_submission(status=status, latency=latency)

        # Update checkpoint (timestamp-based)
        tst = body.get("tst")
        if tst is not None:
            await self._save_checkpoint(int(tst))

        # Flush filtered event buffer (task 6.4) and drain replay queue (task 6.5)
        if self._db_pool is not None:
            try:
                await self._filtered_event_buffer.flush(self._db_pool)
            except Exception:
                logger.warning("OwnTracksConnector: filtered event flush failed", exc_info=True)
            try:
                await drain_replay_pending(
                    pool=self._db_pool,
                    connector_type=_CONNECTOR_TYPE,
                    endpoint_identity=self._endpoint_identity,
                    submit_fn=self._submit_envelope,
                    drain_logger=logger,
                )
            except Exception:
                logger.warning("OwnTracksConnector: replay queue drain failed", exc_info=True)

    async def _submit_envelope(self, envelope: dict[str, Any]) -> None:
        """Submit an ingest.v1 envelope to the Switchboard (for replay drain)."""
        await self._mcp_client.call_tool("ingest", envelope)

    # ------------------------------------------------------------------
    # Checkpoint
    # ------------------------------------------------------------------

    async def _load_checkpoint(self) -> None:
        """Load timestamp checkpoint from cursor store on startup."""
        if self._cursor_pool is None and self._db_pool is None:
            logger.debug("OwnTracksConnector: no DB pool available, skipping checkpoint load")
            return

        pool = self._cursor_pool or self._db_pool
        try:
            cursor = await load_cursor(pool, _CONNECTOR_TYPE, self._endpoint_identity)
            if cursor is not None:
                try:
                    self._last_checkpoint_tst = int(cursor)
                    logger.info(
                        "OwnTracksConnector: loaded checkpoint tst=%d for %s",
                        self._last_checkpoint_tst,
                        self._endpoint_identity,
                    )
                except (ValueError, TypeError):
                    logger.warning(
                        "OwnTracksConnector: invalid checkpoint cursor=%r, ignoring", cursor
                    )
        except Exception:
            logger.warning("OwnTracksConnector: failed to load checkpoint", exc_info=True)

    async def _save_checkpoint(self, tst: int) -> None:
        """Save timestamp checkpoint to cursor store."""
        if self._cursor_pool is None and self._db_pool is None:
            return

        pool = self._cursor_pool or self._db_pool
        try:
            await save_cursor(pool, _CONNECTOR_TYPE, self._endpoint_identity, str(tst))
            self._last_checkpoint_tst = tst
            self._last_checkpoint_save = time.time()
            self._metrics.record_checkpoint_save(status="success")
            logger.debug(
                "OwnTracksConnector: saved checkpoint tst=%d for %s", tst, self._endpoint_identity
            )
        except Exception:
            self._metrics.record_checkpoint_save(status="error")
            logger.warning(
                "OwnTracksConnector: failed to save checkpoint tst=%d", tst, exc_info=True
            )

    def _get_checkpoint_info(self) -> tuple[str | None, datetime | None]:
        """Return (cursor, updated_at) for heartbeat."""
        cursor = str(self._last_checkpoint_tst) if self._last_checkpoint_tst is not None else None
        updated_at = (
            datetime.fromtimestamp(self._last_checkpoint_save, tz=UTC)
            if self._last_checkpoint_save is not None
            else None
        )
        return cursor, updated_at

    # ------------------------------------------------------------------
    # Health state callbacks (task 6.3)
    # ------------------------------------------------------------------

    def _get_health_state(self) -> tuple[str, str | None]:
        """Return (state, error_message) for heartbeat."""
        if self._health_error:
            return "error", self._health_error
        if self._last_ingest_ok is None:
            return "healthy", None
        if self._last_ingest_ok:
            return "healthy", None
        return "degraded", "Last ingest submission failed"

    # ------------------------------------------------------------------
    # Data retention purge (task 5.1-5.4 from spec, needed for lifecycle)
    # ------------------------------------------------------------------

    async def _retention_purge_loop(self) -> None:
        """Background task: purge expired location events every 6 hours."""
        try:
            while True:
                await asyncio.sleep(_RETENTION_PURGE_INTERVAL_S)
                await self._run_retention_purge()
        except asyncio.CancelledError:
            logger.debug("OwnTracksConnector: retention purge loop cancelled")
            raise

    async def _run_retention_purge(self) -> None:
        """Delete expired OwnTracks events from shared.ingestion_events."""
        if self._db_pool is None:
            logger.debug("OwnTracksConnector: no DB pool, skipping retention purge")
            return

        retention_days = self._config.retention_days
        try:
            result = await self._db_pool.execute(
                """
                DELETE FROM shared.ingestion_events
                WHERE source_channel = 'owntracks'
                  AND created_at < NOW() - INTERVAL '1 day' * $1
                """,
                retention_days,
            )
            # asyncpg returns "DELETE N" string
            deleted_count = 0
            if result and isinstance(result, str) and result.startswith("DELETE "):
                try:
                    deleted_count = int(result.split(" ", 1)[1])
                except (ValueError, IndexError):
                    pass
            logger.info(
                "OwnTracksConnector: retention purge deleted %d rows (retention=%d days)",
                deleted_count,
                retention_days,
            )
        except Exception:
            logger.warning(
                "OwnTracksConnector: retention purge failed (retention=%d days)",
                retention_days,
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # FastAPI application builder (webhook + health + metrics)
    # ------------------------------------------------------------------

    def _build_app(self) -> FastAPI:
        """Build the FastAPI application serving webhook, health, and metrics."""
        app = FastAPI(title="owntracks-connector")

        @app.post("/owntracks/webhook")
        async def webhook(request: Request) -> JSONResponse:
            """Receive OwnTracks webhook POSTs."""
            # Validate bearer token
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                raise HTTPException(status_code=401, detail={"error": "Unauthorized"})

            provided_token = auth_header[len("Bearer ") :]
            if not hmac.compare_digest(provided_token, self._webhook_token):
                raise HTTPException(status_code=401, detail={"error": "Unauthorized"})

            # Parse payload
            try:
                body = await request.json()
            except Exception:
                raise HTTPException(status_code=400, detail={"error": "Invalid JSON"})

            if not isinstance(body, dict) or "_type" not in body:
                raise HTTPException(
                    status_code=400,
                    detail={"error": "Missing required field: _type"},
                )

            # Process event (non-blocking: errors are logged, not raised)
            try:
                await self._process_webhook_event(body)
            except Exception:
                logger.exception(
                    "OwnTracksConnector: error processing webhook event (type=%r)",
                    body.get("_type"),
                )

            # OwnTracks protocol requires 200 with empty JSON array on success
            return JSONResponse(content=[])

        @app.get("/health")
        async def health() -> dict[str, Any]:
            """Return connector health status."""
            state, error = self._get_health_state()
            uptime_s = int(time.time() - self._start_time)
            return {
                "state": state,
                "connector_type": _CONNECTOR_TYPE,
                "endpoint_identity": self._endpoint_identity,
                "uptime_s": uptime_s,
                "last_event_at": (self._last_event_at.isoformat() if self._last_event_at else None),
                "events_today": self._events_today,
                "error": error,
            }

        @app.get("/metrics")
        async def metrics() -> bytes:
            """Return Prometheus metrics."""
            return generate_latest()

        return app

    def _start_health_server(self, app: FastAPI) -> None:
        """Start the combined webhook/health/metrics HTTP server in a background thread.

        The OwnTracks mobile app pushes webhook events over the internet, so the server
        must listen on all interfaces (0.0.0.0).  This means /health and /metrics are
        also publicly reachable on the same port.  In production, place a reverse proxy
        in front of this server that restricts /health and /metrics to internal networks.
        """
        port = self._config.health_port
        logger.warning(
            "OwnTracksConnector: webhook server binding to 0.0.0.0:%d. "
            "/health and /metrics are publicly reachable. "
            "Use a reverse proxy to restrict access to those paths in production.",
            port,
        )
        try:
            sock = make_health_socket("0.0.0.0", port)
        except Exception as exc:
            logger.warning(
                "OwnTracksConnector: could not bind server socket on port %d: %s", port, exc
            )
            return

        uvicorn_config = uvicorn.Config(
            app=app,
            host="0.0.0.0",
            port=port,
            log_level="warning",
            access_log=False,
        )
        server = uvicorn.Server(uvicorn_config)
        self._health_server = server

        def _run() -> None:
            asyncio.run(server.serve(sockets=[sock]))

        thread = Thread(target=_run, daemon=True, name="owntracks-server")
        thread.start()
        self._health_thread = thread
        logger.info("OwnTracksConnector: server started on port %d", port)


# ---------------------------------------------------------------------------
# Token resolution helper
# ---------------------------------------------------------------------------


async def resolve_webhook_token(
    cred_store: CredentialStore | None,
) -> str:
    """Resolve the OwnTracks webhook bearer token.

    Lookup order:
    1. CredentialStore under key 'owntracks_webhook_token'
    2. Environment variable OWNTRACKS_WEBHOOK_TOKEN

    Raises ValueError if no token is found (fail-closed).
    """
    # Try CredentialStore first
    if cred_store is not None:
        try:
            token = await cred_store.load(_CRED_WEBHOOK_TOKEN)
            if token:
                logger.debug("OwnTracksConnector: resolved webhook token from CredentialStore")
                return token
        except Exception:
            logger.warning(
                "OwnTracksConnector: failed to read token from CredentialStore", exc_info=True
            )

    # Fall back to environment variable
    env_token = os.environ.get("OWNTRACKS_WEBHOOK_TOKEN", "").strip()
    if env_token:
        logger.debug("OwnTracksConnector: resolved webhook token from OWNTRACKS_WEBHOOK_TOKEN")
        return env_token

    raise ValueError(
        "No OwnTracks webhook token configured. "
        "Set OWNTRACKS_WEBHOOK_TOKEN or configure owntracks_webhook_token in CredentialStore."
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def run_owntracks_connector() -> None:
    """Main async entry point for the OwnTracks connector."""
    configure_logging()
    logger.info("OwnTracks connector starting")

    config = OwnTracksConnectorConfig.from_env()

    import asyncpg

    db_params = db_params_from_env()
    shared_db_name = shared_db_name_from_env()
    # Create DB pool for credentials and policy rules
    db_pool: asyncpg.Pool | None = None
    try:
        pool_kwargs: dict[str, Any] = {
            "host": str(db_params.get("host") or "localhost"),
            "port": int(db_params.get("port") or 5432),
            "user": str(db_params.get("user") or "butlers"),
            "password": str(db_params.get("password") or "butlers"),
            "database": shared_db_name,
            "min_size": 1,
            "max_size": 5,
        }
        try:
            db_pool = await asyncpg.create_pool(**pool_kwargs)
        except Exception as exc:
            if should_retry_with_ssl_disable(exc):
                logger.debug("OwnTracksConnector: retrying DB pool without SSL")
                pool_kwargs["ssl"] = False
                db_pool = await asyncpg.create_pool(**pool_kwargs)
            else:
                raise
        logger.info("OwnTracksConnector: DB pool connected to %s", shared_db_name)
    except Exception:
        logger.warning(
            "OwnTracksConnector: failed to create DB pool; running without DB", exc_info=True
        )

    # Resolve webhook token (fail-closed)
    cred_store: CredentialStore | None = None
    if db_pool is not None:
        try:
            cred_store = CredentialStore(db_pool)
        except Exception:
            logger.warning("OwnTracksConnector: could not create CredentialStore")

    try:
        webhook_token = await resolve_webhook_token(cred_store)
    except ValueError as exc:
        logger.error("OwnTracksConnector: startup aborted: %s", exc)
        raise SystemExit(1) from exc

    connector = OwnTracksConnector(
        config=config,
        webhook_token=webhook_token,
        db_pool=db_pool,
        cursor_pool=db_pool,
    )

    try:
        await connector.start()
    finally:
        if db_pool is not None:
            await db_pool.close()


if __name__ == "__main__":
    asyncio.run(run_owntracks_connector())
