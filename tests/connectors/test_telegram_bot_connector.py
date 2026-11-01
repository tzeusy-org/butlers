"""Condensed Telegram bot connector tests — ingest.v1 contract only.

Verifies:
- ingest.v1 envelope production for text, channel post, photo/document messages
- Returns None for non-message updates (callback_query, service messages)
- Idempotency key format
- Photo/document attachment materialization: caption-only normalized_text,
  one blob per media id, replay produces zero additional blob puts, and a
  failed fetch degrades to an unavailable attachment + filtered_events row
  rather than dropping the message (bu-2jtfw.7)

[bu-35fm7]
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.connectors import telegram_bot, telegram_voice
from butlers.connectors.live_listener.transcription import (
    TranscriptionClient,
    TranscriptionResult,
)
from butlers.connectors.telegram_bot import (
    TelegramBotConnector,
    TelegramBotConnectorConfig,
    _classify_source_api_error,
)
from butlers.tools.switchboard.contracts import IngestAttachment, parse_ingest_envelope

_ENDPOINT = "telegram:bot:123456789"


class _FakeBlobStore:
    """Minimal BlobStore double that records every put() call."""

    def __init__(self) -> None:
        self.put_calls: list[tuple[bytes, str]] = []

    async def put(self, data: bytes, *, content_type: str, filename: str | None = None) -> str:
        self.put_calls.append((data, content_type))
        return f"s3://test-bucket/telegram/{len(self.put_calls)}.bin"

    async def get(self, storage_ref: str) -> bytes:  # pragma: no cover - unused in these tests
        raise NotImplementedError

    async def delete(self, storage_ref: str) -> None:  # pragma: no cover - unused
        raise NotImplementedError

    async def exists(self, storage_ref: str) -> bool:  # pragma: no cover - unused
        return True


def _telegram_get_file_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={"ok": True, "result": {"file_path": "photos/file_1.jpg"}},
        request=request,
    )


def _telegram_download_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=b"fake-jpeg-bytes", request=request)


async def _fake_telegram_get(
    url: str, params: dict[str, Any] | None = None, **_: Any
) -> httpx.Response:
    request = httpx.Request("GET", url, params=params)
    if "/getFile" in url:
        return _telegram_get_file_response(request)
    if "/file/bot" in url:
        return _telegram_download_response(request)
    raise AssertionError(f"unexpected Telegram API URL in test: {url}")


def _photo_update(update_id: int = 321, *, caption: str | None = "is this mold?") -> dict[str, Any]:
    message: dict[str, Any] = {
        "message_id": 10,
        "from": {"id": 987},
        "chat": {"id": 100},
        "photo": [
            {"file_id": "small_id", "file_unique_id": "u1", "width": 90, "height": 90},
            {"file_id": "large_id", "file_unique_id": "u2", "width": 800, "height": 600},
        ],
    }
    if caption is not None:
        message["caption"] = caption
    return {"update_id": update_id, "message": message}


@pytest.fixture
def connector() -> TelegramBotConnector:
    config = TelegramBotConnectorConfig(
        switchboard_mcp_url="http://localhost:41100/sse",
        provider="telegram",
        channel="telegram_bot",
        endpoint_identity=_ENDPOINT,
        telegram_token="test-token",
    )
    return TelegramBotConnector(config, cursor_pool=MagicMock())


async def test_text_message_schema_version(connector: TelegramBotConnector) -> None:
    """Text message envelope must carry schema_version='ingest.v1'."""
    update: dict[str, Any] = {
        "update_id": 123,
        "message": {
            "message_id": 1,
            "from": {"id": 987},
            "chat": {"id": 100},
            "text": "Hello Bot!",
        },
    }
    env = await connector._normalize_to_ingest_v1(update)
    assert env is not None
    assert env["schema_version"] == "ingest.v1"
    assert env["source"]["channel"] == "telegram_bot"
    assert env["source"]["provider"] == "telegram"


async def test_text_message_event_fields(connector: TelegramBotConnector) -> None:
    """Event fields map correctly from update."""
    update: dict[str, Any] = {
        "update_id": 456,
        "message": {
            "message_id": 7,
            "from": {"id": 777},
            "chat": {"id": 200},
            "text": "Test message",
        },
    }
    env = await connector._normalize_to_ingest_v1(update)
    assert env is not None
    assert env["event"]["external_event_id"] == "456"
    assert env["event"]["external_conversation_id"] == "telegram:200"
    assert env["event"]["reply_target_ref"] == "200:7"
    assert env["sender"]["identity"] == "777"
    assert "Hello Bot!" not in env["payload"]["normalized_text"]
    assert "Test message" in env["payload"]["normalized_text"]


async def test_two_messages_share_conversation_but_keep_distinct_reply_targets(
    connector: TelegramBotConnector,
) -> None:
    first = await connector._normalize_to_ingest_v1(
        {
            "update_id": 1,
            "message": {"message_id": 7, "from": {"id": 1}, "chat": {"id": 200}, "text": "one"},
        }
    )
    second = await connector._normalize_to_ingest_v1(
        {
            "update_id": 2,
            "message": {"message_id": 8, "from": {"id": 1}, "chat": {"id": 200}, "text": "two"},
        }
    )

    assert first is not None and second is not None
    assert first["event"]["external_conversation_id"] == second["event"]["external_conversation_id"]
    assert first["event"]["reply_target_ref"] != second["event"]["reply_target_ref"]


async def test_channel_post_produces_envelope(connector: TelegramBotConnector) -> None:
    """channel_post updates must produce an ingest.v1 envelope."""
    update: dict[str, Any] = {
        "update_id": 789,
        "channel_post": {
            "message_id": 5,
            "chat": {"id": 300},
            "text": "Channel announcement",
        },
    }
    env = await connector._normalize_to_ingest_v1(update)
    assert env is not None
    assert "Channel announcement" in env["payload"]["normalized_text"]


async def test_no_message_returns_none(connector: TelegramBotConnector) -> None:
    """callback_query updates (no message) must return None."""
    update: dict[str, Any] = {
        "update_id": 999,
        "callback_query": {"data": "btn_click"},
    }
    result = await connector._normalize_to_ingest_v1(update)
    assert result is None


async def test_service_message_returns_none(connector: TelegramBotConnector) -> None:
    """Service messages with no text/media must return None."""
    update: dict[str, Any] = {
        "update_id": 888,
        "message": {
            "message_id": 3,
            "chat": {"id": 150},
            "new_chat_members": [{"id": 42}],
        },
    }
    result = await connector._normalize_to_ingest_v1(update)
    assert result is None


async def test_idempotency_key_uses_chat_and_message_id(connector: TelegramBotConnector) -> None:
    """Idempotency key must follow 'tg:<chat_id>:<message_id>' format."""
    update: dict[str, Any] = {
        "update_id": 100,
        "message": {
            "message_id": 42,
            "from": {"id": 1},
            "chat": {"id": 999},
            "text": "idempotency test",
        },
    }
    env = await connector._normalize_to_ingest_v1(update)
    assert env is not None
    key = env["control"]["idempotency_key"]
    assert "tg:" in key
    assert "999" in key
    assert "42" in key


# ---------------------------------------------------------------------------
# Photo/document attachment materialization (bu-2jtfw.7)
# ---------------------------------------------------------------------------


async def test_photo_with_caption_normalized_text_is_caption(
    connector: TelegramBotConnector,
) -> None:
    """A photo's normalized_text is the caption verbatim, not a synthesized label."""
    connector._blob_store = _FakeBlobStore()
    connector._http_client.get = AsyncMock(side_effect=_fake_telegram_get)  # type: ignore[method-assign]

    env = await connector._normalize_to_ingest_v1(_photo_update(caption="is this mold?"))

    assert env is not None
    assert env["payload"]["normalized_text"] == "is this mold?"


async def test_photo_without_caption_normalized_text_is_empty_not_photo(
    connector: TelegramBotConnector,
) -> None:
    """A captionless photo's normalized_text is "", never a synthesized '[Photo]'."""
    connector._blob_store = _FakeBlobStore()
    connector._http_client.get = AsyncMock(side_effect=_fake_telegram_get)  # type: ignore[method-assign]

    env = await connector._normalize_to_ingest_v1(_photo_update(caption=None))

    assert env is not None
    assert env["payload"]["normalized_text"] == ""
    assert "Photo" not in env["payload"]["normalized_text"]


async def test_photo_produces_one_attachment_with_resolvable_storage_ref(
    connector: TelegramBotConnector,
) -> None:
    """A photo update yields exactly one IngestAttachment with a real storage_ref."""
    blob_store = _FakeBlobStore()
    connector._blob_store = blob_store
    connector._http_client.get = AsyncMock(side_effect=_fake_telegram_get)  # type: ignore[method-assign]

    env = await connector._normalize_to_ingest_v1(_photo_update())

    assert env is not None
    attachments = env["payload"]["attachments"]
    assert len(attachments) == 1
    att = attachments[0]
    assert att["media_type"] == "image/jpeg"
    assert att["storage_ref"].startswith("s3://")
    assert att["width"] == 800
    assert att["height"] == 600
    assert len(blob_store.put_calls) == 1
    # Must round-trip through the real Switchboard contract, not just look
    # dict-shaped — this is what ingest_v1() actually validates the envelope
    # against.
    IngestAttachment(**att)


async def test_replay_same_photo_update_produces_zero_additional_puts(
    connector: TelegramBotConnector,
) -> None:
    """Replaying the same update must not re-put the already-materialized blob."""
    blob_store = _FakeBlobStore()
    connector._blob_store = blob_store
    connector._http_client.get = AsyncMock(side_effect=_fake_telegram_get)  # type: ignore[method-assign]

    update = _photo_update()
    first = await connector._normalize_to_ingest_v1(update)
    second = await connector._normalize_to_ingest_v1(update)

    assert first is not None
    assert second is not None
    assert len(blob_store.put_calls) == 1
    assert (
        first["payload"]["attachments"][0]["storage_ref"]
        == second["payload"]["attachments"][0]["storage_ref"]
    )


async def test_photo_fetch_failure_preserves_caption_and_records_filtered_event(
    connector: TelegramBotConnector,
) -> None:
    """A getFile failure still ingests the message (caption preserved) and audits it."""
    connector._blob_store = _FakeBlobStore()

    async def _failing_get(url: str, **_: Any) -> httpx.Response:
        request = httpx.Request("GET", url)
        return httpx.Response(
            404, json={"ok": False, "description": "file not found"}, request=request
        )

    connector._http_client.get = AsyncMock(side_effect=_failing_get)  # type: ignore[method-assign]

    env = await connector._normalize_to_ingest_v1(_photo_update(caption="is this mold?"))

    assert env is not None
    assert env["payload"]["normalized_text"] == "is this mold?"
    attachments = env["payload"]["attachments"]
    assert len(attachments) == 1
    assert attachments[0]["storage_ref"] is None
    # An "unavailable" attachment must still satisfy the real Switchboard
    # contract (size_bytes is a required non-nullable int) — a validation
    # failure here would silently drop the whole message, caption included.
    IngestAttachment(**attachments[0])

    rows = connector._filtered_event_buffer._rows
    assert len(rows) == 1
    assert rows[0][8] == "error"


async def test_document_produces_one_attachment(connector: TelegramBotConnector) -> None:
    """A document update materializes its own blob via the same media path."""
    blob_store = _FakeBlobStore()
    connector._blob_store = blob_store
    connector._http_client.get = AsyncMock(side_effect=_fake_telegram_get)  # type: ignore[method-assign]

    update: dict[str, Any] = {
        "update_id": 654,
        "message": {
            "message_id": 11,
            "from": {"id": 987},
            "chat": {"id": 100},
            "document": {
                "file_id": "doc_id",
                "file_name": "report.pdf",
                "mime_type": "application/pdf",
            },
        },
    }
    env = await connector._normalize_to_ingest_v1(update)

    assert env is not None
    assert env["payload"]["normalized_text"] == ""
    attachments = env["payload"]["attachments"]
    assert len(attachments) == 1
    assert attachments[0]["media_type"] == "application/pdf"
    assert attachments[0]["storage_ref"].startswith("s3://")
    assert len(blob_store.put_calls) == 1
    IngestAttachment(**attachments[0])


# ---------------------------------------------------------------------------
# Filtered-content privacy tier (bu-glbjx)
#
# Content the connector deliberately does NOT submit (status='filtered':
# connector-rule block, global skip) persists a bounded preview only — the
# full raw update payload MUST NOT be retained (full_payload.payload.raw == {}).
# Errored content (status='error') is exempt and keeps its payload for
# diagnosis and replay.
# ---------------------------------------------------------------------------


def _text_update(update_id: int = 555) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 1,
            "from": {"id": 987},
            "chat": {"id": 100},
            "text": "Hello Bot!",
        },
    }


async def test_connector_rule_block_persists_no_raw_payload(
    connector: TelegramBotConnector,
) -> None:
    """A connector-scope policy block records raw={} while keeping the preview."""
    from butlers.ingestion_policy import PolicyDecision

    connector._ingestion_policy.evaluate = MagicMock(  # type: ignore[method-assign]
        return_value=PolicyDecision(action="block", matched_rule_type="sender_domain")
    )
    await connector._process_update(_text_update())

    rows = connector._filtered_event_buffer._rows
    assert len(rows) == 1
    row = rows[0]
    assert row[8] == "filtered"
    assert row[9]["payload"]["raw"] == {}
    # Preview is retained so the filtered row stays operationally useful.
    assert row[6]


async def test_global_skip_persists_no_raw_payload(connector: TelegramBotConnector) -> None:
    """A global-scope skip records raw={} while keeping the preview."""
    from butlers.ingestion_policy import PolicyDecision

    connector._ingestion_policy.evaluate = MagicMock(  # type: ignore[method-assign]
        return_value=PolicyDecision(action="pass_through")
    )
    connector._global_ingestion_policy.evaluate = MagicMock(  # type: ignore[method-assign]
        return_value=PolicyDecision(action="skip", matched_rule_type="keyword")
    )
    await connector._process_update(_text_update())

    rows = connector._filtered_event_buffer._rows
    assert len(rows) == 1
    row = rows[0]
    assert row[8] == "filtered"
    assert row[9]["payload"]["raw"] == {}


async def test_submission_error_retains_raw_payload(connector: TelegramBotConnector) -> None:
    """Errored content (status='error') is exempt: the raw payload is retained."""
    from butlers.ingestion_policy import PolicyDecision

    connector._ingestion_policy.evaluate = MagicMock(  # type: ignore[method-assign]
        return_value=PolicyDecision(action="pass_through")
    )
    connector._global_ingestion_policy.evaluate = MagicMock(  # type: ignore[method-assign]
        return_value=PolicyDecision(action="pass_through")
    )
    connector._submit_to_ingest = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]

    update = _text_update()
    await connector._process_update(update)

    rows = connector._filtered_event_buffer._rows
    assert len(rows) == 1
    row = rows[0]
    assert row[8] == "error"
    assert row[9]["payload"]["raw"] == update


# ---------------------------------------------------------------------------
# Source API health classification (bu-q2m3n)
# ---------------------------------------------------------------------------


def _telegram_http_error(status_code: int, description: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://api.telegram.org/bottoken/getUpdates")
    response = httpx.Response(
        status_code,
        json={"ok": False, "error_code": status_code, "description": description},
        request=request,
    )
    return httpx.HTTPStatusError("error", request=request, response=response)


@pytest.mark.parametrize(
    ("status_code", "description", "is_auth_revocation"),
    [
        (401, "Unauthorized", True),
        (403, "Forbidden: bot was blocked by the user", False),
        (409, "Conflict: terminated by other getUpdates request", False),
        (429, "Too Many Requests", False),
        (503, "Service Unavailable", False),
    ],
)
def test_classify_source_api_error_distinguishes_invalid_bot_token_from_transient_failure(
    status_code: int, description: str, is_auth_revocation: bool
) -> None:
    """Telegram 401 is credential failure; service failures remain recoverable."""
    classified, detail = _classify_source_api_error(_telegram_http_error(status_code, description))

    assert classified is is_auth_revocation
    assert description in detail


def test_telegram_health_reports_auth_failure_as_error_and_api_failure_as_degraded(
    connector: TelegramBotConnector,
) -> None:
    connector._record_source_api_failure(_telegram_http_error(503, "Service Unavailable"))

    assert connector._get_health_state() == ("degraded", "HTTP 503: Service Unavailable")

    connector._record_source_api_failure(_telegram_http_error(401, "Unauthorized"))

    assert connector._get_health_state() == ("error", "HTTP 401: Unauthorized")


async def test_static_identity_env_cannot_relabel_real_bot_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operator identity settings cannot relabel the actual ingest producer."""
    monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://switchboard.test/sse")
    monkeypatch.setenv("CONNECTOR_PROVIDER", "ignored-provider")
    monkeypatch.setenv("CONNECTOR_CHANNEL", "ignored-channel")
    config = TelegramBotConnectorConfig.from_env()
    config.endpoint_identity = _ENDPOINT
    runtime = TelegramBotConnector(config, cursor_pool=MagicMock())
    update = {
        "update_id": 123,
        "message": {
            "message_id": 1,
            "from": {"id": 987},
            "chat": {"id": 100},
            "text": "Hello Bot!",
        },
    }
    first = await runtime._normalize_to_ingest_v1(update)
    replay = await runtime._normalize_to_ingest_v1(update)
    assert first is not None and replay is not None
    assert first["source"] == {
        "channel": "telegram_bot",
        "provider": "telegram",
        "endpoint_identity": _ENDPOINT,
    }
    assert first["event"]["external_event_id"] == "123"
    assert first["control"]["idempotency_key"] == replay["control"]["idempotency_key"]
    assert first["event"]["external_conversation_id"] == "telegram:100"


# ---------------------------------------------------------------------------
# Owner voice-note transcription (bu-r0dsz; telegram-voice-transcription spec)
# Audio and transcripts below are synthetic markers, never real owner content.
# ---------------------------------------------------------------------------

_AUDIO = b"SYNTHETIC-OGG-AUDIO-MARKER"
_PCM = b"SYNTHETIC-PCM-MARKER"
_TRANSCRIPT = "synthetic transcript words"


class _FakeTranscriptionClient(TranscriptionClient):
    """Records calls; optional gate holds each call open to observe overlap."""

    def __init__(
        self,
        result: TranscriptionResult | None = None,
        *,
        healthy: bool = True,
        gate: Any = None,
    ) -> None:
        super().__init__(mic_name="test")
        self._result = result
        self._healthy = healthy
        self._gate = gate
        self.calls: list[bytes] = []
        self.active = 0
        self.max_active = 0

    async def connect(self) -> None:  # pragma: no cover - lifecycle not exercised here
        pass

    async def disconnect(self) -> None:  # pragma: no cover
        pass

    async def transcribe(self, audio: bytes) -> TranscriptionResult | None:
        self.calls.append(audio)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self._gate is not None:
                await self._gate.wait()
            return self._result
        finally:
            self.active -= 1


def _ok_result() -> TranscriptionResult:
    return TranscriptionResult(text=_TRANSCRIPT, confidence=0.91, language="en", duration_s=3.0)


def _voice_update(
    update_id: int = 700, *, duration: int = 5, file_size: int | None = 4096, **extra: Any
) -> dict[str, Any]:
    voice: dict[str, Any] = {"file_id": "voice_id", "file_unique_id": "v1", "duration": duration}
    if file_size is not None:
        voice["file_size"] = file_size
    message = {"message_id": update_id, "from": {"id": 987}, "chat": {"id": 100}, "voice": voice}
    return {"update_id": update_id, "message": {**message, **extra}}


class _TelegramFiles:
    """httpx MockTransport for getFile + file download; counts every Telegram request."""

    def __init__(self, body: bytes = _AUDIO, *, fail: bool = False) -> None:
        self.body = body
        self.fail = fail
        self.requests: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request.url.path)
        if self.fail:
            return httpx.Response(502, request=request)
        if request.url.path.endswith("/getFile"):
            return httpx.Response(200, json={"ok": True, "result": {"file_path": "voice/f.oga"}})
        return httpx.Response(200, content=self.body)


def _voice_connector(
    monkeypatch: pytest.MonkeyPatch,
    client: _FakeTranscriptionClient,
    *,
    enabled: bool = True,
    owner: bool = True,
    files: _TelegramFiles | None = None,
    pcm: bytes | None = _PCM,
) -> tuple[TelegramBotConnector, _TelegramFiles, AsyncMock]:
    config = TelegramBotConnectorConfig(
        switchboard_mcp_url="http://localhost:41100/sse",
        endpoint_identity=_ENDPOINT,
        telegram_token="test-token",
        voice_transcription_enabled=enabled,
        voice_transcription_url="tcp://stt.test:10300",
    )
    runtime = TelegramBotConnector(
        config, db_pool=MagicMock(), cursor_pool=MagicMock(), transcription_client=client
    )
    files = files or _TelegramFiles()
    runtime._http_client = httpx.AsyncClient(transport=httpx.MockTransport(files))
    monkeypatch.setattr(
        telegram_bot,
        "resolve_owner_channel_via_definer",
        AsyncMock(return_value=(MagicMock(), True) if owner else None),
    )
    transcode = AsyncMock(return_value=pcm)
    monkeypatch.setattr(telegram_bot, "transcode_to_pcm", transcode)
    return runtime, files, transcode


async def test_voice_transcription_disabled_keeps_descriptor_and_makes_no_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeTranscriptionClient(_ok_result())
    runtime, files, transcode = _voice_connector(monkeypatch, client, enabled=False)

    env = await runtime._normalize_to_ingest_v1(_voice_update())

    assert env is not None
    assert env["payload"]["normalized_text"] == "[Voice message]"
    assert env["payload"]["transcription"]["status"] == "disabled"
    assert files.requests == [] and client.calls == []
    transcode.assert_not_awaited()
    telegram_bot.resolve_owner_channel_via_definer.assert_not_awaited()  # type: ignore[attr-defined]


async def test_owner_voice_note_transcript_becomes_normalized_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeTranscriptionClient(_ok_result())
    runtime, files, transcode = _voice_connector(monkeypatch, client)

    env = await runtime._normalize_to_ingest_v1(_voice_update())

    assert env is not None
    assert env["payload"]["normalized_text"] == _TRANSCRIPT
    assert env["payload"]["transcription"] == {
        "source_media": "voice",
        "status": "transcribed",
        "confidence": 0.91,
        "language": "en",
        "stt_backend": "wyoming",
    }
    assert env["payload"]["raw"]["message"]["voice"]["file_id"] == "voice_id"
    assert "attachments" not in env["payload"] and "untrusted_spans" not in env["payload"]
    transcode.assert_awaited_once()
    assert transcode.await_args.args == (_AUDIO,)
    assert client.calls == [_PCM]
    # The real Switchboard contract accepts the new optional field.
    assert parse_ingest_envelope(env).payload.transcription.status == "transcribed"


@pytest.mark.parametrize(
    ("owner", "extra", "status"),
    [
        (False, {}, "not_owner"),
        (True, {"forward_origin": {"type": "hidden_user", "sender_user_name": "x"}}, "forwarded"),
    ],
)
async def test_ineligible_voice_note_is_never_downloaded(
    monkeypatch: pytest.MonkeyPatch, owner: bool, extra: dict[str, Any], status: str
) -> None:
    client = _FakeTranscriptionClient(_ok_result())
    runtime, files, _ = _voice_connector(monkeypatch, client, owner=owner)

    env = await runtime._normalize_to_ingest_v1(_voice_update(**extra))

    assert env is not None
    assert env["payload"]["normalized_text"] == "[Voice message]"
    assert env["payload"]["transcription"]["status"] == status
    assert env["payload"]["transcription"]["stt_backend"] is None
    assert files.requests == [] and client.calls == []


@pytest.mark.parametrize(
    ("update_kwargs", "body", "requests"),
    [
        ({"file_size": telegram_voice.MAX_VOICE_BYTES + 1}, _AUDIO, 0),
        ({"duration": 121}, _AUDIO, 0),
        ({"file_size": None}, b"x" * (telegram_voice.MAX_VOICE_BYTES + 1), 2),
    ],
    ids=["oversize-metadata", "overlong", "post-download-backstop"],
)
async def test_voice_bounds_reject_oversize_and_overlong_notes(
    monkeypatch: pytest.MonkeyPatch, update_kwargs: dict[str, Any], body: bytes, requests: int
) -> None:
    client = _FakeTranscriptionClient(_ok_result())
    runtime, files, transcode = _voice_connector(monkeypatch, client, files=_TelegramFiles(body))

    env = await runtime._normalize_to_ingest_v1(_voice_update(**update_kwargs))

    assert env is not None
    assert env["payload"]["normalized_text"] == "[Voice message]"
    expected = "overlong" if "duration" in update_kwargs else "oversize"
    assert env["payload"]["transcription"]["status"] == expected
    assert len(files.requests) == requests
    transcode.assert_not_awaited()
    assert client.calls == []


@pytest.mark.parametrize(
    ("fail_download", "pcm", "result", "healthy", "status"),
    [
        (True, _PCM, None, True, "download_failed"),
        (False, None, None, True, "transcode_failed"),
        (False, _PCM, None, False, "stt_unavailable"),
        (False, _PCM, None, True, "empty_or_low_confidence"),
        (False, _PCM, TranscriptionResult("  ", 0.9, "en", 1.0), True, "empty_or_low_confidence"),
    ],
)
async def test_voice_failures_fall_back_to_descriptor_with_named_status(
    monkeypatch: pytest.MonkeyPatch,
    fail_download: bool,
    pcm: bytes | None,
    result: TranscriptionResult | None,
    healthy: bool,
    status: str,
) -> None:
    client = _FakeTranscriptionClient(result, healthy=healthy)
    files = _TelegramFiles(fail=fail_download)
    runtime, files, _ = _voice_connector(monkeypatch, client, files=files, pcm=pcm)

    env = await runtime._normalize_to_ingest_v1(_voice_update())

    assert env is not None
    assert env["payload"]["normalized_text"] == "[Voice message]"
    outcome = env["payload"]["transcription"]
    assert outcome["status"] == status
    assert outcome["confidence"] is None and outcome["language"] is None
    if fail_download:
        assert len(files.requests) == 2  # one bounded retry of getFile


async def test_voice_audio_and_transcript_never_reach_logs_or_envelope_extras(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("DEBUG")
    ok_runtime, _, _ = _voice_connector(monkeypatch, _FakeTranscriptionClient(_ok_result()))
    ok = await ok_runtime._normalize_to_ingest_v1(_voice_update(701))
    failing = _FakeTranscriptionClient(None, healthy=False)
    bad_runtime, _, _ = _voice_connector(monkeypatch, failing, files=_TelegramFiles(fail=True))
    bad = await bad_runtime._normalize_to_ingest_v1(_voice_update(702))

    assert ok is not None and bad is not None
    logged = "".join(r.getMessage() for r in caplog.records)
    for marker in (_AUDIO.decode(), _PCM.decode(), _TRANSCRIPT):
        assert marker not in logged
    # The connector's own warnings never echo the token-bearing file URL.
    ours = "".join(r.getMessage() for r in caplog.records if r.name.startswith("butlers."))
    assert "test-token" not in ours
    for env in (ok, bad):
        assert _AUDIO.decode() not in repr(env) and _PCM.decode() not in repr(env)
        assert "attachments" not in env["payload"]


async def test_ffmpeg_transcode_uses_argument_list_pipes_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    seen: dict[str, Any] = {}

    class _Proc:
        def __init__(self, hang: bool) -> None:
            self.hang = hang
            self.returncode: int | None = 0
            self.killed = False

        async def communicate(self, data: bytes) -> tuple[bytes, bytes]:
            seen["stdin"] = data
            if self.hang:
                await asyncio.sleep(10)
            return _PCM, b"stderr"

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

        async def wait(self) -> int:
            return -9

    procs: list[_Proc] = []

    async def fake_exec(*args: str, **kwargs: Any) -> _Proc:
        seen["args"], seen["kwargs"] = args, kwargs
        procs.append(_Proc(hang=len(procs) == 1))
        return procs[-1]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    assert await telegram_voice.transcode_to_pcm(_AUDIO, timeout_s=5) == _PCM
    assert seen["args"] == telegram_voice.FFMPEG_PCM_ARGS
    assert seen["args"][0] == "ffmpeg" and "pipe:0" in seen["args"] and "pipe:1" in seen["args"]
    assert "shell" not in seen["kwargs"]
    assert seen["kwargs"]["stdin"] == seen["kwargs"]["stdout"] == asyncio.subprocess.PIPE
    assert seen["stdin"] == _AUDIO

    assert await telegram_voice.transcode_to_pcm(_AUDIO, timeout_s=0.01) is None
    assert procs[1].killed


async def test_concurrent_voice_notes_serialize_on_shared_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    gate = asyncio.Event()
    client = _FakeTranscriptionClient(_ok_result(), gate=gate)
    runtime, _, _ = _voice_connector(monkeypatch, client)

    tasks = [
        asyncio.create_task(runtime._normalize_to_ingest_v1(_voice_update(800 + i)))
        for i in range(3)
    ]
    for _ in range(20):
        await asyncio.sleep(0)
    assert client.max_active == 1
    gate.set()
    envs = await asyncio.gather(*tasks)

    assert client.max_active == 1 and len(client.calls) == 3
    assert all(env and env["payload"]["normalized_text"] == _TRANSCRIPT for env in envs)


def test_voice_transcription_env_defaults_off_and_requires_url_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://switchboard.test/sse")
    config = TelegramBotConnectorConfig.from_env()
    assert config.voice_transcription_enabled is False
    assert config.voice_max_duration_s == 120

    monkeypatch.setenv("CONNECTOR_TELEGRAM_VOICE_TRANSCRIPTION_ENABLED", "true")
    with pytest.raises(ValueError, match="CONNECTOR_TELEGRAM_VOICE_TRANSCRIPTION_URL"):
        TelegramBotConnectorConfig.from_env()
