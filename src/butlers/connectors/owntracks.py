"""OwnTracks connector runtime for location event ingestion via HTTP webhook.

This connector implements a FastAPI webhook server that receives HTTP POSTs from
the OwnTracks mobile app, normalizes location and transition events to ingest.v1
envelopes, and submits them to the Switchboard via MCP.

Unlike poll-based connectors, this connector is a webhook server — the OwnTracks
app pushes events to it. A single FastAPI application serves the webhook endpoint
plus the standard /health and /metrics endpoints.

Key behaviors:
- FastAPI HTTP server receiving OwnTracks webhook POSTs at /owntracks/webhook
- Webhook authentication via CredentialStore (owntracks_webhook_token) with
  OWNTRACKS_WEBHOOK_TOKEN env var fallback; fail-closed if no token configured.
  Accepts either ``Authorization: Bearer <token>`` or HTTP Basic auth where the
  password equals the configured token (matches OwnTracks mobile's native
  username/password fields; the username is ignored).
- Payload type dispatch: location, transition, waypoints (ignored: lwt, cmd, etc.)
- ingest.v1 envelope normalization with privacy-conservative metadata tier default
- Timestamp-based checkpoint via cursor_store keyed by ("owntracks", endpoint_identity)
- Idempotency key: owntracks:<endpoint_identity>:<tst>:<type>[:<event>]
- Scheduled data retention purge (every 6 hours, DELETE from public.ingestion_events)
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
- Bearer and Basic-auth tokens validated with constant-time hmac.compare_digest
- Connector refuses to start if no token is configured (fail-closed)
- Raw GPS coordinates are NOT stored at rest in metadata tier (default)
- SSID is not included in normalized text in metadata tier
"""

from __future__ import annotations

import asyncio
import base64
import binascii
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
from typing import TYPE_CHECKING, Annotated, Any, Literal

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from prometheus_client import Counter, generate_latest

from butlers.connectors.cursor_store import load_cursor, save_cursor
from butlers.connectors.db_role import connector_setup_role
from butlers.connectors.filtered_event_buffer import FilteredEventBuffer, drain_replay_pending
from butlers.connectors.health_socket import make_health_socket
from butlers.connectors.heartbeat import ConnectorHeartbeat, HeartbeatConfig
from butlers.connectors.mcp_client import CachedMCPClient, wait_for_switchboard_ready
from butlers.connectors.metrics import ConnectorMetrics
from butlers.core.logging import configure_logging
from butlers.credential_store import CredentialStore, shared_db_name_from_env
from butlers.db import (
    db_params_from_env,
    register_jsonb_codec,
    schema_search_path,
    should_retry_with_ssl_disable,
)
from butlers.ingestion_policy import IngestionEnvelope, IngestionPolicyEvaluator

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONNECTOR_TYPE = "owntracks"
_CONNECTOR_CHANNEL = "owntracks"
_CONNECTOR_PROVIDER = "owntracks"

# Default configuration
_DEFAULT_HEALTH_PORT = 40083
_DEFAULT_HEARTBEAT_INTERVAL_S = 120
_DEFAULT_RETENTION_DAYS = 30
_MIN_RETENTION_DAYS = 1
_RETENTION_PURGE_INTERVAL_S = 6 * 60 * 60  # 6 hours

# Public aliases for use by standalone OwnTracksRetentionConfig / OwnTracksRetention
DEFAULT_RETENTION_DAYS = _DEFAULT_RETENTION_DAYS
MIN_RETENTION_DAYS = _MIN_RETENTION_DAYS
RETENTION_PURGE_INTERVAL_S = _RETENTION_PURGE_INTERVAL_S

# Supported OwnTracks payload types (others are silently ignored)
_SUPPORTED_PAYLOAD_TYPES = frozenset({"location", "transition", "waypoints"})

# Ingestion tier values
_TIER_METADATA = "metadata"
_TIER_FULL = "full"

# Credential key in CredentialStore
_CRED_WEBHOOK_TOKEN = "owntracks_webhook_token"

# Public aliases used by standalone auth helpers and their tests
_DB_KEY = _CRED_WEBHOOK_TOKEN
_ENV_VAR = "OWNTRACKS_WEBHOOK_TOKEN"

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
# Retention configuration (standalone, reusable)
# ---------------------------------------------------------------------------


@dataclass
class OwnTracksRetentionConfig:
    """Configuration for OwnTracks data retention.

    Attributes:
        retention_days: Number of days to retain location events. Rows older than
            this threshold are deleted from public.ingestion_events on each purge cycle.
            Must be an integer >= ``MIN_RETENTION_DAYS`` (1).
    """

    retention_days: int = DEFAULT_RETENTION_DAYS

    def __post_init__(self) -> None:
        """Validate retention_days on construction regardless of how the config is built.

        Raises:
            TypeError: If ``retention_days`` is not an ``int``.
            ValueError: If ``retention_days`` is less than ``MIN_RETENTION_DAYS``.
        """
        if not isinstance(self.retention_days, int):
            raise TypeError(
                f"retention_days must be an int, got {type(self.retention_days).__name__!r}: "
                f"{self.retention_days!r}"
            )
        if self.retention_days < MIN_RETENTION_DAYS:
            raise ValueError(
                f"retention_days must be >= {MIN_RETENTION_DAYS}, got {self.retention_days}. "
                "A value of 0 or negative would delete all location history immediately."
            )

    @classmethod
    def from_env(cls) -> OwnTracksRetentionConfig:
        """Load retention configuration from environment variables.

        Reads ``OWNTRACKS_RETENTION_DAYS``.  If the value is set to a number
        less than ``MIN_RETENTION_DAYS`` (1), a ``ValueError`` is raised to
        prevent accidental mass-deletion of fresh data.

        Returns:
            OwnTracksRetentionConfig with resolved settings.

        Raises:
            ValueError: If ``OWNTRACKS_RETENTION_DAYS`` is set to a value < 1.
        """
        raw = os.environ.get("OWNTRACKS_RETENTION_DAYS")
        if raw is None:
            return cls(retention_days=DEFAULT_RETENTION_DAYS)

        try:
            days = int(raw)
        except ValueError as exc:
            raise ValueError(
                f"OWNTRACKS_RETENTION_DAYS must be a positive integer, got: {raw!r}"
            ) from exc

        if days < MIN_RETENTION_DAYS:
            raise ValueError(
                f"OWNTRACKS_RETENTION_DAYS must be >= {MIN_RETENTION_DAYS}, got {days}. "
                "Setting 0 or a negative value would delete all location history immediately."
            )

        return cls(retention_days=days)


# ---------------------------------------------------------------------------
# Retention purge SQL (parameterized — no interpolation footgun)
# ---------------------------------------------------------------------------

_PURGE_SQL = """\
DELETE FROM public.ingestion_events
WHERE source_channel = 'owntracks'
  AND received_at < NOW() - $1 * INTERVAL '1 day'
"""


# ---------------------------------------------------------------------------
# Retention background task (standalone, reusable)
# ---------------------------------------------------------------------------


class OwnTracksRetention:
    """Background data retention task for the OwnTracks connector.

    Runs a purge cycle every ``RETENTION_PURGE_INTERVAL_S`` seconds (6 hours)
    that deletes expired rows from ``public.ingestion_events`` where
    ``source_channel = 'owntracks'`` and ``received_at`` is older than the
    configured retention period.

    Purge failures are logged at WARNING level and never crash the connector.

    Usage::

        pool = await asyncpg.create_pool(...)
        config = OwnTracksRetentionConfig.from_env()
        retention = OwnTracksRetention(config, pool)
        retention.start()
        ...
        await retention.stop()
    """

    def __init__(
        self,
        config: OwnTracksRetentionConfig,
        pool: asyncpg.Pool,
        *,
        purge_interval_s: int = RETENTION_PURGE_INTERVAL_S,
    ) -> None:
        """Initialise the retention task.

        Args:
            config: Retention configuration (retention_days, etc.).
            pool: asyncpg connection pool that can reach the ``shared`` schema.
            purge_interval_s: Interval between purge cycles in seconds.
                Defaults to ``RETENTION_PURGE_INTERVAL_S`` (6 hours). Exposed as
                a parameter for unit testing so tests do not have to wait 6 hours.
                Must be >= 1; a value of 0 would spin the purge loop without pause
                and hammer the DB. A negative value would cause ``asyncio.sleep``
                to raise immediately, killing the background task.
        """
        if purge_interval_s < 1:
            raise ValueError(
                f"purge_interval_s must be >= 1, got {purge_interval_s}. "
                "A value of 0 would spin the purge loop without pause; "
                "a negative value would raise in asyncio.sleep."
            )
        self._config = config
        self._pool = pool
        self._purge_interval_s = purge_interval_s
        self._task: asyncio.Task | None = None

    @property
    def retention_days(self) -> int:
        """Return the active retention period in days."""
        return self._config.retention_days

    def start(self) -> None:
        """Schedule the background purge loop as an asyncio task.

        Must be called from within a running event loop.  Calling ``start()``
        while a task is already running is a no-op with a warning log.
        """
        if self._task is not None:
            logger.warning(
                "OwnTracks retention task already running; ignoring duplicate start call."
            )
            return

        self._task = asyncio.create_task(self._purge_loop())
        logger.info(
            "OwnTracks retention task started: retention_days=%d, interval_s=%d",
            self._config.retention_days,
            self._purge_interval_s,
        )

    async def stop(self) -> None:
        """Cancel the background purge loop and wait for it to exit."""
        if self._task is None:
            return

        logger.info("Stopping OwnTracks retention task.")
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

        logger.info("OwnTracks retention task stopped.")

    async def purge_once(self) -> int:
        """Execute a single purge cycle immediately.

        Deletes rows from ``public.ingestion_events`` where
        ``source_channel = 'owntracks'`` and ``received_at`` is older than the
        configured retention period.

        Returns:
            Number of rows deleted.

        Raises:
            Exception: Re-raises any database exceptions so that ``_purge_loop``
                can catch and log them without crashing the connector.
        """
        async with self._pool.acquire() as conn:
            result = await conn.execute(_PURGE_SQL, self._config.retention_days)

        # asyncpg returns a status string like "DELETE 42"
        deleted = _parse_delete_count(result)
        return deleted

    async def _purge_loop(self) -> None:
        """Repeat purge cycles forever, separated by ``_purge_interval_s``.

        Failures are logged at WARNING and the loop continues.
        """
        try:
            while True:
                await asyncio.sleep(self._purge_interval_s)
                await self._run_purge()
        except asyncio.CancelledError:
            logger.debug("OwnTracks retention purge loop cancelled.")
            raise

    async def _run_purge(self) -> None:
        """Execute one purge cycle with error handling and logging."""
        try:
            deleted = await self.purge_once()
            logger.info(
                "OwnTracks retention purge complete: deleted %d rows (retention_days=%d)",
                deleted,
                self._config.retention_days,
            )
        except Exception:
            logger.warning(
                "OwnTracks retention purge failed (retention_days=%d). Will retry on next cycle.",
                self._config.retention_days,
                exc_info=True,
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_delete_count(status: str) -> int:
    """Parse the row count from an asyncpg DELETE status string.

    asyncpg returns a string such as ``"DELETE 42"`` after ``conn.execute()``.
    This helper extracts the integer count.  Returns 0 if the string cannot
    be parsed.

    Args:
        status: Status string returned by ``asyncpg.Connection.execute()``.

    Returns:
        Number of deleted rows, or 0 if parsing fails.
    """
    try:
        parts = status.split()
        if len(parts) == 2 and parts[0] == "DELETE":
            return int(parts[1])
    except (ValueError, AttributeError):
        pass
    return 0


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
# Durable location point evidence persistence (Chronicler read surface, RFC 0014)
# ---------------------------------------------------------------------------

_LOCATION_EVIDENCE_TABLE = "connectors.owntracks_points"


async def persist_location_point(
    pool: asyncpg.Pool,
    *,
    endpoint_identity: str,
    tst: int,
    lat: float,
    lon: float,
    accuracy: float | None,
    trigger: str | None,
    raw_payload: dict[str, Any],
) -> bool:
    """Write a location point to the durable evidence table.

    Idempotent — uses ON CONFLICT DO NOTHING on ``idempotency_key``.
    Returns True if the row was inserted, False if it already existed.

    This table is the Chronicler-readable evidence surface for
    ``owntracks.points`` (RFC 0014 §D9).  The connector writes here
    directly (connector_writer role) on every accepted location event.
    Errors are caught and logged; failure to persist does NOT abort the
    ingest submission path.

    Args:
        pool: asyncpg connection pool (connector_writer role).
        endpoint_identity: Connector endpoint identity string.
        tst: OwnTracks device timestamp (Unix seconds).
        lat: Latitude in decimal degrees.
        lon: Longitude in decimal degrees.
        accuracy: GPS accuracy in metres, or None if not reported.
        trigger: OwnTracks ``t`` trigger field (e.g. ``"p"``, ``"c"``), or None.
        raw_payload: Full OwnTracks webhook payload for archival use.

    Returns:
        True if a new row was inserted; False if a duplicate was skipped.
    """
    idempotency_key = f"owntracks:{endpoint_identity}:{tst}:location"
    ts = datetime.fromtimestamp(tst, tz=UTC)

    result = await pool.fetchval(
        f"""
        INSERT INTO {_LOCATION_EVIDENCE_TABLE} (
            idempotency_key,
            ts,
            lat,
            lon,
            accuracy,
            trigger,
            endpoint_identity,
            raw_payload
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (idempotency_key) DO NOTHING
        RETURNING id
        """,
        idempotency_key,
        ts,
        lat,
        lon,
        accuracy,
        trigger,
        endpoint_identity,
        raw_payload,
    )
    return result is not None


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

        # Main event loop captured in start(); uvicorn runs on its own thread loop,
        # so webhook handlers hop back to this loop before touching asyncpg / MCP
        # state — otherwise asyncio.Lock raises "bound to a different event loop".
        self._main_loop: asyncio.AbstractEventLoop | None = None

        # Retention purge (OwnTracksRetention; initialized in start() when db_pool is available)
        self._retention: OwnTracksRetention | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Full startup sequence followed by the main webhook server loop."""
        logger.info("OwnTracksConnector starting")
        self._running = True
        self._main_loop = asyncio.get_running_loop()

        try:
            # Signal handlers
            try:
                for sig in (signal.SIGTERM, signal.SIGINT):
                    self._main_loop.add_signal_handler(sig, self._handle_signal)
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

            # Phase 7 & 8: Start heartbeat only if identity is already resolved.
            # With the placeholder ``owntracks:unknown`` we'd pollute the registry
            # with a zombie row that survives every restart; wait until the first
            # webhook event carries a ``tid`` and let _endpoint_identity_ready()
            # start the heartbeat under the real identity.
            assert self._heartbeat is not None
            if self._config.tracker_id_override:
                self._heartbeat.start()
                try:
                    await self._heartbeat._send_heartbeat()
                except Exception as exc:
                    logger.debug(
                        "OwnTracksConnector: initial heartbeat failed (non-fatal): %s", exc
                    )

            # Phase 9: Start retention purge task
            if self._db_pool is not None:
                retention_config = OwnTracksRetentionConfig(
                    retention_days=self._config.retention_days
                )
                self._retention = OwnTracksRetention(retention_config, self._db_pool)
                self._retention.start()

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

        # Stop retention purge task
        if self._retention is not None:
            await self._retention.stop()

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

    async def _endpoint_identity_ready(self) -> None:
        """Re-initialize identity-bound components once the real endpoint identity is known.

        Called when the first event's ``tid`` resolves the endpoint identity from the
        placeholder ``owntracks:unknown`` to the actual device tracker ID. Keeps
        ConnectorMetrics labels, FilteredEventBuffer flush keys, IngestionPolicyEvaluator
        scope, and HeartbeatConfig endpoint consistent with the real identity.

        The heartbeat task is replaced: the old one (still registering heartbeats under
        the stale identity) is stopped, a new one is constructed with the resolved
        identity, and started. Without this, the placeholder identity keeps ticking
        forever while the real identity's row goes stale on the dashboard.
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

        old_heartbeat = self._heartbeat
        if old_heartbeat is not None:
            try:
                await old_heartbeat.stop()
            except Exception:
                logger.exception("OwnTracksConnector: failed to stop previous heartbeat task")

        self._init_heartbeat()
        assert self._heartbeat is not None
        self._heartbeat.start()

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
                await self._endpoint_identity_ready()

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

        # Persist durable location evidence for Chronicler (RFC 0014 §D9)
        if payload_type == "location" and self._db_pool is not None and tst is not None:
            try:
                await persist_location_point(
                    self._db_pool,
                    endpoint_identity=self._endpoint_identity,
                    tst=int(tst),
                    lat=float(body.get("lat", 0.0)),
                    lon=float(body.get("lon", 0.0)),
                    accuracy=float(body["acc"]) if body.get("acc") is not None else None,
                    trigger=body.get("t") or None,
                    raw_payload=body,
                )
            except Exception:
                logger.warning(
                    "OwnTracksConnector: failed to persist location evidence (non-fatal)",
                    exc_info=True,
                )

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
    # FastAPI application builder (webhook + health + metrics)
    # ------------------------------------------------------------------

    def _dispatch_event_to_main_loop(self, body: dict[str, Any]) -> None:
        """Schedule ``_process_webhook_event`` on the connector's main loop.

        The webhook handler runs inside uvicorn's thread-local event loop, but
        the asyncpg pool and MCP client were created on the main loop and must
        only be touched from there. ``asyncio.run_coroutine_threadsafe`` hops
        the coroutine across loops; we do not await its future, so the HTTP
        response returns immediately. Exceptions are logged via a callback.
        """
        loop = self._main_loop
        if loop is None or loop.is_closed():
            logger.warning(
                "OwnTracksConnector: main loop unavailable, dropping event (type=%r)",
                body.get("_type"),
            )
            return

        future = asyncio.run_coroutine_threadsafe(self._process_webhook_event(body), loop)

        def _log_if_failed(f: Any) -> None:
            try:
                f.result()
            except Exception:
                logger.exception(
                    "OwnTracksConnector: background event processing failed (type=%r)",
                    body.get("_type"),
                )

        future.add_done_callback(_log_if_failed)

    def _build_app(self) -> FastAPI:
        """Build the FastAPI application serving webhook, health, and metrics."""
        app = FastAPI(title="owntracks-connector")

        @app.post("/owntracks/webhook")
        async def webhook(request: Request) -> JSONResponse:
            """Receive OwnTracks webhook POSTs."""
            auth_header = request.headers.get("Authorization", "")
            if not _verify_webhook_auth(auth_header, self._webhook_token):
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

            # Dispatch processing to the main event loop (where asyncpg/MCP live)
            # and return immediately. Awaiting inline from uvicorn's thread loop
            # deadlocks asyncpg with "Lock bound to a different event loop", and
            # OwnTracks mobile clients drop slow responses as SocketTimeoutException.
            self._dispatch_event_to_main_loop(body)

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
# Standalone public auth helpers (task group §2 public API)
# ---------------------------------------------------------------------------
# These provide a stable public interface for tests and callers that do not
# have access to the full OwnTracksConnector class.


async def resolve_owntracks_webhook_token(
    store: CredentialStore | None = None,
) -> str | None:
    """Resolve the OwnTracks webhook bearer token, returning None if absent.

    Resolution order:
    1. ``CredentialStore`` lookup for ``owntracks_webhook_token`` (if *store*
       is provided).
    2. ``OWNTRACKS_WEBHOOK_TOKEN`` environment variable (fallback).

    Parameters
    ----------
    store:
        A ``CredentialStore`` instance backed by the butler DB.  When
        ``None`` (e.g. no DB is available), only the env var is checked.

    Returns
    -------
    str | None
        The resolved token, or ``None`` if neither source has a value.
    """
    # 1. DB lookup via CredentialStore
    if store is not None:
        try:
            raw_db_token = await store.resolve(_DB_KEY, env_fallback=False)
            db_token = (raw_db_token or "").strip()
            if db_token:
                logger.info(
                    "OwnTracks connector: resolved %r from CredentialStore",
                    _DB_KEY,
                )
                return db_token
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "OwnTracks connector: CredentialStore lookup for %r failed (non-fatal): %s",
                _DB_KEY,
                exc,
            )

    # 2. Env var fallback
    env_token = os.environ.get(_ENV_VAR, "").strip()
    if env_token:
        logger.info(
            "OwnTracks connector: resolved webhook token from env var %s",
            _ENV_VAR,
        )
        return env_token

    return None


def _verify_webhook_auth(auth_header: str, expected_token: str) -> bool:
    """Return True if *auth_header* authenticates against *expected_token*.

    Accepts two schemes:
    - ``Bearer <token>``: token compared directly.
    - ``Basic base64(user:password)``: password compared (username ignored).
      This matches the OwnTracks mobile app's native username/password fields,
      which send HTTP Basic auth.

    Comparison uses ``hmac.compare_digest`` for constant-time evaluation.
    Returns False for missing/malformed headers or token mismatch.
    """
    if not auth_header or not expected_token:
        return False

    expected_bytes = expected_token.strip().encode()
    if not expected_bytes:
        return False

    parts = auth_header.split(" ", 1)
    if len(parts) != 2:  # noqa: PLR2004
        return False

    scheme, value = parts[0].lower(), parts[1].strip()
    if scheme == "bearer":
        return hmac.compare_digest(value.encode(), expected_bytes)
    if scheme == "basic":
        try:
            decoded = base64.b64decode(value, validate=True).decode("utf-8", errors="strict")
        except (binascii.Error, ValueError, UnicodeDecodeError):
            return False
        if ":" not in decoded:
            return False
        _, password = decoded.split(":", 1)
        return hmac.compare_digest(password.encode(), expected_bytes)
    return False


def make_bearer_auth_dependency(*, token: str):
    """Return a FastAPI dependency that authenticates the webhook ``Authorization`` header.

    Accepts ``Bearer <token>`` or HTTP Basic auth (password compared to *token*,
    username ignored) — Basic support exists because OwnTracks mobile's
    username/password fields send Basic auth natively.

    Comparison uses ``hmac.compare_digest`` for constant-time evaluation.
    Requests with a missing, malformed, or non-matching credential receive 401.

    Raises
    ------
    ValueError
        If *token* is empty (fail-closed: refuse to create dependency with
        an empty token that would match any empty credential).
    """
    normalized_token = token.strip()
    if not normalized_token:
        raise ValueError("make_bearer_auth_dependency: token must be a non-empty string")

    async def _require_webhook_auth(
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        if authorization is None or not _verify_webhook_auth(authorization, normalized_token):
            logger.debug("OwnTracks webhook: rejected request — auth failure")
            raise HTTPException(status_code=401, detail={"error": "Unauthorized"})

    return _require_webhook_auth


def build_webhook_app(*, token: str) -> FastAPI:
    """Build a minimal FastAPI app with the webhook auth dependency wired.

    This is a thin factory used in tests and for future endpoint assembly.
    Full connector implementation (payload parsing, normalisation, etc.) is
    handled by the main connector entrypoint (task group §3+).

    Parameters
    ----------
    token:
        The resolved bearer token.  Must be non-empty.

    Returns
    -------
    fastapi.FastAPI
        App with ``/owntracks/webhook`` returning 200 on valid auth.
    """
    app = FastAPI()
    require_auth = make_bearer_auth_dependency(token=token)

    @app.post("/owntracks/webhook")
    async def webhook(
        _: None = Depends(require_auth),
    ) -> list:
        # OwnTracks protocol requires an empty JSON array response on success.
        return []

    return app


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
    shared_schema = os.environ.get("BUTLER_SHARED_DB_SCHEMA", "public")
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
        if shared_schema:
            try:
                pool_kwargs["server_settings"] = {"search_path": schema_search_path(shared_schema)}
            except ValueError:
                pass
        pool_kwargs["setup"] = connector_setup_role
        # Register the JSONB codec so dict payloads encode for jsonb columns
        # (e.g. owntracks_points.raw_payload). Without this, every evidence
        # INSERT raises a DataError that persist_location_point swallows,
        # silently dropping all location points. Mirrors steam/home_assistant.
        pool_kwargs["init"] = register_jsonb_codec
        try:
            db_pool = await asyncpg.create_pool(**pool_kwargs)
        except Exception as exc:
            if should_retry_with_ssl_disable(exc, pool_kwargs.get("ssl")):
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
        if db_pool is not None:
            await db_pool.close()
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
