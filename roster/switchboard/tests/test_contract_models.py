"""Unit tests for switchboard ingest/route contract models."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from butlers.tools.switchboard.routing.contracts import (
    IngestEnvelopeV1,
    NotifyRequestV1,
    RouteEnvelopeV1,
    RouteRequestContextV1,
    parse_notify_request,
)

pytestmark = pytest.mark.unit

_VALID_UUID7 = "018f52f3-9d8a-7ef2-8f2d-9fb6b32f12aa"


def _valid_ingest_payload() -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "telegram",
            "provider": "telegram",
            "endpoint_identity": "switchboard-bot",
        },
        "event": {
            "external_event_id": "update-123",
            "external_thread_id": "chat-456",
            "observed_at": now,
        },
        "sender": {"identity": "user-123"},
        "payload": {"raw": {"text": "ping"}, "normalized_text": "ping"},
        "control": {
            "idempotency_key": "idem-123",
            "trace_context": {"traceparent": "00-abc-xyz-01"},
            "policy_tier": "interactive",
        },
    }


def _valid_route_payload() -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    return {
        "schema_version": "route.v1",
        "request_context": {
            "request_id": _VALID_UUID7,
            "received_at": now,
            "source_channel": "telegram",
            "source_endpoint_identity": "switchboard-bot",
            "source_sender_identity": "user-123",
            "source_thread_identity": "chat-456",
        },
        "subrequest": {
            "subrequest_id": str(uuid.uuid4()),
            "segment_id": "seg-1",
            "fanout_mode": "parallel",
        },
        "target": {"butler": "health", "tool": "route.execute"},
        "input": {"prompt": "summarize this message", "context": "high priority"},
        "trace_context": {"traceparent": "00-abc-xyz-01"},
    }


def _valid_notify_payload() -> dict[str, Any]:
    return {
        "schema_version": "notify.v1",
        "origin_butler": "health",
        "delivery": {
            "intent": "send",
            "channel": "telegram",
            "message": "Take your medication.",
            "recipient": "12345",
        },
    }


def test_ingest_v1_valid_envelope() -> None:
    envelope = IngestEnvelopeV1.model_validate(_valid_ingest_payload())
    assert envelope.schema_version == "ingest.v1"
    assert envelope.source.channel == "telegram"
    assert envelope.payload.normalized_text == "ping"


def test_route_v1_valid_envelope() -> None:
    envelope = RouteEnvelopeV1.model_validate(_valid_route_payload())
    assert envelope.schema_version == "route.v1"
    assert envelope.request_context.request_id.version == 7
    assert envelope.subrequest.fanout_mode == "parallel"


def test_route_v1_context_accepts_mapping_payload() -> None:
    payload = _valid_route_payload()
    payload["input"]["context"] = {"notify_request": {"schema_version": "notify.v1"}}

    envelope = RouteEnvelopeV1.model_validate(payload)
    assert isinstance(envelope.input.context, dict)


def test_notify_v1_valid_request() -> None:
    request = parse_notify_request(_valid_notify_payload())
    assert request.schema_version == "notify.v1"
    assert request.delivery.intent == "send"
    assert request.delivery.channel == "telegram"


def test_notify_reply_requires_request_context() -> None:
    payload = _valid_notify_payload()
    payload["delivery"]["intent"] = "reply"

    with pytest.raises(ValidationError) as exc_info:
        NotifyRequestV1.model_validate(payload)

    error = exc_info.value.errors()[0]
    assert error["type"] == "reply_context_required"


def test_notify_telegram_reply_requires_source_thread_identity() -> None:
    payload = _valid_notify_payload()
    payload["delivery"]["intent"] = "reply"
    payload["request_context"] = {
        "request_id": _VALID_UUID7,
        "source_channel": "telegram",
        "source_endpoint_identity": "switchboard-bot",
        "source_sender_identity": "user-123",
    }

    with pytest.raises(ValidationError) as exc_info:
        NotifyRequestV1.model_validate(payload)

    error = exc_info.value.errors()[0]
    assert error["type"] == "reply_thread_required"


def test_route_v1_missing_request_context_required_field() -> None:
    payload = _valid_route_payload()
    del payload["request_context"]["source_sender_identity"]

    with pytest.raises(ValidationError) as exc_info:
        RouteEnvelopeV1.model_validate(payload)

    error = exc_info.value.errors()[0]
    assert error["loc"] == ("request_context", "source_sender_identity")
    assert error["type"] == "missing"


@pytest.mark.parametrize("value", [1700000000, "2026-02-13 00:00:00+00:00"])
def test_ingest_v1_observed_at_requires_rfc3339_string(value: Any) -> None:
    payload = _valid_ingest_payload()
    payload["event"]["observed_at"] = value

    with pytest.raises(ValidationError) as exc_info:
        IngestEnvelopeV1.model_validate(payload)

    error = exc_info.value.errors()[0]
    assert error["loc"] == ("event", "observed_at")
    assert error["type"] == "rfc3339_string_required"


def test_route_v1_received_at_requires_rfc3339_string() -> None:
    payload = _valid_route_payload()
    payload["request_context"]["received_at"] = "2026-02-13 00:00:00+00:00"

    with pytest.raises(ValidationError) as exc_info:
        RouteEnvelopeV1.model_validate(payload)

    error = exc_info.value.errors()[0]
    assert error["loc"] == ("request_context", "received_at")
    assert error["type"] == "rfc3339_string_required"


def test_ingest_v1_rejects_inconsistent_source_channel_provider_pair() -> None:
    payload = _valid_ingest_payload()
    payload["source"]["channel"] = "email"
    payload["source"]["provider"] = "telegram"

    with pytest.raises(ValidationError) as exc_info:
        IngestEnvelopeV1.model_validate(payload)

    error = exc_info.value.errors()[0]
    assert error["loc"] == ("source",)
    assert error["type"] == "invalid_source_provider"


@pytest.mark.parametrize(
    ("model_cls", "schema_version"),
    [
        (IngestEnvelopeV1, "ingest.v99"),
        (RouteEnvelopeV1, "route.v2"),
    ],
)
def test_unknown_or_newer_schema_version_fails_deterministically(
    model_cls: type[IngestEnvelopeV1 | RouteEnvelopeV1],
    schema_version: str,
) -> None:
    payload = _valid_ingest_payload() if model_cls is IngestEnvelopeV1 else _valid_route_payload()
    payload["schema_version"] = schema_version

    with pytest.raises(ValidationError) as exc_info:
        model_cls.model_validate(payload)

    error = exc_info.value.errors()[0]
    assert error["loc"] == ("schema_version",)
    assert error["type"] == "unsupported_schema_version"


def test_request_context_lineage_immutability_enforced() -> None:
    original_payload = {
        "request_id": _VALID_UUID7,
        "received_at": datetime.now(UTC).isoformat(),
        "source_channel": "telegram",
        "source_endpoint_identity": "switchboard-bot",
        "source_sender_identity": "user-123",
    }
    original = RouteRequestContextV1.model_validate(original_payload)

    candidate_payload = {
        **original.model_dump(mode="python"),
        "source_channel": "email",
    }
    with pytest.raises(ValidationError) as exc_info:
        RouteRequestContextV1.model_validate_with_lineage(
            candidate_payload,
            lineage=original,
        )

    error = exc_info.value.errors()[0]
    assert error["loc"] == ()
    assert error["type"] == "immutable_request_context"


def test_request_context_lineage_allows_optional_extension() -> None:
    original_payload = {
        "request_id": _VALID_UUID7,
        "received_at": datetime.now(UTC).isoformat(),
        "source_channel": "telegram",
        "source_endpoint_identity": "switchboard-bot",
        "source_sender_identity": "user-123",
    }
    original = RouteRequestContextV1.model_validate(original_payload)

    candidate = RouteRequestContextV1.model_validate_with_lineage(
        {
            **original.model_dump(mode="python"),
            "source_thread_identity": "chat-456",
        },
        lineage=original,
    )

    assert candidate.source_thread_identity == "chat-456"
    assert candidate.request_id == original.request_id
