"""Live-listener voice connector.

Captures ambient audio from local microphones, segments speech using VAD,
streams segments to a faster-whisper transcription service, applies LLM-based
discretion filtering, and submits actionable utterances to the Switchboard as
``ingest.v1`` envelopes.

Submodules
----------
audio
    MicPipeline — ring buffer, sounddevice stream, device management.
vad
    VAD state machine, segment bounds, Silero ONNX wrapper.
metrics
    Voice-specific Prometheus counters and histograms.
config
    Environment variable configuration.
transcription
    Protocol-agnostic transcription client (Wyoming/WebSocket/HTTP).
envelope
    ingest.v1 envelope builder (field mapping, event-ID minting, idempotency keys).
session
    Conversation session manager (gap-based session grouping).
checkpoint
    Checkpoint persistence via cursor_store (per-mic timestamp + session state).
filter_gate
    Source filter gate (IngestionPolicyEvaluator wiring for voice/mic_id).

Env vars:
    LIVE_LISTENER_TRANSCRIPTION_URL: Transcription service URL (required).
        For Wyoming: tcp://host:10300. For WS: ws://host:port/path. For HTTP: http://host:port.
    LIVE_LISTENER_TRANSCRIPTION_PROTOCOL: "wyoming" | "websocket" | "http" (default: "wyoming")
    LIVE_LISTENER_LANGUAGE: Language hint for transcription (default: "en")
    LIVE_LISTENER_MIN_CONFIDENCE: Minimum confidence threshold (default: 0.3)
"""

from butlers.connectors.live_listener.audio import MicPipeline
from butlers.connectors.live_listener.checkpoint import (
    VoiceCheckpoint,
    load_voice_checkpoint,
    save_voice_checkpoint,
)
from butlers.connectors.live_listener.config import LiveListenerConfig
from butlers.connectors.live_listener.connector import LiveListenerConnector
from butlers.connectors.live_listener.envelope import (
    build_voice_envelope,
    endpoint_identity,
    mint_event_id,
    mint_idempotency_key,
    unix_ms_from_datetime,
    unix_ms_now,
)
from butlers.connectors.live_listener.filter_gate import (
    build_filter_scope,
    create_filter_evaluator,
    evaluate_voice_filter,
    extract_mic_key,
)
from butlers.connectors.live_listener.metrics import LiveListenerMetrics
from butlers.connectors.live_listener.session import ConversationSession
from butlers.connectors.live_listener.transcription import (
    HttpTranscriptionClient,
    TranscriptionClient,
    TranscriptionProtocol,
    TranscriptionResult,
    WebSocketTranscriptionClient,
    WyomingTranscriptionClient,
    create_transcription_client,
)
from butlers.connectors.live_listener.vad import VadStateMachine

__all__ = [
    # connector entrypoint
    "LiveListenerConnector",
    # audio/vad/config/metrics (original)
    "MicPipeline",
    "VadStateMachine",
    "LiveListenerConfig",
    "LiveListenerMetrics",
    # transcription
    "TranscriptionClient",
    "TranscriptionResult",
    "TranscriptionProtocol",
    "WyomingTranscriptionClient",
    "WebSocketTranscriptionClient",
    "HttpTranscriptionClient",
    "create_transcription_client",
    # envelope
    "build_voice_envelope",
    "endpoint_identity",
    "mint_event_id",
    "mint_idempotency_key",
    "unix_ms_from_datetime",
    "unix_ms_now",
    # session
    "ConversationSession",
    # checkpoint
    "VoiceCheckpoint",
    "load_voice_checkpoint",
    "save_voice_checkpoint",
    # filter_gate
    "build_filter_scope",
    "create_filter_evaluator",
    "evaluate_voice_filter",
    "extract_mic_key",
]
