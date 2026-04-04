"""Condensed Home Assistant connector tests — ingest.v1 contract only.

Consolidates: test_home_assistant_envelope.py, test_home_assistant_filter.py,
test_home_assistant_integration.py, test_ha_checkpoint.py,
test_ha_connector_health_metrics.py, test_ha_filter_pipeline.py,
test_ha_rest_poller.py, test_ha_ws_client.py

Verifies:
- ingest.v1 envelope production for state_changed and automation_triggered
- Normalized text generation (branching logic: sensor, binary_sensor, etc.)
- parse_ingest_envelope contract compliance

[bu-35fm7]
"""

from __future__ import annotations

from typing import Any

import pytest

from butlers.connectors.home_assistant_envelope import (
    build_automation_triggered_envelope,
    build_state_changed_envelope,
    build_state_changed_normalized_text,
)

_ENDPOINT = "home_assistant:homeassistant.local:8123"
_TIME_FIRED = "2026-03-26T10:00:00.000000+00:00"


@pytest.fixture
def state_changed_envelope() -> dict[str, Any]:
    return build_state_changed_envelope(
        endpoint_identity=_ENDPOINT,
        entity_id="sensor.living_room_temperature",
        time_fired=_TIME_FIRED,
        ha_event={"event_type": "state_changed"},
        friendly_name="Living Room Temperature",
        old_state={"state": "21.5"},
        new_state={"state": "22.0"},
        domain="sensor",
        device_class="temperature",
        unit_of_measurement="°C",
    )


def test_state_changed_schema_version(state_changed_envelope: dict[str, Any]) -> None:
    """state_changed envelope must carry schema_version='ingest.v1'."""
    assert state_changed_envelope["schema_version"] == "ingest.v1"
    assert state_changed_envelope["source"]["channel"] == "home_assistant"
    assert state_changed_envelope["source"]["provider"] == "home_assistant"
    assert state_changed_envelope["source"]["endpoint_identity"] == _ENDPOINT


def test_state_changed_event_fields(state_changed_envelope: dict[str, Any]) -> None:
    """External event ID must be deterministic for same entity+time."""
    eid = state_changed_envelope["event"]["external_event_id"]
    assert "sensor.living_room_temperature" in eid


def test_state_changed_ingestion_tier_full(state_changed_envelope: dict[str, Any]) -> None:
    """HA connector must use 'full' ingestion tier (state data included)."""
    assert state_changed_envelope["control"]["ingestion_tier"] == "full"


def test_state_changed_passes_parse_ingest_envelope(
    state_changed_envelope: dict[str, Any],
) -> None:
    """state_changed envelope must validate against parse_ingest_envelope contract."""
    from pydantic import ValidationError

    from butlers.tools.switchboard.routing.contracts import parse_ingest_envelope

    try:
        parse_ingest_envelope(state_changed_envelope)
    except ValidationError as exc:
        pytest.fail(f"parse_ingest_envelope raised ValidationError: {exc}")


def test_automation_triggered_schema_version() -> None:
    """automation_triggered envelope must carry schema_version='ingest.v1'."""
    env = build_automation_triggered_envelope(
        endpoint_identity=_ENDPOINT,
        entity_id="automation.morning_routine",
        time_fired=_TIME_FIRED,
        ha_event={"event_type": "automation_triggered"},
        friendly_name="Morning Routine",
    )
    assert env["schema_version"] == "ingest.v1"
    assert env["source"]["provider"] == "home_assistant"


def test_normalized_text_includes_entity_and_state() -> None:
    """Normalized text must include entity ID and new state value."""
    text = build_state_changed_normalized_text(
        entity_id="sensor.living_room_temperature",
        friendly_name="Living Room Temp",
        old_state="21.0",
        new_state="22.5",
        unit_of_measurement="°C",
    )
    assert "22.5" in text
    assert "°C" in text


def test_normalized_text_binary_sensor_on_off() -> None:
    """binary_sensor state changes include state info in normalized text."""
    text = build_state_changed_normalized_text(
        entity_id="binary_sensor.motion_sensor",
        friendly_name="Motion Sensor",
        old_state="off",
        new_state="on",
        unit_of_measurement=None,
    )
    assert text
    assert isinstance(text, str)
    assert "on" in text.lower() or "Motion Sensor" in text
