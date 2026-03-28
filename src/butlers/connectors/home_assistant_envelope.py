"""ingest.v1 envelope builder for the Home Assistant connector.

Implements the field mapping from the connector-home-assistant spec §6:

    schema_version           = "ingest.v1"
    source.channel           = "home_assistant"
    source.provider          = "home_assistant"
    source.endpoint_identity = "home_assistant:<ha_host>:<ha_port>"

    For state_changed events:
        event.external_event_id  = "ha:<entity_id>:<time_fired_unix_ms>"
        event.external_thread_id = "ha:entity:<entity_id>"
        event.observed_at        = time_fired (ISO-8601)
        sender.identity          = entity_id  (e.g. "sensor.living_room_temperature")
        payload.raw              = {entity_id, event_type, domain, device_class,
                                    friendly_name, area, old_state, new_state, ha_event}
        payload.normalized_text  = "<friendly_name>: <old> -> <new>[ <unit>]"
                                   or "<entity_id>: <old> -> <new>[ <unit>]"
        control.idempotency_key  = "ha:<endpoint_identity>:<entity_id>:<time_fired_unix_ms>"
        control.policy_tier      = "default"
        control.ingestion_tier   = "full"

    For automation_triggered events:
        event.external_event_id  = "ha:automation:<entity_id>:<time_fired_unix_ms>"
        event.external_thread_id = "ha:automation:<entity_id>"
        event.observed_at        = time_fired (ISO-8601)
        sender.identity          = entity_id  (e.g. "automation.morning_lights")
        payload.raw              = {entity_id, event_type, domain, friendly_name,
                                    area, automation_id, ha_event}
        payload.normalized_text  = "Automation triggered: <friendly_name or entity_id>"
        control.idempotency_key  = "ha:<endpoint_identity>:<entity_id>:<time_fired_unix_ms>"
        control.policy_tier      = "default"
        control.ingestion_tier   = "full"

Idempotency key format (task 6.4):
    ``"ha:<endpoint_identity>:<entity_id>:<time_fired_unix_ms>"``
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CHANNEL = "home_assistant"
_PROVIDER = "home_assistant"
_POLICY_TIER = "default"
_INGESTION_TIER = "full"


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------


def parse_time_fired(time_fired: str) -> datetime:
    """Parse a HA ``time_fired`` ISO-8601 string into a UTC-aware datetime.

    HA may emit timestamps with ``+00:00`` suffix or bare ``Z``.  Naive
    datetimes are assumed to be UTC.

    Args:
        time_fired: ISO-8601 timestamp string from the HA event.

    Returns:
        A timezone-aware datetime in UTC.
    """
    ts = time_fired.replace("Z", "+00:00")
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def time_fired_unix_ms(time_fired: str) -> int:
    """Return ``time_fired`` as milliseconds since the UNIX epoch.

    Args:
        time_fired: ISO-8601 timestamp string from the HA event.

    Returns:
        Integer millisecond timestamp.
    """
    return int(parse_time_fired(time_fired).timestamp() * 1000)


# ---------------------------------------------------------------------------
# Idempotency key (task 6.4)
# ---------------------------------------------------------------------------


def mint_idempotency_key(
    endpoint_identity: str,
    entity_id: str,
    time_fired_ms: int,
) -> str:
    """Construct the idempotency key for any HA event.

    Format: ``"ha:<endpoint_identity>:<entity_id>:<time_fired_unix_ms>"``

    This key is globally unique for a given (HA instance, entity, timestamp)
    triple.  Millisecond precision on ``time_fired_ms`` means two genuine
    events for the same entity at different milliseconds produce distinct keys;
    the Switchboard dedup layer handles any edge-case clock-skew replays.

    Args:
        endpoint_identity: Connector endpoint identity, e.g.
            ``"home_assistant:homeassistant.local:8123"``.
        entity_id: HA entity ID, e.g. ``"sensor.living_room_temperature"``.
        time_fired_ms: Event timestamp as milliseconds since UNIX epoch.

    Returns:
        Idempotency key string.
    """
    return f"ha:{endpoint_identity}:{entity_id}:{time_fired_ms}"


# ---------------------------------------------------------------------------
# normalized_text generation (task 6.3)
# ---------------------------------------------------------------------------


def build_state_changed_normalized_text(
    *,
    entity_id: str,
    friendly_name: str | None,
    old_state: str | None,
    new_state: str | None,
    unit_of_measurement: str | None,
) -> str:
    """Generate normalized_text for a ``state_changed`` event.

    Format: ``"<label>: <old_state> -> <new_state>[ <unit>]"``

    Where ``<label>`` is ``friendly_name`` when available, otherwise
    ``entity_id``.  When ``unit_of_measurement`` is present it is appended
    to ``new_state`` (e.g. ``"22.0 °C"``).  When either state value is
    ``None`` or empty, the string ``"unknown"`` is used as a placeholder.

    Args:
        entity_id: HA entity ID (fallback label).
        friendly_name: Human-readable entity name from HA attributes (optional).
        old_state: Previous state value string (optional).
        new_state: Current state value string (optional).
        unit_of_measurement: Unit from HA attributes (optional,
            e.g. ``"°C"``, ``"%"``, ``"kWh"``).

    Returns:
        Human-readable state-change summary string.
    """
    label = friendly_name if friendly_name else entity_id
    old_val = old_state if old_state else "unknown"
    new_val = new_state if new_state else "unknown"

    if unit_of_measurement:
        new_val = f"{new_val} {unit_of_measurement}"

    return f"{label}: {old_val} -> {new_val}"


def build_automation_triggered_normalized_text(
    *,
    entity_id: str,
    friendly_name: str | None,
) -> str:
    """Generate normalized_text for an ``automation_triggered`` event.

    Format: ``"Automation triggered: <label>"``

    Where ``<label>`` is ``friendly_name`` when available, otherwise
    ``entity_id``.

    Args:
        entity_id: HA entity ID (fallback label).
        friendly_name: Human-readable automation name from HA attributes (optional).

    Returns:
        Human-readable automation trigger description.
    """
    label = friendly_name if friendly_name else entity_id
    return f"Automation triggered: {label}"


# ---------------------------------------------------------------------------
# state_changed envelope builder (task 6.1)
# ---------------------------------------------------------------------------


def build_state_changed_envelope(
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
    unit_of_measurement: str | None = None,
    area_id: str | None = None,
    discretion_reason: str | None = None,
) -> dict[str, Any]:
    """Build a complete ``ingest.v1`` envelope for a ``state_changed`` HA event.

    Implements spec §6.1 (field mapping) and §6.3 (normalized_text generation).

    Args:
        endpoint_identity: Connector endpoint identity, derived from the HA
            base URL (e.g. ``"home_assistant:homeassistant.local:8123"``).
        entity_id: HA entity ID (e.g. ``"sensor.living_room_temperature"``).
        time_fired: ISO-8601 timestamp from the HA event.
        ha_event: Raw HA event dict as received from the WebSocket or REST API.
        friendly_name: Human-readable entity name from HA attributes (optional).
        old_state: Old entity state dict from HA (optional).
        new_state: New entity state dict from HA (optional).
        domain: Entity domain (e.g. ``"sensor"``).  Derived from ``entity_id``
            if not provided.
        device_class: Entity device class from attributes (optional).
        unit_of_measurement: Unit from entity attributes (optional).
        area_id: HA area ID for the entity from the entity registry (optional).
            Included in ``payload.raw`` as ``area``; ``None`` when the entity
            has no area assignment.
        discretion_reason: One-line reason from the discretion evaluator (optional).

    Returns:
        A dict shaped as a complete ``ingest.v1`` envelope, ready for
        Switchboard submission.
    """
    # Derive domain from entity_id if not explicitly provided
    if domain is None and "." in entity_id:
        domain = entity_id.split(".")[0]

    # Extract scalar state values for normalized_text
    old_state_val: str | None = old_state.get("state") if old_state else None
    new_state_val: str | None = new_state.get("state") if new_state else None

    observed_at_dt = parse_time_fired(time_fired)
    time_ms = int(observed_at_dt.timestamp() * 1000)
    idempotency_key = mint_idempotency_key(endpoint_identity, entity_id, time_ms)

    # Build raw payload — preserve full context for replay and forensics
    raw: dict[str, Any] = {
        "entity_id": entity_id,
        "event_type": "state_changed",
        "domain": domain,
        "device_class": device_class,
        "friendly_name": friendly_name,
        "area": area_id,
    }
    if old_state is not None:
        raw["old_state"] = old_state
    if new_state is not None:
        raw["new_state"] = new_state
    if ha_event:
        raw["ha_event"] = ha_event
    if discretion_reason is not None:
        raw["discretion_reason"] = discretion_reason

    normalized_text = build_state_changed_normalized_text(
        entity_id=entity_id,
        friendly_name=friendly_name,
        old_state=old_state_val,
        new_state=new_state_val,
        unit_of_measurement=unit_of_measurement,
    )

    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": _CHANNEL,
            "provider": _PROVIDER,
            "endpoint_identity": endpoint_identity,
        },
        "event": {
            "external_event_id": f"ha:{entity_id}:{time_ms}",
            "external_thread_id": f"ha:entity:{entity_id}",
            "observed_at": observed_at_dt.isoformat(),
        },
        "sender": {
            "identity": entity_id,
        },
        "payload": {
            "raw": raw,
            "normalized_text": normalized_text,
        },
        "control": {
            "idempotency_key": idempotency_key,
            "policy_tier": _POLICY_TIER,
            "ingestion_tier": _INGESTION_TIER,
        },
    }


# ---------------------------------------------------------------------------
# automation_triggered envelope builder (task 6.2)
# ---------------------------------------------------------------------------


def build_automation_triggered_envelope(
    *,
    endpoint_identity: str,
    entity_id: str,
    time_fired: str,
    ha_event: dict[str, Any],
    friendly_name: str | None = None,
    automation_id: str | None = None,
    domain: str | None = None,
    area_id: str | None = None,
    discretion_reason: str | None = None,
) -> dict[str, Any]:
    """Build a complete ``ingest.v1`` envelope for an ``automation_triggered`` HA event.

    Implements spec §6.2 (field mapping) and §6.3 (normalized_text generation).

    ``automation_triggered`` events differ from ``state_changed`` events in that
    they do not have old/new state values.  The sender is still the entity ID per
    design decision D4 (per-entity sender identity).

    Args:
        endpoint_identity: Connector endpoint identity, derived from the HA
            base URL (e.g. ``"home_assistant:homeassistant.local:8123"``).
        entity_id: HA entity ID of the triggered automation
            (e.g. ``"automation.morning_lights"``).
        time_fired: ISO-8601 timestamp from the HA event.
        ha_event: Raw HA event dict as received from the WebSocket or REST API.
        friendly_name: Human-readable automation name from HA attributes (optional).
        automation_id: HA internal automation ID (optional; distinct from ``entity_id``).
        domain: Entity domain.  Defaults to ``"automation"`` if not provided and
            not derivable from ``entity_id``.
        area_id: HA area ID for the entity from the entity registry (optional).
            Included in ``payload.raw`` as ``area``; ``None`` when the entity
            has no area assignment.
        discretion_reason: One-line reason from the discretion evaluator (optional).

    Returns:
        A dict shaped as a complete ``ingest.v1`` envelope, ready for
        Switchboard submission.
    """
    # Derive domain from entity_id if not explicitly provided
    if domain is None and "." in entity_id:
        domain = entity_id.split(".")[0]
    if domain is None:
        domain = "automation"

    observed_at_dt = parse_time_fired(time_fired)
    time_ms = int(observed_at_dt.timestamp() * 1000)
    idempotency_key = mint_idempotency_key(endpoint_identity, entity_id, time_ms)

    # Build raw payload
    raw: dict[str, Any] = {
        "entity_id": entity_id,
        "event_type": "automation_triggered",
        "domain": domain,
        "friendly_name": friendly_name,
        "area": area_id,
    }
    if automation_id is not None:
        raw["automation_id"] = automation_id
    if ha_event:
        raw["ha_event"] = ha_event
    if discretion_reason is not None:
        raw["discretion_reason"] = discretion_reason

    normalized_text = build_automation_triggered_normalized_text(
        entity_id=entity_id,
        friendly_name=friendly_name,
    )

    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": _CHANNEL,
            "provider": _PROVIDER,
            "endpoint_identity": endpoint_identity,
        },
        "event": {
            "external_event_id": f"ha:automation:{entity_id}:{time_ms}",
            "external_thread_id": f"ha:automation:{entity_id}",
            "observed_at": observed_at_dt.isoformat(),
        },
        "sender": {
            "identity": entity_id,
        },
        "payload": {
            "raw": raw,
            "normalized_text": normalized_text,
        },
        "control": {
            "idempotency_key": idempotency_key,
            "policy_tier": _POLICY_TIER,
            "ingestion_tier": _INGESTION_TIER,
        },
    }
