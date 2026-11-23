"""Owner voice-note transcription helpers for the Telegram bot connector (bu-r0dsz).

Transcription here is transport: the connector turns the verified owner's own, non-forwarded
voice note into the text it already ingests (about/heart-and-soul/v1.md "Voice as an interface",
RFC 0038 D5/D18). The speech-to-text call reuses
``connectors/live_listener/transcription.py::TranscriptionClient`` unchanged; this module owns only
the pieces that client does not cover:

- the outcome taxonomy recorded in ``payload.transcription``
  (openspec/specs/telegram-voice-transcription/spec.md);
- the bounded ``ffmpeg`` transcode (duration, output size, protocol, time) from the voice-note
  container to the client's PCM contract;
- the per-outcome counter.

Audio bytes are owner PII. Nothing here logs, labels or raises with audio bytes, transcript text,
or ffmpeg's stderr (which can echo container metadata).
"""

from __future__ import annotations

import asyncio
import contextlib
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

#: PCM output rate and sample width; ``TranscriptionClient.transcribe()``'s input contract.
PCM_SAMPLE_RATE = 16000
PCM_SAMPLE_BYTES = 2

#: Read size for draining ffmpeg's stdout.
_READ_CHUNK = 64 * 1024


def ffmpeg_pcm_args(max_duration_s: int) -> tuple[str, ...]:
    """ffmpeg argv: one container on stdin to 16 kHz mono s16le PCM on stdout.

    ``-protocol_whitelist pipe`` (inherited by nested demuxers such as HLS or concat) stops a
    crafted container from opening file or network URLs; ``-t`` caps the decoded length, so a
    low-bitrate note that passes the byte ceiling still cannot decode to unbounded PCM.
    """
    return (
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-protocol_whitelist",
        "pipe",
        "-i",
        "pipe:0",
        "-t",
        str(max_duration_s),
        "-f",
        "s16le",
        "-acodec",
        "pcm_s16le",
        "-ac",
        "1",
        "-ar",
        str(PCM_SAMPLE_RATE),
        "pipe:1",
    )


def pcm_byte_ceiling(max_duration_s: int) -> int:
    """Largest PCM output accepted for ``max_duration_s`` of audio (one second of slack)."""
    return (max_duration_s + 1) * PCM_SAMPLE_RATE * PCM_SAMPLE_BYTES


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


async def transcode_to_pcm(audio: bytes, *, timeout_s: float, max_duration_s: int) -> bytes | None:
    """Decode a compressed voice note to raw 16 kHz mono 16-bit PCM with ffmpeg.

    Argument-list subprocess (never a shell), bytes in on stdin and out on stdout, no temporary
    file, stderr discarded unread. Output is bounded twice: ffmpeg's ``-t`` caps the decoded
    length, and stdout is read in chunks and abandoned past :func:`pcm_byte_ceiling`. The process
    is killed on timeout or overflow. Returns ``None`` on any failure: ffmpeg missing, non-zero
    exit, empty or oversized output, or timeout.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *ffmpeg_pcm_args(max_duration_s),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError as exc:
        logger.warning("Voice transcode could not start ffmpeg: %s", type(exc).__name__)
        return None

    try:
        stdout = await asyncio.wait_for(
            _exchange(proc, audio, pcm_byte_ceiling(max_duration_s)), timeout=timeout_s
        )
    except TimeoutError:
        await _kill(proc)
        logger.warning("Voice transcode timed out after %.0fs", timeout_s)
        return None

    if stdout is None:
        await _kill(proc)
        logger.warning("Voice transcode output exceeded the duration ceiling")
        return None
    if proc.returncode != 0 or not stdout:
        logger.warning("Voice transcode failed (exit=%s)", proc.returncode)
        return None
    return stdout


async def _exchange(proc: asyncio.subprocess.Process, audio: bytes, max_bytes: int) -> bytes | None:
    """Feed ``audio`` to stdin while draining stdout; ``None`` once output passes ``max_bytes``."""
    stdin, stdout = proc.stdin, proc.stdout
    assert stdin is not None and stdout is not None

    async def feed() -> None:
        try:
            stdin.write(audio)
            await stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass  # ffmpeg stopped reading (``-t`` reached or bad input); its exit code decides
        finally:
            stdin.close()

    feeder = asyncio.create_task(feed())
    try:
        chunks: list[bytes] = []
        total = 0
        while chunk := await stdout.read(_READ_CHUNK):
            total += len(chunk)
            if total > max_bytes:
                return None
            chunks.append(chunk)
        await proc.wait()
        return b"".join(chunks)
    finally:
        feeder.cancel()
        await asyncio.gather(feeder, return_exceptions=True)


async def _kill(proc: asyncio.subprocess.Process) -> None:
    """Kill and reap ``proc``, discarding whatever output it left behind.

    ``Process.wait()`` also waits for the pipes to close, and a stdout reader paused at its
    buffer high-water mark never sees EOF, so stdout is drained first. The process is dead,
    so what remains is bounded by the reader buffer plus the pipe capacity.
    """
    with contextlib.suppress(ProcessLookupError):
        proc.kill()
    if proc.stdout is not None:
        while await proc.stdout.read(_READ_CHUNK):
            pass
    await proc.wait()


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
