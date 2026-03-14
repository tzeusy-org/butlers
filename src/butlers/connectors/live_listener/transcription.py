"""Transcription client implementations for the live-listener connector.

Provides a protocol-agnostic ``TranscriptionClient`` abstract interface and three
concrete backends:

- ``WyomingTranscriptionClient`` (default): persistent TCP connection using the
  Wyoming wire protocol (JSON header line + binary payload framing).
- ``WebSocketTranscriptionClient``: persistent WebSocket connection, streaming
  audio chunks during capture.
- ``HttpTranscriptionClient``: stateless HTTP POST of a complete audio segment.

All clients implement graceful degradation: when the service is unreachable, the
segment is *dropped* (never buffered) and the health state is set to ``degraded``.
Reconnection uses exponential backoff capped at 30 s.

Metrics (emitted when a metrics registry is provided):
    ``connector_live_listener_transcription_failures_total{mic, error_type}``
    ``connector_live_listener_transcription_discarded_total{mic, reason}``

Spec references:
    specs/connector-live-listener/spec.md → Requirement: Transcription Client
    specs/connector-live-listener/spec.md → Requirement: Latency Budget
"""

from __future__ import annotations

import asyncio
import io
import logging
import socket
import wave
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from prometheus_client import Counter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Default Wyoming transcription URL
DEFAULT_WYOMING_URL = "tcp://wyoming-faster-whisper.parrot-hen.ts.net:10300"

#: Minimum confidence threshold for accepting a transcription result
DEFAULT_MIN_CONFIDENCE = 0.3

#: Initial reconnect backoff in seconds
_BACKOFF_INITIAL = 1.0
#: Maximum reconnect backoff in seconds (spec: 30 s for transcription)
_BACKOFF_MAX = 30.0
#: Backoff multiplier
_BACKOFF_FACTOR = 2.0

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class TranscriptionResult:
    """Result returned by a TranscriptionClient.

    Attributes:
        text: Transcribed text string. Empty string if nothing was recognised.
        confidence: Model confidence in [0, 1]. 0.0 when not reported by service.
        language: BCP-47 language code detected/used (e.g. ``"en"``).
        duration_s: Duration of the audio segment in seconds.
    """

    text: str
    confidence: float
    language: str
    duration_s: float


class TranscriptionProtocol(StrEnum):
    """Supported transcription protocols."""

    WYOMING = "wyoming"
    WEBSOCKET = "websocket"
    HTTP = "http"


# ---------------------------------------------------------------------------
# Prometheus metrics (module-level, lazily created)
# ---------------------------------------------------------------------------

_transcription_failures_total: Counter | None = None
_transcription_discarded_total: Counter | None = None


def _get_failures_counter() -> Counter:
    global _transcription_failures_total
    if _transcription_failures_total is None:
        from prometheus_client import Counter as _Counter

        _transcription_failures_total = _Counter(
            "connector_live_listener_transcription_failures_total",
            "Total number of transcription service failures",
            labelnames=["mic", "error_type"],
        )
    return _transcription_failures_total


def _get_discarded_counter() -> Counter:
    global _transcription_discarded_total
    if _transcription_discarded_total is None:
        from prometheus_client import Counter as _Counter

        _transcription_discarded_total = _Counter(
            "connector_live_listener_transcription_discarded_total",
            "Total number of transcriptions discarded (empty or low confidence)",
            labelnames=["mic", "reason"],
        )
    return _transcription_discarded_total


def _record_failure(mic: str, error_type: str) -> None:
    try:
        _get_failures_counter().labels(mic=mic, error_type=error_type).inc()
    except Exception:
        pass  # Never let metrics crash the pipeline


def _record_discarded(mic: str, reason: str) -> None:
    try:
        _get_discarded_counter().labels(mic=mic, reason=reason).inc()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------


class TranscriptionClient(ABC):
    """Abstract interface for transcription backends.

    All implementations MUST:
    - Be usable as async context managers (``async with client``)
    - Be safe to call ``transcribe()`` concurrently from one mic pipeline
      (single-consumer model; each mic has its own client instance)
    - Drop segments and return ``None`` when the service is unavailable
    - Reconnect in the background with exponential backoff
    """

    def __init__(self, mic_name: str = "default", language: str = "en") -> None:
        self._mic_name = mic_name
        self._language = language
        self._healthy = True

    @property
    def healthy(self) -> bool:
        """True when the client is connected and the service is reachable."""
        return self._healthy

    async def __aenter__(self) -> TranscriptionClient:
        await self.connect()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.disconnect()

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the transcription service."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Close the connection to the transcription service."""

    @abstractmethod
    async def transcribe(self, audio: bytes) -> TranscriptionResult | None:
        """Transcribe raw 16kHz mono 16-bit PCM audio.

        Args:
            audio: Raw PCM bytes (16 kHz, mono, 16-bit signed little-endian).

        Returns:
            ``TranscriptionResult`` on success, or ``None`` if the segment
            should be dropped (service unavailable or empty/low-confidence
            result after filtering).
        """

    def _should_discard(self, result: TranscriptionResult, min_confidence: float) -> str | None:
        """Return a discard reason string, or ``None`` to accept the result."""
        if not result.text.strip():
            return "empty"
        if result.confidence > 0 and result.confidence < min_confidence:
            return "low_confidence"
        return None


# ---------------------------------------------------------------------------
# Wyoming backend
# ---------------------------------------------------------------------------


class WyomingTranscriptionClient(TranscriptionClient):
    """Transcription client using the Wyoming wire protocol over persistent TCP.

    The message flow for a single segment is::

        client → transcribe (optional language hint)
        client → audio-start (rate=16000, width=2, channels=1)
        client → audio-chunk(s) (raw PCM payload)
        client → audio-stop
        server → transcript (or transcript-chunk events before the final one)

    The TCP connection is kept alive across calls.  On any failure the
    connection is dropped and re-established with exponential backoff before
    the next ``transcribe()`` call.

    Args:
        url: TCP URL in the form ``tcp://host:port``.
        mic_name: Mic identifier used in metric labels.
        language: BCP-47 language hint (e.g. ``"en"``).
        min_confidence: Minimum confidence to accept; results below this are
            discarded and ``None`` is returned.
        chunk_size_bytes: How many PCM bytes to send in each ``audio-chunk``
            event (default: 4096 — ~64 ms at 16 kHz/16-bit/mono).
    """

    def __init__(
        self,
        url: str = DEFAULT_WYOMING_URL,
        mic_name: str = "default",
        language: str = "en",
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        chunk_size_bytes: int = 4096,
    ) -> None:
        super().__init__(mic_name=mic_name, language=language)

        # Parse "tcp://host:port" → host, port
        if url.startswith("tcp://"):
            rest = url[len("tcp://") :]
        else:
            rest = url
        if ":" in rest:
            host, port_str = rest.rsplit(":", 1)
            self._host = host
            self._port = int(port_str)
        else:
            raise ValueError(f"Invalid Wyoming URL (expected tcp://host:port): {url!r}")

        self._min_confidence = min_confidence
        self._chunk_size = chunk_size_bytes

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._backoff = _BACKOFF_INITIAL
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        """Open a TCP connection to the Wyoming service."""
        try:
            self._reader, self._writer = await asyncio.open_connection(self._host, self._port)
            # Enable TCP keepalive to detect dead connections before the next
            # transcribe() call, avoiding the idle-timeout-then-fail pattern.
            sock = self._writer.get_extra_info("socket")
            if sock is not None:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                # Linux-specific: probe after 15s idle, every 5s, 3 probes before drop
                if hasattr(socket, "TCP_KEEPIDLE"):
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 15)
                if hasattr(socket, "TCP_KEEPINTVL"):
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 5)
                if hasattr(socket, "TCP_KEEPCNT"):
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
            self._healthy = True
            self._backoff = _BACKOFF_INITIAL
            logger.debug(
                "WyomingTranscriptionClient connected to %s:%d (mic=%s)",
                self._host,
                self._port,
                self._mic_name,
            )
        except OSError as exc:
            self._healthy = False
            _record_failure(self._mic_name, "connection_error")
            logger.warning(
                "WyomingTranscriptionClient: failed to connect to %s:%d: %s (mic=%s)",
                self._host,
                self._port,
                exc,
                self._mic_name,
            )

    async def disconnect(self) -> None:
        """Close the TCP connection."""
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None

    async def _reconnect(self) -> bool:
        """Attempt reconnection; return True if successful."""
        await self.disconnect()
        logger.info(
            "WyomingTranscriptionClient: reconnecting in %.1fs (mic=%s)",
            self._backoff,
            self._mic_name,
        )
        await asyncio.sleep(self._backoff)
        self._backoff = min(self._backoff * _BACKOFF_FACTOR, _BACKOFF_MAX)
        await self.connect()
        return self._healthy

    async def transcribe(self, audio: bytes) -> TranscriptionResult | None:
        """Send audio segment and receive transcript via Wyoming protocol.

        Audio is sent in chunks (streaming) rather than all-at-once to keep
        latency low when the VAD streams bytes as they arrive.
        """
        async with self._lock:
            return await self._transcribe_locked(audio)

    async def _transcribe_locked(self, audio: bytes) -> TranscriptionResult | None:
        # Ensure we have a connection
        if self._writer is None or self._writer.is_closing():
            if not await self._reconnect():
                _record_failure(self._mic_name, "connection_error")
                return None

        try:
            return await self._do_transcribe(audio)
        except (ConnectionResetError, BrokenPipeError, EOFError, OSError) as exc:
            logger.warning(
                "WyomingTranscriptionClient: connection lost during transcribe: %s (mic=%s)",
                exc,
                self._mic_name,
            )
            self._healthy = False
            _record_failure(self._mic_name, "connection_error")
            # Try once to reconnect and retry
            if await self._reconnect():
                try:
                    return await self._do_transcribe(audio)
                except Exception as retry_exc:
                    logger.warning(
                        "WyomingTranscriptionClient: retry failed: %s (mic=%s)",
                        retry_exc,
                        self._mic_name,
                    )
                    self._healthy = False
                    _record_failure(self._mic_name, type(retry_exc).__name__.lower())
            return None
        except TimeoutError:
            logger.warning(
                "WyomingTranscriptionClient: timeout waiting for transcript (mic=%s)",
                self._mic_name,
            )
            self._healthy = False
            _record_failure(self._mic_name, "timeout")
            return None
        except Exception as exc:
            logger.warning(
                "WyomingTranscriptionClient: unexpected error: %s (mic=%s)",
                exc,
                self._mic_name,
            )
            self._healthy = False
            _record_failure(self._mic_name, type(exc).__name__.lower())
            return None

    async def _do_transcribe(self, audio: bytes) -> TranscriptionResult | None:
        """Execute the Wyoming ASR protocol exchange."""
        from wyoming.asr import Transcribe, Transcript
        from wyoming.audio import AudioChunk, AudioStart, AudioStop
        from wyoming.event import async_read_event, async_write_event

        assert self._writer is not None
        assert self._reader is not None

        duration_s = len(audio) / (16000 * 2)  # 16 kHz, 2 bytes/sample

        # 1. Send transcribe request
        transcribe_event = Transcribe(language=self._language).event()
        await async_write_event(transcribe_event, self._writer)

        # 2. Send audio-start
        audio_start = AudioStart(rate=16000, width=2, channels=1).event()
        await async_write_event(audio_start, self._writer)

        # 3. Stream audio chunks
        offset = 0
        while offset < len(audio):
            chunk = audio[offset : offset + self._chunk_size]
            audio_chunk = AudioChunk(rate=16000, width=2, channels=1, audio=chunk).event()
            await async_write_event(audio_chunk, self._writer)
            offset += self._chunk_size

        # 4. Send audio-stop
        audio_stop = AudioStop().event()
        await async_write_event(audio_stop, self._writer)

        # 5. Read response — may receive transcript-chunk events first, then transcript
        transcript_text = ""
        detected_language = self._language
        confidence = 0.0

        while True:
            event = await asyncio.wait_for(
                async_read_event(self._reader),
                timeout=10.0,
            )
            if event is None:
                raise EOFError("Connection closed by server before transcript received")

            if Transcript.is_type(event.type):
                transcript = Transcript.from_event(event)
                transcript_text = transcript.text or ""
                if transcript.language:
                    detected_language = transcript.language
                # Extract confidence from context if present
                if transcript.context and isinstance(transcript.context, dict):
                    confidence = float(transcript.context.get("confidence", 0.0))
                break  # Final transcript received

            # transcript-chunk or other intermediate events — keep reading
            logger.debug(
                "WyomingTranscriptionClient: received intermediate event type=%s (mic=%s)",
                event.type,
                self._mic_name,
            )

        self._healthy = True

        result = TranscriptionResult(
            text=transcript_text,
            confidence=confidence,
            language=detected_language,
            duration_s=duration_s,
        )

        discard_reason = self._should_discard(result, self._min_confidence)
        if discard_reason is not None:
            _record_discarded(self._mic_name, discard_reason)
            logger.debug(
                "WyomingTranscriptionClient: discarding result reason=%s text=%r (mic=%s)",
                discard_reason,
                transcript_text[:50],
                self._mic_name,
            )
            return None

        return result


# ---------------------------------------------------------------------------
# WebSocket backend
# ---------------------------------------------------------------------------


class WebSocketTranscriptionClient(TranscriptionClient):
    """Transcription client using a persistent WebSocket connection.

    Audio chunks are streamed over the WebSocket as binary frames as they
    arrive from the VAD (not buffered until segment complete).  The server is
    expected to respond with a JSON object containing ``{"text": "...",
    "confidence": 0.9, "language": "en"}`` after receiving the audio.

    The connection is persistent per mic pipeline; on failure it reconnects
    with exponential backoff.

    Args:
        url: WebSocket URL (e.g. ``ws://localhost:8765/transcribe``).
        mic_name: Mic identifier used in metric labels.
        language: BCP-47 language hint sent as a connection header.
        min_confidence: Minimum confidence to accept.
    """

    def __init__(
        self,
        url: str,
        mic_name: str = "default",
        language: str = "en",
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    ) -> None:
        super().__init__(mic_name=mic_name, language=language)
        self._url = url
        self._min_confidence = min_confidence
        self._ws: Any | None = None  # aiohttp.ClientWebSocketResponse
        self._session: Any | None = None  # aiohttp.ClientSession
        self._backoff = _BACKOFF_INITIAL
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        """Open a WebSocket connection to the transcription service."""
        import aiohttp

        try:
            if self._session is None:
                self._session = aiohttp.ClientSession()
            self._ws = await self._session.ws_connect(
                self._url,
                headers={"X-Language": self._language},
                timeout=aiohttp.ClientTimeout(total=30),
            )
            self._healthy = True
            self._backoff = _BACKOFF_INITIAL
            logger.debug(
                "WebSocketTranscriptionClient connected to %s (mic=%s)",
                self._url,
                self._mic_name,
            )
        except Exception as exc:
            self._healthy = False
            _record_failure(self._mic_name, "connection_error")
            logger.warning(
                "WebSocketTranscriptionClient: failed to connect to %s: %s (mic=%s)",
                self._url,
                exc,
                self._mic_name,
            )

    async def disconnect(self) -> None:
        """Close the WebSocket and underlying session."""
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        if self._session is not None:
            try:
                await self._session.close()
            except Exception:
                pass
            self._session = None

    async def _reconnect(self) -> bool:
        """Reconnect WebSocket; return True on success."""
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        logger.info(
            "WebSocketTranscriptionClient: reconnecting in %.1fs (mic=%s)",
            self._backoff,
            self._mic_name,
        )
        await asyncio.sleep(self._backoff)
        self._backoff = min(self._backoff * _BACKOFF_FACTOR, _BACKOFF_MAX)
        await self.connect()
        return self._healthy

    async def transcribe(self, audio: bytes) -> TranscriptionResult | None:
        """Stream audio chunks over WebSocket and receive transcript JSON."""
        async with self._lock:
            return await self._transcribe_locked(audio)

    async def _transcribe_locked(self, audio: bytes) -> TranscriptionResult | None:
        import aiohttp

        if self._ws is None:
            reconnected = await self._reconnect()
            if not reconnected:
                self._healthy = False
                _record_failure(self._mic_name, "connection_error")
                return None

        try:
            return await self._do_transcribe(audio)
        except (aiohttp.ClientConnectionError, aiohttp.ServerConnectionError) as exc:
            logger.warning(
                "WebSocketTranscriptionClient: connection error: %s (mic=%s)", exc, self._mic_name
            )
            self._healthy = False
            _record_failure(self._mic_name, "connection_error")
            if await self._reconnect():
                try:
                    return await self._do_transcribe(audio)
                except Exception as retry_exc:
                    logger.warning(
                        "WebSocketTranscriptionClient: retry failed: %s (mic=%s)",
                        retry_exc,
                        self._mic_name,
                    )
                    self._healthy = False
                    _record_failure(self._mic_name, type(retry_exc).__name__.lower())
            return None
        except TimeoutError:
            logger.warning("WebSocketTranscriptionClient: timeout (mic=%s)", self._mic_name)
            self._healthy = False
            _record_failure(self._mic_name, "timeout")
            return None
        except Exception as exc:
            logger.warning("WebSocketTranscriptionClient: error: %s (mic=%s)", exc, self._mic_name)
            self._healthy = False
            _record_failure(self._mic_name, type(exc).__name__.lower())
            return None

    async def _do_transcribe(self, audio: bytes) -> TranscriptionResult | None:
        import json

        import aiohttp

        duration_s = len(audio) / (16000 * 2)

        # Stream audio in chunks (streaming, not buffered)
        chunk_size = 4096
        offset = 0
        while offset < len(audio):
            chunk = audio[offset : offset + chunk_size]
            await self._ws.send_bytes(chunk)
            offset += chunk_size

        # Signal end of audio
        await self._ws.send_str(json.dumps({"type": "end"}))

        # Wait for transcript response
        msg = await asyncio.wait_for(self._ws.receive(), timeout=10.0)

        if msg.type == aiohttp.WSMsgType.TEXT:
            data = json.loads(msg.data)
        elif msg.type == aiohttp.WSMsgType.BINARY:
            data = json.loads(msg.data.decode("utf-8"))
        elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
            raise aiohttp.ClientConnectionError(f"WebSocket closed/errored: {msg.type}")
        else:
            raise ValueError(f"Unexpected WebSocket message type: {msg.type}")

        transcript_text = data.get("text", "")
        confidence = float(data.get("confidence", 0.0))
        language = data.get("language", self._language)

        self._healthy = True
        result = TranscriptionResult(
            text=transcript_text,
            confidence=confidence,
            language=language,
            duration_s=duration_s,
        )

        discard_reason = self._should_discard(result, self._min_confidence)
        if discard_reason is not None:
            _record_discarded(self._mic_name, discard_reason)
            return None

        return result


# ---------------------------------------------------------------------------
# HTTP backend
# ---------------------------------------------------------------------------


class HttpTranscriptionClient(TranscriptionClient):
    """Transcription client using HTTP POST (stateless, one request per segment).

    The complete audio segment is posted to the configured URL as a WAV file.
    The server should respond with JSON: ``{"text": "...", "confidence": 0.9,
    "language": "en"}``.

    Each segment is an independent request — no persistent connection.
    On failure the segment is dropped and ``None`` is returned.

    Args:
        url: HTTP URL to POST audio segments to.
        mic_name: Mic identifier used in metric labels.
        language: BCP-47 language hint sent as ``X-Language`` header.
        min_confidence: Minimum confidence to accept.
    """

    def __init__(
        self,
        url: str,
        mic_name: str = "default",
        language: str = "en",
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    ) -> None:
        super().__init__(mic_name=mic_name, language=language)
        self._url = url
        self._min_confidence = min_confidence
        self._session: Any | None = None  # aiohttp.ClientSession

    async def connect(self) -> None:
        """Create the underlying HTTP session."""
        import aiohttp

        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15),
            )
        self._healthy = True

    async def disconnect(self) -> None:
        """Close the HTTP session."""
        if self._session is not None:
            try:
                await self._session.close()
            except Exception:
                pass
            self._session = None

    async def transcribe(self, audio: bytes) -> TranscriptionResult | None:
        """POST the complete audio segment and parse the JSON response."""
        import aiohttp

        if self._session is None:
            await self.connect()

        duration_s = len(audio) / (16000 * 2)
        wav_bytes = _pcm_to_wav(audio, sample_rate=16000, num_channels=1, sample_width=2)

        try:
            async with self._session.post(
                self._url,
                data=wav_bytes,
                headers={
                    "Content-Type": "audio/wav",
                    "X-Language": self._language,
                    "X-Sample-Rate": "16000",
                },
            ) as resp:
                if resp.status != 200:
                    error_type = f"http_{resp.status}"
                    _record_failure(self._mic_name, error_type)
                    self._healthy = False
                    logger.warning(
                        "HttpTranscriptionClient: HTTP %d from %s (mic=%s)",
                        resp.status,
                        self._url,
                        self._mic_name,
                    )
                    return None

                data = await resp.json(content_type=None)

        except aiohttp.ClientConnectionError as exc:
            _record_failure(self._mic_name, "connection_error")
            self._healthy = False
            logger.warning(
                "HttpTranscriptionClient: connection error: %s (mic=%s)", exc, self._mic_name
            )
            return None
        except TimeoutError:
            _record_failure(self._mic_name, "timeout")
            self._healthy = False
            logger.warning("HttpTranscriptionClient: timeout (mic=%s)", self._mic_name)
            return None
        except Exception as exc:
            _record_failure(self._mic_name, type(exc).__name__.lower())
            self._healthy = False
            logger.warning("HttpTranscriptionClient: error: %s (mic=%s)", exc, self._mic_name)
            return None

        self._healthy = True
        transcript_text = data.get("text", "")
        confidence = float(data.get("confidence", 0.0))
        language = data.get("language", self._language)

        result = TranscriptionResult(
            text=transcript_text,
            confidence=confidence,
            language=language,
            duration_s=duration_s,
        )

        discard_reason = self._should_discard(result, self._min_confidence)
        if discard_reason is not None:
            _record_discarded(self._mic_name, discard_reason)
            return None

        return result


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_transcription_client(
    protocol: str | TranscriptionProtocol = TranscriptionProtocol.WYOMING,
    url: str | None = None,
    mic_name: str = "default",
    language: str = "en",
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> TranscriptionClient:
    """Create a ``TranscriptionClient`` for the given protocol.

    Args:
        protocol: Protocol identifier (``"wyoming"``, ``"websocket"``, ``"http"``).
        url: Service URL. Defaults to ``DEFAULT_WYOMING_URL`` for the Wyoming protocol.
        mic_name: Mic identifier used in metric labels.
        language: BCP-47 language hint.
        min_confidence: Minimum confidence threshold.

    Returns:
        A concrete ``TranscriptionClient`` instance (not yet connected).

    Raises:
        ValueError: If ``protocol`` is unknown or ``url`` is required but not given.
    """
    # Normalise: handle TranscriptionProtocol enum instances directly, or convert string
    if isinstance(protocol, TranscriptionProtocol):
        proto = protocol
    else:
        proto = TranscriptionProtocol(str(protocol).lower())

    if proto == TranscriptionProtocol.WYOMING:
        effective_url = url or DEFAULT_WYOMING_URL
        return WyomingTranscriptionClient(
            url=effective_url,
            mic_name=mic_name,
            language=language,
            min_confidence=min_confidence,
        )

    if proto == TranscriptionProtocol.WEBSOCKET:
        if not url:
            raise ValueError("url is required for the websocket transcription protocol")
        return WebSocketTranscriptionClient(
            url=url,
            mic_name=mic_name,
            language=language,
            min_confidence=min_confidence,
        )

    if proto == TranscriptionProtocol.HTTP:
        if not url:
            raise ValueError("url is required for the http transcription protocol")
        return HttpTranscriptionClient(
            url=url,
            mic_name=mic_name,
            language=language,
            min_confidence=min_confidence,
        )

    raise ValueError(f"Unknown transcription protocol: {protocol!r}")  # pragma: no cover


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pcm_to_wav(
    pcm_data: bytes,
    sample_rate: int = 16000,
    num_channels: int = 1,
    sample_width: int = 2,
) -> bytes:
    """Wrap raw PCM bytes in a WAV container.

    Args:
        pcm_data: Raw PCM bytes (little-endian signed integer).
        sample_rate: Sample rate in Hz (default 16000).
        num_channels: Number of channels (default 1 = mono).
        sample_width: Bytes per sample (default 2 = 16-bit).

    Returns:
        WAV-formatted bytes.
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(num_channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)
    return buf.getvalue()
