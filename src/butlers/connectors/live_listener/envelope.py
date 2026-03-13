"""ingest.v1 envelope builder for the live-listener voice connector.

Implements the field mapping from the connector-live-listener spec:

    source.channel          = "voice"
    source.provider         = "live-listener"
    source.endpoint_identity = "live-listener:mic:{device_name}"
    event.external_event_id  = "utt:{device_name}:{unix_ms}"
    event.external_thread_id = conversation session ID
    event.observed_at        = speech segment offset timestamp (ISO-8601)
    sender.identity          = "ambient"
    payload.raw              = {"transcript": ..., "confidence": ..., ...}
    payload.normalized_text  = transcribed text
    control.idempotency_key  = "voice:{endpoint_identity}:{unix_ms}:{content_hash[:8]}"
    control.policy_tier      = "default"
    control.ingestion_tier   = "full"
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any


def endpoint_identity(device_name: str) -> str:
    """Return the canonical endpoint identity for a mic device.

    Format: ``"live-listener:mic:{device_name}"``

    Args:
        device_name: The device name as configured in LIVE_LISTENER_DEVICES.
    """
    return f"live-listener:mic:{device_name}"


def mint_event_id(device_name: str, unix_ms: int) -> str:
    """Mint a synthetic event ID for a voice utterance.

    Format: ``"utt:{device_name}:{unix_ms}"``

    The combination of device_name and unix_ms is unique across mics and
    monotonically increasing per mic.

    Args:
        device_name: The device name as configured in LIVE_LISTENER_DEVICES.
        unix_ms: Millisecond-precision UNIX timestamp of speech segment offset.
    """
    return f"utt:{device_name}:{unix_ms}"


def mint_idempotency_key(endpoint_id: str, unix_ms: int, transcript: str) -> str:
    """Construct the idempotency key for a voice utterance.

    Format: ``"voice:{endpoint_identity}:{unix_ms}:{content_hash[:8]}"``

    Combines timestamp and content hash for safety against clock skew.

    Args:
        endpoint_id: The endpoint identity (``"live-listener:mic:{device_name}"``).
        unix_ms: Millisecond-precision UNIX timestamp of speech segment offset.
        transcript: The transcribed text (used for content hashing).
    """
    content_hash = hashlib.sha256(transcript.encode()).hexdigest()
    return f"voice:{endpoint_id}:{unix_ms}:{content_hash[:8]}"


def build_voice_envelope(
    *,
    device_name: str,
    unix_ms: int,
    session_id: str | None,
    observed_at: datetime,
    transcript: str,
    confidence: float,
    duration_s: float,
    language: str,
    discretion_reason: str,
) -> dict[str, Any]:
    """Build a complete ``ingest.v1`` envelope for a forwarded voice utterance.

    Args:
        device_name: The device name from LIVE_LISTENER_DEVICES config.
        unix_ms: Millisecond-precision UNIX timestamp of speech segment offset.
        session_id: Conversation session ID (``external_thread_id``); None if
            no active session (the caller is expected to always provide one
            for forwarded utterances — None is accepted for robustness).
        observed_at: Datetime of speech segment offset (when utterance ended).
        transcript: Transcribed text of the utterance.
        confidence: Transcription confidence score (0–1).
        duration_s: Duration of the speech segment in seconds.
        language: Detected or configured language code (e.g. ``"en"``).
        discretion_reason: One-line reason from the discretion LLM (why FORWARD).

    Returns:
        A dict shaped as an ``ingest.v1`` envelope, ready for Switchboard
        submission.
    """
    ep_id = endpoint_identity(device_name)
    event_id = mint_event_id(device_name, unix_ms)
    idempotency_key = mint_idempotency_key(ep_id, unix_ms, transcript)

    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "voice",
            "provider": "live-listener",
            "endpoint_identity": ep_id,
        },
        "event": {
            "external_event_id": event_id,
            "external_thread_id": session_id,
            "observed_at": observed_at.astimezone(UTC).isoformat(),
        },
        "sender": {
            "identity": "ambient",
        },
        "payload": {
            "raw": {
                "transcript": transcript,
                "confidence": confidence,
                "duration_s": duration_s,
                "mic": device_name,
                "language": language,
                "discretion_reason": discretion_reason,
            },
            "normalized_text": transcript,
        },
        "control": {
            "idempotency_key": idempotency_key,
            "policy_tier": "default",
            "ingestion_tier": "full",
        },
    }


def unix_ms_now() -> int:
    """Return the current time as milliseconds since UNIX epoch."""
    return int(datetime.now(UTC).timestamp() * 1000)


def unix_ms_from_datetime(dt: datetime) -> int:
    """Convert a datetime to milliseconds since UNIX epoch.

    Args:
        dt: The datetime to convert. Naive datetimes are assumed to be UTC.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)
