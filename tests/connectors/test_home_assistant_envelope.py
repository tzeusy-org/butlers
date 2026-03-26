"""Tests for Home Assistant ingest.v1 envelope builder (tasks 6.1–6.5).

Covers:
- 6.1  state_changed event to ingest.v1 envelope field mapping
- 6.2  automation_triggered event to ingest.v1 envelope field mapping
- 6.3  normalized_text generation (friendly_name, old/new state, unit_of_measurement)
- 6.4  idempotency key construction ("ha:<endpoint_identity>:<entity_id>:<time_ms>")
- 6.5  envelope construction (field mapping, normalized text, idempotency keys)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from butlers.connectors.home_assistant_envelope import (
    build_automation_triggered_envelope,
    build_automation_triggered_normalized_text,
    build_state_changed_envelope,
    build_state_changed_normalized_text,
    mint_idempotency_key,
    parse_time_fired,
    time_fired_unix_ms,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Shared test constants
# ---------------------------------------------------------------------------

_ENDPOINT_IDENTITY = "home_assistant:homeassistant.local:8123"
_TIME_FIRED = "2026-03-26T12:00:00.000000+00:00"
_TIME_FIRED_MS = 1774526400000  # 2026-03-26T12:00:00Z in milliseconds

_ENTITY_ID_TEMP = "sensor.living_room_temperature"
_ENTITY_ID_LOCK = "lock.front_door"
_ENTITY_ID_LIGHT = "light.bedroom"
_ENTITY_ID_AUTOMATION = "automation.morning_lights"

_HA_STATE_CHANGED_EVENT: dict[str, Any] = {
    "event_type": "state_changed",
    "data": {
        "entity_id": _ENTITY_ID_TEMP,
        "new_state": {"state": "22.0", "attributes": {"friendly_name": "Living Room Temperature"}},
        "old_state": {"state": "21.5", "attributes": {}},
    },
    "time_fired": _TIME_FIRED,
}

_HA_AUTOMATION_EVENT: dict[str, Any] = {
    "event_type": "automation_triggered",
    "data": {
        "entity_id": _ENTITY_ID_AUTOMATION,
        "name": "Morning Lights",
        "source": "state",
    },
    "time_fired": _TIME_FIRED,
}


# ---------------------------------------------------------------------------
# parse_time_fired
# ---------------------------------------------------------------------------


class TestParseTimeFired:
    """parse_time_fired converts various HA timestamp formats correctly."""

    def test_parses_plus_offset(self) -> None:
        dt = parse_time_fired("2026-03-26T12:00:00+00:00")
        assert dt.tzinfo is not None
        assert dt.year == 2026
        assert dt.month == 3
        assert dt.day == 26

    def test_parses_z_suffix(self) -> None:
        dt = parse_time_fired("2026-03-26T12:00:00Z")
        assert dt.tzinfo is not None
        assert dt.utcoffset().total_seconds() == 0  # type: ignore[union-attr]

    def test_parses_microseconds(self) -> None:
        dt = parse_time_fired("2026-03-26T12:00:00.000000+00:00")
        assert dt.tzinfo is not None
        assert dt.microsecond == 0

    def test_returns_utc_aware(self) -> None:
        dt = parse_time_fired("2024-01-15T08:30:00+05:30")
        assert dt.tzinfo is not None
        assert dt.utcoffset().total_seconds() == 0  # type: ignore[union-attr]
        # 08:30 +05:30 = 03:00 UTC
        assert dt.hour == 3
        assert dt.minute == 0


# ---------------------------------------------------------------------------
# time_fired_unix_ms
# ---------------------------------------------------------------------------


class TestTimeFiredUnixMs:
    """time_fired_unix_ms converts HA timestamps to millisecond integers."""

    def test_known_value(self) -> None:
        ms = time_fired_unix_ms(_TIME_FIRED)
        assert ms == _TIME_FIRED_MS

    def test_returns_integer(self) -> None:
        ms = time_fired_unix_ms(_TIME_FIRED)
        assert isinstance(ms, int)

    def test_monotonic_with_increasing_timestamps(self) -> None:
        ms1 = time_fired_unix_ms("2026-03-26T12:00:00+00:00")
        ms2 = time_fired_unix_ms("2026-03-26T12:00:01+00:00")
        assert ms2 > ms1
        assert ms2 - ms1 == 1000  # 1 second = 1000 ms


# ---------------------------------------------------------------------------
# mint_idempotency_key (task 6.4)
# ---------------------------------------------------------------------------


class TestMintIdempotencyKey:
    """Idempotency key format follows spec §6.4."""

    def test_format_is_correct(self) -> None:
        key = mint_idempotency_key(_ENDPOINT_IDENTITY, _ENTITY_ID_TEMP, _TIME_FIRED_MS)
        expected = f"ha:{_ENDPOINT_IDENTITY}:{_ENTITY_ID_TEMP}:{_TIME_FIRED_MS}"
        assert key == expected

    def test_starts_with_ha_prefix(self) -> None:
        key = mint_idempotency_key(_ENDPOINT_IDENTITY, _ENTITY_ID_TEMP, _TIME_FIRED_MS)
        assert key.startswith("ha:")

    def test_contains_endpoint_identity(self) -> None:
        key = mint_idempotency_key(_ENDPOINT_IDENTITY, _ENTITY_ID_TEMP, _TIME_FIRED_MS)
        assert _ENDPOINT_IDENTITY in key

    def test_contains_entity_id(self) -> None:
        key = mint_idempotency_key(_ENDPOINT_IDENTITY, _ENTITY_ID_TEMP, _TIME_FIRED_MS)
        assert _ENTITY_ID_TEMP in key

    def test_contains_timestamp_ms(self) -> None:
        key = mint_idempotency_key(_ENDPOINT_IDENTITY, _ENTITY_ID_TEMP, _TIME_FIRED_MS)
        assert str(_TIME_FIRED_MS) in key

    def test_unique_by_entity_id(self) -> None:
        key1 = mint_idempotency_key(_ENDPOINT_IDENTITY, "sensor.a", _TIME_FIRED_MS)
        key2 = mint_idempotency_key(_ENDPOINT_IDENTITY, "sensor.b", _TIME_FIRED_MS)
        assert key1 != key2

    def test_unique_by_timestamp(self) -> None:
        key1 = mint_idempotency_key(_ENDPOINT_IDENTITY, _ENTITY_ID_TEMP, 1000)
        key2 = mint_idempotency_key(_ENDPOINT_IDENTITY, _ENTITY_ID_TEMP, 2000)
        assert key1 != key2

    def test_unique_by_endpoint_identity(self) -> None:
        key1 = mint_idempotency_key(
            "home_assistant:ha1.local:8123", _ENTITY_ID_TEMP, _TIME_FIRED_MS
        )
        key2 = mint_idempotency_key(
            "home_assistant:ha2.local:8123", _ENTITY_ID_TEMP, _TIME_FIRED_MS
        )
        assert key1 != key2


# ---------------------------------------------------------------------------
# build_state_changed_normalized_text (task 6.3)
# ---------------------------------------------------------------------------


class TestBuildStateChangedNormalizedText:
    """normalized_text generation for state_changed events."""

    def test_uses_friendly_name_as_label(self) -> None:
        text = build_state_changed_normalized_text(
            entity_id=_ENTITY_ID_TEMP,
            friendly_name="Living Room Temp",
            old_state="21.5",
            new_state="22.0",
            unit_of_measurement=None,
        )
        assert text.startswith("Living Room Temp:")

    def test_falls_back_to_entity_id_without_friendly_name(self) -> None:
        text = build_state_changed_normalized_text(
            entity_id=_ENTITY_ID_TEMP,
            friendly_name=None,
            old_state="21.5",
            new_state="22.0",
            unit_of_measurement=None,
        )
        assert text.startswith(_ENTITY_ID_TEMP + ":")

    def test_includes_old_and_new_state(self) -> None:
        text = build_state_changed_normalized_text(
            entity_id=_ENTITY_ID_TEMP,
            friendly_name=None,
            old_state="21.5",
            new_state="22.0",
            unit_of_measurement=None,
        )
        assert "21.5" in text
        assert "22.0" in text
        assert "->" in text

    def test_appends_unit_of_measurement(self) -> None:
        text = build_state_changed_normalized_text(
            entity_id=_ENTITY_ID_TEMP,
            friendly_name=None,
            old_state="21.5",
            new_state="22.0",
            unit_of_measurement="°C",
        )
        assert "22.0 °C" in text

    def test_no_unit_no_append(self) -> None:
        text = build_state_changed_normalized_text(
            entity_id=_ENTITY_ID_TEMP,
            friendly_name=None,
            old_state="21.5",
            new_state="22.0",
            unit_of_measurement=None,
        )
        assert "°C" not in text
        assert "22.0" in text

    def test_none_old_state_uses_unknown(self) -> None:
        text = build_state_changed_normalized_text(
            entity_id=_ENTITY_ID_TEMP,
            friendly_name=None,
            old_state=None,
            new_state="22.0",
            unit_of_measurement=None,
        )
        assert "unknown" in text

    def test_none_new_state_uses_unknown(self) -> None:
        text = build_state_changed_normalized_text(
            entity_id=_ENTITY_ID_TEMP,
            friendly_name=None,
            old_state="21.5",
            new_state=None,
            unit_of_measurement=None,
        )
        assert "unknown" in text

    def test_empty_old_state_uses_unknown(self) -> None:
        text = build_state_changed_normalized_text(
            entity_id=_ENTITY_ID_TEMP,
            friendly_name=None,
            old_state="",
            new_state="on",
            unit_of_measurement=None,
        )
        assert "unknown" in text

    def test_binary_lock_state(self) -> None:
        text = build_state_changed_normalized_text(
            entity_id=_ENTITY_ID_LOCK,
            friendly_name="Front Door",
            old_state="unlocked",
            new_state="locked",
            unit_of_measurement=None,
        )
        assert "Front Door:" in text
        assert "unlocked" in text
        assert "locked" in text

    def test_unavailable_state_transition(self) -> None:
        text = build_state_changed_normalized_text(
            entity_id=_ENTITY_ID_TEMP,
            friendly_name=None,
            old_state="22.0",
            new_state="unavailable",
            unit_of_measurement=None,
        )
        assert "unavailable" in text

    def test_returns_string(self) -> None:
        text = build_state_changed_normalized_text(
            entity_id=_ENTITY_ID_TEMP,
            friendly_name="Temp",
            old_state="21.5",
            new_state="22.0",
            unit_of_measurement="°C",
        )
        assert isinstance(text, str)
        assert len(text) > 0

    def test_humidity_with_percent_unit(self) -> None:
        text = build_state_changed_normalized_text(
            entity_id="sensor.bedroom_humidity",
            friendly_name="Bedroom Humidity",
            old_state="55",
            new_state="60",
            unit_of_measurement="%",
        )
        assert "60 %" in text
        assert "Bedroom Humidity:" in text


# ---------------------------------------------------------------------------
# build_automation_triggered_normalized_text (task 6.3)
# ---------------------------------------------------------------------------


class TestBuildAutomationTriggeredNormalizedText:
    """normalized_text generation for automation_triggered events."""

    def test_uses_friendly_name(self) -> None:
        text = build_automation_triggered_normalized_text(
            entity_id=_ENTITY_ID_AUTOMATION,
            friendly_name="Morning Lights",
        )
        assert "Morning Lights" in text

    def test_falls_back_to_entity_id(self) -> None:
        text = build_automation_triggered_normalized_text(
            entity_id=_ENTITY_ID_AUTOMATION,
            friendly_name=None,
        )
        assert _ENTITY_ID_AUTOMATION in text

    def test_prefix_is_automation_triggered(self) -> None:
        text = build_automation_triggered_normalized_text(
            entity_id=_ENTITY_ID_AUTOMATION,
            friendly_name="Morning Lights",
        )
        assert text.startswith("Automation triggered:")

    def test_returns_string(self) -> None:
        text = build_automation_triggered_normalized_text(
            entity_id=_ENTITY_ID_AUTOMATION,
            friendly_name=None,
        )
        assert isinstance(text, str)
        assert len(text) > 0


# ---------------------------------------------------------------------------
# build_state_changed_envelope (task 6.1) — field mapping
# ---------------------------------------------------------------------------


@pytest.fixture
def state_changed_envelope() -> dict[str, Any]:
    """A fully-populated state_changed envelope built from known test inputs."""
    return build_state_changed_envelope(
        endpoint_identity=_ENDPOINT_IDENTITY,
        entity_id=_ENTITY_ID_TEMP,
        time_fired=_TIME_FIRED,
        ha_event=_HA_STATE_CHANGED_EVENT,
        friendly_name="Living Room Temperature",
        old_state={"state": "21.5", "attributes": {}},
        new_state={"state": "22.0", "attributes": {"unit_of_measurement": "°C"}},
        domain="sensor",
        device_class="temperature",
        unit_of_measurement="°C",
        discretion_reason="Temperature crossed threshold.",
    )


class TestBuildStateChangedEnvelope:
    """state_changed envelope: full ingest.v1 field mapping (task 6.1)."""

    def test_schema_version(self, state_changed_envelope: dict) -> None:
        assert state_changed_envelope["schema_version"] == "ingest.v1"

    def test_source_channel(self, state_changed_envelope: dict) -> None:
        assert state_changed_envelope["source"]["channel"] == "home_assistant"

    def test_source_provider(self, state_changed_envelope: dict) -> None:
        assert state_changed_envelope["source"]["provider"] == "home_assistant"

    def test_source_endpoint_identity(self, state_changed_envelope: dict) -> None:
        assert state_changed_envelope["source"]["endpoint_identity"] == _ENDPOINT_IDENTITY

    def test_event_external_event_id_format(self, state_changed_envelope: dict) -> None:
        ext_id = state_changed_envelope["event"]["external_event_id"]
        assert ext_id == f"ha:{_ENTITY_ID_TEMP}:{_TIME_FIRED_MS}"

    def test_event_external_thread_id_groups_by_entity(self, state_changed_envelope: dict) -> None:
        thread_id = state_changed_envelope["event"]["external_thread_id"]
        assert thread_id == f"ha:entity:{_ENTITY_ID_TEMP}"

    def test_event_observed_at_is_iso8601(self, state_changed_envelope: dict) -> None:
        obs = state_changed_envelope["event"]["observed_at"]
        parsed = datetime.fromisoformat(obs)
        assert parsed.tzinfo is not None

    def test_event_observed_at_is_utc(self, state_changed_envelope: dict) -> None:
        obs = state_changed_envelope["event"]["observed_at"]
        parsed = datetime.fromisoformat(obs)
        assert parsed.utcoffset().total_seconds() == 0  # type: ignore[union-attr]

    def test_sender_identity_is_entity_id(self, state_changed_envelope: dict) -> None:
        assert state_changed_envelope["sender"]["identity"] == _ENTITY_ID_TEMP

    def test_payload_raw_entity_id(self, state_changed_envelope: dict) -> None:
        assert state_changed_envelope["payload"]["raw"]["entity_id"] == _ENTITY_ID_TEMP

    def test_payload_raw_event_type(self, state_changed_envelope: dict) -> None:
        assert state_changed_envelope["payload"]["raw"]["event_type"] == "state_changed"

    def test_payload_raw_domain(self, state_changed_envelope: dict) -> None:
        assert state_changed_envelope["payload"]["raw"]["domain"] == "sensor"

    def test_payload_raw_device_class(self, state_changed_envelope: dict) -> None:
        assert state_changed_envelope["payload"]["raw"]["device_class"] == "temperature"

    def test_payload_raw_friendly_name(self, state_changed_envelope: dict) -> None:
        assert (
            state_changed_envelope["payload"]["raw"]["friendly_name"] == "Living Room Temperature"
        )

    def test_payload_raw_old_state(self, state_changed_envelope: dict) -> None:
        assert state_changed_envelope["payload"]["raw"]["old_state"] == {
            "state": "21.5",
            "attributes": {},
        }

    def test_payload_raw_new_state(self, state_changed_envelope: dict) -> None:
        assert state_changed_envelope["payload"]["raw"]["new_state"] == {
            "state": "22.0",
            "attributes": {"unit_of_measurement": "°C"},
        }

    def test_payload_raw_ha_event(self, state_changed_envelope: dict) -> None:
        assert state_changed_envelope["payload"]["raw"]["ha_event"] == _HA_STATE_CHANGED_EVENT

    def test_payload_raw_discretion_reason(self, state_changed_envelope: dict) -> None:
        assert (
            state_changed_envelope["payload"]["raw"]["discretion_reason"]
            == "Temperature crossed threshold."
        )

    def test_payload_normalized_text_contains_label(self, state_changed_envelope: dict) -> None:
        text = state_changed_envelope["payload"]["normalized_text"]
        assert "Living Room Temperature" in text

    def test_payload_normalized_text_contains_states(self, state_changed_envelope: dict) -> None:
        text = state_changed_envelope["payload"]["normalized_text"]
        assert "21.5" in text
        assert "22.0" in text

    def test_payload_normalized_text_contains_unit(self, state_changed_envelope: dict) -> None:
        text = state_changed_envelope["payload"]["normalized_text"]
        assert "°C" in text

    def test_control_idempotency_key_format(self, state_changed_envelope: dict) -> None:
        key = state_changed_envelope["control"]["idempotency_key"]
        expected = f"ha:{_ENDPOINT_IDENTITY}:{_ENTITY_ID_TEMP}:{_TIME_FIRED_MS}"
        assert key == expected

    def test_control_policy_tier(self, state_changed_envelope: dict) -> None:
        assert state_changed_envelope["control"]["policy_tier"] == "default"

    def test_control_ingestion_tier(self, state_changed_envelope: dict) -> None:
        assert state_changed_envelope["control"]["ingestion_tier"] == "full"

    def test_domain_derived_from_entity_id_when_not_provided(self) -> None:
        env = build_state_changed_envelope(
            endpoint_identity=_ENDPOINT_IDENTITY,
            entity_id=_ENTITY_ID_LIGHT,
            time_fired=_TIME_FIRED,
            ha_event={},
        )
        assert env["payload"]["raw"]["domain"] == "light"

    def test_no_discretion_reason_omits_key_from_raw(self) -> None:
        env = build_state_changed_envelope(
            endpoint_identity=_ENDPOINT_IDENTITY,
            entity_id=_ENTITY_ID_TEMP,
            time_fired=_TIME_FIRED,
            ha_event=_HA_STATE_CHANGED_EVENT,
            discretion_reason=None,
        )
        assert "discretion_reason" not in env["payload"]["raw"]

    def test_missing_old_state_omits_key_from_raw(self) -> None:
        env = build_state_changed_envelope(
            endpoint_identity=_ENDPOINT_IDENTITY,
            entity_id=_ENTITY_ID_TEMP,
            time_fired=_TIME_FIRED,
            ha_event={},
        )
        assert "old_state" not in env["payload"]["raw"]

    def test_missing_new_state_omits_key_from_raw(self) -> None:
        env = build_state_changed_envelope(
            endpoint_identity=_ENDPOINT_IDENTITY,
            entity_id=_ENTITY_ID_TEMP,
            time_fired=_TIME_FIRED,
            ha_event={},
        )
        assert "new_state" not in env["payload"]["raw"]

    def test_normalized_text_without_unit(self) -> None:
        env = build_state_changed_envelope(
            endpoint_identity=_ENDPOINT_IDENTITY,
            entity_id=_ENTITY_ID_LOCK,
            time_fired=_TIME_FIRED,
            ha_event={},
            friendly_name="Front Door",
            old_state={"state": "unlocked"},
            new_state={"state": "locked"},
        )
        text = env["payload"]["normalized_text"]
        assert "Front Door:" in text
        assert "unlocked" in text
        assert "locked" in text

    def test_normalized_text_falls_back_to_entity_id(self) -> None:
        env = build_state_changed_envelope(
            endpoint_identity=_ENDPOINT_IDENTITY,
            entity_id=_ENTITY_ID_TEMP,
            time_fired=_TIME_FIRED,
            ha_event={},
            friendly_name=None,
            old_state={"state": "20.0"},
            new_state={"state": "21.0"},
        )
        text = env["payload"]["normalized_text"]
        assert _ENTITY_ID_TEMP in text

    def test_unavailable_transition_captured(self) -> None:
        env = build_state_changed_envelope(
            endpoint_identity=_ENDPOINT_IDENTITY,
            entity_id=_ENTITY_ID_TEMP,
            time_fired=_TIME_FIRED,
            ha_event={},
            friendly_name="Temp",
            old_state={"state": "21.5"},
            new_state={"state": "unavailable"},
        )
        text = env["payload"]["normalized_text"]
        assert "unavailable" in text

    def test_observed_at_matches_time_fired(self) -> None:
        env = build_state_changed_envelope(
            endpoint_identity=_ENDPOINT_IDENTITY,
            entity_id=_ENTITY_ID_TEMP,
            time_fired="2026-03-26T12:00:00+00:00",
            ha_event={},
        )
        parsed = datetime.fromisoformat(env["event"]["observed_at"])
        assert parsed.year == 2026
        assert parsed.month == 3
        assert parsed.day == 26
        assert parsed.hour == 12


# ---------------------------------------------------------------------------
# build_automation_triggered_envelope (task 6.2) — field mapping
# ---------------------------------------------------------------------------


@pytest.fixture
def automation_envelope() -> dict[str, Any]:
    """A fully-populated automation_triggered envelope."""
    return build_automation_triggered_envelope(
        endpoint_identity=_ENDPOINT_IDENTITY,
        entity_id=_ENTITY_ID_AUTOMATION,
        time_fired=_TIME_FIRED,
        ha_event=_HA_AUTOMATION_EVENT,
        friendly_name="Morning Lights",
        automation_id="aaabbbccc111",
        discretion_reason="Automation fires every morning — routine acknowledgment.",
    )


class TestBuildAutomationTriggeredEnvelope:
    """automation_triggered envelope: full ingest.v1 field mapping (task 6.2)."""

    def test_schema_version(self, automation_envelope: dict) -> None:
        assert automation_envelope["schema_version"] == "ingest.v1"

    def test_source_channel(self, automation_envelope: dict) -> None:
        assert automation_envelope["source"]["channel"] == "home_assistant"

    def test_source_provider(self, automation_envelope: dict) -> None:
        assert automation_envelope["source"]["provider"] == "home_assistant"

    def test_source_endpoint_identity(self, automation_envelope: dict) -> None:
        assert automation_envelope["source"]["endpoint_identity"] == _ENDPOINT_IDENTITY

    def test_event_external_event_id_format(self, automation_envelope: dict) -> None:
        ext_id = automation_envelope["event"]["external_event_id"]
        assert ext_id == f"ha:automation:{_ENTITY_ID_AUTOMATION}:{_TIME_FIRED_MS}"

    def test_event_external_thread_id(self, automation_envelope: dict) -> None:
        thread_id = automation_envelope["event"]["external_thread_id"]
        assert thread_id == f"ha:automation:{_ENTITY_ID_AUTOMATION}"

    def test_event_observed_at_is_utc_aware(self, automation_envelope: dict) -> None:
        obs = automation_envelope["event"]["observed_at"]
        parsed = datetime.fromisoformat(obs)
        assert parsed.tzinfo is not None
        assert parsed.utcoffset().total_seconds() == 0  # type: ignore[union-attr]

    def test_sender_identity_is_entity_id(self, automation_envelope: dict) -> None:
        assert automation_envelope["sender"]["identity"] == _ENTITY_ID_AUTOMATION

    def test_payload_raw_entity_id(self, automation_envelope: dict) -> None:
        assert automation_envelope["payload"]["raw"]["entity_id"] == _ENTITY_ID_AUTOMATION

    def test_payload_raw_event_type(self, automation_envelope: dict) -> None:
        assert automation_envelope["payload"]["raw"]["event_type"] == "automation_triggered"

    def test_payload_raw_domain(self, automation_envelope: dict) -> None:
        assert automation_envelope["payload"]["raw"]["domain"] == "automation"

    def test_payload_raw_friendly_name(self, automation_envelope: dict) -> None:
        assert automation_envelope["payload"]["raw"]["friendly_name"] == "Morning Lights"

    def test_payload_raw_automation_id(self, automation_envelope: dict) -> None:
        assert automation_envelope["payload"]["raw"]["automation_id"] == "aaabbbccc111"

    def test_payload_raw_ha_event(self, automation_envelope: dict) -> None:
        assert automation_envelope["payload"]["raw"]["ha_event"] == _HA_AUTOMATION_EVENT

    def test_payload_raw_discretion_reason(self, automation_envelope: dict) -> None:
        assert "discretion_reason" in automation_envelope["payload"]["raw"]

    def test_payload_normalized_text_prefix(self, automation_envelope: dict) -> None:
        text = automation_envelope["payload"]["normalized_text"]
        assert text.startswith("Automation triggered:")

    def test_payload_normalized_text_uses_friendly_name(self, automation_envelope: dict) -> None:
        text = automation_envelope["payload"]["normalized_text"]
        assert "Morning Lights" in text

    def test_control_idempotency_key_format(self, automation_envelope: dict) -> None:
        key = automation_envelope["control"]["idempotency_key"]
        expected = f"ha:{_ENDPOINT_IDENTITY}:{_ENTITY_ID_AUTOMATION}:{_TIME_FIRED_MS}"
        assert key == expected

    def test_control_policy_tier(self, automation_envelope: dict) -> None:
        assert automation_envelope["control"]["policy_tier"] == "default"

    def test_control_ingestion_tier(self, automation_envelope: dict) -> None:
        assert automation_envelope["control"]["ingestion_tier"] == "full"

    def test_domain_derived_from_entity_id(self) -> None:
        env = build_automation_triggered_envelope(
            endpoint_identity=_ENDPOINT_IDENTITY,
            entity_id="automation.evening_routine",
            time_fired=_TIME_FIRED,
            ha_event={},
        )
        assert env["payload"]["raw"]["domain"] == "automation"

    def test_no_automation_id_omits_key_from_raw(self) -> None:
        env = build_automation_triggered_envelope(
            endpoint_identity=_ENDPOINT_IDENTITY,
            entity_id=_ENTITY_ID_AUTOMATION,
            time_fired=_TIME_FIRED,
            ha_event={},
            automation_id=None,
        )
        assert "automation_id" not in env["payload"]["raw"]

    def test_no_discretion_reason_omits_key_from_raw(self) -> None:
        env = build_automation_triggered_envelope(
            endpoint_identity=_ENDPOINT_IDENTITY,
            entity_id=_ENTITY_ID_AUTOMATION,
            time_fired=_TIME_FIRED,
            ha_event={},
            discretion_reason=None,
        )
        assert "discretion_reason" not in env["payload"]["raw"]

    def test_normalized_text_falls_back_to_entity_id(self) -> None:
        env = build_automation_triggered_envelope(
            endpoint_identity=_ENDPOINT_IDENTITY,
            entity_id=_ENTITY_ID_AUTOMATION,
            time_fired=_TIME_FIRED,
            ha_event={},
            friendly_name=None,
        )
        text = env["payload"]["normalized_text"]
        assert _ENTITY_ID_AUTOMATION in text

    def test_domain_defaults_to_automation_for_entity_without_dot(self) -> None:
        env = build_automation_triggered_envelope(
            endpoint_identity=_ENDPOINT_IDENTITY,
            entity_id="nodot",
            time_fired=_TIME_FIRED,
            ha_event={},
            domain=None,
        )
        assert env["payload"]["raw"]["domain"] == "automation"


# ---------------------------------------------------------------------------
# Cross-envelope consistency checks (task 6.5)
# ---------------------------------------------------------------------------


class TestEnvelopeCrossConsistency:
    """Validate consistency properties across both envelope types."""

    def test_state_changed_idempotency_key_matches_event_id_timestamp(self) -> None:
        """The idempotency key timestamp component must match external_event_id."""
        env = build_state_changed_envelope(
            endpoint_identity=_ENDPOINT_IDENTITY,
            entity_id=_ENTITY_ID_TEMP,
            time_fired=_TIME_FIRED,
            ha_event={},
        )
        idem_key = env["control"]["idempotency_key"]
        ext_id = env["event"]["external_event_id"]
        # Both must contain the same ms timestamp
        assert str(_TIME_FIRED_MS) in idem_key
        assert str(_TIME_FIRED_MS) in ext_id

    def test_automation_idempotency_key_matches_event_id_timestamp(self) -> None:
        env = build_automation_triggered_envelope(
            endpoint_identity=_ENDPOINT_IDENTITY,
            entity_id=_ENTITY_ID_AUTOMATION,
            time_fired=_TIME_FIRED,
            ha_event={},
        )
        idem_key = env["control"]["idempotency_key"]
        ext_id = env["event"]["external_event_id"]
        assert str(_TIME_FIRED_MS) in idem_key
        assert str(_TIME_FIRED_MS) in ext_id

    def test_distinct_entities_produce_distinct_keys(self) -> None:
        env1 = build_state_changed_envelope(
            endpoint_identity=_ENDPOINT_IDENTITY,
            entity_id="sensor.a",
            time_fired=_TIME_FIRED,
            ha_event={},
        )
        env2 = build_state_changed_envelope(
            endpoint_identity=_ENDPOINT_IDENTITY,
            entity_id="sensor.b",
            time_fired=_TIME_FIRED,
            ha_event={},
        )
        assert env1["control"]["idempotency_key"] != env2["control"]["idempotency_key"]

    def test_state_changed_and_automation_envelopes_share_common_source_fields(self) -> None:
        sc_env = build_state_changed_envelope(
            endpoint_identity=_ENDPOINT_IDENTITY,
            entity_id=_ENTITY_ID_TEMP,
            time_fired=_TIME_FIRED,
            ha_event={},
        )
        at_env = build_automation_triggered_envelope(
            endpoint_identity=_ENDPOINT_IDENTITY,
            entity_id=_ENTITY_ID_AUTOMATION,
            time_fired=_TIME_FIRED,
            ha_event={},
        )
        assert sc_env["source"]["channel"] == at_env["source"]["channel"]
        assert sc_env["source"]["provider"] == at_env["source"]["provider"]
        assert sc_env["source"]["endpoint_identity"] == at_env["source"]["endpoint_identity"]

    def test_non_utc_time_fired_normalised_to_utc(self) -> None:
        """HA events from non-UTC offsets must produce UTC observed_at."""
        env = build_state_changed_envelope(
            endpoint_identity=_ENDPOINT_IDENTITY,
            entity_id=_ENTITY_ID_TEMP,
            time_fired="2026-03-26T14:00:00+02:00",  # UTC+2 = 12:00 UTC
            ha_event={},
        )
        obs = env["event"]["observed_at"]
        parsed = datetime.fromisoformat(obs)
        assert parsed.utcoffset().total_seconds() == 0  # type: ignore[union-attr]
        assert parsed.hour == 12  # 14:00 +02:00 = 12:00 UTC

    def test_z_suffix_time_fired_parsed_correctly(self) -> None:
        env = build_state_changed_envelope(
            endpoint_identity=_ENDPOINT_IDENTITY,
            entity_id=_ENTITY_ID_TEMP,
            time_fired="2026-03-26T12:00:00Z",
            ha_event={},
        )
        time_ms = time_fired_unix_ms("2026-03-26T12:00:00Z")
        key = env["control"]["idempotency_key"]
        assert str(time_ms) in key
