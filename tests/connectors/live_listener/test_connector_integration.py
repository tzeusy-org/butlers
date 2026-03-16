"""Integration test: live-listener full pipeline.

Exercises the complete pipeline:
  mock audio → VAD → mock transcription → filter_gate → discretion → ingest

All external services (transcription, discretion LLM, Switchboard MCP) are
mocked so no network connections are required.

Spec references:
  openspec/changes/connector-live-listener/specs/connector-live-listener/spec.md
  § Connector Identity and Role, § Health State Derivation, §8.1–8.6, §9.1–9.3
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.connectors.discretion import DiscretionResult
from butlers.connectors.live_listener.checkpoint import VoiceCheckpoint
from butlers.connectors.live_listener.config import LiveListenerConfig, MicDeviceSpec
from butlers.connectors.live_listener.connector import LiveListenerConnector, MicPipelineState
from butlers.connectors.live_listener.metrics import LiveListenerMetrics
from butlers.connectors.live_listener.session import ConversationSession
from butlers.connectors.live_listener.transcription import TranscriptionResult
from butlers.connectors.live_listener.vad import FRAME_BYTES, SpeechSegment

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**kwargs: Any) -> LiveListenerConfig:
    """Build a LiveListenerConfig with minimal required fields."""
    base: dict[str, Any] = dict(
        switchboard_mcp_url="http://localhost:41100/sse",
        devices=[MicDeviceSpec(name="kitchen", device="hw:0")],
        transcription_url="tcp://localhost:10300",
        reconnect_base_s=0.01,
        reconnect_max_s=0.1,
        ring_buffer_seconds=1.0,
    )
    base.update(kwargs)
    return LiveListenerConfig(**base)


def _make_speech_segment(mic_name: str = "kitchen", text_hint: str = "hello") -> SpeechSegment:
    """Create a realistic 300 ms speech segment."""
    # 300 ms of silence PCM (10 frames × 30 ms @ 16 kHz, 16-bit, mono)
    frames = 10
    audio = b"\x00\x01" * (FRAME_BYTES // 2) * frames
    return SpeechSegment(
        audio_bytes=audio,
        mic_name=mic_name,
        onset_frame_index=0,
        offset_ts=time.monotonic(),
        duration_ms=frames * 30,
        forced_split=False,
    )


def _make_connector(
    config: LiveListenerConfig | None = None,
    mcp_client: Any | None = None,
) -> LiveListenerConnector:
    """Build a LiveListenerConnector with a mock MCP client."""
    cfg = config or _make_config()
    mock_mcp = mcp_client or AsyncMock()
    mock_mcp.call_tool = AsyncMock(return_value={"status": "accepted"})
    connector = LiveListenerConnector(config=cfg, mcp_client=mock_mcp)
    return connector


# ---------------------------------------------------------------------------
# Health state derivation
# ---------------------------------------------------------------------------


class TestHealthStateDerivation:
    """Spec § Health State Derivation."""

    def test_no_devices_returns_error(self) -> None:
        cfg = _make_config(devices=[])
        connector = _make_connector(config=cfg)
        state, msg = connector.get_health_state()
        assert state == "error"
        assert "no microphone" in msg.lower()

    def test_all_mics_disconnected_returns_error(self) -> None:
        connector = _make_connector()
        # No mic is connected
        for s in connector._mic_states.values():
            s.connected = False
        state, msg = connector.get_health_state()
        assert state == "error"
        assert msg is not None

    def test_any_mic_disconnected_returns_degraded(self) -> None:
        cfg = _make_config(
            devices=[
                MicDeviceSpec(name="kitchen", device="hw:0"),
                MicDeviceSpec(name="bedroom", device="hw:1"),
            ]
        )
        connector = _make_connector(config=cfg)
        # kitchen connected, bedroom not
        connector._mic_states["kitchen"].connected = True
        connector._mic_states["bedroom"].connected = False
        state, msg = connector.get_health_state()
        assert state == "degraded"
        assert "bedroom" in (msg or "")

    def test_transcription_unhealthy_returns_degraded(self) -> None:
        connector = _make_connector()
        connector._mic_states["kitchen"].connected = True
        connector._mic_states["kitchen"].transcription_healthy = False
        state, msg = connector.get_health_state()
        assert state == "degraded"
        assert "kitchen" in (msg or "")

    def test_discretion_unhealthy_returns_degraded(self) -> None:
        connector = _make_connector()
        connector._mic_states["kitchen"].connected = True
        connector._mic_states["kitchen"].discretion_healthy = False
        state, msg = connector.get_health_state()
        assert state == "degraded"
        assert "kitchen" in (msg or "")

    def test_all_healthy(self) -> None:
        connector = _make_connector()
        connector._mic_states["kitchen"].connected = True
        connector._mic_states["kitchen"].transcription_healthy = True
        connector._mic_states["kitchen"].discretion_healthy = True
        state, msg = connector.get_health_state()
        assert state == "healthy"
        assert "kitchen" in (msg or "")

    def test_healthy_message_includes_all_mics(self) -> None:
        cfg = _make_config(
            devices=[
                MicDeviceSpec(name="kitchen", device="hw:0"),
                MicDeviceSpec(name="bedroom", device="hw:1"),
            ]
        )
        connector = _make_connector(config=cfg)
        for mic, s in connector._mic_states.items():
            s.connected = True
            s.transcription_healthy = True
            s.discretion_healthy = True
        state, msg = connector.get_health_state()
        assert state == "healthy"
        assert "kitchen" in (msg or "")
        assert "bedroom" in (msg or "")


# ---------------------------------------------------------------------------
# Segment processing pipeline
# ---------------------------------------------------------------------------


class TestProcessSegmentPipeline:
    """Integration test: full pipeline from speech segment to ingest submission."""

    @pytest.fixture
    def mock_mcp(self) -> AsyncMock:
        m = AsyncMock()
        m.call_tool = AsyncMock(return_value={"status": "accepted"})
        return m

    @pytest.fixture
    def connector(self, mock_mcp: AsyncMock) -> LiveListenerConnector:
        cfg = _make_config()
        c = LiveListenerConnector(config=cfg, mcp_client=mock_mcp)
        # Pre-populate pipeline components (normally done in start())

        mic = "kitchen"
        c._sessions[mic] = ConversationSession(device_name=mic)
        c._ll_metrics[mic] = LiveListenerMetrics(mic=mic)
        c._mic_states[mic] = MicPipelineState(mic)
        c._mic_states[mic].connected = True
        return c

    async def test_happy_path_submits_ingest(
        self,
        connector: LiveListenerConnector,
        mock_mcp: AsyncMock,
    ) -> None:
        """A valid transcribed + forwarded utterance reaches ingest."""
        spec = MicDeviceSpec(name="kitchen", device="hw:0")
        segment = _make_speech_segment()

        # Mock transcription client
        mock_tx = AsyncMock()
        mock_tx.healthy = True
        mock_tx.transcribe = AsyncMock(
            return_value=TranscriptionResult(
                text="what is the weather",
                confidence=0.9,
                language="en",
                duration_s=1.0,
            )
        )
        connector._transcription_clients["kitchen"] = mock_tx

        # Mock discretion evaluator (FORWARD)
        mock_disc = AsyncMock()
        mock_disc.evaluate = AsyncMock(
            return_value=DiscretionResult(
                verdict="FORWARD", reason="direct question", is_fail_open=False
            )
        )
        connector._discretion_evaluators["kitchen"] = mock_disc

        # Mock filter gate (allow)
        mock_filter = MagicMock()
        mock_filter.allowed = True

        with patch(
            "butlers.connectors.live_listener.connector.evaluate_voice_filter",
            return_value=mock_filter,
        ):
            await connector._process_segment(spec, segment, MagicMock())

        mock_mcp.call_tool.assert_awaited_once()
        call_args = mock_mcp.call_tool.call_args
        assert call_args[0][0] == "ingest"
        envelope = call_args[0][1]
        assert envelope["schema_version"] == "ingest.v1"
        assert envelope["source"]["channel"] == "voice"
        assert envelope["source"]["provider"] == "live-listener"
        assert envelope["source"]["endpoint_identity"] == "live-listener:mic:kitchen"
        assert envelope["payload"]["normalized_text"] == "what is the weather"
        assert envelope["payload"]["raw"]["discretion_reason"] == "direct question"

    async def test_ignored_utterance_not_submitted(
        self,
        connector: LiveListenerConnector,
        mock_mcp: AsyncMock,
    ) -> None:
        """IGNORE verdict prevents ingest submission."""
        spec = MicDeviceSpec(name="kitchen", device="hw:0")
        segment = _make_speech_segment()

        mock_tx = AsyncMock()
        mock_tx.healthy = True
        mock_tx.transcribe = AsyncMock(
            return_value=TranscriptionResult(
                text="background noise transcription",
                confidence=0.8,
                language="en",
                duration_s=0.5,
            )
        )
        connector._transcription_clients["kitchen"] = mock_tx

        mock_disc = AsyncMock()
        mock_disc.evaluate = AsyncMock(
            return_value=DiscretionResult(verdict="IGNORE", reason="", is_fail_open=False)
        )
        connector._discretion_evaluators["kitchen"] = mock_disc

        mock_filter = MagicMock()
        mock_filter.allowed = True

        with patch(
            "butlers.connectors.live_listener.connector.evaluate_voice_filter",
            return_value=mock_filter,
        ):
            await connector._process_segment(spec, segment, MagicMock())

        mock_mcp.call_tool.assert_not_awaited()

    async def test_transcription_none_not_submitted(
        self,
        connector: LiveListenerConnector,
        mock_mcp: AsyncMock,
    ) -> None:
        """None from transcription client (empty/low-confidence) drops segment."""
        spec = MicDeviceSpec(name="kitchen", device="hw:0")
        segment = _make_speech_segment()

        mock_tx = AsyncMock()
        mock_tx.healthy = True
        mock_tx.transcribe = AsyncMock(return_value=None)
        connector._transcription_clients["kitchen"] = mock_tx

        with patch(
            "butlers.connectors.live_listener.connector.evaluate_voice_filter",
            return_value=MagicMock(allowed=True),
        ):
            await connector._process_segment(spec, segment, MagicMock())

        mock_mcp.call_tool.assert_not_awaited()

    async def test_filter_gate_blocked_not_submitted(
        self,
        connector: LiveListenerConnector,
        mock_mcp: AsyncMock,
    ) -> None:
        """A blocked filter gate prevents discretion + ingest."""
        spec = MicDeviceSpec(name="kitchen", device="hw:0")
        segment = _make_speech_segment()

        mock_tx = AsyncMock()
        mock_tx.healthy = True
        mock_tx.transcribe = AsyncMock(
            return_value=TranscriptionResult(
                text="some utterance",
                confidence=0.9,
                language="en",
                duration_s=0.5,
            )
        )
        connector._transcription_clients["kitchen"] = mock_tx

        mock_disc = AsyncMock()
        mock_disc.evaluate = AsyncMock(
            return_value=DiscretionResult(verdict="FORWARD", reason="ok", is_fail_open=False)
        )
        connector._discretion_evaluators["kitchen"] = mock_disc

        with patch(
            "butlers.connectors.live_listener.connector.evaluate_voice_filter",
            return_value=MagicMock(allowed=False),
        ):
            await connector._process_segment(spec, segment, MagicMock())

        mock_mcp.call_tool.assert_not_awaited()
        # Discretion should not be called for blocked utterances
        mock_disc.evaluate.assert_not_awaited()

    async def test_transcription_error_marks_degraded(
        self,
        connector: LiveListenerConnector,
        mock_mcp: AsyncMock,
    ) -> None:
        """Transcription exception marks transcription_healthy=False."""
        spec = MicDeviceSpec(name="kitchen", device="hw:0")
        segment = _make_speech_segment()

        mock_tx = AsyncMock()
        mock_tx.healthy = False
        mock_tx.transcribe = AsyncMock(side_effect=ConnectionError("service down"))
        connector._transcription_clients["kitchen"] = mock_tx

        with patch(
            "butlers.connectors.live_listener.connector.evaluate_voice_filter",
            return_value=MagicMock(allowed=True),
        ):
            await connector._process_segment(spec, segment, MagicMock())

        assert connector._mic_states["kitchen"].transcription_healthy is False
        mock_mcp.call_tool.assert_not_awaited()

    async def test_discretion_failopen_still_submits(
        self,
        connector: LiveListenerConnector,
        mock_mcp: AsyncMock,
    ) -> None:
        """Discretion error is fail-open: utterance still forwarded."""
        spec = MicDeviceSpec(name="kitchen", device="hw:0")
        segment = _make_speech_segment()

        mock_tx = AsyncMock()
        mock_tx.healthy = True
        mock_tx.transcribe = AsyncMock(
            return_value=TranscriptionResult(
                text="hello butler",
                confidence=0.9,
                language="en",
                duration_s=1.0,
            )
        )
        connector._transcription_clients["kitchen"] = mock_tx

        mock_disc = AsyncMock()
        mock_disc.evaluate = AsyncMock(side_effect=Exception("LLM timeout"))
        connector._discretion_evaluators["kitchen"] = mock_disc

        with patch(
            "butlers.connectors.live_listener.connector.evaluate_voice_filter",
            return_value=MagicMock(allowed=True),
        ):
            await connector._process_segment(spec, segment, MagicMock())

        # Fail-open: should still submit
        mock_mcp.call_tool.assert_awaited_once()
        call_args = mock_mcp.call_tool.call_args[0]
        envelope = call_args[1]
        assert "fail-open" in envelope["payload"]["raw"]["discretion_reason"]

    async def test_duplicate_ingest_response_treated_as_success(
        self,
        connector: LiveListenerConnector,
        mock_mcp: AsyncMock,
    ) -> None:
        """Duplicate response from Switchboard is treated as success, not error."""
        spec = MicDeviceSpec(name="kitchen", device="hw:0")
        segment = _make_speech_segment()

        mock_tx = AsyncMock()
        mock_tx.healthy = True
        mock_tx.transcribe = AsyncMock(
            return_value=TranscriptionResult(
                text="hello", confidence=0.9, language="en", duration_s=0.3
            )
        )
        connector._transcription_clients["kitchen"] = mock_tx

        mock_disc = AsyncMock()
        mock_disc.evaluate = AsyncMock(
            return_value=DiscretionResult(verdict="FORWARD", reason="ok", is_fail_open=False)
        )
        connector._discretion_evaluators["kitchen"] = mock_disc

        # Return "duplicate" from Switchboard
        mock_mcp.call_tool = AsyncMock(return_value={"status": "duplicate"})

        with patch(
            "butlers.connectors.live_listener.connector.evaluate_voice_filter",
            return_value=MagicMock(allowed=True),
        ):
            await connector._process_segment(spec, segment, MagicMock())

        # Should complete without error; duplicate is accepted
        mock_mcp.call_tool.assert_awaited_once()

    async def test_ingest_error_does_not_crash_pipeline(
        self,
        connector: LiveListenerConnector,
        mock_mcp: AsyncMock,
    ) -> None:
        """MCP submission failure is logged and swallowed, pipeline continues."""
        spec = MicDeviceSpec(name="kitchen", device="hw:0")
        segment = _make_speech_segment()

        mock_tx = AsyncMock()
        mock_tx.healthy = True
        mock_tx.transcribe = AsyncMock(
            return_value=TranscriptionResult(
                text="hello", confidence=0.9, language="en", duration_s=0.3
            )
        )
        connector._transcription_clients["kitchen"] = mock_tx

        mock_disc = AsyncMock()
        mock_disc.evaluate = AsyncMock(
            return_value=DiscretionResult(verdict="FORWARD", reason="ok", is_fail_open=False)
        )
        connector._discretion_evaluators["kitchen"] = mock_disc

        mock_mcp.call_tool = AsyncMock(side_effect=ConnectionError("switchboard down"))

        with patch(
            "butlers.connectors.live_listener.connector.evaluate_voice_filter",
            return_value=MagicMock(allowed=True),
        ):
            # Should not raise
            await connector._process_segment(spec, segment, MagicMock())


# ---------------------------------------------------------------------------
# Envelope field mapping
# ---------------------------------------------------------------------------


class TestEnvelopeFieldMapping:
    """Verify ingest.v1 envelope fields per spec § ingest.v1 Field Mapping."""

    async def test_envelope_fields_correct(self) -> None:
        cfg = _make_config()
        mock_mcp = AsyncMock()
        captured_envelopes: list[dict] = []

        async def capture_call(tool: str, args: dict) -> dict:
            captured_envelopes.append(args)
            return {"status": "accepted"}

        mock_mcp.call_tool = capture_call
        connector = LiveListenerConnector(config=cfg, mcp_client=mock_mcp)

        mic = "kitchen"
        spec = MicDeviceSpec(name=mic, device="hw:0")
        connector._sessions[mic] = ConversationSession(device_name=mic)
        connector._ll_metrics[mic] = LiveListenerMetrics(mic=mic)
        connector._mic_states[mic] = MicPipelineState(mic)
        connector._mic_states[mic].connected = True

        mock_tx = AsyncMock()
        mock_tx.healthy = True
        mock_tx.transcribe = AsyncMock(
            return_value=TranscriptionResult(
                text="turn on the lights",
                confidence=0.95,
                language="en",
                duration_s=1.2,
            )
        )
        connector._transcription_clients[mic] = mock_tx

        mock_disc = AsyncMock()
        mock_disc.evaluate = AsyncMock(
            return_value=DiscretionResult(verdict="FORWARD", reason="command", is_fail_open=False)
        )
        connector._discretion_evaluators[mic] = mock_disc

        segment = _make_speech_segment(mic_name=mic)

        with patch(
            "butlers.connectors.live_listener.connector.evaluate_voice_filter",
            return_value=MagicMock(allowed=True),
        ):
            await connector._process_segment(spec, segment, MagicMock())

        assert len(captured_envelopes) == 1
        env = captured_envelopes[0]

        # Source fields
        assert env["schema_version"] == "ingest.v1"
        assert env["source"]["channel"] == "voice"
        assert env["source"]["provider"] == "live-listener"
        assert env["source"]["endpoint_identity"] == "live-listener:mic:kitchen"

        # Event fields
        assert env["event"]["external_event_id"].startswith("utt:kitchen:")
        assert env["event"]["external_thread_id"] is not None

        # Sender
        assert env["sender"]["identity"] == "ambient"

        # Payload
        assert env["payload"]["normalized_text"] == "turn on the lights"
        raw = env["payload"]["raw"]
        assert raw["transcript"] == "turn on the lights"
        assert raw["confidence"] == 0.95
        assert raw["mic"] == "kitchen"
        assert raw["language"] == "en"
        assert raw["discretion_reason"] == "command"

        # Control
        assert env["control"]["idempotency_key"].startswith("voice:live-listener:mic:kitchen:")
        assert env["control"]["policy_tier"] == "interactive"
        assert env["control"]["ingestion_tier"] == "full"


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------


class TestSessionManagement:
    """Verify conversation session grouping via ConversationSession."""

    def test_first_utterance_creates_session(self) -> None:
        session = ConversationSession(device_name="kitchen")
        sid = session.get_or_create_session(1_000_000)
        assert sid.startswith("voice:kitchen:")
        assert "1000000" in sid

    def test_utterances_within_gap_share_session(self) -> None:
        session = ConversationSession(device_name="kitchen", session_gap_s=120)
        ts1 = 1_000_000
        ts2 = ts1 + 30_000  # 30 s gap — within 120 s threshold
        sid1 = session.get_or_create_session(ts1)
        sid2 = session.get_or_create_session(ts2)
        assert sid1 == sid2

    def test_utterances_beyond_gap_start_new_session(self) -> None:
        session = ConversationSession(device_name="kitchen", session_gap_s=120)
        ts1 = 1_000_000
        ts2 = ts1 + 200_000  # 200 s — beyond 120 s threshold
        sid1 = session.get_or_create_session(ts1)
        sid2 = session.get_or_create_session(ts2)
        assert sid1 != sid2
        assert sid2.startswith("voice:kitchen:")


# ---------------------------------------------------------------------------
# Multi-mic independence
# ---------------------------------------------------------------------------


class TestMultiMicIndependence:
    """Each mic pipeline is independent per spec."""

    def test_multi_mic_health_state_degraded_when_one_fails(self) -> None:
        cfg = _make_config(
            devices=[
                MicDeviceSpec(name="kitchen", device="hw:0"),
                MicDeviceSpec(name="bedroom", device="hw:1"),
                MicDeviceSpec(name="office", device="hw:2"),
            ]
        )
        connector = _make_connector(config=cfg)
        connector._mic_states["kitchen"].connected = True
        connector._mic_states["kitchen"].transcription_healthy = True
        connector._mic_states["kitchen"].discretion_healthy = True
        connector._mic_states["bedroom"].connected = True
        connector._mic_states["bedroom"].transcription_healthy = True
        connector._mic_states["bedroom"].discretion_healthy = True
        connector._mic_states["office"].connected = False
        connector._mic_states["office"].last_error = "device not found"

        state, msg = connector.get_health_state()
        assert state == "degraded"
        assert "office" in (msg or "")

    def test_multi_mic_all_healthy(self) -> None:
        cfg = _make_config(
            devices=[
                MicDeviceSpec(name="kitchen", device="hw:0"),
                MicDeviceSpec(name="bedroom", device="hw:1"),
            ]
        )
        connector = _make_connector(config=cfg)
        for s in connector._mic_states.values():
            s.connected = True
            s.transcription_healthy = True
            s.discretion_healthy = True

        state, _ = connector.get_health_state()
        assert state == "healthy"

    def test_multi_mic_all_disconnected_is_error(self) -> None:
        cfg = _make_config(
            devices=[
                MicDeviceSpec(name="kitchen", device="hw:0"),
                MicDeviceSpec(name="bedroom", device="hw:1"),
            ]
        )
        connector = _make_connector(config=cfg)
        for s in connector._mic_states.values():
            s.connected = False

        state, _ = connector.get_health_state()
        assert state == "error"


# ---------------------------------------------------------------------------
# Metrics instrumentation
# ---------------------------------------------------------------------------


class TestMetricsInstrumentation:
    """Verify Prometheus metrics are updated during pipeline execution."""

    async def test_segment_transcribed_metric_incremented(self) -> None:
        cfg = _make_config()
        mock_mcp = AsyncMock()
        mock_mcp.call_tool = AsyncMock(return_value={"status": "accepted"})

        connector = LiveListenerConnector(config=cfg, mcp_client=mock_mcp)
        mic = "kitchen"
        spec = MicDeviceSpec(name=mic, device="hw:0")
        connector._sessions[mic] = ConversationSession(device_name=mic)
        ll_metrics = LiveListenerMetrics(mic=mic)
        connector._ll_metrics[mic] = ll_metrics
        connector._mic_states[mic] = MicPipelineState(mic)
        connector._mic_states[mic].connected = True

        mock_tx = AsyncMock()
        mock_tx.healthy = True
        mock_tx.transcribe = AsyncMock(
            return_value=TranscriptionResult(
                text="hello", confidence=0.9, language="en", duration_s=0.5
            )
        )
        connector._transcription_clients[mic] = mock_tx

        mock_disc = AsyncMock()
        mock_disc.evaluate = AsyncMock(
            return_value=DiscretionResult(verdict="FORWARD", reason="ok", is_fail_open=False)
        )
        connector._discretion_evaluators[mic] = mock_disc

        segment = _make_speech_segment()

        with patch(
            "butlers.connectors.live_listener.connector.evaluate_voice_filter",
            return_value=MagicMock(allowed=True),
        ):
            with (
                patch.object(ll_metrics, "inc_segments") as mock_inc_segments,
                patch.object(ll_metrics, "inc_discretion") as mock_inc_discretion,
                patch.object(ll_metrics, "observe_e2e_latency") as mock_e2e,
            ):
                await connector._process_segment(spec, segment, MagicMock())

        # The ingest tool should have been called
        mock_mcp.call_tool.assert_awaited_once()
        # Metrics: segment counted as transcribed
        mock_inc_segments.assert_called_once_with("transcribed")
        # Discretion verdict recorded as forward
        mock_inc_discretion.assert_called_once_with("forward")
        # E2E latency recorded
        mock_e2e.assert_called_once()


# ---------------------------------------------------------------------------
# Checkpoint integration
# ---------------------------------------------------------------------------


class TestCheckpointIntegration:
    """Verify checkpoint save/load wiring in connector lifecycle."""

    def _make_full_connector(
        self,
        mock_mcp: AsyncMock,
        mock_pool: MagicMock,
    ) -> LiveListenerConnector:
        """Build connector with both mcp_client and db_pool set."""
        cfg = _make_config()
        c = LiveListenerConnector(config=cfg, mcp_client=mock_mcp, db_pool=mock_pool)
        return c

    def _setup_mic_pipeline(self, connector: LiveListenerConnector, mic: str = "kitchen") -> None:
        """Manually wire per-mic pipeline components (normally done in start())."""
        connector._sessions[mic] = ConversationSession(device_name=mic)
        connector._ll_metrics[mic] = LiveListenerMetrics(mic=mic)
        connector._mic_states[mic] = MicPipelineState(mic)
        connector._mic_states[mic].connected = True

    def _make_mock_tx(self, text: str = "hello world") -> AsyncMock:
        mock_tx = AsyncMock()
        mock_tx.healthy = True
        mock_tx.transcribe = AsyncMock(
            return_value=TranscriptionResult(
                text=text, confidence=0.9, language="en", duration_s=1.0
            )
        )
        return mock_tx

    def _make_mock_disc(self, verdict: str = "FORWARD") -> AsyncMock:
        mock_disc = AsyncMock()
        mock_disc.evaluate = AsyncMock(
            return_value=DiscretionResult(verdict=verdict, reason="ok", is_fail_open=False)
        )
        return mock_disc

    async def test_checkpoint_saved_after_successful_ingest(self) -> None:
        """save_voice_checkpoint is called after accepted ingest submission."""
        mock_mcp = AsyncMock()
        mock_mcp.call_tool = AsyncMock(return_value={"status": "accepted"})
        mock_pool = MagicMock()

        connector = self._make_full_connector(mock_mcp, mock_pool)
        mic = "kitchen"
        self._setup_mic_pipeline(connector, mic)
        connector._transcription_clients[mic] = self._make_mock_tx()
        connector._discretion_evaluators[mic] = self._make_mock_disc()

        spec = MicDeviceSpec(name=mic, device="hw:0")
        segment = _make_speech_segment(mic_name=mic)

        with (
            patch(
                "butlers.connectors.live_listener.connector.evaluate_voice_filter",
                return_value=MagicMock(allowed=True),
            ),
            patch(
                "butlers.connectors.live_listener.connector.save_voice_checkpoint",
                new_callable=AsyncMock,
            ) as mock_save,
        ):
            await connector._process_segment(spec, segment, MagicMock())

        mock_save.assert_awaited_once()
        call_kwargs = mock_save.call_args
        # First positional arg is pool, second is mic name
        assert call_kwargs[0][0] is mock_pool
        assert call_kwargs[0][1] == mic
        # last_utterance_ts should be an int (unix_ms)
        assert isinstance(call_kwargs[1]["last_utterance_ts"], int)

    async def test_checkpoint_saved_after_duplicate_ingest(self) -> None:
        """save_voice_checkpoint is also called when Switchboard returns 'duplicate'."""
        mock_mcp = AsyncMock()
        mock_mcp.call_tool = AsyncMock(return_value={"status": "duplicate"})
        mock_pool = MagicMock()

        connector = self._make_full_connector(mock_mcp, mock_pool)
        mic = "kitchen"
        self._setup_mic_pipeline(connector, mic)
        connector._transcription_clients[mic] = self._make_mock_tx()
        connector._discretion_evaluators[mic] = self._make_mock_disc()

        spec = MicDeviceSpec(name=mic, device="hw:0")
        segment = _make_speech_segment(mic_name=mic)

        with (
            patch(
                "butlers.connectors.live_listener.connector.evaluate_voice_filter",
                return_value=MagicMock(allowed=True),
            ),
            patch(
                "butlers.connectors.live_listener.connector.save_voice_checkpoint",
                new_callable=AsyncMock,
            ) as mock_save,
        ):
            await connector._process_segment(spec, segment, MagicMock())

        mock_save.assert_awaited_once()

    async def test_checkpoint_not_saved_when_ingest_fails(self) -> None:
        """save_voice_checkpoint is NOT called when ingest submission raises."""
        mock_mcp = AsyncMock()
        mock_mcp.call_tool = AsyncMock(side_effect=ConnectionError("switchboard down"))
        mock_pool = MagicMock()

        connector = self._make_full_connector(mock_mcp, mock_pool)
        mic = "kitchen"
        self._setup_mic_pipeline(connector, mic)
        connector._transcription_clients[mic] = self._make_mock_tx()
        connector._discretion_evaluators[mic] = self._make_mock_disc()

        spec = MicDeviceSpec(name=mic, device="hw:0")
        segment = _make_speech_segment(mic_name=mic)

        with (
            patch(
                "butlers.connectors.live_listener.connector.evaluate_voice_filter",
                return_value=MagicMock(allowed=True),
            ),
            patch(
                "butlers.connectors.live_listener.connector.save_voice_checkpoint",
                new_callable=AsyncMock,
            ) as mock_save,
        ):
            await connector._process_segment(spec, segment, MagicMock())

        mock_save.assert_not_awaited()

    async def test_checkpoint_not_saved_when_no_db_pool(self) -> None:
        """save_voice_checkpoint is NOT called when db_pool is None."""
        mock_mcp = AsyncMock()
        mock_mcp.call_tool = AsyncMock(return_value={"status": "accepted"})

        cfg = _make_config()
        connector = LiveListenerConnector(config=cfg, mcp_client=mock_mcp)  # no db_pool
        mic = "kitchen"
        self._setup_mic_pipeline(connector, mic)
        connector._transcription_clients[mic] = self._make_mock_tx()
        connector._discretion_evaluators[mic] = self._make_mock_disc()

        spec = MicDeviceSpec(name=mic, device="hw:0")
        segment = _make_speech_segment(mic_name=mic)

        with (
            patch(
                "butlers.connectors.live_listener.connector.evaluate_voice_filter",
                return_value=MagicMock(allowed=True),
            ),
            patch(
                "butlers.connectors.live_listener.connector.save_voice_checkpoint",
                new_callable=AsyncMock,
            ) as mock_save,
        ):
            await connector._process_segment(spec, segment, MagicMock())

        mock_save.assert_not_awaited()

    async def test_start_loads_checkpoint_and_restores_session(self) -> None:
        """On start(), checkpoint is loaded and session.restore() is called with its state."""
        mock_mcp = AsyncMock()
        mock_pool = MagicMock()

        ckpt = VoiceCheckpoint(
            last_utterance_ts=1_700_000_000_000,
            session_id="voice:kitchen:1700000000000",
            session_last_ts=1_700_000_000_000,
        )

        cfg = _make_config()
        connector = LiveListenerConnector(config=cfg, mcp_client=mock_mcp, db_pool=mock_pool)

        with (
            patch(
                "butlers.connectors.live_listener.connector.load_voice_checkpoint",
                new_callable=AsyncMock,
                return_value=ckpt,
            ) as mock_load,
            patch.object(connector, "_start_health_server"),
            patch.object(connector, "_start_heartbeat"),
            patch.object(connector, "_run_mic_pipeline", new_callable=AsyncMock),
            patch(
                "butlers.connectors.live_listener.connector.create_transcription_client"
            ) as mock_create_tx,
        ):
            mock_tx_instance = AsyncMock()
            mock_tx_instance.connect = AsyncMock()
            mock_create_tx.return_value = mock_tx_instance
            await connector.start()

        mock_load.assert_awaited_once_with(mock_pool, "kitchen")

        # Session state should be restored from checkpoint
        session = connector._sessions["kitchen"]
        assert session.session_id == "voice:kitchen:1700000000000"
        assert session.session_last_ts_ms == 1_700_000_000_000

    async def test_start_no_checkpoint_load_when_no_db_pool(self) -> None:
        """load_voice_checkpoint is NOT called when db_pool is None."""
        mock_mcp = AsyncMock()

        cfg = _make_config()
        connector = LiveListenerConnector(config=cfg, mcp_client=mock_mcp)  # no db_pool

        with (
            patch(
                "butlers.connectors.live_listener.connector.load_voice_checkpoint",
                new_callable=AsyncMock,
            ) as mock_load,
            patch.object(connector, "_start_health_server"),
            patch.object(connector, "_start_heartbeat"),
            patch.object(connector, "_run_mic_pipeline", new_callable=AsyncMock),
            patch(
                "butlers.connectors.live_listener.connector.create_transcription_client"
            ) as mock_create_tx,
        ):
            mock_tx_instance = AsyncMock()
            mock_tx_instance.connect = AsyncMock()
            mock_create_tx.return_value = mock_tx_instance
            await connector.start()

        mock_load.assert_not_awaited()

    async def test_checkpoint_db_error_is_fail_open(self) -> None:
        """If save_voice_checkpoint raises, the pipeline continues without corrupting metrics.

        The save is intentionally outside the ingest try/except, so a checkpoint
        DB error is caught by its own guard and must NOT be recorded as an ingest
        failure.  The ingest submission itself succeeds normally.
        """
        mock_mcp = AsyncMock()
        mock_mcp.call_tool = AsyncMock(return_value={"status": "accepted"})
        mock_pool = MagicMock()

        connector = self._make_full_connector(mock_mcp, mock_pool)
        mic = "kitchen"
        self._setup_mic_pipeline(connector, mic)
        connector._transcription_clients[mic] = self._make_mock_tx()
        connector._discretion_evaluators[mic] = self._make_mock_disc()

        spec = MicDeviceSpec(name=mic, device="hw:0")
        segment = _make_speech_segment(mic_name=mic)

        with (
            patch(
                "butlers.connectors.live_listener.connector.evaluate_voice_filter",
                return_value=MagicMock(allowed=True),
            ),
            patch(
                "butlers.connectors.live_listener.connector.save_voice_checkpoint",
                new_callable=AsyncMock,
                side_effect=Exception("DB connection error"),
            ),
            patch.object(
                connector._connector_metrics, "record_ingest_submission"
            ) as mock_record_ingest,
        ):
            # Should not raise despite save_voice_checkpoint failing
            await connector._process_segment(spec, segment, MagicMock())

        # Ingest succeeded — call_tool was awaited once
        mock_mcp.call_tool.assert_awaited_once()
        # Ingest status must be recorded as success (not "error") — checkpoint
        # failure must not pollute ingest metrics
        mock_record_ingest.assert_called_once_with("success")
