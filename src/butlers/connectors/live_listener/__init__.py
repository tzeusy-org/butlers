"""Live-listener connector: ambient audio capture, VAD segmentation, and voice ingestion.

This connector captures audio from local microphones, segments speech using Silero VAD,
streams segments to a faster-whisper transcription service, applies LLM-based discretion
filtering, and submits actionable utterances to the Switchboard as ``ingest.v1`` envelopes.

Modules:
- ``audio``: MicPipeline — ring buffer, sounddevice stream, device management
- ``vad``: VAD state machine, segment bounds, Silero ONNX wrapper
- ``metrics``: Voice-specific Prometheus counters and histograms
- ``config``: Environment variable configuration
"""

__all__ = [
    "MicPipeline",
    "VadStateMachine",
    "LiveListenerConfig",
    "LiveListenerMetrics",
]

from butlers.connectors.live_listener.audio import MicPipeline
from butlers.connectors.live_listener.config import LiveListenerConfig
from butlers.connectors.live_listener.metrics import LiveListenerMetrics
from butlers.connectors.live_listener.vad import VadStateMachine
