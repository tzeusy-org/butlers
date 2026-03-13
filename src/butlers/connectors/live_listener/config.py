"""Configuration for the live-listener connector.

All settings are loaded from environment variables. See the spec for full details:
openspec/changes/connector-live-listener/specs/connector-live-listener/spec.md
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class MicDeviceSpec:
    """Specification for a single microphone device.

    Parsed from LIVE_LISTENER_DEVICES JSON list entries:
    ``[{"name": "kitchen", "device": "<portaudio_device_name_or_index>"}]``
    """

    name: str
    device: str | int  # PortAudio device name (str) or device index (int)

    @classmethod
    def from_dict(cls, d: dict) -> MicDeviceSpec:
        name = d.get("name", "")
        if not name:
            raise ValueError(f"Mic device spec missing 'name': {d!r}")
        device_raw = d.get("device")
        if device_raw is None:
            raise ValueError(f"Mic device spec missing 'device': {d!r}")
        # Accept integer index as string or int
        if isinstance(device_raw, int):
            device: str | int = device_raw
        else:
            device_str = str(device_raw).strip()
            try:
                device = int(device_str)
            except ValueError:
                device = device_str
        return cls(name=name, device=device)


@dataclass
class LiveListenerConfig:
    """Full configuration for the live-listener connector.

    Loaded from environment variables via ``from_env()``.
    """

    # --- Required ---
    switchboard_mcp_url: str
    devices: list[MicDeviceSpec]
    transcription_url: str

    # --- Connector identity ---
    provider: str = "live-listener"
    channel: str = "voice"

    # --- Audio / VAD thresholds ---
    vad_onset_threshold: float = 0.5
    vad_offset_threshold: float = 0.3
    vad_onset_frames: int = 3
    vad_offset_frames: int = 10
    min_segment_ms: int = 300
    max_segment_ms: int = 30_000

    # --- Transcription ---
    transcription_protocol: str = "wyoming"
    language: str = "en"
    min_confidence: float = 0.3

    # --- Discretion ---
    discretion_llm_url: str = ""
    discretion_llm_model: str = ""
    discretion_timeout_s: float = 3.0
    discretion_window_size: int = 10
    discretion_window_seconds: int = 300

    # --- Session ---
    session_gap_s: int = 120

    # --- VAD model ---
    vad_model_path: str = ""

    # --- Reconnection backoff ---
    reconnect_base_s: float = 1.0
    reconnect_max_s: float = 60.0

    # --- Ring buffer ---
    ring_buffer_seconds: float = 10.0  # seconds of audio to keep in ring buffer

    @classmethod
    def from_env(cls) -> LiveListenerConfig:
        """Load configuration from environment variables.

        Required:
            SWITCHBOARD_MCP_URL
            LIVE_LISTENER_DEVICES   JSON list of device specs
            LIVE_LISTENER_TRANSCRIPTION_URL

        Optional:
            CONNECTOR_PROVIDER          (default: live-listener)
            CONNECTOR_CHANNEL           (default: voice)
            LIVE_LISTENER_VAD_ONSET_THRESHOLD
            LIVE_LISTENER_VAD_OFFSET_THRESHOLD
            LIVE_LISTENER_VAD_ONSET_FRAMES
            LIVE_LISTENER_VAD_OFFSET_FRAMES
            LIVE_LISTENER_MIN_SEGMENT_MS
            LIVE_LISTENER_MAX_SEGMENT_MS
            LIVE_LISTENER_TRANSCRIPTION_PROTOCOL
            LIVE_LISTENER_LANGUAGE
            LIVE_LISTENER_MIN_CONFIDENCE
            LIVE_LISTENER_DISCRETION_LLM_URL
            LIVE_LISTENER_DISCRETION_LLM_MODEL
            LIVE_LISTENER_DISCRETION_TIMEOUT_S
            LIVE_LISTENER_DISCRETION_WINDOW_SIZE
            LIVE_LISTENER_DISCRETION_WINDOW_SECONDS
            LIVE_LISTENER_SESSION_GAP_S
        """
        switchboard_mcp_url = os.environ.get("SWITCHBOARD_MCP_URL", "").strip()
        if not switchboard_mcp_url:
            raise ValueError("SWITCHBOARD_MCP_URL environment variable is required")

        devices_json = os.environ.get("LIVE_LISTENER_DEVICES", "").strip()
        if not devices_json:
            raise ValueError("LIVE_LISTENER_DEVICES environment variable is required")
        try:
            raw_devices = json.loads(devices_json)
        except json.JSONDecodeError as exc:
            raise ValueError(f"LIVE_LISTENER_DEVICES must be valid JSON: {exc}") from exc
        if not isinstance(raw_devices, list):
            raise ValueError("LIVE_LISTENER_DEVICES must be a JSON array")
        devices = [MicDeviceSpec.from_dict(d) for d in raw_devices]

        transcription_url = os.environ.get("LIVE_LISTENER_TRANSCRIPTION_URL", "").strip()
        if not transcription_url:
            raise ValueError("LIVE_LISTENER_TRANSCRIPTION_URL environment variable is required")

        provider = os.environ.get("CONNECTOR_PROVIDER", "live-listener")
        channel = os.environ.get("CONNECTOR_CHANNEL", "voice")

        def _float(key: str, default: float) -> float:
            v = os.environ.get(key, "").strip()
            return float(v) if v else default

        def _int(key: str, default: int) -> int:
            v = os.environ.get(key, "").strip()
            return int(v) if v else default

        vad_model_path = os.environ.get("LIVE_LISTENER_VAD_MODEL_PATH", "").strip()
        if not vad_model_path:
            vad_model_path = _find_silero_vad_model()

        return cls(
            switchboard_mcp_url=switchboard_mcp_url,
            devices=devices,
            transcription_url=transcription_url,
            provider=provider,
            channel=channel,
            vad_model_path=vad_model_path,
            vad_onset_threshold=_float("LIVE_LISTENER_VAD_ONSET_THRESHOLD", 0.5),
            vad_offset_threshold=_float("LIVE_LISTENER_VAD_OFFSET_THRESHOLD", 0.3),
            vad_onset_frames=_int("LIVE_LISTENER_VAD_ONSET_FRAMES", 3),
            vad_offset_frames=_int("LIVE_LISTENER_VAD_OFFSET_FRAMES", 10),
            min_segment_ms=_int("LIVE_LISTENER_MIN_SEGMENT_MS", 300),
            max_segment_ms=_int("LIVE_LISTENER_MAX_SEGMENT_MS", 30_000),
            transcription_protocol=os.environ.get(
                "LIVE_LISTENER_TRANSCRIPTION_PROTOCOL", "wyoming"
            ),
            language=os.environ.get("LIVE_LISTENER_LANGUAGE", "en"),
            min_confidence=_float("LIVE_LISTENER_MIN_CONFIDENCE", 0.3),
            discretion_llm_url=os.environ.get("LIVE_LISTENER_DISCRETION_LLM_URL", ""),
            discretion_llm_model=os.environ.get("LIVE_LISTENER_DISCRETION_LLM_MODEL", ""),
            discretion_timeout_s=_float("LIVE_LISTENER_DISCRETION_TIMEOUT_S", 3.0),
            discretion_window_size=_int("LIVE_LISTENER_DISCRETION_WINDOW_SIZE", 10),
            discretion_window_seconds=_int("LIVE_LISTENER_DISCRETION_WINDOW_SECONDS", 300),
            session_gap_s=_int("LIVE_LISTENER_SESSION_GAP_S", 120),
        )

    def endpoint_identity_for_mic(self, mic_name: str) -> str:
        """Build the endpoint identity string for a given mic name."""
        return f"live-listener:mic:{mic_name}"


def _find_silero_vad_model() -> str:
    """Search common locations for the Silero VAD ONNX model."""
    candidates = [
        Path.home() / ".cache/torch/hub/snakers4_silero-vad_master/files/silero_vad.onnx",
    ]
    for p in candidates:
        if p.is_file():
            return str(p)
    return ""
