"""Unit tests for routing contract models (IngestEnvelopeV1, RouteEnvelopeV1, etc.)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from butlers.tools.switchboard.routing.contracts import (
    _ALLOWED_PROVIDERS_BY_CHANNEL,
    RouteInputV1,
    parse_ingest_envelope,
    parse_notify_request,
    parse_route_envelope,
)

pytestmark = pytest.mark.unit


def _build_valid_ingest_envelope(
    *,
    text: str = "Test message",
    idempotency_key: str | None = None,
    schema_version: str = "ingest.v1",
    channel: str = "telegram_bot",
    provider: str = "telegram",
    endpoint_identity: str = "bot_test",
    sender_identity: str = "user123",
    external_event_id: str | None = None,
    observed_at: str | None = None,
) -> dict:
    if external_event_id is None:
        external_event_id = f"event-{uuid.uuid4()}"
    if observed_at is None:
        observed_at = datetime.now(UTC).isoformat()
    envelope = {
        "schema_version": schema_version,
        "source": {
            "channel": channel,
            "provider": provider,
            "endpoint_identity": endpoint_identity,
        },
        "event": {"external_event_id": external_event_id, "observed_at": observed_at},
        "sender": {"identity": sender_identity},
        "payload": {"raw": {"text": text}, "normalized_text": text},
    }
    if idempotency_key:
        envelope["control"] = {"idempotency_key": idempotency_key}
    return envelope


def _build_valid_route_envelope(
    *,
    prompt: str = "Do health check",
    context: str | None = None,
    conversation_history: str | None = None,
    schema_version: str = "route.v1",
    source_channel: str = "telegram_bot",
    request_id: str | None = None,
) -> dict:
    if request_id is None:
        request_id = "018f6f4e-5b3b-7b2d-9c2f-7b7b6b6b6b6b"
    input_payload: dict = {"prompt": prompt}
    if context is not None:
        input_payload["context"] = context
    if conversation_history is not None:
        input_payload["conversation_history"] = conversation_history
    return {
        "schema_version": schema_version,
        "request_context": {
            "request_id": request_id,
            "received_at": datetime.now(UTC).isoformat(),
            "source_channel": source_channel,
            "source_endpoint_identity": "switchboard",
            "source_sender_identity": "user123",
        },
        "input": input_payload,
    }


def test_ingest_attachment_contract():
    """Attachment: absent/empty/valid/lazy accepted; missing required, negative size,
    width=0, empty string, extra fields rejected; frozen."""
    # Absent
    assert parse_ingest_envelope(_build_valid_ingest_envelope()).payload.attachments is None

    # Empty list
    e = _build_valid_ingest_envelope()
    e["payload"]["attachments"] = []
    assert parse_ingest_envelope(e).payload.attachments == ()

    # Valid attachment with all fields
    e2 = _build_valid_ingest_envelope()
    e2["payload"]["attachments"] = [
        {
            "media_type": "image/jpeg",
            "storage_ref": "s3://bucket/photo.jpg",
            "size_bytes": 1024000,
            "filename": "vacation.jpg",
            "width": 1920,
            "height": 1080,
        }
    ]
    att = parse_ingest_envelope(e2).payload.attachments[0]
    assert att.media_type == "image/jpeg" and att.size_bytes == 1024000 and att.width == 1920

    # Multiple attachments
    e3 = _build_valid_ingest_envelope()
    e3["payload"]["attachments"] = [
        {"media_type": "image/png", "storage_ref": "s3://bucket/img1.png", "size_bytes": 500000},
        {
            "media_type": "application/pdf",
            "storage_ref": "s3://bucket/doc.pdf",
            "size_bytes": 2048000,
            "filename": "report.pdf",
        },
    ]
    assert len(parse_ingest_envelope(e3).payload.attachments) == 2

    # Lazy: storage_ref can be None if source identifiers present
    e4 = _build_valid_ingest_envelope()
    e4["payload"]["attachments"] = [
        {
            "media_type": "text/csv",
            "size_bytes": 1024,
            "filename": "data.csv",
            "source_message_id": "msg123",
            "source_attachment_id": "att456",
        }
    ]
    la = parse_ingest_envelope(e4).payload.attachments[0]
    assert la.storage_ref is None and la.source_message_id == "msg123"

    # Zero size accepted
    e5 = _build_valid_ingest_envelope()
    e5["payload"]["attachments"] = [
        {"media_type": "text/plain", "storage_ref": "s3://bucket/empty.txt", "size_bytes": 0}
    ]
    assert parse_ingest_envelope(e5).payload.attachments[0].size_bytes == 0

    # Frozen: mutation raises
    with pytest.raises(ValidationError):
        att.size_bytes = 9999  # type: ignore[misc]

    # Rejections: missing required, negative size, width=0, empty media_type, extra fields
    for bad_att in [
        {"size_bytes": 1024},
        {"media_type": "image/jpeg", "storage_ref": "s3://b/p.jpg", "size_bytes": -1000},
        {
            "media_type": "image/jpeg",
            "storage_ref": "s3://b/p.jpg",
            "size_bytes": 1024,
            "width": 0,
            "height": 1080,
        },
        {"media_type": "", "storage_ref": "s3://b/f", "size_bytes": 1024},
        {
            "media_type": "image/jpeg",
            "storage_ref": "s3://b/p.jpg",
            "size_bytes": 1024,
            "unknown_field": "bad",
        },
    ]:
        eb = _build_valid_ingest_envelope()
        eb["payload"]["attachments"] = [bad_att]
        with pytest.raises(ValidationError):
            parse_ingest_envelope(eb)


def test_channel_provider_pairing_and_registry():
    """Valid channel/provider pairs accepted; invalid rejected; allowed providers registry correct.
    """
    # Valid pairs
    valid_pairs = [
        ("voice", "live-listener", "live-listener:mic:kitchen", "mic-user"),
        ("whatsapp_user_client", "whatsapp", "whatsapp:1234567890", "1234567890@s.whatsapp.net"),
        ("google_calendar", "google_calendar", "gcal:user@example.com", "user@example.com"),
        ("spotify_user_client", "spotify", "spotify:user123", "user123"),
        ("owntracks", "owntracks", "owntracks:device123", "user123"),
        (
            "home_assistant",
            "home_assistant",
            "ha:http://homeassistant.local:8123",
            "ha:light.living_room",
        ),
        ("gaming", "steam", "steam:76561198000000001", "steam:76561198000000001"),
        (
            "google_drive",
            "google_drive",
            "google_drive:user:user@example.com",
            "google_drive:user:user@example.com",
        ),
    ]
    for channel, provider, endpoint, sender in valid_pairs:
        env = _build_valid_ingest_envelope(
            channel=channel, provider=provider, endpoint_identity=endpoint, sender_identity=sender
        )
        parsed = parse_ingest_envelope(env)
        assert parsed.source.channel == channel and parsed.source.provider == provider

    # Invalid pairs
    invalid_pairs = [
        ("voice", "internal", "live-listener:mic:kitchen", "mic-user"),
        ("whatsapp_user_client", "telegram", "whatsapp:1234567890", "1234567890@s.whatsapp.net"),
        ("telegram_bot", "whatsapp", "bot_test", "user123"),
        ("google_calendar", "gmail", "gcal:user@example.com", "user@example.com"),
        ("telegram_bot", "google_drive", "bot_test", "user123"),
    ]
    for channel, provider, endpoint, sender in invalid_pairs:
        env = _build_valid_ingest_envelope(
            channel=channel, provider=provider, endpoint_identity=endpoint, sender_identity=sender
        )
        with pytest.raises(ValidationError):
            parse_ingest_envelope(env)

    # Registry: all expected channels present with correct providers
    expected = {
        "voice": frozenset({"live-listener"}),
        "whatsapp_user_client": frozenset({"whatsapp"}),
        "google_calendar": frozenset({"google_calendar"}),
        "spotify_user_client": frozenset({"spotify"}),
        "owntracks": frozenset({"owntracks"}),
        "home_assistant": frozenset({"home_assistant"}),
        "gaming": frozenset({"steam"}),
        "google_drive": frozenset({"google_drive"}),
    }
    for channel, providers in expected.items():
        assert channel in _ALLOWED_PROVIDERS_BY_CHANNEL
        assert _ALLOWED_PROVIDERS_BY_CHANNEL[channel] == providers


def test_route_envelope_and_idempotency():
    """RouteInputV1 conversation_history field; idempotency key stored verbatim for all
    channel types."""
    # RouteInputV1 conversation_history
    assert RouteInputV1(prompt="Check vitals").conversation_history is None
    assert RouteInputV1(prompt="Follow up", conversation_history="").conversation_history == ""
    history = "**user** (2026-02-16T10:00:00Z):\nHello"
    assert (
        RouteInputV1(prompt="Follow up", conversation_history=history).conversation_history
        == history
    )

    # Full envelope round-trip
    e = _build_valid_route_envelope(prompt="Check vitals")
    assert parse_route_envelope(e).input.conversation_history is None
    e2 = _build_valid_route_envelope(
        prompt="When?", conversation_history=history, context="Some context"
    )
    parsed2 = parse_route_envelope(e2)
    assert parsed2.input.conversation_history == history and parsed2.input.context == "Some context"

    # Idempotency key stored verbatim
    idem_cases = [
        (
            "whatsapp_user_client",
            "whatsapp",
            "whatsapp:1234567890",
            "1234567890@s.whatsapp.net",
            "whatsapp:1234567890:MSGID123456",
        ),
        (
            "google_calendar",
            "google_calendar",
            "gcal:user@example.com",
            "user@example.com",
            "gcal:user@example.com:event_abc123",
        ),
        (
            "spotify_user_client",
            "spotify",
            "spotify:user123",
            "user123",
            "spotify:user123:event_abc123",
        ),
        (
            "home_assistant",
            "home_assistant",
            "ha:http://homeassistant.local:8123",
            "ha:light.living_room",
            "ha:http://homeassistant.local:8123:light.living_room:1711497600000",
        ),
    ]
    for channel, provider, endpoint, sender, idem_key in idem_cases:
        e = _build_valid_ingest_envelope(
            channel=channel,
            provider=provider,
            endpoint_identity=endpoint,
            sender_identity=sender,
            idempotency_key=idem_key,
        )
        assert parse_ingest_envelope(e).control.idempotency_key == idem_key


def test_notify_contracts():
    """WhatsApp reply/send/react; insight intent; empty message rejected; unknown intent
    rejected."""
    _WA_CTX = {
        "request_id": "01916b9d-1234-7000-abcd-123456789abc",
        "source_channel": "whatsapp_user_client",
        "source_endpoint_identity": "whatsapp:1234567890",
        "source_sender_identity": "1234567890@s.whatsapp.net",
    }

    # WhatsApp reply accepted with thread identity
    result = parse_notify_request(
        {
            "schema_version": "notify.v1",
            "origin_butler": "messenger",
            "delivery": {"intent": "reply", "channel": "whatsapp", "message": "Hi there"},
            "request_context": {**_WA_CTX, "source_thread_identity": "1234567890@s.whatsapp.net"},
        }
    )
    assert result.delivery.channel == "whatsapp"

    # Reply without thread identity rejected
    with pytest.raises(ValidationError) as exc:
        parse_notify_request(
            {
                "schema_version": "notify.v1",
                "origin_butler": "messenger",
                "delivery": {"intent": "reply", "channel": "whatsapp", "message": "Hi there"},
                "request_context": _WA_CTX,
            }
        )
    assert "thread" in str(exc.value).lower()

    # React intent rejected
    with pytest.raises(ValidationError):
        parse_notify_request(
            {
                "schema_version": "notify.v1",
                "origin_butler": "messenger",
                "delivery": {
                    "intent": "react",
                    "channel": "whatsapp",
                    "message": "",
                    "emoji": "👍",
                },
                "request_context": {
                    **_WA_CTX,
                    "source_thread_identity": "1234567890@s.whatsapp.net",
                },
            }
        )

    # Send without thread: accepted
    result2 = parse_notify_request(
        {
            "schema_version": "notify.v1",
            "origin_butler": "messenger",
            "delivery": {
                "intent": "send",
                "channel": "whatsapp",
                "message": "Hello",
                "recipient": "1234567890@s.whatsapp.net",
            },
        }
    )
    assert result2.delivery.intent == "send"

    # Insight intent: accepted, request_context optional
    result3 = parse_notify_request(
        {
            "schema_version": "notify.v1",
            "origin_butler": "health",
            "delivery": {
                "intent": "insight",
                "channel": "telegram",
                "message": "Your resting HR improved.",
                "recipient": "123456789",
            },
        }
    )
    assert result3.delivery.intent == "insight" and result3.request_context is None

    # Empty/whitespace message rejected for insight
    for msg in ["", "   "]:
        with pytest.raises(ValidationError):
            parse_notify_request(
                {
                    "schema_version": "notify.v1",
                    "origin_butler": "health",
                    "delivery": {
                        "intent": "insight",
                        "channel": "telegram",
                        "message": msg,
                        "recipient": "123456789",
                    },
                }
            )

    # Unknown intent rejected
    with pytest.raises(ValidationError):
        parse_notify_request(
            {
                "schema_version": "notify.v1",
                "origin_butler": "health",
                "delivery": {
                    "intent": "broadcast",
                    "channel": "telegram",
                    "message": "Some msg",
                    "recipient": "123456789",
                },
            }
        )
