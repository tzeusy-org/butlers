"""Voice Activity Detection (VAD) state machine for the live-listener connector.

Implements:
- Silero VAD ONNX model wrapper (CPU-only, < 2 MB)
- Two-state machine: SILENCE ↔ SPEAKING
- Configurable onset/offset thresholds and frame counts
- Segment duration bounds (min discard, max force-split)

Spec reference:
  openspec/changes/connector-live-listener/specs/connector-live-listener/spec.md
  § Voice Activity Detection, § Segment duration bounds
"""

from __future__ import annotations

import enum
import logging
import struct
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Audio constants
SAMPLE_RATE = 16_000  # Hz — Whisper native input
BYTES_PER_SAMPLE = 2  # 16-bit PCM = 2 bytes
FRAME_MS = 30  # Silero VAD processes 30 ms frames
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 480 samples per 30 ms frame
FRAME_BYTES = FRAME_SAMPLES * BYTES_PER_SAMPLE  # 960 bytes per 30 ms frame

# Optional Silero/ONNX import — connector still operates without it (tests use mock)
try:
    import numpy as np
    import onnxruntime as ort

    ONNX_AVAILABLE = True
except ImportError:
    np = None  # type: ignore[assignment]
    ort = None  # type: ignore[assignment]
    ONNX_AVAILABLE = False


class VadState(enum.Enum):
    """VAD state machine states."""

    SILENCE = "silence"
    SPEAKING = "speaking"


@dataclass
class SpeechSegment:
    """A completed speech segment ready for transcription.

    Attributes:
        audio_bytes: Raw 16-bit signed PCM at 16 kHz, mono.
        mic_name: Originating microphone name.
        onset_frame_index: Frame index when speech onset was detected.
        offset_ts: ``time.monotonic()`` timestamp when speech offset was detected.
        duration_ms: Segment duration in milliseconds.
        forced_split: True when the segment was force-split at max duration.
    """

    audio_bytes: bytes
    mic_name: str
    onset_frame_index: int
    offset_ts: float
    duration_ms: int
    forced_split: bool = False


@dataclass
class VadConfig:
    """Configuration for the VAD state machine."""

    onset_threshold: float = 0.5
    """Speech probability must exceed this to count as an onset frame."""

    offset_threshold: float = 0.3
    """Speech probability must drop below this to count as an offset frame."""

    onset_frames: int = 3
    """Number of consecutive onset frames required to transition SILENCE → SPEAKING (~90 ms)."""

    offset_frames: int = 10
    """Number of consecutive offset frames required to transition SPEAKING → SILENCE (~300 ms)."""

    min_segment_ms: int = 300
    """Segments shorter than this are discarded as noise."""

    max_segment_ms: int = 30_000
    """Segments exceeding this are force-split and both halves forwarded."""


class SileroVad:
    """Thin wrapper around the Silero VAD ONNX model.

    The model processes 30 ms frames of 16 kHz mono 16-bit PCM and returns a
    speech probability in [0, 1].

    The ONNX model is loaded lazily on first call to ``score()``.  If
    ``onnxruntime`` is not installed or the model path is not set, every call
    returns 0.0 (treated as silence) and a warning is emitted once.
    """

    def __init__(self, model_path: str | None = None) -> None:
        """Initialise the Silero VAD wrapper.

        Args:
            model_path: Path to the Silero VAD ONNX model file.  When
                ``None``, the wrapper will attempt to download / locate the
                model automatically using ``silero-vad`` helper utilities if
                available; otherwise it falls back to returning 0.0.
        """
        self._model_path = model_path
        self._session: object | None = None
        self._warned_unavailable = False

        # Silero VAD LSTM state tensors (must persist across frames)
        self._h: object | None = None
        self._c: object | None = None
        self._sr_tensor: object | None = None

    def load(self) -> None:
        """Load the ONNX session.  Safe to call multiple times."""
        if self._session is not None:
            return
        if not ONNX_AVAILABLE:
            if not self._warned_unavailable:
                logger.warning(
                    "onnxruntime / numpy not installed — VAD will return 0.0 for all frames. "
                    "Install with: uv pip install onnxruntime numpy"
                )
                self._warned_unavailable = True
            return

        if self._model_path is None:
            logger.warning(
                "No Silero VAD model path configured — VAD will return 0.0 for all frames."
            )
            return

        try:
            opts = ort.SessionOptions()
            opts.inter_op_num_threads = 1
            opts.intra_op_num_threads = 1
            self._session = ort.InferenceSession(self._model_path, sess_options=opts)
            self._reset_state()
            logger.info("Loaded Silero VAD model from %s", self._model_path)
        except Exception:
            logger.exception("Failed to load Silero VAD ONNX model from %s", self._model_path)

    def reset_state(self) -> None:
        """Reset LSTM hidden state between segments."""
        if self._session is not None:
            self._reset_state()

    def _reset_state(self) -> None:
        """Allocate zero tensors for LSTM h/c state and SR."""
        self._h = np.zeros((2, 1, 64), dtype=np.float32)
        self._c = np.zeros((2, 1, 64), dtype=np.float32)
        self._sr_tensor = np.array([SAMPLE_RATE], dtype=np.int64)

    def score(self, frame_bytes: bytes) -> float:
        """Compute speech probability for a single 30 ms PCM frame.

        Args:
            frame_bytes: Exactly ``FRAME_BYTES`` bytes of 16-bit signed PCM
                at 16 kHz mono.

        Returns:
            Speech probability in [0.0, 1.0].  Returns 0.0 if the model is
            not loaded or the frame size is incorrect.
        """
        if self._session is None:
            return 0.0

        if len(frame_bytes) != FRAME_BYTES:
            logger.debug(
                "VAD: unexpected frame size %d (expected %d) — returning 0.0",
                len(frame_bytes),
                FRAME_BYTES,
            )
            return 0.0

        # Decode PCM int16 → float32 in [-1, 1]
        n_samples = FRAME_SAMPLES
        samples = struct.unpack(f"<{n_samples}h", frame_bytes)
        audio = np.array(samples, dtype=np.float32) / 32768.0
        audio = audio[np.newaxis, :]  # shape: (1, 480)

        ort_inputs = {
            "input": audio,
            "sr": self._sr_tensor,
            "h": self._h,
            "c": self._c,
        }
        out, self._h, self._c = self._session.run(None, ort_inputs)
        return float(out[0, 0])


@dataclass
class _VadStateMachineState:
    """Mutable state used internally by VadStateMachine."""

    state: VadState = VadState.SILENCE
    onset_count: int = 0
    """Consecutive frames with speech probability above onset_threshold."""
    offset_count: int = 0
    """Consecutive frames with speech probability below offset_threshold."""
    segment_frames: list[bytes] = field(default_factory=list)
    """Accumulated PCM frames for the current speaking segment."""
    frame_index: int = 0
    """Global frame counter (incremented for each frame processed)."""
    speech_onset_frame_index: int = 0
    """Frame index when the current speech onset occurred."""


class VadStateMachine:
    """Two-state voice activity detection state machine.

    Accepts raw 30 ms PCM frames, maintains SILENCE/SPEAKING state, and
    yields completed :class:`SpeechSegment` objects.

    Usage::

        vad = VadStateMachine(config, silero_vad, mic_name="kitchen")
        vad.load()
        for frame in audio_frames:
            for segment in vad.process_frame(frame, offset_ts=time.monotonic()):
                # segment is a SpeechSegment ready for transcription
                ...

    Thread safety:
        Not thread-safe.  Should be driven from a single asyncio task.
    """

    def __init__(
        self,
        config: VadConfig,
        model: SileroVad,
        mic_name: str,
    ) -> None:
        self._config = config
        self._model = model
        self._mic_name = mic_name
        self._st = _VadStateMachineState()

    def load(self) -> None:
        """Load the underlying ONNX model (idempotent)."""
        self._model.load()

    @property
    def state(self) -> VadState:
        """Current VAD state."""
        return self._st.state

    @property
    def is_speaking(self) -> bool:
        """True when the VAD is in the SPEAKING state."""
        return self._st.state == VadState.SPEAKING

    @property
    def current_segment_bytes(self) -> bytes:
        """PCM bytes accumulated in the current speaking segment (may be empty)."""
        return b"".join(self._st.segment_frames)

    def reset(self) -> None:
        """Reset all state (used after device reconnection)."""
        self._st = _VadStateMachineState()
        self._model.reset_state()

    def process_frame(self, frame_bytes: bytes, offset_ts: float) -> list[SpeechSegment]:
        """Process a single 30 ms PCM frame.

        Args:
            frame_bytes: Exactly ``FRAME_BYTES`` bytes of 16-bit signed PCM.
            offset_ts: ``time.monotonic()`` timestamp for this frame (used as
                segment offset_ts when speech ends).

        Returns:
            A list of :class:`SpeechSegment` objects completed in this call.
            Normally empty or contains one item; may contain two when a
            force-split occurs (both halves are returned).
        """
        self._st.frame_index += 1
        prob = self._model.score(frame_bytes)
        completed: list[SpeechSegment] = []

        if self._st.state == VadState.SILENCE:
            completed.extend(self._handle_silence(frame_bytes, prob))
        else:
            completed.extend(self._handle_speaking(frame_bytes, prob, offset_ts))

        return completed

    # ------------------------------------------------------------------
    # State handlers
    # ------------------------------------------------------------------

    def _handle_silence(self, frame_bytes: bytes, prob: float) -> list[SpeechSegment]:
        """Process a frame while in SILENCE state."""
        cfg = self._config
        st = self._st

        if prob >= cfg.onset_threshold:
            st.onset_count += 1
        else:
            st.onset_count = 0

        if st.onset_count >= cfg.onset_frames:
            # Transition → SPEAKING
            logger.debug(
                "VAD[%s]: SILENCE → SPEAKING (frame %d, prob=%.3f)",
                self._mic_name,
                st.frame_index,
                prob,
            )
            st.state = VadState.SPEAKING
            st.onset_count = 0
            st.offset_count = 0
            st.speech_onset_frame_index = st.frame_index
            # Include the onset frames that triggered the transition.
            # We have only the current frame in hand; pre-roll is not implemented.
            st.segment_frames = [frame_bytes]

        return []

    def _handle_speaking(
        self,
        frame_bytes: bytes,
        prob: float,
        offset_ts: float,
    ) -> list[SpeechSegment]:
        """Process a frame while in SPEAKING state."""
        cfg = self._config
        st = self._st
        completed: list[SpeechSegment] = []

        # Accumulate frame regardless of probability (we need the audio)
        st.segment_frames.append(frame_bytes)

        # Check for force-split (max segment duration exceeded)
        current_ms = len(st.segment_frames) * FRAME_MS
        if current_ms >= cfg.max_segment_ms:
            logger.debug(
                "VAD[%s]: force-split at %d ms (frame %d)",
                self._mic_name,
                current_ms,
                st.frame_index,
            )
            segment = self._emit_segment(offset_ts, forced_split=True)
            if segment is not None:
                completed.append(segment)
            # Continue in SPEAKING state; reset buffer for second half
            # (already done inside _emit_segment)
            return completed

        # Check for speech offset transition
        if prob < cfg.offset_threshold:
            st.offset_count += 1
        else:
            st.offset_count = 0

        if st.offset_count >= cfg.offset_frames:
            # Transition → SILENCE
            logger.debug(
                "VAD[%s]: SPEAKING → SILENCE (frame %d, prob=%.3f, segment_ms=%d)",
                self._mic_name,
                st.frame_index,
                prob,
                current_ms,
            )
            segment = self._emit_segment(offset_ts, forced_split=False)
            if segment is not None:
                completed.append(segment)

        return completed

    def _emit_segment(self, offset_ts: float, *, forced_split: bool) -> SpeechSegment | None:
        """Finalise the current segment buffer and return a SpeechSegment.

        Returns None when the segment is below ``min_segment_ms`` (noise discard).
        On a natural offset, transitions to SILENCE. On a force-split, the state
        machine remains in SPEAKING so the second half of the utterance is captured
        without requiring a new onset transition.
        """
        st = self._st
        cfg = self._config

        audio_bytes = b"".join(st.segment_frames)
        duration_ms = len(st.segment_frames) * FRAME_MS
        onset_frame_index = st.speech_onset_frame_index

        # Reset segment buffer for the next segment (or force-split continuation).
        st.segment_frames = []
        self._model.reset_state()

        # On natural speech offset, transition to SILENCE.
        # On force-split, remain in SPEAKING so the second half is captured.
        if not forced_split:
            st.state = VadState.SILENCE
            st.onset_count = 0
            st.offset_count = 0

        if duration_ms < cfg.min_segment_ms:
            logger.debug(
                "VAD[%s]: discarding short segment (%d ms < %d ms)",
                self._mic_name,
                duration_ms,
                cfg.min_segment_ms,
            )
            return None

        return SpeechSegment(
            audio_bytes=audio_bytes,
            mic_name=self._mic_name,
            onset_frame_index=onset_frame_index,
            offset_ts=offset_ts,
            duration_ms=duration_ms,
            forced_split=forced_split,
        )
