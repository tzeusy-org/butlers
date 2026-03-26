"""Home Assistant connector — filtered event persistence and replay (task 9).

This module provides HA-specific filter reason constructors and a thin
``HAFilterPersistence`` helper that wires the shared ``FilteredEventBuffer``
and ``drain_replay_pending`` for the Home Assistant connector.

The three HA filter layers each produce a distinct ``filter_reason`` value:

Layer 1 — Domain allowlist
    ``"domain_excluded:<domain>"``
    e.g. ``"domain_excluded:media_player"``

Layer 2 — Significance threshold
    ``"insignificant_delta:<device_class>:<delta>"``
    e.g. ``"insignificant_delta:temperature:0.1"``

Layer 3 — Discretion evaluator
    ``"discretion_ignore"``

Usage in the HA connector::

    from butlers.connectors.home_assistant_filter import HAFilterPersistence

    # Instantiate once at connector startup
    ha_filter = HAFilterPersistence(
        endpoint_identity="home_assistant:homeassistant.local:8123",
        db_pool=pool,
        submit_fn=connector._submit_envelope,
    )

    # Record filtered events during event processing
    ha_filter.record_domain_excluded(
        entity_id="media_player.living_room",
        domain="media_player",
        ha_event=raw_ha_event_dict,
        time_fired="2026-03-26T12:00:00+00:00",
    )

    ha_filter.record_insignificant_delta(
        entity_id="sensor.living_room_temperature",
        device_class="temperature",
        delta=0.1,
        ha_event=raw_ha_event_dict,
        time_fired="2026-03-26T12:00:00+00:00",
    )

    ha_filter.record_discretion_ignore(
        entity_id="light.bedroom",
        ha_event=raw_ha_event_dict,
        time_fired="2026-03-26T12:00:00+00:00",
    )

    # After processing a batch of events — flush buffered rows to DB
    await ha_filter.flush()

    # Drain replay_pending rows (up to 10 per call)
    await ha_filter.drain_replay()
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from butlers.connectors.filtered_event_buffer import FilteredEventBuffer, drain_replay_pending

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Connector constants
# ---------------------------------------------------------------------------

CONNECTOR_TYPE = "home_assistant"
# NOTE: When the HA connector wires up replay via drain_replay_pending → submit_fn →
# Switchboard ingest, "home_assistant" must be added to SourceChannel and SourceProvider
# in roster/switchboard/tools/routing/contracts.py, and _ALLOWED_PROVIDERS_BY_CHANNEL
# updated accordingly, otherwise ingest.v1 Pydantic validation will reject replayed rows.
CONNECTOR_CHANNEL = "home_assistant"
CONNECTOR_PROVIDER = "home_assistant"


# ---------------------------------------------------------------------------
# HA-specific filter reason constructors
# ---------------------------------------------------------------------------


def reason_domain_excluded(domain: str) -> str:
    """Return filter reason for Layer 1 — domain allowlist exclusion.

    Format: ``"domain_excluded:<domain>"``

    Args:
        domain: The entity domain that was excluded (e.g. ``"media_player"``).
    """
    return f"domain_excluded:{domain}"


def reason_insignificant_delta(device_class: str, delta: float) -> str:
    """Return filter reason for Layer 2 — significance threshold not met.

    Format: ``"insignificant_delta:<device_class>:<delta>"``

    The delta is formatted with up to 6 significant decimal places; trailing
    zeros are stripped to keep the string compact.

    Args:
        device_class: The entity device class (e.g. ``"temperature"``).
        delta: The absolute numeric delta between old and new state values.
    """
    # Format delta: strip trailing zeros, keep at most 6 decimal places
    delta_str = f"{delta:.6f}".rstrip("0").rstrip(".")
    return f"insignificant_delta:{device_class}:{delta_str}"


def reason_discretion_ignore() -> str:
    """Return filter reason for Layer 3 — discretion evaluator suppressed the event.

    Format: ``"discretion_ignore"``
    """
    return "discretion_ignore"


# ---------------------------------------------------------------------------
# HA full-payload helper
# ---------------------------------------------------------------------------


def ha_full_payload(
    *,
    endpoint_identity: str,
    entity_id: str,
    time_fired: str,
    ha_event: dict[str, Any],
    friendly_name: str | None = None,
    old_state: dict[str, Any] | None = None,
    new_state: dict[str, Any] | None = None,
    domain: str | None = None,
    device_class: str | None = None,
    event_type: str = "state_changed",
) -> dict[str, Any]:
    """Build an ``ingest.v1``-shaped payload dict for a filtered HA event.

    The stored payload preserves enough context for replay or forensic
    inspection. ``schema_version`` is intentionally omitted — it is added
    by ``drain_replay_pending`` at replay time.

    Args:
        endpoint_identity: Connector endpoint identity
            (e.g. ``"home_assistant:homeassistant.local:8123"``).
        entity_id: HA entity ID (e.g. ``"sensor.living_room_temperature"``).
        time_fired: ISO 8601 timestamp when the HA event was fired.
        ha_event: Raw HA event dict as received from the WebSocket or REST API.
        friendly_name: Entity friendly name from HA attributes (optional).
        old_state: Old entity state dict from HA (optional).
        new_state: New entity state dict from HA (optional).
        domain: Entity domain (e.g. ``"sensor"``). Derived from entity_id if
            not provided.
        device_class: Entity device class from attributes (optional).
        event_type: HA event type string (default ``"state_changed"``).

    Returns:
        Dict shaped as an ``ingest.v1`` envelope body (without
        ``schema_version``).
    """
    # Derive domain from entity_id if not explicitly provided
    if domain is None and "." in entity_id:
        domain = entity_id.split(".")[0]

    # Build raw payload for storage
    raw: dict[str, Any] = {
        "entity_id": entity_id,
        "event_type": event_type,
        "domain": domain,
        "device_class": device_class,
        "friendly_name": friendly_name,
    }
    if old_state is not None:
        raw["old_state"] = old_state
    if new_state is not None:
        raw["new_state"] = new_state
    if ha_event:
        raw["ha_event"] = ha_event

    # Build idempotency-consistent external_event_id
    # Format: "ha:<entity_id>:<time_fired_unix_ms>"
    try:
        dt = datetime.fromisoformat(time_fired.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        time_ms = int(dt.timestamp() * 1000)
    except Exception:
        # Fallback: derive a deterministic suffix from the raw time_fired string
        # so that unparseable timestamps don't collapse to the same ID.
        time_ms = int(hashlib.md5(f"{entity_id}:{time_fired}".encode()).hexdigest(), 16) % (  # noqa: S324
            10**13
        )
    external_event_id = f"ha:{entity_id}:{time_ms}"

    return FilteredEventBuffer.full_payload(
        channel=CONNECTOR_CHANNEL,
        provider=CONNECTOR_PROVIDER,
        endpoint_identity=endpoint_identity,
        external_event_id=external_event_id,
        external_thread_id=f"ha:entity:{entity_id}",
        observed_at=time_fired,
        sender_identity=entity_id,
        raw=raw,
    )


# ---------------------------------------------------------------------------
# HAFilterPersistence — wires buffer and replay drain for HA connector
# ---------------------------------------------------------------------------


class HAFilterPersistence:
    """Manage filtered event persistence and replay for the HA connector.

    Wraps ``FilteredEventBuffer`` and ``drain_replay_pending`` with HA-specific
    convenience methods that construct the correct filter reasons and payloads.

    Args:
        endpoint_identity: Connector endpoint identity string
            (e.g. ``"home_assistant:homeassistant.local:8123"``).
        db_pool: asyncpg connection pool used for flush and replay drain.
            May be ``None`` during tests or when DB is unavailable; in that
            case flush/drain calls are no-ops.
        submit_fn: Async callable that accepts an ``ingest.v1`` envelope dict
            and submits it to the Switchboard. Used by the replay drain loop.
    """

    def __init__(
        self,
        endpoint_identity: str,
        db_pool: asyncpg.Pool | None,
        submit_fn: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        self._endpoint_identity = endpoint_identity
        self._db_pool = db_pool
        self._submit_fn = submit_fn
        self._buffer = FilteredEventBuffer(
            connector_type=CONNECTOR_TYPE,
            endpoint_identity=endpoint_identity,
        )

    # ------------------------------------------------------------------
    # Record helpers (Layer 1 / 2 / 3)
    # ------------------------------------------------------------------

    def record_domain_excluded(
        self,
        *,
        entity_id: str,
        domain: str,
        ha_event: dict[str, Any],
        time_fired: str,
        friendly_name: str | None = None,
        old_state: dict[str, Any] | None = None,
        new_state: dict[str, Any] | None = None,
    ) -> None:
        """Record a Layer 1 domain-excluded filtered event.

        Args:
            entity_id: HA entity ID.
            domain: Entity domain that was excluded.
            ha_event: Raw HA event dict.
            time_fired: ISO 8601 event timestamp.
            friendly_name: Entity friendly name (optional).
            old_state: Old state dict (optional).
            new_state: New state dict (optional).
        """
        self._buffer.record(
            external_message_id=f"ha:{entity_id}:{time_fired}",
            source_channel=CONNECTOR_CHANNEL,
            sender_identity=entity_id,
            subject_or_preview=f"HA domain excluded: {entity_id}",
            filter_reason=reason_domain_excluded(domain),
            full_payload=ha_full_payload(
                endpoint_identity=self._endpoint_identity,
                entity_id=entity_id,
                time_fired=time_fired,
                ha_event=ha_event,
                friendly_name=friendly_name,
                old_state=old_state,
                new_state=new_state,
                domain=domain,
                event_type="state_changed",
            ),
        )

    def record_insignificant_delta(
        self,
        *,
        entity_id: str,
        device_class: str,
        delta: float,
        ha_event: dict[str, Any],
        time_fired: str,
        friendly_name: str | None = None,
        old_state: dict[str, Any] | None = None,
        new_state: dict[str, Any] | None = None,
    ) -> None:
        """Record a Layer 2 insignificant-delta filtered event.

        Args:
            entity_id: HA entity ID.
            device_class: Entity device class (e.g. ``"temperature"``).
            delta: Absolute numeric delta between old and new state.
            ha_event: Raw HA event dict.
            time_fired: ISO 8601 event timestamp.
            friendly_name: Entity friendly name (optional).
            old_state: Old state dict (optional).
            new_state: New state dict (optional).
        """
        self._buffer.record(
            external_message_id=f"ha:{entity_id}:{time_fired}",
            source_channel=CONNECTOR_CHANNEL,
            sender_identity=entity_id,
            subject_or_preview=f"HA insignificant delta: {entity_id} ({device_class})",
            filter_reason=reason_insignificant_delta(device_class, delta),
            full_payload=ha_full_payload(
                endpoint_identity=self._endpoint_identity,
                entity_id=entity_id,
                time_fired=time_fired,
                ha_event=ha_event,
                friendly_name=friendly_name,
                old_state=old_state,
                new_state=new_state,
                device_class=device_class,
                event_type="state_changed",
            ),
        )

    def record_discretion_ignore(
        self,
        *,
        entity_id: str,
        ha_event: dict[str, Any],
        time_fired: str,
        friendly_name: str | None = None,
        old_state: dict[str, Any] | None = None,
        new_state: dict[str, Any] | None = None,
        domain: str | None = None,
        device_class: str | None = None,
        event_type: str = "state_changed",
    ) -> None:
        """Record a Layer 3 discretion-suppressed filtered event.

        Args:
            entity_id: HA entity ID.
            ha_event: Raw HA event dict.
            time_fired: ISO 8601 event timestamp.
            friendly_name: Entity friendly name (optional).
            old_state: Old state dict (optional).
            new_state: New state dict (optional).
            domain: Entity domain (optional; derived from entity_id if omitted).
            device_class: Entity device class (optional).
            event_type: HA event type (default ``"state_changed"``).
        """
        self._buffer.record(
            external_message_id=f"ha:{entity_id}:{time_fired}",
            source_channel=CONNECTOR_CHANNEL,
            sender_identity=entity_id,
            subject_or_preview=f"HA discretion ignore: {entity_id}",
            filter_reason=reason_discretion_ignore(),
            full_payload=ha_full_payload(
                endpoint_identity=self._endpoint_identity,
                entity_id=entity_id,
                time_fired=time_fired,
                ha_event=ha_event,
                friendly_name=friendly_name,
                old_state=old_state,
                new_state=new_state,
                domain=domain,
                device_class=device_class,
                event_type=event_type,
            ),
        )

    # ------------------------------------------------------------------
    # Buffer length
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        """Return the number of buffered (not yet flushed) events."""
        return len(self._buffer)

    # ------------------------------------------------------------------
    # Flush and replay
    # ------------------------------------------------------------------

    async def flush(self) -> None:
        """Batch-INSERT all buffered filtered events then clear the buffer.

        If ``db_pool`` is ``None`` (e.g. during tests), this is a no-op.
        Failures are logged as warnings and do not raise.
        """
        if self._db_pool is None:
            logger.debug("HAFilterPersistence.flush: no db_pool, skipping")
            return
        try:
            await self._buffer.flush(self._db_pool)
        except Exception:
            logger.warning("HAFilterPersistence: filtered event flush failed", exc_info=True)

    async def drain_replay(self) -> None:
        """Drain up to 10 ``replay_pending`` rows for this connector.

        Rows are submitted via the ``submit_fn`` provided at construction.
        If ``db_pool`` is ``None`` (e.g. during tests), this is a no-op.
        Failures are logged as warnings and do not raise.
        """
        if self._db_pool is None:
            logger.debug("HAFilterPersistence.drain_replay: no db_pool, skipping")
            return
        try:
            await drain_replay_pending(
                pool=self._db_pool,
                connector_type=CONNECTOR_TYPE,
                endpoint_identity=self._endpoint_identity,
                submit_fn=self._submit_fn,
                drain_logger=logger,
            )
        except Exception:
            logger.warning("HAFilterPersistence: replay queue drain failed", exc_info=True)
