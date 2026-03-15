"""Tests for the live-listener audio capture pipeline.

Covers:
- RingBuffer: write/read, overflow (oldest-overwrite), capacity, clear
- Device enumeration helpers: MicDeviceSpec.from_dict, validate_devices
- MicPipeline: on_frame callback dispatch, reconnection backoff logic
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from butlers.connectors.live_listener.audio import (
    DeviceError,
    MicPipeline,
    RingBuffer,
    build_pipelines,
    list_input_devices,
    resolve_device,
    validate_devices,
)
from butlers.connectors.live_listener.config import LiveListenerConfig, MicDeviceSpec
from butlers.connectors.live_listener.vad import FRAME_BYTES

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FRAME = b"\x00\x01" * (FRAME_BYTES // 2)  # a valid FRAME_BYTES-length PCM frame


def _make_config(**kwargs: Any) -> LiveListenerConfig:
    base = dict(
        switchboard_mcp_url="http://localhost:41100/sse",
        devices=[MicDeviceSpec(name="kitchen", device="hw:0")],
        transcription_url="tcp://localhost:10300",
        reconnect_base_s=0.01,
        reconnect_max_s=0.1,
        ring_buffer_seconds=1.0,
    )
    base.update(kwargs)
    return LiveListenerConfig(**base)


# ---------------------------------------------------------------------------
# RingBuffer tests
# ---------------------------------------------------------------------------


class TestRingBuffer:
    def test_basic_write_read(self) -> None:
        buf = RingBuffer(capacity_frames=4)
        frame = b"\xab" * FRAME_BYTES
        buf.write(frame)
        assert buf.available == 1
        result = buf.read()
        assert result == frame
        assert buf.available == 0

    def test_read_empty_returns_none(self) -> None:
        buf = RingBuffer(capacity_frames=4)
        assert buf.read() is None

    def test_fifo_ordering(self) -> None:
        buf = RingBuffer(capacity_frames=4)
        frames = [bytes([i]) * FRAME_BYTES for i in range(3)]
        for f in frames:
            buf.write(f)
        for f in frames:
            assert buf.read() == f

    def test_overflow_drops_oldest(self) -> None:
        """When the buffer is full, writes overwrite the oldest frame."""
        # Capacity rounds up to power-of-2; use exactly cap=4
        buf = RingBuffer(capacity_frames=4)
        # Write 5 frames into a capacity-4 buffer
        for i in range(5):
            buf.write(bytes([i]) * FRAME_BYTES)
        # The oldest frame (index 0) was dropped; we should read [1,2,3,4]
        assert buf.available == 4
        for expected in [1, 2, 3, 4]:
            frame = buf.read()
            assert frame is not None
            assert frame[0] == expected

    def test_clear_discards_all_frames(self) -> None:
        buf = RingBuffer(capacity_frames=4)
        buf.write(FRAME)
        buf.write(FRAME)
        buf.clear()
        assert buf.available == 0
        assert buf.read() is None

    def test_capacity_rounds_up_to_power_of_two(self) -> None:
        buf = RingBuffer(capacity_frames=5)
        assert buf.capacity == 8  # next power of 2 >= 5

    def test_capacity_exact_power_of_two(self) -> None:
        buf = RingBuffer(capacity_frames=8)
        assert buf.capacity == 8

    def test_write_truncates_oversized_data(self) -> None:
        """Writes larger than chunk_bytes are truncated to chunk_bytes."""
        buf = RingBuffer(capacity_frames=4)
        oversized = b"\xbb" * (FRAME_BYTES + 100)
        buf.write(oversized)
        result = buf.read()
        assert result is not None
        assert len(result) == FRAME_BYTES

    def test_write_pads_undersized_data(self) -> None:
        """Writes smaller than chunk_bytes are zero-padded."""
        buf = RingBuffer(capacity_frames=4)
        short = b"\xcc" * (FRAME_BYTES - 10)
        buf.write(short)
        result = buf.read()
        assert result is not None
        assert len(result) == FRAME_BYTES
        # First bytes match original
        assert result[: FRAME_BYTES - 10] == short

    def test_multiple_write_read_cycles(self) -> None:
        buf = RingBuffer(capacity_frames=4)
        for i in range(20):
            buf.write(bytes([i % 256]) * FRAME_BYTES)
            result = buf.read()
            assert result is not None
            assert result[0] == i % 256

    def test_invalid_capacity_raises(self) -> None:
        with pytest.raises(ValueError, match="capacity_frames must be positive"):
            RingBuffer(capacity_frames=0)

    def test_invalid_chunk_bytes_raises(self) -> None:
        with pytest.raises(ValueError, match="chunk_bytes must be positive"):
            RingBuffer(capacity_frames=4, chunk_bytes=0)


# ---------------------------------------------------------------------------
# MicDeviceSpec tests
# ---------------------------------------------------------------------------


class TestMicDeviceSpec:
    def test_from_dict_string_device(self) -> None:
        spec = MicDeviceSpec.from_dict({"name": "kitchen", "device": "hw:0,0"})
        assert spec.name == "kitchen"
        assert spec.device == "hw:0,0"

    def test_from_dict_int_device(self) -> None:
        spec = MicDeviceSpec.from_dict({"name": "bedroom", "device": 2})
        assert spec.device == 2

    def test_from_dict_string_int_device(self) -> None:
        """String '3' should be parsed as integer index."""
        spec = MicDeviceSpec.from_dict({"name": "living", "device": "3"})
        assert spec.device == 3

    def test_from_dict_missing_name_raises(self) -> None:
        with pytest.raises(ValueError, match="missing 'name'"):
            MicDeviceSpec.from_dict({"device": "hw:0"})

    def test_from_dict_missing_device_raises(self) -> None:
        with pytest.raises(ValueError, match="missing 'device'"):
            MicDeviceSpec.from_dict({"name": "kitchen"})


# ---------------------------------------------------------------------------
# Device resolution tests (mocked PortAudio)
# ---------------------------------------------------------------------------


MOCK_DEVICES = [
    {"name": "Microphone (Realtek Audio)", "max_input_channels": 2, "default_samplerate": 48000},
    {"name": "USB PnP Audio Device", "max_input_channels": 1, "default_samplerate": 44100},
    {"name": "HDMI Output", "max_input_channels": 0, "default_samplerate": 48000},
]


class TestResolveDevice:
    def test_resolve_by_name_substring(self) -> None:
        spec = MicDeviceSpec(name="usb-mic", device="USB PnP")
        with (
            patch("butlers.connectors.live_listener.audio.SOUNDDEVICE_AVAILABLE", True),
            patch("butlers.connectors.live_listener.audio.sd") as mock_sd,
        ):
            mock_sd.query_devices.return_value = MOCK_DEVICES
            info = resolve_device(spec)
        assert info.name == "USB PnP Audio Device"
        assert info.index == 1

    def test_resolve_by_index(self) -> None:
        spec = MicDeviceSpec(name="realtek", device=0)
        with (
            patch("butlers.connectors.live_listener.audio.SOUNDDEVICE_AVAILABLE", True),
            patch("butlers.connectors.live_listener.audio.sd") as mock_sd,
        ):
            mock_sd.query_devices.return_value = MOCK_DEVICES
            info = resolve_device(spec)
        assert info.index == 0
        assert "Realtek" in info.name

    def test_resolve_by_index_no_inputs_raises(self) -> None:
        spec = MicDeviceSpec(name="hdmi", device=2)
        with (
            patch("butlers.connectors.live_listener.audio.SOUNDDEVICE_AVAILABLE", True),
            patch("butlers.connectors.live_listener.audio.sd") as mock_sd,
        ):
            mock_sd.query_devices.return_value = MOCK_DEVICES
            with pytest.raises(DeviceError, match="no input channels"):
                resolve_device(spec)

    def test_resolve_by_name_not_found_raises(self) -> None:
        spec = MicDeviceSpec(name="ghost", device="NonExistentDevice")
        with (
            patch("butlers.connectors.live_listener.audio.SOUNDDEVICE_AVAILABLE", True),
            patch("butlers.connectors.live_listener.audio.sd") as mock_sd,
        ):
            mock_sd.query_devices.return_value = MOCK_DEVICES
            with pytest.raises(DeviceError, match="not found"):
                resolve_device(spec)

    def test_sounddevice_not_available_raises(self) -> None:
        spec = MicDeviceSpec(name="kitchen", device="hw:0")
        with patch("butlers.connectors.live_listener.audio.SOUNDDEVICE_AVAILABLE", False):
            with pytest.raises(RuntimeError, match="sounddevice is not installed"):
                resolve_device(spec)

    def test_out_of_range_index_raises(self) -> None:
        spec = MicDeviceSpec(name="oob", device=99)
        with (
            patch("butlers.connectors.live_listener.audio.SOUNDDEVICE_AVAILABLE", True),
            patch("butlers.connectors.live_listener.audio.sd") as mock_sd,
        ):
            mock_sd.query_devices.return_value = MOCK_DEVICES
            with pytest.raises(DeviceError, match="out of range"):
                resolve_device(spec)


class TestValidateDevices:
    def test_all_valid(self) -> None:
        config = _make_config(
            devices=[
                MicDeviceSpec(name="realtek", device="Realtek"),
                MicDeviceSpec(name="usb", device="USB PnP"),
            ]
        )
        with (
            patch("butlers.connectors.live_listener.audio.SOUNDDEVICE_AVAILABLE", True),
            patch("butlers.connectors.live_listener.audio.sd") as mock_sd,
        ):
            mock_sd.query_devices.return_value = MOCK_DEVICES
            valid, errors = validate_devices(config)
        assert len(valid) == 2
        assert errors == []

    def test_one_invalid_one_valid(self) -> None:
        config = _make_config(
            devices=[
                MicDeviceSpec(name="good", device="Realtek"),
                MicDeviceSpec(name="bad", device="DoesNotExist"),
            ]
        )
        with (
            patch("butlers.connectors.live_listener.audio.SOUNDDEVICE_AVAILABLE", True),
            patch("butlers.connectors.live_listener.audio.sd") as mock_sd,
        ):
            mock_sd.query_devices.return_value = MOCK_DEVICES
            valid, errors = validate_devices(config)
        assert len(valid) == 1
        assert len(errors) == 1
        assert errors[0][0].name == "bad"

    def test_all_invalid(self) -> None:
        config = _make_config(devices=[MicDeviceSpec(name="ghost", device="GhostDevice")])
        with (
            patch("butlers.connectors.live_listener.audio.SOUNDDEVICE_AVAILABLE", True),
            patch("butlers.connectors.live_listener.audio.sd") as mock_sd,
        ):
            mock_sd.query_devices.return_value = MOCK_DEVICES
            valid, errors = validate_devices(config)
        assert valid == []
        assert len(errors) == 1


# ---------------------------------------------------------------------------
# MicPipeline tests
# ---------------------------------------------------------------------------


class TestMicPipelineOnFrame:
    """Test that MicPipeline correctly dispatches ring buffer frames to on_frame."""

    async def test_frames_dispatched_via_on_frame(self) -> None:
        """Frames written to ring buffer are dispatched to on_frame callback."""
        received: list[bytes] = []

        def on_frame(frame: bytes) -> None:
            received.append(frame)

        ring = RingBuffer(capacity_frames=16)
        config = _make_config()
        spec = MicDeviceSpec(name="test", device="fake")
        pipeline = MicPipeline(spec=spec, config=config, on_frame=on_frame, _ring_buffer=ring)
        pipeline._running = True

        # Pre-fill ring buffer with 3 frames
        for i in range(3):
            ring.write(bytes([i]) * FRAME_BYTES)

        # Run consume loop briefly
        pipeline._running = True
        consume_task = asyncio.create_task(pipeline._consume_loop())
        # Allow event loop to drain the buffer
        await asyncio.sleep(0.05)
        pipeline._running = False
        consume_task.cancel()
        try:
            await consume_task
        except asyncio.CancelledError:
            pass

        assert len(received) == 3
        for i, frame in enumerate(received):
            assert frame[0] == i

    async def test_on_frame_exception_does_not_stop_pipeline(self) -> None:
        """Exceptions in on_frame callback are caught and logged; pipeline continues."""
        received: list[bytes] = []
        call_count = 0

        def on_frame(frame: bytes) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                # Second frame raises — should be caught; pipeline must continue
                raise ValueError("deliberate test error")
            received.append(frame)

        ring = RingBuffer(capacity_frames=16)
        config = _make_config()
        spec = MicDeviceSpec(name="test", device="fake")
        pipeline = MicPipeline(spec=spec, config=config, on_frame=on_frame, _ring_buffer=ring)
        pipeline._running = True

        for i in range(3):
            ring.write(bytes([i]) * FRAME_BYTES)

        consume_task = asyncio.create_task(pipeline._consume_loop())
        await asyncio.sleep(0.05)
        pipeline._running = False
        consume_task.cancel()
        try:
            await consume_task
        except asyncio.CancelledError:
            pass

        # Frame 1 (call_count=1) succeeds, frame 2 (call_count=2) raises but pipeline
        # continues, frame 3 (call_count=3) succeeds → received has frames 0 and 2
        assert call_count == 3
        assert len(received) == 2


class TestMicPipelineReconnection:
    """Test reconnection backoff logic."""

    async def test_reconnect_increments_attempts(self) -> None:
        """Each failure increments reconnect_attempts."""
        config = _make_config(reconnect_base_s=0.001, reconnect_max_s=0.01)
        spec = MicDeviceSpec(name="test", device="fake")
        pipeline = MicPipeline(spec=spec, config=config)
        pipeline._running = True

        call_count = 0

        async def fail_open_consume() -> None:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError("test device error")
            # On 3rd attempt, stop the pipeline to end the loop
            pipeline._running = False

        mock = AsyncMock(side_effect=fail_open_consume)
        with patch.object(pipeline, "_open_and_consume", mock):
            await pipeline._run_with_reconnect()

        assert pipeline._state.reconnect_attempts == 2  # failed twice

    async def test_backoff_caps_at_max(self) -> None:
        """Backoff value is capped at reconnect_max_s."""
        config = _make_config(reconnect_base_s=0.001, reconnect_max_s=0.004)
        spec = MicDeviceSpec(name="test", device="fake")
        pipeline = MicPipeline(spec=spec, config=config)
        pipeline._running = True

        backoff_values: list[float] = []

        original_sleep = asyncio.sleep

        async def patched_sleep(delay: float) -> None:
            backoff_values.append(delay)
            await original_sleep(0)  # don't actually wait

        call_count = 0

        async def fail_open_consume() -> None:
            nonlocal call_count
            call_count += 1
            if call_count >= 5:
                pipeline._running = False
                return
            raise RuntimeError("device error")

        mock = AsyncMock(side_effect=fail_open_consume)
        with (
            patch.object(pipeline, "_open_and_consume", mock),
            patch("butlers.connectors.live_listener.audio.asyncio.sleep", patched_sleep),
        ):
            await pipeline._run_with_reconnect()

        # Backoff should cap at reconnect_max_s
        assert all(v <= config.reconnect_max_s for v in backoff_values)

    async def test_start_stop_lifecycle(self) -> None:
        """start() creates task; stop() cancels it cleanly."""
        config = _make_config()
        spec = MicDeviceSpec(name="test", device="fake")
        ring = RingBuffer(capacity_frames=16)
        pipeline = MicPipeline(spec=spec, config=config, _ring_buffer=ring)

        async def _fake_run_with_reconnect() -> None:
            # Just hang until cancelled
            try:
                await asyncio.sleep(9999)
            except asyncio.CancelledError:
                raise

        mock = AsyncMock(side_effect=_fake_run_with_reconnect)
        with patch.object(pipeline, "_run_with_reconnect", mock):
            await pipeline.start()
            assert pipeline._consumer_task is not None
            assert not pipeline._consumer_task.done()
            await pipeline.stop()
            assert pipeline._consumer_task is None


# ---------------------------------------------------------------------------
# build_pipelines tests
# ---------------------------------------------------------------------------


class TestBuildPipelines:
    def test_returns_pipeline_for_each_valid_device(self) -> None:
        config = _make_config(
            devices=[
                MicDeviceSpec(name="kitchen", device="Realtek"),
                MicDeviceSpec(name="bad", device="GhostDevice"),
            ]
        )
        with (
            patch("butlers.connectors.live_listener.audio.SOUNDDEVICE_AVAILABLE", True),
            patch("butlers.connectors.live_listener.audio.sd") as mock_sd,
        ):
            mock_sd.query_devices.return_value = MOCK_DEVICES
            pipelines, errors = build_pipelines(config)
        assert len(pipelines) == 1
        assert pipelines[0].mic_name == "kitchen"
        assert len(errors) == 1

    def test_on_frame_factory_applied(self) -> None:
        config = _make_config(devices=[MicDeviceSpec(name="kitchen", device="Realtek")])
        factory_calls: list[str] = []

        def factory(mic_name: str) -> None:
            factory_calls.append(mic_name)

            def _cb(frame: bytes) -> None:
                pass

            return _cb

        with (
            patch("butlers.connectors.live_listener.audio.SOUNDDEVICE_AVAILABLE", True),
            patch("butlers.connectors.live_listener.audio.sd") as mock_sd,
        ):
            mock_sd.query_devices.return_value = MOCK_DEVICES
            pipelines, _ = build_pipelines(config, on_frame_factory=factory)
        assert len(pipelines) == 1
        assert "kitchen" in factory_calls


# ---------------------------------------------------------------------------
# list_input_devices tests
# ---------------------------------------------------------------------------


class TestListInputDevices:
    def test_returns_only_input_devices(self) -> None:
        with (
            patch("butlers.connectors.live_listener.audio.SOUNDDEVICE_AVAILABLE", True),
            patch("butlers.connectors.live_listener.audio.sd") as mock_sd,
        ):
            mock_sd.query_devices.return_value = MOCK_DEVICES
            devices = list_input_devices()
        # HDMI Output has max_input_channels=0, should be excluded
        names = [d.name for d in devices]
        assert "HDMI Output" not in names
        assert len(devices) == 2

    def test_sounddevice_unavailable_returns_empty(self) -> None:
        with patch("butlers.connectors.live_listener.audio.SOUNDDEVICE_AVAILABLE", False):
            devices = list_input_devices()
        assert devices == []
