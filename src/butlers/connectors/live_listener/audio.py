"""Audio capture pipeline for the live-listener connector.

Implements:
- Lock-free ring buffer (``RingBuffer``) for PCM audio frames
- ``MicPipeline``: sounddevice.InputStream → ring buffer → asyncio consumer
- Device enumeration and validation against PortAudio device list
- Device reconnection with exponential backoff

Spec reference:
  openspec/changes/connector-live-listener/specs/connector-live-listener/spec.md
  § Audio Capture
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

from butlers.connectors.live_listener.config import LiveListenerConfig, MicDeviceSpec
from butlers.connectors.live_listener.vad import FRAME_BYTES, SAMPLE_RATE

logger = logging.getLogger(__name__)

# sounddevice is an optional dependency — handle gracefully.
try:
    import sounddevice as sd

    SOUNDDEVICE_AVAILABLE = True
except (ImportError, OSError):
    sd = None  # type: ignore[assignment]
    SOUNDDEVICE_AVAILABLE = False


# ---------------------------------------------------------------------------
# Ring buffer
# ---------------------------------------------------------------------------


class RingBuffer:
    """Lock-free single-producer / single-consumer ring buffer for PCM frames.

    The producer (PortAudio callback, runs in a C thread) writes frames
    without holding a Python lock.  The consumer (asyncio task) reads frames
    without holding a lock.  Overwrite semantics: when the buffer is full the
    oldest unread frame is silently discarded (stale audio has no value).

    All reads / writes operate on fixed-size *chunks* of ``chunk_bytes`` bytes.

    Implementation notes:
        - ``_write_pos`` is updated only by the producer.
        - ``_read_pos`` is updated only by the consumer.
        - Both positions are monotonically increasing integers (never wrap).
        - Capacity is always a power of two to allow cheap modulo via bit-mask.

    Thread safety:
        Safe for exactly one writer thread and one reader (asyncio) task.
        Python's GIL guarantees that integer assignment is atomic for ``int``
        objects, so no additional synchronisation primitives are needed.
    """

    def __init__(self, capacity_frames: int, chunk_bytes: int = FRAME_BYTES) -> None:
        """Initialise the ring buffer.

        Args:
            capacity_frames: Number of frames to hold.  Rounded up to the
                next power of two for efficient modulo.
            chunk_bytes: Size of each frame in bytes.  Defaults to ``FRAME_BYTES``
                (960 bytes = 480 samples × 2 bytes @ 16 kHz).
        """
        if capacity_frames <= 0:
            raise ValueError(f"capacity_frames must be positive, got {capacity_frames}")
        if chunk_bytes <= 0:
            raise ValueError(f"chunk_bytes must be positive, got {chunk_bytes}")

        # Round up to next power of two
        cap = 1
        while cap < capacity_frames:
            cap <<= 1

        self._capacity = cap
        self._mask = cap - 1
        self._chunk_bytes = chunk_bytes

        # Pre-allocate storage as a flat bytearray; each slot is chunk_bytes long.
        self._buf = bytearray(cap * chunk_bytes)

        # Monotonically increasing positions (never wrap to keep logic simple)
        self._write_pos: int = 0
        self._read_pos: int = 0

    # ------------------------------------------------------------------
    # Producer side (called from PortAudio C callback thread)
    # ------------------------------------------------------------------

    def write(self, data: bytes | bytearray) -> None:
        """Write a single frame to the buffer.

        If the buffer is full, the oldest unread frame is silently overwritten.

        Args:
            data: Exactly ``chunk_bytes`` bytes of PCM audio.
        """
        if len(data) != self._chunk_bytes:
            # Truncate or pad silently to maintain fixed frame size
            if len(data) < self._chunk_bytes:
                data = bytes(data) + bytes(self._chunk_bytes - len(data))
            else:
                data = data[: self._chunk_bytes]

        slot = (self._write_pos & self._mask) * self._chunk_bytes
        self._buf[slot : slot + self._chunk_bytes] = data
        self._write_pos += 1

        # If the buffer was full, advance read_pos to drop the oldest frame
        if self._write_pos - self._read_pos > self._capacity:
            self._read_pos = self._write_pos - self._capacity

    # ------------------------------------------------------------------
    # Consumer side (called from asyncio event loop)
    # ------------------------------------------------------------------

    def read(self) -> bytes | None:
        """Read the next available frame.

        Returns:
            Frame bytes, or ``None`` if the buffer is empty.
        """
        if self._read_pos >= self._write_pos:
            return None
        slot = (self._read_pos & self._mask) * self._chunk_bytes
        frame = bytes(self._buf[slot : slot + self._chunk_bytes])
        self._read_pos += 1
        return frame

    @property
    def available(self) -> int:
        """Number of unread frames currently in the buffer."""
        return max(0, self._write_pos - self._read_pos)

    @property
    def capacity(self) -> int:
        """Total frame capacity of the buffer."""
        return self._capacity

    def clear(self) -> None:
        """Discard all buffered frames."""
        self._read_pos = self._write_pos


# ---------------------------------------------------------------------------
# Device enumeration
# ---------------------------------------------------------------------------


@dataclass
class DeviceInfo:
    """Resolved PortAudio device information."""

    index: int
    name: str
    max_input_channels: int
    default_samplerate: float


class DeviceError(Exception):
    """Raised when a configured device is not found or invalid."""


def list_input_devices() -> list[DeviceInfo]:
    """Return all PortAudio input devices.

    Returns an empty list when ``sounddevice`` is not available.
    """
    if not SOUNDDEVICE_AVAILABLE:
        return []
    devices = []
    for idx, dev in enumerate(sd.query_devices()):
        if dev.get("max_input_channels", 0) > 0:
            devices.append(
                DeviceInfo(
                    index=idx,
                    name=dev["name"],
                    max_input_channels=dev["max_input_channels"],
                    default_samplerate=dev.get("default_samplerate", SAMPLE_RATE),
                )
            )
    return devices


def resolve_device(spec: MicDeviceSpec) -> DeviceInfo:
    """Resolve a mic device spec against PortAudio's device list.

    Args:
        spec: The device specification from configuration.

    Returns:
        A :class:`DeviceInfo` for the matched device.

    Raises:
        DeviceError: If the device cannot be found.
        RuntimeError: If ``sounddevice`` is not installed.
    """
    if not SOUNDDEVICE_AVAILABLE:
        raise RuntimeError("sounddevice is not installed. Install with: uv pip install sounddevice")

    if isinstance(spec.device, int):
        # Direct device index — validate it exists
        all_devs = sd.query_devices()
        if spec.device < 0 or spec.device >= len(all_devs):
            raise DeviceError(
                f"Device index {spec.device} (mic '{spec.name}') is out of range. "
                f"Available devices: 0–{len(all_devs) - 1}"
            )
        dev = all_devs[spec.device]
        if dev.get("max_input_channels", 0) == 0:
            raise DeviceError(
                f"Device index {spec.device} (mic '{spec.name}', name='{dev['name']}') "
                "has no input channels."
            )
        return DeviceInfo(
            index=spec.device,
            name=dev["name"],
            max_input_channels=dev["max_input_channels"],
            default_samplerate=dev.get("default_samplerate", SAMPLE_RATE),
        )

    # String device name — search by substring match (PortAudio style)
    target = str(spec.device).lower()
    for info in list_input_devices():
        if target in info.name.lower():
            return info

    available = [d.name for d in list_input_devices()]
    raise DeviceError(
        f"Device '{spec.device}' (mic '{spec.name}') not found in PortAudio device list. "
        f"Available input devices: {available!r}"
    )


def validate_devices(
    config: LiveListenerConfig,
) -> tuple[list[tuple[MicDeviceSpec, DeviceInfo]], list[tuple[MicDeviceSpec, str]]]:
    """Validate all configured devices against PortAudio.

    Args:
        config: Full connector configuration.

    Returns:
        A tuple of (valid_devices, errors) where:
        - valid_devices is a list of (spec, DeviceInfo) pairs
        - errors is a list of (spec, error_message) pairs for failures
    """
    valid: list[tuple[MicDeviceSpec, DeviceInfo]] = []
    errors: list[tuple[MicDeviceSpec, str]] = []

    for spec in config.devices:
        try:
            info = resolve_device(spec)
            valid.append((spec, info))
            logger.info(
                "Validated mic device '%s': PortAudio index=%d name='%s'",
                spec.name,
                info.index,
                info.name,
            )
        except (DeviceError, RuntimeError) as exc:
            logger.error(
                "Mic device '%s' not found: %s",
                spec.name,
                exc,
            )
            errors.append((spec, str(exc)))

    return valid, errors


# ---------------------------------------------------------------------------
# MicPipeline
# ---------------------------------------------------------------------------


@dataclass
class MicPipelineState:
    """Health/status state for a MicPipeline."""

    connected: bool = False
    reconnect_attempts: int = 0
    last_error: str | None = None
    frames_written: int = 0
    frames_read: int = 0


class MicPipeline:
    """Single-microphone audio capture pipeline.

    Responsibilities:
    1. Opens a ``sounddevice.InputStream`` for the configured device.
    2. The PortAudio callback writes PCM frames to a lock-free ring buffer.
    3. An asyncio consumer task drains the ring buffer and calls ``on_frame``
       for each complete 30 ms frame.
    4. Reconnects with exponential backoff on device disconnect or error.

    The pipeline is intentionally decoupled from VAD and transcription so
    that those layers can be tested independently.

    Usage::

        async def handle_frame(frame: bytes) -> None:
            ...

        async with MicPipeline(spec, config, on_frame=handle_frame) as pipeline:
            await asyncio.sleep(...)  # pipeline runs in background

    Or manually::

        pipeline = MicPipeline(spec, config, on_frame=handle_frame)
        await pipeline.start()
        ...
        await pipeline.stop()
    """

    # Number of frames to keep in the ring buffer (10 seconds of audio)
    DEFAULT_RING_BUFFER_SECONDS = 10.0

    def __init__(
        self,
        spec: MicDeviceSpec,
        config: LiveListenerConfig,
        on_frame: Callable[[bytes], None] | None = None,
        _ring_buffer: RingBuffer | None = None,
    ) -> None:
        """Initialise the MicPipeline.

        Args:
            spec: Microphone device specification (name + PortAudio device).
            config: Full connector config (backoff, buffer settings, etc.).
            on_frame: Synchronous callback invoked for each 30 ms PCM frame
                drained from the ring buffer.  If ``None``, frames are
                silently discarded.  The callback runs in the asyncio event
                loop (not in the PortAudio callback thread).
            _ring_buffer: Override the ring buffer (for testing).
        """
        self._spec = spec
        self._config = config
        self._on_frame = on_frame
        self._state = MicPipelineState()

        # ring_buffer_seconds * 1000 ms / 30 ms per frame
        ring_capacity_frames = int(config.ring_buffer_seconds * 1000 / 30) + 16  # small headroom
        if _ring_buffer is not None:
            self._ring = _ring_buffer
        else:
            self._ring = RingBuffer(capacity_frames=ring_capacity_frames)

        self._stream: Any | None = None  # sounddevice.InputStream
        self._consumer_task: asyncio.Task | None = None
        self._running = False
        self._reconnect_event = asyncio.Event()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def mic_name(self) -> str:
        """Microphone name from the device spec."""
        return self._spec.name

    @property
    def state(self) -> MicPipelineState:
        """Current pipeline state (health indicator)."""
        return self._state

    async def start(self) -> None:
        """Start the pipeline (open stream + consumer task).

        This method returns quickly; the consumer task runs in the background.
        """
        if self._running:
            return
        self._running = True
        self._consumer_task = asyncio.create_task(
            self._run_with_reconnect(), name=f"mic-pipeline-{self._spec.name}"
        )
        logger.info("MicPipeline '%s': started", self._spec.name)

    async def stop(self) -> None:
        """Stop the pipeline gracefully."""
        self._running = False
        if self._consumer_task is not None:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass
            self._consumer_task = None
        self._close_stream()
        logger.info("MicPipeline '%s': stopped", self._spec.name)

    async def __aenter__(self) -> MicPipeline:
        await self.start()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.stop()

    # ------------------------------------------------------------------
    # Async frame iterator (convenience)
    # ------------------------------------------------------------------

    async def frames(self) -> AsyncIterator[bytes]:
        """Async iterator that yields PCM frames from the ring buffer.

        This is an alternative to providing ``on_frame`` callback; it yields
        frames at the same rate they are produced (30 ms intervals).

        Note: Only one consumer should use ``frames()`` or ``on_frame`` at a
        time.
        """
        while self._running:
            frame = self._ring.read()
            if frame is not None:
                self._state.frames_read += 1
                yield frame
            else:
                # Brief yield to avoid busy-wait
                await asyncio.sleep(0.005)

    # ------------------------------------------------------------------
    # Internal: reconnection loop
    # ------------------------------------------------------------------

    async def _run_with_reconnect(self) -> None:
        """Outer reconnection loop with exponential backoff."""
        backoff = self._config.reconnect_base_s
        max_backoff = self._config.reconnect_max_s

        while self._running:
            try:
                await self._open_and_consume()
                # If _open_and_consume returns normally (not via exception),
                # the stream ended cleanly — try again immediately.
                backoff = self._config.reconnect_base_s
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._state.connected = False
                self._state.last_error = str(exc)
                self._state.reconnect_attempts += 1
                logger.warning(
                    "MicPipeline '%s': error (%s), reconnecting in %.1fs (attempt %d)",
                    self._spec.name,
                    exc,
                    backoff,
                    self._state.reconnect_attempts,
                )
                self._close_stream()
                try:
                    await asyncio.sleep(backoff)
                except asyncio.CancelledError:
                    break
                backoff = min(backoff * 2, max_backoff)

    async def _open_and_consume(self) -> None:
        """Open the sounddevice stream and run the consumer loop.

        Raises on device error or when the stream ends unexpectedly.
        """
        if not SOUNDDEVICE_AVAILABLE:
            raise RuntimeError(
                "sounddevice is not installed. Install with: uv pip install sounddevice"
            )

        # Resolve device index
        try:
            device_info = resolve_device(self._spec)
        except (DeviceError, RuntimeError) as exc:
            raise RuntimeError(
                f"MicPipeline '{self._spec.name}': device resolution failed: {exc}"
            ) from exc

        logger.info(
            "MicPipeline '%s': opening stream on device %d ('%s')",
            self._spec.name,
            device_info.index,
            device_info.name,
        )

        # Clear stale buffer data before re-opening
        self._ring.clear()
        self._state.frames_written = 0
        self._state.frames_read = 0

        # PortAudio callback — runs in a C thread, MUST NOT block
        def _callback(
            indata: Any,
            frames: int,  # noqa: ARG001
            time_info: Any,  # noqa: ARG001
            status: Any,
        ) -> None:
            if status:
                logger.debug(
                    "MicPipeline '%s': sounddevice status: %s",
                    self._spec.name,
                    status,
                )
            # indata shape: (frames, 1) — mono channel, dtype=int16
            # Flatten and convert to bytes
            raw = bytes(indata.tobytes())
            # Write frame-by-frame into the ring buffer
            for offset in range(0, len(raw), FRAME_BYTES):
                chunk = raw[offset : offset + FRAME_BYTES]
                if len(chunk) == FRAME_BYTES:
                    self._ring.write(chunk)
                    self._state.frames_written += 1

        self._stream = sd.InputStream(
            device=device_info.index,
            channels=1,
            samplerate=SAMPLE_RATE,
            dtype="int16",
            blocksize=FRAME_BYTES // 2,  # samples per callback (not bytes)
            callback=_callback,
        )

        with self._stream:
            self._state.connected = True
            self._state.last_error = None
            logger.info(
                "MicPipeline '%s': stream open, consuming frames",
                self._spec.name,
            )
            await self._consume_loop()

    async def _consume_loop(self) -> None:
        """Drain the ring buffer and dispatch frames to the on_frame callback."""
        while self._running:
            frame = self._ring.read()
            if frame is not None:
                self._state.frames_read += 1
                if self._on_frame is not None:
                    try:
                        self._on_frame(frame)
                    except Exception:
                        logger.exception(
                            "MicPipeline '%s': on_frame callback raised",
                            self._spec.name,
                        )
            else:
                # Yield to event loop; poll again after a brief sleep
                await asyncio.sleep(0.005)

    def _close_stream(self) -> None:
        """Close the sounddevice stream if open."""
        if self._stream is not None:
            try:
                self._stream.close()
            except Exception:
                logger.debug(
                    "MicPipeline '%s': error closing stream (ignored)",
                    self._spec.name,
                    exc_info=True,
                )
            self._stream = None
            self._state.connected = False


# ---------------------------------------------------------------------------
# Convenience: build pipelines from config
# ---------------------------------------------------------------------------


def build_pipelines(
    config: LiveListenerConfig,
    on_frame_factory: Callable[[str], Callable[[bytes], None]] | None = None,
) -> tuple[list[MicPipeline], list[str]]:
    """Enumerate and validate devices, return ready MicPipeline instances.

    Args:
        config: Full connector configuration.
        on_frame_factory: Optional factory that receives a mic name and
            returns an ``on_frame`` callback for that mic.  If ``None``,
            pipelines are created without a frame callback.

    Returns:
        A tuple of (pipelines, error_messages) where:
        - pipelines: list of ready-to-start MicPipeline objects.
        - error_messages: list of device-resolution error strings for failed
          devices.
    """
    valid_devices, errors = validate_devices(config)
    error_messages = [f"mic '{s.name}': {msg}" for s, msg in errors]

    pipelines: list[MicPipeline] = []
    for spec, _info in valid_devices:
        on_frame: Callable[[bytes], None] | None = None
        if on_frame_factory is not None:
            on_frame = on_frame_factory(spec.name)
        pipelines.append(MicPipeline(spec=spec, config=config, on_frame=on_frame))

    return pipelines, error_messages


# ---------------------------------------------------------------------------
# Monotonic timestamp helper
# ---------------------------------------------------------------------------


def monotonic_ts() -> float:
    """Return current monotonic time (time.monotonic())."""
    return time.monotonic()
