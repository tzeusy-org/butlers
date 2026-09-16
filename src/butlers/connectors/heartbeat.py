"""Shared heartbeat background task for connector runtimes.

This module provides a reusable heartbeat mechanism that all connectors can use
to report liveness and operational statistics to the Switchboard.

Key features:
- Generates stable instance_id (UUID) at process startup
- Background asyncio task fires every CONNECTOR_HEARTBEAT_INTERVAL_S
- Collects current counter values from ConnectorMetrics
- Determines health state based on connector condition
- Submits heartbeat via CachedMCPClient
- Graceful shutdown on task cancellation
- Failures logged but never crash or block ingestion

Environment variables:
- CONNECTOR_HEARTBEAT_INTERVAL_S (optional, default: 120): Heartbeat interval in seconds
- CONNECTOR_HEARTBEAT_ENABLED (optional, default: true): Enable/disable heartbeat
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from butlers.connectors.mcp_client import CachedMCPClient
    from butlers.connectors.metrics import ConnectorMetrics

logger = logging.getLogger(__name__)

# Default heartbeat interval: 2 minutes (120 seconds)
DEFAULT_HEARTBEAT_INTERVAL_S = 120

# Minimum and maximum heartbeat intervals (per spec)
MIN_HEARTBEAT_INTERVAL_S = 30
MAX_HEARTBEAT_INTERVAL_S = 300


@dataclass
class HeartbeatConfig:
    """Configuration for connector heartbeat task."""

    connector_type: str
    endpoint_identity: str
    version: str | None = None
    interval_s: int = DEFAULT_HEARTBEAT_INTERVAL_S
    enabled: bool = True

    @classmethod
    def from_env(
        cls,
        connector_type: str,
        endpoint_identity: str,
        version: str | None = None,
    ) -> HeartbeatConfig:
        """Load heartbeat configuration from environment variables.

        Args:
            connector_type: Type of connector (e.g., "telegram_bot", "gmail")
            endpoint_identity: Identity of the endpoint (bot username, email, etc.)
            version: Optional connector software version

        Returns:
            HeartbeatConfig instance
        """
        interval_s = int(
            os.environ.get("CONNECTOR_HEARTBEAT_INTERVAL_S", str(DEFAULT_HEARTBEAT_INTERVAL_S))
        )

        # Enforce bounds
        if interval_s < MIN_HEARTBEAT_INTERVAL_S:
            logger.warning(
                "CONNECTOR_HEARTBEAT_INTERVAL_S=%d is below minimum %d, using minimum",
                interval_s,
                MIN_HEARTBEAT_INTERVAL_S,
            )
            interval_s = MIN_HEARTBEAT_INTERVAL_S
        elif interval_s > MAX_HEARTBEAT_INTERVAL_S:
            logger.warning(
                "CONNECTOR_HEARTBEAT_INTERVAL_S=%d is above maximum %d, using maximum",
                interval_s,
                MAX_HEARTBEAT_INTERVAL_S,
            )
            interval_s = MAX_HEARTBEAT_INTERVAL_S

        enabled_str = os.environ.get("CONNECTOR_HEARTBEAT_ENABLED", "true").lower()
        enabled = enabled_str not in ("false", "0", "no", "off")

        return cls(
            connector_type=connector_type,
            endpoint_identity=endpoint_identity,
            version=version,
            interval_s=interval_s,
            enabled=enabled,
        )


class CounterRead(dict[str, int]):
    """Counter values plus the authority state of the registry read.

    The heartbeat v1 wire contract still requires integer counter fields, so
    the mapping retains the established zero placeholders for fields whose
    source could not be observed.  Callers that need to distinguish a real
    zero from an unreadable source can use ``availability`` and
    ``unavailable_fields``; the state is deliberately kept out of the wire
    payload to avoid a protocol change.
    """

    availability: Literal["available", "unavailable"]
    unavailable_fields: frozenset[str]

    def __init__(
        self,
        values: dict[str, int],
        *,
        unavailable_fields: frozenset[str],
    ) -> None:
        super().__init__(values)
        self.unavailable_fields = unavailable_fields
        self.availability = "unavailable" if unavailable_fields else "available"


class ConnectorHeartbeat:
    """Heartbeat background task for connector runtimes.

    This class manages the heartbeat lifecycle:
    - Generates stable instance_id at creation
    - Runs background task that fires every interval_s
    - Collects metrics and determines health state
    - Submits heartbeat envelope to Switchboard
    - Gracefully shuts down on cancellation
    """

    def __init__(
        self,
        config: HeartbeatConfig,
        mcp_client: CachedMCPClient,
        metrics: ConnectorMetrics,
        get_health_state: Callable[[], tuple[str, str | None]],
        get_checkpoint: Callable[[], tuple[str | None, datetime | None]] | None = None,
        get_capabilities: Callable[[], dict[str, object]] | None = None,
    ) -> None:
        """Initialize heartbeat task.

        Args:
            config: Heartbeat configuration
            mcp_client: MCP client for submitting heartbeats
            metrics: Metrics collector for reading counter values
            get_health_state: Callable that returns (state, error_message) tuple
            get_checkpoint: Optional callable that returns (cursor, updated_at) tuple
            get_capabilities: Optional callable that returns a capabilities dict
                (e.g. {"backfill": True}). Included in heartbeat as "capabilities" key.
        """
        self._config = config
        self._mcp_client = mcp_client
        self._metrics = metrics
        self._get_health_state = get_health_state
        self._get_checkpoint = get_checkpoint
        self._get_capabilities = get_capabilities

        # Generate stable instance_id for this process
        self._instance_id = uuid4()

        # Track process start time for uptime calculation
        self._start_time = time.time()

        # Background task handle
        self._task: asyncio.Task | None = None

        logger.info(
            "Initialized heartbeat: connector_type=%s, endpoint_identity=%s, instance_id=%s, "
            "interval_s=%d, enabled=%s",
            config.connector_type,
            config.endpoint_identity,
            self._instance_id,
            config.interval_s,
            config.enabled,
        )

    @property
    def instance_id(self) -> UUID:
        """Get the stable instance ID for this connector process."""
        return self._instance_id

    def start(self) -> None:
        """Start the heartbeat background task."""
        if not self._config.enabled:
            logger.info(
                "Heartbeat disabled via CONNECTOR_HEARTBEAT_ENABLED=false for %s/%s",
                self._config.connector_type,
                self._config.endpoint_identity,
            )
            return

        if self._task is not None:
            logger.warning(
                "Heartbeat task already running for %s/%s",
                self._config.connector_type,
                self._config.endpoint_identity,
            )
            return

        self._task = asyncio.create_task(self._heartbeat_loop())
        logger.info(
            "Started heartbeat task: connector_type=%s, endpoint_identity=%s",
            self._config.connector_type,
            self._config.endpoint_identity,
        )

    async def stop(self) -> None:
        """Stop the heartbeat background task gracefully."""
        if self._task is None:
            return

        logger.info(
            "Stopping heartbeat task: connector_type=%s, endpoint_identity=%s",
            self._config.connector_type,
            self._config.endpoint_identity,
        )

        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass

        self._task = None
        logger.info(
            "Heartbeat task stopped: connector_type=%s, endpoint_identity=%s",
            self._config.connector_type,
            self._config.endpoint_identity,
        )

    async def _heartbeat_loop(self) -> None:
        """Main heartbeat loop that fires every interval_s."""
        try:
            while True:
                await asyncio.sleep(self._config.interval_s)

                try:
                    await self._send_heartbeat()
                except Exception:
                    # Log but don't crash the loop
                    logger.exception(
                        "Failed to send heartbeat for %s/%s",
                        self._config.connector_type,
                        self._config.endpoint_identity,
                    )

        except asyncio.CancelledError:
            logger.debug(
                "Heartbeat loop cancelled for %s/%s",
                self._config.connector_type,
                self._config.endpoint_identity,
            )
            raise

    async def _send_heartbeat(self) -> None:
        """Collect metrics, build envelope, and submit heartbeat."""
        # Calculate uptime
        uptime_s = int(time.time() - self._start_time)

        # Get health state from connector
        state, error_message = self._get_health_state()

        # Get checkpoint if available
        checkpoint_cursor = None
        checkpoint_updated_at = None
        if self._get_checkpoint is not None:
            checkpoint_cursor, checkpoint_updated_at = self._get_checkpoint()

        # Collect counter values from Prometheus metrics
        # Note: Prometheus counters are cumulative, so we read the raw counter values
        counter_read = self._collect_counters()
        if counter_read.availability == "unavailable":
            logger.warning(
                "Heartbeat counter source unavailable for %s/%s: fields=%s",
                self._config.connector_type,
                self._config.endpoint_identity,
                ",".join(sorted(counter_read.unavailable_fields)),
            )
        # ``CounterRead`` is a dict subclass so the v1 wire payload remains
        # exactly the established integer-only counters mapping.
        counters = dict(counter_read)

        # Build heartbeat envelope
        envelope = {
            "schema_version": "connector.heartbeat.v1",
            "connector": {
                "connector_type": self._config.connector_type,
                "endpoint_identity": self._config.endpoint_identity,
                "instance_id": str(self._instance_id),
                "version": self._config.version,
            },
            "status": {
                "state": state,
                "error_message": error_message,
                "uptime_s": uptime_s,
            },
            "counters": counters,
            "sent_at": datetime.now(UTC).isoformat(),
        }

        # Add checkpoint if available
        if checkpoint_cursor is not None or checkpoint_updated_at is not None:
            envelope["checkpoint"] = {
                "cursor": checkpoint_cursor,
                "updated_at": checkpoint_updated_at.isoformat() if checkpoint_updated_at else None,
            }

        # Add capabilities if available (e.g. {"backfill": True})
        if self._get_capabilities is not None:
            capabilities = self._get_capabilities()
            if capabilities:
                envelope["capabilities"] = capabilities

        # Submit via MCP
        try:
            result = await self._mcp_client.call_tool("connector.heartbeat", envelope)

            if isinstance(result, dict) and result.get("status") == "accepted":
                logger.debug(
                    "Heartbeat accepted: connector_type=%s, endpoint_identity=%s, "
                    "instance_id=%s, state=%s, uptime=%ds",
                    self._config.connector_type,
                    self._config.endpoint_identity,
                    self._instance_id,
                    state,
                    uptime_s,
                )
            else:
                logger.warning(
                    "Unexpected heartbeat response: %s",
                    result,
                    extra={
                        "connector_type": self._config.connector_type,
                        "endpoint_identity": self._config.endpoint_identity,
                    },
                )

        except Exception:
            # Log but don't raise — heartbeat failures should never block ingestion
            logger.exception(
                "Failed to submit heartbeat for %s/%s",
                self._config.connector_type,
                self._config.endpoint_identity,
            )

    def _collect_counters(self) -> CounterRead:
        """Collect current counter values from Prometheus metrics.

        Returns a dict matching the connector.heartbeat.v1 counters schema.

        Note: We read the raw counter values from the Prometheus registry.
        These are monotonic cumulative values since process start.
        """
        from prometheus_client import REGISTRY

        counters = {
            "messages_ingested": 0,
            "messages_failed": 0,
            "source_api_calls": 0,
            "checkpoint_saves": 0,
            "dedupe_accepted": 0,
        }
        observed_fields: set[str] = set()

        def sample_value(sample: object, family: str) -> int | None:
            """Return a finite non-negative ``*_total`` sample value.

            ``prometheus_client`` exposes a Counter family as the base name
            (without ``_total``), while its samples include both the real
            ``<family>_total`` counter and metadata such as
            ``<family>_created``.  The metadata sample is a Unix timestamp and
            must never become an operational count.

            Some test/fake registries omit ``sample.name`` because the family
            itself is already selected.  In that case the family name remains
            the authority; when a name is present it must be the exact total
            sample.  Values are parsed as finite, non-negative numbers before
            the integer wire conversion so malformed, NaN, and infinity
            samples are ignored rather than raising or fabricating a count.
            """
            sample_name = getattr(sample, "name", None)
            expected_name = f"{family}_total"
            if isinstance(sample_name, str) and sample_name != expected_name:
                return None

            raw = getattr(sample, "value", None)
            if isinstance(raw, bool):
                return None
            try:
                numeric = float(raw)
            except (TypeError, ValueError, OverflowError):
                return None
            if not math.isfinite(numeric) or numeric < 0:
                return None
            return int(numeric)

        # Read counter values from Prometheus registry.
        # NOTE: prometheus_client strips the ``_total`` suffix from Counter
        # names when returning MetricFamily objects via ``collect()``, so we
        # must compare against the *base* name (without ``_total``).
        for metric in REGISTRY.collect():
            metric_name = getattr(metric, "name", None)
            if not isinstance(metric_name, str):
                continue
            # ``collect()`` normally returns the base family name.  Accept the
            # explicit suffix too for registry fakes and alternate collectors.
            family = (
                metric_name[: -len("_total")] if metric_name.endswith("_total") else metric_name
            )
            # Ingest submissions
            if family == "connector_ingest_submissions":
                for sample in metric.samples:
                    labels = sample.labels
                    if (
                        labels.get("connector_type") == self._config.connector_type
                        and labels.get("endpoint_identity") == self._config.endpoint_identity
                    ):
                        status = labels.get("status", "")
                        value = sample_value(sample, family)
                        if value is None:
                            continue

                        if status == "success":
                            counters["messages_ingested"] += value
                            observed_fields.add("messages_ingested")
                        elif status == "error":
                            counters["messages_failed"] += value
                            observed_fields.add("messages_failed")
                        elif status == "duplicate":
                            counters["dedupe_accepted"] += value
                            observed_fields.add("dedupe_accepted")

            # Source API calls
            elif family == "connector_source_api_calls":
                for sample in metric.samples:
                    labels = sample.labels
                    if (
                        labels.get("connector_type") == self._config.connector_type
                        and labels.get("endpoint_identity") == self._config.endpoint_identity
                    ):
                        value = sample_value(sample, family)
                        if value is not None:
                            counters["source_api_calls"] += value
                            observed_fields.add("source_api_calls")

            # Checkpoint saves
            elif family == "connector_checkpoint_saves":
                for sample in metric.samples:
                    labels = sample.labels
                    if (
                        labels.get("connector_type") == self._config.connector_type
                        and labels.get("endpoint_identity") == self._config.endpoint_identity
                        and labels.get("status") == "success"
                    ):
                        value = sample_value(sample, family)
                        if value is not None:
                            counters["checkpoint_saves"] += value
                            observed_fields.add("checkpoint_saves")

        return CounterRead(
            counters,
            unavailable_fields=frozenset(set(counters) - observed_fields),
        )
