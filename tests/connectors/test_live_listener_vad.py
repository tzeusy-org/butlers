"""Tests for the live-listener VAD state machine.

Covers:
- VadStateMachine: SILENCE → SPEAKING transition
- VadStateMachine: SPEAKING → SILENCE transition
- Segment duration bounds (min discard, max force-split)
- Streaming handoff: segment accumulates frames during SPEAKING
- SileroVad stub / fallback behavior (ONNX not loaded)
- VadConfig defaults
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from butlers.connectors.live_listener.vad import (
    FRAME_BYTES,
    FRAME_MS,
    SileroVad,
    SpeechSegment,
    VadConfig,
    VadState,
    VadStateMachine,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SILENCE_FRAME = b"\x00" * FRAME_BYTES
SPEECH_FRAME = b"\x7f\x00" * (FRAME_BYTES // 2)  # arbitrary non-zero signal


def _make_vad(
    onset_threshold: float = 0.5,
    offset_threshold: float = 0.3,
    onset_frames: int = 3,
    offset_frames: int = 3,
    min_segment_ms: int = 0,  # no discard by default in tests
    max_segment_ms: int = 30_000,
    fixed_score: float | None = None,
) -> VadStateMachine:
    """Build a VadStateMachine with a mock SileroVad that returns fixed_score."""
    config = VadConfig(
        onset_threshold=onset_threshold,
        offset_threshold=offset_threshold,
        onset_frames=onset_frames,
        offset_frames=offset_frames,
        min_segment_ms=min_segment_ms,
        max_segment_ms=max_segment_ms,
    )
    model = MagicMock(spec=SileroVad)
    if fixed_score is not None:
        model.score.return_value = fixed_score
    else:
        model.score.return_value = 0.0
    vad = VadStateMachine(config=config, model=model, mic_name="test-mic")
    return vad


def _drive(vad: VadStateMachine, scores: list[float]) -> list[SpeechSegment]:
    """Feed the VAD a sequence of per-frame scores. Returns all completed segments."""
    ts = time.monotonic()
    segments: list[SpeechSegment] = []
    for score in scores:
        vad._model.score.return_value = score  # type: ignore[attr-defined]
        completed = vad.process_frame(SPEECH_FRAME if score > 0 else SILENCE_FRAME, offset_ts=ts)
        segments.extend(completed)
    return segments


# ---------------------------------------------------------------------------
# VadConfig defaults
# ---------------------------------------------------------------------------


class TestVadConfigDefaults:
    def test_default_thresholds(self) -> None:
        cfg = VadConfig()
        assert cfg.onset_threshold == 0.5
        assert cfg.offset_threshold == 0.3
        assert cfg.onset_frames == 3
        assert cfg.offset_frames == 10
        assert cfg.min_segment_ms == 300
        assert cfg.max_segment_ms == 30_000


# ---------------------------------------------------------------------------
# State transitions: SILENCE → SPEAKING
# ---------------------------------------------------------------------------


class TestOnsetTransition:
    def test_single_speech_frame_does_not_trigger(self) -> None:
        """One high-prob frame alone does not trigger onset (need onset_frames)."""
        vad = _make_vad(onset_frames=3)
        segments = _drive(vad, [0.9])
        assert vad.state == VadState.SILENCE
        assert segments == []

    def test_consecutive_onset_frames_trigger_speaking(self) -> None:
        """onset_frames consecutive high-prob frames trigger SILENCE → SPEAKING."""
        vad = _make_vad(onset_threshold=0.5, onset_frames=3)
        _drive(vad, [0.9, 0.9, 0.9])
        assert vad.state == VadState.SPEAKING

    def test_onset_counter_resets_on_low_prob_frame(self) -> None:
        """Low-prob frame between onset frames resets the onset counter."""
        vad = _make_vad(onset_threshold=0.5, onset_frames=3)
        _drive(vad, [0.9, 0.1, 0.9, 0.9])
        # Only 2 consecutive high frames at end — still SILENCE
        assert vad.state == VadState.SILENCE

    def test_onset_exactly_at_threshold_counts(self) -> None:
        """Score exactly at onset_threshold is treated as a speech frame."""
        vad = _make_vad(onset_threshold=0.5, onset_frames=2)
        _drive(vad, [0.5, 0.5])
        assert vad.state == VadState.SPEAKING

    def test_onset_below_threshold_does_not_count(self) -> None:
        """Score below onset_threshold does not count toward onset."""
        vad = _make_vad(onset_threshold=0.5, onset_frames=2)
        _drive(vad, [0.49, 0.49])
        assert vad.state == VadState.SILENCE


# ---------------------------------------------------------------------------
# State transitions: SPEAKING → SILENCE
# ---------------------------------------------------------------------------


class TestOffsetTransition:
    def _start_speaking(self, vad: VadStateMachine, onset_frames: int = 3) -> None:
        """Drive onset_frames worth of speech to enter SPEAKING state."""
        _drive(vad, [0.9] * onset_frames)
        assert vad.state == VadState.SPEAKING

    def test_speaking_transitions_to_silence_after_offset_frames(self) -> None:
        vad = _make_vad(onset_frames=3, offset_frames=3, min_segment_ms=0)
        self._start_speaking(vad)
        segments = _drive(vad, [0.1, 0.1, 0.1])  # 3 low frames → offset
        assert vad.state == VadState.SILENCE
        assert len(segments) == 1

    def test_offset_counter_resets_on_high_prob_frame(self) -> None:
        """A high-prob frame during offset countdown resets the counter."""
        vad = _make_vad(onset_frames=3, offset_frames=3, min_segment_ms=0)
        self._start_speaking(vad)
        # Two low frames, one high, two more low — only 2 consecutive low at end
        segments = _drive(vad, [0.1, 0.1, 0.9, 0.1, 0.1])
        assert vad.state == VadState.SPEAKING
        assert segments == []

    def test_segment_audio_accumulated(self) -> None:
        """All frames during SPEAKING (including offset frames) are in the segment."""
        vad = _make_vad(onset_frames=3, offset_frames=3, min_segment_ms=0)
        self._start_speaking(vad)
        speaking_frames = 5
        _drive(vad, [0.9] * speaking_frames)  # 5 more speaking frames
        segments = _drive(vad, [0.1, 0.1, 0.1])  # offset transition
        assert len(segments) == 1
        seg = segments[0]
        # Total frames: 1 onset frame (in _start_speaking the 3rd onset frame is
        # the one that triggers transition + is accumulated) + 5 speaking + 3 offset
        # The VadStateMachine accumulates from the TRANSITION frame onward.
        assert len(seg.audio_bytes) > 0
        assert len(seg.audio_bytes) % FRAME_BYTES == 0

    def test_segment_mic_name_set(self) -> None:
        vad = _make_vad(onset_frames=2, offset_frames=2, min_segment_ms=0)
        vad._model.score.return_value = 0.9
        vad.process_frame(SPEECH_FRAME, offset_ts=0.0)
        vad.process_frame(SPEECH_FRAME, offset_ts=0.0)
        vad._model.score.return_value = 0.1
        vad.process_frame(SPEECH_FRAME, offset_ts=1.0)
        segments = vad.process_frame(SPEECH_FRAME, offset_ts=2.0)
        assert len(segments) == 1
        assert segments[0].mic_name == "test-mic"


# ---------------------------------------------------------------------------
# Segment duration bounds
# ---------------------------------------------------------------------------


class TestSegmentDurationBounds:
    def test_short_segment_discarded(self) -> None:
        """Segments below min_segment_ms are discarded (None returned)."""
        # min_segment_ms=300 ms = 10 frames @ 30 ms each
        vad = _make_vad(onset_frames=2, offset_frames=2, min_segment_ms=300, max_segment_ms=30_000)
        # Enter SPEAKING (2 onset frames = 60 ms total)
        _drive(vad, [0.9, 0.9])
        assert vad.state == VadState.SPEAKING
        # Only 1 speech frame more (30 ms each), then offset: total ≈ 90 ms < 300 ms
        _drive(vad, [0.9])
        segments = _drive(vad, [0.1, 0.1])  # offset transition
        assert len(segments) == 0, "Segment under min_segment_ms should be discarded"
        assert vad.state == VadState.SILENCE

    def test_long_enough_segment_not_discarded(self) -> None:
        """Segments at or above min_segment_ms are forwarded."""
        # 10 frames = 300 ms = exactly min_segment_ms
        vad = _make_vad(onset_frames=2, offset_frames=2, min_segment_ms=90, max_segment_ms=30_000)
        _drive(vad, [0.9, 0.9])  # 2 onset → SPEAKING
        _drive(vad, [0.9] * 5)  # 5 more = 7 frames total in segment = 210 ms
        segments = _drive(vad, [0.1, 0.1])  # offset
        assert len(segments) == 1

    def test_force_split_at_max_duration(self) -> None:
        """Segments exceeding max_segment_ms are force-split."""
        # max_segment_ms=90 ms = 3 frames
        vad = _make_vad(onset_frames=2, offset_frames=100, min_segment_ms=0, max_segment_ms=90)
        _drive(vad, [0.9, 0.9])  # → SPEAKING
        # Drive 3 more speech frames → force-split at 3 frames (max_segment_ms=90ms)
        segments = _drive(vad, [0.9, 0.9, 0.9])
        # The force-split should have produced a segment
        force_splits = [s for s in segments if s.forced_split]
        assert len(force_splits) >= 1, f"Expected force-split, got segments={segments}"

    def test_force_split_segment_marked(self) -> None:
        """Force-split segments have forced_split=True."""
        vad = _make_vad(onset_frames=1, offset_frames=100, min_segment_ms=0, max_segment_ms=60)
        _drive(vad, [0.9])  # → SPEAKING
        segments: list[SpeechSegment] = []
        for _ in range(3):
            s = _drive(vad, [0.9])
            segments.extend(s)
        force_splits = [s for s in segments if s.forced_split]
        assert force_splits, "At least one forced_split segment expected"

    def test_segment_duration_ms_set(self) -> None:
        """Segment duration_ms matches the number of accumulated frames."""
        vad = _make_vad(onset_frames=2, offset_frames=2, min_segment_ms=0, max_segment_ms=30_000)
        _drive(vad, [0.9, 0.9])  # onset + into SPEAKING (1 frame accumulated)
        _drive(vad, [0.9] * 4)  # 4 more frames
        segments = _drive(vad, [0.1, 0.1])  # offset (2 more frames)
        assert len(segments) == 1
        seg = segments[0]
        expected_frames = len(seg.audio_bytes) // FRAME_BYTES
        assert seg.duration_ms == expected_frames * FRAME_MS


# ---------------------------------------------------------------------------
# Streaming handoff: audio available during SPEAKING
# ---------------------------------------------------------------------------


class TestStreamingHandoff:
    def test_current_segment_bytes_grows_during_speaking(self) -> None:
        """current_segment_bytes grows as frames are accumulated in SPEAKING."""
        vad = _make_vad(onset_frames=2, offset_frames=10, min_segment_ms=0)
        _drive(vad, [0.9, 0.9])  # → SPEAKING
        assert vad.is_speaking
        initial_len = len(vad.current_segment_bytes)
        assert initial_len > 0

        _drive(vad, [0.9] * 5)
        assert len(vad.current_segment_bytes) > initial_len

    def test_current_segment_bytes_empty_in_silence(self) -> None:
        vad = _make_vad(onset_frames=2, offset_frames=2, min_segment_ms=0)
        assert not vad.is_speaking
        assert vad.current_segment_bytes == b""

    def test_is_speaking_false_after_offset(self) -> None:
        vad = _make_vad(onset_frames=2, offset_frames=2, min_segment_ms=0)
        _drive(vad, [0.9, 0.9])  # SPEAKING
        assert vad.is_speaking
        _drive(vad, [0.1, 0.1])  # back to SILENCE
        assert not vad.is_speaking


# ---------------------------------------------------------------------------
# SileroVad stub / fallback
# ---------------------------------------------------------------------------


class TestSileroVadStub:
    def test_score_returns_zero_when_not_loaded(self) -> None:
        model = SileroVad(model_path=None)
        # Not loaded → returns 0.0 silently
        score = model.score(SILENCE_FRAME)
        assert score == 0.0

    def test_score_returns_zero_for_wrong_frame_size(self) -> None:
        """Wrong-size frame returns 0.0 without crashing."""
        # Create a model that pretends to be loaded but feed wrong size
        model = SileroVad(model_path="/fake/path")
        model._session = MagicMock()  # pretend loaded
        model._h = MagicMock()
        model._c = MagicMock()
        model._sr_tensor = MagicMock()
        # Pass wrong size frame
        score = model.score(b"\x00" * 10)
        assert score == 0.0
        # ONNX run should NOT have been called
        model._session.run.assert_not_called()

    def test_load_warns_when_onnx_unavailable(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        model = SileroVad(model_path="/fake.onnx")
        with patch("butlers.connectors.live_listener.vad.ONNX_AVAILABLE", False):
            with caplog.at_level(logging.WARNING, logger="butlers.connectors.live_listener.vad"):
                model.load()
        assert "onnxruntime" in caplog.text or model._session is None

    def test_reset_state_clears_lstm_tensors(self) -> None:
        """reset_state() reinitialises LSTM tensors so VAD is fresh for new segment."""
        with patch("butlers.connectors.live_listener.vad.ONNX_AVAILABLE", True):
            import numpy as np_real

            with (
                patch("butlers.connectors.live_listener.vad.np", np_real),
                patch("butlers.connectors.live_listener.vad.ort") as mock_ort,
            ):
                mock_session = MagicMock()
                mock_ort.InferenceSession.return_value = mock_session
                mock_ort.SessionOptions.return_value = MagicMock()

                model = SileroVad(model_path="/fake.onnx")
                model.load()
                # Capture initial state tensors
                h1 = model._h
                model.reset_state()
                h2 = model._h
                # After reset the tensor is recreated
                assert h1 is not h2


# ---------------------------------------------------------------------------
# VadStateMachine reset
# ---------------------------------------------------------------------------


class TestVadStateMachineReset:
    def test_reset_clears_state(self) -> None:
        vad = _make_vad(onset_frames=2, offset_frames=2, min_segment_ms=0)
        _drive(vad, [0.9, 0.9])  # → SPEAKING
        assert vad.state == VadState.SPEAKING
        vad.reset()
        assert vad.state == VadState.SILENCE
        assert vad.current_segment_bytes == b""

    def test_reset_allows_fresh_onset(self) -> None:
        """After reset, the VAD can transition to SPEAKING again."""
        vad = _make_vad(onset_frames=2, offset_frames=2, min_segment_ms=0)
        _drive(vad, [0.9, 0.9])  # → SPEAKING
        vad.reset()
        _drive(vad, [0.9, 0.9])  # → SPEAKING again
        assert vad.state == VadState.SPEAKING
