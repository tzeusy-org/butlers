"""Tests for live-listener transcription client implementations.

Covers:
- TranscriptionResult dataclass
- TranscriptionClient abstract interface
- WyomingTranscriptionClient: happy path, empty result, low confidence,
  connection failure, retry on disconnect, backoff, timeout
- WebSocketTranscriptionClient: happy path, empty result, low confidence,
  connection failure, retry
- HttpTranscriptionClient: happy path, empty result, low confidence,
  HTTP error, connection error, timeout
- create_transcription_client() factory
- _pcm_to_wav() helper

All tests are unit tests (no external services).
"""

from __future__ import annotations

import asyncio
import io
import json
import wave
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.connectors.live_listener.transcription import (
    DEFAULT_MIN_CONFIDENCE,
    HttpTranscriptionClient,
    TranscriptionClient,
    TranscriptionProtocol,
    TranscriptionResult,
    WebSocketTranscriptionClient,
    WyomingTranscriptionClient,
    _pcm_to_wav,
    create_transcription_client,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_RATE = 16000
_SAMPLE_WIDTH = 2  # bytes
_DURATION_S = 0.1  # 100 ms
_SAMPLE_COUNT = int(_SAMPLE_RATE * _DURATION_S)
_PCM_BYTES = b"\x00\x00" * _SAMPLE_COUNT  # 100 ms of silence


def _make_pcm(duration_s: float = 0.1) -> bytes:
    """Generate silent raw PCM bytes (16 kHz, mono, 16-bit)."""
    samples = int(_SAMPLE_RATE * duration_s)
    return b"\x00\x00" * samples


# ---------------------------------------------------------------------------
# TranscriptionResult
# ---------------------------------------------------------------------------


class TestTranscriptionResult:
    def test_fields(self) -> None:
        r = TranscriptionResult(text="hello", confidence=0.9, language="en", duration_s=1.0)
        assert r.text == "hello"
        assert r.confidence == 0.9
        assert r.language == "en"
        assert r.duration_s == 1.0

    def test_empty_text(self) -> None:
        r = TranscriptionResult(text="", confidence=0.0, language="en", duration_s=0.5)
        assert r.text == ""


# ---------------------------------------------------------------------------
# TranscriptionProtocol enum
# ---------------------------------------------------------------------------


class TestTranscriptionProtocol:
    def test_values(self) -> None:
        assert TranscriptionProtocol.WYOMING == "wyoming"
        assert TranscriptionProtocol.WEBSOCKET == "websocket"
        assert TranscriptionProtocol.HTTP == "http"

    def test_from_string(self) -> None:
        assert TranscriptionProtocol("wyoming") == TranscriptionProtocol.WYOMING
        assert TranscriptionProtocol("websocket") == TranscriptionProtocol.WEBSOCKET
        assert TranscriptionProtocol("http") == TranscriptionProtocol.HTTP


# ---------------------------------------------------------------------------
# _pcm_to_wav helper
# ---------------------------------------------------------------------------


class TestPcmToWav:
    def test_produces_valid_wav(self) -> None:
        wav = _pcm_to_wav(_PCM_BYTES)
        buf = io.BytesIO(wav)
        with wave.open(buf, "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == 16000
            assert wf.getnframes() == _SAMPLE_COUNT

    def test_roundtrip_audio_content(self) -> None:
        """PCM content should survive WAV container round-trip."""
        pcm = b"\x01\x02" * 100
        wav = _pcm_to_wav(pcm, sample_rate=16000)
        buf = io.BytesIO(wav)
        with wave.open(buf, "rb") as wf:
            recovered = wf.readframes(wf.getnframes())
        assert recovered == pcm

    def test_custom_params(self) -> None:
        wav = _pcm_to_wav(b"\x00" * 400, sample_rate=8000, num_channels=1, sample_width=2)
        buf = io.BytesIO(wav)
        with wave.open(buf, "rb") as wf:
            assert wf.getframerate() == 8000
            assert wf.getnframes() == 200


# ---------------------------------------------------------------------------
# should_discard helper (via subclass access)
# ---------------------------------------------------------------------------


class _ConcreteClient(TranscriptionClient):
    """Minimal concrete subclass for testing base-class helpers."""

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def transcribe(self, audio: bytes) -> TranscriptionResult | None:
        return None


class TestShouldDiscard:
    def setup_method(self) -> None:
        self.client = _ConcreteClient(mic_name="test", language="en")

    def _make_result(self, text: str, confidence: float = 0.9) -> TranscriptionResult:
        return TranscriptionResult(text=text, confidence=confidence, language="en", duration_s=1.0)

    def test_accept_normal_result(self) -> None:
        r = self._make_result("hello", confidence=0.9)
        assert self.client._should_discard(r, DEFAULT_MIN_CONFIDENCE) is None

    def test_discard_empty_text(self) -> None:
        r = self._make_result("")
        assert self.client._should_discard(r, DEFAULT_MIN_CONFIDENCE) == "empty"

    def test_discard_whitespace_only(self) -> None:
        r = self._make_result("   ")
        assert self.client._should_discard(r, DEFAULT_MIN_CONFIDENCE) == "empty"

    def test_discard_low_confidence(self) -> None:
        r = self._make_result("something", confidence=0.1)
        assert self.client._should_discard(r, min_confidence=0.3) == "low_confidence"

    def test_accept_confidence_at_threshold(self) -> None:
        # confidence == min_confidence is NOT discarded (boundary: strict < check)
        r = self._make_result("something", confidence=0.3)
        assert self.client._should_discard(r, min_confidence=0.3) is None

    def test_accept_zero_confidence(self) -> None:
        # confidence=0.0 means "not reported" — don't discard on confidence alone
        r = self._make_result("something", confidence=0.0)
        assert self.client._should_discard(r, min_confidence=0.3) is None


# ---------------------------------------------------------------------------
# WyomingTranscriptionClient — URL parsing
# ---------------------------------------------------------------------------


class TestWyomingTranscriptionClientParsing:
    def test_parse_tcp_url(self) -> None:
        c = WyomingTranscriptionClient(url="tcp://localhost:10300")
        assert c._host == "localhost"
        assert c._port == 10300

    def test_parse_without_scheme(self) -> None:
        c = WyomingTranscriptionClient(url="myhost:9999")
        assert c._host == "myhost"
        assert c._port == 9999

    def test_invalid_url_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid Wyoming URL"):
            WyomingTranscriptionClient(url="tcp://noporthere")

    def test_default_url(self) -> None:
        c = WyomingTranscriptionClient()
        assert c._port == 10300


# ---------------------------------------------------------------------------
# WyomingTranscriptionClient — behaviour (mock _do_transcribe / connect layers)
# ---------------------------------------------------------------------------


class TestWyomingTranscriptionClientBehaviour:
    async def test_happy_path_returns_result(self) -> None:
        """When _do_transcribe succeeds, transcribe() returns the result."""
        expected = TranscriptionResult(
            text="hello world", confidence=0.9, language="en", duration_s=0.1
        )
        client = WyomingTranscriptionClient(url="tcp://localhost:10300", mic_name="kitchen")
        client._healthy = True

        mock_reader = AsyncMock(spec=asyncio.StreamReader)
        mock_writer = MagicMock(spec=asyncio.StreamWriter)
        mock_writer.is_closing.return_value = False
        client._reader = mock_reader
        client._writer = mock_writer

        with patch.object(client, "_do_transcribe", new=AsyncMock(return_value=expected)):
            result = await client.transcribe(_make_pcm())

        assert result is expected

    async def test_empty_transcript_discarded(self) -> None:
        """_do_transcribe returning empty text → transcribe() returns None."""
        client = WyomingTranscriptionClient(url="tcp://localhost:10300", mic_name="kitchen")
        mock_writer = MagicMock(spec=asyncio.StreamWriter)
        mock_writer.is_closing.return_value = False
        client._reader = AsyncMock(spec=asyncio.StreamReader)
        client._writer = mock_writer
        client._healthy = True

        # Simulate _do_transcribe returning None (already filtered by discard logic)
        with patch.object(client, "_do_transcribe", new=AsyncMock(return_value=None)):
            result = await client.transcribe(_make_pcm())
        assert result is None

    async def test_connection_failure_returns_none_and_sets_unhealthy(self) -> None:
        """When connect() fails, transcribe() returns None and client is unhealthy."""
        client = WyomingTranscriptionClient(url="tcp://localhost:10300", mic_name="kitchen")

        with patch("asyncio.open_connection", side_effect=OSError("connection refused")):
            await client.connect()

        assert not client.healthy

        with patch.object(client, "_reconnect", new=AsyncMock(return_value=False)):
            result = await client.transcribe(_make_pcm())

        assert result is None

    async def test_connection_lost_mid_transcribe_triggers_retry(self) -> None:
        """On ConnectionResetError, client reconnects and retries once."""
        client = WyomingTranscriptionClient(
            url="tcp://localhost:10300", mic_name="kitchen", min_confidence=0.3
        )
        mock_writer = MagicMock(spec=asyncio.StreamWriter)
        mock_writer.is_closing.return_value = False
        client._reader = AsyncMock(spec=asyncio.StreamReader)
        client._writer = mock_writer
        client._healthy = True

        expected = TranscriptionResult(
            text="retry success", confidence=0.9, language="en", duration_s=0.1
        )
        call_count = 0

        async def _failing_then_success(audio: bytes) -> TranscriptionResult | None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionResetError("connection reset")
            return expected

        with patch.object(client, "_do_transcribe", side_effect=_failing_then_success):
            with patch.object(client, "_reconnect", new=AsyncMock(return_value=True)):
                result = await client.transcribe(_make_pcm())

        assert result is expected
        assert call_count == 2

    async def test_timeout_returns_none_and_sets_unhealthy(self) -> None:
        """TimeoutError during transcription → None and unhealthy."""
        client = WyomingTranscriptionClient(url="tcp://localhost:10300", mic_name="kitchen")
        mock_writer = MagicMock(spec=asyncio.StreamWriter)
        mock_writer.is_closing.return_value = False
        client._reader = AsyncMock(spec=asyncio.StreamReader)
        client._writer = mock_writer
        client._healthy = True

        with patch.object(client, "_do_transcribe", side_effect=TimeoutError()):
            result = await client.transcribe(_make_pcm())

        assert result is None
        assert not client.healthy

    async def test_connect_stores_reader_writer(self) -> None:
        """connect() should populate _reader and _writer on success."""
        mock_reader = AsyncMock(spec=asyncio.StreamReader)
        mock_writer = MagicMock(spec=asyncio.StreamWriter)

        client = WyomingTranscriptionClient(url="tcp://localhost:10300", mic_name="kitchen")

        with patch("asyncio.open_connection", return_value=(mock_reader, mock_writer)):
            await client.connect()

        assert client._reader is mock_reader
        assert client._writer is mock_writer
        assert client.healthy

    async def test_disconnect_closes_writer(self) -> None:
        """disconnect() should close the writer and clear reader/writer."""
        mock_writer = AsyncMock(spec=asyncio.StreamWriter)
        mock_writer.is_closing.return_value = False

        client = WyomingTranscriptionClient(url="tcp://localhost:10300", mic_name="kitchen")
        client._writer = mock_writer
        client._reader = AsyncMock(spec=asyncio.StreamReader)

        await client.disconnect()

        mock_writer.close.assert_called_once()
        assert client._writer is None
        assert client._reader is None

    async def test_backoff_caps_at_max(self) -> None:
        """Backoff should not exceed _BACKOFF_MAX."""
        from butlers.connectors.live_listener.transcription import _BACKOFF_MAX

        client = WyomingTranscriptionClient(url="tcp://localhost:10300", mic_name="kitchen")
        client._backoff = _BACKOFF_MAX / 2.0

        with (
            patch.object(client, "disconnect", new=AsyncMock()),
            patch("asyncio.sleep", new=AsyncMock()),
            patch("asyncio.open_connection", side_effect=OSError("still down")),
        ):
            for _ in range(10):
                await client._reconnect()

        assert client._backoff <= _BACKOFF_MAX

    async def test_context_manager(self) -> None:
        """Client should work as async context manager."""
        client = WyomingTranscriptionClient(url="tcp://localhost:10300", mic_name="kitchen")

        with (
            patch.object(client, "connect", new=AsyncMock()),
            patch.object(client, "disconnect", new=AsyncMock()),
        ):
            async with client as c:
                assert c is client
            client.disconnect.assert_called_once()


# ---------------------------------------------------------------------------
# WyomingTranscriptionClient — _do_transcribe (Wyoming protocol exchange)
# ---------------------------------------------------------------------------


class TestWyomingDoTranscribe:
    """Test the actual Wyoming protocol exchange using mocked reader/writer."""

    async def test_happy_path_full_protocol(self) -> None:
        """Full Wyoming protocol exchange: send events, receive transcript."""
        from wyoming.asr import Transcript

        client = WyomingTranscriptionClient(
            url="tcp://localhost:10300", mic_name="kitchen", min_confidence=0.3
        )

        fake_transcript = Transcript(
            text="full protocol test", language="en", context={"confidence": 0.95}
        ).event()

        mock_reader = AsyncMock(spec=asyncio.StreamReader)
        mock_writer = AsyncMock(spec=asyncio.StreamWriter)
        client._reader = mock_reader
        client._writer = mock_writer

        with patch(
            "butlers.connectors.live_listener.transcription.asyncio.wait_for",
            new=AsyncMock(return_value=fake_transcript),
        ):
            with patch("wyoming.event.async_write_event", new=AsyncMock()):
                result = await client._do_transcribe(_make_pcm(0.1))

        assert result is not None
        assert result.text == "full protocol test"
        assert result.language == "en"
        assert result.confidence == pytest.approx(0.95)
        assert result.duration_s > 0

    async def test_empty_response_discarded(self) -> None:
        """Empty transcript from Wyoming protocol is discarded."""
        from wyoming.asr import Transcript

        client = WyomingTranscriptionClient(
            url="tcp://localhost:10300", mic_name="kitchen", min_confidence=0.3
        )
        mock_reader = AsyncMock(spec=asyncio.StreamReader)
        mock_writer = AsyncMock(spec=asyncio.StreamWriter)
        client._reader = mock_reader
        client._writer = mock_writer

        fake_event = Transcript(text="", language="en").event()

        with patch(
            "butlers.connectors.live_listener.transcription.asyncio.wait_for",
            new=AsyncMock(return_value=fake_event),
        ):
            with patch("wyoming.event.async_write_event", new=AsyncMock()):
                result = await client._do_transcribe(_make_pcm())

        assert result is None

    async def test_low_confidence_discarded(self) -> None:
        """Low confidence response from Wyoming protocol is discarded."""
        from wyoming.asr import Transcript

        client = WyomingTranscriptionClient(
            url="tcp://localhost:10300", mic_name="kitchen", min_confidence=0.5
        )
        mock_reader = AsyncMock(spec=asyncio.StreamReader)
        mock_writer = AsyncMock(spec=asyncio.StreamWriter)
        client._reader = mock_reader
        client._writer = mock_writer

        fake_event = Transcript(text="maybe", language="en", context={"confidence": 0.2}).event()

        with patch(
            "butlers.connectors.live_listener.transcription.asyncio.wait_for",
            new=AsyncMock(return_value=fake_event),
        ):
            with patch("wyoming.event.async_write_event", new=AsyncMock()):
                result = await client._do_transcribe(_make_pcm())

        assert result is None

    async def test_server_closes_connection_raises(self) -> None:
        """None returned from async_read_event (server closed) raises EOFError."""
        client = WyomingTranscriptionClient(
            url="tcp://localhost:10300", mic_name="kitchen", min_confidence=0.3
        )
        mock_reader = AsyncMock(spec=asyncio.StreamReader)
        mock_writer = AsyncMock(spec=asyncio.StreamWriter)
        client._reader = mock_reader
        client._writer = mock_writer

        with patch(
            "butlers.connectors.live_listener.transcription.asyncio.wait_for",
            new=AsyncMock(return_value=None),  # Server closed
        ):
            with patch("wyoming.event.async_write_event", new=AsyncMock()):
                with pytest.raises(EOFError):
                    await client._do_transcribe(_make_pcm())


# ---------------------------------------------------------------------------
# WebSocketTranscriptionClient
# ---------------------------------------------------------------------------


class TestWebSocketTranscriptionClient:
    async def test_happy_path(self) -> None:
        """Successful WebSocket transcription via _do_transcribe returns result."""
        expected = TranscriptionResult(
            text="hi there", confidence=0.85, language="en", duration_s=0.1
        )
        client = WebSocketTranscriptionClient(
            url="ws://localhost:8765/transcribe",
            mic_name="bedroom",
            min_confidence=0.3,
        )
        client._ws = AsyncMock()
        client._session = AsyncMock()
        client._healthy = True

        with patch.object(client, "_do_transcribe", new=AsyncMock(return_value=expected)):
            result = await client.transcribe(_make_pcm())

        assert result is not None
        assert result.text == "hi there"
        assert result.confidence == pytest.approx(0.85)

    async def test_empty_response_discarded(self) -> None:
        """Empty text returned by _do_transcribe is passed through (None)."""
        client = WebSocketTranscriptionClient(
            url="ws://localhost:8765/transcribe", mic_name="bedroom"
        )
        client._ws = AsyncMock()
        client._session = AsyncMock()
        client._healthy = True

        with patch.object(client, "_do_transcribe", new=AsyncMock(return_value=None)):
            result = await client.transcribe(_make_pcm())

        assert result is None

    async def test_low_confidence_discarded(self) -> None:
        """_do_transcribe returning None propagates to transcribe()."""
        client = WebSocketTranscriptionClient(
            url="ws://localhost:8765/transcribe", mic_name="bedroom", min_confidence=0.5
        )
        client._ws = AsyncMock()
        client._session = AsyncMock()
        client._healthy = True

        with patch.object(client, "_do_transcribe", new=AsyncMock(return_value=None)):
            result = await client.transcribe(_make_pcm())

        assert result is None

    async def test_connection_failure_returns_none_and_sets_unhealthy(self) -> None:
        """When _ws is None and reconnect fails → None and unhealthy."""
        client = WebSocketTranscriptionClient(
            url="ws://localhost:8765/transcribe", mic_name="bedroom"
        )
        # _ws is None (not connected)
        assert client._ws is None

        with patch.object(client, "_reconnect", new=AsyncMock(return_value=False)):
            result = await client.transcribe(_make_pcm())

        assert result is None
        assert not client.healthy

    async def test_connection_error_triggers_retry(self) -> None:
        """On aiohttp.ClientConnectionError, reconnects and retries once."""
        import aiohttp

        client = WebSocketTranscriptionClient(
            url="ws://localhost:8765/transcribe", mic_name="bedroom", min_confidence=0.3
        )
        client._ws = AsyncMock()
        client._session = AsyncMock()
        client._healthy = True

        expected = TranscriptionResult(text="ok", confidence=0.9, language="en", duration_s=0.1)
        call_count = 0

        async def _failing_then_success(audio: bytes) -> TranscriptionResult | None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise aiohttp.ClientConnectionError("closed")
            return expected

        with patch.object(client, "_do_transcribe", side_effect=_failing_then_success):
            with patch.object(client, "_reconnect", new=AsyncMock(return_value=True)):
                result = await client.transcribe(_make_pcm())

        assert result is expected
        assert call_count == 2

    async def test_timeout_returns_none(self) -> None:
        """TimeoutError → None and unhealthy."""
        client = WebSocketTranscriptionClient(
            url="ws://localhost:8765/transcribe", mic_name="bedroom"
        )
        client._ws = AsyncMock()
        client._session = AsyncMock()
        client._healthy = True

        with patch.object(client, "_do_transcribe", side_effect=TimeoutError()):
            result = await client.transcribe(_make_pcm())

        assert result is None
        assert not client.healthy

    async def test_connect_creates_ws(self) -> None:
        """connect() should create a WebSocket connection."""

        mock_ws = AsyncMock()
        mock_session = MagicMock()
        mock_session.ws_connect = AsyncMock(return_value=mock_ws)

        client = WebSocketTranscriptionClient(
            url="ws://localhost:8765/transcribe", mic_name="bedroom"
        )
        client._session = mock_session  # inject existing session

        await client.connect()

        assert client._ws is mock_ws
        assert client.healthy

    async def test_connect_creates_session_if_needed(self) -> None:
        """connect() creates a new aiohttp.ClientSession when none exists."""

        mock_ws = AsyncMock()
        mock_session = MagicMock()
        mock_session.ws_connect = AsyncMock(return_value=mock_ws)

        client = WebSocketTranscriptionClient(
            url="ws://localhost:8765/transcribe", mic_name="bedroom"
        )
        assert client._session is None

        with patch("aiohttp.ClientSession", return_value=mock_session):
            await client.connect()

        assert client._session is mock_session
        assert client._ws is mock_ws

    async def test_do_transcribe_streams_and_parses_response(self) -> None:
        """_do_transcribe streams audio chunks and parses JSON transcript."""
        import aiohttp

        mock_ws = AsyncMock()
        mock_ws.send_bytes = AsyncMock()
        mock_ws.send_str = AsyncMock()

        ws_msg = MagicMock()
        ws_msg.type = aiohttp.WSMsgType.TEXT
        ws_msg.data = json.dumps({"text": "streaming test", "confidence": 0.88, "language": "en"})

        mock_ws.receive = AsyncMock(return_value=ws_msg)

        client = WebSocketTranscriptionClient(
            url="ws://localhost:8765/transcribe",
            mic_name="bedroom",
            min_confidence=0.3,
        )
        client._ws = mock_ws

        # Patch asyncio.wait_for at the module level to return the mock message
        with patch(
            "butlers.connectors.live_listener.transcription.asyncio.wait_for",
            new=AsyncMock(return_value=ws_msg),
        ):
            result = await client._do_transcribe(_make_pcm())

        assert result is not None
        assert result.text == "streaming test"
        assert result.confidence == pytest.approx(0.88)
        # Verify audio was streamed (send_bytes called at least once)
        mock_ws.send_bytes.assert_called()
        # Verify end signal was sent
        mock_ws.send_str.assert_called()


# ---------------------------------------------------------------------------
# HttpTranscriptionClient
# ---------------------------------------------------------------------------


class TestHttpTranscriptionClient:
    async def test_happy_path(self) -> None:
        """Successful HTTP POST returns TranscriptionResult."""

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={"text": "http result", "confidence": 0.88, "language": "en"}
        )
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_response)

        client = HttpTranscriptionClient(
            url="http://localhost:8080/transcribe",
            mic_name="living_room",
            min_confidence=0.3,
        )
        client._session = mock_session
        client._healthy = True

        result = await client.transcribe(_make_pcm())

        assert result is not None
        assert result.text == "http result"
        assert result.confidence == pytest.approx(0.88)
        assert result.language == "en"
        assert result.duration_s > 0

    async def test_empty_text_discarded(self) -> None:
        """Empty text response is discarded."""

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={"text": "", "confidence": 0.9, "language": "en"}
        )
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_response)

        client = HttpTranscriptionClient(
            url="http://localhost:8080/transcribe", mic_name="living_room"
        )
        client._session = mock_session
        client._healthy = True

        result = await client.transcribe(_make_pcm())
        assert result is None

    async def test_low_confidence_discarded(self) -> None:
        """Low confidence result is discarded."""

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={"text": "something", "confidence": 0.05, "language": "en"}
        )
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_response)

        client = HttpTranscriptionClient(
            url="http://localhost:8080/transcribe", mic_name="living_room", min_confidence=0.5
        )
        client._session = mock_session
        client._healthy = True

        result = await client.transcribe(_make_pcm())
        assert result is None

    async def test_http_error_returns_none(self) -> None:
        """Non-200 HTTP status returns None and marks client unhealthy."""
        mock_response = AsyncMock()
        mock_response.status = 503
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_response)

        client = HttpTranscriptionClient(
            url="http://localhost:8080/transcribe", mic_name="living_room"
        )
        client._session = mock_session
        client._healthy = True

        result = await client.transcribe(_make_pcm())
        assert result is None
        assert not client.healthy

    async def test_connection_error_returns_none(self) -> None:
        """Connection error returns None and marks client unhealthy."""
        import aiohttp

        mock_response = AsyncMock()
        mock_response.__aenter__ = AsyncMock(side_effect=aiohttp.ClientConnectionError("refused"))
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_response)

        client = HttpTranscriptionClient(
            url="http://localhost:8080/transcribe", mic_name="living_room"
        )
        client._session = mock_session
        client._healthy = True

        result = await client.transcribe(_make_pcm())
        assert result is None
        assert not client.healthy

    async def test_timeout_returns_none(self) -> None:
        """Timeout returns None and marks client unhealthy."""
        mock_response = AsyncMock()
        mock_response.__aenter__ = AsyncMock(side_effect=TimeoutError())
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_response)

        client = HttpTranscriptionClient(
            url="http://localhost:8080/transcribe", mic_name="living_room"
        )
        client._session = mock_session
        client._healthy = True

        result = await client.transcribe(_make_pcm())
        assert result is None
        assert not client.healthy

    async def test_connect_creates_session(self) -> None:
        """connect() creates an aiohttp ClientSession."""
        import aiohttp

        client = HttpTranscriptionClient(
            url="http://localhost:8080/transcribe", mic_name="living_room"
        )

        mock_session = MagicMock(spec=aiohttp.ClientSession)
        with patch("aiohttp.ClientSession", return_value=mock_session):
            await client.connect()

        assert client._session is mock_session
        assert client.healthy

    async def test_disconnect_closes_session(self) -> None:
        """disconnect() closes the session."""
        import aiohttp

        mock_session = AsyncMock(spec=aiohttp.ClientSession)

        client = HttpTranscriptionClient(
            url="http://localhost:8080/transcribe", mic_name="living_room"
        )
        client._session = mock_session

        await client.disconnect()
        mock_session.close.assert_called_once()
        assert client._session is None

    async def test_posts_wav_content_type(self) -> None:
        """The HTTP request uses audio/wav content type with language header."""
        posted_headers: dict = {}

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={"text": "check headers", "confidence": 0.9, "language": "en"}
        )
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        def _capture_post(url: str, data: bytes, headers: dict, **kwargs: object) -> object:
            posted_headers.update(headers)
            return mock_response

        mock_session = AsyncMock()
        mock_session.post = MagicMock(side_effect=_capture_post)

        client = HttpTranscriptionClient(
            url="http://localhost:8080/transcribe",
            mic_name="living_room",
            language="fr",
        )
        client._session = mock_session
        client._healthy = True

        await client.transcribe(_make_pcm())

        assert posted_headers.get("Content-Type") == "audio/wav"
        assert posted_headers.get("X-Language") == "fr"

    async def test_context_manager(self) -> None:
        """Client should work as async context manager."""
        client = HttpTranscriptionClient(
            url="http://localhost:8080/transcribe", mic_name="living_room"
        )

        with (
            patch.object(client, "connect", new=AsyncMock()),
            patch.object(client, "disconnect", new=AsyncMock()),
        ):
            async with client as c:
                assert c is client
            client.disconnect.assert_called_once()


# ---------------------------------------------------------------------------
# create_transcription_client factory
# ---------------------------------------------------------------------------


class TestCreateTranscriptionClient:
    def test_wyoming_default(self) -> None:
        c = create_transcription_client(protocol="wyoming", url="tcp://localhost:10300")
        assert isinstance(c, WyomingTranscriptionClient)

    def test_wyoming_default_url(self) -> None:
        c = create_transcription_client(protocol="wyoming")
        assert isinstance(c, WyomingTranscriptionClient)
        assert c._port == 10300  # part of default URL

    def test_websocket(self) -> None:
        c = create_transcription_client(protocol="websocket", url="ws://localhost:8765/transcribe")
        assert isinstance(c, WebSocketTranscriptionClient)

    def test_http(self) -> None:
        c = create_transcription_client(protocol="http", url="http://localhost:8080/transcribe")
        assert isinstance(c, HttpTranscriptionClient)

    def test_websocket_requires_url(self) -> None:
        with pytest.raises(ValueError, match="url is required"):
            create_transcription_client(protocol="websocket")

    def test_http_requires_url(self) -> None:
        with pytest.raises(ValueError, match="url is required"):
            create_transcription_client(protocol="http")

    def test_invalid_protocol(self) -> None:
        with pytest.raises(ValueError):
            create_transcription_client(protocol="grpc", url="grpc://host:50051")  # type: ignore[arg-type]

    def test_mic_name_propagated(self) -> None:
        c = create_transcription_client(
            protocol="wyoming", url="tcp://localhost:10300", mic_name="kitchen"
        )
        assert c._mic_name == "kitchen"

    def test_language_propagated(self) -> None:
        c = create_transcription_client(
            protocol="wyoming", url="tcp://localhost:10300", language="de"
        )
        assert c._language == "de"

    def test_min_confidence_propagated_wyoming(self) -> None:
        c = create_transcription_client(
            protocol="wyoming", url="tcp://localhost:10300", min_confidence=0.7
        )
        assert isinstance(c, WyomingTranscriptionClient)
        assert c._min_confidence == pytest.approx(0.7)

    def test_enum_protocol_arg_wyoming(self) -> None:
        c = create_transcription_client(
            protocol=TranscriptionProtocol.WYOMING, url="tcp://localhost:10300"
        )
        assert isinstance(c, WyomingTranscriptionClient)

    def test_enum_protocol_arg_http(self) -> None:
        c = create_transcription_client(
            protocol=TranscriptionProtocol.HTTP, url="http://localhost:8080"
        )
        assert isinstance(c, HttpTranscriptionClient)

    def test_enum_protocol_arg_websocket(self) -> None:
        c = create_transcription_client(
            protocol=TranscriptionProtocol.WEBSOCKET, url="ws://localhost:8765"
        )
        assert isinstance(c, WebSocketTranscriptionClient)
