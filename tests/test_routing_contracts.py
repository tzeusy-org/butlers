"""Unit tests for routing contract models (IngestEnvelopeV1, RouteEnvelopeV1, etc.)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from butlers.tools.switchboard.routing.contracts import parse_ingest_envelope


def _build_valid_ingest_envelope(
    *,
    text: str = "Test message",
    idempotency_key: str | None = None,
    schema_version: str = "ingest.v1",
    channel: str = "telegram",
    provider: str = "telegram",
    endpoint_identity: str = "bot_test",
    sender_identity: str = "user123",
    external_event_id: str | None = None,
    observed_at: str | None = None,
) -> dict:
    """Build a well-formed IngestEnvelopeV1 dict for testing."""
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
        "event": {
            "external_event_id": external_event_id,
            "observed_at": observed_at,
        },
        "sender": {
            "identity": sender_identity,
        },
        "payload": {
            "raw": {"text": text},
            "normalized_text": text,
        },
    }

    if idempotency_key:
        envelope["control"] = {"idempotency_key": idempotency_key}

    return envelope


# ---------------------------------------------------------------------------
# IngestPayloadV1 Attachments Field Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ingest_payload_without_attachments():
    """IngestPayloadV1 should accept envelopes without attachments field (backwards compatible)."""
    envelope = _build_valid_ingest_envelope(text="Test message")
    parsed = parse_ingest_envelope(envelope)

    assert parsed.payload.attachments is None


@pytest.mark.unit
def test_ingest_payload_with_empty_attachments():
    """IngestPayloadV1 should accept envelopes with empty attachments tuple."""
    envelope = _build_valid_ingest_envelope(text="Test message")
    envelope["payload"]["attachments"] = []

    parsed = parse_ingest_envelope(envelope)
    assert parsed.payload.attachments == ()


@pytest.mark.unit
def test_ingest_payload_with_valid_attachment():
    """IngestPayloadV1 should accept envelopes with valid attachment metadata."""
    envelope = _build_valid_ingest_envelope(text="Check out this photo")
    envelope["payload"]["attachments"] = [
        {
            "media_type": "image/jpeg",
            "storage_ref": "s3://bucket/photo123.jpg",
            "size_bytes": 1024000,
            "filename": "vacation.jpg",
            "width": 1920,
            "height": 1080,
        }
    ]

    parsed = parse_ingest_envelope(envelope)
    assert parsed.payload.attachments is not None
    assert len(parsed.payload.attachments) == 1

    attachment = parsed.payload.attachments[0]
    assert attachment.media_type == "image/jpeg"
    assert attachment.storage_ref == "s3://bucket/photo123.jpg"
    assert attachment.size_bytes == 1024000
    assert attachment.filename == "vacation.jpg"
    assert attachment.width == 1920
    assert attachment.height == 1080


@pytest.mark.unit
def test_ingest_payload_with_multiple_attachments():
    """IngestPayloadV1 should accept multiple attachments."""
    envelope = _build_valid_ingest_envelope(text="Multiple files attached")
    envelope["payload"]["attachments"] = [
        {
            "media_type": "image/png",
            "storage_ref": "s3://bucket/img1.png",
            "size_bytes": 500000,
        },
        {
            "media_type": "application/pdf",
            "storage_ref": "s3://bucket/doc.pdf",
            "size_bytes": 2048000,
            "filename": "report.pdf",
        },
    ]

    parsed = parse_ingest_envelope(envelope)
    assert parsed.payload.attachments is not None
    assert len(parsed.payload.attachments) == 2

    assert parsed.payload.attachments[0].media_type == "image/png"
    assert parsed.payload.attachments[1].media_type == "application/pdf"
    assert parsed.payload.attachments[1].filename == "report.pdf"


@pytest.mark.unit
def test_ingest_attachment_minimal_fields():
    """IngestAttachment should accept minimal required fields only."""
    envelope = _build_valid_ingest_envelope(text="Minimal attachment")
    envelope["payload"]["attachments"] = [
        {
            "media_type": "video/mp4",
            "storage_ref": "s3://bucket/video.mp4",
            "size_bytes": 5000000,
        }
    ]

    parsed = parse_ingest_envelope(envelope)
    assert parsed.payload.attachments is not None
    assert len(parsed.payload.attachments) == 1

    attachment = parsed.payload.attachments[0]
    assert attachment.media_type == "video/mp4"
    assert attachment.storage_ref == "s3://bucket/video.mp4"
    assert attachment.size_bytes == 5000000
    assert attachment.filename is None
    assert attachment.width is None
    assert attachment.height is None


@pytest.mark.unit
def test_ingest_attachment_missing_required_field():
    """IngestAttachment should reject attachments missing required fields."""
    envelope = _build_valid_ingest_envelope(text="Invalid attachment")
    envelope["payload"]["attachments"] = [
        {
            "media_type": "image/jpeg",
            # Missing storage_ref
            "size_bytes": 1024,
        }
    ]

    with pytest.raises(ValidationError) as exc_info:
        parse_ingest_envelope(envelope)

    errors = exc_info.value.errors()
    assert any("storage_ref" in str(e.get("loc", [])) for e in errors)


@pytest.mark.unit
def test_ingest_attachment_negative_size_rejected():
    """IngestAttachment should reject negative size_bytes."""
    envelope = _build_valid_ingest_envelope(text="Negative size")
    envelope["payload"]["attachments"] = [
        {
            "media_type": "image/jpeg",
            "storage_ref": "s3://bucket/photo.jpg",
            "size_bytes": -1000,
        }
    ]

    with pytest.raises(ValidationError) as exc_info:
        parse_ingest_envelope(envelope)

    errors = exc_info.value.errors()
    # Should fail validation due to Field(ge=0) constraint
    assert any(e["type"] in ("greater_than_equal", "int_parsing") for e in errors)


@pytest.mark.unit
def test_ingest_attachment_zero_size_accepted():
    """IngestAttachment should accept zero size_bytes."""
    envelope = _build_valid_ingest_envelope(text="Empty file")
    envelope["payload"]["attachments"] = [
        {
            "media_type": "text/plain",
            "storage_ref": "s3://bucket/empty.txt",
            "size_bytes": 0,
        }
    ]

    parsed = parse_ingest_envelope(envelope)
    assert parsed.payload.attachments is not None
    assert parsed.payload.attachments[0].size_bytes == 0


@pytest.mark.unit
def test_ingest_attachment_invalid_dimensions_rejected():
    """IngestAttachment should reject invalid width/height values."""
    envelope = _build_valid_ingest_envelope(text="Invalid dimensions")
    envelope["payload"]["attachments"] = [
        {
            "media_type": "image/jpeg",
            "storage_ref": "s3://bucket/photo.jpg",
            "size_bytes": 1024,
            "width": 0,  # Must be >= 1
            "height": 1080,
        }
    ]

    with pytest.raises(ValidationError) as exc_info:
        parse_ingest_envelope(envelope)

    errors = exc_info.value.errors()
    assert any("width" in str(e.get("loc", [])) for e in errors)


@pytest.mark.unit
def test_ingest_attachment_empty_string_rejected():
    """IngestAttachment should reject empty string for NonEmptyStr fields."""
    envelope = _build_valid_ingest_envelope(text="Empty string")
    envelope["payload"]["attachments"] = [
        {
            "media_type": "",  # Empty string not allowed
            "storage_ref": "s3://bucket/file",
            "size_bytes": 1024,
        }
    ]

    with pytest.raises(ValidationError) as exc_info:
        parse_ingest_envelope(envelope)

    errors = exc_info.value.errors()
    assert any("media_type" in str(e.get("loc", [])) for e in errors)


@pytest.mark.unit
def test_ingest_attachment_extra_fields_rejected():
    """IngestAttachment should reject extra fields (extra='forbid')."""
    envelope = _build_valid_ingest_envelope(text="Extra field")
    envelope["payload"]["attachments"] = [
        {
            "media_type": "image/jpeg",
            "storage_ref": "s3://bucket/photo.jpg",
            "size_bytes": 1024,
            "unknown_field": "should_fail",
        }
    ]

    with pytest.raises(ValidationError) as exc_info:
        parse_ingest_envelope(envelope)

    errors = exc_info.value.errors()
    assert any(e["type"] == "extra_forbidden" for e in errors)


@pytest.mark.unit
def test_ingest_attachment_frozen():
    """IngestAttachment should be frozen (immutable)."""
    envelope = _build_valid_ingest_envelope(text="Test immutability")
    envelope["payload"]["attachments"] = [
        {
            "media_type": "image/jpeg",
            "storage_ref": "s3://bucket/photo.jpg",
            "size_bytes": 1024,
        }
    ]

    parsed = parse_ingest_envelope(envelope)
    attachment = parsed.payload.attachments[0]

    with pytest.raises(ValidationError):
        attachment.size_bytes = 2048  # Should raise ValidationError due to frozen=True


@pytest.mark.unit
def test_existing_tests_still_pass():
    """Verify that existing envelopes without attachments continue to validate."""
    # This represents the backwards compatibility guarantee
    envelope = _build_valid_ingest_envelope(text="Log weight 80kg")
    parsed = parse_ingest_envelope(envelope)

    assert parsed.schema_version == "ingest.v1"
    assert parsed.source.channel == "telegram"
    assert parsed.payload.normalized_text == "Log weight 80kg"
    assert parsed.payload.attachments is None  # Default value
