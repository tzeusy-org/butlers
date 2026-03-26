"""Tests for HA-specific filtered event persistence and replay (tasks 9.1–9.3).

Covers:
- HA filter reason constructors (domain_excluded, insignificant_delta, discretion_ignore)
- ha_full_payload helper: shape and field values
- HAFilterPersistence.record_* methods: accumulation and filter_reason format
- HAFilterPersistence.flush: delegates to FilteredEventBuffer.flush
- HAFilterPersistence.drain_replay: delegates to drain_replay_pending
- No-op behaviour when db_pool is None
- Flush and drain are no-ops when db_pool=None
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.connectors.home_assistant_filter import (
    CONNECTOR_CHANNEL,
    CONNECTOR_PROVIDER,
    CONNECTOR_TYPE,
    HAFilterPersistence,
    ha_full_payload,
    reason_discretion_ignore,
    reason_domain_excluded,
    reason_insignificant_delta,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Test data / constants
# ---------------------------------------------------------------------------

_ENDPOINT_IDENTITY = "home_assistant:homeassistant.local:8123"
_TIME_FIRED = "2026-03-26T12:00:00+00:00"
_ENTITY_ID_TEMP = "sensor.living_room_temperature"
_ENTITY_ID_MEDIA = "media_player.living_room"
_ENTITY_ID_LIGHT = "light.bedroom"
_HA_EVENT: dict[str, Any] = {
    "event_type": "state_changed",
    "data": {
        "entity_id": _ENTITY_ID_TEMP,
        "new_state": {"state": "22.0", "attributes": {}},
        "old_state": {"state": "21.9", "attributes": {}},
    },
    "time_fired": _TIME_FIRED,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_pool() -> MagicMock:
    """Mock asyncpg pool with acquire, execute, and fetch as AsyncMock."""
    mock_conn = AsyncMock()
    mock_conn.executemany = AsyncMock(return_value=None)
    mock_conn.execute = AsyncMock(return_value=None)
    mock_conn.fetch = AsyncMock(return_value=[])

    mock_txn_ctx = AsyncMock()
    mock_txn_ctx.__aenter__ = AsyncMock(return_value=None)
    mock_txn_ctx.__aexit__ = AsyncMock(return_value=None)
    mock_conn.transaction = MagicMock(return_value=mock_txn_ctx)

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)

    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=mock_ctx)
    mock_pool.execute = AsyncMock(return_value=None)
    return mock_pool


def _make_persistence(
    db_pool: MagicMock | None = None,
    submit_fn: AsyncMock | None = None,
) -> HAFilterPersistence:
    if submit_fn is None:
        submit_fn = AsyncMock()
    return HAFilterPersistence(
        endpoint_identity=_ENDPOINT_IDENTITY,
        db_pool=db_pool,
        submit_fn=submit_fn,
    )


# ---------------------------------------------------------------------------
# Filter reason constructors
# ---------------------------------------------------------------------------


class TestFilterReasonConstructors:
    """Filter reason helpers produce the correct string formats."""

    def test_reason_domain_excluded_basic(self) -> None:
        assert reason_domain_excluded("media_player") == "domain_excluded:media_player"

    def test_reason_domain_excluded_various_domains(self) -> None:
        assert reason_domain_excluded("light") == "domain_excluded:light"
        assert reason_domain_excluded("sensor") == "domain_excluded:sensor"
        assert reason_domain_excluded("camera") == "domain_excluded:camera"

    def test_reason_insignificant_delta_temperature(self) -> None:
        result = reason_insignificant_delta("temperature", 0.1)
        assert result == "insignificant_delta:temperature:0.1"

    def test_reason_insignificant_delta_humidity(self) -> None:
        result = reason_insignificant_delta("humidity", 1.5)
        assert result == "insignificant_delta:humidity:1.5"

    def test_reason_insignificant_delta_strips_trailing_zeros(self) -> None:
        result = reason_insignificant_delta("energy", 0.10000)
        # Should strip trailing zeros
        assert "insignificant_delta:energy:0.1" == result

    def test_reason_insignificant_delta_zero(self) -> None:
        result = reason_insignificant_delta("temperature", 0.0)
        assert result == "insignificant_delta:temperature:0"

    def test_reason_insignificant_delta_large_value(self) -> None:
        result = reason_insignificant_delta("power", 9.5)
        assert result == "insignificant_delta:power:9.5"

    def test_reason_discretion_ignore(self) -> None:
        assert reason_discretion_ignore() == "discretion_ignore"


# ---------------------------------------------------------------------------
# ha_full_payload helper
# ---------------------------------------------------------------------------


class TestHAFullPayload:
    """ha_full_payload produces correctly structured ingest.v1 envelope bodies."""

    def test_returns_five_top_level_keys(self) -> None:
        payload = ha_full_payload(
            endpoint_identity=_ENDPOINT_IDENTITY,
            entity_id=_ENTITY_ID_TEMP,
            time_fired=_TIME_FIRED,
            ha_event=_HA_EVENT,
        )
        assert set(payload.keys()) == {"source", "event", "sender", "payload", "control"}

    def test_no_schema_version(self) -> None:
        payload = ha_full_payload(
            endpoint_identity=_ENDPOINT_IDENTITY,
            entity_id=_ENTITY_ID_TEMP,
            time_fired=_TIME_FIRED,
            ha_event=_HA_EVENT,
        )
        assert "schema_version" not in payload

    def test_source_channel_and_provider(self) -> None:
        payload = ha_full_payload(
            endpoint_identity=_ENDPOINT_IDENTITY,
            entity_id=_ENTITY_ID_TEMP,
            time_fired=_TIME_FIRED,
            ha_event=_HA_EVENT,
        )
        assert payload["source"]["channel"] == CONNECTOR_CHANNEL
        assert payload["source"]["provider"] == CONNECTOR_PROVIDER
        assert payload["source"]["endpoint_identity"] == _ENDPOINT_IDENTITY

    def test_event_external_thread_id_groups_by_entity(self) -> None:
        payload = ha_full_payload(
            endpoint_identity=_ENDPOINT_IDENTITY,
            entity_id=_ENTITY_ID_TEMP,
            time_fired=_TIME_FIRED,
            ha_event=_HA_EVENT,
        )
        assert payload["event"]["external_thread_id"] == f"ha:entity:{_ENTITY_ID_TEMP}"

    def test_event_external_event_id_format(self) -> None:
        payload = ha_full_payload(
            endpoint_identity=_ENDPOINT_IDENTITY,
            entity_id=_ENTITY_ID_TEMP,
            time_fired=_TIME_FIRED,
            ha_event=_HA_EVENT,
        )
        ext_id = payload["event"]["external_event_id"]
        assert ext_id.startswith(f"ha:{_ENTITY_ID_TEMP}:")
        # The trailing part must be a numeric timestamp in milliseconds
        ts_part = ext_id.rsplit(":", 1)[-1]
        assert ts_part.isdigit()

    def test_event_observed_at_equals_time_fired(self) -> None:
        payload = ha_full_payload(
            endpoint_identity=_ENDPOINT_IDENTITY,
            entity_id=_ENTITY_ID_TEMP,
            time_fired=_TIME_FIRED,
            ha_event=_HA_EVENT,
        )
        assert payload["event"]["observed_at"] == _TIME_FIRED

    def test_sender_identity_is_entity_id(self) -> None:
        payload = ha_full_payload(
            endpoint_identity=_ENDPOINT_IDENTITY,
            entity_id=_ENTITY_ID_TEMP,
            time_fired=_TIME_FIRED,
            ha_event=_HA_EVENT,
        )
        assert payload["sender"]["identity"] == _ENTITY_ID_TEMP

    def test_raw_contains_entity_id_and_event_type(self) -> None:
        payload = ha_full_payload(
            endpoint_identity=_ENDPOINT_IDENTITY,
            entity_id=_ENTITY_ID_TEMP,
            time_fired=_TIME_FIRED,
            ha_event=_HA_EVENT,
        )
        raw = payload["payload"]["raw"]
        assert raw["entity_id"] == _ENTITY_ID_TEMP
        assert raw["event_type"] == "state_changed"

    def test_raw_includes_domain_derived_from_entity_id(self) -> None:
        payload = ha_full_payload(
            endpoint_identity=_ENDPOINT_IDENTITY,
            entity_id=_ENTITY_ID_TEMP,
            time_fired=_TIME_FIRED,
            ha_event=_HA_EVENT,
        )
        raw = payload["payload"]["raw"]
        assert raw["domain"] == "sensor"

    def test_raw_uses_explicit_domain_when_provided(self) -> None:
        payload = ha_full_payload(
            endpoint_identity=_ENDPOINT_IDENTITY,
            entity_id=_ENTITY_ID_TEMP,
            time_fired=_TIME_FIRED,
            ha_event=_HA_EVENT,
            domain="custom_domain",
        )
        assert payload["payload"]["raw"]["domain"] == "custom_domain"

    def test_raw_includes_old_and_new_state_when_provided(self) -> None:
        old_state = {"state": "21.9", "attributes": {}}
        new_state = {"state": "22.0", "attributes": {}}
        payload = ha_full_payload(
            endpoint_identity=_ENDPOINT_IDENTITY,
            entity_id=_ENTITY_ID_TEMP,
            time_fired=_TIME_FIRED,
            ha_event=_HA_EVENT,
            old_state=old_state,
            new_state=new_state,
        )
        raw = payload["payload"]["raw"]
        assert raw["old_state"] == old_state
        assert raw["new_state"] == new_state

    def test_raw_omits_states_when_not_provided(self) -> None:
        payload = ha_full_payload(
            endpoint_identity=_ENDPOINT_IDENTITY,
            entity_id=_ENTITY_ID_TEMP,
            time_fired=_TIME_FIRED,
            ha_event=_HA_EVENT,
        )
        raw = payload["payload"]["raw"]
        assert "old_state" not in raw
        assert "new_state" not in raw

    def test_raw_includes_device_class(self) -> None:
        payload = ha_full_payload(
            endpoint_identity=_ENDPOINT_IDENTITY,
            entity_id=_ENTITY_ID_TEMP,
            time_fired=_TIME_FIRED,
            ha_event=_HA_EVENT,
            device_class="temperature",
        )
        assert payload["payload"]["raw"]["device_class"] == "temperature"

    def test_raw_includes_friendly_name(self) -> None:
        payload = ha_full_payload(
            endpoint_identity=_ENDPOINT_IDENTITY,
            entity_id=_ENTITY_ID_TEMP,
            time_fired=_TIME_FIRED,
            ha_event=_HA_EVENT,
            friendly_name="Living Room Temperature",
        )
        assert payload["payload"]["raw"]["friendly_name"] == "Living Room Temperature"

    def test_payload_is_json_serializable(self) -> None:
        payload = ha_full_payload(
            endpoint_identity=_ENDPOINT_IDENTITY,
            entity_id=_ENTITY_ID_TEMP,
            time_fired=_TIME_FIRED,
            ha_event=_HA_EVENT,
            friendly_name="Living Room Temp",
            old_state={"state": "21.9"},
            new_state={"state": "22.0"},
            device_class="temperature",
        )
        # Should not raise
        serialized = json.dumps(payload)
        assert isinstance(serialized, str)


# ---------------------------------------------------------------------------
# HAFilterPersistence — record helpers
# ---------------------------------------------------------------------------


class TestHAFilterPersistenceRecord:
    """HAFilterPersistence accumulates events via the correct record_* helpers."""

    def test_new_persistence_is_empty(self) -> None:
        fp = _make_persistence()
        assert len(fp) == 0

    def test_record_domain_excluded_increments_buffer(self) -> None:
        fp = _make_persistence()
        fp.record_domain_excluded(
            entity_id=_ENTITY_ID_MEDIA,
            domain="media_player",
            ha_event=_HA_EVENT,
            time_fired=_TIME_FIRED,
        )
        assert len(fp) == 1

    def test_record_domain_excluded_filter_reason(self) -> None:
        fp = _make_persistence()
        fp.record_domain_excluded(
            entity_id=_ENTITY_ID_MEDIA,
            domain="media_player",
            ha_event=_HA_EVENT,
            time_fired=_TIME_FIRED,
        )
        row = fp._buffer._rows[0]
        # filter_reason is at column index 7
        assert row[7] == "domain_excluded:media_player"

    def test_record_domain_excluded_source_channel(self) -> None:
        fp = _make_persistence()
        fp.record_domain_excluded(
            entity_id=_ENTITY_ID_MEDIA,
            domain="media_player",
            ha_event=_HA_EVENT,
            time_fired=_TIME_FIRED,
        )
        row = fp._buffer._rows[0]
        # source_channel is at column index 4
        assert row[4] == CONNECTOR_CHANNEL

    def test_record_insignificant_delta_increments_buffer(self) -> None:
        fp = _make_persistence()
        fp.record_insignificant_delta(
            entity_id=_ENTITY_ID_TEMP,
            device_class="temperature",
            delta=0.1,
            ha_event=_HA_EVENT,
            time_fired=_TIME_FIRED,
        )
        assert len(fp) == 1

    def test_record_insignificant_delta_filter_reason(self) -> None:
        fp = _make_persistence()
        fp.record_insignificant_delta(
            entity_id=_ENTITY_ID_TEMP,
            device_class="temperature",
            delta=0.1,
            ha_event=_HA_EVENT,
            time_fired=_TIME_FIRED,
        )
        row = fp._buffer._rows[0]
        assert row[7] == "insignificant_delta:temperature:0.1"

    def test_record_discretion_ignore_increments_buffer(self) -> None:
        fp = _make_persistence()
        fp.record_discretion_ignore(
            entity_id=_ENTITY_ID_LIGHT,
            ha_event=_HA_EVENT,
            time_fired=_TIME_FIRED,
        )
        assert len(fp) == 1

    def test_record_discretion_ignore_filter_reason(self) -> None:
        fp = _make_persistence()
        fp.record_discretion_ignore(
            entity_id=_ENTITY_ID_LIGHT,
            ha_event=_HA_EVENT,
            time_fired=_TIME_FIRED,
        )
        row = fp._buffer._rows[0]
        assert row[7] == "discretion_ignore"

    def test_record_multiple_layers_accumulates(self) -> None:
        fp = _make_persistence()
        fp.record_domain_excluded(
            entity_id=_ENTITY_ID_MEDIA,
            domain="media_player",
            ha_event=_HA_EVENT,
            time_fired=_TIME_FIRED,
        )
        fp.record_insignificant_delta(
            entity_id=_ENTITY_ID_TEMP,
            device_class="temperature",
            delta=0.1,
            ha_event=_HA_EVENT,
            time_fired=_TIME_FIRED,
        )
        fp.record_discretion_ignore(
            entity_id=_ENTITY_ID_LIGHT,
            ha_event=_HA_EVENT,
            time_fired=_TIME_FIRED,
        )
        assert len(fp) == 3

    def test_record_domain_excluded_with_optional_fields(self) -> None:
        fp = _make_persistence()
        fp.record_domain_excluded(
            entity_id=_ENTITY_ID_MEDIA,
            domain="media_player",
            ha_event=_HA_EVENT,
            time_fired=_TIME_FIRED,
            friendly_name="Living Room TV",
            old_state={"state": "off"},
            new_state={"state": "playing"},
        )
        row = fp._buffer._rows[0]
        # full_payload is at column index 9 (JSON string)
        full_payload = json.loads(row[9])
        raw = full_payload["payload"]["raw"]
        assert raw["friendly_name"] == "Living Room TV"
        assert raw["old_state"] == {"state": "off"}
        assert raw["new_state"] == {"state": "playing"}

    def test_sender_identity_is_entity_id_in_record(self) -> None:
        fp = _make_persistence()
        fp.record_domain_excluded(
            entity_id=_ENTITY_ID_MEDIA,
            domain="media_player",
            ha_event=_HA_EVENT,
            time_fired=_TIME_FIRED,
        )
        row = fp._buffer._rows[0]
        # sender_identity is at column index 5
        assert row[5] == _ENTITY_ID_MEDIA

    def test_connector_type_in_record(self) -> None:
        fp = _make_persistence()
        fp.record_discretion_ignore(
            entity_id=_ENTITY_ID_LIGHT,
            ha_event=_HA_EVENT,
            time_fired=_TIME_FIRED,
        )
        row = fp._buffer._rows[0]
        # connector_type is at column index 1
        assert row[1] == CONNECTOR_TYPE


# ---------------------------------------------------------------------------
# HAFilterPersistence — flush
# ---------------------------------------------------------------------------


class TestHAFilterPersistenceFlush:
    """flush() delegates to FilteredEventBuffer.flush and is a no-op without pool."""

    async def test_flush_noop_when_no_db_pool(self) -> None:
        fp = _make_persistence(db_pool=None)
        fp.record_discretion_ignore(
            entity_id=_ENTITY_ID_LIGHT,
            ha_event=_HA_EVENT,
            time_fired=_TIME_FIRED,
        )
        # Must not raise
        await fp.flush()
        # Buffer is NOT cleared since we couldn't flush
        assert len(fp) == 1

    async def test_flush_calls_db_when_pool_provided(self) -> None:
        pool = _make_mock_pool()
        fp = _make_persistence(db_pool=pool)
        fp.record_domain_excluded(
            entity_id=_ENTITY_ID_MEDIA,
            domain="media_player",
            ha_event=_HA_EVENT,
            time_fired=_TIME_FIRED,
        )
        await fp.flush()
        # After successful flush the buffer is cleared
        assert len(fp) == 0

    async def test_flush_calls_ensure_partition(self) -> None:
        pool = _make_mock_pool()
        fp = _make_persistence(db_pool=pool)
        fp.record_discretion_ignore(
            entity_id=_ENTITY_ID_LIGHT,
            ha_event=_HA_EVENT,
            time_fired=_TIME_FIRED,
        )
        await fp.flush()
        pool.execute.assert_awaited_once()
        sql = pool.execute.call_args[0][0]
        assert "connectors_filtered_events_ensure_partition" in sql

    async def test_flush_does_not_raise_on_db_error(self) -> None:
        pool = _make_mock_pool()
        pool.execute.side_effect = RuntimeError("DB error")
        fp = _make_persistence(db_pool=pool)
        fp.record_domain_excluded(
            entity_id=_ENTITY_ID_MEDIA,
            domain="media_player",
            ha_event=_HA_EVENT,
            time_fired=_TIME_FIRED,
        )
        # Must not raise
        await fp.flush()

    async def test_flush_empty_buffer_is_noop(self) -> None:
        pool = _make_mock_pool()
        fp = _make_persistence(db_pool=pool)
        await fp.flush()
        pool.execute.assert_not_awaited()


# ---------------------------------------------------------------------------
# HAFilterPersistence — drain_replay
# ---------------------------------------------------------------------------


class TestHAFilterPersistenceDrainReplay:
    """drain_replay() delegates to drain_replay_pending and is a no-op without pool."""

    async def test_drain_noop_when_no_db_pool(self) -> None:
        fp = _make_persistence(db_pool=None)
        # Must not raise
        await fp.drain_replay()

    async def test_drain_calls_drain_replay_pending_with_correct_args(self) -> None:
        pool = _make_mock_pool()
        submit_fn = AsyncMock()
        fp = _make_persistence(db_pool=pool, submit_fn=submit_fn)

        await fp.drain_replay()

        # fetch was called with the correct connector_type and endpoint_identity
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.fetch.assert_awaited_once()
        fetch_args = conn.fetch.call_args[0]
        assert fetch_args[1] == CONNECTOR_TYPE
        assert fetch_args[2] == _ENDPOINT_IDENTITY

    async def test_drain_submits_replay_pending_rows(self) -> None:
        # Build a fake replay_pending row
        payload_dict = {
            "source": {
                "channel": "home_assistant",
                "provider": "home_assistant",
                "endpoint_identity": _ENDPOINT_IDENTITY,
            },
            "event": {
                "external_event_id": f"ha:{_ENTITY_ID_TEMP}:1711454400000",
                "external_thread_id": f"ha:entity:{_ENTITY_ID_TEMP}",
                "observed_at": _TIME_FIRED,
            },
            "sender": {"identity": _ENTITY_ID_TEMP},
            "payload": {"raw": {"entity_id": _ENTITY_ID_TEMP}, "normalized_text": "Temp changed"},
            "control": {"policy_tier": "default"},
        }

        fake_row = MagicMock()
        fake_row.__getitem__ = MagicMock(
            side_effect=lambda key: {
                "id": 1,
                "received_at": datetime(2026, 3, 26, 12, 0, 0, tzinfo=UTC),
                "external_message_id": f"ha:{_ENTITY_ID_TEMP}:{_TIME_FIRED}",
                "full_payload": json.dumps(payload_dict),
            }[key]
        )

        pool = _make_mock_pool()
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.fetch = AsyncMock(return_value=[fake_row])

        submit_fn = AsyncMock()
        fp = _make_persistence(db_pool=pool, submit_fn=submit_fn)

        await fp.drain_replay()

        # submit_fn called once with ingest.v1 envelope
        submit_fn.assert_awaited_once()
        envelope = submit_fn.call_args[0][0]
        assert envelope["schema_version"] == "ingest.v1"
        assert envelope["source"]["channel"] == "home_assistant"
        assert envelope["sender"]["identity"] == _ENTITY_ID_TEMP

    async def test_drain_does_not_raise_on_db_error(self) -> None:
        pool = _make_mock_pool()
        # Simulate acquire failure
        pool.acquire.return_value.__aenter__.side_effect = RuntimeError("DB unreachable")
        submit_fn = AsyncMock()
        fp = _make_persistence(db_pool=pool, submit_fn=submit_fn)
        # Must not raise
        await fp.drain_replay()

    async def test_drain_noop_when_no_replay_rows(self) -> None:
        pool = _make_mock_pool()  # conn.fetch returns [] by default
        submit_fn = AsyncMock()
        fp = _make_persistence(db_pool=pool, submit_fn=submit_fn)

        await fp.drain_replay()

        submit_fn.assert_not_awaited()


# ---------------------------------------------------------------------------
# All three filter reasons coexist in one buffer
# ---------------------------------------------------------------------------


class TestMixedFilterReasons:
    """Integration: all three HA filter layers can coexist in one flush batch."""

    async def test_flush_writes_all_three_filter_reasons(self) -> None:
        pool = _make_mock_pool()
        fp = _make_persistence(db_pool=pool)

        fp.record_domain_excluded(
            entity_id=_ENTITY_ID_MEDIA,
            domain="media_player",
            ha_event=_HA_EVENT,
            time_fired=_TIME_FIRED,
        )
        fp.record_insignificant_delta(
            entity_id=_ENTITY_ID_TEMP,
            device_class="humidity",
            delta=1.0,
            ha_event=_HA_EVENT,
            time_fired=_TIME_FIRED,
        )
        fp.record_discretion_ignore(
            entity_id=_ENTITY_ID_LIGHT,
            ha_event=_HA_EVENT,
            time_fired=_TIME_FIRED,
        )

        await fp.flush()

        conn = pool.acquire.return_value.__aenter__.return_value
        conn.executemany.assert_awaited_once()
        rows = conn.executemany.call_args[0][1]
        assert len(rows) == 3

        reasons = {row[7] for row in rows}
        assert "domain_excluded:media_player" in reasons
        assert "insignificant_delta:humidity:1" in reasons
        assert "discretion_ignore" in reasons

    async def test_flush_then_drain_full_cycle(self) -> None:
        """Verify that a flush followed by a drain does not raise."""
        pool = _make_mock_pool()
        submit_fn = AsyncMock()
        fp = _make_persistence(db_pool=pool, submit_fn=submit_fn)

        fp.record_discretion_ignore(
            entity_id=_ENTITY_ID_LIGHT,
            ha_event=_HA_EVENT,
            time_fired=_TIME_FIRED,
        )

        # flush clears buffer and writes to DB
        await fp.flush()
        assert len(fp) == 0

        # drain processes any replay_pending rows (none in this test)
        await fp.drain_replay()
        submit_fn.assert_not_awaited()
