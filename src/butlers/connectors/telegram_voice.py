"""Owner voice-note transcription helpers for the Telegram bot connector (bu-r0dsz).

Transcription here is transport: the connector turns the verified owner's own, non-forwarded
voice note into the text it already ingests (about/heart-and-soul/v1.md "Voice as an interface",
RFC 0038 D5/D18). The speech-to-text call reuses
``connectors/live_listener/transcription.py::TranscriptionClient`` unchanged; this module owns only
the pieces that client does not cover:

- the outcome taxonomy recorded in ``payload.transcription``
  (openspec/specs/telegram-voice-transcription/spec.md);
- the bounded ``ffmpeg`` transcode from the voice-note container to the client's PCM contract;
- the per-outcome counter.

Audio bytes are owner PII. Nothing here logs, labels or raises with audio bytes, transcript text,
or ffmpeg's stderr (which can echo container metadata).
"""

from __future__ import annotations

import asyncio
import logging
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from prometheus_client import Counter

logger = logging.getLogger(__name__)

#: Telegram Bot API ``getFile`` download ceiling (20 MB), enforced before and after download.
MAX_VOICE_BYTES = 20 * 1024 * 1024

#: Default ceiling on a voice note's sender-reported duration, in seconds.
DEFAULT_MAX_DURATION_S = 120

#: Descriptor ``_extract_normalized_text`` produces for a voice note with no caption.
VOICE_DESCRIPTOR = "[Voice message]"

#: ffmpeg arguments: read any container on stdin, write 16 kHz mono s16le PCM on stdout.
#: Matches ``TranscriptionClient.transcribe()``'s input contract, so nothing resamples later.
FFMPEG_PCM_ARGS: tuple[str, ...] = (
    "ffmpeg",
    "-hide_banner",
    "-loglevel",
    "error",
    "-i",
    "pipe:0",
    "-f",
    "s16le",
    "-acodec",
    "pcm_s16le",
    "-ac",
    "1",
    "-ar",
    "16000",
    "pipe:1",
)


class VoiceTranscriptionStatus(StrEnum):
    """``payload.transcription.status`` values (telegram-voice-transcription spec)."""

    TRANSCRIBED = "transcribed"
    DISABLED = "disabled"
    NOT_OWNER = "not_owner"
    FORWARDED = "forwarded"
    OVERSIZE = "oversize"
    OVERLONG = "overlong"
    DOWNLOAD_FAILED = "download_failed"
    TRANSCODE_FAILED = "transcode_failed"
    STT_UNAVAILABLE = "stt_unavailable"
    EMPTY_OR_LOW_CONFIDENCE = "empty_or_low_confidence"


def transcription_outcome(
    status: VoiceTranscriptionStatus,
    *,
    stt_backend: str | None = None,
    confidence: float | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    """Build the ``payload.transcription`` object for one evaluated voice note.

    ``stt_backend`` is set whenever an STT call was attempted; ``confidence`` and ``language``
    only on success.
    """
    transcribed = status is VoiceTranscriptionStatus.TRANSCRIBED
    return {
        "source_media": "voice",
        "status": status.value,
        "confidence": confidence if transcribed else None,
        "language": (language or None) if transcribed else None,
        "stt_backend": stt_backend,
    }


async def transcode_to_pcm(audio: bytes, *, timeout_s: float) -> bytes | None:
    """Decode a compressed voice note to raw 16 kHz mono 16-bit PCM with ffmpeg.

    Argument-list subprocess (never a shell), bytes in on stdin and out on stdout, no temporary
    file, killed when ``timeout_s`` elapses. Returns ``None`` on any failure: ffmpeg missing,
    non-zero exit, empty output or timeout.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *FFMPEG_PCM_ARGS,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        logger.warning("Voice transcode could not start ffmpeg: %s", type(exc).__name__)
        return None

    try:
        stdout, _stderr = await asyncio.wait_for(proc.communicate(audio), timeout=timeout_s)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        logger.warning("Voice transcode timed out after %.0fs", timeout_s)
        return None

    if proc.returncode != 0 or not stdout:
        logger.warning("Voice transcode failed (exit=%s)", proc.returncode)
        return None
    return stdout


_outcomes_total: Counter | None = None


def record_outcome(status: VoiceTranscriptionStatus) -> None:
    """Count one evaluated voice note by outcome; metrics never break ingestion."""
    global _outcomes_total
    try:
        if _outcomes_total is None:
            from prometheus_client import Counter as _Counter

            _outcomes_total = _Counter(
                "connector_telegram_voice_transcription_total",
                "Telegram voice notes evaluated for transcription, by outcome",
                labelnames=["status"],
            )
        _outcomes_total.labels(status=status.value).inc()
    except Exception:
        pass
