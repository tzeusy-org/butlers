"""Live-listener connector: ambient audio capture, VAD segmentation, and voice ingestion.

This connector captures audio from local microphones, segments speech using Silero VAD,
streams segments to a faster-whisper transcription service, applies LLM-based discretion
filtering, and submits actionable utterances to the Switchboard as ``ingest.v1`` envelopes.

Modules:
- ``audio``: MicPipeline — ring buffer, sounddevice stream, device management
- ``vad``: VAD state machine, segment bounds, Silero ONNX wrapper
- ``metrics``: Voice-specific Prometheus counters and histograms
- ``config``: Environment variable configuration
- ``transcription``: Protocol-agnostic transcription client (Wyoming/WebSocket/HTTP)

Env vars:
    LIVE_LISTENER_TRANSCRIPTION_URL: Transcription service URL (required).
        For Wyoming: tcp://host:10300. For WS: ws://host:port/path. For HTTP: http://host:port.
    LIVE_LISTENER_TRANSCRIPTION_PROTOCOL: "wyoming" | "websocket" | "http" (default: "wyoming")
    LIVE_LISTENER_LANGUAGE: Language hint for transcription (default: "en")
    LIVE_LISTENER_MIN_CONFIDENCE: Minimum confidence threshold (default: 0.3)
"""

__all__ = [
    "MicPipeline",
    "VadStateMachine",
    "LiveListenerConfig",
    "LiveListenerMetrics",
    "TranscriptionClient",
    "TranscriptionResult",
    "TranscriptionProtocol",
    "WyomingTranscriptionClient",
    "WebSocketTranscriptionClient",
    "HttpTranscriptionClient",
    "create_transcription_client",
]

from butlers.connectors.live_listener.audio import MicPipeline
from butlers.connectors.live_listener.config import LiveListenerConfig
from butlers.connectors.live_listener.metrics import LiveListenerMetrics
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
