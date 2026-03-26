"""Tests for Home Assistant connector checkpoint persistence (tasks 7.1–7.4).

Covers:
- HACheckpoint JSON serialization/deserialization (task 7.1)
- adjusted_resume_ts with safety margin subtraction (task 7.2)
- is_duplicate_event deduplication logic (task 7.3)
- load_ha_checkpoint and save_ha_checkpoint DB helpers (task 7.4)
- Fail-open behaviour: DB errors return empty checkpoint, not an exception
- Checkpoint keyed by (connector_type="home_assistant", endpoint_identity)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.connectors.home_assistant_checkpoint import (
    HACheckpoint,
    adjusted_resume_ts,
    is_duplicate_event,
    load_ha_checkpoint,
    save_ha_checkpoint,
)

pytestmark = pytest.mark.unit

_ENDPOINT = "home_assistant:ha.local:8123"
_CONNECTOR_TYPE = "home_assistant"

# Patch targets for cursor_store functions imported into checkpoint module
_PATCH_LOAD = "butlers.connectors.home_assistant_checkpoint.load_cursor"
_PATCH_SAVE = "butlers.connectors.home_assistant_checkpoint.save_cursor"

# Reusable fixture datetimes
_TS_BASE = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)
_TS_NEWER = datetime(2024, 6, 15, 12, 1, 0, tzinfo=UTC)
_TS_OLDER = datetime(2024, 6, 15, 11, 59, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# HACheckpoint serialisation (task 7.1)
# ---------------------------------------------------------------------------


class TestHACheckpointSerialisation:
    """Tests for HACheckpoint JSON round-trip."""

    def test_to_json_all_fields(self) -> None:
        """to_json produces a JSON string with all required keys."""
        ckpt = HACheckpoint(
            last_event_ts=_TS_BASE,
            last_entity_id="sensor.living_room_temp",
            transport="websocket",
        )
        data = json.loads(ckpt.to_json())
        assert data["last_event_ts"] == _TS_BASE.isoformat()
        assert data["last_entity_id"] == "sensor.living_room_temp"
        assert data["transport"] == "websocket"

    def test_to_json_null_fields(self) -> None:
        """to_json serialises None values as JSON null."""
        ckpt = HACheckpoint(last_event_ts=None, last_entity_id=None, transport=None)
        data = json.loads(ckpt.to_json())
        assert data["last_event_ts"] is None
        assert data["last_entity_id"] is None
        assert data["transport"] is None

    def test_to_json_rest_fallback_transport(self) -> None:
        """to_json correctly serialises rest_fallback transport mode."""
        ckpt = HACheckpoint(
            last_event_ts=_TS_BASE,
            last_entity_id="light.bedroom",
            transport="rest_fallback",
        )
        data = json.loads(ckpt.to_json())
        assert data["transport"] == "rest_fallback"

    def test_roundtrip_websocket(self) -> None:
        """from_json(to_json()) preserves all fields for websocket transport."""
        ckpt = HACheckpoint(
            last_event_ts=_TS_BASE,
            last_entity_id="lock.front_door",
            transport="websocket",
        )
        restored = HACheckpoint.from_json(ckpt.to_json())
        assert restored.last_event_ts == _TS_BASE
        assert restored.last_entity_id == "lock.front_door"
        assert restored.transport == "websocket"

    def test_roundtrip_rest_fallback(self) -> None:
        """from_json(to_json()) preserves all fields for rest_fallback transport."""
        ckpt = HACheckpoint(
            last_event_ts=_TS_BASE,
            last_entity_id="binary_sensor.motion",
            transport="rest_fallback",
        )
        restored = HACheckpoint.from_json(ckpt.to_json())
        assert restored.last_event_ts == _TS_BASE
        assert restored.last_entity_id == "binary_sensor.motion"
        assert restored.transport == "rest_fallback"

    def test_roundtrip_none_fields(self) -> None:
        """from_json(to_json()) preserves None values."""
        ckpt = HACheckpoint.empty()
        restored = HACheckpoint.from_json(ckpt.to_json())
        assert restored.last_event_ts is None
        assert restored.last_entity_id is None
        assert restored.transport is None

    def test_from_json_naive_datetime_becomes_utc(self) -> None:
        """from_json assumes UTC for naive datetimes in stored JSON."""
        naive_ts = "2024-06-15T12:00:00"
        raw = json.dumps({"last_event_ts": naive_ts, "last_entity_id": None, "transport": None})
        ckpt = HACheckpoint.from_json(raw)
        assert ckpt.last_event_ts is not None
        assert ckpt.last_event_ts.tzinfo is not None

    def test_from_json_missing_optional_keys(self) -> None:
        """from_json tolerates a JSON object with only last_event_ts (missing other keys)."""
        raw = json.dumps({"last_event_ts": _TS_BASE.isoformat()})
        ckpt = HACheckpoint.from_json(raw)
        assert ckpt.last_event_ts == _TS_BASE
        assert ckpt.last_entity_id is None
        assert ckpt.transport is None

    def test_from_json_invalid_json_raises(self) -> None:
        """from_json raises ValueError on malformed JSON input."""
        with pytest.raises(ValueError, match="not valid JSON"):
            HACheckpoint.from_json("not-json")

    def test_from_json_non_dict_raises(self) -> None:
        """from_json raises ValueError when JSON root is not an object."""
        with pytest.raises(ValueError, match="must be an object"):
            HACheckpoint.from_json("[1, 2, 3]")

    def test_from_json_invalid_ts_type_raises(self) -> None:
        """from_json raises ValueError when last_event_ts is not a string."""
        raw = json.dumps({"last_event_ts": 1234567890, "last_entity_id": None, "transport": None})
        with pytest.raises(ValueError, match="must be a string or null"):
            HACheckpoint.from_json(raw)

    def test_from_json_invalid_ts_format_raises(self) -> None:
        """from_json raises ValueError when last_event_ts is not ISO 8601."""
        raw = json.dumps({"last_event_ts": "not-a-date", "last_entity_id": None, "transport": None})
        with pytest.raises(ValueError, match="not a valid ISO 8601"):
            HACheckpoint.from_json(raw)

    def test_from_json_invalid_transport_raises(self) -> None:
        """from_json raises ValueError for unrecognised transport values."""
        raw = json.dumps(
            {
                "last_event_ts": None,
                "last_entity_id": None,
                "transport": "mqtt",
            }
        )
        with pytest.raises(ValueError, match="'transport' must be"):
            HACheckpoint.from_json(raw)

    def test_empty_returns_all_none(self) -> None:
        """HACheckpoint.empty() has all fields set to None."""
        ckpt = HACheckpoint.empty()
        assert ckpt.last_event_ts is None
        assert ckpt.last_entity_id is None
        assert ckpt.transport is None


# ---------------------------------------------------------------------------
# HACheckpoint.advance (task 7.1)
# ---------------------------------------------------------------------------


class TestHACheckpointAdvance:
    """Tests for HACheckpoint.advance()."""

    def test_advance_from_none(self) -> None:
        """advance() sets fields when starting from an empty checkpoint."""
        ckpt = HACheckpoint.empty()
        ckpt.advance(_TS_BASE, "sensor.temp", "websocket")
        assert ckpt.last_event_ts == _TS_BASE
        assert ckpt.last_entity_id == "sensor.temp"
        assert ckpt.transport == "websocket"

    def test_advance_with_newer_ts(self) -> None:
        """advance() updates the checkpoint when the new ts is strictly newer."""
        ckpt = HACheckpoint(
            last_event_ts=_TS_BASE, last_entity_id="sensor.old", transport="websocket"
        )
        ckpt.advance(_TS_NEWER, "sensor.new", "rest_fallback")
        assert ckpt.last_event_ts == _TS_NEWER
        assert ckpt.last_entity_id == "sensor.new"
        assert ckpt.transport == "rest_fallback"

    def test_advance_with_older_ts_no_change(self) -> None:
        """advance() does NOT update the checkpoint for an older timestamp."""
        ckpt = HACheckpoint(
            last_event_ts=_TS_BASE, last_entity_id="sensor.current", transport="websocket"
        )
        ckpt.advance(_TS_OLDER, "sensor.old", "rest_fallback")
        assert ckpt.last_event_ts == _TS_BASE
        assert ckpt.last_entity_id == "sensor.current"
        assert ckpt.transport == "websocket"

    def test_advance_with_equal_ts_no_change(self) -> None:
        """advance() does NOT update when timestamps are exactly equal."""
        ckpt = HACheckpoint(
            last_event_ts=_TS_BASE, last_entity_id="sensor.current", transport="websocket"
        )
        ckpt.advance(_TS_BASE, "sensor.other", "rest_fallback")
        assert ckpt.last_event_ts == _TS_BASE
        assert ckpt.last_entity_id == "sensor.current"
        assert ckpt.transport == "websocket"


# ---------------------------------------------------------------------------
# adjusted_resume_ts (task 7.2)
# ---------------------------------------------------------------------------


class TestAdjustedResumeTs:
    """Tests for checkpoint loading with safety margin."""

    def test_returns_none_when_no_checkpoint(self) -> None:
        """adjusted_resume_ts returns None for empty checkpoint."""
        ckpt = HACheckpoint.empty()
        assert adjusted_resume_ts(ckpt, overlap_seconds=30) is None

    def test_subtracts_overlap_seconds(self) -> None:
        """adjusted_resume_ts subtracts the safety margin from last_event_ts."""
        ckpt = HACheckpoint(
            last_event_ts=_TS_BASE,
            last_entity_id="sensor.temp",
            transport="websocket",
        )
        result = adjusted_resume_ts(ckpt, overlap_seconds=30)
        expected = _TS_BASE - timedelta(seconds=30)
        assert result == expected

    def test_zero_overlap(self) -> None:
        """adjusted_resume_ts with overlap_seconds=0 returns last_event_ts unchanged."""
        ckpt = HACheckpoint(
            last_event_ts=_TS_BASE,
            last_entity_id="sensor.temp",
            transport="websocket",
        )
        result = adjusted_resume_ts(ckpt, overlap_seconds=0)
        assert result == _TS_BASE

    def test_large_overlap(self) -> None:
        """adjusted_resume_ts correctly handles a large safety margin."""
        ckpt = HACheckpoint(
            last_event_ts=_TS_BASE,
            last_entity_id="sensor.temp",
            transport="websocket",
        )
        result = adjusted_resume_ts(ckpt, overlap_seconds=300)
        expected = _TS_BASE - timedelta(seconds=300)
        assert result == expected

    def test_result_is_timezone_aware(self) -> None:
        """adjusted_resume_ts result preserves timezone-awareness."""
        ckpt = HACheckpoint(
            last_event_ts=_TS_BASE,
            last_entity_id="sensor.temp",
            transport="websocket",
        )
        result = adjusted_resume_ts(ckpt, overlap_seconds=30)
        assert result is not None
        assert result.tzinfo is not None


# ---------------------------------------------------------------------------
# is_duplicate_event (task 7.3)
# ---------------------------------------------------------------------------


class TestIsDuplicateEvent:
    """Tests for event deduplication based on checkpoint timestamp."""

    def test_no_checkpoint_is_not_duplicate(self) -> None:
        """When resume_ts is None (no prior checkpoint), all events are new."""
        assert is_duplicate_event(resume_ts=None, event_ts=_TS_BASE) is False

    def test_event_before_resume_ts_is_duplicate(self) -> None:
        """Events with time_fired strictly before resume_ts are duplicates."""
        resume_ts = _TS_BASE
        event_ts = _TS_BASE - timedelta(seconds=1)
        assert is_duplicate_event(resume_ts=resume_ts, event_ts=event_ts) is True

    def test_event_exactly_at_resume_ts_is_duplicate(self) -> None:
        """Events with time_fired equal to resume_ts are treated as duplicates."""
        assert is_duplicate_event(resume_ts=_TS_BASE, event_ts=_TS_BASE) is True

    def test_event_after_resume_ts_is_new(self) -> None:
        """Events with time_fired strictly after resume_ts are new."""
        resume_ts = _TS_BASE
        event_ts = _TS_BASE + timedelta(microseconds=1)
        assert is_duplicate_event(resume_ts=resume_ts, event_ts=event_ts) is False

    def test_event_far_before_resume_ts_is_duplicate(self) -> None:
        """Events well before the resume timestamp are duplicates."""
        resume_ts = _TS_BASE
        event_ts = _TS_BASE - timedelta(hours=1)
        assert is_duplicate_event(resume_ts=resume_ts, event_ts=event_ts) is True

    def test_event_far_after_resume_ts_is_new(self) -> None:
        """Events well after the resume timestamp are new."""
        resume_ts = _TS_BASE
        event_ts = _TS_BASE + timedelta(hours=1)
        assert is_duplicate_event(resume_ts=resume_ts, event_ts=event_ts) is False

    def test_dedup_with_safety_margin_applied(self) -> None:
        """Combining adjusted_resume_ts + is_duplicate_event covers the overlap window.

        With stored_ts = 12:00:00 and overlap = 30s, resume_ts = 11:59:30.
        - Events at or before 11:59:30 → duplicate
        - Events after 11:59:30 → new (processed again within the overlap window)
        """
        stored_ts = _TS_BASE  # 12:00:00
        overlap = 30
        ckpt = HACheckpoint(
            last_event_ts=stored_ts, last_entity_id="sensor.x", transport="websocket"
        )
        resume = adjusted_resume_ts(ckpt, overlap_seconds=overlap)
        # resume = 11:59:30

        # Event fired exactly at resume_ts boundary (11:59:30) → duplicate
        assert is_duplicate_event(resume_ts=resume, event_ts=resume) is True  # type: ignore[arg-type]

        # Event fired 1s before resume_ts (11:59:29) → duplicate
        event_before_resume = stored_ts - timedelta(seconds=31)
        assert is_duplicate_event(resume_ts=resume, event_ts=event_before_resume) is True

        # Event fired 1s after resume_ts (11:59:31) — within overlap window but new
        event_in_overlap = stored_ts - timedelta(seconds=29)
        assert is_duplicate_event(resume_ts=resume, event_ts=event_in_overlap) is False

        # Event fired exactly at stored_ts (12:00:00) → new
        assert is_duplicate_event(resume_ts=resume, event_ts=stored_ts) is False

        # Event fired 1s after stored_ts → new
        event_after = stored_ts + timedelta(seconds=1)
        assert is_duplicate_event(resume_ts=resume, event_ts=event_after) is False


# ---------------------------------------------------------------------------
# load_ha_checkpoint — success path (task 7.4)
# ---------------------------------------------------------------------------


class TestLoadHACheckpoint:
    """Tests for load_ha_checkpoint DB helper."""

    async def test_load_returns_saved_state(self) -> None:
        """load_ha_checkpoint parses the cursor value returned by load_cursor."""
        stored = HACheckpoint(
            last_event_ts=_TS_BASE,
            last_entity_id="sensor.living_room_temp",
            transport="websocket",
        )
        mock_pool = MagicMock()

        with patch(_PATCH_LOAD, new=AsyncMock(return_value=stored.to_json())) as mock_load:
            checkpoint, resume_ts = await load_ha_checkpoint(
                mock_pool, _ENDPOINT, overlap_seconds=30
            )

        mock_load.assert_awaited_once_with(mock_pool, _CONNECTOR_TYPE, _ENDPOINT)
        assert checkpoint.last_event_ts == _TS_BASE
        assert checkpoint.last_entity_id == "sensor.living_room_temp"
        assert checkpoint.transport == "websocket"
        # Resume ts should be 30s before stored ts
        assert resume_ts == _TS_BASE - timedelta(seconds=30)

    async def test_load_returns_empty_when_no_row(self) -> None:
        """When no checkpoint row exists, load_ha_checkpoint returns empty state."""
        mock_pool = MagicMock()

        with patch(_PATCH_LOAD, new=AsyncMock(return_value=None)):
            checkpoint, resume_ts = await load_ha_checkpoint(mock_pool, _ENDPOINT)

        assert checkpoint.last_event_ts is None
        assert checkpoint.last_entity_id is None
        assert checkpoint.transport is None
        assert resume_ts is None

    async def test_load_fail_open_on_db_error(self) -> None:
        """DB errors during load return empty checkpoint (fail-open), no exception."""
        mock_pool = MagicMock()

        with patch(_PATCH_LOAD, side_effect=Exception("connection refused")):
            checkpoint, resume_ts = await load_ha_checkpoint(mock_pool, _ENDPOINT)

        assert checkpoint.last_event_ts is None
        assert resume_ts is None

    async def test_load_fail_open_on_malformed_json(self) -> None:
        """Malformed checkpoint JSON returns empty checkpoint (fail-open), no exception."""
        mock_pool = MagicMock()

        with patch(_PATCH_LOAD, new=AsyncMock(return_value="!!invalid-json!!")):
            checkpoint, resume_ts = await load_ha_checkpoint(mock_pool, _ENDPOINT)

        assert checkpoint.last_event_ts is None
        assert resume_ts is None

    async def test_load_uses_correct_connector_type(self) -> None:
        """load_ha_checkpoint passes 'home_assistant' as connector_type to load_cursor."""
        mock_pool = MagicMock()

        with patch(_PATCH_LOAD, new=AsyncMock(return_value=None)) as mock_load:
            await load_ha_checkpoint(mock_pool, _ENDPOINT)

        assert mock_load.call_args.args[1] == "home_assistant"

    async def test_load_uses_endpoint_as_key(self) -> None:
        """load_ha_checkpoint passes the endpoint_identity as the cursor key."""
        mock_pool = MagicMock()
        custom_ep = "home_assistant:myhome.example.com:443"

        with patch(_PATCH_LOAD, new=AsyncMock(return_value=None)) as mock_load:
            await load_ha_checkpoint(mock_pool, custom_ep)

        assert mock_load.call_args.args[2] == custom_ep

    async def test_load_applies_custom_overlap(self) -> None:
        """load_ha_checkpoint applies the caller-specified overlap_seconds."""
        stored = HACheckpoint(
            last_event_ts=_TS_BASE, last_entity_id="sensor.x", transport="websocket"
        )
        mock_pool = MagicMock()

        with patch(_PATCH_LOAD, new=AsyncMock(return_value=stored.to_json())):
            _ckpt, resume_ts = await load_ha_checkpoint(mock_pool, _ENDPOINT, overlap_seconds=60)

        assert resume_ts == _TS_BASE - timedelta(seconds=60)

    async def test_load_rest_fallback_transport(self) -> None:
        """load_ha_checkpoint correctly restores rest_fallback transport mode."""
        stored = HACheckpoint(
            last_event_ts=_TS_BASE,
            last_entity_id="sensor.x",
            transport="rest_fallback",
        )
        mock_pool = MagicMock()

        with patch(_PATCH_LOAD, new=AsyncMock(return_value=stored.to_json())):
            checkpoint, _resume_ts = await load_ha_checkpoint(mock_pool, _ENDPOINT)

        assert checkpoint.transport == "rest_fallback"


# ---------------------------------------------------------------------------
# save_ha_checkpoint — success path (task 7.4)
# ---------------------------------------------------------------------------


class TestSaveHACheckpoint:
    """Tests for save_ha_checkpoint DB helper."""

    async def test_save_calls_save_cursor_with_correct_args(self) -> None:
        """save_ha_checkpoint calls save_cursor with correct connector_type and endpoint."""
        mock_pool = MagicMock()

        with patch(_PATCH_SAVE, new=AsyncMock()) as mock_save:
            await save_ha_checkpoint(
                pool=mock_pool,
                endpoint_identity=_ENDPOINT,
                event_ts=_TS_BASE,
                entity_id="sensor.living_room_temp",
                transport="websocket",
            )

        mock_save.assert_awaited_once()
        call_args = mock_save.call_args.args
        assert call_args[0] is mock_pool
        assert call_args[1] == "home_assistant"
        assert call_args[2] == _ENDPOINT

        # Verify the JSON payload
        saved_data = json.loads(call_args[3])
        assert saved_data["last_event_ts"] == _TS_BASE.isoformat()
        assert saved_data["last_entity_id"] == "sensor.living_room_temp"
        assert saved_data["transport"] == "websocket"

    async def test_save_rest_fallback_transport(self) -> None:
        """save_ha_checkpoint persists rest_fallback transport correctly."""
        mock_pool = MagicMock()

        with patch(_PATCH_SAVE, new=AsyncMock()) as mock_save:
            await save_ha_checkpoint(
                pool=mock_pool,
                endpoint_identity=_ENDPOINT,
                event_ts=_TS_BASE,
                entity_id="light.bedroom",
                transport="rest_fallback",
            )

        saved_data = json.loads(mock_save.call_args.args[3])
        assert saved_data["transport"] == "rest_fallback"

    async def test_save_swallows_db_errors(self) -> None:
        """DB errors during checkpoint save are swallowed (no exception raised)."""
        mock_pool = MagicMock()

        with patch(_PATCH_SAVE, side_effect=Exception("db write error")):
            # Should not raise
            await save_ha_checkpoint(
                pool=mock_pool,
                endpoint_identity=_ENDPOINT,
                event_ts=_TS_BASE,
                entity_id="sensor.x",
                transport="websocket",
            )

    async def test_save_uses_correct_endpoint(self) -> None:
        """save_ha_checkpoint uses the endpoint_identity as the cursor key."""
        mock_pool = MagicMock()
        custom_ep = "home_assistant:192.168.1.100:8123"

        with patch(_PATCH_SAVE, new=AsyncMock()) as mock_save:
            await save_ha_checkpoint(
                pool=mock_pool,
                endpoint_identity=custom_ep,
                event_ts=_TS_BASE,
                entity_id="switch.garden_lights",
                transport="websocket",
            )

        assert mock_save.call_args.args[2] == custom_ep


# ---------------------------------------------------------------------------
# Full checkpoint save/load/resume cycle (task 7.4)
# ---------------------------------------------------------------------------


class TestCheckpointSaveLoadResumeCycle:
    """End-to-end tests for the save → load → dedup cycle."""

    async def test_full_cycle_websocket(self) -> None:
        """Saved checkpoint is correctly loaded and resume_ts computed for websocket."""
        mock_pool = MagicMock()
        entity = "sensor.kitchen_temp"
        saved_json_holder: list[str] = []

        async def capture_save(pool, connector_type, endpoint, value):  # noqa: ANN001
            saved_json_holder.append(value)

        async def return_saved(pool, connector_type, endpoint):  # noqa: ANN001
            return saved_json_holder[0] if saved_json_holder else None

        with (
            patch(_PATCH_SAVE, side_effect=capture_save),
            patch(_PATCH_LOAD, side_effect=return_saved),
        ):
            await save_ha_checkpoint(mock_pool, _ENDPOINT, _TS_BASE, entity, "websocket")
            checkpoint, resume_ts = await load_ha_checkpoint(
                mock_pool, _ENDPOINT, overlap_seconds=30
            )

        assert checkpoint.last_event_ts == _TS_BASE
        assert checkpoint.last_entity_id == entity
        assert checkpoint.transport == "websocket"
        assert resume_ts == _TS_BASE - timedelta(seconds=30)

    async def test_events_before_resume_ts_are_deduped(self) -> None:
        """After loading a checkpoint, events at or before resume_ts are deduped.

        With stored_ts = 12:00:00 and overlap = 30s, resume_ts = 11:59:30.
        - Events at or before 11:59:30 → duplicate
        - Events after 11:59:30 → new (overlap window; Switchboard dedup handles replays)
        """
        ckpt = HACheckpoint(
            last_event_ts=_TS_BASE, last_entity_id="sensor.x", transport="websocket"
        )
        mock_pool = MagicMock()

        with patch(_PATCH_LOAD, new=AsyncMock(return_value=ckpt.to_json())):
            _, resume_ts = await load_ha_checkpoint(mock_pool, _ENDPOINT, overlap_seconds=30)
        # resume_ts = _TS_BASE - 30s = 11:59:30

        # Event 31s before stored_ts (11:59:29) → before resume_ts → dedup
        event_ts_before_resume = _TS_BASE - timedelta(seconds=31)
        assert is_duplicate_event(resume_ts, event_ts_before_resume) is True

        # Event exactly at resume_ts (11:59:30) → dedup
        assert is_duplicate_event(resume_ts, resume_ts) is True  # type: ignore[arg-type]

        # Event 20s before stored_ts (11:59:40) → after resume_ts → re-processed (not deduped here)
        event_ts_in_window = _TS_BASE - timedelta(seconds=20)
        assert is_duplicate_event(resume_ts, event_ts_in_window) is False

        # Event 1s after stored_ts → new
        event_ts_new = _TS_BASE + timedelta(seconds=1)
        assert is_duplicate_event(resume_ts, event_ts_new) is False

    async def test_empty_checkpoint_allows_all_events(self) -> None:
        """Fresh start (no checkpoint) — no events are considered duplicates."""
        mock_pool = MagicMock()

        with patch(_PATCH_LOAD, new=AsyncMock(return_value=None)):
            _, resume_ts = await load_ha_checkpoint(mock_pool, _ENDPOINT, overlap_seconds=30)

        assert resume_ts is None
        assert is_duplicate_event(resume_ts, _TS_BASE) is False
        assert is_duplicate_event(resume_ts, _TS_OLDER) is False
        assert is_duplicate_event(resume_ts, _TS_NEWER) is False

    async def test_checkpoint_advance_and_dedup_integration(self) -> None:
        """HACheckpoint.advance() correctly positions the cursor for subsequent dedup."""
        ckpt = HACheckpoint.empty()

        # Process first event
        ckpt.advance(_TS_BASE, "sensor.a", "websocket")

        # Process second event (newer)
        ckpt.advance(_TS_NEWER, "sensor.b", "websocket")

        # Simulate restart: compute resume_ts
        resume_ts = adjusted_resume_ts(ckpt, overlap_seconds=30)
        assert resume_ts is not None
        expected_resume = _TS_NEWER - timedelta(seconds=30)
        assert resume_ts == expected_resume

        # Old events should be deduped
        assert is_duplicate_event(resume_ts, _TS_BASE - timedelta(seconds=10)) is True
        assert is_duplicate_event(resume_ts, _TS_OLDER) is True

        # New events should pass
        assert is_duplicate_event(resume_ts, _TS_NEWER + timedelta(seconds=1)) is False

    async def test_different_endpoints_have_independent_checkpoints(self) -> None:
        """Each endpoint_identity uses its own cursor key in DB."""
        ep1 = "home_assistant:home1.local:8123"
        ep2 = "home_assistant:home2.local:8123"
        mock_pool = MagicMock()

        captured_endpoints: list[str] = []

        async def capture_save(pool, connector_type, endpoint, value):  # noqa: ANN001
            captured_endpoints.append(endpoint)

        with patch(_PATCH_SAVE, side_effect=capture_save):
            await save_ha_checkpoint(mock_pool, ep1, _TS_BASE, "sensor.x", "websocket")
            await save_ha_checkpoint(mock_pool, ep2, _TS_NEWER, "sensor.y", "websocket")

        assert len(captured_endpoints) == 2
        assert captured_endpoints[0] == ep1
        assert captured_endpoints[1] == ep2
        assert captured_endpoints[0] != captured_endpoints[1]

    def test_timezone_roundtrip_preserves_utc(self) -> None:
        """Serialising and deserialising a UTC-aware datetime preserves the offset."""
        ts_utc = datetime(2024, 3, 15, 9, 30, 45, 123456, tzinfo=UTC)
        ckpt = HACheckpoint(last_event_ts=ts_utc, last_entity_id="sensor.x", transport="websocket")
        restored = HACheckpoint.from_json(ckpt.to_json())
        assert restored.last_event_ts is not None
        # Value must be equal (same instant)
        assert restored.last_event_ts == ts_utc

    def test_timezone_roundtrip_non_utc(self) -> None:
        """Non-UTC aware datetimes survive a round-trip via ISO 8601."""
        tz_plus2 = timezone(timedelta(hours=2))
        ts_local = datetime(2024, 3, 15, 11, 30, 45, tzinfo=tz_plus2)
        ckpt = HACheckpoint(
            last_event_ts=ts_local, last_entity_id="sensor.x", transport="websocket"
        )
        restored = HACheckpoint.from_json(ckpt.to_json())
        assert restored.last_event_ts is not None
        assert restored.last_event_ts == ts_local
