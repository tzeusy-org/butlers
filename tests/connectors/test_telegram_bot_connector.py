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

from butlers.connectors.telegram_bot import (
    TelegramBotConnector,
    TelegramBotConnectorConfig,
    _classify_source_api_error,
)
from butlers.tools.switchboard.routing.contracts import IngestAttachment

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
    assert env["sender"]["identity"] == "777"
    assert "Hello Bot!" not in env["payload"]["normalized_text"]
    assert "Test message" in env["payload"]["normalized_text"]


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
